"""Center video display with overlay painting."""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from aeroshield.vision.overlay import draw_crosshair, draw_detections, draw_hud
from aeroshield.workers.ipc import Detection


class VideoWidget(QWidget):
    def __init__(self, config: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.config = config or {}
        self.label = QLabel("KAMERA BAĞLANTISI BEKLENİYOR")
        self.label.setObjectName("VideoWait")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setMinimumSize(720, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self._last_pixmap: Optional[QPixmap] = None

    def update_frame(self, payload: dict[str, Any]) -> None:
        jpeg = payload.get("jpeg")
        if not jpeg:
            return
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        detections = []
        for d in payload.get("detections") or []:
            detections.append(
                Detection(
                    track_id=int(d.get("track_id", 0)),
                    x=float(d.get("x", 0)),
                    y=float(d.get("y", 0)),
                    w=float(d.get("w", 0)),
                    h=float(d.get("h", 0)),
                    conf=float(d.get("conf", 0)),
                    label=str(d.get("label", "?")),
                    hostile=bool(d.get("hostile", True)),
                    cx=float(d.get("cx", 0)),
                    cy=float(d.get("cy", 0)),
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
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        self._last_pixmap = pix
        self.label.setPixmap(
            pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._last_pixmap is not None:
            self.label.setPixmap(
                self._last_pixmap.scaled(
                    self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
