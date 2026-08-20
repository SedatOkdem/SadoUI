"""Control process: FSM + PID -> UART command packets."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any, Optional

from aeroshield.core.fsm import FireControlFSM, FsmContext, Stage, SystemState
from aeroshield.core.modes import (
    auto_fire_allowed,
    display_target_name,
    in_wez,
    is_hostile,
    wez_status_text,
)
from aeroshield.core.pid import PID
from aeroshield.core.protocol import CommandPacket, ModCode, StatusCode
from aeroshield.workers.ipc import put_latest


def control_process_main(
    track_q: Queue,
    op_q: Queue,
    uart_cmd_q: Queue,
    ui_telem_q: Queue,
    stop_event,
    config: dict[str, Any],
) -> None:
    ctrl = config.get("control", {})
    safety = config.get("safety", {})
    pid_pan = PID.from_dict(ctrl.get("pid_pan", {}))
    pid_tilt = PID.from_dict(ctrl.get("pid_tilt", {}))
    lock_angle = float(ctrl.get("lock_angle_deg", 0.3))
    fov_deg = float((config.get("range_estimator") or {}).get("fov_deg", 78.0))
    dpp_cfg = float(ctrl.get("degrees_per_pixel", 0.045))
    invert_track_pan = bool(ctrl.get("invert_track_pan", True))
    track_slew_dps = float(ctrl.get("track_slew_dps", 120.0))
    tilt_slew_dps = float(ctrl.get("tilt_slew_dps", 80.0))
    pid_deadzone = float(ctrl.get("pid_deadzone_deg", 0.25))
    jog_pan_dps = float(ctrl.get("jog_pan_dps", 40.0))
    jog_tilt_dps = float(ctrl.get("jog_tilt_dps", 25.0))
    pan_limits = ctrl.get("pan_limits_deg", [0, 270])
    tilt_limits = ctrl.get("tilt_limits_deg", [-30, 60])
    home_pan = float(ctrl.get("home_pan_deg", 135.0))
    home_tilt = float(ctrl.get("home_tilt_deg", 0.0))

    fsm = FireControlFSM()
    pan_pos = home_pan
    tilt_pos = home_tilt
    last_t = time.time()
    last_aim_key = None
    pan_rate = 0.0
    tilt_rate = 0.0
    rem_pan = 0.0
    rem_tilt = 0.0
    last_pid_t = time.time()
    last_slider_pan = None
    last_slider_tilt = None
    fire_pulse_until = 0.0
    fire_spin_s = float(config.get("weapon", {}).get("fire_spin_s", 0.45))
    esc_idle_us = int(config.get("weapon", {}).get("esc_idle_us", 1060))
    esc_fire_us = int(config.get("weapon", {}).get("esc_fire_us", 1200))
    bda_until = 0.0
    last_track = None
    op = {
        "stage": 1,
        "start_mission": False,
        "estop": False,
        "estop_clear": False,
        "maint": False,
        "fire": False,
        "pan_cmd": int(round(home_pan)),
        "tilt_cmd": int(round(home_tilt)),
        "pan_forbidden_min": float(safety.get("pan_forbidden_min", 200)),
        "pan_forbidden_max": float(safety.get("pan_forbidden_max", 270)),
        "manual_range_m": None,
        "bit_ok": True,
        "maint_remaining_s": float(safety.get("maintenance_total_s", 600)),
    }
    # Brief BIT delay
    bit_done_at = time.time() + 1.0
    serial_telem = {"linked": False, "failsafe": False, "mock": True}
    estop_frozen_pan: Optional[float] = None
    estop_frozen_tilt: Optional[float] = None
    estop_latched = False
    estop_clear_pending = False
    estop_ack_until = 0.0
    estop_source = "-"
    last_mod = int(ModCode.HOME)
    failsafe_since: Optional[float] = None
    hw_estop_since: Optional[float] = None
    ignore_hw_estop = bool(safety.get("ignore_hw_estop", True))
    estop_enabled = bool(safety.get("estop_enabled", False))
    last_ui_push = 0.0
    last_frame_id_sent = None
    pending_events: list = []
    ui_hz = float(config.get("ui", {}).get("target_gui_hz", 25))
    ui_period = 1.0 / max(10.0, min(45.0, ui_hz))

    while not stop_event.is_set():
        now = time.time()
        dt = max(1e-3, now - last_t)
        last_t = now

        # Operator commands
        try:
            while True:
                msg = op_q.get_nowait()
                if isinstance(msg, dict):
                    if msg.get("type") == "serial_telem":
                        serial_telem.update(msg)
                    else:
                        op.update(msg)
                        if msg.get("estop_clear"):
                            estop_clear_pending = True
                        if msg.get("estop") and not estop_clear_pending:
                            estop_latched = True
                            estop_source = "SW"
        except Exception:
            pass

        # Latest track snapshot
        try:
            while True:
                last_track = track_q.get_nowait()
        except Exception:
            pass

        if now >= bit_done_at:
            op["bit_ok"] = True

        failsafe = bool(serial_telem.get("failsafe", False))
        is_mock = bool(serial_telem.get("mock", True))
        status_estop = int(serial_telem.get("status", 0)) == int(StatusCode.ESTOP)
        # Ignore ESTOP echo of our own ESTOP command; require a stable HW report.
        hw_candidate = (
            status_estop
            and not is_mock
            and not ignore_hw_estop
            and last_mod != int(ModCode.ESTOP)
        )
        if hw_candidate:
            if hw_estop_since is None:
                hw_estop_since = now
            hw_physical = (now - hw_estop_since) >= 0.35
        else:
            hw_estop_since = None
            hw_physical = False
        if estop_clear_pending and status_estop and not is_mock and last_mod != int(ModCode.ESTOP):
            hw_physical = True

        if not estop_enabled:
            estop_latched = False
            estop_clear_pending = False
            op["estop"] = False
            op["estop_clear"] = False
            estop_source = "-"
            estop_frozen_pan = None
            estop_frozen_tilt = None
            hw_physical = False
            failsafe_since = None
            if fsm.state in (SystemState.ESTOP, SystemState.FAILSAFE):
                fsm.state = SystemState.READY
        elif failsafe:
            if failsafe_since is None:
                failsafe_since = now
            if (now - failsafe_since) >= 2.5:
                estop_latched = True
                estop_source = "FAILSAFE"
        else:
            failsafe_since = None
        if estop_enabled and hw_physical:
            estop_latched = True
            estop_source = "HW"
        if estop_enabled and bool(op.get("estop", False)) and not estop_clear_pending:
            estop_latched = True
            if estop_source == "-":
                estop_source = "SW"

        # --- Reset attempt ---
        estop_cleared_ack = False
        estop_reset_waiting_hw = False
        if estop_clear_pending:
            if failsafe:
                estop_reset_waiting_hw = True
            elif is_mock or ignore_hw_estop:
                estop_latched = False
                estop_clear_pending = False
                op["estop"] = False
                op["estop_clear"] = False
                estop_frozen_pan = None
                estop_frozen_tilt = None
                estop_source = "-"
                estop_cleared_ack = True
                estop_ack_until = now + 0.6
                if fsm.state == SystemState.ESTOP:
                    fsm.state = SystemState.READY
                    fsm._emit("FSM ESTOP cleared -> READY")
            elif status_estop:
                # Real ESP32 still reports ESTOP after MANUAL → physical button held
                estop_reset_waiting_hw = True
            else:
                estop_latched = False
                estop_clear_pending = False
                op["estop"] = False
                op["estop_clear"] = False
                estop_frozen_pan = None
                estop_frozen_tilt = None
                estop_source = "-"
                estop_cleared_ack = True
                estop_ack_until = now + 0.6
                if fsm.state == SystemState.ESTOP:
                    fsm.state = SystemState.READY
                    fsm._emit("FSM ESTOP cleared -> READY")

        estop_active = bool(estop_enabled) and (estop_latched or failsafe)
        if estop_active and estop_frozen_pan is None:
            estop_frozen_pan = pan_pos
            estop_frozen_tilt = tilt_pos
            fire_pulse_until = 0.0
            op["start_mission"] = False
            op["fire"] = False

        if "esc_idle_us" in op:
            esc_idle_us = max(1000, min(2000, int(op.get("esc_idle_us", esc_idle_us))))
        if "esc_fire_us" in op:
            esc_fire_us = max(1000, min(2000, int(op.get("esc_fire_us", esc_fire_us))))
        if esc_fire_us < esc_idle_us:
            esc_fire_us = esc_idle_us
        if "fire_spin_ms" in op:
            fire_spin_s = max(0.05, min(2.0, float(op.get("fire_spin_ms", fire_spin_s * 1000)) / 1000.0))

        stage = Stage(int(op.get("stage", 1)))
        fsm.stage = stage

        dets = (last_track or {}).get("detections") or []
        primary_id = (last_track or {}).get("primary_id")
        primary = None
        for d in dets:
            if primary_id is not None and d.get("track_id") == primary_id:
                primary = d
                break
        if primary is None and dets:
            primary = dets[0]

        width = int((last_track or {}).get("width") or 0)
        height = int((last_track or {}).get("height") or 0)
        if width <= 1:
            width = int(ctrl.get("frame_center", [640, 360])[0] * 2)
        if height <= 1:
            height = int(ctrl.get("frame_center", [640, 360])[1] * 2)
        cx0, cy0 = width / 2.0, height / 2.0
        dpp = (fov_deg / float(width)) if width > 1 else dpp_cfg

        label = str(primary.get("label", "-")) if primary else "-"
        if primary is not None and "hostile" in primary:
            hostile = bool(primary.get("hostile"))
        else:
            hostile = is_hostile(label) if primary else False
        range_m = op.get("manual_range_m")
        if range_m is None and primary is not None:
            range_m = primary.get("range_m")
        if range_m is not None:
            range_m = round(float(range_m), 1)

        ang_err_pan = 0.0
        ang_err_tilt = 0.0
        locked = False
        wez_ok = False
        vx = vy = 0.0

        if primary is not None:
            pcx = float(primary.get("cx", cx0))
            pcy = float(primary.get("cy", cy0))
            vx = float(primary.get("vx", 0.0))
            vy = float(primary.get("vy", 0.0))
            lead_x = pcx + vx * 0.08 if abs(vx) > 50 else pcx
            lead_y = pcy + vy * 0.08 if abs(vy) > 50 else pcy
            ang_err_pan = (lead_x - cx0) * dpp
            if invert_track_pan:
                ang_err_pan = -ang_err_pan
            ang_err_tilt = (cy0 - lead_y) * dpp
            locked = abs(ang_err_pan) <= lock_angle and abs(ang_err_tilt) <= lock_angle
            if stage == Stage.STAGE2:
                wez_ok = True
            elif stage == Stage.STAGE3:
                wez_ok = hostile and in_wez(config, label, range_m)
            else:
                wez_ok = True

        # Aiming — freeze while E-STOP
        if estop_active:
            if estop_frozen_pan is not None:
                pan_pos = estop_frozen_pan
                tilt_pos = estop_frozen_tilt if estop_frozen_tilt is not None else tilt_pos
            pid_pan.reset()
            pid_tilt.reset()
            last_aim_key = None
            pan_rate = 0.0
            tilt_rate = 0.0
            rem_pan = 0.0
            rem_tilt = 0.0
        elif stage == Stage.STAGE1 or fsm.state == SystemState.MAINT:
            jog_pan = float(op.get("pan_jog", 0) or 0)
            jog_tilt = float(op.get("tilt_jog", 0) or 0)
            slider_pan = float(op.get("pan_cmd", pan_pos))
            slider_tilt = float(op.get("tilt_cmd", tilt_pos))
            if last_slider_pan is None:
                last_slider_pan = slider_pan
                last_slider_tilt = slider_tilt
            if abs(slider_pan - last_slider_pan) >= 0.5 or abs(slider_tilt - last_slider_tilt) >= 0.5:
                pan_pos = slider_pan
                tilt_pos = slider_tilt
                last_slider_pan = slider_pan
                last_slider_tilt = slider_tilt
            if jog_pan:
                pan_pos += jog_pan * jog_pan_dps * dt
            if jog_tilt:
                tilt_pos += jog_tilt * jog_tilt_dps * dt
            pid_pan.reset()
            pid_tilt.reset()
            last_aim_key = None
            pan_rate = 0.0
            tilt_rate = 0.0
            rem_pan = 0.0
            rem_tilt = 0.0
        else:
            if stage in (Stage.STAGE2, Stage.STAGE3) and fsm.state == SystemState.READY:
                fsm.state = SystemState.SEARCH
                fsm.log_events.append("FSM READY -> SEARCH (aşama 2/3)")
            if primary is not None and fsm.state in (
                SystemState.SEARCH,
                SystemState.TRACK,
                SystemState.LOCK,
                SystemState.ENGAGE,
                SystemState.BDA,
            ):
                fid = (last_track or {}).get("frame_id")
                if last_aim_key != fid:
                    last_aim_key = fid
                    frame_dt = max(1.0 / 60.0, min(0.08, now - last_pid_t))
                    last_pid_t = now
                    if abs(ang_err_pan) < pid_deadzone:
                        pid_pan.reset()
                        pan_rate = 0.0
                        rem_pan = 0.0
                    else:
                        pan_rate = pid_pan.update(ang_err_pan, frame_dt)
                        rem_pan = ang_err_pan
                    if abs(ang_err_tilt) < pid_deadzone:
                        pid_tilt.reset()
                        tilt_rate = 0.0
                        rem_tilt = 0.0
                    else:
                        tilt_rate = pid_tilt.update(ang_err_tilt, frame_dt)
                        rem_tilt = ang_err_tilt
                    edge = 0.15
                    near_edge = (
                        pcx < width * edge
                        or pcx > width * (1.0 - edge)
                        or pcy < height * edge
                        or pcy > height * (1.0 - edge)
                    )
                    if near_edge:
                        pan_rate *= 0.5
                        tilt_rate *= 0.5
                pan_rate = _clamp(pan_rate, -track_slew_dps, track_slew_dps)
                tilt_rate = _clamp(tilt_rate, -tilt_slew_dps, tilt_slew_dps)
                pan_pos, pan_rate, rem_pan = _limited_step(pan_pos, pan_rate, rem_pan, dt)
                tilt_pos, tilt_rate, rem_tilt = _limited_step(tilt_pos, tilt_rate, rem_tilt, dt)
            else:
                pid_pan.reset()
                pid_tilt.reset()
                last_aim_key = None
                pan_rate = 0.0
                tilt_rate = 0.0
                rem_pan = 0.0
                rem_tilt = 0.0

        pan_pos = _clamp(pan_pos, float(pan_limits[0]), float(pan_limits[1]))
        tilt_pos = _clamp(tilt_pos, float(tilt_limits[0]), float(tilt_limits[1]))

        # Forbidden zone → latch E-STOP once
        fmin = float(op.get("pan_forbidden_min", 200))
        fmax = float(op.get("pan_forbidden_max", 270))
        in_forbidden = fmin <= pan_pos <= fmax
        if in_forbidden and estop_enabled and not estop_active:
            mid = (fmin + fmax) / 2.0
            pan_pos = fmin - 1.0 if pan_pos < mid else fmax + 1.0
            pan_pos = _clamp(pan_pos, float(pan_limits[0]), float(pan_limits[1]))
            if not estop_latched:
                estop_latched = True
                estop_active = True
                estop_source = "YASAK"
                estop_frozen_pan = pan_pos
                estop_frozen_tilt = tilt_pos
                fsm.log_events.append("E-STOP: yasak pan bölgesi ihlali")

        fire_req = bool(op.get("fire", False)) and not estop_active
        allow_auto = (
            auto_fire_allowed(stage, label, range_m, config, locked, hostile=hostile)
            if primary
            else False
        )
        if in_forbidden:
            fire_req = False
            allow_auto = False

        ctx = FsmContext(
            has_target=primary is not None,
            locked=locked,
            wez_ok=wez_ok,
            is_hostile=hostile if primary else False,
            fire_requested=fire_req,
            fire_done=now < fire_pulse_until,
            bda_lost=now > bda_until and fsm.state == SystemState.BDA,
            bit_ok=bool(op.get("bit_ok", False)),
            estop=estop_latched if estop_enabled else False,
            hw_estop=False if not estop_enabled else bool(status_estop and not is_mock and not ignore_hw_estop),
            failsafe=False if not estop_enabled else bool(failsafe and not ignore_hw_estop),
            maint=bool(op.get("maint", False)) and not estop_active,
            operator_start=bool(op.get("start_mission", False)) and not estop_active,
            estop_cleared=bool(estop_clear_pending or estop_cleared_ack),
        )
        fsm.update(ctx)

        if op.get("fire"):
            op["fire"] = False

        fire_bit = 0
        if (
            not estop_active
            and fsm.state not in (SystemState.ESTOP, SystemState.FAILSAFE, SystemState.MAINT)
        ):
            if stage == Stage.STAGE1 and fire_req and fsm.allow_manual_fire() and not in_forbidden:
                fire_bit = 1
                fire_pulse_until = now + fire_spin_s
                bda_until = now + 3.0
                fsm.state = SystemState.ENGAGE
            elif fsm.allow_auto_fire() and allow_auto and not in_forbidden:
                fire_bit = 1
                fire_pulse_until = now + fire_spin_s
                bda_until = now + 3.0

        if fire_bit and fsm.state == SystemState.ENGAGE:
            ctx.fire_done = True
            fsm.update(ctx)

        # UART: after software reset keep sending MANUAL so ESP leaves ESTOP
        if (not estop_enabled) or (estop_clear_pending and not failsafe):
            mod = _mod_for_state(fsm, stage)
            if not estop_enabled and mod == ModCode.ESTOP:
                mod = ModCode.MANUAL
        elif ignore_hw_estop and not estop_latched and fsm.state != SystemState.ESTOP:
            mod = _mod_for_state(fsm, stage)
        elif estop_active or fsm.state in (SystemState.ESTOP, SystemState.FAILSAFE):
            mod = ModCode.ESTOP
            fire_bit = 0
        else:
            mod = _mod_for_state(fsm, stage)
        last_mod = int(mod)

        cmd = CommandPacket(
            mod=int(mod),
            pan=int(round(pan_pos)),
            tilt=int(round(tilt_pos)),
            fire=int(fire_bit),
            esc_idle_us=int(esc_idle_us),
            esc_fire_us=int(esc_fire_us),
            fire_spin_ms=int(round(fire_spin_s * 1000)),
        )
        put_latest(uart_cmd_q, cmd)

        events = fsm.pop_events()
        if events:
            pending_events.extend(events)
        frame_id = (last_track or {}).get("frame_id")
        new_frame = frame_id is not None and frame_id != last_frame_id_sent
        # Strict UI rate limit — never bypass (was freezing Qt main thread)
        due_ui = (now - last_ui_push) >= ui_period

        if due_ui:
            last_ui_push = now
            jpeg = None
            dets_out = []
            if new_frame:
                jpeg = (last_track or {}).get("jpeg")
                last_frame_id_sent = frame_id
                dets_out = dets if dets else ((last_track or {}).get("detections") or [])
            log_batch = pending_events
            pending_events = []
            telem = {
                "ts": now,
                "linked": bool(serial_telem.get("linked", False)),
                "mock": bool(serial_telem.get("mock", True)),
                "failsafe": failsafe,
                "status": int(serial_telem.get("status", 0)),
                "limit_pan": int(serial_telem.get("limit_pan", 0)),
                "limit_tilt": int(serial_telem.get("limit_tilt", 0)),
                "fsm": fsm.state.name,
                "stage": int(stage.value),
                "estop_active": estop_active,
                "estop_enabled": estop_enabled,
                "estop_source": estop_source if estop_active else "-",
                "estop_cleared_ack": estop_cleared_ack or (now < estop_ack_until),
                "estop_reset_waiting_hw": estop_reset_waiting_hw,
                "estop_reset_pending": estop_clear_pending,
                "fps": float((last_track or {}).get("fps") or 0.0),
                "latency_ms": float((last_track or {}).get("latency_ms") or 0.0),
                "pan": int(round(pan_pos)),
                "tilt": int(round(tilt_pos)),
                "fire": int(fire_bit),
                "locked": locked,
                "wez_ok": wez_ok,
                "primary_label": display_target_name(label, hostile) if primary else "-",
                "primary_id": primary_id,
                "cx": float(primary.get("cx", 0)) if primary else 0.0,
                "cy": float(primary.get("cy", 0)) if primary else 0.0,
                "vx": vx,
                "vy": vy,
                "range_m": range_m,
                "range_text": wez_status_text(config, label, range_m) if primary else "-",
                "maint_remaining_s": float(op.get("maint_remaining_s", 600)),
                "in_forbidden": in_forbidden,
                "detections": dets_out,
                "jpeg": jpeg,
                "width": width,
                "height": height,
                "frame_id": frame_id,
                "log_events": log_batch,
                "has_frame": jpeg is not None,
            }
            put_latest(ui_telem_q, telem)

        time.sleep(0.01)


def _mod_for_state(fsm: FireControlFSM, stage: Stage) -> int:
    if fsm.state == SystemState.ESTOP:
        return ModCode.ESTOP
    if fsm.state in (SystemState.BIT, SystemState.READY) and stage == Stage.STAGE1:
        return ModCode.HOME if fsm.state == SystemState.BIT else ModCode.MANUAL
    if stage == Stage.STAGE1:
        return ModCode.MANUAL
    if stage == Stage.STAGE2:
        return ModCode.SEMI
    return ModCode.AUTO


def _limited_step(
    pos: float, rate: float, remaining: float, dt: float
) -> tuple[float, float, float]:
    """Integrate rate*dt without crossing remaining error (no overshoot)."""
    if remaining == 0.0:
        return pos, 0.0, 0.0
    delta = rate * dt
    if delta * remaining <= 0.0:
        return pos, 0.0, remaining
    if abs(delta) >= abs(remaining):
        return pos + remaining, 0.0, 0.0
    return pos + delta, rate, remaining - delta


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def start_control_process(
    track_q: Queue,
    op_q: Queue,
    uart_cmd_q: Queue,
    ui_telem_q: Queue,
    stop_event,
    config: dict,
) -> Process:
    p = Process(
        target=control_process_main,
        args=(track_q, op_q, uart_cmd_q, ui_telem_q, stop_event, config),
        name="ControlProcess",
        daemon=True,
    )
    p.start()
    return p
