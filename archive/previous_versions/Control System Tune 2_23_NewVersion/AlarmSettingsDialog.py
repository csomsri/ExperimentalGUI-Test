# AlarmSettingsDialog.py — RF/Vac alarm settings dialog (styled for dark UI)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QCheckBox,
    QLineEdit, QPushButton, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt
from alarm import AlarmConfig, alarm_manager

class AlarmSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alarm Settings")
        self.resize(520, 380)

        # ---- dark theme for readability over your background ----
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14,16,24, 235);
            }
            QLabel {
                color: #EAF6FF;
                font-size: 12px;
            }
            QGroupBox {
                color: #AEE1FF;
                font-weight: bold;
                border: 1px solid #1C5F82;
                border-radius: 6px;
                margin-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 8px; top: -2px;
                padding: 0 6px;
                background: transparent;
            }
            QLineEdit, QDoubleSpinBox {
                background: #0E1421;
                color: #EAF6FF;
                border: 1px solid #2D6B93;
                border-radius: 4px;
                padding: 4px 6px;
                selection-background-color: #2D6B93;
            }
            QCheckBox {
                color: #EAF6FF;
                spacing: 8px;
            }
            QPushButton {
                background-color: #1F74C1;
                color: white;
                border: 1px solid #2C88DA;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #2896F0; }
            QPushButton:pressed { background-color: #1A5FA1; }
        """)

        cfg = alarm_manager.cfg

        root = QVBoxLayout(self)

        # ---- RF settings ----
        gb_rf = QGroupBox("RF alarm")
        gl_rf = QGridLayout(gb_rf)

        self.rf_dkv = QDoubleSpinBox(); self.rf_dkv.setRange(0, 1e6); self.rf_dkv.setDecimals(2); self.rf_dkv.setValue(cfg.rf_dkv)
        self.rf_win = QDoubleSpinBox(); self.rf_win.setRange(0.1, 60); self.rf_win.setDecimals(2); self.rf_win.setValue(cfg.rf_window_s)
        self.rf_bg  = QLineEdit(cfg.rf_bg); self.rf_bg.setPlaceholderText("rf_alarm_bg.png")

        gl_rf.addWidget(QLabel("ΔRF (kV) ≥"), 0, 0, Qt.AlignmentFlag.AlignRight)
        gl_rf.addWidget(self.rf_dkv,           0, 1)
        gl_rf.addWidget(QLabel("within (s)"),  0, 2, Qt.AlignmentFlag.AlignRight)
        gl_rf.addWidget(self.rf_win,           0, 3)
        gl_rf.addWidget(QLabel("RF overlay image"), 1, 0, Qt.AlignmentFlag.AlignRight)
        gl_rf.addWidget(self.rf_bg,            1, 1, 1, 3)

        root.addWidget(gb_rf)

        # ---- Vacuum settings ----
        gb_v = QGroupBox("Vacuum alarm (rise by factor)")
        gl_v = QGridLayout(gb_v)

        self.vac_fac = QDoubleSpinBox(); self.vac_fac.setRange(1.0, 1e6); self.vac_fac.setDecimals(2); self.vac_fac.setValue(cfg.vac_factor)
        self.vac_win = QDoubleSpinBox(); self.vac_win.setRange(0.1, 120); self.vac_win.setDecimals(2); self.vac_win.setValue(cfg.vac_window_s)
        self.vac_bg  = QLineEdit(cfg.vac_bg); self.vac_bg.setPlaceholderText("vac_alarm_bg.png")

        gl_v.addWidget(QLabel("Factor ≥"),   0, 0, Qt.AlignmentFlag.AlignRight)
        gl_v.addWidget(self.vac_fac,         0, 1)
        gl_v.addWidget(QLabel("within (s)"), 0, 2, Qt.AlignmentFlag.AlignRight)
        gl_v.addWidget(self.vac_win,         0, 3)
        gl_v.addWidget(QLabel("Vac overlay image"), 1, 0, Qt.AlignmentFlag.AlignRight)
        gl_v.addWidget(self.vac_bg,          1, 1, 1, 3)

        # Which vac channels
        self.chk_v1 = QCheckBox("Vac1"); self.chk_v2 = QCheckBox("Vac2")
        self.chk_v3 = QCheckBox("Vac3"); self.chk_v4 = QCheckBox("Vac4"); self.chk_v5 = QCheckBox("Vac5")
        for i, k in enumerate(["vac1","vac2","vac3","vac4","vac5"]):
            if k in cfg.vac_channels: [self.chk_v1,self.chk_v2,self.chk_v3,self.chk_v4,self.chk_v5][i].setChecked(True)

        gl_v.addWidget(QLabel("Watch:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        gl_v.addWidget(self.chk_v1,      2, 1); gl_v.addWidget(self.chk_v2, 2, 2); gl_v.addWidget(self.chk_v3, 2, 3)
        gl_v.addWidget(self.chk_v4,      3, 1); gl_v.addWidget(self.chk_v5, 3, 2)

        root.addWidget(gb_v)

        # ---- Buttons ----
        btns = QHBoxLayout()
        btns.addStretch(1)
        save = QPushButton("Save");   save.clicked.connect(self.on_save)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        btns.addWidget(save); btns.addWidget(cancel)
        root.addLayout(btns)

    def on_save(self):
        vac_list = []
        if self.chk_v1.isChecked(): vac_list.append("vac1")
        if self.chk_v2.isChecked(): vac_list.append("vac2")
        if self.chk_v3.isChecked(): vac_list.append("vac3")
        if self.chk_v4.isChecked(): vac_list.append("vac4")
        if self.chk_v5.isChecked(): vac_list.append("vac5")
        if not vac_list:
            vac_list = ["vac1","vac2"]  # default to TP1/TP2

        cfg = AlarmConfig(
            rf_dkv=float(self.rf_dkv.value()),
            rf_window_s=float(self.rf_win.value()),
            vac_factor=float(self.vac_fac.value()),
            vac_window_s=float(self.vac_win.value()),
            vac_channels=vac_list,
            rf_bg=self.rf_bg.text().strip(),
            vac_bg=self.vac_bg.text().strip(),
        )
        alarm_manager.apply_config(cfg)
        self.accept()
