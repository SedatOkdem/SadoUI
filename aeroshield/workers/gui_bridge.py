"""QThread bridge from multiprocessing queues to Qt signals.

Heavy JPEG decode + overlay runs here so the Qt main thread only paints.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

from aeroshield.vision.overlay import draw_crosshair, draw_detections, draw_hud
from aeroshield.workers.ipc import Detection


class GuiBridge(QThread):
    frame_ready = pyqtSignal(object)  # QImage
    telemetry_ready = pyqtSignal(dict)
    log_ready = pyqtSignal(str)

    def __init__(
        self,
        ui_telem_q,
        serial_telem_q,
        op_q,
        config: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ui_telem_q = ui_telem_q
        self.serial_telem_q = serial_telem_q
        self.op_q = op_q
        self.config = config or {}
        self._running = True
        ui_hz = float(self.config.get("ui", {}).get("target_gui_hz", 25))
        self._min_frame_interval = 1.0 / max(10.0, min(45.0, ui_hz))
        self._min_telem_interval = 1.0 / max(10.0, min(30.0, ui_hz))
        self._last_frame_emit = 0.0
        self._last_telem_emit = 0.0
        self._last_frame_id = None

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            did_work = False

            try:
                while True:
                    st = self.serial_telem_q.get_nowait()
                    did_work = True
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
                    did_work = True
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

                now = time.time()
                if now - self._last_telem_emit >= self._min_telem_interval:
                    self._last_telem_emit = now
                    # Strip bulky fields before crossing to GUI thread
                    telem = {
                        k: v
                        for k, v in latest.items()
                        if k not in ("jpeg", "detections", "log_events")
                    }
                    self.telemetry_ready.emit(telem)

                jpeg = latest.get("jpeg")
                if jpeg is not None and (now - self._last_frame_emit) >= self._min_frame_interval:
                    frame_id = latest.get("frame_id")
                    if frame_id is None or frame_id != self._last_frame_id:
                        qimg = self._render_frame(latest)
                        if qimg is not None:
                            self._last_frame_emit = now
                            self._last_frame_id = frame_id
                            self.frame_ready.emit(qimg)

            time.sleep(0.002 if did_work else 0.008)

    def _render_frame(self, payload: dict[str, Any]) -> Optional[QImage]:
        jpeg = payload.get("jpeg")
        if not jpeg:
            return None
        try:
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return None

            # Scale boxes from camera space → decoded JPEG space
            cam_w = float(payload.get("width") or frame.shape[1] or 1)
            cam_h = float(payload.get("height") or frame.shape[0] or 1)
            sx = float(frame.shape[1]) / cam_w
            sy = float(frame.shape[0]) / cam_h
            # Optional extra downscale if JPEG still larger than display budget
            max_w = int(self.config.get("ui", {}).get("display_max_width", 960))
            if frame.shape[1] > max_w:
                extra = max_w / float(frame.shape[1])
                frame = cv2.resize(
                    frame,
                    (int(frame.shape[1] * extra), int(frame.shape[0] * extra)),
                    interpolation=cv2.INTER_AREA,
                )
                sx *= extra
                sy *= extra

            detections = []
            for d in payload.get("detections") or []:
                detections.append(
                    Detection(
                        track_id=int(d.get("track_id", 0)),
                        x=float(d.get("x", 0)) * sx,
                        y=float(d.get("y", 0)) * sy,
                        w=float(d.get("w", 0)) * sx,
                        h=float(d.get("h", 0)) * sy,
                        conf=float(d.get("conf", 0)),
                        label=str(d.get("label", "?")),
                        hostile=bool(d.get("hostile", True)),
                        cx=float(d.get("cx", 0)) * sx,
                        cy=float(d.get("cy", 0)) * sy,
                        range_m=d.get("range_m"),
                    )
                )
            draw_detections(
                frame,
                detections,
                payload.get("primary_id"),
                bool(payload.get("locked")),
                int(payload.get("stage", 1)),
                self.config,
            )
            draw_crosshair(frame)
            draw_hud(
                frame,
                str(payload.get("fsm", "-")),
                int(payload.get("stage", 1)),
                float(payload.get("fps", 0)),
                bool(payload.get("linked", False)),
                bool(payload.get("wez_ok", False)),
                bool(payload.get("locked", False)),
                str(payload.get("range_text", "")),
                bool(payload.get("estop_active", False)),
                bool(payload.get("mock", False)),
            )

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        except Exception:
            return None
