"""QThread bridge from multiprocessing queues to Qt signals."""

from __future__ import annotations

import time
from typing import Any, Optional

from PyQt5.QtCore import QThread, pyqtSignal


class GuiBridge(QThread):
    frame_ready = pyqtSignal(object)  # dict with jpeg + detections + hud flags
    telemetry_ready = pyqtSignal(dict)
    log_ready = pyqtSignal(str)

    def __init__(self, ui_telem_q, serial_telem_q, op_q, parent=None) -> None:
        super().__init__(parent)
        self.ui_telem_q = ui_telem_q
        self.serial_telem_q = serial_telem_q
        self.op_q = op_q
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            # Forward serial telemetry into control process via op_q
            try:
                while True:
                    st = self.serial_telem_q.get_nowait()
                    if isinstance(st, dict):
                        payload = dict(st)
                        payload["type"] = "serial_telem"
                        try:
                            self.op_q.put_nowait(payload)
                        except Exception:
                            pass
                        if payload.get("log_line"):
                            self.log_ready.emit(str(payload["log_line"]))
            except Exception:
                pass

            latest = None
            try:
                while True:
                    item = self.ui_telem_q.get_nowait()
                    if isinstance(item, dict) and item.get("log_line") and "jpeg" not in item:
                        self.log_ready.emit(str(item["log_line"]))
                    else:
                        latest = item
            except Exception:
                pass

            if latest and isinstance(latest, dict):
                for ev in latest.get("log_events") or []:
                    self.log_ready.emit(str(ev))
                if latest.get("log_line"):
                    self.log_ready.emit(str(latest["log_line"]))
                self.telemetry_ready.emit(latest)
                if latest.get("jpeg") is not None:
                    self.frame_ready.emit(latest)
            else:
                time.sleep(0.01)
