# -*- coding: utf-8 -*-
"""Tesla-style Magnetic Field Monitoring window.

Keeps the existing control-system interfaces:
  data_queue, target_values, plot_config, scaling.py, app_settings,
  bitmask ON/REMOTE states, hold-last behavior, and 14-channel buffers.
"""
from project_paths import project_file

import time
from pathlib import Path
from collections import deque

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QLinearGradient, QFont, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame
)
import pyqtgraph as pg

from scaling import get_scaler, TRIM_KEYS
from app_settings import app_settings

pg.setConfigOptions(antialias=True)

NUM_CHANNELS = 14
NUM_TRIM = 12
CHANNEL_LABELS = [f"TC {i+1}" for i in range(12)] + ["Main Magnet", "Centering Beam"]

# ============================================================
# TESLA STYLE
# ============================================================
BG = "#05090C"
PANEL = "#0B1115"
BORDER = "#263038"
TEXT = "#F4F4F4"
MUTED = "#9FA7AD"
GRID = "#31404A"
BLUE = "#209CFF"
GREEN = "#31E56F"
YELLOW = "#F0C83B"
RED = "#FF4B4B"

TC_COLORS = [
    "#168BFF", "#00C8D7", "#6BCB21", "#D7D7D7",
    "#A855F7", "#00C8D7", "#E72593", "#C8C8C8",
    "#FF7A00", "#F5C400", "#73B51B", "#D7D7D7",
]


def qcolor(hex_color, alpha=255):
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def make_card():
    card = QFrame()
    card.setObjectName("TeslaCard")
    card.setStyleSheet(f"""
        QFrame#TeslaCard {{
            background:{PANEL};
            border:1px solid {BORDER};
            border-radius:14px;
        }}
    """)
    return card


def make_label(text, size=10, color=TEXT, bold=False):
    label = QLabel(text)
    weight = QFont.Weight.DemiBold if bold else QFont.Weight.Normal
    label.setFont(QFont("Segoe UI", size, weight))
    label.setStyleSheet(f"color:{color}; background:transparent; border:none;")
    return label


def style_plot_tesla(plot, title):
    plot.setBackground(PANEL)
    plot.setTitle(title, color=BLUE, size="11pt")
    for axis_name in ("left", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(qcolor(GRID, 170), width=1))
        axis.setTextPen(pg.mkPen(qcolor(MUTED, 240)))
        axis.setStyle(tickLength=-5)
    plot.showGrid(x=True, y=True, alpha=0.10)
    plot.setLabel("bottom", "Time (s)", color=MUTED)
    plot.setMouseEnabled(x=False, y=False)
    plot.setMenuEnabled(False)
    plot.setClipToView(True)
    plot.getPlotItem().setContentsMargins(8, 8, 8, 8)
    return plot


def add_tesla_series(plot, color_hex, name):
    color = QColor(color_hex)
    glow_color = QColor(color)
    glow_color.setAlpha(45)
    glow = plot.plot(pen=pg.mkPen(glow_color, width=5), antialias=True)
    line = plot.plot(
        pen=pg.mkPen(color, width=1.8), name=name, symbol=None, antialias=True
    )

    fill = QLinearGradient(0, 0, 0, 1)
    fill.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
    fill.setColorAt(0.0, qcolor(color_hex, 70))
    fill.setColorAt(0.45, qcolor(color_hex, 28))
    fill.setColorAt(1.0, qcolor(color_hex, 0))
    line.setFillLevel(0.0)
    line.setBrush(QBrush(fill))
    glow.setZValue(line.zValue() - 1)
    try:
        line.setDownsampling(auto=True)
    except Exception:
        pass
    line.setClipToView(True)
    return glow, line


class MagneticFieldMonitoringWindow(QWidget):
    scalingFactorsUpdated = pyqtSignal()

    def __init__(self, data_queue, target_values, plot_config, scaler=None):
        super().__init__()
        self.setWindowTitle("Magnetic Field Monitoring")
        self.resize(1600, 950)
        self.setMinimumSize(1180, 720)
        self.setStyleSheet(f"""
            QWidget {{
                background:{BG};
                color:{TEXT};
                font-family:"Segoe UI";
            }}
        """)

        self.data_queue = data_queue
        self.target_values = list(target_values)
        if len(self.target_values) < NUM_CHANNELS:
            self.target_values += [0.0] * (NUM_CHANNELS - len(self.target_values))
        self.plot_config = plot_config or {}
        self.current_values = [0.0] * NUM_CHANNELS
        self.raw_values = [0.0] * NUM_CHANNELS

        self.time_buffer = deque()
        self.data_buffers = [deque() for _ in range(NUM_CHANNELS)]
        self.raw_buffers = [deque() for _ in range(NUM_CHANNELS)]
        self.last_data_time = 0.0

        self.scaler = scaler or get_scaler()
        self.scaling_enabled = True

        self._last_sample = None
        self._misses = 0
        self.HOLD_SECONDS = 5.0
        self.heartbeat = None

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_gui)

        self.scaling_timer = QTimer(self)
        self.scaling_timer.timeout.connect(self.refresh_scaling_factors)
        self.scaling_timer.start(5000)

        self.apply_plot_settings()
        self.timer.start()

    # ========================================================
    # UI
    # ========================================================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 18)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        body.addLayout(left, 42)
        body.addLayout(right, 58)
        root.addLayout(body, 1)

        left.addWidget(self._build_table_card(), 7)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        lower.addWidget(self._build_bar_card(), 7)

        metrics = QVBoxLayout()
        metrics.setSpacing(12)
        (self.mm_value_label, self.mm_error_label, self.mm_reg_label,
         self.mm_spark_curve, mm_card) = self._build_metric_card("MAIN MAGNET", "A")
        (self.cb_value_label, self.cb_error_label, self.cb_reg_label,
         self.cb_spark_curve, cb_card) = self._build_metric_card("CENTERING BEAM", "A")
        metrics.addWidget(mm_card)
        metrics.addWidget(cb_card)
        
        
        
        
        lower.addLayout(metrics, 4)
        left.addLayout(lower, 4)
        
        
        


        self.plot_widgets = []
        self.curves = [[] for _ in range(3)]
        self.shadow_curves = [[] for _ in range(3)]
        self.plot_groups = {
            0: [0, 1, 2, 3],
            1: [4, 5, 6, 7],
            2: [8, 9, 10, 11],
        }
        titles = [
            "GROUP 1   (TC 1 – TC 4)",
            "GROUP 2   (TC 5 – TC 8)",
            "GROUP 3   (TC 9 – TC 12)",
        ]

        for group_idx in range(3):
            card = make_card()
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 8, 10, 8)
            plot = pg.PlotWidget()
            style_plot_tesla(plot, titles[group_idx])
            legend = plot.addLegend(
                offset=(-8, 8), brush=pg.mkBrush(0, 0, 0, 0), pen=pg.mkPen(0, 0, 0, 0)
            )
            try:
                legend.setLabelTextColor(MUTED)
            except Exception:
                pass

            for ch in self.plot_groups[group_idx]:
                glow, line = add_tesla_series(plot, TC_COLORS[ch], CHANNEL_LABELS[ch])
                self.shadow_curves[group_idx].append((ch, glow))
                self.curves[group_idx].append((ch, line))

            self.plot_widgets.append(plot)
            lay.addWidget(plot)
            right.addWidget(card, 1)

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(16)

        logo = QLabel()
        logo.setFixedSize(86, 64)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = project_file('cnl_logo.png')
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                logo.setPixmap(pix.scaled(
                    80, 58, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))
        else:
            logo.setText("C")
            logo.setFont(QFont("Segoe UI", 24, QFont.Weight.DemiBold))
            logo.setStyleSheet(f"""
                QLabel {{ color:{TEXT}; background:transparent;
                         border:2px solid {TEXT}; border-radius:28px; }}
            """)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = make_label("CROCKER NUCLEAR LABORATORY", 11, TEXT, True)
        brand.setStyleSheet(f"color:{TEXT}; background:transparent; border:none; letter-spacing:2px;")
        subbrand = make_label("ISOCHRONOUS CYCLOTRON", 8, MUTED)
        subbrand.setStyleSheet(f"color:{MUTED}; background:transparent; border:none; letter-spacing:1px;")
        brand_box.addWidget(brand)
        brand_box.addWidget(subbrand)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = make_label("ISOCHRONOUS MAGNETIC FIELD", 20, TEXT, True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{TEXT}; background:transparent; border:none; letter-spacing:3px;")
        subtitle = make_label("MONITORING SOURCES", 9, MUTED)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color:{MUTED}; background:transparent; border:none; letter-spacing:3px;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self.header_status = make_label("●  NO DATA", 10, RED, True)
        self.clock_label = make_label(time.strftime("%H:%M:%S"), 10, TEXT)

        header.addWidget(logo)
        header.addLayout(brand_box)
        header.addStretch(1)
        header.addLayout(title_box, 3)
        header.addStretch(1)
        header.addWidget(self.header_status)
        header.addSpacing(12)
        header.addWidget(self.clock_label)
        return header

    def _build_table_card(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = make_label("TRIM COILS", 13, TEXT, True)
        title.setStyleSheet(f"color:{TEXT}; background:transparent; border:none; letter-spacing:1px;")
        lay.addWidget(title)

        self.table = QTableWidget(NUM_TRIM, 6)
        self.table.setHorizontalHeaderLabels(
            ["CHANNEL", "CURRENT (A)", "TARGET (A)", "ERROR (A)", "STATUS", "REMOTE"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background:{PANEL}; color:{TEXT}; border:none;
                            gridline-color:transparent; font-size:10pt; }}
            QTableWidget::item {{ border-bottom:1px solid #1D262C;
                                  padding-left:7px; padding-right:5px; }}
            QHeaderView::section {{ background:{PANEL}; color:{MUTED}; border:none;
                                    border-bottom:1px solid #354149; padding:7px;
                                    font-size:8pt; font-weight:600; }}
        """)

        for row in range(NUM_TRIM):
            vals = [
                CHANNEL_LABELS[row], "0.000", f"{self.target_values[row]:.3f}",
                "0.000", "●  OFF", "●"
            ]
            for col, value in enumerate(vals):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col in (4, 5):
                    item.setForeground(QBrush(QColor(RED)))
                elif col == 0:
                    item.setForeground(QBrush(QColor(TEXT)))
                else:
                    item.setForeground(QBrush(QColor("#D4D8DB")))
                self.table.setItem(row, col, item)

        self.table.setMinimumHeight(360)
        lay.addWidget(self.table)
        return card

    def _build_bar_card(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(4)
        lay.addWidget(make_label("TRIM COILS OVERVIEW (A)", 11, TEXT, True))

        self.trim_bar_plot = pg.PlotWidget()
        self.trim_bar_plot.setBackground(PANEL)
        self.trim_bar_plot.showGrid(x=False, y=True, alpha=0.08)
        self.trim_bar_plot.setMouseEnabled(x=False, y=False)
        self.trim_bar_plot.setMenuEnabled(False)
        self.trim_bar_plot.setYRange(0, 620)
        self.trim_bar_plot.setXRange(0.3, 12.7, padding=0)

        la = self.trim_bar_plot.getAxis("left")
        la.setPen(pg.mkPen(qcolor(GRID, 130)))
        la.setTextPen(pg.mkPen(qcolor(MUTED, 230)))
        ba = self.trim_bar_plot.getAxis("bottom")
        ba.setPen(pg.mkPen(qcolor(GRID, 130)))
        ba.setTextPen(pg.mkPen(qcolor(MUTED, 230)))
        ba.setTicks([[(i, str(i)) for i in range(1, 13)]])
        self.trim_bar_plot.setLabel("bottom", "TRIM COIL", color=MUTED)

        self.trim_bars = []
        for ch in range(NUM_TRIM):
            grad = QLinearGradient(0, 0, 0, 1)
            grad.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
            grad.setColorAt(0.0, qcolor(TC_COLORS[ch], 240))
            grad.setColorAt(1.0, qcolor(TC_COLORS[ch], 120))
            bar = pg.BarGraphItem(
                x=[ch + 1], height=[0], width=0.55, brush=QBrush(grad),
                pen=pg.mkPen(qcolor(TC_COLORS[ch], 180), width=0.8)
            )
            self.trim_bar_plot.addItem(bar)
            self.trim_bars.append((ch, bar))

        lay.addWidget(self.trim_bar_plot)
        return card

    def _build_metric_card(self, title, unit):
        card = make_card()
        card.setMinimumWidth(205)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 12)
        lay.setSpacing(3)
        lay.addWidget(make_label(title, 10, TEXT))

        value_row = QHBoxLayout()
        value = QLabel("0.0")
        value.setFont(QFont("Segoe UI", 25))
        value.setStyleSheet(f"color:{TEXT}; background:transparent; border:none;")
        unit_label = make_label(unit, 11, MUTED)
        value_row.addWidget(value)
        value_row.addWidget(unit_label)
        value_row.addStretch()
        lay.addLayout(value_row)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#253038; border:none;")
        lay.addWidget(sep)

        erow = QHBoxLayout()
        erow.addWidget(make_label("ERROR", 8, MUTED))
        erow.addStretch()
        error = make_label("+0.000 A", 9, GREEN, True)
        erow.addWidget(error)
        lay.addLayout(erow)

        rrow = QHBoxLayout()
        rrow.addWidget(make_label("REGULATION", 8, MUTED))
        rrow.addStretch()
        regulation = make_label("0.000 %", 9, BLUE, True)
        rrow.addWidget(regulation)
        lay.addLayout(rrow)

        spark = pg.PlotWidget()
        spark.setFixedHeight(38)
        spark.setBackground(PANEL)
        spark.hideAxis("left")
        spark.hideAxis("bottom")
        spark.setMouseEnabled(x=False, y=False)
        spark.setMenuEnabled(False)
        curve = spark.plot(pen=pg.mkPen(BLUE, width=1.4), antialias=True)
        lay.addWidget(spark)

        return value, error, regulation, curve, card


    # ========================================================
    # SCALING
    # ========================================================
    def toggle_scaling(self, enabled: bool):
        self.scaling_enabled = enabled
        self.scaling_status.setText("ACTIVE" if enabled else "BYPASSED")
        self.scaling_status.setStyleSheet(
            f"color:{GREEN if enabled else RED}; background:transparent; border:none; font-weight:600;"
        )

    def refresh_scaling_factors(self):
        try:
            if self.scaler and hasattr(self.scaler, "reload_if_changed"):
                self.scaler.reload_if_changed()
                self.scaling_status.setText("UPDATED ✓")
                self.scaling_status.setStyleSheet(
                    f"color:{GREEN}; background:transparent; border:none; font-weight:600;"
                )
                self.scalingFactorsUpdated.emit()
                if self._last_sample:
                    self._process_data_sample(self._last_sample)
        except Exception as exc:
            self.scaling_status.setText("ERROR")
            self.scaling_status.setStyleSheet(
                f"color:{RED}; background:transparent; border:none; font-weight:600;"
            )
            print(f"Scaling refresh error: {exc}")

    def apply_scaling(self, raw_data):
        if not self.scaling_enabled or not self.scaler:
            return (raw_data + [0.0] * NUM_CHANNELS)[:NUM_CHANNELS]
        try:
            scaled = []
            for i, raw_val in enumerate(raw_data[:len(TRIM_KEYS)]):
                scaled.append(self.scaler.raw_to_eng(TRIM_KEYS[i], raw_val))
            while len(scaled) < NUM_CHANNELS:
                scaled.append(0.0)
            return scaled
        except Exception as exc:
            print(f"Scaling error: {exc}")
            return (raw_data + [0.0] * NUM_CHANNELS)[:NUM_CHANNELS]

    # ========================================================
    # SETTINGS + DATA
    # ========================================================
    def apply_plot_settings(self):
        fps = max(1.0, float(app_settings.plot_fps))
        self.timer.setInterval(int(1000.0 / fps))
        max_pts = int(max(1, app_settings.plot_sps * app_settings.plot_window_s))
        self.time_buffer = deque(self.time_buffer, maxlen=max_pts)
        self.data_buffers = [deque(buf, maxlen=max_pts) for buf in self.data_buffers]
        self.raw_buffers = [deque(buf, maxlen=max_pts) for buf in self.raw_buffers]

    def _process_data_sample(self, sample):
        now = time.time()
        raw = list(sample.get("channels", []))
        self.raw_values = (raw + [0.0] * NUM_CHANNELS)[:NUM_CHANNELS]
        self.current_values = self.apply_scaling(raw) if self.scaling_enabled and self.scaler else self.raw_values[:]

        self.time_buffer.append(now)
        for i in range(NUM_CHANNELS):
            self.data_buffers[i].append(self.current_values[i])
            self.raw_buffers[i].append(self.raw_values[i])
        return self.current_values

    # ========================================================
    # GUI UPDATE
    # ========================================================
    def update_gui(self):
        now = time.time()
        last = None
        while True:
            try:
                last = self.data_queue.get_nowait()
            except Exception:
                break

        if last is not None:
            self._last_sample = last
            self.last_data_time = now
            self._misses = 0
            self._process_data_sample(last)
        else:
            self._misses += 1

        # Safe initialization fixes the old UnboundLocalError.
        bitmask = 0
        if self._last_sample is not None:
            bitmask = int(self._last_sample.get("bitmask", 0))

        # TC1..TC12 table
        for i in range(NUM_TRIM):
            

            current = self.current_values[i]
            target = self.target_values[i]

            # Peak-to-peak error over the current plot/data buffer
            if len(self.data_buffers[i]) > 0:

                data = self.data_buffers[i]

                max_peak = max(data)
                min_peak = min(data)

                error = max_peak - min_peak

            else:
                error = 0.0

            self.table.item(i, 1).setText(f"{current:.3f}")
            self.table.item(i, 2).setText(f"{target:.3f}")
            self.table.item(i, 3).setText(f"{error:+.3f}")

            on = (bitmask >> i) & 1
            remote = (bitmask >> (14 + i)) & 1
            self.table.item(i, 4).setText("●  ON" if on else "●  OFF")
            self.table.item(i, 5).setText("●")
            self.table.item(i, 4).setForeground(QBrush(QColor(GREEN if on else RED)))
            self.table.item(i, 5).setForeground(QBrush(QColor(GREEN if remote else RED)))

        # Time-domain plots
        if len(self.time_buffer) >= 2:
            times = [t - self.time_buffer[0] for t in self.time_buffer]
            plot_flags = self.plot_config.get("channels", [True] * NUM_CHANNELS)
            if not any(plot_flags):
                plot_flags = [True] * NUM_CHANNELS

            for group_idx, ch_curves in enumerate(self.curves):
                for (ch, line), (_, glow) in zip(ch_curves, self.shadow_curves[group_idx]):
                    if ch < len(plot_flags) and plot_flags[ch]:
                        data = list(self.data_buffers[ch])
                        glow.setData(times, data)
                        line.setData(times, data)
                    else:
                        glow.clear()
                        line.clear()

        # Matching overview bars
        for ch, bar in self.trim_bars:
            bar.setOpts(height=[self.current_values[ch]])

        # Main Magnet card
        mm = self.current_values[12]
        mm_target = self.target_values[12]
        
        # Main Magnet peak-to-peak error
        if len(self.data_buffers[12]) > 0:
            mm_data = self.data_buffers[12]
            
            mm_max = max(mm_data)
            mm_min = min(mm_data)

            mm_error = mm_max - mm_min
        else:
            mm_error = 0.0
    
        mm_reg = abs(mm_error) / abs(mm_target) * 100.0 if abs(mm_target) > 1e-12 else 0.0
        self.mm_value_label.setText(f"{mm:,.1f}")
        self.mm_error_label.setText(f"{mm_error:+.2f} A")
        self.mm_reg_label.setText(f"{mm_reg:.4f} %")
        self.mm_error_label.setStyleSheet(
            f"color:{GREEN if abs(mm_error) < 5.0 else YELLOW}; background:transparent; border:none; font-weight:600;"
        )

        # Centering Beam card
        cb = self.current_values[13]
        cb_target = self.target_values[13]
        cb_error = cb - cb_target
        cb_reg = abs(cb_error) / abs(cb_target) * 100.0 if abs(cb_target) > 1e-12 else 0.0
        self.cb_value_label.setText(f"{cb:.2f}")
        self.cb_error_label.setText(f"{cb_error:+.3f} A")
        self.cb_reg_label.setText(f"{cb_reg:.3f} %")
        self.cb_error_label.setStyleSheet(
            f"color:{GREEN if abs(cb_error) < 0.10 else YELLOW}; background:transparent; border:none; font-weight:600;"
        )

        self.mm_spark_curve.setData(list(self.data_buffers[12])[-90:])
        self.cb_spark_curve.setData(list(self.data_buffers[13])[-90:])

        # Communication indicator
        age = time.time() - self.last_data_time
        if age < 1.0:
            status_text, status_color = "●  RECEIVING DATA", GREEN
        elif age <= self.HOLD_SECONDS:
            status_text, status_color = "●  HOLDING LAST", YELLOW
        else:
            status_text, status_color = "●  NO DATA", RED

        style = f"color:{status_color}; background:transparent; border:none; font-weight:600;"
        self.header_status.setText(status_text)
        self.header_status.setStyleSheet(style)

        self.clock_label.setText(time.strftime("%H:%M:%S"))

    def closeEvent(self, event):
        self.scaling_timer.stop()
        self.timer.stop()
        event.accept()
