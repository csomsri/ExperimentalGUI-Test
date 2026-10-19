# -*- coding: utf-8 -*-
"""
Created on Tue Oct 28 06:49:18 2025

@author: clasa
"""

# -*- coding: utf-8 -*-
"""
VacuumBeamMonitoringWindow.py — 2×2 neon HUD with CORONA STYLING + status light
Enhanced to:
- Auto-scale Beam readout to pA / nA / µA (no long zeros)
- Show decade-style tick labels (e.g., 0.3 → 3 nA for range 300 pA–3 nA)
- Keep gauge/plot sweep starting at 0 (no protocol/calibration changes)
"""

import time, math, collections
from PyQt6 import QtCore, QtGui, QtWidgets

# Import your local modules (make sure these exist)
try:
    from beam_cal import beam_cal            # singleton: .cfg, .select_index_for_pkt(), .volts_to_nA()
    from scaling import Scaler               # PV -> engineering units (mbar) with TP piecewise fit
    from app_settings import app_settings
except ImportError:
    # Fallback dummy classes for testing
    class beam_cal:
        cfg = type('obj', (object,), {'ranges': [type('obj', (object,), {'fs_nA': 1000})()]})()
        @staticmethod
        def select_index_for_pkt(pkt): return 0
        @staticmethod
        def volts_to_nA(volts, idx): return volts * 100
        @staticmethod
        def unit_for_fs(fs_nA): return "nA", 1.0
    
    class Scaler:
        def raw_to_eng(self, sensor, value): return value * 0.001
    
    class app_settings:
        rf_gauge_max_kv = 10.0
        vac_min_exp = -7
        vac_max_exp = -2
        vac_units = "mbar"
        plot_fps = 30

# ----------------------- tiny helpers -----------------------
def clamp(v, a, b): return a if v < a else b if v > b else v

# ----------------------- CORONA STYLE MANAGER -----------------------
class CoronaStyleManager:
    """Manages different corona styles for gauges"""
    
    STYLES = {
        "beam": {
            "name": "Beam Current",
            "corona_mode": "color",
            "base_color": QtGui.QColor(160, 100, 255),  # Purple/blue
            "value_colors": [
                (0.0, QtGui.QColor(0, 255, 0)),      # Green
                (0.3, QtGui.QColor(255, 255, 0)),    # Yellow
                (0.7, QtGui.QColor(255, 165, 0)),    # Orange
                (1.0, QtGui.QColor(255, 0, 0))       # Red
            ]
        },
        "rf_power": {
            "name": "RF Power", 
            "corona_mode": "color",
            "base_color": QtGui.QColor(255, 190, 70),  # Amber/gold
            "value_colors": [
                (0.0, QtGui.QColor(0, 200, 255)),    # Blue
                (0.5, QtGui.QColor(255, 255, 0)),    # Yellow
                (1.0, QtGui.QColor(255, 50, 50))     # Red
            ]
        },
        "vacuum": {
            "name": "Vacuum",
            "corona_mode": "color", 
            "base_color": QtGui.QColor(70, 210, 255),  # Cyan
            "value_colors": [
                (0.0, QtGui.QColor(50, 200, 100)),   # Green (good vacuum)
                (0.7, QtGui.QColor(255, 200, 50)),   # Yellow
                (1.0, QtGui.QColor(255, 80, 80))     # Red (poor vacuum)
            ]
        }
    }
    
    @classmethod
    def get_style(cls, gauge_type):
        return cls.STYLES.get(gauge_type, cls.STYLES["beam"])

# ----------------------- status dot -------------------------
class StatusDot(QtWidgets.QWidget):
    def __init__(self, color_ok=QtGui.QColor(60,220,120), color_bad=QtGui.QColor(230,90,80), parent=None):
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

        # glossy glass fill
        glass = QtGui.QLinearGradient(rf.topLeft(), rf.bottomLeft())
        glass.setColorAt(0.00, QtGui.QColor(255, 255, 255, 18))
        glass.setColorAt(1.00, QtGui.QColor(255, 255, 255,  6))
        p.fillPath(path, glass)

        # outer glow + thin border
        p.setPen(QtGui.QPen(QtGui.QColor(30, 150, 220, 70), 8))
        p.drawPath(path)
        p.setPen(QtGui.QPen(QtGui.QColor(100, 210, 255, 90), 1.3))
        p.drawPath(path)

# ----------------------- ENHANCED mini time-series plot with NEON STYLING -----------------------
class TimeSeriesPlot(QtWidgets.QWidget):
    def __init__(self, parent=None, max_points=240, ymin=0.0, ymax=100.0, line=None, title=""):
        super().__init__(parent)
        self.setMinimumSize(260, 180)
        self.max_points = int(max_points)
        self._buf = collections.deque(maxlen=self.max_points)
        self._ymin, self._ymax = float(ymin), float(ymax)
        self.title = title

        # Enhanced neon styling
        self.col_bg    = QtGui.QColor(8, 15, 25)  # Darker background like gauges
        self.col_grid  = QtGui.QColor(100, 200, 255, 25)  # Softer grid
        self.col_axes  = QtGui.QColor(120, 220, 255, 80)  # Brighter axes
        self.col_title = QtGui.QColor(185, 215, 255, 220)  # Enhanced title
        self.col_line  = QtGui.QColor(line) if line else QtGui.QColor(120, 240, 255)
        self.col_fill  = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 40)
        
        # Glow effect colors
        self.col_glow = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 80)
        
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
        bg_gradient.setColorAt(0.0, QtGui.QColor(12, 20, 32))
        bg_gradient.setColorAt(1.0, QtGui.QColor(5, 10, 18))
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
            alpha = 40 if i % 2 == 0 else 25  # Alternating opacity
            p.setPen(QtGui.QPen(QtGui.QColor(100, 180, 255, alpha), 0.8))
            p.drawLine(0, int(h*(i/rows)), w, int(h*(i/rows)))
        for j in range(1, cols):
            alpha = 40 if j % 2 == 0 else 25
            p.setPen(QtGui.QPen(QtGui.QColor(100, 180, 255, alpha), 0.8))
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
                                                 self.col_fill.blue(), 60))
        fill_gradient.setColorAt(1.0, QtGui.QColor(self.col_fill.red(), self.col_fill.green(), 
                                                 self.col_fill.blue(), 10))
        p.setBrush(fill_gradient)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawPolygon(poly)

        # Glow effect behind the line
        glow_pen = QtGui.QPen(self.col_glow, 6)
        glow_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(glow_pen)
        p.drawPolyline(QtGui.QPolygon(pts))

        # Main data line with enhanced styling
        main_pen = QtGui.QPen(self.col_line, 2.5)
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
        self.setMinimumSize(260, 260)
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
        """Apply color corona styling based on gauge type"""
        style = CoronaStyleManager.get_style(self.gauge_type)
        
        # Base colors with darker background for better contrast
        self.c_bg      = QtGui.QColor(8, 15, 25)
        self.c_tick_ma = QtGui.QColor(100, 255, 255, 230)
        self.c_tick_mi = QtGui.QColor(100, 255, 255, 140)
        self.c_readout = QtGui.QColor(255, 220, 160)
        self.c_title   = QtGui.QColor(170, 195, 220)
        self.c_cyan_dim= QtGui.QColor(60, 120, 180, 120)
        
        # Color corona configuration
        self.corona_mode = "color"
        self.base_color = style["base_color"]
        self.value_colors = style["value_colors"]
        
        # Warning range colors
        self.warning_color = QtGui.QColor(255, 165, 0, 180)
        self.danger_color = QtGui.QColor(255, 0, 0, 200)
        self.warning_glow = QtGui.QColor(255, 100, 0, 80)
        
        # Status colors coordinated with value colors
        self.col_ok    = self.value_colors[0][1] if self.value_colors else QtGui.QColor(120,255,170,220)
        self.col_warn  = self.value_colors[len(self.value_colors)//2][1] if len(self.value_colors) > 2 else QtGui.QColor(255,190,70,220)
        self.col_alarm = self.value_colors[-1][1] if self.value_colors else QtGui.QColor(255,90,80,220)
        
        # Needle configuration
        self.nd_front_len = 0.76
        self.nd_base_from_c = 0.12
        self.nd_base_w = 8.0
        self.nd_tip_w = 2.0
        self.nd_edge_highlight = QtGui.QColor(255, 255, 255, 200)
        self.nd_glow = QtGui.QColor(255, 90, 40, 90)

    # NEW: Over-range methods
    def set_over_range(self, is_over_range: bool):
        """Visual indicator for over-range condition"""
        self._over_range = is_over_range
        self.update()

    def is_over_range(self):
        """Check if current value is over range"""
        return getattr(self, '_over_range', False)

    # NEW: Range indicator methods
    def set_range_indicator(self, range_min: float, range_max: float, unit: str):
        """Show the current range on the gauge"""
        # Format range for display
        if range_max < 1e-9:  # pA
            min_str = f"{range_min * 1e12:.0f}pA"
            max_str = f"{range_max * 1e12:.0f}pA"
        elif range_max < 1e-6:  # nA
            min_str = f"{range_min * 1e9:.0f}nA" 
            max_str = f"{range_max * 1e9:.0f}nA"
        else:  # uA
            min_str = f"{range_min * 1e6:.0f}uA"
            max_str = f"{range_max * 1e6:.0f}uA"
            
        self.range_indicator = f"{min_str} - {max_str}"
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
        ring.setColorAt(inner_stop, current_color)
        ring.setColorAt(0.8, self._interpolate_color(current_color, QtGui.QColor(130, 20, 20), 0.5))
        ring.setColorAt(1.0, self._interpolate_color(current_color, QtGui.QColor(80, 10, 10), 0.7))
        
        self._fill_annulus(p, cx, cy, r_in, r_out, ring)
        
        # Add outer glow
        p.setPen(QtGui.QPen(current_color, 3))
        p.drawEllipse(QtCore.QPointF(cx, cy), r_out, r_out)
        
        # Add inner glow
        p.setPen(QtGui.QPen(current_color, 1))
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
        p.setPen(QtGui.QPen(QtGui.QColor(0, 30, 60, 40), 1))
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
        """Draw warning and danger range indicator bar"""
        start_deg, span_deg = self.start_deg, self.span_deg

        # Convert warning/danger ranges to angles (normalized 0..1 of the gauge)
        def norm(v): 
            return clamp(v, 0.0, 1.0)

        warning_start_angle = start_deg - span_deg * norm(self.warning_start)
        warning_end_angle   = start_deg - span_deg * norm(self.warning_end)
        danger_start_angle  = start_deg - span_deg * norm(self.danger_start)
        max_angle = start_deg - span_deg

        r_outer = R * 0.90
        # WARNING (orange)
        if self.warning_start < 1.0:
            warning_rect = QtCore.QRectF(cx - r_outer, cy - r_outer, 2*r_outer, 2*r_outer)
            pen_warning = QtGui.QPen(self.warning_color, 6.0)
            pen_warning.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pen_warning)
            warning_span = warning_start_angle - warning_end_angle
            p.drawArc(warning_rect, int(warning_start_angle * 16), int(warning_span * 16))
            if self.is_in_warning_zone():
                pen_glow = QtGui.QPen(self.warning_glow, 10)
                pen_glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                p.setPen(pen_glow)
                p.drawArc(warning_rect, int(warning_start_angle * 16), int(warning_span * 16))

        # DANGER (red)
        if self.danger_start < 1.0:
            danger_rect = QtCore.QRectF(cx - r_outer, cy - r_outer, 2*r_outer, 2*r_outer)
            pen_danger = QtGui.QPen(self.danger_color, 6.0)
            pen_danger.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pen_danger)
            danger_span = danger_start_angle - max_angle
            p.drawArc(danger_rect, int(danger_start_angle * 16), int(danger_span * 16))
            if self.is_in_danger_zone() or self.is_over_range():
                pulse_alpha = int(120 + 100 * abs(math.sin(self._t * 4)))
                pulse_color = QtGui.QColor(255, 50, 50, pulse_alpha)
                pen_pulse = QtGui.QPen(pulse_color, 12)
                pen_pulse.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                p.setPen(pen_pulse)
                p.drawArc(danger_rect, int(danger_start_angle * 16), int(danger_span * 16))

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
        pen_fg = QtGui.QPen(current_color, 8)
        pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_fg)
        p.drawArc(rect_val, int(start*16), int(sweep*16))
        
        # Glow effect
        pen_glow = QtGui.QPen(current_color, 12)
        pen_glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setOpacity(0.3)
        p.setPen(pen_glow)
        p.drawArc(rect_val, int(start*16), int(sweep*16))
        p.setOpacity(1.0)

    def _draw_color_center(self, p, cx, cy, inner_ring_r):
        """Enhanced center with color elements"""
        current_color = self.get_current_color()
        
        # Outer glow ring
        p.setPen(QtGui.QPen(current_color, 6))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r, inner_ring_r)
        
        # Inner ring
        p.setPen(QtGui.QPen(QtGui.QColor(160, 230, 255, 220), 3))
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
        nd_color_base = self._interpolate_color(current_color, QtGui.QColor(180, 40, 30), 0.7)
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
            p.setPen(QtGui.QColor(150, 210, 255))
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
        bg.setColorAt(0.0, QtGui.QColor(15, 25, 40))
        bg.setColorAt(0.7, QtGui.QColor(8, 15, 25))
        bg.setColorAt(1.0, QtGui.QColor(5, 10, 18))
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

# ----------------------- Semi-circle LOG vacuum gauge -----------------------
class LogVacuumGauge(QtWidgets.QWidget):
    def __init__(self, title="TP", min_exp=-7, max_exp=-2, units="mbar", parent=None):
        super().__init__(parent)
        self.title, self.units = title, units
        self.min_exp, self.max_exp = int(min_exp), int(max_exp)
        self._value = float("nan")
        self.setMinimumHeight(200)

    def set_value(self, v: float):
        try: self._value = float(v)
        except Exception: self._value = float("nan")
        self.update()

    def _exp_to_angle(self, expv: float) -> float:
        expv = max(self.min_exp, min(self.max_exp, expv))
        span = self.max_exp - self.min_exp or 1.0
        t = (expv - self.min_exp) / span
        return 180.0 - 180.0 * t

    def paintEvent(self, _):
        # Simplified implementation for vacuum gauge
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        size = min(W, H) - 16
        cx, cy = W/2, H*0.62
        R = size/2
        p.fillRect(self.rect(), QtGui.QColor(0,0,0))
        
        # Draw basic semi-circle
        p.setPen(QtGui.QPen(QtGui.QColor(100, 200, 255), 2))
        p.drawArc(int(cx - R), int(cy - R), int(2*R), int(2*R), 0, 180*16)
        
        # Draw title
        p.setPen(QtGui.QPen(QtGui.QColor(200, 235, 255)))
        p.setFont(QtGui.QFont("Segoe UI", 12, QtGui.QFont.Weight.Bold))
        p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignHCenter, self.title)
        
        # Draw value if available
        if not math.isnan(self._value):
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255)))
            p.setFont(QtGui.QFont("Segoe UI", 14, QtGui.QFont.Weight.Bold))
            value_text = f"{self._value:.2e} {self.units}"
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, value_text)

# ----------------------- Tiles (plot + gauges) with COLOR STYLING -----------------------
class _BeamTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=0.0, ymax=1.0, line=QtGui.QColor(160,100,255), title="Beam (µA)")
        self.gauge = NeonRadial01(value=0.0, title="Beam Current", unit="µA",
                                  status_bands=dict(ok=(0.25,0.75), warn=(0.15,0.90)), gauge_type="beam")
        self.plot.setFixedWidth(300)
        lay.addWidget(self.plot, 0); lay.addWidget(self.gauge, 0)

    # FIXED: Proper needle positioning with actual range values from calibration
    def set_display(self, value_in_unit: float, unit: str,
                    range_min: float, range_max: float, decimals: int):
        """Set display with actual range boundaries from LabVIEW"""
        try:
            val = float(value_in_unit)
            range_min = float(range_min)
            range_max = float(range_max)
        
            print(f"DEBUG: Setting display - Value: {val} {unit}, Range: {range_min}-{range_max} {unit}")
            print(f"DEBUG: Detailed - val={val}, range_min={range_min}, range_max={range_max}")
            print(f"DEBUG: Comparison - val > range_max = {val > range_max}")
            print(f"DEBUG: Range span = {range_max - range_min}")
    
            # Set the gauge to use the actual range from LabVIEW
            self.gauge.set_fullscale(range_max, unit, decimals=decimals, label_min=range_min)
            
            # Set the range indicator
            self.gauge.set_range_indicator(range_min, range_max, unit)
        
            # Calculate normalized position within the actual range
            # Handle over-range values by clamping to the range boundaries
            if val > range_max:
                normalized_value = 1.0
                self.gauge.set_over_range(True)
                print(f"DEBUG: Over-range detected! {val} > {range_max}")
            elif val < range_min:
                normalized_value = 0.0
                self.gauge.set_over_range(False)
                print(f"DEBUG: Under-range detected! {val} < {range_min}")
            else:
                normalized_value = (val - range_min) / (range_max - range_min)
                self.gauge.set_over_range(False)
                print(f"DEBUG: Normal range - normalized_value = {normalized_value}")
        
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
            print(f"DEBUG: Error in set_display: {e}")
            import traceback
            traceback.print_exc()

class _PowerTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=0.0, ymax=float(app_settings.rf_gauge_max_kv),
                                    line=QtGui.QColor(255,190,70), title="RF Power (kV)")
        self.gauge = NeonRadial01(value=0.0, title="Power", unit="kV",
                                  status_bands=dict(ok=(0.30,0.80), warn=(0.15,0.90)), gauge_type="rf_power")
        self.plot.setFixedWidth(300)
        lay.addWidget(self.plot, 0); lay.addWidget(self.gauge, 0)

    def set_kV(self, kv: float, fs_kV: float):
        kv = max(0.0, float(kv)); fs_kV = max(1.0, float(fs_kV))
        self.gauge.set_fullscale(fs_kV, "kV", decimals=2, label_min=0.0)
        self.gauge.set_value(min(1.0, kv/fs_kV))
        self.gauge.set_big_readout(kv, "kV", decimals=2)
        self.plot.add_point(kv)
        self.plot.set_range(0.0, fs_kV)

class _VacTile(QtWidgets.QWidget):
    def __init__(self, title="TP"):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=-7, ymax=-2, line=QtGui.QColor(70,210,255), title=f"{title} (log10 mbar)")
        self.gauge = LogVacuumGauge(title=title, min_exp=app_settings.vac_min_exp, max_exp=app_settings.vac_max_exp, units=app_settings.vac_units)
        self.plot.setFixedWidth(300)
        lay.addWidget(self.plot, 0); lay.addWidget(self.gauge, 0)

    def set_mbar(self, mbar: float):
        v = max(1e-12, float(mbar))
        self.gauge.set_value(v)
        self.plot.add_point(math.log10(v))

# ----------------------- Main Window -----------------------
class VacuumBeamMonitoringWindow(QtWidgets.QWidget):
    def __init__(self, data_queue, plot_rate_hz=None, seconds=None, bg_image=None):
        super().__init__()
        self.setWindowTitle("Vacuum • Beam • RF — Color Styled HUD")
        self.setStyleSheet("background-color: black;")

        self._q = data_queue
        self.scaler = Scaler()
        self._last_data_ts = 0.0

        # top bar
        root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(12,12,12,12); root.setSpacing(12)
        topbar = QtWidgets.QHBoxLayout(); topbar.setContentsMargins(0,0,0,0); topbar.setSpacing(8)
        self.dot = StatusDot(); self.status_lbl = QtWidgets.QLabel("No data")
        self.status_lbl.setStyleSheet("color: rgb(230,90,80); font-weight: 600;")
        topbar.addWidget(self.dot); topbar.addWidget(self.status_lbl); topbar.addStretch(1)
        root.addLayout(topbar)

        # tiles inside boxes
        self.beam = _BeamTile(); self.rf = _PowerTile(); self.tp2 = _VacTile("TP2"); self.tp1 = _VacTile("TP1")

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setSpacing(18)
        grid.addWidget(HudCard(self.beam), 0, 0)
        grid.addWidget(HudCard(self.rf),   0, 1)
        grid.addWidget(HudCard(self.tp2),  1, 0)
        grid.addWidget(HudCard(self.tp1),  1, 1)
        root.addLayout(grid)

        # overlay debug
        self._overlay_on = False
        self._overlay = QtWidgets.QLabel(self); self._overlay.setVisible(False)
        self._overlay.setStyleSheet("QLabel { color:#bfe6ff; background:rgba(20,30,45,120); "
                                    "border:1px solid rgba(160,200,255,90); padding:6px; "
                                    "font-family:Consolas, Fira Mono, monospace; }")
        self._overlay.move(10, 32)

        # timers
        fps = float(plot_rate_hz) if plot_rate_hz else float(app_settings.plot_fps)
        fps = max(1.0, fps)
        self._timer = QtCore.QTimer(self, interval=int(1000.0 / fps))
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

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() == QtCore.Qt.Key.Key_D:
            self._overlay_on = not self._overlay_on
            self._overlay.setVisible(self._overlay_on)
        super().keyPressEvent(e)

    def _heartbeat(self):
        ok = (time.time() - self._last_data_ts) < 1.0
        self.dot.set_ok(ok)
        self.status_lbl.setText("Receiving" if ok else "No data")
        self.status_lbl.setStyleSheet("color: rgb(60,220,120); font-weight: 600;" if ok
                                      else "color: rgb(230,90,80); font-weight: 600;")

    def _on_timer(self):
        # Drain to newest
        last = None
        while not self._q.empty():
            last = self._q.get()

        if last is None or not isinstance(last, dict):
            return  # let heartbeat make it red if stale

        # ✅ We have a packet
        self._last_data_ts = time.time()
    
        # Get beam range index and value separately
        beam_range_idx = last.get("beam_range_idx")
        beam_value = last.get("beam_current")
    
        self.dot.set_ok(True)
        self.status_lbl.setText("Receiving")
        self.status_lbl.setStyleSheet("color: rgb(60,220,120); font-weight: 600;")

        try:
            vac = last.get("vacuum")
            rf  = last.get("rf_power_kv")
            bv  = last.get("beam_current")

            # normalize list-style beam_current
            if isinstance(bv, (list, tuple)):
                bv = bv[0]

            # vacuum fallbacks
            if vac is None:
                vac_try = [last.get(f"vac{i}", None) for i in range(1, 6)]
                if all(v is not None for v in vac_try):
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
                bv = last.get("beam_v_raw") or last.get("beam")

            # Vacuum → mbar
            if isinstance(vac, (list, tuple)) and len(vac) >= 2:
                tp1_mbar = self.scaler.raw_to_eng("vac1", vac[0])
                tp2_mbar = self.scaler.raw_to_eng("vac2", vac[1])
                self.tp1.set_mbar(tp1_mbar)
                self.tp2.set_mbar(tp2_mbar)

            # RF
# RF (apply scaling)
            if rf is not None:
                rf_eng = self.scaler.raw_to_eng("rf_power_kv", float(rf))
                print(f"RF raw={rf:.3f}, scaled={rf_eng:.3f}")  # temporary debug
                self.rf.set_kV(float(rf_eng), float(app_settings.rf_gauge_max_kv))



            # Beam: V -> nA (per range), then unit-aware display with actual range boundaries
            if bv is not None:
                # Use the provided beam_range_idx if available, otherwise auto-select
                if beam_range_idx is not None:
                    idx = int(beam_range_idx)
                else:
                    idx = beam_cal.select_index_for_pkt(last)
            
                rng = beam_cal.cfg.ranges[idx]

                # FIXED: Use the actual maximum from the calibration curve for range boundaries
                if rng.curve and len(rng.curve) > 0:
                    range_max_nA = rng.curve[-1][1]  # Last point's nA value from calibration curve
                else:
                    range_max_nA = rng.fs_nA  # Fallback to fs_nA

                # For beam current, range typically starts from 0
                range_min_nA = 0.0

                print(f"DEBUG: Range {idx} '{rng.name}' - curve max: {range_max_nA} nA, fs_nA: {rng.fs_nA}")
                print(f"DEBUG: Using range boundaries: {range_min_nA} to {range_max_nA} nA")
            
                # choose unit from FS, convert value to that unit
                unit, scale = beam_cal.unit_for_fs(rng.fs_nA)      # pA/nA/µA selection
                i_nA = beam_cal.volts_to_nA(float(bv), idx)        # volts → nA
                i_disp  = i_nA * scale                              # value in chosen unit
            
                # Convert range boundaries to display units
                range_min_disp = range_min_nA * scale
                range_max_disp = range_max_nA * scale

                print(f"DEBUG: Unit conversion - i_nA={i_nA:.6f}, scale={scale}, i_disp={i_disp:.6f} {unit}")
                print(f"DEBUG: Range in display units: {range_min_disp:.6f} to {range_max_disp:.6f} {unit}")

                # compact decimals per unit
                if unit == "pA":
                    dec = 0
                elif unit == "nA":
                    dec = 1 if range_max_disp < 10 else 0  # 1 decimal for small nA, 0 for larger
                else:  # uA
                    dec = 2

                # Update beam tile with actual range boundaries
                self.beam.set_display(i_disp, unit, range_min_disp, range_max_disp, dec)

                if self._overlay_on:
                    self._overlay.setText(
                        f"raw V: {float(bv):+.3f}\n"
                        f"Range idx: {idx} ({range_min_nA:.1e}-{range_max_nA:.1e} nA)\n"
                        f"Display: {range_min_disp:.3f}-{range_max_disp:.3f} {unit}\n"
                        f"I: {i_nA:.3f} nA  disp: {i_disp:.3f} {unit}\n"
                        f"FS: {rng.fs_nA:.3f} nA  scale: {scale}\n"
                        f"Normalized: {(i_disp - range_min_disp) / (range_max_disp - range_min_disp):.3f}"
                    )
                    self._overlay.adjustSize()

        except Exception as e:
            # Keep status green; just log the parsing issue for debugging
            print("[Vac/Beam] parse error:", repr(e))
            import traceback
            traceback.print_exc()

# For testing purposes
if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    
    # Create a simple test window
    test_window = QtWidgets.QWidget()
    test_window.setWindowTitle("VacuumBeamMonitoringWindow Test")
    layout = QtWidgets.QVBoxLayout(test_window)
    
    # Create the monitoring window with empty queue
    import queue
    data_queue = queue.Queue()
    monitor = VacuumBeamMonitoringWindow(data_queue)
    layout.addWidget(monitor)
    
    # Test with some sample data
    test_data = {
        "beam_range_idx": 3,  # 3nA to 30nA range
        "beam_current": 0.5,  # Should give ~15nA in the middle of the range
        "rf_power_kv": 5.0,
        "vacuum": [2.5e-6, 1.8e-6, 0, 0, 0]  # TP1 and TP2 values
    }
    data_queue.put(test_data)
    
    test_window.resize(1200, 800)
    test_window.show()
    
    sys.exit(app.exec())