# neon_gauges_alarm_timeseries.py
# PyQt6 neon gauges with alarm zones + cyberpunk time-domain plots (left of each gauge)
# - GaugeWithPlot: left = TimeSeriesPlot, right = NeonGauge
# - FourGaugeWindow: 2x2 layout of GaugeWithPlot
# - Alarm zones: configurable (safe/warn/danger) colored bands on the dial
# - Smooth demo feed (press D to toggle)

from __future__ import annotations
import math, sys, random, collections
from PyQt6 import QtCore, QtGui, QtWidgets

def clamp(v, a, b): 
    return a if v < a else b if v > b else v

# ---------------------------- Cyberpunk Time Series ---------------------------- #
class TimeSeriesPlot(QtWidgets.QWidget):
    """
    Ultra-light plot for real-time streams. Single series by default.
    • add_point(v) to append
    • set_range(ymin, ymax)
    • set_series_color(line_qcolor, fill_qcolor)
    """
    def __init__(self, parent=None, max_points=240, ymin=0.0, ymax=100.0):
        super().__init__(parent)
        self.setMinimumSize(240, 160)
        self.max_points = int(max_points)
        self._buf = collections.deque(maxlen=self.max_points)
        self._ymin, self._ymax = float(ymin), float(ymax)

        # cyberpunk palette
        self.col_bg_top   = QtGui.QColor(10, 14, 30)
        self.col_bg_bot   = QtGui.QColor(24, 28, 40)
        self.col_grid     = QtGui.QColor(120, 140, 200, 35)
        self.col_line     = QtGui.QColor(120, 240, 255)   # cyan
        self.col_fill     = QtGui.QColor(120, 240, 255, 60)
        self.col_axes     = QtGui.QColor(200, 210, 240, 70)

        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    # API
    def add_point(self, v: float):
        self._buf.append(float(v))
        self.update()

    def set_range(self, ymin: float, ymax: float):
        self._ymin, self._ymax = float(ymin), float(ymax)
        self.update()

    def set_series_color(self, line: QtGui.QColor, fill: QtGui.QColor | None = None):
        self.col_line = QtGui.QColor(line)
        self.col_fill = QtGui.QColor(fill) if fill else QtGui.QColor(line.red(), line.green(), line.blue(), 60)
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # bg gradient
        grad = QtGui.QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, self.col_bg_top); grad.setColorAt(1, self.col_bg_bot)
        p.fillRect(self.rect(), grad)

        # grid
        p.setPen(QtGui.QPen(self.col_grid, 1))
        rows, cols = 4, 6
        for i in range(1, rows):
            y = int(h * i/rows)
            p.drawLine(0, y, w, y)
        for j in range(1, cols):
            x = int(w * j/cols)
            p.drawLine(x, 0, x, h)

        if len(self._buf) < 2:
            p.end(); return

        # map points
        xmin, xmax = 0, self.max_points-1
        vr = (self._ymax - self._ymin) or 1.0
        def map_point(i, v):
            x = int(w * (i / (self.max_points-1)))
            y = int(h - ( (v - self._ymin) / vr ) * h)
            return QtCore.QPoint(x, y)

        pts = [map_point(i, self._buf[i]) for i in range(len(self._buf))]
        # fill polygon
        poly = QtGui.QPolygon([QtCore.QPoint(0, h)] + pts + [QtCore.QPoint(pts[-1].x(), h)])
        p.setBrush(self.col_fill); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPolygon(poly)

        # line
        pen = QtGui.QPen(self.col_line, 2)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawPolyline(QtGui.QPolygon(pts))

        # subtle axis baseline
        p.setPen(QtGui.QPen(self.col_axes, 1))
        p.drawLine(0, h-1, w, h-1)
        p.end()

# --------------------------------- Neon Gauge --------------------------------- #
class NeonGauge(QtWidgets.QWidget):
    """
    Neon gauge with alarm zones.
    alarm_zones: list of (min, max, QColor) in data units; drawn as bands along the scale.
    """
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None,
                 min_value=0.0, max_value=160.0, value=89.0,
                 start_deg=220.0, span_deg=280.0,
                 unit="Mph", title="", decimals=0,
                 alarm_zones=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self._min, self._max = float(min_value), float(max_value)
        self._value = clamp(float(value), self._min, self._max)
        self._start, self._span = float(start_deg), float(span_deg)
        self._unit, self._title, self._decimals = unit, title, int(decimals)
        self.alarm_zones = alarm_zones or []  # [(lo, hi, QColor), ...]

        # palette
        self.col_bg      = QtGui.QColor("#0b0b16")
        self.col_ring    = QtGui.QColor(255, 120, 20)
        self.col_ring2   = QtGui.QColor(151, 77, 255)
        self.col_tick    = QtGui.QColor(255, 175, 95)
        self.col_text    = QtGui.QColor(255, 210, 140)
        self.col_needle  = QtGui.QColor(255, 170, 40, 230)
        self.col_arc_dim = QtGui.QColor(120, 60, 200, 90)

        self._cached_size = QtCore.QSize()
        self._layer_static = QtGui.QPixmap()
        self._font_main = QtGui.QFont("Segoe UI", 9, QtGui.QFont.Weight.DemiBold)
        self._font_big  = QtGui.QFont("Segoe UI", 26, QtGui.QFont.Weight.Black)
        self._font_title= QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.DemiBold)

        self._anim = QtCore.QPropertyAnimation(self, b"value", duration=220)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

    # property
    def getValue(self): return self._value
    def setValue(self, v: float):
        v = clamp(float(v), self._min, self._max)
        if abs(v - self._value) <= 1e-9: return
        self._value = v; self.valueChanged.emit(v); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)

    # public API
    def set_value(self, v: float, animated: bool = True):
        v = clamp(float(v), self._min, self._max)
        if animated:
            self._anim.stop(); self._anim.setStartValue(self._value); self._anim.setEndValue(v); self._anim.start()
        else:
            self.setValue(v)

    def set_range(self, vmin: float, vmax: float):
        self._min, self._max = float(vmin), float(vmax)
        self._value = clamp(self._value, self._min, self._max)
        self._layer_static = QtGui.QPixmap(); self.update()

    def set_unit(self, unit: str): self._unit = unit; self.update()
    def set_title(self, title: str): self._title = title; self.update()
    def set_alarm_zones(self, zones): self.alarm_zones = zones; self._layer_static = QtGui.QPixmap(); self.update()

    # helpers
    def _val_to_angle_deg(self, v: float) -> float:
        f = (v - self._min) / ((self._max - self._min) or 1.0)
        f = clamp(f, 0.0, 1.0)
        return self._start - f*self._span

    def _rebuild_static_layer(self, size: QtCore.QSize):
        self._cached_size = size
        self._layer_static = QtGui.QPixmap(size)
        self._layer_static.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(self._layer_static)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        w, h = size.width(), size.height()
        d = min(w, h) - 2
        cx, cy = w/2, h/2
        r_outer = d * 0.48
        r_inner = d * 0.36
        rect_outer = QtCore.QRectF(cx - r_outer, cy - r_outer, 2*r_outer, 2*r_outer)
        rect_inner = QtCore.QRectF(cx - r_inner, cy - r_inner, 2*r_inner, 2*r_inner)
        angle0 = -self._start  # Qt's arc start

        # background
        p.fillRect(self.rect(), self.col_bg)
        grad_bg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_outer*1.05)
        grad_bg.setColorAt(0.0, QtGui.QColor(30, 20, 60, 220))
        grad_bg.setColorAt(1.0, QtGui.QColor(5, 5, 10, 0))
        p.setBrush(grad_bg); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawEllipse(rect_outer)

        # ---- alarm zones (under everything, along the inner ring)
        if self.alarm_zones:
            band_w = 10
            for lo, hi, col in self.alarm_zones:
                lo = clamp(lo, self._min, self._max); hi = clamp(hi, self._min, self._max)
                if hi <= lo: continue
                a1 = -self._val_to_angle_deg(lo); a2 = -self._val_to_angle_deg(hi)
                start = min(a1, a2); span = -(abs(a2 - a1))
                pen = QtGui.QPen(QtGui.QColor(col)); pen.setWidth(band_w)
                pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
                p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.drawArc(rect_inner, int(start*16), int(span*16))

        # outer neon ring
        for thick, alpha in [(10, 210), (22, 60)]:
            pen = QtGui.QPen(self.col_ring)
            c = QtGui.QColor(self.col_ring); c.setAlpha(alpha)
            pen.setColor(c); pen.setWidth(thick)
            p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawArc(rect_outer, int(angle0*16), int(-self._span*16))
        # inner violet ring
        pen = QtGui.QPen(self.col_ring2); pen.setWidth(6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen); p.drawArc(rect_inner, int(angle0*16), int(-self._span*16))

        # ticks + labels
        p.setFont(self._font_main); p.setPen(self.col_tick)
        steps = 16
        for i in range(steps+1):
            f = i/steps
            v = self._min + f*(self._max - self._min)
            ang_deg = self._start - f*self._span
            r1 = r_inner*0.92
            r2 = r_outer*(0.98 if i%2==0 else 0.965)
            x1, y1 = cx + r1*math.cos(math.radians(ang_deg)), cy - r1*math.sin(math.radians(ang_deg))
            x2, y2 = cx + r2*math.cos(math.radians(ang_deg)), cy - r2*math.sin(math.radians(ang_deg))
            pen_tick = QtGui.QPen(self.col_tick); pen_tick.setWidth(2 if i%2==0 else 1)
            p.setPen(pen_tick); p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))
            if i%2==0:
                r_text = r_inner*0.78
                ang = math.radians(ang_deg)
                tx, ty = cx + r_text*math.cos(ang), cy - r_text*math.sin(ang)
                s = f"{v:.0f}"
                b = QtGui.QFontMetrics(p.font()).boundingRect(s)
                p.drawText(QtCore.QRectF(tx-b.width()/2, ty-b.height()/2, b.width(), b.height()),
                           QtCore.Qt.AlignmentFlag.AlignCenter, s)

        # segmented decorative arc
        seg_rect = QtCore.QRectF(cx - r_inner*0.98, cy - r_inner*0.98, 2*r_inner*0.98, 2*r_inner*0.98)
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(self.col_arc_dim)
        segments, gap = 22, 2.5
        usable = self._span - segments*gap*0.5
        for i in range(segments):
            a = angle0 - (i*(usable/segments) + gap/2)*16
            span = -(usable/segments - gap)*16
            p.drawPie(seg_rect, int(a), int(span))

        if self._title:
            p.setFont(self._font_title); p.setPen(self.col_text)
            p.drawText(QtCore.QRectF(0, cy + r_inner*0.55, w, 28),
                       QtCore.Qt.AlignmentFlag.AlignHCenter, self._title)
        p.end()

    def paintEvent(self, ev):
        size = self.size()
        if self._layer_static.isNull() or size != self._cached_size:
            self._rebuild_static_layer(size)

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.drawPixmap(0, 0, self._layer_static)

        w, h = size.width(), size.height()
        d = min(w, h) - 2
        cx, cy = w/2, h/2
        r_outer, r_inner = d*0.48, d*0.36

        # needle color shifts when inside the highest-priority alarm zone
        needle_color = QtGui.QColor(self.col_needle)
        for lo, hi, col in self.alarm_zones:
            if lo <= self._value <= hi:
                needle_color = QtGui.QColor(col); needle_color.setAlpha(230)
        # needle geometry
        frac = (self._value - self._min) / (self._max - self._min + 1e-12)
        frac = clamp(frac, 0.0, 1.0)
        ang_deg = self._start - frac*self._span
        ang = math.radians(ang_deg)
        tip_r, base_r = r_outer*0.96, r_inner*0.42
        tip = QtCore.QPointF(cx + tip_r*math.cos(ang), cy - tip_r*math.sin(ang))
        base = QtCore.QPointF(cx - base_r*math.cos(ang), cy + base_r*math.sin(ang))
        path = QtGui.QPainterPath()
        ortho = ang + math.pi/2
        w1, w2 = d*0.010, d*0.030
        p1 = QtCore.QPointF(base.x() + w2*math.cos(ortho), base.y() - w2*math.sin(ortho))
        p2 = QtCore.QPointF(base.x() - w2*math.cos(ortho), base.y() + w2*math.sin(ortho))
        p3 = QtCore.QPointF(tip.x() - w1*math.cos(ortho), tip.y() + w1*math.sin(ortho))
        p4 = QtCore.QPointF(tip.x() + w1*math.cos(ortho), tip.y() - w1*math.sin(ortho))
        path.moveTo(p1); path.lineTo(p2); path.lineTo(p3); path.lineTo(p4); path.closeSubpath()

        grad = QtGui.QLinearGradient(base, tip)
        c1 = QtGui.QColor(needle_color); c1.setAlpha(255)
        c2 = QtGui.QColor(255, 230, 120, 10)
        grad.setColorAt(0.0, c1); grad.setColorAt(1.0, c2)
        p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)

        hub_r = d*0.045
        hub_grad = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), hub_r*2.2)
        hub_grad.setColorAt(0.0, QtGui.QColor(255, 120, 20))
        hub_grad.setColorAt(0.5, QtGui.QColor(255, 80, 10))
        hub_grad.setColorAt(1.0, QtGui.QColor(20, 10, 0, 0))
        p.setBrush(hub_grad)
        p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

        p.setPen(self.col_text); p.setFont(self._font_big)
        val_str = f"{self._value:.{self._decimals}f} {self._unit}".strip()
        metrics = QtGui.QFontMetrics(p.font())
        bw = metrics.horizontalAdvance(val_str) + 6; bh = metrics.height()
        p.drawText(QtCore.QRectF(cx-bw/2, cy-bh*0.1, bw, bh),
                   QtCore.Qt.AlignmentFlag.AlignCenter, val_str)
        p.end()

# ----------------------------- Composite Widget ----------------------------- #
class GaugeWithPlot(QtWidgets.QWidget):
    """
    Combines TimeSeriesPlot (left) and NeonGauge (right).
    Public:
      - set_value(v, animated=True)
      - add_point(v)
      - set_alarm_zones(list)
      - set_plot_range(ymin, ymax)
    """
    def __init__(self, title, unit, vmin, vmax, decimals=1,
                 alarm_zones=None, plot_range=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8,8,8,8)
        lay.setSpacing(12)

        # left plot
        pr = plot_range if plot_range else (vmin, vmax)
        self.plot = TimeSeriesPlot(max_points=240, ymin=pr[0], ymax=pr[1])

        # right gauge
        self.gauge = NeonGauge(title=title, unit=unit, min_value=vmin, max_value=vmax,
                               decimals=decimals, alarm_zones=alarm_zones)

        self.plot.set_series_color(QtGui.QColor(120,240,255), QtGui.QColor(120,240,255,60))
        self.plot.setFixedWidth(280)  # tidy rectangle left of the dial
        lay.addWidget(self.plot, 0)
        lay.addWidget(self.gauge, 0)

    # piping helpers
    def set_value(self, v, animated=True):
        self.gauge.set_value(v, animated)

    def add_point(self, v):
        self.plot.add_point(v)

    def set_alarm_zones(self, zones):
        self.gauge.set_alarm_zones(zones)

    def set_plot_range(self, ymin, ymax):
        self.plot.set_range(ymin, ymax)

# --------------------------------- 2x2 Window -------------------------------- #
class FourGaugeWindow(QtWidgets.QWidget):
    demoModeToggled = QtCore.pyqtSignal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Neon Gauges + Cyberpunk Plots")
        self.setStyleSheet("background-color:#05060a;")
        grid = QtWidgets.QGridLayout(self); grid.setContentsMargins(16,16,16,16); grid.setSpacing(16)

        # Alarm bands: safe (teal), warn (amber), danger (red)
        SAFE   = QtGui.QColor(0, 220, 200, 160)
        WARN   = QtGui.QColor(255, 170, 30, 170)
        DANGER = QtGui.QColor(255, 70, 60, 200)

        self.w1 = GaugeWithPlot("Beam Current", "µA", 0, 120, decimals=1,
                                alarm_zones=[(0, 90, SAFE), (90, 105, WARN), (105, 120, DANGER)])
        self.w2 = GaugeWithPlot("RF kV", "kV", 0, 120, decimals=1,
                                alarm_zones=[(0, 90, SAFE), (90, 110, WARN), (110, 120, DANGER)])
        self.w3 = GaugeWithPlot("Main Magnet", "A", 0, 1000, decimals=0,
                                alarm_zones=[(0, 850, SAFE), (850, 950, WARN), (950, 1000, DANGER)])
        # Vacuum shown log-like but gauge is linear range here; set bands accordingly
        self.w4 = GaugeWithPlot("Vacuum", "mTorr", 1e-6, 1e-2, decimals=6,
                                alarm_zones=[(1e-6, 1e-4, SAFE), (1e-4, 5e-4, WARN), (5e-4, 1e-2, DANGER)],
                                plot_range=(1e-6, 1e-2))
        # Make vacuum plot feel log: we will push log10(v) scaled (see demo tick)

        grid.addWidget(self.w1,0,0); grid.addWidget(self.w2,0,1)
        grid.addWidget(self.w3,1,0); grid.addWidget(self.w4,1,1)

        self._timer = QtCore.QTimer(self, interval=80)
        self._timer.timeout.connect(self._demo_tick)
        self._demo = False

    # Feed live data from ZMQ:
    # self.set_values(v1=..., v2=..., v3=..., v4=...)
    def set_values(self, v1=None, v2=None, v3=None, v4=None, animated=True):
        if v1 is not None: self.w1.set_value(v1, animated); self.w1.add_point(v1)
        if v2 is not None: self.w2.set_value(v2, animated); self.w2.add_point(v2)
        if v3 is not None: self.w3.set_value(v3, animated); self.w3.add_point(v3)
        if v4 is not None:
            self.w4.set_value(v4, animated)
            # show log trend while keeping gauge linear:
            self.w4.add_point(v4)

    def toggle_demo(self, on: bool | None = None):
        self._demo = (not self._demo) if on is None else bool(on)
        self._timer.start() if self._demo else self._timer.stop()
        self.demoModeToggled.emit(self._demo)

    def _demo_tick(self):
        def walk(cur, mag, vmin, vmax):
            return clamp(cur + random.uniform(-mag, mag), vmin, vmax)
        # Beam current
        v1 = walk(self.w1.gauge.getValue(), 1.5, 0, 120)
        v2 = walk(self.w2.gauge.getValue(), 1.0, 0, 120)
        v3 = walk(self.w3.gauge.getValue(), 7.0, 0, 1000)
        # vacuum drift in log space
        base = math.log10(max(self.w4.gauge.getValue(), 1e-9))
        base += random.uniform(-0.01, 0.01)
        v4 = 10**clamp(base, math.log10(1e-6), math.log10(1e-2))

        self.set_values(v1, v2, v3, v4, animated=True)

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() in (QtCore.Qt.Key.Key_D, QtCore.Qt.Key.Key_Space): self.toggle_demo()
        else: super().keyPressEvent(e)

# --------------------------------------- MAIN -------------------------------- #
def main():
    app = QtWidgets.QApplication(sys.argv)
    w = FourGaugeWindow()
    w.resize(1400, 900)
    w.show()
    w.toggle_demo(True)    # press D to stop demo
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
