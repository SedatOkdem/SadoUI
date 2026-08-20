"""Right telemetry / log panel with compact metric rows."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class TelemetryPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 8, 2)
        root.setSpacing(8)

        header = QLabel("TELEMETRİ")
        header.setObjectName("PanelHeader")
        root.addWidget(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        self._vals: dict[str, QLabel] = {}
        specs = [
            ("fsm", "FSM"),
            ("stage", "AŞAMA"),
            ("estop", "E-STOP"),
            ("link", "ESP32"),
            ("fps", "FPS"),
            ("lat", "LATENCY"),
            ("pan", "PAN"),
            ("tilt", "TILT"),
            ("fire", "FIRE"),
            ("lock", "LOCK"),
            ("wez", "WEZ"),
            ("target", "HEDEF"),
            ("pos", "CX / CY"),
            ("vel", "VX / VY"),
            ("range", "MENZİL"),
            ("limits", "LIMIT"),
        ]
        for i, (key, title) in enumerate(specs):
            card, val = self._metric(title)
            self._vals[key] = val
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid)

        maint_lbl = QLabel("BAKIM SÜRESİ")
        maint_lbl.setObjectName("SectionTitle")
        root.addWidget(maint_lbl)
        self.maint_bar = QProgressBar()
        self.maint_bar.setRange(0, 600)
        self.maint_bar.setValue(600)
        self.maint_bar.setMinimumHeight(20)
        root.addWidget(self.maint_bar)

        log_header = QHBoxLayout()
        log_lbl = QLabel("SİSTEM LOG")
        log_lbl.setObjectName("SectionTitle")
        self.btn_clear_log = QPushButton("TEMİZLE")
        self.btn_clear_log.setObjectName("GhostButton")
        self.btn_clear_log.setToolTip("Log penceresini temizle (Ctrl+L)")
        self.btn_clear_log.clicked.connect(self.clear_log)
        log_header.addWidget(log_lbl)
        log_header.addStretch(1)
        log_header.addWidget(self.btn_clear_log)
        root.addLayout(log_header)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setMaximumHeight(220)
        root.addWidget(self.log)
        root.addStretch(1)

    def _metric(self, key: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("MetricCard")
        card.setMinimumHeight(48)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        k = QLabel(key)
        k.setObjectName("MetricKey")
        v = QLabel("—")
        v.setObjectName("MetricVal")
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.NoTextInteraction)
        lay.addWidget(k)
        lay.addWidget(v)
        return card, v

    def update_telemetry(self, t: dict) -> None:
        self._vals["fsm"].setText(str(t.get("fsm", "—")))
        self._vals["stage"].setText(f"A{t.get('stage', '—')}")
        if not t.get("estop_enabled", True):
            self._vals["estop"].setText("KAPALI")
            self._vals["estop"].setStyleSheet("color:#8aa0b4;")
        elif t.get("estop_active") or str(t.get("fsm", "")) == "ESTOP":
            src = str(t.get("estop_source", "SW"))
            self._vals["estop"].setText(f"AKTİF ({src})")
            self._vals["estop"].setStyleSheet("color:#ff7a8a;")
        else:
            self._vals["estop"].setText("HAZIR")
            self._vals["estop"].setStyleSheet("color:#5ee0c4;")
        mock = bool(t.get("mock"))
        linked = bool(t.get("linked"))
        failsafe = bool(t.get("failsafe"))
        if mock:
            link = "MOCK (kart yok)"
            color = "#f0b45a"
        elif failsafe:
            link = f"YOK {t.get('port', '')}".strip()
            color = "#ff7a8a"
        elif linked:
            link = f"BAĞLI {t.get('port', '')}".strip()
            color = "#5ee0c4"
        else:
            link = "BAĞLI DEĞİL"
            color = "#ff7a8a"
        self._vals["link"].setText(link)
        self._vals["link"].setStyleSheet(f"color:{color};")
        self._vals["fps"].setText(f"{float(t.get('fps', 0)):.1f}")
        self._vals["lat"].setText(f"{float(t.get('latency_ms', 0)):.0f} ms")
        self._vals["pan"].setText(f"{t.get('pan', 0)}°")
        self._vals["tilt"].setText(f"{t.get('tilt', 0)}°")
        self._vals["fire"].setText("ON" if t.get("fire") else "OFF")
        self._vals["lock"].setText("ON" if t.get("locked") else "OFF")
        self._vals["wez"].setText("OK" if t.get("wez_ok") else "—")
        pid = t.get("primary_id")
        label = str(t.get("primary_label", "—"))
        if pid is not None:
            self._vals["target"].setText(f"ID{pid} {label}")
        else:
            self._vals["target"].setText("—")
        self._vals["pos"].setText(f"{float(t.get('cx', 0)):.0f}, {float(t.get('cy', 0)):.0f}")
        self._vals["vel"].setText(f"{float(t.get('vx', 0)):.1f}, {float(t.get('vy', 0)):.1f}")
        rm = t.get("range_m")
        # Short display — full WEZ text overflows cards
        if rm is not None:
            wez = "OK" if t.get("wez_ok") else "—"
            self._vals["range"].setText(f"{float(rm):.1f} m  {wez}")
        else:
            self._vals["range"].setText("—")
        self._vals["limits"].setText(f"P{t.get('limit_pan', 0)} T{t.get('limit_tilt', 0)}")
        rem = float(t.get("maint_remaining_s", 600))
        self.maint_bar.setValue(int(max(0, rem)))
        self.maint_bar.setFormat(f"{int(rem)} sn kalan")

    def clear_log(self) -> None:
        self.log.clear()

    def append_log(self, line: str) -> None:
        if not line:
            return
        self.log.append(line)
        if self.log.document().blockCount() > 500:
            cursor = self.log.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
