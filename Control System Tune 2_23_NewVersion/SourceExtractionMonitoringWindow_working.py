# SourceExtractionMonitoringWindow.py
import math, time, sys, random
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QFrame, QApplication, QMainWindow, QHBoxLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QLinearGradient, QBrush, QColor

import pyqtgraph as pg

# ---------------- Scaling (with robust fallbacks) ----------------
# We try to import Scaler and canonical key arrays; if the arrays aren't
# exported in your scaling.py, we define them here to match RAW_LABELS.
try:
    from scaling import Scaler
    try:
        # Optional canonical key names – use if your scaling.py exports them
        from scaling import EXTR_BASE_KEYS, EXTR_ANGLE_KEYS, SOURCE_KEYS
    except Exception:
        EXTR_BASE_KEYS = ["coax_a", "inside_iron_a", "outside_iron_a", "esd_v", "esd_a", "fringe_a"]
        EXTR_ANGLE_KEYS = ["esd_in_deg", "esd_out_deg", "coax_in_deg", "coax_out_deg", "iron_in_deg", "iron_out_deg"]
        SOURCE_KEYS = ["filament_a", "filament_v", "arc_v", "arc_a", "gas_flow", "gas_turns"]
except Exception:
    # Very defensive fallback so the module still runs without scaling.py
    class Scaler:
        def __init__(self, *args, **kwargs): pass
        def raw_to_eng(self, key, value): return float(value)
    EXTR_BASE_KEYS = ["coax_a", "inside_iron_a", "outside_iron_a", "esd_v", "esd_a", "fringe_a"]
    EXTR_ANGLE_KEYS = ["esd_in_deg", "esd_out_deg", "coax_in_deg", "coax_out_deg", "iron_in_deg", "iron_out_deg"]
    SOURCE_KEYS = ["filament_a", "filament_v", "arc_v", "arc_a", "gas_flow", "gas_turns"]

# smooth lines everywhere
pg.setConfigOptions(antialias=True)

# 12 channels (6 extraction + 6 source) in this order (labels only):
RAW_LABELS = [
    # Extraction (6)
    "Coax [A]", "Inside Iron [A]", "Outside Iron [A]", "ESD [V]", "ESD [A]", "Fringe [A]",
    # Source (6)
    "Filament [A]", "Filament [V]", "Arc [V]", "Arc [A]", "Gas Flow", "Gas Turns"
]
RAW_COUNT = len(RAW_LABELS)

# Angle labels (6) — table-only
ANGLE_LABELS = [
    "ESD Entrance [°]", "ESD Exit [°]",
    "Coax Entrance [°]", "Coax Exit [°]",
    "Iron Entrance [°]", "Iron Exit [°]"
]
ANGLE_COUNT = len(ANGLE_LABELS)

# Indices for convenience (map to RAW_LABELS order)
IDX_FIL_A = 6
IDX_FIL_V = 7
IDX_GAS_FLOW  = 10
IDX_GAS_TURNS = 11

# Table shows all raw + angles + computed impedance + errors
TABLE_LABELS = RAW_LABELS + ANGLE_LABELS + ["Filament Impedance [Ω]"]
TABLE_COUNT  = len(TABLE_LABELS)

# Plots: all raw EXCEPT Gas Flow & Gas Turns, plus impedance (angles not plotted)
PLOT_SERIES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, "imp"]

# --------- cyberpunk styling helpers ---------
HSV_COLORS = [
    "#FF0000", "#FF7F00", "#FFFF00", "#00FF00",
    "#00FFFF", "#0000FF", "#8B00FF", "#FF00FF", 
    "#FF0040", "#FF8000"
]

def neon_color(i: int) -> QColor:
    return QColor(HSV_COLORS[i % len(HSV_COLORS)])

def style_plot_cyberpunk(plot: pg.PlotWidget, title: str):
    # deep navy → purple gradient background
    g = QLinearGradient(0, 0, 0, 1)
    g.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
    g.setColorAt(0.0, QColor(8, 12, 26))
    g.setColorAt(1.0, QColor(22, 10, 35))
    plot.setBackground(QBrush(g))
    plot.setTitle(title, **{'color': '#BFEFFF', 'size': '10pt'})

    grid_c = (160, 220, 255, 110)
    ax_pen  = pg.mkPen(color=grid_c, width=1)
    txt_pen = pg.mkPen(color=(200, 240, 255, 230))
    for axname in ('left', 'bottom'):
        ax = plot.getAxis(axname)
        ax.setPen(ax_pen)
        ax.setTextPen(txt_pen)
        ax.setStyle(tickLength=-6, stopAxisAtTick=(True, True))
    plot.showGrid(x=True, y=True, alpha=0.28)
    plot.setClipToView(True)
    plot.setMouseEnabled(x=False, y=False)
    plot.setMenuEnabled(False)
    plot.setAutoVisible(y=True)
    plot.plotItem.setContentsMargins(6, 6, 10, 6)
    return plot

def add_cyberpunk_series(plot: pg.PlotWidget, color: QColor, name: str):
    """Clean neon line with soft glow and under-fill to y=0."""
    glow = plot.plot(pen=pg.mkPen(color.lighter(170), width=6))
    glow.setSymbol(None); glow.setSymbolBrush(None); glow.setSymbolPen(None)

    line = plot.plot(
        pen=pg.mkPen(color, width=2),
        name=name,
        symbol=None,
        symbolBrush=None,
        symbolPen=None
    )
    line.setSymbol(None); line.setSymbolBrush(None); line.setSymbolPen(None)

    fill_grad = QLinearGradient(0, 0, 0, 1)
    fill_grad.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
    fill_grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 80))
    fill_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    line.setFillLevel(0.0)
    line.setBrush(QBrush(fill_grad))

    glow.setZValue(line.zValue() - 1)
    try:
        line.setDownsampling(auto=True)
    except Exception:
        pass
    line.setClipToView(True)
    return glow, line

class NeonFrame(QFrame):
    """Custom frame with neon border effect"""
    def __init__(self, color="#00FFFF", parent=None):
        super().__init__(parent)
        self.neon_color = color
        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(3)
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = pg.mkPen(self.neon_color, width=4)
        painter.setPen(pen)
        painter.drawRect(2, 2, self.width()-4, self.height()-4)
        pen = pg.mkPen(QColor(self.neon_color).lighter(150), width=2)
        painter.setPen(pen)
        painter.drawRect(4, 4, self.width()-8, self.height()-8)

class SourceExtractionMonitoringWindow(QWidget):
    """
    Expects queue items like:
      {
        "timestamp": <float>,
        "extraction": [coax_a, inside_iron_a, outside_iron_a, esd_v, esd_a, fringe_a],
        "extraction_angles": [esd_in, esd_out, coax_in, coax_out, iron_in, iron_out],   # optional
        "source":     [fil_a, fil_v, arc_v, arc_a, gas_flow, gas_turns],
      }
    """
    def __init__(self, data_queue, plot_rate_hz=4, seconds=60, bg_image="source_extraction_bg.png"):
        super().__init__()
        self.setWindowTitle("Proton Source & Extraction Monitoring")
        self.resize(1800, 1000)
        self.setStyleSheet("background-color: #000000;")

        # Scaler (auto-watches your calibration file)
        self.scaler = Scaler()

        # Background image (optional)
        self._bg = QPixmap(bg_image)
        if self._bg.isNull():
            print(f"⚠️ Background '{bg_image}' not found.")

        self.data_queue = data_queue
        self.max_points = int(plot_rate_hz * seconds)

        self.time_buffer = deque(maxlen=self.max_points)

        # Buffers store SCALED (engineering units) values
        self.eng_buffers = [deque(maxlen=self.max_points) for _ in range(RAW_COUNT)]
        self.imp_buffer  = deque(maxlen=self.max_points)
        self.latest_eng  = [float("nan")] * RAW_COUNT
        self.latest_angles = [float("nan")] * ANGLE_COUNT
        self.latest_imp  = float("nan")
        self._last_data_ts = 0.0

        # ---------- Main Layout ----------
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---------- Header ----------
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addStretch()
        self.status = QLabel("🔴 No Data")
        self.status.setStyleSheet("color: red; font-weight: bold; font-size: 10pt;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.status)
        root.addWidget(header_widget)

        # ---------- Splitter ----------
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.main_splitter, 1)

        # ---------- LEFT: Table ----------
        left_panel = QWidget()
        left_panel.setStyleSheet("background-color: #111111; border-radius: 8px;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)

        self.table = QTableWidget(TABLE_COUNT, 3)
        self.table.setHorizontalHeaderLabels(["Channel", "Value", "Error"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                color: #0ff; 
                font-size: 14pt;
                background-color: #000000;
                gridline-color: #333333;
                border: 2px solid #00FFFF;
            }
            QTableWidget::item {
                border-bottom: 1px solid #333333;
                padding: 12px;
            }
            QHeaderView::section {
                background-color: #222222;
                color: #00FFFF;
                font-weight: bold;
                padding: 12px;
                font-size: 14pt;
                border: 1px solid #333333;
            }
        """)
        for i, name in enumerate(TABLE_LABELS):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            itm = QTableWidgetItem("—"); itm.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 1, itm)
            err_itm = QTableWidgetItem("—"); err_itm.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 2, err_itm)
        left_layout.addWidget(self.table)
        self.main_splitter.addWidget(left_panel)

        # ---------- RIGHT: Graphs ----------
        right_panel = QWidget()
        right_panel.setStyleSheet("background-color: #111111; border-radius: 8px;")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(15)

        # --- Top: Extraction (6) ---
        top_graphs_container = NeonFrame("#00FFFF")
        top_graphs_container.setStyleSheet("background-color: #0a0a0a;")
        top_graphs_layout = QVBoxLayout(top_graphs_container)
        top_graphs_layout.setContentsMargins(15, 15, 15, 15)

        top_graphs_title = QLabel("PROTON EXTRACTION")
        top_graphs_title.setStyleSheet("color: #00FFFF; font-weight: bold; font-size: 18pt; text-align: center;")
        top_graphs_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_graphs_layout.addWidget(top_graphs_title)

        top_plots_grid = QGridLayout()
        top_plots_grid.setSpacing(10)
        self.top_plot_widgets, self.top_curves, self.top_shadow_curves = [], [], []
        for i in range(6):
            name = RAW_LABELS[i]
            pw = pg.PlotWidget()
            style_plot_cyberpunk(pw, name)
            color = neon_color(i)
            shadow_curve, main_curve = add_cyberpunk_series(pw, color, name)
            for c in (main_curve, shadow_curve):
                c.setSymbol(None); c.setSymbolBrush(None); c.setSymbolPen(None)
            self.top_plot_widgets.append(pw)
            self.top_shadow_curves.append(shadow_curve)
            self.top_curves.append(main_curve)
            row, col = divmod(i, 3)
            top_plots_grid.addWidget(pw, row, col)
        top_graphs_layout.addLayout(top_plots_grid)
        right_layout.addWidget(top_graphs_container, 1)

        # --- Bottom: Source (4) + Impedance (1) ---
        bottom_graphs_container = NeonFrame("#FF00FF")
        bottom_graphs_container.setStyleSheet("background-color: #0a0a0a;")
        bottom_graphs_layout = QVBoxLayout(bottom_graphs_container)
        bottom_graphs_layout.setContentsMargins(15, 15, 15, 15)

        bottom_graphs_title = QLabel("PROTON SOURCE")
        bottom_graphs_title.setStyleSheet("color: #FF00FF; font-weight: bold; font-size: 18pt; text-align: center;")
        bottom_graphs_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_graphs_layout.addWidget(bottom_graphs_title)

        bottom_plots_grid = QGridLayout()
        bottom_plots_grid.setSpacing(10)
        self.bottom_plot_widgets, self.bottom_curves, self.bottom_shadow_curves = [], [], []
        bottom_plot_indices = [6, 7, 8, 9, "imp"]
        for idx, key in enumerate(bottom_plot_indices):
            name = "Filament Impedance [Ω]" if key == "imp" else RAW_LABELS[key]
            pw = pg.PlotWidget()
            style_plot_cyberpunk(pw, name)
            color = neon_color(idx + 6)
            shadow_curve, main_curve = add_cyberpunk_series(pw, color, name)
            for c in (main_curve, shadow_curve):
                c.setSymbol(None); c.setSymbolBrush(None); c.setSymbolPen(None)
            self.bottom_plot_widgets.append(pw)
            self.bottom_shadow_curves.append(shadow_curve)
            self.bottom_curves.append(main_curve)
            row, col = divmod(idx, 3)
            bottom_plots_grid.addWidget(pw, row, col)
        bottom_graphs_layout.addLayout(bottom_plots_grid)
        right_layout.addWidget(bottom_graphs_container, 1)

        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setSizes([514, 1286])

        self.plot_widgets = self.top_plot_widgets + self.bottom_plot_widgets
        self.curves = self.top_curves + self.bottom_curves
        self.shadow_curves = self.top_shadow_curves + self.bottom_shadow_curves

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(int(1000/plot_rate_hz))

    # Programmatic split control
    def set_split_ratio(self, left_px: int, right_px: int):
        self.main_splitter.setSizes([max(100,left_px), max(200,right_px)])

    # ---------- Background paint ----------
    def paintEvent(self, event):
        if not self._bg.isNull():
            p = QPainter(self)
            scaled = self._bg.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(self.rect(), scaled)
        super().paintEvent(event)

    # ---------- Helpers ----------
    @staticmethod
    def _fmt(v):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return "—"
        try:
            return f"{float(v):.3f}"
        except Exception:
            return "—"

    def _vector12(self, pkt: dict):
        """Return raw list of length 12 (extraction[6] + source[6]) or [None]*12."""
        ext = pkt.get("extraction")
        src = pkt.get("source")

        if isinstance(ext, (list, tuple)) and len(ext) >= 6 and isinstance(src, (list, tuple)) and len(src) >= 6:
            return list(ext[:6]) + list(src[:6])

        # Back-compat: old 7-source vector
        if isinstance(src, (list, tuple)) and len(src) == 7:
            old = list(src)
            # ["Arc Voltage","Arc Current","Filament","ESD kV","ESD mA","Outside Iron","Inside Iron"]
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
                coax_a, inside_a, outside_a, esd_v, esd_a, fringe_a,
                fil_a, fil_v, arc_v, arc_a, gas_flow, gas_turns
            ]

        return [None] * RAW_COUNT

    def _angles6(self, pkt: dict):
        """Return angles list of length 6 (or [None]*6) from pkt['extraction_angles']"""
        ang = pkt.get("extraction_angles")
        if isinstance(ang, (list, tuple)) and len(ang) >= ANGLE_COUNT:
            return list(ang[:ANGLE_COUNT])
        return [None] * ANGLE_COUNT

    def _scale_or_nan(self, key: str, v):
        """Safe raw→eng scaling that tolerates None/NaN."""
        try:
            if v is None:
                return float("nan")
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                return float("nan")
            return self.scaler.raw_to_eng(key, f)
        except Exception:
            return float("nan")

    def _drain_latest(self):
        """
        Return the newest packet from self.data_queue without blocking.
        Works with collections.deque, queue.Queue, or multiprocessing.Queue.
        """
        q = self.data_queue
        latest = None

        # Fast path: deque (has popleft/append)
        if hasattr(q, "popleft"):
            while q:
                latest = q.popleft()
            return latest

        # Generic Queue: get_nowait until empty
        get_nowait = getattr(q, "get_nowait", None)
        if callable(get_nowait):
            while True:
                try:
                    latest = get_nowait()
                except Exception:
                    break
            return latest

        # Last resort: non-blocking get(False) if available
        get_nb = getattr(q, "get", None)
        if callable(get_nb):
            while True:
                try:
                    latest = get_nb(False)  # non-blocking
                except Exception:
                    break
            return latest

        return None

    # ---------- GUI update ----------
    def update_gui(self):
        now = time.time()

        # Drain to last (compatible with deque or Queue)
        last = self._drain_latest()

        if last is None:
            # status when no new data
            if time.time() - self._last_data_ts < 1.0:
                self.status.setText("🟢 RECEIVING DATA")
                self.status.setStyleSheet("color: #00FF00; font-weight: bold; font-size: 10pt;")
            else:
                self.status.setText("🔴 NO DATA")
                self.status.setStyleSheet("color: #FF0000; font-weight: bold; font-size: 10pt;")
            return

        vec_raw = self._vector12(last)
        ang_raw = self._angles6(last)

        if not any(v is not None for v in vec_raw) and not any(a is not None for a in ang_raw):
            return

        self._last_data_ts = now
        self.time_buffer.append(now if not self.time_buffer else now)

        # -------- SCALE: extraction (6) + source (6) to engineering units --------
        scaled_vals = []
        for i, key in enumerate(EXTR_BASE_KEYS):
            scaled_vals.append(self._scale_or_nan(key, vec_raw[i]))
        for j, key in enumerate(SOURCE_KEYS):
            scaled_vals.append(self._scale_or_nan(key, vec_raw[6 + j]))

        # Store latest & push to buffers (scaled)
        for i in range(RAW_COUNT):
            val = scaled_vals[i]
            self.latest_eng[i] = val
            self.eng_buffers[i].append(val)
            self.table.item(i, 1).setText(self._fmt(val))
            # error (range in current window) in ENG units
            if len(self.eng_buffers[i]) > 0:
                data_min = min(self.eng_buffers[i])
                data_max = max(self.eng_buffers[i])
                self.table.item(i, 2).setText(self._fmt(data_max - data_min))
            else:
                self.table.item(i, 2).setText("—")

        # -------- angles (scaled via EXTR_ANGLE_KEYS) --------
        for j, key in enumerate(EXTR_ANGLE_KEYS):
            fa = self._scale_or_nan(key, ang_raw[j])
            self.latest_angles[j] = fa
            self.table.item(RAW_COUNT + j, 1).setText(self._fmt(fa))
            self.table.item(RAW_COUNT + j, 2).setText("—")

        # -------- impedance (use scaled filament V/A) --------
        fil_a = self.latest_eng[IDX_FIL_A]
        fil_v = self.latest_eng[IDX_FIL_V]
        if fil_a is None or (isinstance(fil_a, float) and (math.isnan(fil_a) or abs(fil_a) < 1e-9)):
            imp = float("nan")
        else:
            imp = fil_v / fil_a if (fil_v is not None and not (isinstance(fil_v, float) and math.isnan(fil_v))) else float("nan")

        self.latest_imp = imp
        self.imp_buffer.append(imp)
        self.table.item(TABLE_COUNT - 1, 1).setText(self._fmt(imp))
        if len(self.imp_buffer) > 0:
            self.table.item(TABLE_COUNT - 1, 2).setText(self._fmt(max(self.imp_buffer) - min(self.imp_buffer)))
        else:
            self.table.item(TABLE_COUNT - 1, 2).setText("—")

        # -------- plots (all scaled) --------
        if len(self.time_buffer) >= 2:
            t0 = self.time_buffer[0]
            xs = [t - t0 for t in self.time_buffer]
            # top 6 = extraction
            for i in range(6):
                data = list(self.eng_buffers[i])
                self.top_shadow_curves[i].setData(xs, data)
                self.top_curves[i].setData(xs, data)
            # bottom 5 = source + impedance
            bottom_plot_indices = [6, 7, 8, 9, "imp"]
            for idx, key in enumerate(bottom_plot_indices):
                if key == "imp":
                    data = list(self.imp_buffer)
                else:
                    data = list(self.eng_buffers[key])
                self.bottom_shadow_curves[idx].setData(xs, data)
                self.bottom_curves[idx].setData(xs, data)

        # status
        if time.time() - self._last_data_ts < 1.0:
            self.status.setText("🟢 RECEIVING DATA")
            self.status.setStyleSheet("color: #00FF00; font-weight: bold; font-size: 10pt;")
        else:
            self.status.setText("🔴 NO DATA")
            self.status.setStyleSheet("color: #FF0000; font-weight: bold; font-size: 10pt;")

# ---------------- Demo/test application ----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Proton Source & Extraction Monitor - Demo")
        # Use deque here; in your real app you might pass queue.Queue or mp.Queue
        self.data_queue = deque()
        self.monitor = SourceExtractionMonitoringWindow(self.data_queue)
        self.setCentralWidget(self.monitor)
        self.sim_timer = QTimer(self)
        self.sim_timer.timeout.connect(self.generate_simulated_data)
        self.sim_timer.start(250)  # 4 Hz
        self.sim_time = 0
    def generate_simulated_data(self):
        """Generate simulated extraction and source data"""
        self.sim_time += 0.25
        extraction_data = [
            random.uniform(1.0, 2.0),   # Coax [A]
            random.uniform(0.5, 1.5),   # Inside Iron [A]
            random.uniform(0.8, 1.8),   # Outside Iron [A]
            random.uniform(1000, 2000), # ESD [V]
            random.uniform(0.1, 0.5),   # ESD [A]
            random.uniform(0.2, 0.8)    # Fringe [A]
        ]
        source_data = [
            random.uniform(50, 100),    # Filament [A]
            random.uniform(5, 15),      # Filament [V]
            random.uniform(50, 150),    # Arc [V]
            random.uniform(1, 5),       # Arc [A]
            random.uniform(10, 20),     # Gas Flow
            random.uniform(2, 8)        # Gas Turns
        ]
        angles_data = [
            random.uniform(10, 30),     # ESD Entrance [°]
            random.uniform(20, 40),     # ESD Exit [°]
            random.uniform(15, 35),     # Coax Entrance [°]
            random.uniform(25, 45),     # Coax Exit [°]
            random.uniform(5, 25),      # Iron Entrance [°]
            random.uniform(30, 50)      # Iron Exit [°]
        ]
        data_packet = {
            "timestamp": time.time(),
            "extraction": extraction_data,
            "source": source_data,
            "extraction_angles": angles_data
        }
        self.data_queue.append(data_packet)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("QMainWindow { background-color: #000000; }")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
