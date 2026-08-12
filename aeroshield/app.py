"""Application orchestration: processes + main window."""

from __future__ import annotations

import time
from multiprocessing import Event, Queue
from pathlib import Path
from typing import Any, Optional

import yaml
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication

from aeroshield.ui.main_window import MainWindow
from aeroshield.workers.camera_worker import start_camera_process
from aeroshield.workers.control_worker import start_control_process
from aeroshield.workers.gui_bridge import GuiBridge
from aeroshield.workers.inference_worker import start_inference_process
from aeroshield.workers.serial_worker import start_serial_process


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class AeroShieldApp:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.stop_event = Event()

        self.frame_q: Queue = Queue(maxsize=2)
        self.track_q: Queue = Queue(maxsize=2)
        self.uart_cmd_q: Queue = Queue(maxsize=4)
        self.serial_telem_q: Queue = Queue(maxsize=4)
        self.op_q: Queue = Queue(maxsize=16)
        self.ui_telem_q: Queue = Queue(maxsize=8)

        self.processes = []
        self.bridge: Optional[GuiBridge] = None
        self.window: Optional[MainWindow] = None
        self._maint_remaining = float(self.config.get("safety", {}).get("maintenance_total_s", 600))
        self._maint_active = False
        self._maint_tick = None
        self._estop_ack_logged = False
        self._last_slider_sync = 0.0
        self._last_range_sync = 0.0

    def start_workers(self) -> None:
        self.processes = [
            start_camera_process(self.frame_q, self.stop_event, self.config),
            start_inference_process(self.frame_q, self.track_q, self.stop_event, self.config),
            start_control_process(
                self.track_q,
                self.op_q,
                self.uart_cmd_q,
                self.ui_telem_q,
                self.stop_event,
                self.config,
            ),
            start_serial_process(self.uart_cmd_q, self.serial_telem_q, self.stop_event, self.config),
        ]

    def stop_workers(self) -> None:
        self.stop_event.set()
        if self.bridge is not None:
            self.bridge.stop()
            self.bridge.wait(2000)
        for p in self.processes:
            p.join(timeout=2.0)
            if p.is_alive():
                p.terminate()

    def run(self) -> int:
        import sys

        # Required on Windows for multiprocessing + Qt
        app = QApplication(sys.argv)
        app.setApplicationName("AeroShield GCS")

        self.start_workers()
        self.window = MainWindow(self.config)
        self.window.control.command_changed.connect(self._on_command)
        self.window.control.estop_triggered.connect(
            lambda msg: self.window.telemetry.append_log(msg) if self.window else None
        )
        self.window.control.estop_reset_requested.connect(
            lambda: self.window.telemetry.append_log("E-STOP reset talebi (hakem)") if self.window else None
        )
        self.window.control.refresh_ports_clicked.connect(self._refresh_ports)
        self._refresh_ports()

        self._cmd_timer = QTimer()
        self._cmd_timer.setInterval(200)
        self._cmd_timer.timeout.connect(self._heartbeat_command)
        self._cmd_timer.start()

        self.bridge = GuiBridge(self.ui_telem_q, self.serial_telem_q, self.op_q, self.config)
        self.bridge.frame_ready.connect(self.window.video.update_frame, type=Qt.QueuedConnection)
        self.bridge.telemetry_ready.connect(self._on_telemetry, type=Qt.QueuedConnection)
        self.bridge.log_ready.connect(self.window.telemetry.append_log, type=Qt.QueuedConnection)
        self.bridge.start()

        # Seed initial command
        self._on_command(self.window.control.build_command())
        self.window.telemetry.append_log("AeroShield GCS started")
        self.window.telemetry.append_log(
            f"Serial mock={self.config.get('serial', {}).get('mock')} "
            f"camera={self.config.get('camera', {}).get('index')}"
        )

        self.window.show()
        code = app.exec_()
        self._cmd_timer.stop()
        self.stop_workers()
        return code

    def _heartbeat_command(self) -> None:
        if self.window is not None:
            self._on_command(self.window.control.build_command())

    def _on_command(self, cmd: dict) -> None:
        payload = dict(cmd)
        # Maintenance timer accounting
        if payload.get("maint") and not self._maint_active:
            self._maint_active = True
            self._maint_tick = time.time()
            min_s = float(self.config.get("safety", {}).get("maintenance_min_s", 30))
            self._maint_remaining = max(0.0, self._maint_remaining - min_s)
            if self.window:
                self.window.telemetry.append_log(
                    f"Bakım başladı (−{int(min_s)} sn). Kalan: {int(self._maint_remaining)} sn"
                )
        elif not payload.get("maint") and self._maint_active:
            self._maint_active = False
            if self._maint_tick is not None:
                elapsed = time.time() - self._maint_tick
                min_s = float(self.config.get("safety", {}).get("maintenance_min_s", 30))
                extra = max(0.0, elapsed - min_s)
                if extra > 0:
                    self._maint_remaining = max(0.0, self._maint_remaining - extra)
                if self.window:
                    self.window.telemetry.append_log(
                        f"Bakım bitti. Kalan: {int(self._maint_remaining)} sn"
                    )
            self._maint_tick = None

        payload["maint_remaining_s"] = self._maint_remaining
        try:
            self.op_q.put_nowait(payload)
        except Exception:
            try:
                self.op_q.get_nowait()
            except Exception:
                pass
            try:
                self.op_q.put_nowait(payload)
            except Exception:
                pass

    def _on_telemetry(self, telem: dict) -> None:
        if self.window is None:
            return
        telem = dict(telem)
        telem["maint_remaining_s"] = self._maint_remaining
        self.window.telemetry.update_telemetry(telem)
        self.window.update_top_bar(telem)
        if telem.get("estop_cleared_ack"):
            self.window.control.on_estop_cleared()
            if not self._estop_ack_logged:
                self.window.telemetry.append_log("E-STOP reset onaylandı → READY")
                self._estop_ack_logged = True
        elif telem.get("estop_reset_pending") or self.window.control._reset_pending:
            self._estop_ack_logged = False
            if telem.get("estop_reset_waiting_hw"):
                self.window.control.set_reset_pending(waiting_hw=True)
        elif telem.get("estop_active") or str(telem.get("fsm")) == "ESTOP":
            self._estop_ack_logged = False
            if not self.window.control._reset_pending:
                self.window.control.set_estop_active(
                    True, str(telem.get("estop_source", "SW"))
                )
        else:
            self._estop_ack_logged = False
            # Ensure UI unlocks if FSM already left ESTOP without ack edge
            if self.window.control._estop and not self.window.control._reset_pending:
                if str(telem.get("fsm")) not in ("ESTOP", "FAILSAFE") and not telem.get("estop_active"):
                    self.window.control.on_estop_cleared()
        if not self.window.control.use_manual_range.isChecked():
            rm = telem.get("range_m")
            now = time.time()
            if rm is not None and (now - self._last_range_sync) > 0.25:
                self._last_range_sync = now
                self.window.control.range_spin.blockSignals(True)
                self.window.control.range_spin.setValue(float(rm))
                self.window.control.range_spin.blockSignals(False)
        # Keep manual sliders roughly synced in auto stages (throttled)
        if int(telem.get("stage", 1)) != 1:
            now = time.time()
            if (now - self._last_slider_sync) > 0.15:
                self._last_slider_sync = now
                self.window.control.set_pan_tilt(int(telem.get("pan", 0)), int(telem.get("tilt", 0)))

    def _refresh_ports(self) -> None:
        ports = []
        try:
            from serial.tools import list_ports

            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        if self.window is not None:
            self.window.control.populate_ports(ports or ["COM3"])
            self.window.telemetry.append_log(
                "COM ports: " + (", ".join(ports) if ports else "none")
            )
