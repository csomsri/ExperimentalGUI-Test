from project_paths import project_file
# sqlite_viewer_qt.py — CNL record viewer, Tesla-inspired dark interface.
# Based on the uploaded viewer. SQLite queries, schema, alias resolution,
# time-window selection, and sample-index plotting are retained.
# Keep cnl_logo.png and sqlite_viewer_scaling.py beside this file.
# Scaling factors ON/OFF changes the display only; raw SQLite rows stay unchanged.
# Trimming-coil labels use TC1–TC12; database/calibration/drag keys remain ch1–ch12.
# Calibration files are read, never replaced. No sqlite_logger.py changes are required.
# Marker values and PDF export also require sqlite_viewer_report.py beside this file.
# -*- coding: utf-8 -*-

# --- must be the very first matplotlib lines ---
import matplotlib
matplotlib.use("QtAgg")  # Qt canvas compatible with this application's PyQt6 widgets

import sys, os, sqlite3
import re
from functools import lru_cache, partial
from typing import List
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from sqlite_viewer_scaling import ViewerCalibration, identify_column, related_columns
from sqlite_viewer_report import (
    TraceSnapshot, MarkerSnapshot, PlotSnapshot, ReportInfo, snap_sample,
    marker_text, add_marker_artists, position_marker_labels, export_pdf,
)

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from matplotlib.ticker import NullFormatter, MaxNLocator, ScalarFormatter

# ---------------- Config ----------------
LV_OFFSET = 2082844800.0  # LabVIEW -> Unix offset (sec)
DEFAULT_DB = os.environ.get("CNL_SQLITE", str(project_file('cyclotron_data_fresh.db')))
DEBUG = os.environ.get("VIEWER_DEBUG", "0") == "1"

# ---- FIXED CONFIGURABLE PARAMETERS ----
AXIS_LABEL_SIZE = 10
AXIS_TICK_SIZE  = 9
LEGEND_FONT_SIZE= 9
PLOT_TITLE_SIZE = 12
UI_FONT_SIZE    = 11
STATUS_FONT_SIZE= 10

# Layout proportions
COLUMNS_PANEL_WIDTH = 20
PLOTS_PANEL_WIDTH   = 80

# Point limiting
DEFAULT_MAX_POINTS  = 500

# Window dimensions
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900
MIN_WIDTH = 1000
MIN_HEIGHT = 700

# Zoom parameters
ZOOM_FACTOR = 1.2
MIN_Y_RANGE = 1e-10

# ---- Viewer-local Tesla-inspired theme ----
# Existing NEON_* names are kept so the plotting code does not need rewiring.
NEON_BG     = "#090A0C"
NEON_PANEL  = "#181A1E"
NEON_GRID   = "#30343B"
NEON_SPINE  = "#424852"
NEON_TEXT   = "#ECEFF3"
NEON_DIM    = "#A4ADB9"
ACCENT      = "#E83B45"
NEON_COLORS = ["#ED4A54", "#D8DEE8", "#82A9CF", "#C2AC80", "#81B5A8", "#BBA1C9"]
MARKER_COLORS = ["#ED4A54", "#D8DEE8", "#82A9CF", "#C2AC80", "#81B5A8", "#BBA1C9"]

APP_TITLE = "CNL | Record Viewer"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Match complete ch1–ch12 identifiers, optionally followed by a suffix such
# as _raw / _eu. Do not match ch13, ch100, or names containing an embedded ch1.
_TRIM_LABEL_PATTERN = re.compile(r"\bch(1[0-2]|[1-9])(?=\b|_)")


def display_channel_labels(text: str) -> str:
    """Format visible text only; never use this output as a database key.

    Keep suffixes/units visible so stored and raw variants remain distinct.
    main_magnet, centering_beam, and all other variable names stay unchanged.
    """
    return _TRIM_LABEL_PATTERN.sub(r"TC\1", text)


def load_logo_pixmap(size=76):
    """Load the user's logo from a portable path; no hard-coded machine paths."""
    candidates = (
        os.environ.get("CNL_VIEWER_LOGO", ""),
        os.path.join(SCRIPT_DIR, str(project_file('cnl_logo.png'))),
        os.path.join(SCRIPT_DIR, "assets", str(project_file('cnl_logo.png'))),
    )
    for path in candidates:
        if path and os.path.isfile(path):
            pixmap = QtGui.QPixmap(path)
            if not pixmap.isNull():
                # Retain extra pixels for high-DPI Windows displays.
                result = pixmap.scaled(
                    size * 2, size * 2,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
                result.setDevicePixelRatio(2.0)
                return result
    return None


def apply_shadow(widget, blur=28, y_offset=7):
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QtGui.QColor(0, 0, 0, 180))
    widget.setGraphicsEffect(effect)


def viewer_stylesheet():
    """Scoped to this widget, not QApplication or the rest of the controller."""
    return f"""
        QWidget {{
            color: {NEON_TEXT};
            font-family: "Segoe UI", "Arial", sans-serif;
            font-size: {UI_FONT_SIZE}px;
        }}
        QWidget#CNLRecordViewer {{ background-color: {NEON_BG}; }}
        QFrame#HeaderCard, QFrame#ControlCard, QFrame#SideCard, QFrame#PlotCard {{
            background-color: {NEON_PANEL};
            border: 1px solid #343941;
            border-radius: 14px;
        }}
        QFrame#HeaderCard {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1D2025, stop:1 #131518);
        }}
        QLabel {{ background: transparent; border: none; }}
        QLabel#AppTitle {{ font-size: 23px; font-weight: 600; }}
        QLabel#AppSubtitle {{ color: {NEON_DIM}; font-size: 11px; }}
        QLabel#SectionLabel {{ color: {NEON_DIM}; font-size: 10px; font-weight: 600; }}
        QLabel#ViewerBadge {{
            color: #F1B6BA; background: #342126;
            border: 1px solid #663138; border-radius: 11px;
            padding: 5px 13px; font-size: 10px; font-weight: 600;
        }}
        QLabel#StatusLabel {{
            color: {NEON_DIM}; background: #111317;
            border: 1px solid #282D34; border-radius: 9px;
            padding: 7px 11px;
        }}
        QListWidget {{
            background: #121418; border: 1px solid #30353D;
            border-radius: 10px; padding: 5px; outline: none;
            color: {NEON_TEXT}; font-size: 12px;
        }}
        QListWidget::item {{
            padding: 7px 8px; margin: 1px 0px;
            border: 1px solid transparent; border-radius: 6px;
        }}
        QListWidget::item:hover {{ background: #242830; }}
        QListWidget::item:selected {{
            color: #FFFFFF; background: #3A242B; border: 1px solid #87414B;
        }}
        QPushButton {{
            background: #272B32; color: {NEON_TEXT};
            border: 1px solid #3E444E; border-radius: 9px;
            padding: 6px 11px; min-height: 19px; font-weight: 500;
        }}
        QPushButton:hover {{ background: #333943; border-color: #646D7C; }}
        QPushButton:pressed {{ background: #1E2228; }}
        QPushButton:focus {{ border-color: {ACCENT}; }}
        QPushButton:checked {{ background: #46282E; border-color: {ACCENT}; }}
        QPushButton:disabled {{ color: #727985; background: #1A1D22; }}
        QPushButton#PrimaryButton {{
            background: {ACCENT}; border-color: {ACCENT}; color: white;
        }}
        QPushButton#PrimaryButton:hover {{ background: #F24D57; border-color: #F24D57; }}
        QPushButton#PrimaryButton:pressed {{ background: #C42C38; }}
        QPushButton#PlotTool {{ padding: 2px 5px; min-height: 20px; border-radius: 7px; }}
        QLineEdit, QDateTimeEdit, QSpinBox {{
            background: #101216; color: {NEON_TEXT};
            border: 1px solid #3D444E; border-radius: 8px;
            padding: 6px 9px; min-height: 20px;
            selection-background-color: #66333D;
        }}
        QLineEdit:focus, QDateTimeEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
        QCalendarWidget QWidget {{ background: #191C21; color: {NEON_TEXT}; }}
        QCalendarWidget QAbstractItemView {{
            background: #191C21; color: {NEON_TEXT};
            selection-background-color: #71343F;
        }}
        QScrollBar:vertical {{ background: #13161A; width: 10px; margin: 3px 1px; }}
        QScrollBar::handle:vertical {{ background: #454D59; min-height: 25px; border-radius: 4px; }}
        QScrollBar::handle:vertical:hover {{ background: #687483; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        QScrollBar:horizontal {{ background: #13161A; height: 10px; margin: 1px 3px; }}
        QScrollBar::handle:horizontal {{ background: #454D59; min-width: 25px; border-radius: 4px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
        QMenu {{ background: #181A1E; color: {NEON_TEXT}; border: 1px solid #424852; padding: 5px; }}
        QMenu::item {{ padding: 7px 15px; border-radius: 5px; }}
        QMenu::item:selected {{ background: #46282E; }}
        QMenu::separator {{ height: 1px; background: #424852; margin: 4px 8px; }}
        QDialog#ExportDialog {{ background: #181A1E; }}
        QCheckBox {{ color: {NEON_TEXT}; spacing: 8px; min-height: 22px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; }}
        QMessageBox {{ background: #181A1E; }}
        QMessageBox QTextEdit {{ background: #101216; color: {NEON_TEXT}; }}
        QToolTip {{ color: #ECEFF3; background: #252A32; border: 1px solid #596270; padding: 5px; }}
    """


def neonize_axes(ax):
    ax.set_facecolor(NEON_PANEL)
    ax.tick_params(colors=NEON_TEXT, labelsize=AXIS_TICK_SIZE)
    ax.xaxis.label.set_color(NEON_TEXT)
    ax.yaxis.label.set_color(NEON_TEXT)
    ax.xaxis.label.set_size(AXIS_LABEL_SIZE)
    ax.yaxis.label.set_size(AXIS_LABEL_SIZE)
    for s in ax.spines.values():
        s.set_color(NEON_SPINE); s.set_linewidth(0.8)
    ax.grid(True, color=NEON_GRID, alpha=0.6, linewidth=0.6)
    ax.set_axisbelow(True)

def glow_line(ax, x, y, color, label=None):
    # Restrained trace styling instead of the original multi-layer neon glow.
    ax.plot(x, y, color=color, linewidth=1.5, label=label)
    ax.scatter(x, y, s=5, color=color, edgecolors="none", alpha=0.55, zorder=3)

# ------------ Drag source (columns) ------------
class ColumnList(QtWidgets.QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QtWidgets.QListWidget.SelectionMode.ExtendedSelection)
        self._drag_start = None

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self._drag_start = e.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if not (e.buttons() & QtCore.Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(e)
        if (self._drag_start is None or
            (e.pos() - self._drag_start).manhattanLength() < QtWidgets.QApplication.startDragDistance()):
            return
        items = self.selectedItems()
        if not items:
            item = self.itemAt(self._drag_start)
            if item: items = [item]
        if not items:
            return
        mime_data = QtCore.QMimeData()
        # Display labels can differ from the real SQLite column names.
        # Drag the stored keys, not the visible TC labels, so both SELECTs
        # and calibration lookup still receive ch1, ch2, ... unchanged.
        mime_data.setText("\n".join(
            it.data(QtCore.Qt.ItemDataRole.UserRole) or it.text() for it in items))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(QtCore.Qt.DropAction.CopyAction)

# ------------ QWidget wrapper around the MPL canvas ------------
class DroppableCanvas(QtWidgets.QWidget):
    dropped = QtCore.pyqtSignal(list)

    def __init__(self, fig: Figure, parent=None):
        super().__init__(parent)
        self.fig = fig
        self._canvas = FigureCanvas(fig)
        self.setAcceptDrops(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self._canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

        # Enable mouse wheel events
        self._canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self._canvas.setCursor(QtCore.Qt.CursorShape.CrossCursor)

        # Connect wheel event
        self._canvas.mpl_connect('scroll_event', self._on_mouse_wheel)

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasText() else e.ignore()
    def dragMoveEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasText() else e.ignore()
    def dropEvent(self, e):
        if e.mimeData().hasText():
            names = [n.strip() for n in e.mimeData().text().splitlines() if n.strip()]
            if names:
                self.dropped.emit(names); e.acceptProposedAction()
        else:
            e.ignore()

    def _on_mouse_wheel(self, event):
        parent = self.parent()
        while parent and not isinstance(parent, PlotPane):
            parent = parent.parent()
        if parent and hasattr(parent, '_on_mouse_wheel'):
            parent._on_mouse_wheel(event)

# ---------------- Plot pane  ----------------
class PlotPane(QtWidgets.QFrame):
    changed = QtCore.pyqtSignal()

    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setObjectName("PlotCard")
        apply_shadow(self, blur=24, y_offset=6)
        self.series: List[str] = []
        self._zoom_stack = []
        self._initial_ylim = None
        self._y_axis_scale = 'linear'
        self._use_scientific_notation = False
        self._format_index_time = False
        self._display_unit = ''
        self._trace_snapshots = []
        self._plot_warnings = []
        self._marker_specs = []
        self._positioning_markers = False

        self.title_lbl = QtWidgets.QLabel(title)
        self.title_lbl.setStyleSheet(f"font-weight:600; color:{NEON_TEXT}; font-size: {PLOT_TITLE_SIZE}px;")

        # Zoom buttons
        zoom_layout = QtWidgets.QHBoxLayout()

        self.zoom_in_btn = QtWidgets.QPushButton("+")
        self.zoom_in_btn.setMaximumWidth(30)
        self.zoom_in_btn.setToolTip("Zoom in (Y-axis)")
        self.zoom_in_btn.clicked.connect(lambda: self._zoom_y_axis(ZOOM_FACTOR))

        self.zoom_out_btn = QtWidgets.QPushButton("-")
        self.zoom_out_btn.setMaximumWidth(30)
        self.zoom_out_btn.setToolTip("Zoom out (Y-axis)")
        self.zoom_out_btn.clicked.connect(lambda: self._zoom_y_axis(1/ZOOM_FACTOR))

        self.reset_zoom_btn = QtWidgets.QPushButton("⟲")
        self.reset_zoom_btn.setMaximumWidth(30)
        self.reset_zoom_btn.setToolTip("Reset zoom")
        self.reset_zoom_btn.clicked.connect(self._reset_zoom)

        self.log_scale_btn = QtWidgets.QPushButton("Log")
        self.log_scale_btn.setMaximumWidth(40)
        self.log_scale_btn.setCheckable(True)
        self.log_scale_btn.setToolTip("Toggle logarithmic Y scale")
        self.log_scale_btn.clicked.connect(self._toggle_log_scale)

        self.sci_notation_btn = QtWidgets.QPushButton("Sci")
        self.sci_notation_btn.setMaximumWidth(40)
        self.sci_notation_btn.setCheckable(True)
        self.sci_notation_btn.setToolTip("Toggle scientific notation")
        self.sci_notation_btn.clicked.connect(self._toggle_scientific_notation)

        zoom_layout.addWidget(self.zoom_in_btn)
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.reset_zoom_btn)
        zoom_layout.addWidget(self.log_scale_btn)
        zoom_layout.addWidget(self.sci_notation_btn)
        zoom_layout.addStretch()

        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.clear_series)
        clear.setMaximumWidth(64)
        for button in (self.zoom_in_btn, self.zoom_out_btn, self.reset_zoom_btn,
                       self.log_scale_btn, self.sci_notation_btn, clear):
            button.setObjectName("PlotTool")
            button.setMinimumHeight(24)
        self.zoom_in_btn.setFixedWidth(30)
        self.zoom_out_btn.setFixedWidth(30)
        self.reset_zoom_btn.setFixedWidth(30)
        self.log_scale_btn.setFixedWidth(40)
        self.sci_notation_btn.setFixedWidth(40)
        zoom_layout.setSpacing(5)

        head = QtWidgets.QHBoxLayout()
        head.addWidget(self.title_lbl)
        head.addStretch()
        head.addLayout(zoom_layout)
        head.addWidget(clear)

        self.fig = Figure(figsize=(5, 2), constrained_layout=True)
        self.fig.set_facecolor(NEON_PANEL)
        self.canvas = DroppableCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        neonize_axes(self.ax)
        self.ax.set_xlabel("Time")

        self._create_crosshair()

        # Markers
        self.marker_lines = []

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 8)
        lay.setSpacing(3)
        lay.addLayout(head)
        lay.addWidget(self.canvas, 1)

        self.canvas.dropped.connect(self._apply_drop)
        self.canvas._canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.canvas._canvas.mpl_connect('button_press_event', self._on_mouse_click)
        self.canvas._canvas.mpl_connect('draw_event', self._on_plot_draw)

        self._update_yaxis_formatter()

    def _create_crosshair(self):
        # Crosshair
        self.cursor_vline = self.ax.axvline(np.nan, color=ACCENT, alpha=0.8, lw=1.5, visible=False)
        self.cursor_hline = self.ax.axhline(np.nan, color=ACCENT, alpha=0.8, lw=1.5, visible=False)
        self.cursor_text  = self.ax.text(
            0.02, 0.98, "", transform=self.ax.transAxes,
            fontsize=9, color=NEON_TEXT, va='top', ha='left',
            bbox=dict(boxstyle="round,pad=0.3", facecolor=NEON_PANEL, edgecolor=NEON_SPINE, alpha=0.9),
            visible=False
        )


    def _zoom_y_axis(self, factor, center=None):
        if not self.ax.has_data():
            return
        ylim = self.ax.get_ylim()
        if center is None:
            center = (ylim[0] + ylim[1]) / 2

        current_range = ylim[1] - ylim[0]
        new_range = current_range / factor
        if new_range < MIN_Y_RANGE:
            return

        half_range = new_range / 2
        new_ylim = (center - half_range, center + half_range)
        self.ax.set_ylim(new_ylim)
        self._update_yaxis_formatter()
        self.canvas._canvas.draw_idle()
        self._zoom_stack.append(ylim)

    def _reset_zoom(self):
        if self._initial_ylim:
            self.ax.set_ylim(self._initial_ylim)
        else:
            self.ax.relim()
            self.ax.autoscale_view(scaley=True)
        self._update_yaxis_formatter()
        self._zoom_stack.clear()
        self.canvas._canvas.draw_idle()

    def _toggle_log_scale(self):
        if self._y_axis_scale == 'linear':
            self.ax.set_yscale('log')
            self._y_axis_scale = 'log'
            self.log_scale_btn.setChecked(True)
            self.log_scale_btn.setText("Lin")
        else:
            self.ax.set_yscale('linear')
            self._y_axis_scale = 'linear'
            self.log_scale_btn.setChecked(False)
            self.log_scale_btn.setText("Log")
        self._update_yaxis_formatter()
        self.canvas._canvas.draw_idle()

    def _toggle_scientific_notation(self):
        self._use_scientific_notation = not self._use_scientific_notation
        self.sci_notation_btn.setChecked(self._use_scientific_notation)
        self._update_yaxis_formatter()
        self.canvas._canvas.draw_idle()

    def _update_yaxis_formatter(self):
        if self._use_scientific_notation:
            formatter = ScalarFormatter(useMathText=True)
            formatter.set_scientific(True)
            formatter.set_powerlimits((-3, 4))
            self.ax.yaxis.set_major_formatter(formatter)
        else:
            self.ax.yaxis.set_major_formatter(ScalarFormatter())
        self.ax.yaxis.set_tick_params(labelsize=AXIS_TICK_SIZE)
        self.ax.yaxis.label.set_color(NEON_TEXT)
        self.ax.yaxis.label.set_size(AXIS_LABEL_SIZE)

    def _on_mouse_wheel(self, event):
        if event.inaxes != self.ax:
            return
        if event.button == 'up':
            self._zoom_y_axis(ZOOM_FACTOR, center=event.ydata)
        elif event.button == 'down':
            self._zoom_y_axis(1/ZOOM_FACTOR, center=event.ydata)

    def _on_mouse_move(self, event):
        if event.inaxes != self.ax:
            self.cursor_vline.set_visible(False)
            self.cursor_hline.set_visible(False)
            self.cursor_text.set_visible(False)
            self.canvas._canvas.draw_idle()
            return

        x, y = event.xdata, event.ydata
        self.cursor_vline.set_xdata([x, x])
        self.cursor_hline.set_ydata([y, y])
        self.cursor_vline.set_visible(True)
        self.cursor_hline.set_visible(True)

        parent = self.parent()
        while parent and not hasattr(parent, '_format_marker_time'):
            parent = parent.parent()

        if x is not None and y is not None and parent:
            time_str = parent._format_marker_time(x)
            self.cursor_text.set_text(f"{time_str}\nValue: {y:.6g} {self._display_unit}".rstrip())
            self.cursor_text.set_visible(True)
        else:
            self.cursor_text.set_visible(False)

        self.canvas._canvas.draw_idle()

        parent = self.parent()
        while parent and not hasattr(parent, '_update_crosshair'):
            parent = parent.parent()
        if parent and x is not None:
            parent._update_crosshair(x, self)

    def _on_mouse_click(self, event):
        if event.inaxes == self.ax and event.button == 1:
            x = event.xdata
            if x is None:
                return
            parent = self.parent()
            while parent and not hasattr(parent, 'marker_mode_active'):
                parent = parent.parent()
            if parent and parent.marker_mode_active:
                parent._add_marker(x)
            else:
                parent = self.parent()
                while parent and not hasattr(parent, '_sync_crosshair_click'):
                    parent = parent.parent()
                if parent:
                    parent._sync_crosshair_click(x)

    def _on_plot_draw(self, event):
        # Reposition only the label boxes after zoom / resize, never the markers.
        if self._positioning_markers or not self.marker_lines:
            return
        self._positioning_markers = True
        try:
            if position_marker_labels(self.ax, self.marker_lines, event.renderer):
                self.canvas._canvas.draw_idle()
        finally:
            self._positioning_markers = False

    def add_marker(self, x, marker_id, color, label_override=None, time_text=None):
        # Keep label_override compatibility; stored labels are regenerated after
        # a scaling toggle rather than reusing stale values from the old units.
        if time_text is None:
            parent = self.parent()
            while parent and not hasattr(parent, '_format_marker_time'):
                parent = parent.parent()
            time_text = parent._format_marker_time(x) if parent else f"Sample {x:g}"
            if label_override and len(label_override.splitlines()) > 1:
                time_text = label_override.splitlines()[1]
        marker = MarkerSnapshot(float(x), int(marker_id), color, time_text)
        self._marker_specs.append(marker)
        self.marker_lines.append(add_marker_artists(
            self.ax, marker, marker_text(marker, self._trace_snapshots), background=NEON_PANEL))
        self.canvas._canvas.draw_idle()

    def clear_markers(self):
        for line, text in self.marker_lines:
            if line.axes is self.ax:
                line.remove()
            if text.axes is self.ax:
                text.remove()
        self.marker_lines.clear()
        self._marker_specs.clear()
        self.canvas._canvas.draw_idle()

    def report_snapshot(self):
        return PlotSnapshot(
            title=self.title_lbl.text(),
            traces=[TraceSnapshot(t.name, t.label, t.unit, t.x.copy(), t.y.copy(), t.color, t.warning)
                    for t in self._trace_snapshots],
            markers=list(self._marker_specs), xlim=tuple(self.ax.get_xlim()),
            ylim=tuple(self.ax.get_ylim()), ylabel=self.ax.get_ylabel(),
            yscale=self.ax.get_yscale(), scientific=self._use_scientific_notation,
            warnings=list(self._plot_warnings))

    def update_crosshair(self, x):
        if x is None:
            return
        self.cursor_vline.set_xdata([x, x])
        self.cursor_vline.set_visible(True)
        self.canvas._canvas.draw_idle()

    def _apply_drop(self, names: list):
        changed = False
        for n in names:
            if n not in self.series:
                self.series.append(n)
                changed = True
        if changed:
            self.changed.emit()
            parent = self.parent()
            while parent and not hasattr(parent, 'run_query'):
                parent = parent.parent()
            if parent:
                parent.run_query()

    def clear_series(self):
        if not self.series:
            return
        self.series.clear()
        self.changed.emit()
        self.ax.clear()
        neonize_axes(self.ax)
        self.marker_lines.clear()
        self._marker_specs.clear()
        self._trace_snapshots.clear()
        self._plot_warnings.clear()
        self._create_crosshair()
        self._zoom_stack.clear()
        self._initial_ylim = None
        self._y_axis_scale = 'linear'
        self.log_scale_btn.setChecked(False)
        self.log_scale_btn.setText("Log")
        self._use_scientific_notation = False
        self.sci_notation_btn.setChecked(False)
        self._update_yaxis_formatter()
        self.canvas._canvas.draw_idle()

    # IMPORTANT: resolver can only pick columns that EXIST in df (i.e., SELECTed)
    # We still keep resolver logic, but now SQLiteLogViewer.run_query() SELECTs expanded candidates.
    def _resolve_series_name(self, name: str, df: pd.DataFrame) -> str | None:
        legacy_map = {
            "inside_iron": "inside_iron_a",
            "inside_iron_raw": "inside_iron_a_raw",
            "inside_iron_eu": "inside_iron_a_eu",
            "outside_iron": "outside_iron_a",
            "outside_iron_raw": "outside_iron_a_raw",
            "outside_iron_eu": "outside_iron_a_eu",
        }
        if name in legacy_map:
            name = legacy_map[name]

        def has_data(col):
            return (col in df.columns) and pd.to_numeric(df[col], errors="coerce").notna().any()

        if has_data(name):
            return name

        cands = []
        if name.endswith("_raw"):
            base = name[:-4]; cands += [base + "_mbar", base + "_eu"]
        elif name.endswith("_mbar") or name.endswith("_eu"):
            base = name.rsplit("_", 1)[0]; cands += [base + "_raw"]
        else:
            cands += [name + "_mbar", name + "_eu", name + "_raw"]

        if name in ("beam_current", "beam"):
            cands = [name, "beam_ua", "beam_raw"] + cands
        if name in ("rf_kv", "rf"):
            cands = [name, "rf_power_kv", "rf_kv_raw"] + cands
        if name.startswith("vac") and len(name) > 3 and name[3].isdigit():
            base = name.split("_", 1)[0]
            cands = [base + "_mbar", base + "_raw", base] + cands
        if name in ("main_magnet", "centering_beam"):
            cands = [name + "_eu", name + "_raw", name] + cands

        for c in cands:
            if has_data(c):
                return c
        key = identify_column(name)[0]
        for c in sorted(related_columns(name)):
            if identify_column(c)[0] == key and has_data(c):
                return c
        return None

    def plot(self, times_num, df: pd.DataFrame, preserve_markers: bool = False):
        # On a units toggle the samples stay the same. Keep marker X positions,
        # but recalculate their Y placement in the new display units.
        saved_markers = list(self._marker_specs) if preserve_markers else []
        self._marker_specs.clear()
        self._trace_snapshots.clear()
        self._plot_warnings.clear()
        self.ax.clear()
        neonize_axes(self.ax)
        self.marker_lines.clear()
        self._create_crosshair()
        self._display_unit = ''
        parent = self.parent()
        while parent and not hasattr(parent, '_sync_xlim_from'):
            parent = parent.parent()
        if parent:
            self.ax.callbacks.connect("xlim_changed", partial(parent._sync_xlim_from, self.ax))

        self.ax.set_xlabel("Sample", color=NEON_TEXT)
        self.ax.xaxis.set_major_formatter(NullFormatter())
        self.ax.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True))
        if df is None or df.empty or times_num is None or len(times_num) == 0:
            self.canvas._canvas.draw_idle()
            return

        x = np.asarray(times_num)
        drew = False
        units, kinds, plot_warnings = set(), set(), []
        scatter_points = []
        for i, name in enumerate(self.series):
            col = self._resolve_series_name(name, df)
            if not col:
                label = display_channel_labels(name)
                plot_warnings.append(f"{label}: no numeric data")
                self._trace_snapshots.append(TraceSnapshot(
                    name, label, '', x.copy(), np.full(len(x), np.nan),
                    NEON_COLORS[i % len(NEON_COLORS)], 'No numeric data'))
                continue
            if parent and hasattr(parent, '_display_series'):
                display = parent._display_series(name, col, df)
                y, label = display.values, display.label
                if display.warning:
                    plot_warnings.append(display_channel_labels(f"{name}: {display.warning}"))
                kind, unit = display.kind, display.unit
            else:
                y = pd.to_numeric(df[col], errors="coerce").to_numpy()
                label, unit, kind = display_channel_labels(name), '', 'recorded'
            n = min(len(x), len(y))
            if not n:
                continue
            xx, yy = x[:n], y[:n]
            self._trace_snapshots.append(TraceSnapshot(
                name, label, unit, np.asarray(xx, dtype=float).copy(),
                np.asarray(yy, dtype=float).copy(), NEON_COLORS[i % len(NEON_COLORS)]))
            finite = np.isfinite(xx) & np.isfinite(yy)
            if not finite.any():
                continue
            kinds.add(kind)
            units.add(unit)
            # Preserve gaps for rows which cannot be converted. Do not connect
            # a valid beam point across a missing/invalid historical range.
            xx, yy = xx.copy(), yy.copy()
            yy[~finite] = np.nan
            color = NEON_COLORS[i % len(NEON_COLORS)]
            if finite.sum() == 1:
                scatter_points.append(np.column_stack((xx[finite], yy[finite])))
                self.ax.scatter(xx[finite], yy[finite], s=24, color=color,
                                edgecolors="none", alpha=0.95, label=label, zorder=3)
            else:
                glow_line(self.ax, xx, yy, color, label=label)
                self.ax.fill_between(xx, yy, alpha=0.035, color=color)
            drew = True

        if drew:
            leg = self.ax.legend(loc="upper right", fontsize=LEGEND_FONT_SIZE, framealpha=0.9)
            leg.get_frame().set_facecolor(NEON_PANEL)
            leg.get_frame().set_edgecolor(NEON_SPINE)
            for t in leg.get_texts():
                t.set_color(NEON_TEXT)
            self.ax.relim()
            # relim() does not include scatter collections. Include a lone valid
            # sample so partial/missing range metadata cannot hide that sample.
            for points in scatter_points:
                self.ax.update_datalim(points)
            self.ax.autoscale_view()
            self._initial_ylim = self.ax.get_ylim()

        scaled_mode = bool(parent and getattr(parent, '_scaling_enabled', False))
        if scaled_mode:
            if len(units) == 1 and '' not in units:
                self._display_unit = next(iter(units))
                axis_label = f"Value [{self._display_unit}]"
            elif len(units) > 1 or ('recorded' in kinds and len(kinds) > 1):
                axis_label = "Value (units in legend)"
            elif kinds == {'recorded'}:
                axis_label = "Recorded value"
            else:
                axis_label = "Scaled value"
            self.ax.set_ylabel(axis_label, color=NEON_TEXT)
        if plot_warnings:
            message = "\n".join(plot_warnings[:2])
            if len(plot_warnings) > 2:
                message += f"\n+ {len(plot_warnings)-2} more; see scaling details"
            self.ax.text(0.01, 0.97, message, transform=self.ax.transAxes, va="top", ha="left",
                         fontsize=8, color=NEON_DIM,
                         bbox=dict(facecolor=NEON_PANEL, edgecolor="none", alpha=0.9))
        self.ax.set_yscale(self._y_axis_scale)
        self._update_yaxis_formatter()
        self._plot_warnings = list(plot_warnings)
        for marker in saved_markers:
            self.add_marker(marker.x, marker.marker_id, marker.color, time_text=marker.time_text)
        self.canvas._canvas.draw_idle()

# ---------------- Main viewer widget ----------------
class SQLiteLogViewer(QtWidgets.QWidget):
    def __init__(self, db_path: str = None, parent=None):
        super().__init__(parent)
        self.setObjectName("CNLRecordViewer")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle(APP_TITLE)
        logo = load_logo_pixmap(96)
        if logo is not None:
            self.setWindowIcon(QtGui.QIcon(logo))
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        self._db_path = db_path or DEFAULT_DB
        self._loaded_db_path = self._db_path
        self._loaded_epoch = "unix"  # Epoch belonging to the cached displayed rows.
        self._last_pdf_dir = os.path.expanduser('~')

        # Marker system
        self.marker_mode_active = False
        self.marker_counter = 0
        self.current_markers = []

        self._db_cols = set()  # populated by load_columns()
        self._scaling_enabled = False
        self._calibration = ViewerCalibration(SCRIPT_DIR)
        self._scaling_results = {}

        self.setStyleSheet(viewer_stylesheet())

        self._create_top_bar()
        self._create_main_layout()
        self._connect_signals()

        self._last_df = pd.DataFrame()
        self._last_df_shown = pd.DataFrame()
        self._format_time_as_index = False
        self._current_crosshair_x = None
        # Open the interface even when the default DB is not beside the script.
        if os.path.isfile(self._db_path):
            self.load_columns(autofit_window=True)
        else:
            self.status.setText("Open a SQLite database using Browse DB to begin.")

    def _create_top_bar(self):
        self.db_edit = QtWidgets.QLineEdit(self._db_path)
        self.db_edit.setMinimumWidth(160)
        self.db_edit.setToolTip("Path to your existing SQLite database")

        self.browse = QtWidgets.QPushButton("Browse DB")
        self.browse.setObjectName("PrimaryButton")
        self.reload_cols = QtWidgets.QPushButton("Reload Columns")
        self.jump_latest_btn = QtWidgets.QPushButton("Jump to Latest")
        self.check_b = QtWidgets.QPushButton("Check Window")

        self.points_n = QtWidgets.QSpinBox()
        self.points_n.setRange(10, 20000)
        self.points_n.setValue(DEFAULT_MAX_POINTS)
        self.points_n.setSuffix(" pts")
        self.points_n.setMaximumWidth(110)
        self.points_n.setToolTip("Maximum displayed points; recorded data is not changed")

        self.marker_btn = QtWidgets.QPushButton("Marker Mode")
        self.marker_btn.setCheckable(True)
        self.marker_btn.setToolTip(
            "Enable, then click a plot to mark the nearest displayed sample.\n"
            "Every plot shows its own signal values in the current display units.")
        self.clear_markers_btn = QtWidgets.QPushButton("Clear")
        self.clear_markers_btn.setToolTip("Remove all markers from all three plots")
        self.export_pdf_btn = QtWidgets.QPushButton("Export PDF…")
        self.export_pdf_btn.setToolTip("Save selected plots, current scaling and zoom, with optional markers")

        self.scaling_btn = QtWidgets.QPushButton("Scaling factors: OFF")
        self.scaling_btn.setCheckable(True)
        self.scaling_btn.setToolTip(
            "Switch every plot between recorded values and calibrated engineering units.\n"
            "Display only: the SQLite records are never changed.\n"
            "Right-click to choose calibration files, reload them, or view details.")
        self.scaling_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.scaling_info = QtWidgets.QLabel("Recorded values · display only")
        self.scaling_info.setObjectName("AppSubtitle")
        self.scaling_info.setWordWrap(True)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QtWidgets.QLabel("Database"))
        top.addWidget(self.db_edit, 1)
        top.addWidget(self.browse)
        top.addWidget(self.reload_cols)
        self.top_bar_layout = top

    def _create_main_layout(self):
        # Branded header: your logo, not a Tesla logo.
        header = QtWidgets.QFrame()
        header.setObjectName("HeaderCard")
        apply_shadow(header)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(18, 8, 18, 8)
        header_layout.setSpacing(16)

        self.logo_label = QtWidgets.QLabel()
        self.logo_label.setFixedSize(78, 72)
        self.logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        logo = load_logo_pixmap(72)
        if logo is not None:
            self.logo_label.setPixmap(logo)
        else:
            self.logo_label.setText("CNL")
            self.logo_label.setToolTip("Place cnl_logo.png beside this Python file")
        header_layout.addWidget(self.logo_label)

        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(3)
        title = QtWidgets.QLabel("Record Viewer")
        title.setObjectName("AppTitle")
        subtitle = QtWidgets.QLabel("CROCKER NUCLEAR LABORATORY  /  UC DAVIS")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header_layout.addLayout(titles, 1)
        badge = QtWidgets.QLabel("SQLITE RECORDS")
        badge.setObjectName("ViewerBadge")
        header_layout.addWidget(badge)
        self.brand_header = header

        self.col_list = ColumnList()
        self.col_list.setMinimumWidth(190)
        self.col_list.setToolTip("Select one or more columns and drag them onto a plot")

        self.start_dt = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime().addSecs(-300))
        self.start_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_dt.setCalendarPopup(True)
        self.start_dt.setMinimumWidth(182)
        self.start_dt.setMaximumWidth(215)
        self.end_dt = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.end_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.end_dt.setCalendarPopup(True)
        self.end_dt.setMinimumWidth(182)
        self.end_dt.setMaximumWidth(215)

        time_row = QtWidgets.QHBoxLayout()
        time_row.setSpacing(8)
        time_row.addWidget(QtWidgets.QLabel("From"))
        time_row.addWidget(self.start_dt)
        time_row.addSpacing(8)
        time_row.addWidget(QtWidgets.QLabel("To"))
        time_row.addWidget(self.end_dt)
        time_row.addStretch(1)
        time_row.addWidget(self.jump_latest_btn)

        controls = QtWidgets.QFrame()
        controls.setObjectName("ControlCard")
        apply_shadow(controls, blur=22, y_offset=5)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(13, 11, 13, 11)
        controls_layout.setSpacing(9)
        controls_layout.addLayout(self.top_bar_layout)
        controls_layout.addLayout(time_row)

        self.p1, self.p2, self.p3 = PlotPane("Plot A"), PlotPane("Plot B"), PlotPane("Plot C")
        plots_layout = QtWidgets.QVBoxLayout()
        plots_layout.setSpacing(12)
        for pane in (self.p1, self.p2, self.p3):
            pane.canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                      QtWidgets.QSizePolicy.Policy.Expanding)
            pane.setMinimumHeight(145)
            plots_layout.addWidget(pane, 1)

        self.status = QtWidgets.QLabel("Drag variables to plots | Click to sync crosshair | Wheel to zoom Y")
        self.status.setObjectName("StatusLabel")
        self.status.setWordWrap(True)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("SideCard")
        sidebar.setMinimumWidth(226)
        sidebar.setMaximumWidth(300)
        apply_shadow(sidebar)
        left = QtWidgets.QVBoxLayout(sidebar)
        left.setContentsMargins(13, 15, 13, 13)
        left.setSpacing(9)
        section = QtWidgets.QLabel("RECORDED VARIABLES")
        section.setObjectName("SectionLabel")
        left.addWidget(section)
        hint = QtWidgets.QLabel("Drag columns onto a plot")
        hint.setObjectName("AppSubtitle")
        left.addWidget(hint)
        left.addWidget(self.col_list, 1)
        left.addSpacing(5)
        tools_label = QtWidgets.QLabel("DISPLAY TOOLS")
        tools_label.setObjectName("SectionLabel")
        left.addWidget(tools_label)
        points_row = QtWidgets.QHBoxLayout()
        points_row.addWidget(QtWidgets.QLabel("Max points"))
        points_row.addStretch(1)
        points_row.addWidget(self.points_n)
        left.addLayout(points_row)
        left.addWidget(self.scaling_btn)
        left.addWidget(self.scaling_info)
        markers_row = QtWidgets.QHBoxLayout()
        markers_row.addWidget(self.marker_btn, 1)
        markers_row.addWidget(self.clear_markers_btn)
        left.addLayout(markers_row)
        left.addWidget(self.check_b)
        left.addWidget(self.export_pdf_btn)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(controls)
        right.addLayout(plots_layout, 1)
        right.addWidget(self.status)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(14)
        content.addWidget(sidebar, COLUMNS_PANEL_WIDTH)
        content.addLayout(right, PLOTS_PANEL_WIDTH)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(14)
        root.addWidget(header)
        root.addLayout(content, 1)

    def _connect_signals(self):
        self.browse.clicked.connect(self._on_browse)
        self.reload_cols.clicked.connect(lambda: self.load_columns(autofit_window=False))
        self.jump_latest_btn.clicked.connect(lambda: self.jump_to_latest(300))
        self.check_b.clicked.connect(self._check_window)
        self.marker_btn.toggled.connect(self._on_marker_mode_toggled)
        self.clear_markers_btn.clicked.connect(self._clear_all_markers)
        self.export_pdf_btn.clicked.connect(self._export_pdf_dialog)
        self.scaling_btn.toggled.connect(self._on_scaling_toggled)
        self.scaling_btn.customContextMenuRequested.connect(self._scaling_menu)

        self.start_dt.dateTimeChanged.connect(self.run_query)
        self.end_dt.dateTimeChanged.connect(self.run_query)
        self.points_n.valueChanged.connect(self.run_query)

        for p in (self.p1, self.p2, self.p3):
            p.canvas.dropped.connect(partial(self._after_drop, pane=p))
            p.ax.callbacks.connect("xlim_changed", partial(self._sync_xlim_from, p.ax))

    # ---------- helpers ----------
    def _warn(self, title, msg):
        QtWidgets.QMessageBox.warning(self, title, msg)

    def _connect(self):
        path = self.db_edit.text().strip()
        if not os.path.exists(path):
            self._warn("Database", f"Path not found:\n{path}")
            raise FileNotFoundError(path)
        return sqlite3.connect(path)

    def _detect_db_epoch(self) -> str:
        # NO caching: correct even when switching DBs
        try:
            with self._connect() as c:
                mn = c.execute("SELECT MIN(timestamp) FROM channel_data").fetchone()[0]
            return "labview" if (mn is not None and float(mn) > 3e9) else "unix"
        except Exception:
            return "unix"

    def _latest_timestamp(self) -> float | None:
        try:
            with self._connect() as c:
                mx = c.execute("SELECT MAX(timestamp) FROM channel_data").fetchone()[0]
            return float(mx) if mx is not None else None
        except Exception:
            return None

    def _time_clause(self) -> str:
        epoch = self._detect_db_epoch()
        s = self.start_dt.dateTime().toSecsSinceEpoch()
        e = self.end_dt.dateTime().toSecsSinceEpoch()
        if epoch == "labview":
            s += LV_OFFSET
            e += LV_OFFSET
        return f"FROM channel_data WHERE timestamp BETWEEN {s:.6f} AND {e:.6f}"

    def _decimate_uniform(self, df: pd.DataFrame, n: int) -> pd.DataFrame:
        if df is None or df.empty or len(df) <= n:
            return df
        idx = np.linspace(0, len(df) - 1, n, dtype=int)
        return df.iloc[idx].reset_index(drop=True)

    # ----------- key FIX: expand needed columns for fallbacks -----------
    def _expand_needed_cols(self, series_names: List[str]) -> List[str]:
        """
        Build a SELECT column list that includes the dragged names AND their likely aliases,
        so PlotPane._resolve_series_name() can actually find real data.
        """
        legacy_map = {
            "inside_iron": "inside_iron_a",
            "inside_iron_raw": "inside_iron_a_raw",
            "inside_iron_eu": "inside_iron_a_eu",
            "outside_iron": "outside_iron_a",
            "outside_iron_raw": "outside_iron_a_raw",
            "outside_iron_eu": "outside_iron_a_eu",
        }

        needed = {"timestamp"}

        for n in series_names:
            base = legacy_map.get(n, n)
            needed.add(base)

            # add common variants
            if base.endswith("_raw"):
                b = base[:-4]
                needed.add(b + "_eu")
                needed.add(b + "_mbar")
            elif base.endswith("_eu") or base.endswith("_mbar"):
                b = base.rsplit("_", 1)[0]
                needed.add(b + "_raw")
            else:
                needed.add(base + "_raw")
                needed.add(base + "_eu")
                needed.add(base + "_mbar")

            # special cases
            if n in ("beam_current", "beam"):
                needed |= {"beam_current", "beam_ua", "beam_raw"}
            if n in ("rf_kv", "rf"):
                needed |= {"rf_kv", "rf_power_kv", "rf_kv_raw"}

        for name in series_names:
            needed.update(related_columns(name))

        # keep only columns that exist to avoid SQL errors
        if self._db_cols:
            needed = {c for c in needed if c in self._db_cols}

        # stable order (timestamp first)
        out = ["timestamp"] + sorted([c for c in needed if c != "timestamp"])
        return out

    # ---------- viewer-only engineering-unit display ----------
    def _display_series(self, name, source, df):
        result = self._calibration.display(name, source, df, self._scaling_enabled)
        # Conversion uses the original keys; only the resulting legend is relabeled.
        result.label = display_channel_labels(result.label)
        self._scaling_results[name] = result
        return result

    def _on_scaling_toggled(self, checked):
        self._scaling_enabled = bool(checked)
        for pane in (self.p1, self.p2, self.p3):
            pane._zoom_stack.clear()  # Old Y limits belong to different units.
            pane._initial_ylim = None
        self._render_current_data(preserve_view=True)

    def _scaling_details(self):
        lines = [
            "DISPLAY ONLY — SQLite records and calibration files are not modified.",
            "OFF: show the stored values. ON: use raw_to_eng calibration / recorded beam range.",
            "Raw/base columns follow the supplied logger's raw-packet schema.",
            "Explicit *_eu / *_mbar / beam_*a engineering columns are not rescaled.",
            "Raw records use the currently loaded calibration, not a historical calibration version.",
            "Beam values use a constant nA display unit. Unknown ranges are omitted, not guessed.",
            "", "Calibration files:",
        ]
        for kind, title in (("channels", "Channels"), ("beam", "Beam")):
            lines.append(f"{title}: {self._calibration.paths.get(kind, 'not loaded')}")
            if kind in self._calibration.errors:
                lines.append(f"  {self._calibration.errors[kind]}")
        active = set(self.p1.series + self.p2.series + self.p3.series)
        for name, result in self._scaling_results.items():
            if name in active:
                # Keep the note's original SQLite/calibration key for traceability.
                lines.append(f"\n{display_channel_labels(name)}: {result.note or result.kind}")
                if result.warning:
                    lines.append(f"  WARNING: {display_channel_labels(result.warning)}")
        return "\n".join(lines)

    def _update_scaling_ui(self):
        self.scaling_btn.setText("Scaling factors: ON" if self._scaling_enabled else "Scaling factors: OFF")
        warnings = sum(bool(result.warning) for result in self._scaling_results.values())
        caption = "Engineering units · display only" if self._scaling_enabled else "Recorded values · display only"
        if self._scaling_enabled and warnings:
            caption += f"\n{warnings} conversion warning(s) — right-click for details"
        self.scaling_info.setText(caption)
        self.scaling_info.setToolTip(self._scaling_details())

    def _scaling_menu(self, position):
        menu = QtWidgets.QMenu(self)
        choose_channels = menu.addAction("Choose calibration.json…")
        choose_beam = menu.addAction("Choose beam_cal.json…")
        menu.addSeparator()
        reload_action = menu.addAction("Reload calibration files")
        details_action = menu.addAction("Show scaling details")
        action = menu.exec(self.scaling_btn.mapToGlobal(position))
        if action in (choose_channels, choose_beam):
            kind = "channels" if action is choose_channels else "beam"
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select existing calibration file",
                self._calibration.paths.get(kind, SCRIPT_DIR), "Calibration (*.json);;All files (*)")
            if path:
                self._calibration.overrides[kind] = path
                self._render_current_data(preserve_view=True)
        elif action is reload_action:
            self._render_current_data(preserve_view=True)
        elif action is details_action:
            self._calibration.refresh(self.db_edit.text().strip())
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Scaling factors — display only")
            box.setText("Each channel uses its calibration. Beam samples use their recorded range.")
            box.setInformativeText("Open Details to see the loaded files and any conversion warnings.")
            box.setDetailedText(self._scaling_details())
            box.exec()

    def _render_current_data(self, preserve_view=False):
        self._calibration.refresh(self.db_edit.text().strip())
        self._scaling_results = {}
        df = self._last_df_shown
        panes = (self.p1, self.p2, self.p3)
        if not preserve_view:
            # A new query / decimation has new sample indices. Never attach an
            # old marker to a different timestamp. Unit toggles do preserve it.
            self.current_markers.clear()
        old_xlim = self.p1.ax.get_xlim() if preserve_view and not df.empty else None
        times_num = np.arange(len(df), dtype=float)
        for pane in panes:
            pane._format_index_time = True
            pane.plot(times_num, df, preserve_markers=preserve_view)
        if len(df):
            self._format_time_as_index = True
            xlim = old_xlim if old_xlim is not None else self.p1.ax.get_xlim()
            for pane in panes:
                pane.ax.set_xlim(xlim)
                pane.canvas._canvas.draw_idle()
            count = sum(len(pane.series) for pane in panes)
            mode = "Scaling ON" if self._scaling_enabled else "Recorded values"
            message = f"Showing {len(df)} of {len(self._last_df)} points for {count} plotted series | {mode}"
            if self._scaling_enabled and any(r.warning for r in self._scaling_results.values()):
                message += " | Conversion warnings: right-click Scaling factors for details"
            self.status.setText(message)
        else:
            self.status.setText("Drag variables to plots to load data. Scaling mode also applies to new plots.")
        self._update_scaling_ui()

    # ---------- marker mode toggles ----------
    def _on_marker_mode_toggled(self, checked):
        self.marker_mode_active = checked
        self.status.setText("Marker mode: Click on plots to place markers" if checked
                            else "Drag variables to plots to start | Click plot to sync crosshair | Wheel zoom Y")

    # ---------- index→window time helpers ----------
    def _format_marker_time(self, x_sample: float) -> str:
        if self._format_time_as_index and "timestamp" in self._last_df_shown.columns:
            sample_idx = int(round(x_sample))
            if 0 <= sample_idx < len(self._last_df_shown):
                ts = float(self._last_df_shown["timestamp"].iloc[sample_idx])
                unix_ts = ts - LV_OFFSET if self._loaded_epoch == "labview" else ts
                # Round the complete timestamp, so .9996 carries into the next
                # second instead of producing an impossible '.1000' suffix.
                dt_obj = datetime.fromtimestamp(round(unix_ts * 1000) / 1000)
                return dt_obj.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return f"Sample {int(round(x_sample))}"

    def _add_marker(self, x):
        index = snap_sample(x, len(self._last_df_shown))
        if index is None:
            self.status.setText("Load plotted data before adding a marker.")
            return
        self.marker_counter += 1
        marker_id = self.marker_counter
        color = MARKER_COLORS[(marker_id - 1) % len(MARKER_COLORS)]
        stamp = self._format_marker_time(index)
        self.current_markers.append(MarkerSnapshot(float(index), marker_id, color, stamp))
        for pane in (self.p1, self.p2, self.p3):
            pane.add_marker(index, marker_id, color, time_text=stamp)
        self.status.setText(f"Added M{marker_id} at {stamp} | Values at displayed sample {index}")

    def _clear_all_markers(self):
        for pane in (self.p1, self.p2, self.p3):
            pane.clear_markers()
        self.current_markers.clear()
        self.marker_counter = 0
        self.status.setText("All markers cleared.")

    # ---------- viewer-only PDF export ----------
    def _export_pdf_dialog(self):
        panes = (self.p1, self.p2, self.p3)
        if self._last_df_shown.empty or not any(p._trace_snapshots for p in panes):
            QtWidgets.QMessageBox.information(self, "Export PDF", "Load data onto at least one plot first.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setObjectName("ExportDialog")
        dialog.setWindowTitle("Export plots to PDF")
        dialog.setMinimumWidth(420)
        form = QtWidgets.QVBoxLayout(dialog)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)
        heading = QtWidgets.QLabel("Choose plots and report options")
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        form.addWidget(heading)
        choices = []
        for pane in panes:
            checkbox = QtWidgets.QCheckBox(pane.title_lbl.text())
            checkbox.setChecked(bool(pane._trace_snapshots))
            checkbox.setEnabled(bool(pane._trace_snapshots))
            choices.append(checkbox)
            form.addWidget(checkbox)
        include = QtWidgets.QCheckBox("Include markers and marker-value table")
        include.setChecked(any(p._marker_specs for p in panes))
        form.addWidget(include)
        paper = QtWidgets.QCheckBox("Print-friendly colors (white background)")
        paper.setChecked(True)
        form.addWidget(paper)
        separate = QtWidgets.QCheckBox("One plot per page")
        form.addWidget(separate)
        help_text = QtWidgets.QLabel(
            "Uses the displayed samples, current scaling and current zoom.\n"
            "Uncheck print-friendly colors to retain the dark plot theme.\n"
            "Only markers inside the exported X range are included.")
        help_text.setObjectName("AppSubtitle")
        help_text.setWordWrap(True)
        form.addWidget(help_text)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("Save PDF…")
        def update_selection():
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setEnabled(
                any(c.isChecked() and c.isEnabled() for c in choices))
        for checkbox in choices:
            checkbox.toggled.connect(lambda _checked: update_selection())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addWidget(buttons)
        update_selection()
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        selected = [p for p, c in zip(panes, choices) if c.isChecked() and c.isEnabled()]
        name = "CNL_record_plots_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pdf"
        save = QtWidgets.QFileDialog(self, "Save plot report", os.path.join(self._last_pdf_dir, name))
        save.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        save.setFileMode(QtWidgets.QFileDialog.FileMode.AnyFile)
        save.setNameFilter("PDF files (*.pdf)")
        save.setDefaultSuffix("pdf")  # Confirm overwrites with the final extension.
        if save.exec() != QtWidgets.QDialog.DialogCode.Accepted or not save.selectedFiles():
            return
        path = save.selectedFiles()[0]
        try:
            self.export_pdf_btn.setEnabled(False)
            pages = self._save_pdf(path, selected, include_markers=include.isChecked(),
                                   print_friendly=paper.isChecked(), one_plot_per_page=separate.isChecked())
        except Exception as exc:
            self._warn("PDF export failed", f"The report could not be saved.\n{exc}\n\n"
                       "Check the folder permissions, or close the destination PDF and try again.")
        else:
            self._last_pdf_dir = os.path.dirname(os.path.abspath(path))
            self.status.setText(f"Saved PDF ({pages} pages): {path}")
            QtWidgets.QMessageBox.information(self, "PDF saved", f"Saved {pages} page(s):\n{path}")
        finally:
            self.export_pdf_btn.setEnabled(True)

    def _save_pdf(self, path, panes, **options):
        snapshots = [pane.report_snapshot() for pane in panes]
        notes = []
        if self._scaling_enabled:
            notes.append("Scaled raw records use the currently loaded calibration, not a historical version. "
                         "Already-scaled engineering columns are not rescaled.")
            for kind, location in self._calibration.paths.items():
                notes.append(f"{kind} calibration: {location}")
        info = ReportInfo(
            database=self._loaded_db_path,
            start_time=self._format_marker_time(0),
            end_time=self._format_marker_time(len(self._last_df_shown) - 1),
            displayed_count=len(self._last_df_shown), loaded_count=len(self._last_df),
            scaling_mode="Scaling ON (engineering units)" if self._scaling_enabled else "Scaling OFF (recorded values)",
            notes=notes)
        logo = next((p for p in (
            os.environ.get("CNL_VIEWER_LOGO", ""), os.path.join(SCRIPT_DIR, str(project_file('cnl_logo.png'))),
            os.path.join(SCRIPT_DIR, "assets", str(project_file('cnl_logo.png')))) if p and os.path.isfile(p)), None)
        return export_pdf(path, snapshots, info, logo_path=logo, **options)

    def _update_crosshair(self, x, source_pane):
        self._current_crosshair_x = x
        for pane in (self.p1, self.p2, self.p3):
            if pane != source_pane:
                pane.update_crosshair(x)

    def _sync_crosshair_click(self, x):
        self._current_crosshair_x = x
        for pane in (self.p1, self.p2, self.p3):
            pane.update_crosshair(x)

    # ---------- load columns ----------
    def load_columns(self, autofit_window: bool = True):
        self.col_list.clear()
        try:
            with self._connect() as c:
                cols = pd.read_sql_query("PRAGMA table_info(channel_data)", c)
            names = list(cols["name"].astype(str))
        except Exception as e:
            self.status.setText(f"PRAGMA failed: {e}")
            names = []

        self._db_cols = set(names)

        for c in names:
            it = QtWidgets.QListWidgetItem(display_channel_labels(c))
            it.setData(QtCore.Qt.ItemDataRole.UserRole, c)
            it.setToolTip(f"SQLite column: {c}")
            it.setSelected(False)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.col_list.addItem(it)

        self.status.setText(f"Loaded {len(names)} columns. Drag variables to plots.")

        if autofit_window:
            self.jump_to_latest(300)

    def jump_to_latest(self, safety_window_s: int = 300):
        mx = self._latest_timestamp()
        if mx is None:
            return
        unix = mx - LV_OFFSET if self._detect_db_epoch() == "labview" else mx
        end_dt = datetime.fromtimestamp(unix)
        start_dt = end_dt - timedelta(seconds=int(safety_window_s))

        self.start_dt.blockSignals(True)
        self.end_dt.blockSignals(True)
        self.start_dt.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(start_dt.timestamp())))
        self.end_dt.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(end_dt.timestamp())))
        self.start_dt.blockSignals(False)
        self.end_dt.blockSignals(False)

        self.run_query()

    def _on_browse(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open SQLite DB",
            self.db_edit.text().strip(),
            "SQLite DB (*.db *.sqlite *.sqlite3);;All files (*)"
        )
        if p:
            self.db_edit.setText(p)
            self.load_columns(autofit_window=True)

    def _after_drop(self, names: list, pane: PlotPane):
        changed = False
        for n in names:
            if n not in pane.series:
                pane.series.append(n)
                changed = True
        if changed:
            pane.changed.emit()
            self.run_query()

    def _sync_xlim_from(self, source_ax, *args):
        try:
            xlim = source_ax.get_xlim()
        except Exception:
            return
        for ax in (self.p1.ax, self.p2.ax, self.p3.ax):
            if ax is source_ax:
                continue
            if ax.get_xlim() != xlim:
                ax.set_xlim(xlim)
                ax.figure.canvas.draw_idle()

    def _check_window(self):
        try:
            with self._connect() as c:
                epoch = self._detect_db_epoch()
                s = self.start_dt.dateTime().toSecsSinceEpoch()
                e = self.end_dt.dateTime().toSecsSinceEpoch()
                if epoch == "labview":
                    s += LV_OFFSET; e += LV_OFFSET
                n = c.execute("SELECT COUNT(*) FROM channel_data WHERE timestamp BETWEEN ? AND ?;", (s, e)).fetchone()[0]
                latest = c.execute("SELECT MAX(timestamp) FROM channel_data;").fetchone()[0]
            msg = [f"Rows in window: {n}", f"Epoch mode: {epoch}"]
            if latest is not None:
                unix = float(latest) - (LV_OFFSET if epoch == "labview" else 0)
                msg.append(f"Latest row: {datetime.fromtimestamp(unix)}")
            QtWidgets.QMessageBox.information(self, "Window check", "\n".join(msg))
        except Exception as e:
            self._warn("Window check", str(e))

    # ---------- main query ----------
    def run_query(self):
        all_series = self.p1.series + self.p2.series + self.p3.series
        if not all_series:
            self.status.setText("Drag variables to plots to load data")
            self._last_df = pd.DataFrame()
            self._last_df_shown = pd.DataFrame()
            return

        # KEY FIX: expand SELECT columns so fallback columns exist in df
        cols_q = self._expand_needed_cols(all_series)

        time_clause = self._time_clause()
        try:
            with self._connect() as c:
                sql = f"SELECT {', '.join(cols_q)} {time_clause} ORDER BY timestamp ASC"
                df_full = pd.read_sql_query(sql, c)
        except Exception as e:
            self._warn("Query failed", str(e))
            return

        self._loaded_db_path = os.path.abspath(self.db_edit.text().strip())
        self._loaded_epoch = self._detect_db_epoch()
        self._last_df = df_full
        self._last_df_shown = self._decimate_uniform(df_full, int(self.points_n.value())).copy()
        self._render_current_data()
        if df_full.empty:
            self.status.setText("No rows in window. Use 'Check' or 'Jump to Latest'.")

# ---- standalone launcher ----
def launch(db_path: str = None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = SQLiteLogViewer(db_path or (sys.argv[1] if len(sys.argv) > 1 else None))
    w.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(launch())
