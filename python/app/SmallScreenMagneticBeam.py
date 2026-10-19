from project_paths import project_file
# -*- coding: utf-8 -*-
# SmallScreenMagneticBeam.py - 7" portrait, two tabs with backgrounds (container + diagnostics)
# Digital taps forward to control_queue -> REP server -> LabVIEW.

import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from beam_range import BeamRangePad
from MagneticFieldControllerWindow import MagneticFieldControllerWindow as MagneticOriginal

MAG_BG  = str(project_file('magnetic_bg.png'))
BEAM_BG = str(project_file('beam_range_bg.png'))


def _border_image_stylesheet(object_name: str, path: str | None) -> str:
    if path and os.path.exists(path):
        qurl = path.replace("\\", "/")
        return f"#{object_name} {{ border-image: url('{qurl}') 0 0 0 0 stretch stretch; }}"
    return f"#{object_name} {{ background: #0d1321; }}"


class SmallScreenMagneticBeamWindow(QWidget):
    """
    Portrait wrapper (600x1024).
    Tab 1: Magnetic controller
    Tab 2: Beam Meter (BeamRangePad)

    Provides:
      - request_beam_range_change(idx, bitmask_u64): follow HW in Analog mode
      - Digital taps -> control_queue: send beam_range_idx/bitmask to LabVIEW
    """
    def __init__(self, control_queue, target_values, plot_config, mag_data_queue=None, **kwargs):
        super().__init__()
        self.control_queue = control_queue
        self.target_values = target_values
        self.plot_config   = plot_config
        self.mag_data_queue = mag_data_queue  # NEW: dedicated queue carrying LabVIEW packets (dict)

        self.setWindowTitle("Cyclotron Control - Magnetic & Beam (Small Screen)")
        self.resize(600, 1024)

        tabs = QTabWidget(self)
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(
            "QTabBar::tab{background:#0f2230;color:#bfefff;padding:6px 14px;"
            "border:1px solid #66d6ff;border-radius:10px;margin:4px}"
            "QTabBar::tab:selected{background:#114b5f;color:#e6ffff}"
            "QTabWidget::pane{border:0;background:transparent}"
        )

        # ---------------- Tab 1 - Magnetic ----------------
        mag_page = QWidget()
        mag_page.setObjectName("MagBgPage")
        mag_page.setStyleSheet(_border_image_stylesheet("MagBgPage", MAG_BG))

        # IMPORTANT: pass mag_data_queue to controller so it can plot ACTUAL values (scaled)
        mag = MagneticOriginal(
            self.control_queue,
            self.target_values,
            self.plot_config,
            data_queue=self.mag_data_queue
        )

        try:
            mag.setAutoFillBackground(False)
        except Exception:
            pass

        lay1 = QVBoxLayout(mag_page)
        lay1.setContentsMargins(0, 0, 0, 0)
        lay1.addWidget(mag)
        tabs.addTab(mag_page, "Magnetic")

        # ---------------- Tab 2 - Beam Meter ----------------
        beam_page = QWidget()
        beam_page.setObjectName("BeamBgPage")
        beam_page.setStyleSheet(_border_image_stylesheet("BeamBgPage", BEAM_BG))

        self.beam_pad = BeamRangePad(parent=beam_page, columns=2)

        try:
            self.beam_pad.setAutoFillBackground(False)
        except Exception:
            pass

        # forward Digital taps to server so LabVIEW receives the 4-bit address
        self.beam_pad.rangeSelected.connect(self._on_range_selected)

        lay2 = QVBoxLayout(beam_page)
        lay2.setContentsMargins(0, 0, 0, 0)
        lay2.addWidget(self.beam_pad)
        tabs.addTab(beam_page, "Beam Meter")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(tabs)
        self.setLayout(root)

    def request_beam_range_change(self, idx: int, bitmask_u64: int):
        """Update the BeamRangePad highlight in Analog-follow style."""
        try:
            self.beam_pad.set_base_bitmask(int(bitmask_u64))
            self.beam_pad.follow_from_u64(int(bitmask_u64), explicit_idx=int(idx))
        except Exception:
            pass

    def _on_range_selected(self, idx: int, bitmask_u64: int):
        """
        Digital tap: forward selection to REP server so reply bitmask includes the 4-bit address.
        """
        try:
            if self.control_queue is not None:
                self.control_queue.put(
                    {"cmd": "set_beam_range", "beam_range_idx": int(idx), "bitmask_u64": int(bitmask_u64)},
                    block=False
                )
            print(f"[SmallScreen] Digital tap -> idx={idx}, bm=0x{int(bitmask_u64):016X}")
        except Exception as e:
            print("SmallScreen forward error:", e)
