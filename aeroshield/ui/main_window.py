"""Main GCS window — judge-ready operator console."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QKeyEvent
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from aeroshield.ui.control_panel import ControlPanel
from aeroshield.ui.telemetry_panel import TelemetryPanel
from aeroshield.ui.video_widget import VideoWidget


def _wrap_scroll(inner: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setWidget(inner)
    scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    return scroll


class MainWindow(QMainWindow):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(config.get("ui", {}).get("window_title", "AeroShield GCS"))
        self.resize(1680, 960)
        self.setMinimumSize(1360, 800)

        central = QWidget()
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setSpacing(10)

        left = QFrame()
        left.setObjectName("Panel")
        left.setMinimumWidth(320)
        left.setMaximumWidth(380)
        left.setFixedWidth(360)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        left_l.setSpacing(0)
        self.control = ControlPanel(config)
        left_l.addWidget(_wrap_scroll(self.control))

        center = QFrame()
        center.setObjectName("VideoChrome")
        center.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center_l = QVBoxLayout(center)
        center_l.setContentsMargins(10, 10, 10, 10)
        center_l.setSpacing(8)
        video_header = QHBoxLayout()
        vt = QLabel("OPTİK GÖRÜŞ  ·  VİZÖR / TAKİP")
        vt.setObjectName("VideoTitle")
        self.lbl_video_meta = QLabel("—")
        self.lbl_video_meta.setObjectName("StatusChip")
        video_header.addWidget(vt)
        video_header.addStretch(1)
        video_header.addWidget(self.lbl_video_meta)
        center_l.addLayout(video_header)
        self.video = VideoWidget(config)
        center_l.addWidget(self.video, 1)

        right = QFrame()
        right.setObjectName("Panel")
        right.setMinimumWidth(300)
        right.setMaximumWidth(360)
        right.setFixedWidth(340)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(10, 10, 10, 10)
        right_l.setSpacing(0)
        self.telemetry = TelemetryPanel()
        right_l.addWidget(_wrap_scroll(self.telemetry))

        body.addWidget(left, 0)
        body.addWidget(center, 1)
        body.addWidget(right, 0)
        root.addLayout(body, 1)

        self._apply_styles()
        self._manual_step = int(config.get("control", {}).get("manual_step", 25))

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setMinimumHeight(64)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand = QLabel("AEROSHIELD")
        brand.setObjectName("BrandMark")
        sub = QLabel("YER KONTROL İSTASYONU  ·  TEKNOFEST 2026")
        sub.setObjectName("BrandSub")
        brand_col.addWidget(brand)
        brand_col.addWidget(sub)
        brand_wrap = QWidget()
        brand_wrap.setLayout(brand_col)
        brand_wrap.setMinimumWidth(260)
        lay.addWidget(brand_wrap)
        lay.addStretch(1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_fsm = QLabel("FSM  BIT")
        self.chip_fsm.setObjectName("StatusChip")
        self.chip_stage = QLabel("AŞAMA  1")
        self.chip_stage.setObjectName("StatusChip")
        self.chip_link = QLabel("LINK  —")
        self.chip_link.setObjectName("StatusChip")
        self.chip_lock = QLabel("LOCK  —")
        self.chip_lock.setObjectName("StatusChip")
        self.chip_estop = QLabel("E-STOP  HAZIR")
        self.chip_estop.setObjectName("StatusChipOk")

        for chip in (
            self.chip_fsm,
            self.chip_stage,
            self.chip_link,
            self.chip_lock,
            self.chip_estop,
        ):
            chip.setAlignment(Qt.AlignCenter)
            chip.setMinimumHeight(28)
            chips.addWidget(chip)
        chip_wrap = QWidget()
        chip_wrap.setLayout(chips)
        lay.addWidget(chip_wrap)
        return bar

    def update_top_bar(self, t: dict) -> None:
        fsm = str(t.get("fsm", "-"))
        stage = t.get("stage", "-")
        self.chip_fsm.setText(f"FSM  {fsm}")
        self.chip_stage.setText(f"AŞAMA  {stage}")

        if t.get("mock"):
            self.chip_link.setText("LINK  MOCK")
            self.chip_link.setObjectName("StatusChipWarn")
        elif t.get("linked"):
            self.chip_link.setText("LINK  OK")
            self.chip_link.setObjectName("StatusChipOk")
        else:
            self.chip_link.setText("LINK  YOK")
            self.chip_link.setObjectName("StatusChipBad")
        self.chip_link.style().unpolish(self.chip_link)
        self.chip_link.style().polish(self.chip_link)

        if t.get("locked"):
            self.chip_lock.setText("LOCK  ON")
            self.chip_lock.setObjectName("StatusChipOk")
        else:
            self.chip_lock.setText("LOCK  OFF")
            self.chip_lock.setObjectName("StatusChip")
        self.chip_lock.style().unpolish(self.chip_lock)
        self.chip_lock.style().polish(self.chip_lock)

        if t.get("estop_active") or fsm == "ESTOP":
            src = t.get("estop_source", "SW")
            self.chip_estop.setText(f"E-STOP  AKTİF ({src})")
            self.chip_estop.setObjectName("StatusChipBad")
        elif fsm == "FAILSAFE":
            self.chip_estop.setText("FAILSAFE")
            self.chip_estop.setObjectName("StatusChipBad")
        else:
            self.chip_estop.setText("E-STOP  HAZIR")
            self.chip_estop.setObjectName("StatusChipOk")
        self.chip_estop.style().unpolish(self.chip_estop)
        self.chip_estop.style().polish(self.chip_estop)

        fps = float(t.get("fps", 0) or 0)
        lat = float(t.get("latency_ms", 0) or 0)
        self.lbl_video_meta.setText(f"{fps:.0f} FPS  ·  {lat:.0f} ms")

    def _apply_styles(self) -> None:
        qss = Path(__file__).with_name("styles.qss")
        if qss.exists():
            self.setStyleSheet(qss.read_text(encoding="utf-8"))
        self.setFont(QFont("Bahnschrift", 10))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        step = self._manual_step
        if key == Qt.Key_W:
            self.control.nudge(0, step)
        elif key == Qt.Key_S:
            self.control.nudge(0, -step)
        elif key == Qt.Key_A:
            self.control.nudge(-step, 0)
        elif key == Qt.Key_D:
            self.control.nudge(step, 0)
        elif key == Qt.Key_Space:
            self.control._on_fire()
        elif key == Qt.Key_Escape:
            self.control._on_estop()
        elif key == Qt.Key_L and event.modifiers() & Qt.ControlModifier:
            self.telemetry.clear_log()
        else:
            super().keyPressEvent(event)
