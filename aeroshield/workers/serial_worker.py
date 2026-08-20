"""ESP32 UART worker process (real or mock)."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any, Optional

from aeroshield.core.protocol import (
    CommandPacket,
    ModCode,
    PacketParser,
    StatusCode,
    StatusPacket,
)
from aeroshield.workers.ipc import put_latest


def _open_serial(port: str, baud: int, timeout_s: float):
    import serial

    ser = serial.Serial()
    ser.port = port.strip()
    ser.baudrate = int(baud)
    ser.bytesize = serial.EIGHTBITS
    ser.parity = serial.PARITY_NONE
    ser.stopbits = serial.STOPBITS_ONE
    ser.timeout = float(timeout_s)
    ser.write_timeout = 0.2
    ser.xonxoff = False
    ser.rtscts = False
    ser.dsrdtr = False
    ser.open()
    try:
        ser.setDTR(False)
        ser.setRTS(False)
    except Exception:
        pass
    # ESP32 USB-UART often resets on open; wait out bootloader + banner.
    time.sleep(1.8)
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception:
        pass
    return ser


def _close_serial(ser) -> None:
    if ser is None:
        return
    try:
        ser.close()
    except Exception:
        pass


def serial_process_main(
    cmd_q: Queue,
    telem_q: Queue,
    stop_event,
    config: dict[str, Any],
    cfg_q: Optional[Queue] = None,
) -> None:
    ser_cfg = dict(config.get("serial", {}))
    mock = bool(ser_cfg.get("mock", True))
    port = str(ser_cfg.get("port", "COM3"))
    baud = int(ser_cfg.get("baud", 115200))
    timeout_s = float(ser_cfg.get("timeout_s", 0.05))
    rate_hz = float(ser_cfg.get("rate_hz", 30))
    period = 1.0 / max(1.0, rate_hz)
    failsafe_timeout = float(ser_cfg.get("failsafe_timeout_s", 1.5))
    parser = PacketParser()
    ser = None
    linked = False
    last_rx = time.time()
    last_cmd = CommandPacket(mod=ModCode.HOME)
    mock_pan = 0
    mock_tilt = 0
    last_log = ""
    grace_until = 0.0

    def emit_log(msg: str) -> None:
        nonlocal last_log
        last_log = msg
        put_latest(
            telem_q,
            {
                "linked": False,
                "mock": mock,
                "failsafe": not mock,
                "port": port,
                "log_line": msg,
                "ts": time.time(),
            },
        )

    def apply_open() -> None:
        nonlocal ser, mock, linked, last_rx, grace_until, parser
        _close_serial(ser)
        ser = None
        parser = PacketParser()
        if mock:
            linked = True
            last_rx = time.time()
            emit_log("Serial: MOCK (ESP32 kullanılmıyor)")
            return
        try:
            ser = _open_serial(port, baud, timeout_s)
            linked = False
            last_rx = time.time()
            grace_until = time.time() + 2.5
            emit_log(f"Serial açık: {port} @ {baud}")
        except Exception as exc:
            ser = None
            linked = False
            emit_log(f"Serial açılamadı ({port}): {exc}")

    apply_open()

    while not stop_event.is_set():
        t0 = time.time()

        if cfg_q is not None:
            try:
                while True:
                    cfg = cfg_q.get_nowait()
                    if not isinstance(cfg, dict):
                        continue
                    new_mock = bool(cfg.get("mock", mock))
                    new_port = str(cfg.get("port", port)).strip() or port
                    new_baud = int(cfg.get("baud", baud))
                    if new_mock != mock or new_port != port or new_baud != baud or cfg.get("reconnect"):
                        mock = new_mock
                        port = new_port
                        baud = new_baud
                        apply_open()
            except Exception:
                pass

        latest = None
        try:
            while True:
                latest = cmd_q.get_nowait()
        except Exception:
            pass

        if latest is not None:
            if isinstance(latest, CommandPacket):
                last_cmd = latest
            elif isinstance(latest, dict) and latest.get("type") != "serial_cfg":
                last_cmd = CommandPacket(
                    mod=int(latest.get("mod", ModCode.MANUAL)),
                    pan=int(latest.get("pan", 0)),
                    tilt=int(latest.get("tilt", 0)),
                    fire=int(latest.get("fire", 0)),
                    esc_idle_us=int(latest.get("esc_idle_us", 1060)),
                    esc_fire_us=int(latest.get("esc_fire_us", 1200)),
                    fire_spin_ms=int(latest.get("fire_spin_ms", 450)),
                )

        payload = last_cmd.to_bytes()
        status_pkt = None

        if mock:
            mock_pan += int(0.35 * (last_cmd.pan - mock_pan))
            mock_tilt += int(0.35 * (last_cmd.tilt - mock_tilt))
            st = StatusCode.ESTOP if last_cmd.mod == ModCode.ESTOP else StatusCode.OK
            if last_cmd.mod == ModCode.HOME:
                st = StatusCode.HOME
            status_pkt = StatusPacket(status=int(st), limit_pan=0, limit_tilt=0)
            linked = True
            last_rx = time.time()
        else:
            try:
                if ser is not None and ser.is_open:
                    ser.write(payload)
                    waiting = ser.in_waiting
                    if waiting:
                        raw = ser.read(waiting)
                        packets = parser.feed(raw)
                        if packets:
                            status_pkt = packets[-1]
                            last_rx = time.time()
                            linked = True
                elif ser is None:
                    linked = False
            except Exception as exc:
                linked = False
                emit_log(f"Serial I/O hata: {exc}")
                _close_serial(ser)
                ser = None

        in_grace = (not mock) and (time.time() < grace_until)
        failsafe = (not mock) and (not in_grace) and ((time.time() - last_rx) > failsafe_timeout)
        if failsafe:
            linked = False

        telem = {
            "ts": time.time(),
            "linked": linked and not failsafe,
            "mock": mock,
            "failsafe": failsafe,
            "port": port,
            "status": int(status_pkt.status) if status_pkt else 0,
            "limit_pan": int(status_pkt.limit_pan) if status_pkt else 0,
            "limit_tilt": int(status_pkt.limit_tilt) if status_pkt else 0,
            "pan": int(last_cmd.pan if not mock else mock_pan),
            "tilt": int(last_cmd.tilt if not mock else mock_tilt),
            "fire": int(last_cmd.fire),
            "mod": int(last_cmd.mod),
        }
        put_latest(telem_q, telem)

        elapsed = time.time() - t0
        time.sleep(max(0.0, period - elapsed))

    _close_serial(ser)


def start_serial_process(
    cmd_q: Queue,
    telem_q: Queue,
    stop_event,
    config: dict,
    cfg_q: Optional[Queue] = None,
) -> Process:
    p = Process(
        target=serial_process_main,
        args=(cmd_q, telem_q, stop_event, config, cfg_q),
        name="SerialProcess",
        daemon=True,
    )
    p.start()
    return p
