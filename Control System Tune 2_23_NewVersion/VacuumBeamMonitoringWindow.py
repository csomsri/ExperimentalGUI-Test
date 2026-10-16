# -*- coding: utf-8 -*-
"""
VacuumBeamMonitoringWindow.py

Drop-in Tesla-style replacement for the real CNL Vacuum / Beam / RF monitor.

Preserved CNL interface and communication:
    - class name: VacuumBeamMonitoringWindow
    - constructor:
        VacuumBeamMonitoringWindow(
            data_queue,
            plot_rate_hz=None,
            seconds=None,
            bg_image=None,
        )
    - accepts queue.Queue, multiprocessing.Queue, or deque-like queues
    - consumes the existing CNL packet dictionary from main_control_gui.py
    - uses the real scaling.py / Scaler conversion for TP1, TP2, and RF
    - uses the real beam_cal range selection and voltage-to-current calibration
    - follows beam_range_idx/range_idx from the received packet
    - keeps legacy packet fallbacks used by the previous monitor

Tesla-style display:
    - side table with current value and displayed-history peak-to-peak error
    - dark Beam and RF tiles
    - RF peak-to-peak error bar synchronized with the side table
    - TP1/TP2 FA111-style semicircles with time history inside the meter
    - cyan normal state; amber/red only for active warning/alarm conditions

This is the real control-system module. It intentionally contains no random
standalone data generator.
"""

import time
import math
import collections

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from beam_cal import beam_cal
    from scaling import Scaler
    from app_settings import app_settings
except ImportError as exc:
    raise ImportError(
        "VacuumBeamMonitoringWindow.py requires beam_cal.py, scaling.py, "
        "and app_settings.py in the CNL control-system environment."
    ) from exc


# ----------------------- tiny helpers -----------------------
def clamp(v, a, b):
    return a if v < a else b if v > b else v


def _setting_float(*names, default):
    """Return the first finite numeric app_settings attribute in *names*."""
    for name in names:
        try:
            value = float(getattr(app_settings, name))
            if math.isfinite(value):
                return value
        except Exception:
            pass
    return float(default)

# ----------------------- CORONA STYLE MANAGER -----------------------
class CoronaStyleManager:
    """Manages different corona styles for gauges"""
    
    STYLES = {
        "beam": {
            "name": "Beam Current",
            "corona_mode": "color",
            "base_color": QtGui.QColor(82, 188, 214),
            "value_colors": [
                (0.0, QtGui.QColor(82, 188, 214)),
                (1.0, QtGui.QColor(82, 188, 214)),
            ],
        },
        "rf_power": {
            "name": "RF Power",
            "corona_mode": "color",
            "base_color": QtGui.QColor(82, 188, 214),
            "value_colors": [
                (0.0, QtGui.QColor(82, 188, 214)),
                (1.0, QtGui.QColor(82, 188, 214)),
            ],
        },
        "vacuum": {
            "name": "Vacuum",
            "corona_mode": "color",
            "base_color": QtGui.QColor(82, 188, 214),
            "value_colors": [
                (0.0, QtGui.QColor(82, 188, 214)),
                (1.0, QtGui.QColor(82, 188, 214)),
            ],
        },
    }

    @classmethod
    def get_style(cls, gauge_type):
        return cls.STYLES.get(gauge_type, cls.STYLES["beam"])

# ----------------------- status dot -------------------------
class StatusDot(QtWidgets.QWidget):
    def __init__(self, color_ok=QtGui.QColor(82,188,214), color_bad=QtGui.QColor(92,101,109), parent=None):
        super().__init__(parent)
        self._ok = False; self._ok_c, self._bad_c = color_ok, color_bad
        self.setFixedSize(14, 14)
    def set_ok(self, ok: bool):
        if ok != self._ok: self._ok = ok; self.update()
    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setBrush(self._ok_c if self._ok else self._bad_c); p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(self.rect().adjusted(1,1,-1,-1))

# ----------------------- HUD Card (glossy box) -----------------------
class HudCard(QtWidgets.QFrame):
    """Glass/glow card wrapper like your reference HUD."""
    def __init__(self, child: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(0)
        lay.addWidget(child)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rf = QtCore.QRectF(self.rect().adjusted(2, 2, -2, -2))

        path = QtGui.QPainterPath()
        path.addRoundedRect(rf, 14.0, 14.0)

        # Tesla-like matte graphite card
        glass = QtGui.QLinearGradient(rf.topLeft(), rf.bottomLeft())
        glass.setColorAt(0.00, QtGui.QColor(27, 29, 33, 245))
        glass.setColorAt(1.00, QtGui.QColor(15, 17, 20, 245))
        p.fillPath(path, glass)

        # Thin restrained edge instead of a large neon halo
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 22), 1.0))
        p.drawPath(path)

# ----------------------- ENHANCED mini time-series plot with NEON STYLING -----------------------
class TimeSeriesPlot(QtWidgets.QWidget):
    def __init__(self, parent=None, max_points=240, ymin=0.0, ymax=100.0, line=None, title=""):
        super().__init__(parent)
        self.setMinimumSize(220, 170)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.max_points = int(max_points)
        self._buf = collections.deque(maxlen=self.max_points)
        self._ymin, self._ymax = float(ymin), float(ymax)
        self.title = title

        # Enhanced neon styling
        self.col_bg    = QtGui.QColor(12, 13, 15)
        self.col_grid  = QtGui.QColor(255, 255, 255, 15)
        self.col_axes  = QtGui.QColor(255, 255, 255, 45)
        self.col_title = QtGui.QColor(235, 238, 242, 235)
        self.col_line  = QtGui.QColor(line) if line else QtGui.QColor(120, 240, 255)
        self.col_fill  = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 14)
        
        # Glow effect colors
        self.col_glow = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 26)
        
        # Animation for pulse effects
        self._pulse_phase = 0.0
        self._animation_timer = QtCore.QTimer(self)
        self._animation_timer.timeout.connect(self._animate)
        self._animation_timer.start(50)

    def _animate(self):
        """Animation for subtle pulse effects"""
        self._pulse_phase += 0.05
        self.update()

    def add_point(self, v: float): 
        self._buf.append(float(v))
        self.update()
        
    def set_range(self, ymin: float, ymax: float): 
        self._ymin, self._ymax = float(ymin), float(ymax)
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | 
                        QtGui.QPainter.RenderHint.SmoothPixmapTransform |
                        QtGui.QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        
        # Enhanced background with gradient
        bg_gradient = QtGui.QLinearGradient(0, 0, 0, h)
        bg_gradient.setColorAt(0.0, QtGui.QColor(17, 18, 21))
        bg_gradient.setColorAt(1.0, QtGui.QColor(10, 11, 13))
        p.fillRect(self.rect(), bg_gradient)

        # Draw title with enhanced styling
        if self.title:
            p.setPen(QtGui.QPen(self.col_title, 1))
            p.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.DemiBold))
            p.drawText(QtCore.QRectF(0, 6, w, 24),
                       QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop,
                       self.title)

        # Enhanced grid with subtle styling
        p.setPen(QtGui.QPen(self.col_grid, 0.8))
        rows, cols = 8, 12  # More grid lines for better readability
        for i in range(1, rows):
            alpha = 20 if i % 2 == 0 else 11
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, alpha), 0.7))
            p.drawLine(0, int(h*(i/rows)), w, int(h*(i/rows)))
        for j in range(1, cols):
            alpha = 20 if j % 2 == 0 else 11
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, alpha), 0.7))
            p.drawLine(int(w*(j/cols)), 0, int(w*(j/cols)), h)

        if len(self._buf) < 2: 
            return

        vr = (self._ymax - self._ymin) or 1.0
        pts = [QtCore.QPoint(int(w*(i/(self.max_points-1))),
                             int(h - ((self._buf[i]-self._ymin)/vr)*h))
               for i in range(len(self._buf))]

        # Enhanced filled area with gradient
        poly = QtGui.QPolygon([QtCore.QPoint(0,h)] + pts + [QtCore.QPoint(pts[-1].x(),h)])
        
        # Gradient fill
        fill_gradient = QtGui.QLinearGradient(0, h, 0, 0)
        fill_gradient.setColorAt(0.0, QtGui.QColor(self.col_fill.red(), self.col_fill.green(), 
                                                 self.col_fill.blue(), 32))
        fill_gradient.setColorAt(1.0, QtGui.QColor(self.col_fill.red(), self.col_fill.green(), 
                                                 self.col_fill.blue(), 4))
        p.setBrush(fill_gradient)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawPolygon(poly)

        # Glow effect behind the line
        glow_pen = QtGui.QPen(self.col_glow, 2.4)
        glow_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(glow_pen)
        p.drawPolyline(QtGui.QPolygon(pts))

        # Main data line with enhanced styling
        main_pen = QtGui.QPen(self.col_line, 2.0)
        main_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(main_pen)
        p.drawPolyline(QtGui.QPolygon(pts))

        # Highlight current point with pulse effect
        if pts:
            current_pt = pts[-1]
            pulse_alpha = int(150 + 100 * math.sin(self._pulse_phase))
            p.setBrush(QtGui.QColor(255, 255, 255, pulse_alpha))
            p.setPen(QtGui.QPen(self.col_line, 1))
            p.drawEllipse(current_pt, 4, 4)

        # Enhanced axes with neon styling
        p.setPen(QtGui.QPen(self.col_axes, 2.0))
        p.drawLine(0, h-1, w, h-1)  # X-axis
        p.drawLine(0, 0, 0, h)      # Y-axis

        # Draw current value indicator
        if self._buf:
            current_val = self._buf[-1]
            value_text = f"{current_val:.3f}"
            p.setFont(QtGui.QFont("Consolas, Monospace", 9, QtGui.QFont.Weight.Medium))
            fm = QtGui.QFontMetrics(p.font())
            text_width = fm.horizontalAdvance(value_text)
            
            # Background for value display
            bg_rect = QtCore.QRect(w - text_width - 12, 8, text_width + 8, fm.height() + 4)
            p.setBrush(QtGui.QColor(0, 0, 0, 180))
            p.setPen(QtGui.QPen(self.col_line, 1))
            p.drawRect(bg_rect)
            
            # Value text
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
            p.drawText(bg_rect, QtCore.Qt.AlignmentFlag.AlignCenter, value_text)

# ----------------------- ENHANCED Neon radial gauge with COLOR STYLING -----------------------
class NeonRadial01(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal(float)
    
    def __init__(self, parent=None, value=0.0, title="Gauge", unit="", status_bands=None, gauge_type="beam"):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        self._value = float(clamp(value, 0.0, 1.0))
        self.title, self.unit = title, unit
        
        # ROTATED configuration: bottom start, clockwise sweep
        self.span_deg, self.start_deg = 220.0, -140.0
        self.clockwise = True
        
        self.display_decimals = 2
        self._big_text = None
        self._big_unit = ""
        self._over_range = False  # NEW: Over-range indicator
        self.range_indicator = ""  # NEW: For showing current range
        
        # Animation timers for enhanced effects
        self._t = 0.0
        self._pulse = QtCore.QTimer(self)
        self._pulse.timeout.connect(self._tick)
        self._pulse.start(16)
        
        # Warning range configuration
        self.warning_start = 0.7  # Start of warning range (normalized)
        self.warning_end = 0.85   # End of warning range (normalized)
        self.danger_start = 0.85  # Start of danger range (normalized)
        
        # Apply corona styling based on gauge type
        self.gauge_type = gauge_type
        self._apply_corona_style()
        
        self.status_bands = status_bands or dict(ok=(0.25,0.75), warn=(0.15,0.90))
        self._fs_value = 1.0
        self._tick_decimals = 2
        self._tick_labels = [f"{i/10:.1f}" for i in range(11)]
        self._label_min = 0.0

    def _apply_corona_style(self):
        """Apply a restrained graphite/cyan instrument palette."""
        style = CoronaStyleManager.get_style(self.gauge_type)

        self.c_bg = QtGui.QColor(9, 11, 13)
        self.c_tick_ma = QtGui.QColor(235, 241, 244, 225)
        self.c_tick_mi = QtGui.QColor(150, 163, 172, 145)
        self.c_readout = QtGui.QColor(241, 245, 247)
        self.c_title = QtGui.QColor(195, 204, 210)
        self.c_cyan_dim = QtGui.QColor(83, 96, 104, 85)

        self.corona_mode = "color"
        self.base_color = style["base_color"]
        self.value_colors = style["value_colors"]

        # Warning and alarm colors remain available, but are shown brightly
        # only when the current value actually enters those zones.
        self.warning_color = QtGui.QColor(224, 166, 65, 215)
        self.danger_color = QtGui.QColor(232, 88, 88, 225)
        self.warning_glow = QtGui.QColor(224, 166, 65, 72)

        self.col_ok = QtGui.QColor(82, 188, 214)
        self.col_warn = QtGui.QColor(224, 166, 65)
        self.col_alarm = QtGui.QColor(232, 88, 88)

        self.nd_front_len = 0.76
        self.nd_base_from_c = 0.12
        self.nd_base_w = 8.0
        self.nd_tip_w = 2.0
        self.nd_edge_highlight = QtGui.QColor(242, 247, 249, 205)
        self.nd_glow = QtGui.QColor(82, 188, 214, 70)

    # NEW: Over-range methods
    def set_over_range(self, is_over_range: bool):
        """Visual indicator for over-range condition"""
        self._over_range = is_over_range
        self.update()

    def is_over_range(self):
        """Check if current value is over range"""
        return getattr(self, '_over_range', False)

    # Range indicator methods
    def set_range_indicator(self, range_min: float, range_max: float, unit: str):
        """Show a range whose values are already expressed in *unit*."""
        if unit == "pA":
            decimals = 0
        elif unit == "nA":
            decimals = 1 if abs(float(range_max)) < 10 else 0
        else:
            decimals = 2

        self.range_indicator = (
            f"{float(range_min):.{decimals}f} - "
            f"{float(range_max):.{decimals}f} {unit}"
        )
        self.update()

    def _draw_range_indicator(self, p, cx, cy, s):
        """Draw the current range indicator"""
        if hasattr(self, 'range_indicator') and self.range_indicator:
            f = QtGui.QFont("Segoe UI", max(8, int(s*0.025)))
            p.setFont(f)
            p.setPen(QtGui.QPen(QtGui.QColor(150, 200, 255, 180)))
            fm = QtGui.QFontMetricsF(f)
            p.drawText(QtCore.QPointF(cx - fm.horizontalAdvance(self.range_indicator)/2, 
                                     cy + s*0.35), self.range_indicator)

    def _draw_color_corona(self, p, cx, cy, r_in, r_out):
        """Color corona with smooth gradient based on current value"""
        current_color = self.get_current_color()
        
        # Create a smooth radial gradient for the color corona
        ring = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
        inner_stop = r_in / r_out
        
        # Main color gradient from current value color to darker variant
        ring.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
        ring.setColorAt(inner_stop, QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 132))
        ring.setColorAt(0.8, self._interpolate_color(current_color, QtGui.QColor(24, 32, 36), 0.62))
        ring.setColorAt(1.0, self._interpolate_color(current_color, QtGui.QColor(7, 10, 12), 0.82))
        
        self._fill_annulus(p, cx, cy, r_in, r_out, ring)
        
        # Add outer glow
        p.setPen(QtGui.QPen(QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 150), 2.0))
        p.drawEllipse(QtCore.QPointF(cx, cy), r_out, r_out)
        
        # Add inner glow
        p.setPen(QtGui.QPen(QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 95), 1.0))
        p.drawEllipse(QtCore.QPointF(cx, cy), r_in, r_in)

    def _fill_annulus(self, p, cx, cy, r_in, r_out, brush):
        outer = QtCore.QRectF(cx - r_out, cy - r_out, 2*r_out, 2*r_out)
        inner = QtCore.QRectF(cx - r_in, cy - r_in, 2*r_in, 2*r_in)
        path_outer = QtGui.QPainterPath()
        path_outer.addEllipse(outer)
        path_inner = QtGui.QPainterPath()
        path_inner.addEllipse(inner)
        annulus = path_outer.subtracted(path_inner)
        p.save()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(brush))
        p.drawPath(annulus)
        p.restore()

    def _draw_grid_pattern(self, p, rect):
        """Draw subtle grid pattern"""
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 12), 1))
        grid_size = 20
        for x in range(0, rect.width(), grid_size):
            p.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), grid_size):
            p.drawLine(0, y, rect.width(), y)

    def set_warning_range(self, start, end, danger_start=None):
        """Set the warning and danger ranges (normalized values)"""
        self.warning_start = float(start)
        self.warning_end = float(end)
        if danger_start is not None:
            self.danger_start = float(danger_start)
        self.update()

    def is_in_warning_zone(self):
        """Check if current value is in warning zone"""
        return self.warning_start <= self._value < self.warning_end

    def is_in_danger_zone(self):
        """Check if current value is in danger zone"""
        return self._value >= self.danger_start

    def _tick(self):
        """Animation tick for enhanced corona effects"""
        self._t += 0.016
        self.update()

    def get_current_color(self):
        """Get color based on current normalized value using corona styling"""
        normalized = self._value
        
        # If over-range, use danger color
        if self.is_over_range():
            return self.col_alarm
        
        for i in range(len(self.value_colors) - 1):
            pos1, color1 = self.value_colors[i]
            pos2, color2 = self.value_colors[i + 1]
            if pos1 <= normalized <= pos2:
                t = (normalized - pos1) / (pos2 - pos1)
                return self._interpolate_color(color1, color2, t)
        return self.value_colors[-1][1] if self.value_colors else QtGui.QColor(255, 0, 0)

    def _interpolate_color(self, color1, color2, t):
        """Interpolate between two colors"""
        r = int(color1.red() + (color2.red() - color1.red()) * t)
        g = int(color1.green() + (color2.green() - color1.green()) * t)
        b = int(color1.blue() + (color2.blue() - color1.blue()) * t)
        return QtGui.QColor(r, g, b)

    def set_fullscale(self, fs_value: float, unit_label: str,
                      decimals: int | None = None, label_min: float | None = None):
        """Set FS and tick labels. Labels run label_min..FS; sweep still 0..FS."""
        self._fs_value  = max(1e-12, float(fs_value))
        self._label_min = float(label_min) if (label_min is not None) else 0.0
        self.unit = unit_label or self.unit
        if decimals is not None:
            self._tick_decimals = int(decimals)
        # Build 11 labels from label_min..FS
        self._tick_labels = []
        for i in range(11):
            val = self._label_min + (self._fs_value - self._label_min) * (i / 10.0)
            self._tick_labels.append(f"{val:.{self._tick_decimals}f}")
        self.update()

    def set_big_readout(self, value: float, unit: str, decimals: int = 2):
        self._big_text = f"{float(value):.{decimals}f}"
        self._big_unit = unit
        self.update()

    def getValue(self): return self._value
    def setValue(self, v: float):
        v = clamp(float(v), 0.0, 1.0)
        if abs(v - self._value) <= 1e-9: return
        self._value = v
        self.valueChanged.emit(v)
        self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)

    def set_value(self, v, animated=True):
        v = clamp(float(v), 0, 1)
        if animated:
            anim = QtCore.QPropertyAnimation(self, b"value", duration=220)
            anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)
            anim.setStartValue(self._value); anim.setEndValue(v); anim.start(QtCore.QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        else:
            self.setValue(v)

    def _val_to_angle(self, norm): 
        return (self.start_deg - self.span_deg*norm) if self.clockwise else (self.start_deg + self.span_deg*norm)

    def _draw_ticks_labels(self, p, cx, cy, R):
        """Draw ticks and labels with corona styling"""
        start, span = self.start_deg, self.span_deg
        dir_mult = -1.0 if self.clockwise else 1.0
        r_out, r_major_in, r_minor_in = R*0.94, R*0.86, R*0.90

        # Draw tick marks
        for i in range(0, 101, 5):
            frac = i/100.0
            a_deg = start + dir_mult * span * frac
            a = math.radians(a_deg)
            x1, y1 = cx + r_out * math.cos(a), cy - r_out * math.sin(a)
            x2, y2 = cx + r_major_in * math.cos(a), cy - r_major_in * math.sin(a)
            p.setPen(QtGui.QPen(self.c_tick_mi if i % 25 else self.c_tick_ma, 2 if i % 25 else 3))
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))

        # Draw labels (use decade-style list)
        f_px = max(8, int(min(self.width(), self.height()) * 0.030))
        f = QtGui.QFont("Segoe UI", f_px, QtGui.QFont.Weight.Medium)
        r_label = (R*0.76) * 0.92
        
        for i in range(11):
            frac = i / 10.0
            a_deg = start - span * frac if self.clockwise else start + span * frac
            a = math.radians(a_deg)
            tx, ty = cx + r_label * math.cos(a), cy - r_label * math.sin(a)

            s = self._tick_labels[i] if 0 <= i < len(self._tick_labels) else f"{frac:.1f}"

            if abs(frac - 0.5) < 1e-6:
                fb = QtGui.QFont(f); fb.setWeight(QtGui.QFont.Weight.Bold); p.setFont(fb)
                p.setPen(QtGui.QColor(220,255,255,255))
            else:
                p.setFont(f); p.setPen(QtGui.QColor(200,235,255,220))

            fm = QtGui.QFontMetricsF(p.font())
            p.drawText(QtCore.QRectF(tx - fm.horizontalAdvance(s)/2, ty - fm.height()/2,
                                     fm.horizontalAdvance(s), fm.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, s)

    def _draw_warning_range_bar(self, p, cx, cy, R):
        """Draw quiet zones; illuminate amber/red only when active."""
        start_deg, span_deg = self.start_deg, self.span_deg

        def norm(v):
            return clamp(v, 0.0, 1.0)

        warning_start_angle = start_deg - span_deg * norm(self.warning_start)
        warning_end_angle = start_deg - span_deg * norm(self.warning_end)
        danger_start_angle = start_deg - span_deg * norm(self.danger_start)
        max_angle = start_deg - span_deg
        r_outer = R * 0.90
        zone_rect = QtCore.QRectF(
            cx - r_outer, cy - r_outer, 2 * r_outer, 2 * r_outer
        )
        dim_zone = QtGui.QColor(135, 146, 154, 42)

        warning_active = self.is_in_warning_zone()
        danger_active = self.is_in_danger_zone() or self.is_over_range()

        warning_color = self.warning_color if warning_active else dim_zone
        warning_width = 5.0 if warning_active else 3.5
        warning_span = warning_start_angle - warning_end_angle
        pen = QtGui.QPen(warning_color, warning_width)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(
            zone_rect,
            int(warning_start_angle * 16),
            int(warning_span * 16),
        )
        if warning_active:
            glow = QtGui.QPen(self.warning_glow, 9.0)
            glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(glow)
            p.drawArc(
                zone_rect,
                int(warning_start_angle * 16),
                int(warning_span * 16),
            )

        danger_color = self.danger_color if danger_active else dim_zone
        danger_width = 5.0 if danger_active else 3.5
        danger_span = danger_start_angle - max_angle
        pen = QtGui.QPen(danger_color, danger_width)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(
            zone_rect,
            int(danger_start_angle * 16),
            int(danger_span * 16),
        )
        if danger_active:
            pulse_alpha = int(80 + 65 * abs(math.sin(self._t * 4)))
            pulse = QtGui.QPen(
                QtGui.QColor(232, 88, 88, pulse_alpha), 9.0
            )
            pulse.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pulse)
            p.drawArc(
                zone_rect,
                int(danger_start_angle * 16),
                int(danger_span * 16),
            )

    def _draw_glowing_arc(self, p, cx, cy, R_arc):
        """Draw value arc with glow effect"""
        current_color = self.get_current_color()
        start, span = self.start_deg, self.span_deg
        f = clamp(self._value, 0.0, 1.0)
        a_val = self.start_deg - self.span_deg * f

        # Background arc
        pen_bg = QtGui.QPen(self.c_cyan_dim, 8)
        pen_bg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        rect_val = QtCore.QRectF(cx - R_arc, cy - R_arc, 2*R_arc, 2*R_arc)
        p.drawArc(rect_val, int(start*16), int(-span*16))
        
        # Value arc with dynamic color and glow
        sweep = a_val - start
        if self.clockwise: sweep = -abs(sweep)
        
        # Main arc
        pen_fg = QtGui.QPen(current_color, 6.5)
        pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_fg)
        p.drawArc(rect_val, int(start*16), int(sweep*16))
        
        # Glow effect
        pen_glow = QtGui.QPen(current_color, 9.0)
        pen_glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setOpacity(0.18)
        p.setPen(pen_glow)
        p.drawArc(rect_val, int(start*16), int(sweep*16))
        p.setOpacity(1.0)

    def _draw_color_center(self, p, cx, cy, inner_ring_r):
        """Enhanced center with color elements"""
        current_color = self.get_current_color()
        
        # Outer glow ring
        p.setPen(QtGui.QPen(QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 150), 3.5))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r, inner_ring_r)
        
        # Inner ring
        p.setPen(QtGui.QPen(QtGui.QColor(215, 224, 229, 155), 2.0))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r - 3, inner_ring_r - 3)
        
        # Center with subtle gradient matching current color
        center_grad = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), inner_ring_r - 5)
        center_grad.setColorAt(0.0, QtGui.QColor(current_color.red()//4, 
                                               current_color.green()//4, 
                                               current_color.blue()//4))
        center_grad.setColorAt(1.0, QtGui.QColor(0, 0, 0))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(center_grad)
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r - 5, inner_ring_r - 5)
        
        # Pulsing center dot - faster pulse for over-range
        pulse_speed = 8 if self.is_over_range() else 3
        pulse_alpha = int(150 + 100 * math.sin(self._t * pulse_speed))
        p.setBrush(QtGui.QColor(current_color.red(), current_color.green(), 
                               current_color.blue(), pulse_alpha))
        p.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)

    def _draw_color_needle(self, p, cx, cy, R, a_deg):
        """Color needle with dynamic colors"""
        current_color = self.get_current_color()
        a = math.radians(a_deg)
        dirx, diry = math.cos(a), -math.sin(a)
        nx, ny = -diry, dirx
        
        r_start = R * self.nd_base_from_c
        r_tip = R * self.nd_front_len
        
        baseL = QtCore.QPointF(cx + dirx*r_start + nx*(self.nd_base_w/2),
                               cy + diry*r_start + ny*(self.nd_base_w/2))
        baseR = QtCore.QPointF(cx + dirx*r_start - nx*(self.nd_base_w/2),
                               cy + diry*r_start - ny*(self.nd_base_w/2))
        tipL = QtCore.QPointF(cx + dirx*r_tip + nx*(self.nd_tip_w/2),
                              cy + diry*r_tip + ny*(self.nd_tip_w/2))
        tipR = QtCore.QPointF(cx + dirx*r_tip - nx*(self.nd_tip_w/2),
                              cy + diry*r_tip - ny*(self.nd_tip_w/2))

        # Dynamic needle colors based on value
        nd_color_tip = current_color
        nd_color_base = self._interpolate_color(current_color, QtGui.QColor(24, 31, 35), 0.76)
        nd_glow = QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 90)

        # Enhanced glow in warning/danger/over-range zones
        if self.is_over_range():
            nd_glow = QtGui.QColor(255, 0, 0, 150)  # Bright red for over-range
            # Make needle blink for over-range
            blink_alpha = int(100 + 100 * abs(math.sin(self._t * 10)))
            nd_glow = QtGui.QColor(255, 0, 0, blink_alpha)
        elif self.is_in_danger_zone():
            nd_glow = QtGui.QColor(255, 50, 50, 120)
        elif self.is_in_warning_zone():
            nd_glow = QtGui.QColor(255, 150, 50, 100)

        # Glow effect
        glow_path = QtGui.QPainterPath()
        for pt in (baseL, tipL, tipR, baseR):
            if glow_path.isEmpty(): glow_path.moveTo(pt)
            else: glow_path.lineTo(pt)
        glow_path.closeSubpath()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(nd_glow)
        p.drawPath(glow_path)

        # Needle fill gradient
        grad = QtGui.QLinearGradient(tipL, baseR)
        grad.setColorAt(0.0, nd_color_tip)
        grad.setColorAt(1.0, nd_color_base)

        path = QtGui.QPainterPath()
        for pt in (baseL, tipL, tipR, baseR):
            if path.isEmpty(): path.moveTo(pt)
            else: path.lineTo(pt)
        path.closeSubpath()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(path)

        # Edge highlights
        pen_hi = QtGui.QPen(self.nd_edge_highlight, 2.0)
        pen_hi.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_hi)
        p.drawLine(tipL, baseL)
        p.drawLine(tipR, baseR)

    def _draw_enhanced_readout(self, p, cx, cy, s):
        """Enhanced numeric readout with warning status and over-range indicator"""
        current_color = self.get_current_color()
        
        # Over-range indicator (highest priority)
        if self.is_over_range():
            status_text = "OVER RANGE!"
            status_color = QtGui.QColor(255, 0, 0)
            # Fast pulsing effect for over-range
            pulse_alpha = int(200 + 55 * math.sin(self._t * 12))
            status_color = QtGui.QColor(255, 0, 0, pulse_alpha)
        # Warning status text
        elif self.is_in_danger_zone():
            status_text = "DANGER!"
            status_color = QtGui.QColor(255, 50, 50)
            # Pulsing effect for danger
            pulse_alpha = int(200 + 55 * math.sin(self._t * 8))
            status_color = QtGui.QColor(255, 50, 50, pulse_alpha)
        elif self.is_in_warning_zone():
            status_text = "WARNING"
            status_color = QtGui.QColor(255, 165, 0)
        else:
            status_text = ""
            status_color = QtGui.QColor(100, 255, 100)  # Default green
        
        # Draw status text
        if status_text:
            f_status = QtGui.QFont("Segoe UI", max(10, int(s*0.04)), QtGui.QFont.Weight.Bold)
            p.setFont(f_status)
            p.setPen(QtGui.QPen(status_color, 2))
            fm_status = QtGui.QFontMetricsF(f_status)
            status_rect = QtCore.QRectF(cx - fm_status.horizontalAdvance(status_text)/2,
                                      cy - s*0.15,
                                      fm_status.horizontalAdvance(status_text),
                                      fm_status.height())
            p.drawText(status_rect, QtCore.Qt.AlignmentFlag.AlignCenter, status_text)
        
        # Main value - add over-range indicator
        big = self._big_text if self._big_text is not None else f"{self._value:.{self.display_decimals}f}"
        if self.is_over_range():
            big = ">" + big  # Add > prefix to indicate over-range
        
        f = QtGui.QFont("Segoe UI", max(12, int(s*0.08)), QtGui.QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QtGui.QPen(current_color, 2))
        br = QtGui.QFontMetricsF(f).boundingRect(big)
        p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()/4), big)
        
        # Unit
        if self._big_unit:
            f2 = QtGui.QFont("Segoe UI", max(10, int(s*0.045)))
            p.setFont(f2)
            p.setPen(QtGui.QColor(155, 168, 176))
            p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()*0.8), self._big_unit)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        s = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        R = s*0.46

        # background gradient from cyberpunk version
        bg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R * 1.5)
        bg.setColorAt(0.0, QtGui.QColor(18, 21, 23))
        bg.setColorAt(0.7, QtGui.QColor(10, 12, 14))
        bg.setColorAt(1.0, QtGui.QColor(5, 6, 8))
        p.fillRect(rect, QtGui.QBrush(bg))

        # Draw subtle grid pattern
        self._draw_grid_pattern(p, rect)

        # --- WARNING RANGE BAR (drawn first, behind everything) ---
        self._draw_warning_range_bar(p, cx, cy, R)

        # --- COLOR CORONA ANNULAR BAND ---
        r_in, r_out = R*0.36, R*0.56
        self._draw_color_corona(p, cx, cy, r_in, r_out)

        # --- ENHANCED CENTER ---
        inner_ring_r = R * 0.40
        self._draw_color_center(p, cx, cy, inner_ring_r)

        # --- VALUE ARC ---
        R_arc = R * 0.76
        self._draw_glowing_arc(p, cx, cy, R_arc)

        # --- TICKS + LABELS ---
        self._draw_ticks_labels(p, cx, cy, R)

        # --- ENHANCED NEEDLE ---
        # Use the normalized value for needle positioning
        a_val = self._val_to_angle(self._value)
        self._draw_color_needle(p, cx, cy, R, a_val)

        # --- ENHANCED NUMERIC READOUT ---
        self._draw_enhanced_readout(p, cx, cy, s)

        # --- RANGE INDICATOR (NEW: Add this at the end) ---
        self._draw_range_indicator(p, cx, cy, s)


# ----------------------- Tesla / FA111 embedded-history vacuum gauge -----------------------
class LogVacuumGauge(QtWidgets.QWidget):
    """
    Dark Tesla-style FA111-inspired logarithmic vacuum meter.

    The vacuum history is drawn inside the semicircle, so TP1 and TP2 no
    longer need separate side plots.  The pressure scale remains logarithmic:

        10^-6 -> 10^-5 -> 10^-4 -> 10^-3 mbar

    Default alarm bands:
        GOOD    : pressure < 1e-5 mbar
        WARNING : 1e-5 <= pressure < 1e-4 mbar
        ALARM   : pressure >= 1e-4 mbar
    """

    def __init__(
        self,
        title="TP",
        min_exp=-6,
        max_exp=-3,
        units="mbar",
        warning_at=1.0e-5,
        alarm_at=1.0e-4,
        max_points=240,
        history_seconds=60.0,
        parent=None,
    ):
        super().__init__(parent)

        self.title = str(title)
        self.units = str(units)
        self.min_exp = int(min_exp)
        self.max_exp = int(max_exp)
        self.warning_at = float(warning_at)
        self.alarm_at = float(alarm_at)
        self.history_seconds = max(1.0, float(history_seconds))

        self._value = float("nan")
        self._history = collections.deque(maxlen=max(16, int(max_points)))
        self._phase = 0.0

        self.setMinimumSize(410, 270)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        # Very dark Tesla-style palette.
        self.c_bg_top = QtGui.QColor(10, 13, 16)
        self.c_bg_bottom = QtGui.QColor(2, 4, 6)
        self.c_track = QtGui.QColor(61, 72, 80, 145)
        self.c_edge = QtGui.QColor(177, 193, 203, 65)
        self.c_tick_major = QtGui.QColor(236, 242, 246, 235)
        self.c_tick_minor = QtGui.QColor(160, 177, 187, 180)
        self.c_text = QtGui.QColor(239, 244, 247, 245)
        self.c_muted = QtGui.QColor(128, 143, 153, 220)
        self.c_history_grid = QtGui.QColor(255, 255, 255, 18)

        self.c_cyan = QtGui.QColor(35, 207, 255)
        self.c_good = QtGui.QColor(35, 207, 255)
        self.c_warn = QtGui.QColor(224, 166, 65)
        self.c_alarm = QtGui.QColor(232, 88, 88)

        self._anim = QtCore.QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(40)

    def _tick(self):
        self._phase += 0.08
        if self._state_name() == "ALARM":
            self.update()

    def set_thresholds(self, warning_at: float, alarm_at: float):
        warning_at = float(warning_at)
        alarm_at = float(alarm_at)
        if warning_at <= 0.0 or alarm_at <= warning_at:
            raise ValueError(
                "Vacuum thresholds must satisfy 0 < warning_at < alarm_at."
            )
        self.warning_at = warning_at
        self.alarm_at = alarm_at
        self.update()

    def set_value(self, value: float):
        try:
            value = float(value)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError

            low = 10.0 ** self.min_exp
            high = 10.0 ** self.max_exp
            value = max(low, min(high, value))
            self._value = value
            self._history.append(value)
        except (TypeError, ValueError, OverflowError):
            self._value = float("nan")

        self.update()

    def _value_to_norm(self, value: float) -> float:
        low = 10.0 ** self.min_exp
        high = 10.0 ** self.max_exp
        value = max(low, min(high, float(value)))
        span = float(self.max_exp - self.min_exp) or 1.0
        return clamp(
            (math.log10(value) - self.min_exp) / span,
            0.0,
            1.0,
        )

    def _norm_to_angle(self, norm: float) -> float:
        # True semicircle: low pressure at left, high pressure at right.
        return 180.0 - 180.0 * clamp(norm, 0.0, 1.0)

    def _value_to_angle(self, value: float) -> float:
        return self._norm_to_angle(self._value_to_norm(value))

    def _state_name(self) -> str:
        if math.isnan(self._value):
            return "NO DATA"
        if self._value >= self.alarm_at:
            return "ALARM"
        if self._value >= self.warning_at:
            return "WARNING"
        return "GOOD"

    def _state_color(self) -> QtGui.QColor:
        state = self._state_name()
        if state == "ALARM":
            return QtGui.QColor(self.c_alarm)
        if state == "WARNING":
            return QtGui.QColor(self.c_warn)
        if state == "GOOD":
            return QtGui.QColor(self.c_good)
        return QtGui.QColor(self.c_muted)

    def _display_color(self) -> QtGui.QColor:
        state = self._state_name()
        if state == "GOOD":
            return QtGui.QColor(self.c_cyan)
        return self._state_color()

    @staticmethod
    def _font(family, pixel_size, weight=QtGui.QFont.Weight.Normal):
        font = QtGui.QFont(family)
        font.setPixelSize(max(7, int(pixel_size)))
        font.setWeight(weight)
        return font

    @staticmethod
    def _superscript(number: int) -> str:
        table = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
        return str(number).translate(table)

    @staticmethod
    def _draw_arc(
        painter,
        rect,
        start_deg,
        span_deg,
        color,
        width,
        cap=QtCore.Qt.PenCapStyle.RoundCap,
    ):
        pen = QtGui.QPen(color, float(width))
        pen.setCapStyle(cap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawArc(
            rect,
            int(start_deg * 16),
            int(span_deg * 16),
        )

    def _draw_zone(self, painter, rect, norm_0, norm_1, color, width):
        start = self._norm_to_angle(norm_0)
        end = self._norm_to_angle(norm_1)
        self._draw_arc(
            painter,
            rect,
            start,
            end - start,
            color,
            width,
        )

    def _draw_title(self, painter, width, height):
        dot_radius = max(3.5, height * 0.010)
        dot_x = width * 0.045
        dot_y = height * 0.075

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self.c_cyan)
        painter.drawEllipse(
            QtCore.QPointF(dot_x, dot_y),
            dot_radius,
            dot_radius,
        )

        title_font = self._font(
            "Segoe UI",
            min(width, height) * 0.054,
            QtGui.QFont.Weight.DemiBold,
        )
        painter.setFont(title_font)
        painter.setPen(self.c_text)
        painter.drawText(
            QtCore.QRectF(
                dot_x + dot_radius * 2.6,
                height * 0.025,
                width * 0.55,
                height * 0.10,
            ),
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"{self.title} VACUUM",
        )

    def _scale_marks(self):
        low = 10.0 ** self.min_exp
        high = 10.0 ** self.max_exp
        marks = []

        for exponent in range(self.min_exp, self.max_exp + 1):
            decade = 10.0 ** exponent
            if low <= decade <= high:
                marks.append(
                    (decade, True, f"10{self._superscript(exponent)}")
                )

            if exponent < self.max_exp:
                for mantissa in (2, 5):
                    value = mantissa * decade
                    if low <= value <= high:
                        marks.append((value, False, str(mantissa)))

        return marks

    def _draw_scale(self, painter, cx, cy, radius):
        for value, major, label in self._scale_marks():
            angle = math.radians(self._value_to_angle(value))

            r_outer = radius * 1.015
            tick_length = radius * (0.090 if major else 0.042)
            r_inner = r_outer - tick_length

            x1 = cx + r_outer * math.cos(angle)
            y1 = cy - r_outer * math.sin(angle)
            x2 = cx + r_inner * math.cos(angle)
            y2 = cy - r_inner * math.sin(angle)

            pen = QtGui.QPen(
                self.c_tick_major if major else self.c_tick_minor,
                2.2 if major else 1.15,
            )
            pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawLine(
                QtCore.QPointF(x1, y1),
                QtCore.QPointF(x2, y2),
            )

            text_radius = radius * (1.12 if major else 0.885)
            tx = cx + text_radius * math.cos(angle)
            ty = cy - text_radius * math.sin(angle)

            if major:
                font = self._font(
                    "Segoe UI",
                    min(self.width(), self.height()) * 0.041,
                    QtGui.QFont.Weight.DemiBold,
                )
                painter.setPen(self.c_text)
                box_w = max(46.0, radius * 0.28)
                box_h = max(20.0, radius * 0.13)
            else:
                font = self._font(
                    "Segoe UI",
                    min(self.width(), self.height()) * 0.027,
                )
                painter.setPen(self.c_tick_minor)
                box_w = max(24.0, radius * 0.15)
                box_h = max(17.0, radius * 0.10)

            painter.setFont(font)
            painter.drawText(
                QtCore.QRectF(
                    tx - box_w / 2,
                    ty - box_h / 2,
                    box_w,
                    box_h,
                ),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                label,
            )

    def _draw_marker(self, painter, cx, cy, radius):
        if math.isnan(self._value):
            return

        angle = math.radians(self._value_to_angle(self._value))
        display_color = self._display_color()

        r0 = radius * 0.88
        r1 = radius * 1.07

        x0 = cx + r0 * math.cos(angle)
        y0 = cy - r0 * math.sin(angle)
        x1 = cx + r1 * math.cos(angle)
        y1 = cy - r1 * math.sin(angle)

        glow = QtGui.QPen(
            QtGui.QColor(
                display_color.red(),
                display_color.green(),
                display_color.blue(),
                100,
            ),
            8.0,
        )
        glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(glow)
        painter.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))

        marker = QtGui.QPen(QtGui.QColor(239, 249, 253), 2.6)
        marker.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(marker)
        painter.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))

        dot_x = cx + radius * 0.975 * math.cos(angle)
        dot_y = cy - radius * 0.975 * math.sin(angle)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(display_color)
        painter.drawEllipse(QtCore.QPointF(dot_x, dot_y), 3.8, 3.8)

    def _draw_history(self, painter, cx, cy, radius):
        plot_rect = QtCore.QRectF(
            cx - radius * 0.58,
            cy - radius * 0.60,
            radius * 1.16,
            radius * 0.245,
        )

        # Minimal internal time-domain reference lines.
        painter.setPen(QtGui.QPen(self.c_history_grid, 1.0))
        painter.drawLine(
            QtCore.QPointF(plot_rect.left(), plot_rect.center().y()),
            QtCore.QPointF(plot_rect.right(), plot_rect.center().y()),
        )
        for fraction in (0.25, 0.50, 0.75):
            x = plot_rect.left() + plot_rect.width() * fraction
            painter.drawLine(
                QtCore.QPointF(x, plot_rect.top()),
                QtCore.QPointF(x, plot_rect.bottom()),
            )

        values = []
        for value in self._history:
            try:
                value = float(value)
                if math.isfinite(value) and value > 0.0:
                    values.append(math.log10(value))
            except Exception:
                pass

        if len(values) < 2:
            hint_font = self._font(
                "Segoe UI",
                min(self.width(), self.height()) * 0.027,
            )
            painter.setFont(hint_font)
            painter.setPen(self.c_muted)
            painter.drawText(
                plot_rect,
                QtCore.Qt.AlignmentFlag.AlignCenter,
                "BUILDING HISTORY",
            )
            return

        low = min(values)
        high = max(values)
        span = high - low
        minimum_span = 0.030
        if span < minimum_span:
            center = (high + low) * 0.5
            low = center - minimum_span * 0.5
            high = center + minimum_span * 0.5
        else:
            padding = span * 0.16
            low -= padding
            high += padding

        points = []
        count = len(values)
        for index, value in enumerate(values):
            x = plot_rect.left() + plot_rect.width() * (
                index / max(1, count - 1)
            )
            norm = clamp((value - low) / max(1e-12, high - low), 0.0, 1.0)
            y = plot_rect.bottom() - plot_rect.height() * norm
            points.append(QtCore.QPointF(x, y))

        path = QtGui.QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)

        display_color = self._display_color()

        glow_pen = QtGui.QPen(
            QtGui.QColor(
                display_color.red(),
                display_color.green(),
                display_color.blue(),
                55,
            ),
            5.0,
        )
        glow_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        glow_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glow_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        line_pen = QtGui.QPen(display_color, 1.7)
        line_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        line_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(line_pen)
        painter.drawPath(path)

        last_point = points[-1]
        painter.setPen(QtGui.QPen(display_color, 2.0))
        painter.setBrush(QtGui.QColor(4, 8, 11))
        painter.drawEllipse(last_point, 4.0, 4.0)

        time_font = self._font(
            "Segoe UI",
            min(self.width(), self.height()) * 0.022,
        )
        painter.setFont(time_font)
        painter.setPen(self.c_muted)
        painter.drawText(
            QtCore.QRectF(
                plot_rect.left(),
                plot_rect.bottom() + 2,
                70,
                18,
            ),
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"{self.history_seconds:.0f} s",
        )
        painter.drawText(
            QtCore.QRectF(
                plot_rect.right() - 40,
                plot_rect.bottom() + 2,
                40,
                18,
            ),
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "0 s",
        )

    def _draw_readout(self, painter, width, height, cx, cy, radius):
        state = self._state_name()
        display_color = self._display_color()

        if math.isnan(self._value):
            mantissa_text = "--"
            exponent_text = ""
        else:
            exponent = int(math.floor(math.log10(self._value)))
            mantissa = self._value / (10.0 ** exponent)
            mantissa_text = f"{mantissa:.2f}"
            exponent_text = f"×10{self._superscript(exponent)}"

        big_font = self._font(
            "Segoe UI",
            min(width, height) * 0.103,
            QtGui.QFont.Weight.Bold,
        )
        exponent_font = self._font(
            "Segoe UI",
            min(width, height) * 0.048,
            QtGui.QFont.Weight.DemiBold,
        )

        big_metrics = QtGui.QFontMetricsF(big_font)
        exponent_metrics = QtGui.QFontMetricsF(exponent_font)

        group_width = big_metrics.horizontalAdvance(mantissa_text)
        if exponent_text:
            group_width += exponent_metrics.horizontalAdvance(exponent_text) + 7

        x = cx - group_width / 2
        baseline = cy - radius * 0.17

        painter.setFont(big_font)
        painter.setPen(display_color)
        painter.drawText(QtCore.QPointF(x, baseline), mantissa_text)

        x += big_metrics.horizontalAdvance(mantissa_text) + 7
        if exponent_text:
            painter.setFont(exponent_font)
            painter.setPen(self.c_text)
            painter.drawText(
                QtCore.QPointF(
                    x,
                    baseline - big_metrics.height() * 0.17,
                ),
                exponent_text,
            )

        unit_font = self._font(
            "Segoe UI",
            min(width, height) * 0.038,
            QtGui.QFont.Weight.DemiBold,
        )
        painter.setFont(unit_font)
        painter.setPen(self.c_muted)
        painter.drawText(
            QtCore.QRectF(
                cx - radius * 0.50,
                baseline + radius * 0.035,
                radius,
                radius * 0.17,
            ),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self.units,
        )

        # Tesla-style GOOD / WARNING / ALARM status pill.
        state_color = self._state_color()
        pill_width = min(width * 0.35, radius * 0.90)
        pill_height = max(27.0, height * 0.080)
        pill = QtCore.QRectF(
            cx - pill_width / 2,
            min(height - pill_height - 8, cy + radius * 0.10),
            pill_width,
            pill_height,
        )

        border_color = QtGui.QColor(state_color)
        border_color.setAlpha(165)
        painter.setPen(QtGui.QPen(border_color, 1.15))
        painter.setBrush(QtGui.QColor(2, 6, 8, 235))
        painter.drawRoundedRect(
            pill,
            pill_height / 2,
            pill_height / 2,
        )

        dot_alpha = 255
        if state == "ALARM":
            dot_alpha = int(145 + 110 * abs(math.sin(self._phase)))

        dot_color = QtGui.QColor(state_color)
        dot_color.setAlpha(dot_alpha)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(
            QtCore.QPointF(
                pill.left() + pill_height * 0.68,
                pill.center().y(),
            ),
            pill_height * 0.14,
            pill_height * 0.14,
        )

        status_font = self._font(
            "Segoe UI",
            min(width, height) * 0.037,
            QtGui.QFont.Weight.DemiBold,
        )
        painter.setFont(status_font)
        painter.setPen(
            state_color if state != "NO DATA" else self.c_muted
        )
        painter.drawText(
            pill.adjusted(pill_height * 0.35, 0, 0, 0),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            state,
        )

    def paintEvent(self, _):
        painter = QtGui.QPainter(self)
        painter.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
            | QtGui.QPainter.RenderHint.SmoothPixmapTransform
        )

        width = self.width()
        height = self.height()

        background = QtGui.QLinearGradient(0, 0, 0, height)
        background.setColorAt(0.0, self.c_bg_top)
        background.setColorAt(1.0, self.c_bg_bottom)
        painter.fillRect(self.rect(), QtGui.QBrush(background))

        # Quiet center illumination; almost black at the edges.
        center_glow = QtGui.QRadialGradient(
            QtCore.QPointF(width * 0.50, height * 0.53),
            max(width, height) * 0.45,
        )
        center_glow.setColorAt(0.0, QtGui.QColor(17, 42, 53, 42))
        center_glow.setColorAt(0.58, QtGui.QColor(6, 14, 19, 16))
        center_glow.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), QtGui.QBrush(center_glow))

        self._draw_title(painter, width, height)

        cx = width * 0.50
        cy = height * 0.765
        radius = min(width * 0.405, height * 0.615)

        arc_rect = QtCore.QRectF(
            cx - radius,
            cy - radius,
            2 * radius,
            2 * radius,
        )

        start_angle = self._norm_to_angle(0.0)
        end_angle = self._norm_to_angle(1.0)
        total_span = end_angle - start_angle

        # Dark foundation and one restrained inner edge.
        self._draw_arc(
            painter,
            arc_rect,
            start_angle,
            total_span,
            QtGui.QColor(17, 24, 29, 250),
            15.0,
        )
        self._draw_arc(
            painter,
            arc_rect,
            start_angle,
            total_span,
            self.c_track,
            4.5,
        )

        warning_norm = self._value_to_norm(self.warning_at)
        alarm_norm = self._value_to_norm(self.alarm_at)

        zone_rect = arc_rect.adjusted(
            -radius * 0.035,
            -radius * 0.035,
            radius * 0.035,
            radius * 0.035,
        )

        # Threshold ranges remain visible, but only as quiet references.
        self._draw_zone(
            painter, zone_rect, 0.0, warning_norm,
            QtGui.QColor(82, 188, 214, 54), 4.5
        )
        self._draw_zone(
            painter, zone_rect, warning_norm, alarm_norm,
            QtGui.QColor(224, 166, 65, 42), 4.5
        )
        self._draw_zone(
            painter, zone_rect, alarm_norm, 1.0,
            QtGui.QColor(232, 88, 88, 42), 4.5
        )

        # The current state is the only strongly illuminated portion.
        if not math.isnan(self._value):
            value_norm = self._value_to_norm(self._value)
            active_color = self._display_color()
            active_color.setAlpha(165)
            self._draw_zone(
                painter, arc_rect, 0.0, value_norm,
                active_color, 8.0
            )

        inner_rect = arc_rect.adjusted(
            radius * 0.055,
            radius * 0.055,
            -radius * 0.055,
            -radius * 0.055,
        )
        self._draw_arc(
            painter,
            inner_rect,
            start_angle,
            total_span,
            self.c_edge,
            1.0,
            QtCore.Qt.PenCapStyle.FlatCap,
        )

        self._draw_scale(painter, cx, cy, radius)
        self._draw_history(painter, cx, cy, radius)
        self._draw_marker(painter, cx, cy, radius)
        self._draw_readout(painter, width, height, cx, cy, radius)
# ----------------------- Tiles (plot + gauges) with COLOR STYLING -----------------------
class _BeamTile(QtWidgets.QWidget):
    def __init__(self, max_points=240):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=max_points, ymin=0.0, ymax=1.0, line=QtGui.QColor(82,188,214), title="Beam (µA)")
        self.gauge = NeonRadial01(value=0.0, title="Beam Current", unit="µA",
                                  status_bands=dict(ok=(0.25,0.75), warn=(0.15,0.90)), gauge_type="beam")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        lay.setStretch(0, 1)
        lay.setStretch(1, 1)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.gauge, 1)

    # FIXED: Proper needle positioning with actual range values from calibration
    def set_display(self, value_in_unit: float, unit: str,
                    range_min: float, range_max: float, decimals: int):
        """Set display with actual range boundaries from LabVIEW"""
        try:
            val = float(value_in_unit)
            range_min = float(range_min)
            range_max = float(range_max)
        
    
            # Set the gauge to use the actual range from LabVIEW
            self.gauge.set_fullscale(range_max, unit, decimals=decimals, label_min=range_min)
            
            # Set the range indicator
            self.gauge.set_range_indicator(range_min, range_max, unit)
        
            # Calculate normalized position within the actual range
            # Handle over-range values by clamping to the range boundaries
            if val > range_max:
                normalized_value = 1.0
                self.gauge.set_over_range(True)
            elif val < range_min:
                normalized_value = 0.0
                self.gauge.set_over_range(False)
            else:
                span = range_max - range_min
                normalized_value = (
                    0.0 if span <= 0.0
                    else (val - range_min) / span
                )
                self.gauge.set_over_range(False)
        
            # Set the normalized value for needle positioning
            self.gauge.set_value(normalized_value, animated=False)
            
            # Set the big readout display
            self.gauge.set_big_readout(val, unit, decimals=decimals)
            
            # Plot: use the actual range but allow some headroom for over-range values
            self.plot.title = f"Beam ({unit})"
            self.plot.add_point(val)
            plot_max = max(range_max, val * 1.1)  # Show at least 10% beyond current value
            self.plot.set_range(range_min, plot_max)
            
        except Exception as e:
            import traceback
            traceback.print_exc()

class RFNeonGauge(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(220, 220)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.kv = 0.0
        self.fs = 80.0
        self.error_kv = float("nan")

        # Visual reference only. A full bar represents the configurable
        # fraction of RF full scale; the exact P-P value is always printed.
        self.error_bar_reference_fraction = _setting_float(
            "rf_error_bar_reference_fraction",
            default=0.10,
        )

        self._t = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        # Original gauge geometry
        self.start_deg = -140.0
        self.span_deg = 220.0

    def _tick(self):
        self._t += 0.016
        self.update()

    def set_kv(self, kv, fs=80.0):
        self.kv = max(0.0, float(kv))
        self.fs = max(1.0, float(fs))
        self.update()

    def set_error_kv(self, error_kv):
        """Set RF peak-to-peak error over the displayed history."""
        try:
            error_kv = float(error_kv)
            if not math.isfinite(error_kv):
                raise ValueError
            self.error_kv = max(0.0, error_kv)
        except (TypeError, ValueError, OverflowError):
            self.error_kv = float("nan")
        self.update()

    def draw_error_bar(self, p, cx, cy, R, s):
        """Draw a restrained Tesla-style RF peak-to-peak error bar."""
        bar_w = min(self.width() * 0.50, R * 1.08)
        bar_h = max(7.0, s * 0.022)
        bar_x = cx - bar_w / 2.0
        bar_y = cy + R * 0.50

        label_h = max(16.0, s * 0.050)
        label_rect = QtCore.QRectF(
            bar_x,
            bar_y - label_h - 4.0,
            bar_w,
            label_h,
        )

        label_font = QtGui.QFont(
            "Segoe UI",
            max(8, int(s * 0.027)),
            QtGui.QFont.Weight.DemiBold,
        )
        p.setFont(label_font)

        p.setPen(QtGui.QColor(135, 148, 156, 220))
        p.drawText(
            QtCore.QRectF(
                label_rect.left(),
                label_rect.top(),
                label_rect.width() * 0.48,
                label_rect.height(),
            ),
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "P-P ERROR",
        )

        error_text = (
            f"{self.error_kv:.2f} kV"
            if math.isfinite(self.error_kv)
            else "—"
        )
        p.setPen(QtGui.QColor(241, 245, 247, 245))
        p.drawText(
            QtCore.QRectF(
                label_rect.left() + label_rect.width() * 0.48,
                label_rect.top(),
                label_rect.width() * 0.52,
                label_rect.height(),
            ),
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            error_text,
        )

        track = QtCore.QRectF(bar_x, bar_y, bar_w, bar_h)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 28), 1.0))
        p.setBrush(QtGui.QColor(17, 22, 25, 238))
        p.drawRoundedRect(track, bar_h / 2.0, bar_h / 2.0)

        p.setPen(QtGui.QPen(QtGui.QColor(202, 214, 220, 72), 1.0))
        p.drawLine(
            QtCore.QPointF(cx, bar_y - 2.0),
            QtCore.QPointF(cx, bar_y + bar_h + 2.0),
        )

        if not math.isfinite(self.error_kv):
            return

        reference_kv = max(
            1e-9,
            self.fs * max(1e-6, self.error_bar_reference_fraction),
        )
        fraction = clamp(self.error_kv / reference_kv, 0.0, 1.0)
        error_w = min(bar_w, max(8.0, bar_w * fraction))

        segment = QtCore.QRectF(
            cx - error_w / 2.0,
            bar_y + 1.0,
            error_w,
            max(2.0, bar_h - 2.0),
        )

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(82, 188, 214, 38))
        glow_rect = segment.adjusted(-3.0, -2.0, 3.0, 2.0)
        p.drawRoundedRect(
            glow_rect,
            glow_rect.height() / 2.0,
            glow_rect.height() / 2.0,
        )

        p.setBrush(QtGui.QColor(82, 188, 214, 205))
        p.drawRoundedRect(
            segment,
            segment.height() / 2.0,
            segment.height() / 2.0,
        )

        cap_pen = QtGui.QPen(QtGui.QColor(235, 242, 245, 225), 1.25)
        cap_pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(cap_pen)
        cap_top = bar_y - 1.0
        cap_bottom = bar_y + bar_h + 1.0
        p.drawLine(
            QtCore.QPointF(segment.left(), cap_top),
            QtCore.QPointF(segment.left(), cap_bottom),
        )
        p.drawLine(
            QtCore.QPointF(segment.right(), cap_top),
            QtCore.QPointF(segment.right(), cap_bottom),
        )

    def norm(self):
        return clamp(self.kv / self.fs, 0.0, 1.0)

    def angle_deg(self):
        # Same visual sweep as your original gauge: 0 at lower-left, full-scale at right.
        return self.start_deg - self.span_deg * self.norm()

    @staticmethod
    def interp(c1, c2, t):
        t = clamp(t, 0.0, 1.0)
        return QtGui.QColor(int(c1.red() + (c2.red()-c1.red())*t),
                            int(c1.green() + (c2.green()-c1.green())*t),
                            int(c1.blue() + (c2.blue()-c1.blue())*t))

    def current_color(self):
        return QtGui.QColor(82, 188, 214)

    def draw_grid(self, p, rect):
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 12), 1))
        for x in range(0, rect.width(), 20):
            p.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 20):
            p.drawLine(0, y, rect.width(), y)

    def draw_annulus(self, p, cx, cy, r_in, r_out, brush):
        outer = QtGui.QPainterPath(); outer.addEllipse(QtCore.QRectF(cx-r_out, cy-r_out, 2*r_out, 2*r_out))
        inner = QtGui.QPainterPath(); inner.addEllipse(QtCore.QRectF(cx-r_in, cy-r_in, 2*r_in, 2*r_in))
        p.save(); p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(QtGui.QBrush(brush)); p.drawPath(outer.subtracted(inner)); p.restore()

    def draw_arc(self, p, cx, cy, radius, start, span, color, width):
        pen = QtGui.QPen(color, width)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QtCore.QRectF(cx-radius, cy-radius, 2*radius, 2*radius), int(start*16), int(span*16))

    def draw_ticks_labels(self, p, cx, cy, R):
        r_out, r_major_in, r_minor_in = R*0.94, R*0.86, R*0.90
        for i in range(0, 101, 5):
            frac = i / 100.0
            a_deg = self.start_deg - self.span_deg * frac
            a = math.radians(a_deg)
            x1, y1 = cx + r_out*math.cos(a), cy - r_out*math.sin(a)
            x2, y2 = cx + (r_major_in if i % 25 == 0 else r_minor_in)*math.cos(a), cy - (r_major_in if i % 25 == 0 else r_minor_in)*math.sin(a)
            p.setPen(QtGui.QPen(QtGui.QColor(235, 241, 244, 220 if i % 25 == 0 else 120), 2.6 if i % 25 == 0 else 1.5))
            p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

        f = QtGui.QFont("Segoe UI", max(9, int(min(self.width(), self.height())*0.030)))
        for i in range(11):
            frac = i / 10.0
            a_deg = self.start_deg - self.span_deg * frac
            a = math.radians(a_deg)
            r_label = R*0.70
            tx, ty = cx + r_label*math.cos(a), cy - r_label*math.sin(a)
            s = f"{self.fs*frac:.2f}"
            if i == 5:
                fb = QtGui.QFont(f); fb.setWeight(QtGui.QFont.Weight.Bold); p.setFont(fb)
                p.setPen(QtGui.QColor(241, 245, 247, 245))
            else:
                p.setFont(f); p.setPen(QtGui.QColor(180, 191, 198, 205))
            fm = QtGui.QFontMetricsF(p.font())
            p.drawText(QtCore.QRectF(tx-fm.horizontalAdvance(s)/2, ty-fm.height()/2,
                                     fm.horizontalAdvance(s), fm.height()), QtCore.Qt.AlignmentFlag.AlignCenter, s)

    def draw_needle(self, p, cx, cy, R):
        # This is the fixed part. It uses the same coordinate convention as the working beam gauge,
        # but the value is set directly from RF kV/fs.
        a = math.radians(self.angle_deg())
        dirx, diry = math.cos(a), -math.sin(a)
        nx, ny = -diry, dirx
        r_start = R * 0.12
        r_tip = R * 0.76
        base_w, tip_w = 8.0, 2.0
        baseL = QtCore.QPointF(cx + dirx*r_start + nx*(base_w/2), cy + diry*r_start + ny*(base_w/2))
        baseR = QtCore.QPointF(cx + dirx*r_start - nx*(base_w/2), cy + diry*r_start - ny*(base_w/2))
        tipL  = QtCore.QPointF(cx + dirx*r_tip + nx*(tip_w/2),   cy + diry*r_tip + ny*(tip_w/2))
        tipR  = QtCore.QPointF(cx + dirx*r_tip - nx*(tip_w/2),   cy + diry*r_tip - ny*(tip_w/2))

        path = QtGui.QPainterPath()
        for pt in (baseL, tipL, tipR, baseR):
            path.moveTo(pt) if path.isEmpty() else path.lineTo(pt)
        path.closeSubpath()

        color = self.current_color()
        glow = QtGui.QColor(color.red(), color.green(), color.blue(), 115)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawPath(path)
        grad = QtGui.QLinearGradient(tipL, baseR)
        grad.setColorAt(0.0, QtGui.QColor(255, 255, 255))
        grad.setColorAt(0.25, color)
        grad.setColorAt(1.0, QtGui.QColor(26, 33, 37))
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(path)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 230), 2.0, cap=QtCore.Qt.PenCapStyle.RoundCap))
        p.drawLine(tipL, baseL)
        p.drawLine(tipR, baseR)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                         QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        s = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        R = s * 0.46

        bg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R*1.5)
        bg.setColorAt(0.0, QtGui.QColor(18, 21, 23))
        bg.setColorAt(0.7, QtGui.QColor(10, 12, 14))
        bg.setColorAt(1.0, QtGui.QColor(5, 6, 8))
        p.fillRect(rect, QtGui.QBrush(bg))
        self.draw_grid(p, rect)

        # Quiet high-range references; no permanent amber/red decoration.
        zone_dim = QtGui.QColor(135, 146, 154, 42)
        self.draw_arc(
            p, cx, cy, R * 0.90,
            self.start_deg - self.span_deg * 0.70,
            -(self.span_deg * (0.85 - 0.70)), zone_dim, 3.5
        )
        self.draw_arc(
            p, cx, cy, R * 0.90,
            self.start_deg - self.span_deg * 0.85,
            -(self.span_deg * (1.00 - 0.85)), zone_dim, 3.5
        )

        color = self.current_color()
        # Corona annular band
        ring = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R*0.56)
        ring.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
        ring.setColorAt(0.64, QtGui.QColor(color.red(), color.green(), color.blue(), 132))
        ring.setColorAt(0.8, self.interp(color, QtGui.QColor(24, 32, 36), 0.62))
        ring.setColorAt(1.0, self.interp(color, QtGui.QColor(7, 10, 12), 0.82))
        self.draw_annulus(p, cx, cy, R*0.36, R*0.56, ring)
        p.setPen(QtGui.QPen(QtGui.QColor(color.red(), color.green(), color.blue(), 150), 2.0)); p.drawEllipse(QtCore.QPointF(cx, cy), R*0.56, R*0.56)

        # Center rings
        p.setPen(QtGui.QPen(QtGui.QColor(color.red(), color.green(), color.blue(), 150), 3.5)); p.drawEllipse(QtCore.QPointF(cx, cy), R*0.40, R*0.40)
        p.setPen(QtGui.QPen(QtGui.QColor(215, 224, 229, 135), 1.8)); p.drawEllipse(QtCore.QPointF(cx, cy), R*0.37, R*0.37)
        center_grad = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R*0.35)
        center_grad.setColorAt(0.0, QtGui.QColor(color.red()//4, color.green()//4, color.blue()//4))
        center_grad.setColorAt(1.0, QtGui.QColor(0, 0, 0))
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(center_grad); p.drawEllipse(QtCore.QPointF(cx, cy), R*0.35, R*0.35)

        # Value arc
        rect_arc = QtCore.QRectF(cx-R*0.76, cy-R*0.76, 2*R*0.76, 2*R*0.76)
        pen_bg = QtGui.QPen(QtGui.QColor(83, 96, 104, 85), 7); pen_bg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg); p.drawArc(rect_arc, int(self.start_deg*16), int(-self.span_deg*16))
        sweep = -self.span_deg * self.norm()
        pen_fg = QtGui.QPen(color, 6.5); pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_fg); p.drawArc(rect_arc, int(self.start_deg*16), int(sweep*16))
        pen_glow = QtGui.QPen(color, 9.0); pen_glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setOpacity(0.18); p.setPen(pen_glow); p.drawArc(rect_arc, int(self.start_deg*16), int(sweep*16)); p.setOpacity(1.0)

        self.draw_ticks_labels(p, cx, cy, R)
        self.draw_needle(p, cx, cy, R)

        # Readout over needle, same as original
        p.setPen(QtGui.QPen(color, 2))
        p.setFont(QtGui.QFont("Segoe UI", max(14, int(s*0.08)), QtGui.QFont.Weight.Bold))
        big = f"{self.kv:.2f}"
        br = QtGui.QFontMetricsF(p.font()).boundingRect(big)
        p.drawText(QtCore.QPointF(cx-br.width()/2, cy+br.height()/4), big)
        p.setPen(QtGui.QColor(150, 210, 255))
        unit_font = QtGui.QFont("Segoe UI", max(10, int(s*0.045)))
        p.setFont(unit_font)
        unit_fm = QtGui.QFontMetricsF(unit_font)
        p.drawText(QtCore.QRectF(cx - 50, cy + br.height()*0.42, 100, unit_fm.height()+6),
                   QtCore.Qt.AlignmentFlag.AlignCenter, "kV")

        self.draw_error_bar(p, cx, cy, R, s)

        # Small center dot
        p.setBrush(QtGui.QColor(color.red(), color.green(), color.blue(), int(150 + 100*math.sin(self._t*3))))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)


class _PowerTile(QtWidgets.QWidget):
    def __init__(self, max_points=240):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=max_points, ymin=0.0, ymax=float(app_settings.rf_gauge_max_kv),
                                    line=QtGui.QColor(82,188,214), title="RF Power (kV)")
        # Replaced the old RF gauge with the fixed standalone RF gauge.
        # This keeps the neon style but uses a direct RF kV -> needle angle calculation.
        self.gauge = RFNeonGauge()
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding
        )
        lay.setStretch(0, 1)
        lay.setStretch(1, 1)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.gauge, 1)

    def set_kV(self, kv: float, fs_kV: float):
        kv = max(0.0, float(kv)); fs_kV = max(1.0, float(fs_kV))
        self.gauge.set_kv(kv, fs_kV)
        self.plot.add_point(kv)
        self.plot.set_range(0.0, fs_kV)

    def set_error_kV(self, error_kV: float):
        self.gauge.set_error_kv(error_kV)

class _VacTile(QtWidgets.QWidget):
    """One full-width vacuum semicircle with its history embedded inside."""

    def __init__(
        self,
        title="TP",
        max_points=240,
        history_seconds=60.0,
        warning_at=1.0e-5,
        alarm_at=1.0e-4,
    ):
        super().__init__()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)

        self.gauge = LogVacuumGauge(
            title=title,
            min_exp=app_settings.vac_min_exp,
            max_exp=app_settings.vac_max_exp,
            units=app_settings.vac_units,
            warning_at=warning_at,
            alarm_at=alarm_at,
            max_points=max_points,
            history_seconds=history_seconds,
        )

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        lay.addWidget(self.gauge, 1)

    def set_mbar(self, mbar: float):
        value = max(1e-12, float(mbar))
        self.gauge.set_value(value)


class MonitoringErrorTable(QtWidgets.QFrame):
    """Minimal full-height side table for current values and stability."""

    ROWS = (
        "Beam Current",
        "RF Power",
        "TP1 Vacuum",
        "TP2 Vacuum",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MonitoringErrorPanel")
        self.setMinimumWidth(350)
        self.setMaximumWidth(430)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.setStyleSheet("""
            QFrame#MonitoringErrorPanel {
                background: #080b0e;
                border: 1px solid #2a3339;
                border-radius: 15px;
            }
            QLabel#ErrorTitle {
                color: #f1f4f6;
                font-size: 15px;
                font-weight: 650;
                letter-spacing: 1px;
            }
            QLabel#ErrorSubTitle {
                color: #7f8b93;
                font-size: 10px;
                letter-spacing: 0.5px;
            }
            QFrame#AccentLine {
                background: #52bcd6;
                border: none;
                border-radius: 1px;
            }
            QTableWidget {
                background: #050709;
                alternate-background-color: #080b0e;
                color: #eef2f4;
                border: 1px solid #20282d;
                border-radius: 10px;
                gridline-color: #1b2227;
                selection-background-color: transparent;
                font-size: 11px;
            }
            QHeaderView::section {
                background: #0b0f12;
                color: #89959d;
                border: none;
                border-right: 1px solid #20282d;
                border-bottom: 1px solid #2b353b;
                padding: 8px 4px;
                font-size: 9px;
                font-weight: 650;
                letter-spacing: 0.5px;
            }
            QTableWidget::item {
                padding: 8px 7px;
                border-bottom: 1px solid #171d21;
            }
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(13, 14, 13, 13)
        root.setSpacing(9)

        title = QtWidgets.QLabel("MONITOR STABILITY")
        title.setObjectName("ErrorTitle")
        root.addWidget(title)

        subtitle = QtWidgets.QLabel(
            "CURRENT VALUE  ·  PEAK-TO-PEAK / DISPLAYED HISTORY"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("ErrorSubTitle")
        root.addWidget(subtitle)

        accent = QtWidgets.QFrame()
        accent.setObjectName("AccentLine")
        accent.setFixedHeight(2)
        accent.setFixedWidth(54)
        root.addWidget(accent)

        self.table = QtWidgets.QTableWidget(len(self.ROWS), 3)
        self.table.setHorizontalHeaderLabels(
            ["VARIABLE", "VALUE", "P-P ERROR"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QtWidgets.QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QtWidgets.QTableWidget.SelectionMode.NoSelection
        )
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        hh.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        hh.setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Fixed
        )
        self.table.setMinimumHeight(292)
        self.table.setMaximumHeight(318)

        for row, name in enumerate(self.ROWS):
            name_item = QtWidgets.QTableWidgetItem(name.upper())
            name_item.setForeground(QtGui.QColor(225, 231, 234))

            value_item = QtWidgets.QTableWidgetItem("—")
            value_item.setForeground(QtGui.QColor(166, 178, 185))
            value_item.setTextAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter
            )

            error_item = QtWidgets.QTableWidgetItem("—")
            error_item.setForeground(QtGui.QColor(255, 255, 255))
            error_item.setTextAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight
                | QtCore.Qt.AlignmentFlag.AlignVCenter
            )

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, value_item)
            self.table.setItem(row, 2, error_item)

        root.addWidget(self.table, 0)
        root.addStretch(1)

        note_line = QtWidgets.QFrame()
        note_line.setFixedHeight(1)
        note_line.setStyleSheet(
            "background:#1d252a; border:none;"
        )
        root.addWidget(note_line)

        note = QtWidgets.QLabel(
            "P-P ERROR = MAXIMUM − MINIMUM\n"
            "WINDOW = DISPLAYED HISTORY"
        )
        note.setStyleSheet(
            "color:#6f7b83; font-size:9px; letter-spacing:0.5px;"
        )
        root.addWidget(note)

    def set_row(self, row: int, value_text: str, error_text: str):
        if not 0 <= int(row) < self.table.rowCount():
            return
        self.table.item(int(row), 1).setText(value_text)
        self.table.item(int(row), 2).setText(error_text)
        self.table.item(int(row), 2).setForeground(
            QtGui.QColor(255, 255, 255)
        )

# ----------------------- Main Window -----------------------
class VacuumBeamMonitoringWindow(QtWidgets.QWidget):
    def __init__(self, data_queue, plot_rate_hz=None, seconds=None, bg_image=None):
        super().__init__()
        self.setWindowTitle("CNL — Vacuum / Beam / RF Monitoring")
        self.setMinimumSize(1260, 760)
        self.setStyleSheet("""
            QWidget { background-color: #07090b; color: #f2f4f7; }
            QLabel { background: transparent; }
            QSplitter::handle {
                background: transparent;
                width: 10px;
            }
        """)

        self._q = data_queue
        self.scaler = Scaler()
        self._last_data_ts = 0.0
        self._requested_beam_range_idx = None

        # Kept for constructor compatibility with main_control_gui.py. The
        # Tesla skin intentionally does not draw the former background image.
        self._bg_image = bg_image

        self._plot_fps = (
            float(plot_rate_hz)
            if plot_rate_hz
            else float(app_settings.plot_fps)
        )
        self._plot_fps = max(1.0, self._plot_fps)
        self._history_seconds = (
            max(1.0, float(seconds)) if seconds else 60.0
        )
        self._history_points = max(
            30,
            int(self._plot_fps * self._history_seconds),
        )

        # Standardized buffers used by the side error table.
        self._monitor_buffers = {
            "beam_nA": collections.deque(maxlen=self._history_points),
            "rf_kV": collections.deque(maxlen=self._history_points),
            "tp1_mbar": collections.deque(maxlen=self._history_points),
            "tp2_mbar": collections.deque(maxlen=self._history_points),
        }
        self._latest_monitor_values = {
            key: float("nan") for key in self._monitor_buffers
        }
        self._beam_table_unit = "nA"
        self._beam_table_scale = 1.0
        self._beam_table_decimals = 2

        # top bar
        root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(18,16,18,18); root.setSpacing(10)
        topbar = QtWidgets.QHBoxLayout(); topbar.setContentsMargins(0,0,0,0); topbar.setSpacing(8)
        self.dot = StatusDot(); self.status_lbl = QtWidgets.QLabel("OFFLINE")
        self.status_lbl.setStyleSheet("color:#8e939b; font-size:11px; font-weight:600; letter-spacing:1px;")
        topbar.addWidget(self.dot); topbar.addWidget(self.status_lbl); topbar.addStretch(1)
        root.addLayout(topbar)

        # Real CNL thresholds can be defined in app_settings.py. Generic
        # defaults preserve the visual prototype until lab limits are entered.
        tp1_warning = _setting_float(
            "tp1_vac_warning_mbar",
            "vac_warning_mbar",
            default=1.0e-5,
        )
        tp1_alarm = _setting_float(
            "tp1_vac_alarm_mbar",
            "vac_alarm_mbar",
            default=1.0e-4,
        )
        tp2_warning = _setting_float(
            "tp2_vac_warning_mbar",
            "vac_warning_mbar",
            default=1.0e-5,
        )
        tp2_alarm = _setting_float(
            "tp2_vac_alarm_mbar",
            "vac_alarm_mbar",
            default=1.0e-4,
        )

        # tiles inside boxes
        self.beam = _BeamTile(max_points=self._history_points)
        self.rf = _PowerTile(max_points=self._history_points)
        self.tp2 = _VacTile(
            "TP2",
            max_points=self._history_points,
            history_seconds=self._history_seconds,
            warning_at=tp2_warning,
            alarm_at=tp2_alarm,
        )
        self.tp1 = _VacTile(
            "TP1",
            max_points=self._history_points,
            history_seconds=self._history_seconds,
            warning_at=tp1_warning,
            alarm_at=tp1_alarm,
        )

        self.error_summary = MonitoringErrorTable()

        monitor_area = QtWidgets.QWidget()
        monitor_area.setStyleSheet("background: transparent;")
        grid = QtWidgets.QGridLayout(monitor_area)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        self.beam_card = HudCard(self.beam)
        self.rf_card = HudCard(self.rf)
        self.tp2_card = HudCard(self.tp2)
        self.tp1_card = HudCard(self.tp1)

        for card in (
            self.beam_card,
            self.rf_card,
            self.tp2_card,
            self.tp1_card,
        ):
            card.setMinimumSize(390, 285)
            card.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )

        grid.addWidget(self.beam_card, 0, 0)
        grid.addWidget(self.rf_card, 0, 1)
        grid.addWidget(self.tp2_card, 1, 0)
        grid.addWidget(self.tp1_card, 1, 1)

        self.content_splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Horizontal
        )
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self.error_summary)
        self.content_splitter.addWidget(monitor_area)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([380, 1160])
        root.addWidget(self.content_splitter, 1)

        # overlay debug
        self._overlay_on = False
        self._overlay = QtWidgets.QLabel(self); self._overlay.setVisible(False)
        self._overlay.setStyleSheet("QLabel { color:#bfe6ff; background:rgba(20,30,45,120); "
                                    "border:1px solid rgba(160,200,255,90); padding:6px; "
                                    "font-family:Consolas, Fira Mono, monospace; }")
        self._overlay.move(10, 32)

        # timers
        self._timer = QtCore.QTimer(
            self,
            interval=int(1000.0 / self._plot_fps),
        )
        self._timer.timeout.connect(self._on_timer); self._timer.start()

        self._hb = QtCore.QTimer(self, interval=250)
        self._hb.timeout.connect(self._heartbeat); self._hb.start()

    # -- Safely pull vac, beam, rf from a normalized packet dict --
    def _extract_values(self, pkt):
        vac = None; beam_raw = None; rf_kv = None
        if isinstance(pkt, dict):
            v = pkt.get("vacuum")
            if isinstance(v, (list, tuple)) and len(v) >= 5:
                vac = list(v[:5])
            bc = pkt.get("beam_current")
            if bc is not None:
                beam_raw = float(bc[0] if isinstance(bc, (list, tuple)) else bc)
            rv = pkt.get("rf_power_kv")
            if rv is not None:
                rf_kv = float(rv[0] if isinstance(rv, (list, tuple)) else rv)
        return vac, beam_raw, rf_kv


    def _drain_latest(self):
        """
        Drain to the newest packet.

        Compatible with deque, queue.Queue, and multiprocessing.Queue, matching
        the communication pattern used by the other real CNL monitor windows.
        """
        q = self._q
        latest = None

        if q is None:
            return None

        if hasattr(q, "popleft"):
            try:
                while q:
                    latest = q.popleft()
            except Exception:
                pass
            return latest

        get_nowait = getattr(q, "get_nowait", None)
        if callable(get_nowait):
            while True:
                try:
                    latest = get_nowait()
                except Exception:
                    break
            return latest

        get_nonblocking = getattr(q, "get", None)
        if callable(get_nonblocking):
            while True:
                try:
                    latest = get_nonblocking(False)
                except Exception:
                    break
            return latest

        return None

    def request_beam_range_change(self, idx: int, bitmask_u64=None):
        """Follow a range selected from the main CNL Beam Range dialog."""
        try:
            self._requested_beam_range_idx = max(0, int(idx))
        except Exception:
            self._requested_beam_range_idx = None

    @staticmethod
    def _peak_to_peak(buffer):
        values = []
        for value in buffer:
            try:
                value = float(value)
                if math.isfinite(value):
                    values.append(value)
            except Exception:
                pass

        if len(values) < 2:
            return float("nan")
        return max(values) - min(values)

    def _record_monitor_value(self, key, value):
        try:
            value = float(value)
            if not math.isfinite(value):
                return
        except Exception:
            return

        if key not in self._monitor_buffers:
            return

        self._monitor_buffers[key].append(value)
        self._latest_monitor_values[key] = value

    @staticmethod
    def _format_or_dash(value, formatter):
        try:
            value = float(value)
            if not math.isfinite(value):
                return "—"
            return formatter(value)
        except Exception:
            return "—"

    def _refresh_error_summary(self):
        beam_value_nA = self._latest_monitor_values["beam_nA"]
        beam_error_nA = self._peak_to_peak(
            self._monitor_buffers["beam_nA"]
        )
        beam_scale = float(self._beam_table_scale)
        beam_decimals = int(self._beam_table_decimals)
        beam_unit = self._beam_table_unit

        self.error_summary.set_row(
            0,
            self._format_or_dash(
                beam_value_nA,
                lambda value: (
                    f"{value * beam_scale:.{beam_decimals}f} "
                    f"{beam_unit}"
                ),
            ),
            self._format_or_dash(
                beam_error_nA,
                lambda value: (
                    f"{value * beam_scale:.{beam_decimals}f} "
                    f"{beam_unit}"
                ),
            ),
        )

        rf_value = self._latest_monitor_values["rf_kV"]
        rf_error = self._peak_to_peak(self._monitor_buffers["rf_kV"])
        self.rf.set_error_kV(rf_error)
        self.error_summary.set_row(
            1,
            self._format_or_dash(
                rf_value,
                lambda value: f"{value:.2f} kV",
            ),
            self._format_or_dash(
                rf_error,
                lambda value: f"{value:.2f} kV",
            ),
        )

        for row, key in ((2, "tp1_mbar"), (3, "tp2_mbar")):
            value = self._latest_monitor_values[key]
            error = self._peak_to_peak(self._monitor_buffers[key])
            self.error_summary.set_row(
                row,
                self._format_or_dash(
                    value,
                    lambda item: f"{item:.2e} mbar",
                ),
                self._format_or_dash(
                    error,
                    lambda item: f"{item:.2e} mbar",
                ),
            )

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() == QtCore.Qt.Key.Key_D:
            self._overlay_on = not self._overlay_on
            self._overlay.setVisible(self._overlay_on)
        super().keyPressEvent(e)

    def _heartbeat(self):
        ok = (time.time() - self._last_data_ts) < 1.0
        self.dot.set_ok(ok)
        self.status_lbl.setText("LIVE" if ok else "OFFLINE")
        self.status_lbl.setStyleSheet(
            "color:#52bcd6; font-size:11px; font-weight:600; letter-spacing:1px;" if ok
            else "color:#8e939b; font-size:11px; font-weight:600; letter-spacing:1px;"
        )

    def _on_timer(self):
        # Drain to newest using the real CNL queue-compatible path.
        last = self._drain_latest()

        if last is None or not isinstance(last, dict):
            return  # let heartbeat make it red if stale

        # ✅ We have a packet
        self._last_data_ts = time.time()
    
        # Get beam range index and value separately
        beam_range_idx = last.get(
            "beam_range_idx",
            last.get("range_idx"),
        )
    
        self.dot.set_ok(True)
        self.status_lbl.setText("LIVE")
        self.status_lbl.setStyleSheet("color:#52bcd6; font-size:11px; font-weight:600; letter-spacing:1px;")

        try:
            vac = last.get("vacuum")
            rf = last.get("rf_power_kv")
            if rf is None:
                rf = last.get("rf_kv")
            bv = last.get("beam_current")

            # normalize list-style beam_current
            if isinstance(bv, (list, tuple)):
                bv = bv[0]

            # vacuum fallbacks
            if vac is None:
                vac_try = [last.get(f"vac{i}", None) for i in range(1, 6)]
                if vac_try[0] is not None and vac_try[1] is not None:
                    vac = vac_try

            # channels tail fallback: … vac1..vac5, beamV
            if bv is None and isinstance(last.get("channels"), (list, tuple)):
                ch = last["channels"]
                if len(ch) >= 6:
                    if vac is None:
                        vac = ch[-6:-1]
                    bv  = ch[-1]

            # other legacy beam keys
            if bv is None:
                bv = last.get("beam_v_raw")
            if bv is None:
                bv = last.get("beam")

            # Vacuum → mbar
            if isinstance(vac, (list, tuple)) and len(vac) >= 2:
                tp1_mbar = self.scaler.raw_to_eng("vac1", vac[0])
                tp2_mbar = self.scaler.raw_to_eng("vac2", vac[1])
                self.tp1.set_mbar(tp1_mbar)
                self.tp2.set_mbar(tp2_mbar)
                self._record_monitor_value("tp1_mbar", tp1_mbar)
                self._record_monitor_value("tp2_mbar", tp2_mbar)

            # RF raw value -> engineering kV through the real CNL scaler.
            if rf is not None:
                rf_eng = self.scaler.raw_to_eng(
                    "rf_power_kv",
                    float(rf),
                )
                self.rf.set_kV(
                    float(rf_eng),
                    float(app_settings.rf_gauge_max_kv),
                )
                self._record_monitor_value("rf_kV", rf_eng)



            # Beam: V -> nA (per range), then unit-aware display with actual range boundaries
            if bv is not None:
                # Follow the packet range first; otherwise follow the main
                # Beam Range dialog; finally use beam_cal automatic selection.
                if beam_range_idx is not None:
                    idx = int(beam_range_idx)
                elif self._requested_beam_range_idx is not None:
                    idx = int(self._requested_beam_range_idx)
                else:
                    idx = int(beam_cal.select_index_for_pkt(last))

                ranges = list(beam_cal.cfg.ranges)
                if not ranges:
                    raise RuntimeError("beam_cal.cfg.ranges is empty")

                idx = max(0, min(idx, len(ranges) - 1))
                rng = ranges[idx]

                # Use the calibrated final curve point as the displayed range
                # maximum, preserving the previous real-monitor behavior.
                curve = getattr(rng, "curve", None)
                if curve:
                    range_max_nA = float(curve[-1][1])
                else:
                    range_max_nA = float(rng.fs_nA)

                # For beam current, range typically starts from 0
                range_min_nA = 0.0

            
                # choose unit from FS, convert value to that unit
                fs_nA = float(rng.fs_nA)
                unit, scale = beam_cal.unit_for_fs(fs_nA)          # pA/nA/µA selection
                i_nA = beam_cal.volts_to_nA(float(bv), idx)        # volts → nA
                i_disp  = i_nA * scale                              # value in chosen unit
            
                # Convert range boundaries to display units
                range_min_disp = range_min_nA * scale
                range_max_disp = range_max_nA * scale


                # compact decimals per unit
                if unit == "pA":
                    dec = 0
                elif unit == "nA":
                    dec = 1 if range_max_disp < 10 else 0  # 1 decimal for small nA, 0 for larger
                else:  # uA
                    dec = 2

                # Update beam tile with actual range boundaries
                self.beam.set_display(
                    i_disp,
                    unit,
                    range_min_disp,
                    range_max_disp,
                    dec,
                )

                self._beam_table_unit = unit
                self._beam_table_scale = scale
                self._beam_table_decimals = dec
                self._record_monitor_value("beam_nA", i_nA)

                if self._overlay_on:
                    self._overlay.setText(
                        f"raw V: {float(bv):+.3f}\n"
                        f"Range idx: {idx} ({range_min_nA:.1e}-{range_max_nA:.1e} nA)\n"
                        f"Display: {range_min_disp:.3f}-{range_max_disp:.3f} {unit}\n"
                        f"I: {i_nA:.3f} nA  disp: {i_disp:.3f} {unit}\n"
                        f"FS: {fs_nA:.3f} nA  scale: {scale}\n"
                        f"Normalized: {(i_disp - range_min_disp) / (range_max_disp - range_min_disp):.3f}"
                    )
                    self._overlay.adjustSize()

            self._refresh_error_summary()

        except Exception as e:
            # Keep communication status live; log parsing issues for debugging
            print("[Vac/Beam] parse error:", repr(e))
            import traceback
            traceback.print_exc()

