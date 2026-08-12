"""Center video display — receives pre-rendered QImage from GuiBridge."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget


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
        self._busy = False

    def update_frame(self, qimg: QImage) -> None:
        if self._busy or qimg is None or qimg.isNull():
            return
        self._busy = True
        try:
            pix = QPixmap.fromImage(qimg)
            self._last_pixmap = pix
            self.label.setPixmap(
                pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
            )
        finally:
            self._busy = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._last_pixmap is not None:
            self.label.setPixmap(
                self._last_pixmap.scaled(
                    self.label.size(), Qt.KeepAspectRatio, Qt.FastTransformation
                )
            )
