# MagneticFieldControllerWindow.py
# Neon Bubble-styled Controller — Right-side Digit Panel + Mini Plot (Target vs Actual)
# - "Actual" comes from shared Monitoring list (current_values_ref) OR from data_queue packets (raw->eng via Scaler)
# - Current readout fixed: now shows TWO columns (Target + Actual), matching plot colors
# - Removed place-value headers (THOUSANDS/HUNDREDS/...) and removed quick adjust buttons (+10/+1/...)
# - Split moved closer to center (55/45) and right panel can expand (no hard max width)

from typing import List
import time
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGraphicsDropShadowEffect, QSizePolicy, QScrollArea
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QRadialGradient, QFont,
    QLinearGradient, QBrush
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal

import pyqtgraph as pg
pg.setConfigOptions(antialias=True)

from scaling import get_scaler, TRIM_KEYS


# --- Monitoring-style colors (same palette idea) ---
HSV_COLORS = [
    "#FF0000", "#FF7F00", "#FFFF00", "#00FF00",
    "#00FFFF", "#0000FF", "#8B00FF", "#FF00FF",
    "#FF0040", "#FF8000"
]

def neon_color(i: int) -> QColor:
    return QColor(HSV_COLORS[i % len(HSV_COLORS)])

def style_plot_cyberpunk(plot: pg.PlotWidget, title: str):
    g = QLinearGradient(0, 0, 0, 1)
    g.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
    g.setColorAt(0.0, QColor(8, 12, 26))
    g.setColorAt(1.0, QColor(22, 10, 35))
    plot.setBackground(QBrush(g))
    plot.setTitle(title, **{'color': '#BFEFFF', 'size': '9pt'})

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
    # glow behind
    glow = plot.plot(pen=pg.mkPen(color.lighter(170), width=6))

    # main line
    line = plot.plot(
        pen=pg.mkPen(color, width=2),
        name=name,
        symbol=None,
    )

    # under-fill gradient
    fill_grad = QLinearGradient(0, 0, 0, 1)
    fill_grad.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
    fill_grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 70))
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





CHANNEL_NAMES = [
    "TC1", "TC2", "TC3", "TC4", "TC5", "TC6", "TC7",
    "TC8", "TC9", "TC10", "TC11", "TC12", "Main Magnet", "Centering Beam"
]
NUM_CHANNELS = len(CHANNEL_NAMES)

NUM_DIGITS = 6
DIGIT_FACTORS = [1000, 100, 10, 1, 0.1, 0.01]

# ==== Palette & fonts ====
PALETTES = [
    {"rim": QColor(72, 205, 255),  "rim2": QColor(140, 235, 255),
     "tint": QColor(30, 110, 150), "label": QColor(210, 240, 255)},
    {"rim": QColor(120, 150, 255), "rim2": QColor(170, 200, 255),
     "tint": QColor(35, 45, 110),  "label": QColor(220, 230, 255)},
    {"rim": QColor(0, 230, 200),   "rim2": QColor(140, 255, 230),
     "tint": QColor(10, 90, 80),   "label": QColor(200, 255, 245)},
    {"rim": QColor(200, 140, 255), "rim2": QColor(230, 190, 255),
     "tint": QColor(65, 40, 100),  "label": QColor(235, 225, 255)},
]
BUBBLE_ALPHA = 0.85

LABEL_FONT   = QFont("Orbitron, Exo 2, Rajdhani, Segoe UI Semibold, Arial", 18, QFont.Weight.DemiBold)
READOUT_FONT = QFont("Consolas, JetBrains Mono, Roboto Mono", 18, QFont.Weight.Medium)

TARGET_CSS = (
    "color: #7CFFB2; background: rgba(0,30,35,160); "
    "border: 1px solid #00D0FF; border-radius: 4px; padding: 3px;"
)
TARGET_CSS_SELECTED = (
    "color: #E6FFF5; background: rgba(0,85,105,245); "
    "border: 2px solid #46F5FF; border-radius: 5px; padding: 4px;"
)


def qmix(a: QColor, b: QColor, t: float, alpha: int | None = None) -> QColor:
    t = max(0.0, min(1.0, t))
    q = QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
    )
    if alpha is not None:
        q.setAlpha(alpha)
    return q


class BubbleToggle(QPushButton):
    """Compact but readable bubble-style toggle."""
    def __init__(self, diameter_px: int = 40, palette_idx: int = 0, filled_when_checked=True, tooltip: str = ""):
        super().__init__("")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self._hover = False
        self._pulse = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

        self._diam = max(32, int(diameter_px))
        self._pal = PALETTES[palette_idx % len(PALETTES)]
        self._filled_when_checked = filled_when_checked

        if tooltip:
            self.setToolTip(tooltip)
        self.setFixedSize(self._diam, self._diam)

    def sizeHint(self):
        return self.size()

    def enterEvent(self, e):
        self._hover = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        super().leaveEvent(e)

    def _tick(self):
        target = 1.0 if (self._hover or self.isChecked()) else 0.0
        self._pulse += (target - self._pulse) * 0.22
        if abs(target - self._pulse) > 0.01:
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform |
            QPainter.RenderHint.TextAntialiasing
        )

        w, h = self.width(), self.height()
        rect = QRectF(1, 1, w - 2, h - 2)
        cx, cy = rect.center().x(), rect.center().y()
        r = min(rect.width(), rect.height()) * 0.5

        base_dark = QColor(12, 18, 26)
        base_mid  = QColor(22, 30, 42)
        base_ctr  = QColor(30, 40, 54)
        tint      = self._pal["tint"]

        depth = QRadialGradient(QPointF(cx, cy), r)
        depth.setColorAt(1.00, qmix(base_dark, tint, 0.35, int(220 * BUBBLE_ALPHA)))
        depth.setColorAt(0.55, qmix(base_mid,  tint, 0.30, int(170 * BUBBLE_ALPHA)))
        depth.setColorAt(0.00, qmix(base_ctr,  tint, 0.25, int(120 * BUBBLE_ALPHA)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(depth)
        p.drawEllipse(QPointF(cx, cy), r, r)

        rim = QRadialGradient(QPointF(cx, cy), r)
        c1 = QColor(self._pal["rim2"]); c1.setAlpha(int(220 * BUBBLE_ALPHA))
        c2 = QColor(self._pal["rim"]);  c2.setAlpha(int(240 * BUBBLE_ALPHA))
        rim.setColorAt(0.80, c2)
        rim.setColorAt(1.00, c1)
        width = 1.8 + 0.9 * self._pulse
        p.setPen(QPen(rim, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r - 0.8, r - 0.8)

        if self.isChecked() and self._filled_when_checked:
            bright = QColor(self._pal["rim"]); bright.setAlpha(160)
            g = QRadialGradient(QPointF(cx, cy), r * 0.9)
            g.setColorAt(0.0, QColor(255, 255, 255, 110))
            g.setColorAt(0.4, bright)
            g.setColorAt(1.0, QColor(bright.red(), bright.green(), bright.blue(), 0))
            p.setBrush(g)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r * 0.86, r * 0.86)


class ClickableLabel(QLabel):
    """Label that emits clicked(index) when pressed."""
    clicked = pyqtSignal(int)

    def __init__(self, index: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._index = index

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(e)


class MagneticFieldControllerWindow(QWidget):
    def __init__(self, control_queue, target_values, plot_config,
                 current_values_ref=None, data_queue=None):
        super().__init__()

        self.setWindowTitle("Magnetic Field Controller")
        self.setGeometry(100, 100, 1400, 700)

        self.setStyleSheet("""
            background-color: #0a1015;
            color: #ffffff;
        """)

        self.control_queue = control_queue
        self.target_values = target_values
        self.plot_config = plot_config or {}

        # Preferred: shared live list from MonitoringWindow (already in ENG units)
        self.current_values_ref = current_values_ref

        # Fallback: packet queue (dict packets with "channels" raw values) -> scaled using Scaler
        self.data_queue = data_queue

        self.scaler = get_scaler()
        self.actual_values = [0.0] * NUM_CHANNELS
        self._last_sample = None
        self.last_data_time = 0.0
        self.HOLD_SECONDS = 5.0

        self.selected_index = 0
        self.controls_locked = False

        # Poll incoming packets so controller can show "Actual" even if Monitoring isn't open
        self._rx_timer = QTimer(self)
        self._rx_timer.timeout.connect(self._poll_actual_from_queue)
        self._rx_timer.start(50)  # 20 Hz

        # Main horizontal layout: left side (channels) + right side (digit panel)
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(10)

        # ========= LEFT SIDE: Channel controls =========
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content_widget = QWidget()
        self.left_content_layout = QVBoxLayout(content_widget)
        self.left_content_layout.setContentsMargins(4, 4, 4, 4)
        self.left_content_layout.setSpacing(8)

        self.labels: List[ClickableLabel] = []
        self.plot_buttons: List[BubbleToggle] = []
        self.on_buttons: List[QPushButton] = []
        self.en_buttons: List[QPushButton] = []

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.grid.setContentsMargins(4, 4, 4, 4)

        self.grid.setColumnMinimumWidth(0, 50)    # Plot column
        self.grid.setColumnMinimumWidth(1, 100)   # Target column
        self.grid.setColumnMinimumWidth(2, 120)   # Channel column
        self.grid.setColumnMinimumWidth(3, 90)    # Power column
        self.grid.setColumnMinimumWidth(4, 90)    # Enable column
        self.grid.setRowMinimumHeight(0, 40)

        self._add_header("Plot", 0)
        self._add_header("Target", 1)
        self._add_header("Channel", 2)

        self._add_group_header_with_button(
            title="Power", col=3,
            btn_attr="all_power_btn", btn_text_on="All ON",
            btn_tooltip="Turn ON/OFF all power buttons",
            clicked=self.toggle_power_all
        )
        self._add_group_header_with_button(
            title="Enable", col=4,
            btn_attr="all_enable_btn", btn_text_on="All EN",
            btn_tooltip="Toggle all EN/UN buttons",
            clicked=self.toggle_enable_all
        )

        for i, name in enumerate(CHANNEL_NAMES):
            row = i + 1
            self.grid.setRowMinimumHeight(row, 50)

            plot_bub = BubbleToggle(diameter_px=36, palette_idx=0, filled_when_checked=True, tooltip=f"Plot {name}")
            plot_bub.clicked.connect(self.send_update)
            self.plot_buttons.append(plot_bub)
            self.grid.addWidget(plot_bub, row, 0, alignment=Qt.AlignmentFlag.AlignCenter)

            tlab = ClickableLabel(i, f"{self.target_values[i]:.2f}")
            tlab.setFont(READOUT_FONT)
            tlab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tlab.setStyleSheet(TARGET_CSS)
            tlab.clicked.connect(self._on_target_clicked)
            self.labels.append(tlab)
            self.grid.addWidget(tlab, row, 1)

            ch_label = QLabel(name)
            ch_label.setFont(LABEL_FONT)
            tone = PALETTES[0]["label"]
            ch_label.setStyleSheet(f"color: rgba({tone.red()},{tone.green()},{tone.blue()},240);")
            self.grid.addWidget(ch_label, row, 2)

            on_btn = QPushButton("ON")
            on_btn.setCheckable(True)
            on_btn.setFixedHeight(42)
            on_btn.setMinimumWidth(70)
            on_btn.setStyleSheet(self._neon_rect_button_css(cyan=True))
            on_btn.clicked.connect(lambda checked, btn=on_btn: (self._toggle_on_label(btn), self.send_update()))
            self.on_buttons.append(on_btn)
            self.grid.addWidget(on_btn, row, 3)

            en_btn = QPushButton("EN")
            en_btn.setCheckable(True)
            en_btn.setFixedHeight(42)
            en_btn.setMinimumWidth(70)
            en_btn.setStyleSheet(self._neon_rect_button_css(cyan=False))
            en_btn.clicked.connect(lambda checked, btn=en_btn: (self._toggle_en_label(btn), self.send_update()))
            self.en_buttons.append(en_btn)
            self.grid.addWidget(en_btn, row, 4)

        self.left_content_layout.addLayout(self.grid)
        self.left_content_layout.addSpacing(10)

        control_buttons_layout = QHBoxLayout()

        self.plot_all_btn = QPushButton("Plot ALL")
        self.plot_all_btn.setCheckable(True)
        self.plot_all_btn.setToolTip("Toggle all plot bubbles on/off")
        self.plot_all_btn.setFixedHeight(45)
        self.plot_all_btn.setStyleSheet("""
            QPushButton {
                font: bold 16pt 'Orbitron';
                background-color: #003a5a;
                color: #e6ffff;
                border: 1px solid #00b7ff;
                border-radius: 5px;
                padding: 4px 12px;
                min-width: 120px;
            }
            QPushButton:checked {
                background-color: #1a6ea8;
                color: #ffffff;
                border: 1px solid #00ffff;
            }
        """)
        self.plot_all_btn.clicked.connect(self.toggle_plot_all)

        self.lock_btn = QPushButton("UNLOCK")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setToolTip("Lock/Unlock all Power (ON/OFF) and Enable buttons")
        self.lock_btn.setFixedHeight(45)
        self.lock_btn.setStyleSheet("""
            QPushButton {
                font: bold 16pt 'Orbitron';
                background-color: #004a5a;
                color: #e6ffff;
                border: 1px solid #00ffee;
                border-radius: 5px;
                padding: 4px 12px;
                min-width: 120px;
            }
            QPushButton:checked {
                background-color: #9f1c1c;
                color: #ffffff;
                border: 1px solid #ff6666;
            }
        """)
        self.lock_btn.clicked.connect(self.toggle_lock_controls)

        control_buttons_layout.addWidget(self.plot_all_btn)
        control_buttons_layout.addStretch()
        control_buttons_layout.addWidget(self.lock_btn)

        self.left_content_layout.addLayout(control_buttons_layout)
        self.left_content_layout.addStretch()

        scroll.setWidget(content_widget)
        left_layout.addWidget(scroll)

        # ========= RIGHT SIDE: Digit Panel =========
        right_container = QWidget()
        right_container.setMinimumWidth(520)  # allow a wider right panel (more centered split)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        digit_panel = self._create_right_side_digit_panel()
        right_layout.addWidget(digit_panel)

        # Split closer to center
        main_layout.addWidget(left_container, 55)
        main_layout.addWidget(right_container, 45)

        self.setLayout(main_layout)

        self._apply_row_selection_styles()
        self.update_display()

    # =======================
    # Live actual data polling
    # =======================
    def _poll_actual_from_queue(self):
        """Drain queue, keep newest sample, apply scaling raw->eng (same pattern as Monitoring)."""
        if self.data_queue is None:
            return

        now = time.time()

        # Drain queue; keep only newest sample (exactly like Monitoring)
        last = None
        while True:
            try:
                last = self.data_queue.get_nowait()
            except Exception:
                break

        if last is not None:
            self._last_sample = last
            self.last_data_time = now
        else:
            # Hold last for a short time (so plot stays stable if packets pause)
            if self._last_sample is not None and (now - self.last_data_time) <= self.HOLD_SECONDS:
                last = self._last_sample
            else:
                return  # nothing valid to update

        if not isinstance(last, dict):
            return

        raw = list(last.get("channels", []))
        if not raw:
            return

        n = min(len(TRIM_KEYS), len(raw), NUM_CHANNELS)

        try:
            for i in range(n):
                self.actual_values[i] = float(self.scaler.raw_to_eng(TRIM_KEYS[i], raw[i]))
        except Exception as e:
            print("[Controller] scaling error:", e)
            return


    # ==============
    # UI helpers
    # ==============
    def _add_header(self, text: str, col: int):
        lab = QLabel(text)
        lab.setFont(QFont("Orbitron, Segoe UI Semibold, Arial", 16, QFont.Weight.Bold))
        lab.setStyleSheet("color: #79E9FF; background: transparent;")
        lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lab.setFixedHeight(35)
        lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.grid.addWidget(lab, 0, col, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

    def _add_group_header_with_button(self, title: str, col: int, btn_attr: str,
                                      btn_text_on: str, btn_tooltip: str, clicked):
        hdr = QWidget()
        hdr.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v = QVBoxLayout(hdr)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        hdr.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        hdr.setFixedHeight(40)

        lab = QLabel(title)
        lab.setFont(QFont("Orbitron, Segoe UI Semibold, Arial", 16, QFont.Weight.Bold))
        lab.setStyleSheet("color: #79E9FF; background: transparent;")
        lab.setFixedHeight(18)
        lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        btn = QPushButton(btn_text_on)
        btn.setCheckable(True)
        btn.setToolTip(btn_tooltip)
        btn.setFixedHeight(20)
        btn.setStyleSheet("""
            QPushButton {
                font: 14pt 'Orbitron';
                background-color: #083a4a;
                color: #e6ffff;
                border: 1px solid #00b7ff;
                border-radius: 4px;
                padding: 1px 6px;
            }
            QPushButton:checked {
                background-color: #1a6ea8;
                color: #ffffff;
                border: 1px solid #00ffff;
            }
        """)
        btn.clicked.connect(clicked)
        setattr(self, btn_attr, btn)

        v.addWidget(lab, alignment=Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.grid.addWidget(hdr, 0, col)

    def _neon_rect_button_css(self, cyan: bool) -> str:
        if cyan:
            return """
                QPushButton {
                    font: 14pt 'Orbitron';
                    background: rgba(10,25,35,0.9);
                    color: #7CFFB2;
                    border: 1px solid #00ffee;
                    border-radius: 5px;
                    padding: 3px 8px;
                }
                QPushButton:checked {
                    background-color: #00ff85;
                    color: #000;
                    border: 1px solid #00ffff;
                }
                QPushButton:disabled {
                    background: rgba(20,30,40,0.5);
                    color: #666;
                    border: 1px solid #444;
                }
            """
        else:
            return """
                QPushButton {
                    font: 14pt 'Orbitron';
                    background: rgba(10,25,35,0.9);
                    color: #8CE8FF;
                    border: 1px solid #00b7ff;
                    border-radius: 5px;
                    padding: 3px 8px;
                }
                QPushButton:checked {
                    background-color: #00D0FF;
                    color: #000;
                    border: 1px solid #00ffff;
                }
                QPushButton:disabled {
                    background: rgba(20,30,40,0.5);
                    color: #666;
                    border: 1px solid #444;
                }
            """

    # ==============
    # Selection
    # ==============
    def _on_target_clicked(self, index: int):
        self.selected_index = index
        self._apply_row_selection_styles()
        self.update_display()
        self._reset_mini_buffers()

    def _apply_row_selection_styles(self):
        for i, lbl in enumerate(self.labels):
            if i == self.selected_index:
                lbl.setStyleSheet(TARGET_CSS_SELECTED)
                eff = QGraphicsDropShadowEffect(self)
                eff.setBlurRadius(15)
                eff.setOffset(0, 0)
                eff.setColor(QColor(0, 255, 240, 150))
                lbl.setGraphicsEffect(eff)
            else:
                lbl.setStyleSheet(TARGET_CSS)
                lbl.setGraphicsEffect(None)

    def _toggle_on_label(self, button: QPushButton):
        button.setText("OFF" if button.isChecked() else "ON")

    def _toggle_en_label(self, button: QPushButton):
        button.setText("UN" if button.isChecked() else "EN")

    def toggle_lock_controls(self):
        self.controls_locked = self.lock_btn.isChecked()
        self.lock_btn.setText("LOCKED" if self.controls_locked else "UNLOCK")
        enabled = not self.controls_locked
        for b in self.on_buttons:
            b.setEnabled(enabled)
        for b in self.en_buttons:
            b.setEnabled(enabled)
        self.all_power_btn.setEnabled(enabled)
        self.all_enable_btn.setEnabled(enabled)

    def toggle_plot_all(self):
        state = self.plot_all_btn.isChecked()
        self.plot_all_btn.setText("Clear ALL" if state else "Plot ALL")
        for b in self.plot_buttons:
            b.setChecked(state)
        self.send_update()

    def toggle_power_all(self):
        state = self.all_power_btn.isChecked()
        self.all_power_btn.setText("All OFF" if state else "All ON")
        for b in self.on_buttons:
            b.setChecked(state)
            self._toggle_on_label(b)
        self.send_update()

    def toggle_enable_all(self):
        state = self.all_enable_btn.isChecked()
        self.all_enable_btn.setText("All UN" if state else "All EN")
        for b in self.en_buttons:
            b.setChecked(state)
            self._toggle_en_label(b)
        self.send_update()

    # ==============
    # Right Panel UI
    # ==============
    def _create_right_side_digit_panel(self):
        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #051015, stop:1 #0a1820);
                border-radius: 12px;
                border: 2px solid #006688;
            }
        """)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("ADJUST SELECTED CHANNEL")
        title.setFont(QFont("Orbitron", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #7CFFB2; background: transparent; padding: 5px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_effect = QGraphicsDropShadowEffect()
        title_effect.setBlurRadius(10)
        title_effect.setOffset(0, 0)
        title_effect.setColor(QColor(0, 255, 200, 100))
        title.setGraphicsEffect(title_effect)

        layout.addWidget(title)
        layout.addSpacing(10)

        # === Target / Actual readout (two columns, graph-matching colors) ===
        # match the mini plot series colors
        self._c_target = neon_color(4)  # cyan
        self._c_actual = neon_color(9)  # orange

        readout_frame = QWidget()
        readout_frame.setStyleSheet("""
            background: rgba(0, 40, 50, 180);
            border-radius: 10px;
            border: 1px solid #0088AA;
        """)
        readout_layout = QGridLayout(readout_frame)
        readout_layout.setContentsMargins(12, 10, 12, 10)
        readout_layout.setHorizontalSpacing(14)
        readout_layout.setVerticalSpacing(6)

        # Column 1: Target
        target_title = QLabel("Target")
        target_title.setFont(QFont("Orbitron", 13, QFont.Weight.DemiBold))
        target_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        target_title.setStyleSheet(
            f"color: rgba({self._c_target.red()},{self._c_target.green()},{self._c_target.blue()},240);"
        )

        self.target_value_display = QLabel("0.00")
        self.target_value_display.setFont(QFont("Orbitron", 26, QFont.Weight.Bold))
        self.target_value_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.target_value_display.setStyleSheet(
            f"color: rgba({self._c_target.red()},{self._c_target.green()},{self._c_target.blue()},255);"
            "background: rgba(0, 22, 28, 160);"
            "border: 1px solid rgba(0, 180, 255, 200);"
            "border-radius: 8px;"
            "padding: 6px 8px;"
        )

        # Column 2: Actual
        actual_title = QLabel("Actual")
        actual_title.setFont(QFont("Orbitron", 13, QFont.Weight.DemiBold))
        actual_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        actual_title.setStyleSheet(
            f"color: rgba({self._c_actual.red()},{self._c_actual.green()},{self._c_actual.blue()},240);"
        )

        self.actual_value_display = QLabel("—")
        self.actual_value_display.setFont(QFont("Orbitron", 26, QFont.Weight.Bold))
        self.actual_value_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.actual_value_display.setStyleSheet(
            f"color: rgba({self._c_actual.red()},{self._c_actual.green()},{self._c_actual.blue()},255);"
            "background: rgba(0, 22, 28, 160);"
            "border: 1px solid rgba(255, 150, 0, 200);"
            "border-radius: 8px;"
            "padding: 6px 8px;"
        )

        readout_layout.addWidget(target_title, 0, 0)
        readout_layout.addWidget(actual_title, 0, 1)
        readout_layout.addWidget(self.target_value_display, 1, 0)
        readout_layout.addWidget(self.actual_value_display, 1, 1)

        layout.addWidget(readout_frame)

        # Mini trend plot: Target vs Actual (Monitoring-style)
        self.mini_plot = pg.PlotWidget()
        style_plot_cyberpunk(self.mini_plot, "Target vs Actual (Selected)")
        self.mini_plot.setMinimumHeight(170)
        self.mini_plot.setLabel("bottom", "Time (s)")
        self.mini_plot.setLabel("left", "Current (A)")
        layout.addWidget(self.mini_plot)
        layout.addSpacing(12)

        self._mini_t0 = time.time()
        self._mini_t = deque(maxlen=600)
        self._mini_target = deque(maxlen=600)
        self._mini_actual = deque(maxlen=600)

        # choose two distinct neon colors (match monitoring vibe)
        c_target = self._c_target
        c_actual = self._c_actual

        self._mini_target_glow, self._mini_target_line = add_cyberpunk_series(self.mini_plot, c_target, "Target")
        self._mini_actual_glow, self._mini_actual_line = add_cyberpunk_series(self.mini_plot, c_actual, "Actual")

        self._mini_timer = QTimer(self)
        self._mini_timer.timeout.connect(self._update_mini_plot)
        self._mini_timer.start(50)  # 20 Hz

        # Digits
        digits_container = QWidget()
        digits_layout = QVBoxLayout(digits_container)
        digits_layout.setContentsMargins(0, 0, 0, 0)

        self.digit_labels: List[QLabel] = []

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        # Removed: place-value headers
        grid.setRowMinimumHeight(0, 6)

        for i in range(NUM_DIGITS):
            up_btn = QPushButton("▲")
            up_btn.setFixedSize(70, 40)
            up_btn.setStyleSheet(self._digit_button_css())
            up_btn.clicked.connect(self._make_digit_adjuster(i, +1))
            grid.addWidget(up_btn, 1, i, alignment=Qt.AlignmentFlag.AlignCenter)

            digit = QLabel("0")
            digit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            digit.setFont(QFont("Orbitron", 26, QFont.Weight.Bold))
            digit.setFixedSize(80, 60)
            digit.setStyleSheet("""
                color: #7CFFB2;
                background: rgba(0, 30, 40, 200);
                border: 2px solid #00D0FF;
                border-radius: 8px;
                padding: 0px;
            """)
            self.digit_labels.append(digit)
            grid.addWidget(digit, 2, i, alignment=Qt.AlignmentFlag.AlignCenter)

            down_btn = QPushButton("▼")
            down_btn.setFixedSize(70, 40)
            down_btn.setStyleSheet(self._digit_button_css())
            down_btn.clicked.connect(self._make_digit_adjuster(i, -1))
            grid.addWidget(down_btn, 3, i, alignment=Qt.AlignmentFlag.AlignCenter)

        digits_layout.addLayout(grid)
        layout.addWidget(digits_container)

        # Removed: quick adjust buttons (+10, +1, ...)
        layout.addSpacing(6)

        layout.addStretch()
        return panel

    def _digit_button_css(self):
        return """
            QPushButton {
                font: 14pt 'Orbitron';
                background-color: #083a4a;
                color: #e6ffff;
                border: 2px solid #00b7ff;
                border-radius: 6px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #0a4a5a;
                border: 2px solid #00ffff;
            }
            QPushButton:pressed {
                background-color: #0d5068;
            }
        """

    def _make_digit_adjuster(self, digit_index, direction):
        def adjust():
            idx = self.selected_index
            delta = float(DIGIT_FACTORS[digit_index]) * float(direction)
            new_val = float(self.target_values[idx]) + delta
            self.target_values[idx] = max(0.0, round(new_val, 2))
            self.update_display()
            self.send_update()
        return adjust

    # ==============
    # Mini plot helpers
    # ==============
    def _get_actual_current_selected(self) -> float:
        # 1) Preferred: external shared list from MonitoringWindow (already ENG)
        if self.current_values_ref is not None and 0 <= self.selected_index < len(self.current_values_ref):
            try:
                return float(self.current_values_ref[self.selected_index])
            except Exception:
                pass

        # 2) Fallback: internal values updated from data_queue (scaled raw->ENG)
        if 0 <= self.selected_index < len(self.actual_values):
            try:
                return float(self.actual_values[self.selected_index])
            except Exception:
                pass

        return float("nan")

    def _reset_mini_buffers(self):
        self._mini_t0 = time.time()
        self._mini_t.clear()
        self._mini_target.clear()
        self._mini_actual.clear()

    def _update_mini_plot(self):
        if not hasattr(self, "mini_plot"):
            return

        t = time.time() - self._mini_t0
        target = float(self.target_values[self.selected_index])
        actual = self._get_actual_current_selected()

        # If actual is NaN, hold last value (or 0.0)
        if actual != actual:
            actual = self._mini_actual[-1] if len(self._mini_actual) else 0.0

        # append ALL series
        self._mini_t.append(t)
        self._mini_target.append(target)
        self._mini_actual.append(actual)

        # update plot
        if len(self._mini_t) >= 2:
            x = list(self._mini_t)
            yT = list(self._mini_target)
            yA = list(self._mini_actual)

            self._mini_target_glow.setData(x, yT)
            self._mini_target_line.setData(x, yT)

            self._mini_actual_glow.setData(x, yA)
            self._mini_actual_line.setData(x, yA)

        # update numeric actual readout (same sample as plot)
        if hasattr(self, "actual_value_display"):
            try:
                self.actual_value_display.setText(f"{float(actual):.2f}")
            except Exception:
                self.actual_value_display.setText("—")

    # ==============
    # Display / I-O
    # ==============
    def update_display(self):
        # update target labels in the left grid
        for i, lbl in enumerate(self.labels):
            lbl.setText(f"{float(self.target_values[i]):.2f}")

        # update right-panel numeric target + digit readout for selected channel
        if 0 <= self.selected_index < len(self.target_values):
            target_val = float(self.target_values[self.selected_index])

            if hasattr(self, "target_value_display"):
                self.target_value_display.setText(f"{target_val:.2f}")

            digits = self._split_digits(target_val)
            for i, d in enumerate(digits):
                self.digit_labels[i].setText(f".{d}" if i == 4 else str(d))

    def _split_digits(self, value: float):
        int_part = int(value)
        frac_part = round(value - int_part, 2)
        i_thousands = int_part // 1000 % 10
        i_hundreds  = int_part // 100 % 10
        i_tens      = int_part // 10 % 10
        i_ones      = int_part % 10
        d_tenths    = int(frac_part * 10) % 10
        d_hundredths = int(frac_part * 100) % 10
        return [i_thousands, i_hundreds, i_tens, i_ones, d_tenths, d_hundredths]

    def send_update(self):
        on_bits = [btn.isChecked() for btn in self.on_buttons]
        en_bits = [btn.isChecked() for btn in self.en_buttons]
        plot_flags = [btn.isChecked() for btn in self.plot_buttons]
        self.plot_config["channels"] = plot_flags

        eng_values = list(self.target_values)
        raw_values = eng_values[:]

        # Apply scaling to outputs (ENG->RAW) using your Scaler + TRIM_KEYS
        n = min(len(TRIM_KEYS), len(raw_values))
        for i in range(n):
            ch_key = TRIM_KEYS[i]
            raw_values[i] = self.scaler.eng_to_raw(ch_key, eng_values[i])

        try:
            self.control_queue.put((raw_values[:len(TRIM_KEYS)], on_bits, en_bits))
        except Exception as e:
            print("⚠️ control_queue.put failed:", e)
