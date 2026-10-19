"""
SourceExtractionMonitoringWindow_Tesla_CNL.py

Drop-in Tesla-style replacement for the CNL Source & Extraction monitor.

Preserved CNL behavior:
    - class name: SourceExtractionMonitoringWindow
    - constructor:
        SourceExtractionMonitoringWindow(
            data_queue,
            plot_rate_hz=4,
            seconds=60,
            bg_image="source_extraction_bg.png"
        )
    - uses scaling.py / Scaler
    - accepts queue.Queue, multiprocessing.Queue, or deque-like queues
    - accepts the current CNL packet structure
    - preserves extraction/source/angle scaling
    - preserves filament impedance calculation
    - preserves Error as window range (max-min)
    - preserves 6 Extraction plots
    - preserves 5 Ion Source plots
    - Error column is WHITE

Put CNL_logo.png in the same folder as this file.
"""
from project_paths import project_file

import os
import sys
import math
import time
import random
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QFrame, QApplication, QMainWindow, QHBoxLayout,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QPainter, QPixmap, QLinearGradient, QBrush, QColor, QPen
)

import pyqtgraph as pg


# =============================================================================
# CNL scaling imports — same behavior as your existing monitor
# =============================================================================
try:
    from scaling import Scaler
    try:
        from scaling import EXTR_BASE_KEYS, EXTR_ANGLE_KEYS, SOURCE_KEYS
    except Exception:
        EXTR_BASE_KEYS = [
            "coax_a", "inside_iron_a", "outside_iron_a",
            "esd_v", "esd_a", "fringe_a"
        ]
        EXTR_ANGLE_KEYS = [
            "esd_in_deg", "esd_out_deg",
            "coax_in_deg", "coax_out_deg",
            "iron_in_deg", "iron_out_deg"
        ]
        SOURCE_KEYS = [
            "filament_a", "filament_v", "arc_v",
            "arc_a", "gas_flow", "gas_turns"
        ]
except Exception:
    # Defensive fallback. In the real CNL system scaling.py should be present.
    class Scaler:
        def __init__(self, *args, **kwargs):
            pass

        def raw_to_eng(self, key, value):
            return float(value)

    EXTR_BASE_KEYS = [
        "coax_a", "inside_iron_a", "outside_iron_a",
        "esd_v", "esd_a", "fringe_a"
    ]
    EXTR_ANGLE_KEYS = [
        "esd_in_deg", "esd_out_deg",
        "coax_in_deg", "coax_out_deg",
        "iron_in_deg", "iron_out_deg"
    ]
    SOURCE_KEYS = [
        "filament_a", "filament_v", "arc_v",
        "arc_a", "gas_flow", "gas_turns"
    ]


pg.setConfigOptions(antialias=True)


# =============================================================================
# Channels — preserved from your original SourceExtractionMonitoringWindow.py
# =============================================================================
RAW_LABELS = [
    # Extraction
    "Coax [A]",
    "Inside Iron [A]",
    "Outside Iron [A]",
    "ESD [V]",
    "ESD [A]",
    "Fringe [A]",

    # Source
    "Filament [A]",
    "Filament [V]",
    "Arc [V]",
    "Arc [A]",
    "Gas Flow",
    "Gas Turns",
]

RAW_COUNT = len(RAW_LABELS)

ANGLE_LABELS = [
    "ESD Entrance [°]",
    "ESD Exit [°]",
    "Coax Entrance [°]",
    "Coax Exit [°]",
    "Iron Entrance [°]",
    "Iron Exit [°]",
]

ANGLE_COUNT = len(ANGLE_LABELS)

IDX_FIL_A = 6
IDX_FIL_V = 7
IDX_GAS_FLOW = 10
IDX_GAS_TURNS = 11

TABLE_LABELS = RAW_LABELS + ANGLE_LABELS + ["Filament Impedance [Ω]"]
TABLE_COUNT = len(TABLE_LABELS)

# Exactly the original 11 plots:
# 6 Extraction + 4 source raw channels + filament impedance.
EXTRACTION_PLOTS = [0, 1, 2, 3, 4, 5]
SOURCE_PLOTS = [6, 7, 8, 9, "imp"]


# =============================================================================
# Tesla palette
# =============================================================================
C_BG = "#05080B"
C_SHELL = "#080D11"
C_PANEL = "#0B1116"
C_PANEL_2 = "#0E151B"
C_PLOT = "#080E13"
C_BORDER = "#25313A"
C_TEXT = "#F2F5F7"
C_MUTED = "#8D9AA5"

C_EXTR = "#43D9FF"
C_SRC = "#C079FF"

C_OK = "#57E36F"
C_BAD = "#FF6868"

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, str(project_file('CNL_logo.png')))


# =============================================================================
# Tesla-style frames / plot helpers
# =============================================================================
class TeslaShell(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TeslaShell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class AccentPanel(QFrame):
    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self.accent = QColor(accent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = self.rect().adjusted(1, 1, -1, -1)

        grad = QLinearGradient(0, 0, 0, max(1, r.height()))
        grad.setColorAt(0.0, QColor(14, 21, 27))
        grad.setColorAt(1.0, QColor(7, 12, 16))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(r, 14, 14)

        p.setBrush(Qt.BrushStyle.NoBrush)

        p.setPen(QPen(QColor(65, 78, 88, 155), 1.0))
        p.drawRoundedRect(r, 14, 14)

        ac = QColor(self.accent)
        ac.setAlpha(115)
        p.setPen(QPen(ac, 1.0))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 13, 13)

        super().paintEvent(event)


class TeslaPlotCard(QFrame):
    def __init__(self, plot_widget, parent=None):
        super().__init__(parent)
        self.setObjectName("PlotCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(7, 7, 7, 7)
        lay.setSpacing(0)
        lay.addWidget(plot_widget)


def style_plot_tesla(plot: pg.PlotWidget, title: str):
    plot.setBackground(C_PLOT)
    plot.setTitle(title, color=C_TEXT, size="10pt")

    axis_pen = pg.mkPen((90, 103, 114, 130), width=1)
    text_pen = pg.mkPen((205, 214, 220, 225))

    for name in ("left", "bottom"):
        ax = plot.getAxis(name)
        ax.setPen(axis_pen)
        ax.setTextPen(text_pen)
        ax.setStyle(tickLength=-4)

    plot.showGrid(x=True, y=True, alpha=0.12)
    plot.setClipToView(True)
    plot.setMouseEnabled(x=False, y=False)
    plot.setMenuEnabled(False)
    plot.setAutoVisible(y=True)
    plot.plotItem.setContentsMargins(5, 5, 7, 5)

    return plot


def add_tesla_series(plot: pg.PlotWidget, accent: str):
    c = QColor(accent)

    glow = plot.plot(
        pen=pg.mkPen(
            QColor(c.red(), c.green(), c.blue(), 42),
            width=6
        ),
        symbol=None
    )

    line = plot.plot(
        pen=pg.mkPen(c, width=1.8),
        symbol=None
    )

    glow.setZValue(line.zValue() - 1)

    for curve in (glow, line):
        curve.setSymbol(None)
        curve.setSymbolBrush(None)
        curve.setSymbolPen(None)
        curve.setClipToView(True)

        try:
            curve.setDownsampling(auto=True)
        except Exception:
            pass

    return glow, line


# =============================================================================
# Main CNL monitor
# =============================================================================
class SourceExtractionMonitoringWindow(QWidget):
    """
    Expects queue packets like:

      {
        "timestamp": <float>,

        "extraction": [
            coax_a,
            inside_iron_a,
            outside_iron_a,
            esd_v,
            esd_a,
            fringe_a
        ],

        "extraction_angles": [
            esd_in,
            esd_out,
            coax_in,
            coax_out,
            iron_in,
            iron_out
        ],  # optional

        "source": [
            fil_a,
            fil_v,
            arc_v,
            arc_a,
            gas_flow,
            gas_turns
        ],
      }

    Also preserves the old 7-source-vector compatibility from the previous
    CNL SourceExtractionMonitoringWindow.py.
    """

    def __init__(
        self,
        data_queue,
        plot_rate_hz=4,
        seconds=60,
        bg_image=str(project_file('source_extraction_bg.png'))
    ):
        super().__init__()

        self.setWindowTitle("CNL — Source & Extraction Monitoring")
        self.resize(1800, 1000)
        self.setMinimumSize(1180, 700)

        # Real CNL scaler.
        self.scaler = Scaler()

        # Kept in the signature for main_control_gui.py compatibility.
        # Tesla skin does not draw the old background image.
        self._bg = QPixmap(bg_image) if bg_image else QPixmap()

        self.data_queue = data_queue
        self.max_points = int(plot_rate_hz * seconds)

        self.time_buffer = deque(maxlen=self.max_points)

        self.eng_buffers = [
            deque(maxlen=self.max_points)
            for _ in range(RAW_COUNT)
        ]

        self.imp_buffer = deque(maxlen=self.max_points)

        self.latest_eng = [float("nan")] * RAW_COUNT
        self.latest_angles = [float("nan")] * ANGLE_COUNT
        self.latest_imp = float("nan")

        self._last_data_ts = 0.0

        # ---------------------------------------------------------------------
        # Global Tesla stylesheet
        # ---------------------------------------------------------------------
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                color: {C_TEXT};
                font-family: "Segoe UI", Arial, sans-serif;
            }}

            QFrame#TeslaShell {{
                background-color: {C_SHELL};
                border: 1px solid #82909A;
                border-radius: 26px;
            }}

            QFrame#PlotCard {{
                background-color: {C_PANEL_2};
                border: 1px solid {C_BORDER};
                border-radius: 11px;
            }}

            QLabel#MainTitle {{
                color: {C_TEXT};
                font-size: 23px;
                font-weight: 650;
            }}

            QLabel#SubTitle {{
                color: #AAB5BD;
                font-size: 10px;
                font-weight: 500;
            }}

            QLabel#SectionExtraction {{
                color: {C_EXTR};
                font-size: 15px;
                font-weight: 650;
            }}

            QLabel#SectionSource {{
                color: {C_SRC};
                font-size: 15px;
                font-weight: 650;
            }}

            QLabel#Status {{
                font-size: 10px;
                font-weight: 600;
            }}

            QSplitter::handle {{
                background: transparent;
                width: 8px;
            }}

            QTableWidget {{
                background-color: #071016;
                alternate-background-color: #09131A;
                color: {C_TEXT};
                border: 1px solid {C_BORDER};
                border-radius: 10px;
                gridline-color: #23303A;
                font-size: 11pt;
                selection-background-color: #132632;
            }}

            QTableWidget::item {{
                padding: 7px;
                border-bottom: 1px solid #1C2830;
            }}

            QHeaderView::section {{
                background-color: #0B1218;
                color: {C_TEXT};
                font-size: 10pt;
                font-weight: 600;
                padding: 9px;
                border: none;
                border-right: 1px solid #24313A;
                border-bottom: 1px solid #3A4953;
            }}
        """)

        # ---------------------------------------------------------------------
        # Outer window
        # ---------------------------------------------------------------------
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 13, 18, 38)
        root.setSpacing(0)

        self.shell = TeslaShell()

        # Tesla-style lower shadow.
        shell_shadow = QGraphicsDropShadowEffect(self)
        shell_shadow.setBlurRadius(42)
        shell_shadow.setOffset(0, 14)
        shell_shadow.setColor(QColor(215, 235, 245, 190))
        self.shell.setGraphicsEffect(shell_shadow)

        root.addWidget(self.shell, 1)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(18, 12, 18, 18)
        shell_layout.setSpacing(10)

        # ---------------------------------------------------------------------
        # Header
        # ---------------------------------------------------------------------
        header = QHBoxLayout()
        header.setContentsMargins(5, 0, 5, 2)
        header.setSpacing(12)

        self.logo = QLabel()
        self.logo.setFixedSize(74, 62)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        logo_pm = QPixmap(LOGO_PATH)

        if not logo_pm.isNull():
            self.logo.setPixmap(
                logo_pm.scaled(
                    66,
                    58,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )
        else:
            self.logo.setText("CNL")
            self.logo.setStyleSheet(
                "color:#DDEEFF; font-size:18px; font-weight:700;"
            )

        header.addWidget(self.logo)

        main_title = QLabel("CYCLOTRON CONTROL")
        main_title.setObjectName("MainTitle")
        header.addWidget(main_title)

        sub_title = QLabel("SOURCE & EXTRACTION MONITOR")
        sub_title.setObjectName("SubTitle")
        header.addWidget(sub_title)

        header.addStretch(1)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(
            f"color:{C_BAD}; font-size:15px;"
        )
        header.addWidget(self.status_dot)

        self.status = QLabel("NO DATA")
        self.status.setObjectName("Status")
        self.status.setStyleSheet(f"color:{C_MUTED};")
        header.addWidget(self.status)

        shell_layout.addLayout(header)

        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(
            "background:#26323A; border:none;"
        )
        shell_layout.addWidget(separator)

        # ---------------------------------------------------------------------
        # Main splitter
        # ---------------------------------------------------------------------
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)

        shell_layout.addWidget(self.main_splitter, 1)

        # ---------------------------------------------------------------------
        # LEFT: table
        # ---------------------------------------------------------------------
        left_panel = QFrame()
        left_panel.setObjectName("PlotCard")

        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(9, 9, 9, 9)

        self.table = QTableWidget(TABLE_COUNT, 3)
        self.table.setHorizontalHeaderLabels(
            ["CHANNEL", "VALUE", "ERROR"]
        )

        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)

        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection
        )
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch
        )
        hh.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch
        )
        hh.setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Stretch
        )

        for row, label in enumerate(TABLE_LABELS):
            ch = QTableWidgetItem(label)
            ch.setForeground(QColor(C_TEXT))

            val = QTableWidgetItem("—")
            val.setForeground(QColor(C_TEXT))
            val.setTextAlignment(
                Qt.AlignmentFlag.AlignRight |
                Qt.AlignmentFlag.AlignVCenter
            )

            err = QTableWidgetItem("—")
            err.setForeground(QColor(C_TEXT))  # always white
            err.setTextAlignment(
                Qt.AlignmentFlag.AlignRight |
                Qt.AlignmentFlag.AlignVCenter
            )

            self.table.setItem(row, 0, ch)
            self.table.setItem(row, 1, val)
            self.table.setItem(row, 2, err)

        left_layout.addWidget(self.table)

        self.main_splitter.addWidget(left_panel)

        # ---------------------------------------------------------------------
        # RIGHT: graphs
        # ---------------------------------------------------------------------
        right_panel = QWidget()

        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(9)

        # ============================ Extraction ==============================
        extraction_panel = AccentPanel(C_EXTR)

        extraction_layout = QVBoxLayout(extraction_panel)
        extraction_layout.setContentsMargins(12, 8, 12, 12)
        extraction_layout.setSpacing(6)

        ext_header = QHBoxLayout()
        ext_header.setContentsMargins(5, 0, 5, 0)

        ext_title = QLabel("EXTRACTION")
        ext_title.setObjectName("SectionExtraction")
        ext_header.addWidget(ext_title)

        ext_history = QLabel(f"({int(seconds)} s)")
        ext_history.setStyleSheet(
            f"color:{C_MUTED}; font-size:11px;"
        )
        ext_header.addWidget(ext_history)

        ext_header.addStretch(1)

        extraction_layout.addLayout(ext_header)

        ext_grid = QGridLayout()
        ext_grid.setContentsMargins(0, 0, 0, 0)
        ext_grid.setHorizontalSpacing(8)
        ext_grid.setVerticalSpacing(8)

        self.top_plot_widgets = []
        self.top_curves = []
        self.top_shadow_curves = []

        for pos, key in enumerate(EXTRACTION_PLOTS):
            pw = pg.PlotWidget()
            pw.setMinimumHeight(130)

            style_plot_tesla(
                pw,
                RAW_LABELS[key]
            )

            shadow_curve, main_curve = add_tesla_series(
                pw,
                C_EXTR
            )

            card = TeslaPlotCard(pw)

            self.top_plot_widgets.append(pw)
            self.top_shadow_curves.append(shadow_curve)
            self.top_curves.append(main_curve)

            row, col = divmod(pos, 3)
            ext_grid.addWidget(card, row, col)

        extraction_layout.addLayout(ext_grid)

        right_layout.addWidget(extraction_panel, 1)

        # ============================== Source ================================
        source_panel = AccentPanel(C_SRC)

        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(12, 8, 12, 12)
        source_layout.setSpacing(6)

        source_header = QHBoxLayout()
        source_header.setContentsMargins(5, 0, 5, 0)

        source_title = QLabel("ION SOURCE")
        source_title.setObjectName("SectionSource")
        source_header.addWidget(source_title)

        source_history = QLabel(f"({int(seconds)} s)")
        source_history.setStyleSheet(
            f"color:{C_MUTED}; font-size:11px;"
        )
        source_header.addWidget(source_history)

        source_header.addStretch(1)

        source_layout.addLayout(source_header)

        source_grid = QGridLayout()
        source_grid.setContentsMargins(0, 0, 0, 0)
        source_grid.setHorizontalSpacing(8)
        source_grid.setVerticalSpacing(8)

        self.bottom_plot_widgets = []
        self.bottom_curves = []
        self.bottom_shadow_curves = []

        for pos, key in enumerate(SOURCE_PLOTS):
            if key == "imp":
                title = "Filament Impedance [Ω]"
            else:
                title = RAW_LABELS[key]

            pw = pg.PlotWidget()
            pw.setMinimumHeight(130)

            style_plot_tesla(
                pw,
                title
            )

            shadow_curve, main_curve = add_tesla_series(
                pw,
                C_SRC
            )

            card = TeslaPlotCard(pw)

            self.bottom_plot_widgets.append(pw)
            self.bottom_shadow_curves.append(shadow_curve)
            self.bottom_curves.append(main_curve)

            row, col = divmod(pos, 3)
            source_grid.addWidget(card, row, col)

        # Keep sixth source cell empty.
        empty_cell = QWidget()
        empty_cell.setStyleSheet("background:transparent;")
        source_grid.addWidget(empty_cell, 1, 2)

        source_layout.addLayout(source_grid)

        right_layout.addWidget(source_panel, 1)

        self.main_splitter.addWidget(right_panel)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([515, 1260])

        self.plot_widgets = (
            self.top_plot_widgets +
            self.bottom_plot_widgets
        )

        self.curves = (
            self.top_curves +
            self.bottom_curves
        )

        self.shadow_curves = (
            self.top_shadow_curves +
            self.bottom_shadow_curves
        )

        # ---------------------------------------------------------------------
        # GUI refresh
        # ---------------------------------------------------------------------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(int(1000 / plot_rate_hz))

    # =========================================================================
    # Existing external API
    # =========================================================================
    def set_split_ratio(self, left_px: int, right_px: int):
        self.main_splitter.setSizes(
            [
                max(100, left_px),
                max(200, right_px)
            ]
        )

    # =========================================================================
    # Tesla background / bottom glow
    # =========================================================================
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True
        )

        p.fillRect(
            self.rect(),
            QColor(C_BG)
        )

        # Broad luminous bottom shadow inspired by the reference GUI.
        h = self.height()
        w = self.width()

        glow = QLinearGradient(
            0,
            h - 80,
            0,
            h
        )

        glow.setColorAt(
            0.00,
            QColor(255, 255, 255, 0)
        )
        glow.setColorAt(
            0.52,
            QColor(210, 230, 240, 26)
        )
        glow.setColorAt(
            0.72,
            QColor(230, 242, 248, 82)
        )
        glow.setColorAt(
            0.86,
            QColor(175, 205, 220, 42)
        )
        glow.setColorAt(
            1.00,
            QColor(0, 0, 0, 0)
        )

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))

        p.drawRoundedRect(
            55,
            h - 72,
            max(20, w - 110),
            55,
            28,
            28
        )

        super().paintEvent(event)

    # =========================================================================
    # Formatting / parsing helpers
    # =========================================================================
    @staticmethod
    def _fmt(v):
        if v is None:
            return "—"

        try:
            fv = float(v)

            if math.isnan(fv) or math.isinf(fv):
                return "—"

            return f"{fv:.3f}"

        except Exception:
            return "—"

    def _vector12(self, pkt: dict):
        """
        Return:
            extraction[6] + source[6]

        Preserves old 7-source-vector compatibility from your current file.
        """

        ext = pkt.get("extraction")
        src = pkt.get("source")

        if (
            isinstance(ext, (list, tuple)) and
            len(ext) >= 6 and
            isinstance(src, (list, tuple)) and
            len(src) >= 6
        ):
            return list(ext[:6]) + list(src[:6])

        # Back compatibility:
        # ["Arc Voltage","Arc Current","Filament",
        #  "ESD kV","ESD mA","Outside Iron","Inside Iron"]
        if isinstance(src, (list, tuple)) and len(src) == 7:
            old = list(src)

            coax_a = None
            inside_a = old[6]
            outside_a = old[5]
            esd_v = old[3]
            esd_a = old[4]
            fringe_a = None

            fil_a = old[2]
            fil_v = None
            arc_v = old[0]
            arc_a = old[1]
            gas_flow = None
            gas_turns = None

            return [
                coax_a,
                inside_a,
                outside_a,
                esd_v,
                esd_a,
                fringe_a,

                fil_a,
                fil_v,
                arc_v,
                arc_a,
                gas_flow,
                gas_turns
            ]

        return [None] * RAW_COUNT

    def _angles6(self, pkt: dict):
        ang = pkt.get("extraction_angles")

        if (
            isinstance(ang, (list, tuple)) and
            len(ang) >= ANGLE_COUNT
        ):
            return list(ang[:ANGLE_COUNT])

        return [None] * ANGLE_COUNT

    def _scale_or_nan(self, key, value):
        try:
            if value is None:
                return float("nan")

            fv = float(value)

            if math.isnan(fv) or math.isinf(fv):
                return float("nan")

            return self.scaler.raw_to_eng(
                key,
                fv
            )

        except Exception:
            return float("nan")

    def _drain_latest(self):
        """
        Same queue compatibility as the original monitor:
        - deque
        - queue.Queue
        - multiprocessing.Queue
        """

        q = self.data_queue
        latest = None

        if hasattr(q, "popleft"):
            while q:
                latest = q.popleft()

            return latest

        get_nowait = getattr(
            q,
            "get_nowait",
            None
        )

        if callable(get_nowait):
            while True:
                try:
                    latest = get_nowait()
                except Exception:
                    break

            return latest

        get_nb = getattr(
            q,
            "get",
            None
        )

        if callable(get_nb):
            while True:
                try:
                    latest = get_nb(False)
                except Exception:
                    break

            return latest

        return None

    @staticmethod
    def _finite_range(buffer):
        values = []

        for value in buffer:
            try:
                fv = float(value)

                if math.isfinite(fv):
                    values.append(fv)

            except Exception:
                pass

        if not values:
            return None

        return max(values) - min(values)

    def _set_status(self, ok: bool):
        if ok:
            self.status_dot.setStyleSheet(
                f"color:{C_OK}; font-size:15px;"
            )

            self.status.setText(
                "RECEIVING DATA"
            )

            self.status.setStyleSheet(
                f"color:{C_OK};"
            )

        else:
            self.status_dot.setStyleSheet(
                f"color:{C_BAD}; font-size:15px;"
            )

            self.status.setText(
                "NO DATA"
            )

            self.status.setStyleSheet(
                f"color:{C_MUTED};"
            )

    # =========================================================================
    # Main update
    # =========================================================================
    def update_gui(self):
        now = time.time()

        last = self._drain_latest()

        if last is None:
            self._set_status(
                (time.time() - self._last_data_ts) < 1.0
            )
            return

        if not isinstance(last, dict):
            return

        vec_raw = self._vector12(last)
        ang_raw = self._angles6(last)

        if (
            not any(v is not None for v in vec_raw) and
            not any(v is not None for v in ang_raw)
        ):
            return

        self._last_data_ts = now
        self.time_buffer.append(now)

        # ---------------------------------------------------------------------
        # Scale extraction + source into engineering units
        # ---------------------------------------------------------------------
        scaled_vals = []

        for i, key in enumerate(EXTR_BASE_KEYS):
            scaled_vals.append(
                self._scale_or_nan(
                    key,
                    vec_raw[i]
                )
            )

        for j, key in enumerate(SOURCE_KEYS):
            scaled_vals.append(
                self._scale_or_nan(
                    key,
                    vec_raw[6 + j]
                )
            )

        # ---------------------------------------------------------------------
        # Main channels + Error = max-min across current history
        # ---------------------------------------------------------------------
        for i in range(RAW_COUNT):
            value = scaled_vals[i]

            self.latest_eng[i] = value
            self.eng_buffers[i].append(value)

            self.table.item(
                i,
                1
            ).setText(
                self._fmt(value)
            )

            error = self._finite_range(
                self.eng_buffers[i]
            )

            self.table.item(
                i,
                2
            ).setText(
                self._fmt(error)
            )

            # User request: ERROR ALWAYS WHITE.
            self.table.item(
                i,
                2
            ).setForeground(
                QColor(C_TEXT)
            )

        # ---------------------------------------------------------------------
        # Extraction angles — table only
        # ---------------------------------------------------------------------
        for j, key in enumerate(EXTR_ANGLE_KEYS):
            value = self._scale_or_nan(
                key,
                ang_raw[j]
            )

            self.latest_angles[j] = value

            row = RAW_COUNT + j

            self.table.item(
                row,
                1
            ).setText(
                self._fmt(value)
            )

            self.table.item(
                row,
                2
            ).setText("—")

            self.table.item(
                row,
                2
            ).setForeground(
                QColor(C_TEXT)
            )

        # ---------------------------------------------------------------------
        # Filament impedance
        # ---------------------------------------------------------------------
        fil_a = self.latest_eng[
            IDX_FIL_A
        ]

        fil_v = self.latest_eng[
            IDX_FIL_V
        ]

        try:
            fa = float(fil_a)
            fv = float(fil_v)

            if (
                not math.isfinite(fa) or
                not math.isfinite(fv) or
                abs(fa) < 1e-9
            ):
                impedance = float("nan")

            else:
                impedance = fv / fa

        except Exception:
            impedance = float("nan")

        self.latest_imp = impedance
        self.imp_buffer.append(impedance)

        imp_row = TABLE_COUNT - 1

        self.table.item(
            imp_row,
            1
        ).setText(
            self._fmt(impedance)
        )

        imp_error = self._finite_range(
            self.imp_buffer
        )

        self.table.item(
            imp_row,
            2
        ).setText(
            self._fmt(imp_error)
        )

        self.table.item(
            imp_row,
            2
        ).setForeground(
            QColor(C_TEXT)
        )

        # ---------------------------------------------------------------------
        # Plot history — newest sample at 0 s
        # ---------------------------------------------------------------------
        if len(self.time_buffer) >= 2:
            newest = self.time_buffer[-1]

            xs = [
                t - newest
                for t in self.time_buffer
            ]

            # 6 Extraction plots.
            for i, key in enumerate(EXTRACTION_PLOTS):
                data = list(
                    self.eng_buffers[key]
                )

                self.top_shadow_curves[
                    i
                ].setData(
                    xs,
                    data
                )

                self.top_curves[
                    i
                ].setData(
                    xs,
                    data
                )

            # 5 Ion Source plots.
            for i, key in enumerate(SOURCE_PLOTS):
                if key == "imp":
                    data = list(
                        self.imp_buffer
                    )
                else:
                    data = list(
                        self.eng_buffers[key]
                    )

                self.bottom_shadow_curves[
                    i
                ].setData(
                    xs,
                    data
                )

                self.bottom_curves[
                    i
                ].setData(
                    xs,
                    data
                )

            history = max(
                1.0,
                self.max_points /
                max(
                    1.0,
                    self.timer.interval() and
                    (1000.0 / self.timer.interval())
                )
            )

            # Use constructor seconds visually.
            x_min = -float(
                max(
                    1,
                    int(
                        self.max_points *
                        self.timer.interval() /
                        1000
                    )
                )
            )

            for pw in self.plot_widgets:
                pw.setXRange(
                    x_min,
                    0,
                    padding=0.01
                )

        self._set_status(True)


# =============================================================================
# Optional standalone test.
# This block does NOT affect the CNL control system import.
# =============================================================================
class _DemoMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "CNL Source & Extraction Tesla Test"
        )

        self.data_queue = deque()

        self.monitor = SourceExtractionMonitoringWindow(
            self.data_queue,
            plot_rate_hz=4,
            seconds=60
        )

        self.setCentralWidget(
            self.monitor
        )

        self.sim_time = 0.0

        self.sim_timer = QTimer(self)
        self.sim_timer.timeout.connect(
            self.generate_simulated_data
        )
        self.sim_timer.start(250)

    def generate_simulated_data(self):
        self.sim_time += 0.25
        t = self.sim_time

        def noise(scale):
            return random.uniform(
                -scale,
                scale
            )

        extraction_data = [
            1.45 + 0.06 * math.sin(t * 0.40) + noise(0.012),
            7.42 + 0.16 * math.sin(t * 0.23 + 0.6) + noise(0.035),
            6.91 + 0.15 * math.sin(t * 0.20 + 1.1) + noise(0.035),
            14.80 + 0.20 * math.sin(t * 0.14) + noise(0.040),
            2.14 + 0.05 * math.sin(t * 0.30 + 0.8) + noise(0.012),
            0.36 + 0.025 * math.sin(t * 0.18 + 1.5) + noise(0.006),
        ]

        source_data = [
            18.70 + 0.24 * math.sin(t * 0.17) + noise(0.045),
            5.62 + 0.09 * math.sin(t * 0.21 + 0.3) + noise(0.020),
            92.40 + 1.0 * math.sin(t * 0.13 + 0.5) + noise(0.20),
            1.82 + 0.045 * math.sin(t * 0.27 + 1.0) + noise(0.010),
            12.40 + 0.16 * math.sin(t * 0.11) + noise(0.025),
            1260.0 + noise(2.0),
        ]

        angles_data = [
            21.3 + noise(0.03),
            20.1 + noise(0.03),
            19.8 + noise(0.03),
            18.9 + noise(0.03),
            17.6 + noise(0.03),
            16.4 + noise(0.03),
        ]

        self.data_queue.append({
            "timestamp": time.time(),
            "extraction": extraction_data,
            "source": source_data,
            "extraction_angles": angles_data
        })


if __name__ == "__main__":
    app = QApplication(sys.argv)

    app.setStyleSheet(
        f"QMainWindow {{ background-color: {C_BG}; }}"
    )

    window = _DemoMainWindow()
    window.resize(1600, 930)
    window.show()

    sys.exit(app.exec())