# -*- coding: utf-8 -*-
"""Beam Transport monitor: working receive logic + CNL/Tesla presentation.

Only this module needs replacing. Keep the existing launcher, data queue,
transport channel order, incoming values, and sample-count/error calculations.
Data are read from packet["transport"] exactly as in the working monitor;
this file does not apply calibration, create a receiver, or write setpoints.

The visible time axis is relative to the monitor's current receipt clock:
-2 min ... -1 min ... Now for seconds=120. Old data move left even when the
queue is empty; numeric readouts retain the last values, as before.
The ERROR column remains the peak-to-peak range of the stored sample buffer.

The visible-shadow revision keeps the existing trace colors but adds a stronger
two-layer halo and a separate gradient under-fill to the visible plot floor.
The under-fill is decorative, not an integral or an uncertainty band. It is
excluded from auto-ranging, so it cannot stretch the current axis. The
previous zero-inclusive current-axis bounds are retained. Only the local
trace-rendering helper changes; the receive and time-axis code is unchanged.

Place cnl_logo.png beside this file and open the window from the normal GUI.
A standalone synthetic preview is available ONLY with the --demo argument.
"""
from project_paths import project_file

import time
import math
from pathlib import Path
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QLinearGradient, QBrush, QColor, QFont
import pyqtgraph as pg

pg.setConfigOptions(antialias=True)

# Packet order and channel labels are unchanged.
LABELS = [
    "1_A Quadrupoles [A]",
    "1_B Quadrupoles [A]",
    "2_A Quadrupoles [A]",
    "2_B Quadrupoles [A]",
    "Steering 40\" [A]",
    "Steering 50\" [A]",
    "Collimator S1 Left [A]",
    "Collimator S1 Right [A]",
    "Collimator S2 Left [A]",
    "Collimator S2 Right [A]",
]
COUNT = len(LABELS)

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

# Same per-channel palette as the uploaded Magnetic Field monitor, in order.
# Only the trace appearance changes; packet order and measured values do not.
TRANSPORT_COLORS = [
    "#168BFF", "#00C8D7", "#6BCB21", "#D7D7D7",
    "#A855F7", "#00C8D7", "#E72593", "#C8C8C8",
    "#FF7A00", "#F5C400",
]
HSV_COLORS = TRANSPORT_COLORS  # compatibility for older helper imports


def neon_color(i: int) -> QColor:
    return QColor(TRANSPORT_COLORS[i % len(TRANSPORT_COLORS)])


def qcolor(hex_color, alpha=255):
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


def make_card():
    card = QFrame()
    card.setObjectName("TeslaCard")
    card.setStyleSheet(f"""
        QFrame#TeslaCard {{
            background:{PANEL}; border:1px solid {BORDER}; border-radius:14px;
        }}
    """)
    return card


def make_label(text, size=10, color=TEXT, bold=False):
    label = QLabel(text)
    font = QFont("Segoe UI", size)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    label.setStyleSheet(f"color:{color}; background:transparent; border:none;")
    return label


def style_plot_tesla(plot: pg.PlotWidget, title: str):
    plot.setBackground(PANEL)
    plot.setTitle(title, color=BLUE, size="10pt")
    for axis_name in ("left", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(qcolor(GRID, 170), width=1))
        axis.setTextPen(pg.mkPen(qcolor(MUTED, 240)))
        axis.setStyle(tickLength=-5)
    plot.showGrid(x=True, y=True, alpha=0.10)
    plot.setClipToView(True)
    plot.setMouseEnabled(x=False, y=False)
    plot.setMenuEnabled(False)
    plot.setAutoVisible(y=True)
    plot.plotItem.setContentsMargins(6, 6, 8, 6)
    return plot


# Appearance controls only; none of these modify incoming measurements.
TRACE_LINE_WIDTH = 1.8
TRACE_INNER_HALO_WIDTH = 4.5
TRACE_INNER_HALO_ALPHA = 90
TRACE_OUTER_HALO_WIDTH = 10.0
TRACE_OUTER_HALO_ALPHA = 32
TRACE_SHADE_ALPHA = 110  # 0 = transparent; 255 = opaque


def add_tesla_series(plot: pg.PlotWidget, color: QColor, name: str):
    """Draw a crisp trace, a two-layer halo, and visible shading beneath it.

    The returned pair is still (glow, line), so the existing data-update loop
    remains untouched. Both items receive the same x/y data as before.

    The old fill ended at y=0, which has no area when a trace is exactly zero.
    Here the decorative fill ends at the visible lower edge, even for zero or
    negative readings. Its baseline is excluded from axis auto-ranging.
    """
    # A native OpenGL curve fast path may flatten/omit a gradient QBrush.
    # Use Qt's painter for these local plots only, not a global pg setting.
    # This keeps the gradient independent of the main application's backend.
    plot.useOpenGL(False)

    color = QColor(color)
    outer_color = QColor(color)
    outer_color.setAlpha(TRACE_OUTER_HALO_ALPHA)
    inner_color = QColor(color)
    inner_color.setAlpha(TRACE_INNER_HALO_ALPHA)

    # This same item draws the broad halo and the area shadow. Ignoring its
    # bounds prevents the view-dependent fill level from causing auto-range
    # feedback (the y-axis must not keep expanding to include its own shadow).
    glow = pg.PlotDataItem(
        pen=pg.mkPen(outer_color, width=TRACE_OUTER_HALO_WIDTH),
        symbol=None, antialias=True, fillOutline=False,
    )
    plot.addItem(glow, ignoreBounds=True)
    line = plot.plot(
        pen=pg.mkPen(color, width=TRACE_LINE_WIDTH),
        shadowPen=pg.mkPen(inner_color, width=TRACE_INNER_HALO_WIDTH),
        name=name, symbol=None, antialias=True,
    )

    # Preserve the previous zero-inclusive current-axis range, but move the
    # actual brush to the separate glow item instead of drawing the fill twice.
    line.setFillLevel(0.0)
    line.setBrush(None)
    glow.setZValue(line.zValue() - 1)
    for curve in (glow, line):
        curve.setClipToView(True)
        try:
            curve.setDownsampling(auto=True)
        except Exception:
            pass

    view_box = plot.getViewBox()
    last_gradient = None
    refreshing = False

    def refresh_shadow(*_args):
        """Refresh only display geometry; never change samples or axis limits."""
        nonlocal last_gradient, refreshing
        if refreshing:
            return
        refreshing = True
        try:
            lower, upper = map(float, view_box.viewRange()[1])
            if not (math.isfinite(lower) and math.isfinite(upper) and upper > lower):
                return

            # getData() uses the same clipping/downsampling as the visible line.
            # This calculation sets brush coordinates only; data are not filtered.
            _xs, ys = line.getData()
            finite_y = [] if ys is None else [float(v) for v in ys if math.isfinite(v)]
            if not finite_y:
                glow.setBrush(None)
                last_gradient = None
                return

            top = min(upper, max(finite_y))
            if top <= lower:
                top = upper
            geometry = (lower, top)
            if geometry == last_gradient:
                return
            last_gradient = geometry

            # Plot coordinates increase upward. Start at the trace's high edge
            # and fade toward the LOWER y coordinate, not the opposite direction.
            # Logical coordinates avoid the inverted object-bounding gradient.
            fill = QLinearGradient(0.0, top, 0.0, lower)
            fill.setCoordinateMode(QLinearGradient.CoordinateMode.LogicalMode)
            for position, fraction in ((0.0, 1.0), (0.35, 0.60),
                                       (0.75, 0.18), (1.0, 0.0)):
                fill_color = QColor(color)
                fill_color.setAlpha(round(TRACE_SHADE_ALPHA * fraction))
                fill.setColorAt(position, fill_color)
            glow.setFillLevel(lower)
            glow.setBrush(QBrush(fill))
        finally:
            refreshing = False

    # The main line is updated AFTER the glow by the existing refresh loop.
    # Refreshing on its signal keeps the shadow synchronized with that frame.
    # Range changes cover auto-scaling and window resizing without extra timers.
    line.sigPlotChanged.connect(refresh_shadow)
    view_box.sigYRangeChanged.connect(refresh_shadow)
    return glow, line


# Preserve old helper names without retaining the old cyberpunk appearance.
style_plot_cyberpunk = style_plot_tesla
add_cyberpunk_series = add_tesla_series


def _history_tick_text(seconds_ago):
    """Human-readable negative age; coordinates remain in seconds internally."""
    age = abs(float(seconds_ago))
    if age < 1e-9:
        return "Now"
    if age >= 60.0:
        return f"−{age / 60.0:g} min"
    return f"−{age:g} s"


def _history_ticks(history_seconds):
    # All ten plots use the same fixed reference and the same three major ticks.
    span = float(history_seconds)
    majors = [(-span, _history_tick_text(span)),
              (-span / 2.0, _history_tick_text(span / 2.0)), (0.0, "Now")]
    minors = [(-0.75 * span, ""), (-0.25 * span, "")]
    return [majors, minors]


def _history_caption(history_seconds):
    span = float(history_seconds)
    unit, value = ("min", span / 60.0) if span >= 60.0 else ("s", span)
    return f"LAST {value:g} {unit}  |  RIGHT EDGE = NOW"


# =============================================================================
# Presentation-only CNL header. Kept local so this file stays a drop-in module.
# =============================================================================
class _CNLMonitorHeader(QFrame):
    """Laboratory branding, responsive titles, data-age indicator, and clock."""

    def __init__(self, title, subtitle="MONITORING SOURCES", parent=None):
        super().__init__(parent)
        self.setObjectName("CNLMonitorHeader")
        self.setStyleSheet("QFrame#CNLMonitorHeader { background:transparent; border:none; }")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.hold_seconds = 5.0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 4)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(8)

        self._brand_panel = QWidget()
        brand_row = QHBoxLayout(self._brand_panel)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(12)

        self.logo = QLabel()
        self.logo.setFixedSize(86, 64)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Resolve relative to the module, not the process working directory.
        # Accept both spellings used by the existing monitoring modules.
        logo_pixmap = QPixmap()
        for filename in ("cnl_logo.png", "CNL_logo.png"):
            candidate = project_file(filename)
            if candidate.is_file():
                logo_pixmap = QPixmap(str(candidate))
                if not logo_pixmap.isNull():
                    break
        if not logo_pixmap.isNull():
            self.logo.setPixmap(logo_pixmap.scaled(
                80, 58, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
            self.logo.setText("C")
            self.logo.setFont(QFont("Segoe UI", 24, QFont.Weight.DemiBold))
            self.logo.setStyleSheet(
                "color:#F4F4F4; background:transparent; "
                "border:2px solid #F4F4F4; border-radius:28px;"
            )
        brand_row.addWidget(self.logo)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(2)
        self.brand_label = self._label(
            "CROCKER NUCLEAR LABORATORY", 10, "#F4F4F4", True, 0.8
        )
        self.subbrand_label = self._label(
            "ISOCHRONOUS CYCLOTRON", 8, "#9FA7AD", False, 1.0
        )
        brand_text.addWidget(self.brand_label)
        brand_text.addWidget(self.subbrand_label)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)

        self._title_panel = QWidget()
        title_box = QVBoxLayout(self._title_panel)
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(3)
        self.title_label = self._label(title, 20, "#F4F4F4", True, 2.0)
        self.subtitle_label = self._label(subtitle, 9, "#9FA7AD", False, 2.0)
        for label in (self.title_label, self.subtitle_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            title_box.addWidget(label)

        self._status_panel = QWidget()
        status_row = QHBoxLayout(self._status_panel)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(7)
        status_row.addStretch(1)
        self.status_dot = self._label("●", 12, "#FF4B4B")
        self.status = self._label("NO DATA", 9, "#FF4B4B", True)
        # Reserve space for the longest state to prevent header movement.
        self.status.setMinimumWidth(
            self.status.fontMetrics().horizontalAdvance("RECEIVING DATA") + 8
        )
        self.clock_label = self._label(time.strftime("%H:%M:%S"), 10, "#F4F4F4")
        self.clock_label.setMinimumWidth(
            self.clock_label.fontMetrics().horizontalAdvance("00:00:00") + 4
        )
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status)
        status_row.addSpacing(10)
        status_row.addWidget(self.clock_label)

        self._compact = None
        self._arrange_header(True)
        self.refresh(0.0)

    @staticmethod
    def _label(text, size, color, bold=False, spacing=0.0):
        label = QLabel(text)
        font = QFont("Segoe UI", size)
        if bold:
            font.setWeight(QFont.Weight.DemiBold)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        label.setFont(font)
        label.setStyleSheet(f"color:{color}; background:transparent; border:none;")
        return label

    def _arrange_header(self, compact):
        if compact == self._compact:
            return
        for widget in (self._brand_panel, self._title_panel, self._status_panel):
            self._grid.removeWidget(widget)
        if compact:
            # At narrower sizes the title gets its own row; never overlap labels.
            self._grid.addWidget(self._brand_panel, 0, 0, 1, 2)
            self._grid.addWidget(self._status_panel, 0, 2)
            self._grid.addWidget(self._title_panel, 1, 0, 1, 3)
            stretches = (1, 1, 0)
        else:
            self._grid.addWidget(self._brand_panel, 0, 0)
            self._grid.addWidget(self._title_panel, 0, 1)
            self._grid.addWidget(self._status_panel, 0, 2)
            stretches = (1, 2, 1)
        for col, stretch in enumerate(stretches):
            self._grid.setColumnStretch(col, stretch)
        self._compact = compact

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_status_panel"):
            self._arrange_header(self.width() < 1350)

    def refresh(self, last_data_timestamp):
        """Status is data freshness, not a device ON/OFF or interlock state."""
        age = time.time() - last_data_timestamp if last_data_timestamp else float("inf")
        if age < 1.0:
            text, color = "RECEIVING DATA", "#31E56F"
        elif age <= self.hold_seconds:
            text, color = "HOLDING LAST", "#F0C83B"
        else:
            text, color = "NO DATA", "#FF4B4B"
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color}; background:transparent; border:none;")
        self.status_dot.setStyleSheet(f"color:{color}; background:transparent; border:none;")
        self.clock_label.setText(time.strftime("%H:%M:%S"))



class BeamTransportMonitoringWindow(QWidget):
    """Expects {"transport": [q1a, q1b, q2a, q2b, steer40, steer50,
                             s1L, s1R, s2L, s2R]}.

    Values are displayed directly, as in the supplied project version.
    This style update does not introduce another transport scaling stage.
    """

    def __init__(self, data_queue, plot_rate_hz=4, seconds=120, bg_image=str(project_file('beam_transport_bg.png'))):
        super().__init__()
        # Validate only display settings; do not modify incoming measurements.
        plot_rate_hz = float(plot_rate_hz)
        seconds = float(seconds)
        if not math.isfinite(plot_rate_hz) or plot_rate_hz <= 0.0:
            raise ValueError("plot_rate_hz must be a positive finite number")
        if not math.isfinite(seconds) or seconds <= 0.0:
            raise ValueError("seconds must be a positive finite number")
        self.history_seconds = seconds
        self.setWindowTitle("Beam Transport Monitoring")
        self.resize(1600, 950)
        self.setMinimumSize(1180, 720)
        self.setStyleSheet(f"""
            QWidget {{ background:{BG}; color:{TEXT}; font-family:"Segoe UI"; }}
            QSplitter::handle {{ background:{BORDER}; width:2px; }}
            QSplitter::handle:hover {{ background:{BLUE}; }}
            QScrollBar:vertical {{ background:{PANEL}; width:9px; margin:0; }}
            QScrollBar::handle:vertical {{ background:#354149; border-radius:4px; min-height:24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
        """)

        # bg_image=None is already used by main_control_gui.py.
        # Keep the argument/attribute, but do not paint the old background.
        self._bg = QPixmap(bg_image) if bg_image else QPixmap()
        self.data_queue = data_queue
        self.max_points = max(2, int(plot_rate_hz * seconds))
        self.time_buffer = deque(maxlen=self.max_points)
        self.buffers = [deque(maxlen=self.max_points) for _ in range(COUNT)]
        self.latest = [float("nan")] * COUNT
        self._last_ts = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 18)
        root.setSpacing(12)
        self.header = _CNLMonitorHeader("BEAM TRANSPORT")
        # Existing status attribute plus matching magnetic-monitor aliases.
        self.logo = self.header.logo
        self.status = self.header.status
        self.status_dot = self.header.status_dot
        self.header_status = self.status
        self.clock_label = self.header.clock_label
        root.addWidget(self.header)

        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background:{BORDER}; border:none;")
        root.addWidget(separator)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        root.addWidget(self.splitter, 1)

        left_panel = make_card()
        left_panel.setMinimumWidth(355)
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(14, 14, 14, 12)
        left_lay.setSpacing(8)
        left_lay.addWidget(make_label("TRANSPORT CHANNELS", 13, TEXT, True))
        description = make_label("QUADRUPOLES · STEERING · COLLIMATORS", 8, MUTED)
        description.setWordWrap(True)
        left_lay.addWidget(description)

        self.table = QTableWidget(COUNT, 3)
        self.table.setHorizontalHeaderLabels(["CHANNEL", "CURRENT (A)", "ERROR (A)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Reserve readable numbers while allowing the channel column to expand.
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(82)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(39)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background:{PANEL}; color:{TEXT}; border:none;
                            gridline-color:transparent; font-size:10pt; }}
            QTableWidget::item {{ border-bottom:1px solid #1D262C;
                                  padding-left:7px; padding-right:5px; }}
            QHeaderView::section {{ background:{PANEL}; color:{MUTED}; border:none;
                                    border-bottom:1px solid #354149; padding:7px;
                                    font-size:8pt; font-weight:600; }}
        """)
        for row, name in enumerate(LABELS):
            for col, value in enumerate((name, "—", "—")):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(QBrush(QColor(TEXT)))
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        left_lay.addWidget(self.table, 1)
        self.data_note = make_label("Waiting for a transport sample.", 8, MUTED)
        self.data_note.setWordWrap(True)
        left_lay.addWidget(self.data_note)
        error_note = make_label("ERROR = peak-to-peak range (max − min) in the stored history.", 8, MUTED)
        error_note.setWordWrap(True)
        left_lay.addWidget(error_note)
        self.splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(8, 0, 0, 0)
        right_lay.setSpacing(8)
        plots_header = QHBoxLayout()
        plots_header.addWidget(make_label("CURRENT HISTORY", 11, TEXT, True))
        plots_header.addStretch(1)
        plots_header.addWidget(make_label(_history_caption(seconds), 9, MUTED))
        right_lay.addLayout(plots_header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(9)
        right_lay.addLayout(grid, 1)
        self.plot_widgets = []
        self.curves = []
        self.shadow_curves = []
        for i, name in enumerate(LABELS):
            card = make_card()
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(5, 5, 5, 5)
            card_lay.setSpacing(0)
            pw = pg.PlotWidget()
            # Same ten plots and three-column ordering as the original module.
            style_plot_tesla(pw, name)
            pw.setLabel("bottom", "Time before now", color=MUTED)
            bottom_axis = pw.getAxis("bottom")
            bottom_axis.enableAutoSIPrefix(False)
            bottom_axis.setTicks(_history_ticks(self.history_seconds))
            pw.setXRange(-self.history_seconds, 0.0, padding=0.0)
            pw.enableAutoRange(x=False, y=True)
            pw.setLabel("left", "Current [A]", color=MUTED)
            pw.setToolTip(
                "Right edge = the current monitor receipt time.\n"
                "Negative times show how long ago a sample reached this window.\n"
                "No samples are invented while data are missing."
            )
            glow, curve = add_tesla_series(pw, neon_color(i), name)
            self.plot_widgets.append(pw)
            self.curves.append(curve)
            self.shadow_curves.append(glow)
            card_lay.addWidget(pw)
            row, col = divmod(i, 3)
            grid.addWidget(card, row, col)
            grid.setRowStretch(row, 1)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([450, 1110])

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(max(1, int(1000 / plot_rate_hz)))

    def set_split_ratio(self, left_px: int, right_px: int):
        self.splitter.setSizes([max(100, left_px), max(200, right_px)])

    @staticmethod
    def _fmt(v):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return "—"
        try:
            return f"{float(v):.3f}"
        except Exception:
            return "—"

    def _refresh_history(self, now):
        """Display-only time translation; original sample buffers are unchanged."""
        if len(self.time_buffer) < 2:
            return
        # Unlike oldest-sample-relative elapsed time, Now stays at the right.
        # Refresh on empty-queue ticks too, so a stale trace is not pinned to Now.
        xs = [stamp - now for stamp in self.time_buffer]
        for i, (curve, glow) in enumerate(zip(self.curves, self.shadow_curves)):
            data = list(self.buffers[i])
            glow.setData(xs, data, connect="finite")
            curve.setData(xs, data, connect="finite")

    def _refresh_receipt_note(self, now):
        """Receipt information only; zero is a measurement, not a communication fault."""
        if not self._last_ts:
            self.data_note.setText("Waiting for a transport sample.")
            return
        age = max(0.0, now - self._last_ts)
        received = time.strftime("%H:%M:%S", time.localtime(self._last_ts))
        if age >= 1.0:
            note = f"Last transport sample: {received} ({age:.1f} s ago). Readouts held."
        elif all(value == 0.0 for value in self.latest):
            note = "Incoming transport packet: all 10 values are exactly zero."
        else:
            note = f"Last transport sample: {received}. Hover here for incoming values."
        self.data_note.setText(note)
        values_text = ", ".join(f"{value:.12g}" for value in self.latest)
        self.data_note.setToolTip(
            f"Module: {Path(__file__).resolve()}\n"
            f"Queue type: {type(self.data_queue).__name__}\n"
            f"Incoming transport values: [{values_text}]\n"
            "No additional transport scaling is applied in this window.\n"
            "Receipt time does not establish the acquisition time of the device."
        )

    # ---------- GUI update: receive path from the working monitor ----------
    def update_gui(self):
        now = time.time()
        last = None
        while not self.data_queue.empty():
            last = self.data_queue.get()


        # Header and time-display refresh do not read or write the queue.
        if last is None:
            self.header.refresh(self._last_ts)
            self._refresh_history(now)
            self._refresh_receipt_note(now)
            return

        vec = None
        if isinstance(last, dict):
            t = last.get("transport")
            if isinstance(t, (list, tuple)) and len(t) >= COUNT:
                vec = t[:COUNT]
        if vec is None:
            self.header.refresh(self._last_ts)
            self._refresh_history(now)
            self._refresh_receipt_note(now)
            return

        self._last_ts = now
        self.time_buffer.append(now if not self.time_buffer else now)

        # Update buffers/table
        for i in range(COUNT):
            try:
                v = float(vec[i])
            except Exception:
                v = float("nan")
            self.latest[i] = v
            self.buffers[i].append(v)
            self.table.item(i, 1).setText(self._fmt(v))
            
            # Calculate and display error (max - min of data buffer)
            if len(self.buffers[i]) > 0:
                data_min = min(self.buffers[i])
                data_max = max(self.buffers[i])
                error = data_max - data_min
                self.table.item(i, 2).setText(f"{error:.3f}")
            else:
                self.table.item(i, 2).setText("—")


        self._refresh_history(now)
        self.header.refresh(self._last_ts)
        self._refresh_receipt_note(now)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "timer") and not self.timer.isActive():
            self.timer.start()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


def _run_demo():
    """Synthetic local preview; never connects to or writes to the cyclotron."""
    import sys
    from PyQt6.QtWidgets import QApplication
    from queue import Queue

    app = QApplication(sys.argv)
    data_queue = Queue()
    window = BeamTransportMonitoringWindow(data_queue, bg_image=None)
    window.setWindowTitle("DEMO — Beam Transport — SIMULATED DATA")
    window.header.subtitle_label.setText("SIMULATED DATA — NOT CONNECTED")
    start = time.monotonic()
    demo_timer = QTimer(window)

    def feed():
        t = time.monotonic() - start
        base = (115.0, 124.0, 132.0, 142.0, 5.2, 6.1, 0.40, 0.43, 0.36, 0.39)
        values = [
            value + max(abs(value) * 0.008, 0.008) * math.sin(0.5 * t + i * 0.6)
            for i, value in enumerate(base)
        ]
        data_queue.put({"transport": values})

    demo_timer.timeout.connect(feed)
    demo_timer.start(250)
    feed()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv:
        _run_demo()
    else:
        print(
            "Open Beam Transport from your normal main GUI to use live data.\n"
            "For a synthetic preview only, run: "
            "python BeamTransportMonitoringWindow.py --demo"
        )
