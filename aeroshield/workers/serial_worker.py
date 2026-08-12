"""ESP32 UART worker process (real or mock)."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any

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

    return serial.Serial(port=port, baudrate=baud, timeout=timeout_s)


def serial_process_main(
    cmd_q: Queue,
    telem_q: Queue,
    stop_event,
    config: dict[str, Any],
) -> None:
    ser_cfg = config.get("serial", {})
    mock = bool(ser_cfg.get("mock", True))
    rate_hz = float(ser_cfg.get("rate_hz", 30))
    period = 1.0 / max(1.0, rate_hz)
    failsafe_timeout = float(ser_cfg.get("failsafe_timeout_s", 0.5))
    parser = PacketParser()
    ser = None
    linked = False
    last_rx = time.time()
    last_cmd = CommandPacket(mod=ModCode.HOME)
    mock_pan = 0
    mock_tilt = 0

    if not mock:
        try:
            ser = _open_serial(
                str(ser_cfg.get("port", "COM3")),
                int(ser_cfg.get("baud", 115200)),
                float(ser_cfg.get("timeout_s", 0.05)),
            )
            linked = True
            last_rx = time.time()
        except Exception as exc:
            put_latest(
                telem_q,
                {
                    "linked": False,
                    "mock": False,
                    "failsafe": True,
                    "log_line": f"Serial open failed: {exc}",
                    "ts": time.time(),
                },
            )
            mock = True

    while not stop_event.is_set():
        t0 = time.time()
        # Drain commands, keep latest
        latest = None
        try:
            while True:
                latest = cmd_q.get_nowait()
        except Exception:
            pass

        if latest is not None:
            if isinstance(latest, CommandPacket):
                last_cmd = latest
            elif isinstance(latest, dict):
                last_cmd = CommandPacket(
                    mod=int(latest.get("mod", ModCode.MANUAL)),
                    pan=int(latest.get("pan", 0)),
                    tilt=int(latest.get("tilt", 0)),
                    fire=int(latest.get("fire", 0)),
                )

        payload = last_cmd.to_bytes()

        status_pkt = None
        if mock:
            # Echo mock turret motion toward command
            mock_pan += int(0.35 * (last_cmd.pan - mock_pan))
            mock_tilt += int(0.35 * (last_cmd.tilt - mock_tilt))
            st = StatusCode.ESTOP if last_cmd.mod == ModCode.ESTOP else StatusCode.OK
            if last_cmd.mod == ModCode.HOME:
                st = StatusCode.HOME
            status_pkt = StatusPacket(status=int(st), limit_pan=0, limit_tilt=0)
            linked = True
            last_rx = time.time()
            # Simulate loopback bytes through parser
            parser.feed(status_pkt.to_bytes())
            parsed = parser.feed(b"")
            if parsed:
                status_pkt = parsed[-1]
        else:
            try:
                if ser and ser.is_open:
                    ser.write(payload)
                    waiting = ser.in_waiting
                    if waiting:
                        raw = ser.read(waiting)
                        packets = parser.feed(raw)
                        if packets:
                            status_pkt = packets[-1]
                            last_rx = time.time()
                            linked = True
            except Exception as exc:
                linked = False
                put_latest(
                    telem_q,
                    {
                        "linked": False,
                        "mock": False,
                        "failsafe": True,
                        "log_line": f"Serial I/O error: {exc}",
                        "ts": time.time(),
                    },
                )

        failsafe = (time.time() - last_rx) > failsafe_timeout
        if failsafe and not mock:
            linked = False

        telem = {
            "ts": time.time(),
            "linked": linked and not failsafe,
            "mock": mock,
            "failsafe": failsafe and not mock,
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

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass


def start_serial_process(cmd_q: Queue, telem_q: Queue, stop_event, config: dict) -> Process:
    p = Process(
        target=serial_process_main,
        args=(cmd_q, telem_q, stop_event, config),
        name="SerialProcess",
        daemon=True,
    )
    p.start()
    return p
