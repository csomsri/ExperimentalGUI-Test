# -*- coding: utf-8 -*-
"""
Created on Tue Oct 28 07:47:05 2025

@author: clasa
"""

# -*- coding: utf-8 -*-
"""
VacuumBeamMonitoringWindow.py — 2×2 neon HUD with CORONA STYLING + status light
Enhanced with corona color modes for different gauge types
"""

import time, math, collections
from PyQt6 import QtCore, QtGui, QtWidgets

from beam_cal import beam_cal            # singleton: .cfg, .select_index_for_pkt(), .volts_to_nA()
from scaling import Scaler               # PV -> engineering units (mbar) with TP piecewise fit
from app_settings import app_settings

# ----------------------- tiny helpers -----------------------
def clamp(v, a, b): return a if v < a else b if v > b else v

# ----------------------- CORONA STYLE MANAGER -----------------------
class CoronaStyleManager:
    """Manages different corona styles for gauges"""
    
    STYLES = {
        "beam": {
            "name": "Beam Current",
            "corona_mode": "cyberpunk",
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
            "corona_mode": "metal", 
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

# ----------------------- mini time-series plot -----------------------
class TimeSeriesPlot(QtWidgets.QWidget):
    def __init__(self, parent=None, max_points=240, ymin=0.0, ymax=100.0, line=None, title=""):
        super().__init__(parent)
        self.setMinimumSize(260, 180)
        self.max_points = int(max_points)
        self._buf = collections.deque(maxlen=self.max_points)
        self._ymin, self._ymax = float(ymin), float(ymax)
        self.title = title

        self.col_bg    = QtGui.QColor(0, 0, 0)      # solid black card area
        self.col_grid  = QtGui.QColor(150, 200, 255, 35)
        self.col_axes  = QtGui.QColor(160, 210, 255, 70)
        self.col_title = QtGui.QColor(185, 215, 255, 180)
        self.col_line  = QtGui.QColor(line) if line else QtGui.QColor(120, 240, 255)
        self.col_fill  = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 55)

    def add_point(self, v: float): self._buf.append(float(v)); self.update()
    def set_range(self, ymin: float, ymax: float): self._ymin, self._ymax = float(ymin), float(ymax); self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self.col_bg)

        if self.title:
            p.setPen(self.col_title)
            p.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Medium))
            p.drawText(QtCore.QRectF(0,0,w,20),
                       QtCore.Qt.AlignmentFlag.AlignHCenter|QtCore.Qt.AlignmentFlag.AlignTop,
                       self.title)

        # grid
        p.setPen(QtGui.QPen(self.col_grid, 1))
        rows, cols = 6, 6
        for i in range(1, rows): p.drawLine(0, int(h*(i/rows)), w, int(h*(i/rows)))
        for j in range(1, cols): p.drawLine(int(w*(j/cols)), 0, int(w*(j/cols)), h)

        if len(self._buf) < 2: return

        vr = (self._ymax - self._ymin) or 1.0
        pts = [QtCore.QPoint(int(w*(i/(self.max_points-1))),
                             int(h - ((self._buf[i]-self._ymin)/vr)*h))
               for i in range(len(self._buf))]

        poly = QtGui.QPolygon([QtCore.QPoint(0,h)] + pts + [QtCore.QPoint(pts[-1].x(),h)])
        p.setBrush(self.col_fill); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPolygon(poly)
        pen = QtGui.QPen(self.col_line, 2); pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap); p.setPen(pen)
        p.drawPolyline(QtGui.QPolygon(pts))
        p.setPen(QtGui.QPen(self.col_axes, 1)); p.drawLine(0, h-1, w, h-1)

# ----------------------- ENHANCED Neon radial gauge with CORONA STYLING -----------------------
class NeonRadial01(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal(float)
    
    def __init__(self, parent=None, value=0.0, title="Gauge", unit="", status_bands=None, gauge_type="beam"):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self._value = float(clamp(value, 0.0, 1.0))
        self.title, self.unit = title, unit
        self.span_deg, self.start_deg = 280.0, 90.0 + 280.0/2.0
        self.clockwise = True
        self.display_decimals = 2; self._big_text = None; self._big_unit = ""
        
        # Apply corona styling based on gauge type
        self.gauge_type = gauge_type
        self._apply_corona_style()
        
        self.status_bands = status_bands or dict(ok=(0.25,0.75), warn=(0.15,0.90))
        self._fs_value = 1.0
        self._tick_decimals = 2
        self._tick_labels = [f"{i/10:.1f}" for i in range(11)]
        
        # Animation timer for corona effects
        self._t = 0.0
        self._pulse = QtCore.QTimer(self)
        self._pulse.timeout.connect(self._tick)
        self._pulse.start(16)

    def _apply_corona_style(self):
        """Apply corona styling based on gauge type"""
        style = CoronaStyleManager.get_style(self.gauge_type)
        
        # Base colors
        self.c_bg      = QtGui.QColor(0, 0, 0)
        self.c_tick_ma = QtGui.QColor(200, 240, 255, 230)
        self.c_tick_mi = QtGui.QColor(200, 240, 255, 150)
        self.c_readout = QtGui.QColor(255, 220, 160)
        self.c_title   = QtGui.QColor(170, 195, 220)
        self.c_cyan_dim= QtGui.QColor(60, 120, 180, 120)
        
        # Corona configuration
        self.corona_mode = style["corona_mode"]
        self.base_color = style["base_color"]
        self.value_colors = style["value_colors"]
        
        # Status colors coordinated with value colors
        self.col_ok    = self.value_colors[0][1] if self.value_colors else QtGui.QColor(120,255,170,220)
        self.col_warn  = self.value_colors[len(self.value_colors)//2][1] if len(self.value_colors) > 2 else QtGui.QColor(255,190,70,220)
        self.col_alarm = self.value_colors[-1][1] if self.value_colors else QtGui.QColor(255,90,80,220)

    def _tick(self):
        """Animation tick for corona effects"""
        self._t += 0.016
        self.update()

    def get_current_color(self):
        """Get color based on current normalized value using corona styling"""
        normalized = self._value
        
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

    def _draw_corona_ring(self, p, cx, cy, r_in, r_out):
        """Draw corona ring based on current mode"""
        current_color = self.get_current_color()
        
        if self.corona_mode == "cyberpunk":
            # Cyberpunk style with dynamic colors
            base = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
            inner_stop = r_in / r_out
            base.setColorAt(0.0, QtGui.QColor(0,0,0,0))
            base.setColorAt(inner_stop, QtGui.QColor(80, 90, 100, 220))
            base.setColorAt(1.0, QtGui.QColor(30, 35, 40, 220))
            self._fill_annulus(p, cx, cy, r_in, r_out, base)
            
            # Color accent
            color_ring = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), -90.0)
            color_ring.setColorAt(0.0, current_color)
            color_ring.setColorAt(0.3, QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 100))
            color_ring.setColorAt(0.7, QtGui.QColor(current_color.red(), current_color.green(), current_color.blue(), 100))
            color_ring.setColorAt(1.0, current_color)
            self._fill_annulus(p, cx, cy, r_in, r_out - 5, color_ring)
            
        elif self.corona_mode == "color":
            # Solid color style
            ring = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
            inner_stop = r_in / r_out
            ring.setColorAt(0.0, QtGui.QColor(0,0,0,0))
            ring.setColorAt(inner_stop, current_color)
            ring.setColorAt(1.0, self._interpolate_color(current_color, QtGui.QColor(130,20,20), 0.7))
            self._fill_annulus(p, cx, cy, r_in, r_out, ring)
            
        else:  # metal mode
            # Metallic style
            base = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
            inner_stop = r_in / r_out
            base.setColorAt(0.0, QtGui.QColor(0,0,0,0))
            base.setColorAt(inner_stop, QtGui.QColor(110,120,130,220))
            base.setColorAt(1.0, QtGui.QColor(50,55,60,220))
            self._fill_annulus(p, cx, cy, r_in, r_out, base)

    def _fill_annulus(self, p, cx, cy, r_in, r_out, brush):
        """Helper to fill annular ring"""
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

    # ... (keep all your existing methods like set_fullscale, set_big_readout, getValue, setValue, etc.)

    def set_fullscale(self, fs_value: float, unit_label: str, decimals: int | None = None):
        """Set engineering full-scale and regenerate tick labels 0..FS."""
        self._fs_value = max(1e-12, float(fs_value))
        self.unit = unit_label or self.unit
        if decimals is not None:
            self._tick_decimals = int(decimals)
        # generate 11 labels (0%,10%,...,100%) in engineering units
        self._tick_labels = []
        for i in range(11):
            val = (i/10.0) * self._fs_value
            self._tick_labels.append(f"{val:.{self._tick_decimals}f}")
        self.update()

    def set_big_readout(self, value: float, unit: str, decimals: int = 2):
        self._big_text = f"{float(value):.{decimals}f}"; self._big_unit = unit; self.update()

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

    def _val_to_angle(self, norm): return (self.start_deg - self.span_deg*norm) if self.clockwise else (self.start_deg + self.span_deg*norm)

    def _status_color(self, f):
        # Use corona color system instead of fixed status colors
        return self.get_current_color()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect(); s = min(rect.width(), rect.height()); cx, cy = rect.center().x(), rect.center().y(); R = s*0.46
        p.fillRect(rect, self.c_bg)

        # Draw corona ring
        r_in, r_out = R*0.36, R*0.56
        self._draw_corona_ring(p, cx, cy, r_in, r_out)

        # ... (keep the rest of your existing paintEvent code, but update color references)
        
        # Use dynamic colors instead of fixed ones
        current_color = self.get_current_color()
        
        # background arc - use dimmed current color
        rect_a = QtCore.QRectF(cx-R*0.76, cy-R*0.76, 2*R*0.76, 2*R*0.76)
        dim_color = QtGui.QColor(current_color.red()//3, current_color.green()//3, current_color.blue()//3, 120)
        p.setPen(QtGui.QPen(dim_color, 8)); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawArc(rect_a, int(self.start_deg*16), int(-self.span_deg*16))

        # value arc - use current color
        f = clamp((self._value - 0.0) / ((1.0 - 0.0) or 1.0), 0.0, 1.0)
        p.setPen(QtGui.QPen(current_color, 10))
        p.drawArc(rect_a, int(self.start_deg*16), int(-self.span_deg*f*16))

        # ... (rest of your existing drawing code with dynamic color adaptation)

# ----------------------- Tiles (plot + gauges) with CORONA STYLING -----------------------
class _BeamTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=0.0, ymax=1.0, line=QtGui.QColor(160,100,255), title="Beam (µA)")
        self.gauge = NeonRadial01(value=0.0, title="Beam Current", unit="µA",
                                  status_bands=dict(ok=(0.25,0.75), warn=(0.15,0.90)), gauge_type="beam")
        self.plot.setFixedWidth(300)
        lay.addWidget(self.plot, 0); lay.addWidget(self.gauge, 0)

    def set_from_uA(self, uA: float, fs_uA: float):
        fs_uA = max(1e-12, float(fs_uA))
        fs_eff = fs_uA

        if   fs_eff >= 1.0:   dec = 2
        elif fs_eff >= 0.1:   dec = 3
        elif fs_eff >= 0.01:  dec = 4
        else:                 dec = 4

        self.gauge.set_fullscale(fs_eff, "µA", decimals=dec)
        norm = clamp(uA / fs_eff, 0.0, 1.0)
        self.gauge.set_value(norm)
        self.gauge.set_big_readout(uA, "µA", decimals=dec)

        self.plot.add_point(uA)
        self.plot.set_range(0.0, fs_eff)

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

# ----------------------- Main Window (unchanged) -----------------------
class VacuumBeamMonitoringWindow(QtWidgets.QWidget):
    def __init__(self, data_queue, plot_rate_hz=None, seconds=None, bg_image=None):
        super().__init__()
        self.setWindowTitle("Vacuum • Beam • RF — Corona Styled HUD")
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

        # ... (rest of your main window code remains the same)