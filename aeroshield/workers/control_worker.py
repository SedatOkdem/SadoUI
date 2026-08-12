"""Control process: FSM + PID -> UART command packets."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any, Optional

from aeroshield.core.fsm import FireControlFSM, FsmContext, Stage, SystemState
from aeroshield.core.modes import auto_fire_allowed, in_wez, is_hostile, wez_status_text
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
    dpp = float(ctrl.get("degrees_per_pixel", 0.045))
    pan_limits = ctrl.get("pan_limits_deg", [0, 270])
    tilt_limits = ctrl.get("tilt_limits_deg", [-30, 60])

    fsm = FireControlFSM()
    pan_pos = 0.0
    tilt_pos = 0.0
    last_t = time.time()
    fire_pulse_until = 0.0
    bda_until = 0.0
    last_track = None
    op = {
        "stage": 1,
        "start_mission": False,
        "estop": False,
        "estop_clear": False,
        "maint": False,
        "fire": False,
        "pan_cmd": 0,
        "tilt_cmd": 0,
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
        # Physical HW stop: real ESP32 still reports ESTOP after we stopped commanding ESTOP.
        # Mock echo of our own ESTOP command is NOT physical.
        hw_physical = (
            status_estop
            and not is_mock
            and last_mod != int(ModCode.ESTOP)
            and not estop_clear_pending
        ) or (status_estop and not is_mock and estop_clear_pending)

        if failsafe:
            estop_latched = True
            estop_source = "FAILSAFE"
        if hw_physical:
            estop_latched = True
            estop_source = "HW"
        if bool(op.get("estop", False)) and not estop_clear_pending:
            estop_latched = True
            if estop_source == "-":
                estop_source = "SW"

        # --- Reset attempt ---
        estop_cleared_ack = False
        estop_reset_waiting_hw = False
        if estop_clear_pending:
            if failsafe:
                estop_reset_waiting_hw = True
            elif is_mock:
                # Mock: always allow software reset (ESP echo is not physical)
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

        estop_active = estop_latched or failsafe
        if estop_active and estop_frozen_pan is None:
            estop_frozen_pan = pan_pos
            estop_frozen_tilt = tilt_pos
            fire_pulse_until = 0.0
            op["start_mission"] = False
            op["fire"] = False

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

        width = int((last_track or {}).get("width") or ctrl.get("frame_center", [640, 360])[0] * 2)
        height = int((last_track or {}).get("height") or ctrl.get("frame_center", [640, 360])[1] * 2)
        cx0, cy0 = width / 2.0, height / 2.0

        label = str(primary.get("label", "-")) if primary else "-"
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
            lead_x = pcx + vx * 0.08
            lead_y = pcy + vy * 0.08
            ang_err_pan = (lead_x - cx0) * dpp
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
        elif stage == Stage.STAGE1 or fsm.state in (SystemState.BIT, SystemState.READY, SystemState.MAINT):
            pan_pos = float(op.get("pan_cmd", pan_pos))
            tilt_pos = float(op.get("tilt_cmd", tilt_pos))
            pid_pan.reset()
            pid_tilt.reset()
        elif primary is not None and fsm.state in (
            SystemState.SEARCH,
            SystemState.TRACK,
            SystemState.LOCK,
            SystemState.ENGAGE,
            SystemState.BDA,
        ):
            pan_delta = pid_pan.update(ang_err_pan, dt)
            tilt_delta = pid_tilt.update(ang_err_tilt, dt)
            pan_pos += pan_delta * dt * 8.0
            tilt_pos += tilt_delta * dt * 8.0
        else:
            pid_pan.reset()
            pid_tilt.reset()

        pan_pos = _clamp(pan_pos, float(pan_limits[0]), float(pan_limits[1]))
        tilt_pos = _clamp(tilt_pos, float(tilt_limits[0]), float(tilt_limits[1]))

        # Forbidden zone → latch E-STOP once
        fmin = float(op.get("pan_forbidden_min", 200))
        fmax = float(op.get("pan_forbidden_max", 270))
        in_forbidden = fmin <= pan_pos <= fmax
        if in_forbidden and not estop_active:
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
        allow_auto = auto_fire_allowed(stage, label, range_m, config, locked) if primary else False
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
            estop=estop_latched,
            # Real ESP32 ESTOP status (ignore mock command echo)
            hw_estop=bool(status_estop and not is_mock),
            failsafe=failsafe,
            maint=bool(op.get("maint", False)) and not estop_active,
            operator_start=bool(op.get("start_mission", False)) and not estop_active,
            # Clear is handled by control worker latch logic; don't race FSM clear
            estop_cleared=False,
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
                fire_pulse_until = now + 0.25
                bda_until = now + 3.0
                fsm.state = SystemState.ENGAGE
            elif fsm.allow_auto_fire() and allow_auto and not in_forbidden:
                fire_bit = 1
                fire_pulse_until = now + 0.25
                bda_until = now + 3.0

        if fire_bit and fsm.state == SystemState.ENGAGE:
            ctx.fire_done = True
            fsm.update(ctx)

        # UART mode: while resetting, send MANUAL so ESP can leave ESTOP echo
        if estop_clear_pending and not failsafe:
            mod = ModCode.MANUAL
            fire_bit = 0
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
                "primary_label": label,
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
