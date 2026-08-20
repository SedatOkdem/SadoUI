"""Left control panel: stages, fire, E-Stop, axes, forbidden zone."""

from __future__ import annotations

import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    command_changed = pyqtSignal(dict)
    refresh_ports_clicked = pyqtSignal()
    estop_triggered = pyqtSignal(str)
    estop_reset_requested = pyqtSignal()

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        safety = config.get("safety", {})
        ctrl = config.get("control", {})

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 8, 2)
        root.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        header = QLabel("KOMUTA")
        header.setObjectName("PanelHeader")
        root.addWidget(header)

        self.lbl_esp = QLabel("ESP32  ·  BEKLENİYOR")
        self.lbl_esp.setObjectName("EspBannerWarn")
        self.lbl_esp.setAlignment(Qt.AlignCenter)
        self.lbl_esp.setMinimumHeight(42)
        self.lbl_esp.setWordWrap(True)
        root.addWidget(self.lbl_esp)

        root.addWidget(self._section("GÖREV AŞAMASI"))
        stage_row = QHBoxLayout()
        stage_row.setSpacing(6)
        self.stage_group = QButtonGroup(self)
        self.btn_s1 = QPushButton("A1\nMANUEL")
        self.btn_s2 = QPushButton("A2\nSÜRÜ")
        self.btn_s3 = QPushButton("A3\nWEZ")
        for i, btn in enumerate((self.btn_s1, self.btn_s2, self.btn_s3), start=1):
            btn.setCheckable(True)
            btn.setMinimumHeight(44)
            btn.setMaximumHeight(52)
            self.stage_group.addButton(btn, i)
            stage_row.addWidget(btn)
        self.btn_s1.setChecked(True)
        root.addLayout(stage_row)

        self.lbl_estop_banner = QLabel("")
        self.lbl_estop_banner.setObjectName("EstopBanner")
        self.lbl_estop_banner.setWordWrap(True)
        self.lbl_estop_banner.hide()
        root.addWidget(self.lbl_estop_banner)

        root.addWidget(self._section("ANGAJMAN"))
        self.btn_start = QPushButton("GÖREVİ BAŞLAT  ·  SEARCH")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_fire = QPushButton("ATEŞ")
        self.btn_fire.setObjectName("FireButton")
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setObjectName("EStopButton")
        self.btn_estop.setCheckable(True)
        self.btn_reset_estop = QPushButton("ESTOP RESET  ·  HAKEM")
        self.btn_reset_estop.setEnabled(False)
        self._estop_enabled = bool(config.get("safety", {}).get("estop_enabled", False))
        if not self._estop_enabled:
            self.btn_estop.setEnabled(False)
            self.btn_estop.setText("E-STOP KAPALI")
            self.btn_reset_estop.setEnabled(False)
        self.btn_maint = QPushButton("BAKIM MOLASI")
        self.btn_maint.setCheckable(True)
        root.addWidget(self.btn_start)
        root.addWidget(self.btn_fire)
        root.addWidget(self.btn_estop)
        root.addWidget(self.btn_reset_estop)
        root.addWidget(self.btn_maint)

        root.addWidget(self._section("YÖNELİM  ·  WASD"))
        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.pan_slider = QSlider(Qt.Horizontal)
        self.tilt_slider = QSlider(Qt.Horizontal)
        pan_lim = ctrl.get("pan_limits_deg", [0, 270])
        tilt_lim = ctrl.get("tilt_limits_deg", [-30, 60])
        self.pan_slider.setRange(int(pan_lim[0]), int(pan_lim[1]))
        self.tilt_slider.setRange(int(tilt_lim[0]), int(tilt_lim[1]))
        home_pan = int(ctrl.get("home_pan_deg", 135))
        home_tilt = int(ctrl.get("home_tilt_deg", 0))
        self.pan_slider.setValue(home_pan)
        self.tilt_slider.setValue(home_tilt)
        self.pan_spin = QSpinBox()
        self.tilt_spin = QSpinBox()
        self.pan_spin.setRange(int(pan_lim[0]), int(pan_lim[1]))
        self.tilt_spin.setRange(int(tilt_lim[0]), int(tilt_lim[1]))
        self.pan_spin.setSuffix("°")
        self.tilt_spin.setSuffix("°")
        self.pan_spin.setKeyboardTracking(False)
        self.tilt_spin.setKeyboardTracking(False)
        self.pan_spin.setFocusPolicy(Qt.ClickFocus)
        self.tilt_spin.setFocusPolicy(Qt.ClickFocus)
        self.pan_spin.setFixedWidth(78)
        self.tilt_spin.setFixedWidth(78)
        self.pan_spin.setValue(home_pan)
        self.tilt_spin.setValue(home_tilt)
        form.addRow("PAN", self._slider_row(self.pan_slider, self.pan_spin))
        form.addRow("TILT", self._slider_row(self.tilt_slider, self.tilt_spin))
        root.addLayout(form)

        root.addWidget(self._section("RS2205  ·  ESC SİNYAL  (L=R)"))
        weapon = config.get("weapon", {})
        form_esc = QFormLayout()
        form_esc.setSpacing(6)
        form_esc.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form_esc.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.esc_idle_spin = QSpinBox()
        self.esc_fire_spin = QSpinBox()
        self.esc_spin_ms = QSpinBox()
        for sp, lo, hi, val, suf in (
            (self.esc_idle_spin, 1000, 2000, int(weapon.get("esc_idle_us", 1060)), " µs"),
            (self.esc_fire_spin, 1000, 2000, int(weapon.get("esc_fire_us", 1200)), " µs"),
            (self.esc_spin_ms, 50, 2000, int(float(weapon.get("fire_spin_s", 0.45)) * 1000), " ms"),
        ):
            sp.setRange(lo, hi)
            sp.setValue(val)
            sp.setSuffix(suf)
            sp.setKeyboardTracking(False)
            sp.setFocusPolicy(Qt.ClickFocus)
            sp.setFixedWidth(96)
        self.esc_idle_spin.setToolTip("Sol+sağ ESC aynı — hazır / yavaş (1000=dur)")
        self.esc_fire_spin.setToolTip("Sol+sağ ESC aynı ateş PWM — varsayılan 1200, paneldan değiştir")
        self.esc_spin_ms.setToolTip("Ateş yüksek devir süresi (her iki motor)")
        form_esc.addRow("IDLE L=R", self.esc_idle_spin)
        form_esc.addRow("ATEŞ L=R", self.esc_fire_spin)
        form_esc.addRow("SÜRE", self.esc_spin_ms)
        hint_esc = QLabel("Ateşte L=R aynı µs. Varsayılan ateş 1200; üst sınır şimdilik yok (max 2000).")
        hint_esc.setObjectName("HintLabel")
        hint_esc.setWordWrap(True)
        root.addLayout(form_esc)
        root.addWidget(hint_esc)

        root.addWidget(self._section("YASAK PAN BÖLGESİ"))
        self.forbid_min = QSlider(Qt.Horizontal)
        self.forbid_max = QSlider(Qt.Horizontal)
        self.forbid_min.setRange(0, 270)
        self.forbid_max.setRange(0, 270)
        self.forbid_min.setValue(int(safety.get("pan_forbidden_min", 200)))
        self.forbid_max.setValue(int(safety.get("pan_forbidden_max", 270)))
        form2 = QFormLayout()
        form2.setSpacing(6)
        form2.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form2.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.forbid_min_lbl = QLabel(str(self.forbid_min.value()))
        self.forbid_min_lbl.setObjectName("ValueAccent")
        self.forbid_min_lbl.setMinimumWidth(36)
        self.forbid_min_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.forbid_max_lbl = QLabel(str(self.forbid_max.value()))
        self.forbid_max_lbl.setObjectName("ValueAccent")
        self.forbid_max_lbl.setMinimumWidth(36)
        self.forbid_max_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form2.addRow("MIN", self._slider_row(self.forbid_min, self.forbid_min_lbl))
        form2.addRow("MAX", self._slider_row(self.forbid_max, self.forbid_max_lbl))
        root.addLayout(form2)

        root.addWidget(self._section("MENZİL / UART"))
        form3 = QFormLayout()
        form3.setSpacing(6)
        form3.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(0.0, 30.0)
        self.range_spin.setDecimals(1)
        self.range_spin.setSuffix(" m")
        self.range_spin.setSpecialValueText("Auto")
        self.range_spin.setValue(0.0)
        self.use_manual_range = QCheckBox("Manuel menzil (A3 WEZ)")
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItem(str(config.get("serial", {}).get("port", "COM3")))
        self.mock_check = QCheckBox("Mock ESP32 (simülasyon)")
        self.mock_check.setChecked(bool(config.get("serial", {}).get("mock", True)))
        self.btn_refresh_ports = QPushButton("COM TARA")
        self.btn_refresh_ports.setObjectName("GhostButton")
        self.btn_connect = QPushButton("ESP BAĞLAN")
        self.btn_connect.setObjectName("PrimaryButton")
        form3.addRow(self.use_manual_range)
        form3.addRow("MENZİL", self.range_spin)
        form3.addRow("PORT", self.port_combo)
        form3.addRow(self.mock_check)
        form3.addRow(self.btn_refresh_ports)
        form3.addRow(self.btn_connect)
        root.addLayout(form3)

        hint = QLabel("Aşama 1: kaydırıcı/sayı veya WASD basılı tut  ·  Aşama 2/3: otomatik takip  ·  Space ateş")
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch(1)

        self._estop_lockables = (
            self.btn_start,
            self.btn_fire,
            self.btn_s1,
            self.btn_s2,
            self.btn_s3,
            self.pan_slider,
            self.tilt_slider,
            self.pan_spin,
            self.tilt_spin,
            self.btn_maint,
        )

        self.stage_group.idClicked.connect(self._emit)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_fire.pressed.connect(self._on_fire)
        self.btn_estop.clicked.connect(self._on_estop)
        self.btn_reset_estop.clicked.connect(self._on_reset_estop)
        self.btn_maint.toggled.connect(self._emit)
        self.pan_slider.valueChanged.connect(self._on_pan)
        self.tilt_slider.valueChanged.connect(self._on_tilt)
        self.pan_spin.valueChanged.connect(self._on_pan_spin)
        self.tilt_spin.valueChanged.connect(self._on_tilt_spin)
        self.esc_idle_spin.valueChanged.connect(self._on_esc_idle)
        self.esc_fire_spin.valueChanged.connect(self._emit)
        self.esc_spin_ms.valueChanged.connect(self._emit)
        self.forbid_min.valueChanged.connect(self._on_forbid)
        self.forbid_max.valueChanged.connect(self._on_forbid)
        self.use_manual_range.toggled.connect(self._emit)
        self.range_spin.valueChanged.connect(self._emit)
        self.btn_refresh_ports.clicked.connect(self.refresh_ports_clicked.emit)
        self.btn_connect.clicked.connect(self._on_connect_esp)
        self.mock_check.toggled.connect(self._emit)

        self._estop = False
        self._estop_clear = False
        self._reset_pending = False
        self._estop_ignore_until = 0.0
        self._start_pulse = False
        self._fire_pulse = False
        self._jog_pan = 0
        self._jog_tilt = 0
        self._axis_emit = QTimer(self)
        self._axis_emit.setSingleShot(True)
        self._axis_emit.timeout.connect(self._emit)

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _slider_row(self, slider: QSlider, editor: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(slider, 1)
        lay.addWidget(editor)
        return w

    def set_estop_active(self, active: bool, source: str = "SW") -> None:
        if active and time.time() < self._estop_ignore_until:
            return
        if active == self._estop and self.btn_estop.isChecked() == active:
            if active:
                self._update_estop_banner(source)
            return
        self._estop = active
        self.btn_estop.blockSignals(True)
        self.btn_estop.setChecked(active)
        self.btn_estop.blockSignals(False)
        self.btn_estop.setText("■ E-STOP AKTİF" if active else "E-STOP")
        self.btn_reset_estop.setEnabled(active)
        self._apply_estop_lock(active)
        if active:
            self._update_estop_banner(source)
            self.btn_maint.setChecked(False)
        else:
            self.lbl_estop_banner.hide()

    def _update_estop_banner(self, source: str) -> None:
        src = source or "SW"
        self.lbl_estop_banner.setText(
            f"ACİL DURDURMA AKTİF ({src}) — Reset için hakem onayı gerekir."
        )
        self.lbl_estop_banner.show()

    def _apply_estop_lock(self, locked: bool) -> None:
        for widget in self._estop_lockables:
            widget.setEnabled(not locked)
        self.btn_estop.setEnabled(True)

    def on_estop_cleared(self) -> None:
        self._estop_clear = False
        self._reset_pending = False
        self._estop_ignore_until = time.time() + 2.0
        self.set_estop_active(False)

    def set_reset_pending(self, waiting_hw: bool = False) -> None:
        self._reset_pending = True
        extra = " (donanım onayı bekleniyor…)" if waiting_hw else ""
        self.lbl_estop_banner.setText(
            f"E-STOP RESET TALEBİ{extra} — Hakem onayı sonrası sistem READY olacak."
        )
        self.lbl_estop_banner.show()
        self.btn_reset_estop.setEnabled(False)

    def _on_pan(self, v: int) -> None:
        self.pan_spin.blockSignals(True)
        self.pan_spin.setValue(int(v))
        self.pan_spin.blockSignals(False)
        self._axis_emit.start(40)

    def _on_tilt(self, v: int) -> None:
        self.tilt_spin.blockSignals(True)
        self.tilt_spin.setValue(int(v))
        self.tilt_spin.blockSignals(False)
        self._axis_emit.start(40)

    def _on_pan_spin(self, v: int) -> None:
        self.pan_slider.blockSignals(True)
        self.pan_slider.setValue(int(v))
        self.pan_slider.blockSignals(False)
        self._emit()

    def _on_tilt_spin(self, v: int) -> None:
        self.tilt_slider.blockSignals(True)
        self.tilt_slider.setValue(int(v))
        self.tilt_slider.blockSignals(False)
        self._emit()

    def _on_esc_idle(self, v: int) -> None:
        if self.esc_fire_spin.value() < v:
            self.esc_fire_spin.blockSignals(True)
            self.esc_fire_spin.setValue(v)
            self.esc_fire_spin.blockSignals(False)
        self._emit()

    def _on_forbid(self, _: int) -> None:
        if self.forbid_min.value() > self.forbid_max.value():
            self.forbid_max.setValue(self.forbid_min.value())
        self.forbid_min_lbl.setText(str(self.forbid_min.value()))
        self.forbid_max_lbl.setText(str(self.forbid_max.value()))
        self._emit()

    def _on_start(self) -> None:
        if self._estop:
            return
        self._start_pulse = True
        self._emit()
        self._start_pulse = False

    def _on_fire(self) -> None:
        if self._estop:
            return
        self._fire_pulse = True
        self._emit()
        self._fire_pulse = False

    def _on_estop(self) -> None:
        if not getattr(self, "_estop_enabled", False):
            return
        if self._estop:
            return
        self.set_estop_active(True, "SW")
        self.estop_triggered.emit("Operatör E-STOP")
        self._emit()

    def _on_reset_estop(self) -> None:
        if not self._estop or self._reset_pending:
            return
        self._estop_clear = True
        self.set_reset_pending(waiting_hw=False)
        self.estop_reset_requested.emit()
        self._emit()
        # Unlock immediately — worker used to bounce back to ESTOP from ESP echo
        self.on_estop_cleared()

    def set_jog(self, pan: int, tilt: int) -> None:
        if self._estop:
            pan, tilt = 0, 0
        pan = 0 if pan == 0 else (1 if pan > 0 else -1)
        tilt = 0 if tilt == 0 else (1 if tilt > 0 else -1)
        if pan == self._jog_pan and tilt == self._jog_tilt:
            return
        self._jog_pan = pan
        self._jog_tilt = tilt
        self._emit()

    def set_pan_tilt(self, pan: int, tilt: int) -> None:
        if self._estop:
            return
        self.pan_slider.blockSignals(True)
        self.tilt_slider.blockSignals(True)
        self.pan_spin.blockSignals(True)
        self.tilt_spin.blockSignals(True)
        self.pan_slider.setValue(int(pan))
        self.tilt_slider.setValue(int(tilt))
        self.pan_spin.setValue(int(pan))
        self.tilt_spin.setValue(int(tilt))
        self.pan_slider.blockSignals(False)
        self.tilt_slider.blockSignals(False)
        self.pan_spin.blockSignals(False)
        self.tilt_spin.blockSignals(False)

    def set_esp_status(
        self, linked: bool, mock: bool, failsafe: bool = False, port: str = ""
    ) -> None:
        port_txt = f"  {port}" if port else ""
        if mock:
            self.lbl_esp.setText("ESP32  ·  MOCK  (kart bağlı değil)")
            self.lbl_esp.setObjectName("EspBannerWarn")
        elif failsafe:
            self.lbl_esp.setText(f"ESP32  ·  BAĞLI DEĞİL{port_txt}")
            self.lbl_esp.setObjectName("EspBannerBad")
        elif linked:
            self.lbl_esp.setText(f"ESP32  ·  BAĞLI{port_txt}")
            self.lbl_esp.setObjectName("EspBannerOk")
        else:
            self.lbl_esp.setText(f"ESP32  ·  BAĞLI DEĞİL{port_txt}")
            self.lbl_esp.setObjectName("EspBannerBad")
        self.lbl_esp.style().unpolish(self.lbl_esp)
        self.lbl_esp.style().polish(self.lbl_esp)

    def _on_connect_esp(self) -> None:
        self.mock_check.setChecked(False)
        cmd = self.build_command()
        cmd["serial_reconnect"] = True
        self.command_changed.emit(cmd)

    def populate_ports(self, ports: list[str]) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems(ports or ["COM3"])
        if current:
            self.port_combo.setEditText(current)

    def build_command(self) -> dict:
        manual_range = None
        if self.use_manual_range.isChecked() and self.range_spin.value() > 0:
            manual_range = float(self.range_spin.value())
        return {
            "stage": int(self.stage_group.checkedId()),
            "start_mission": self._start_pulse,
            "estop": bool(self._estop) and not self._reset_pending,
            "estop_clear": bool(self._reset_pending or self._estop_clear),
            "reset_pending": self._reset_pending,
            "maint": self.btn_maint.isChecked() and not self._estop,
            "fire": self._fire_pulse,
            "pan_cmd": int(self.pan_slider.value()),
            "tilt_cmd": int(self.tilt_slider.value()),
            "pan_jog": int(self._jog_pan),
            "tilt_jog": int(self._jog_tilt),
            "esc_idle_us": int(self.esc_idle_spin.value()),
            "esc_fire_us": int(self.esc_fire_spin.value()),
            "fire_spin_ms": int(self.esc_spin_ms.value()),
            "pan_forbidden_min": float(self.forbid_min.value()),
            "pan_forbidden_max": float(self.forbid_max.value()),
            "manual_range_m": manual_range,
            "serial_port": self.port_combo.currentText(),
            "serial_mock": self.mock_check.isChecked(),
        }

    def _emit(self, *_args) -> None:
        self.command_changed.emit(self.build_command())
