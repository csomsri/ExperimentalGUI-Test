# -*- coding: utf-8 -*-
"""
VacuumBeamMonitoringWindow.py — 2×2 neon HUD with glossy cards + status light
Solid black background + robust packet parsing (vac, beam, rf)

Layout:
  ┌───────────────┬───────────────┐
  │ Beam (µA)     │ RF Power (kV) │
  ├───────────────┼───────────────┤
  │ TP2 (mbar)    │ TP1 (mbar)    │
  └───────────────┴───────────────┘
"""

import time, math, collections
from PyQt6 import QtCore, QtGui, QtWidgets

from beam_cal import beam_cal            # singleton: .cfg, .select_index_for_pkt(), .volts_to_nA()
from scaling import Scaler               # PV -> engineering units (mbar) with TP piecewise fit
from app_settings import app_settings



# ----------------------- tiny helpers -----------------------
def clamp(v, a, b): return a if v < a else b if v > b else v

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

# ----------------------- Neon 0..1 radial gauge -----------------------
class NeonRadial01(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal(float)
    def __init__(self, parent=None, value=0.0, title="Gauge", unit="", status_bands=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self._value = float(clamp(value, 0.0, 1.0))
        self.title, self.unit = title, unit
        self.span_deg, self.start_deg = 280.0, 90.0 + 280.0/2.0
        self.clockwise = True
        self.display_decimals = 2; self._big_text = None; self._big_unit = ""
        # colors
        self.c_bg      = QtGui.QColor(0,0,0)
        self.c_tick_ma = QtGui.QColor(200, 240, 255, 230)
        self.c_tick_mi = QtGui.QColor(200, 240, 255, 150)
        self.c_readout = QtGui.QColor(255, 220, 160)
        self.c_title   = QtGui.QColor(170, 195, 220)
        self.c_cyan_dim= QtGui.QColor(60, 120, 180, 120)
        self.col_ok    = QtGui.QColor(120,255,170,220)
        self.col_warn  = QtGui.QColor(255,190,70,220)
        self.col_alarm = QtGui.QColor(255,90,80,220)
        self.status_bands = status_bands or dict(ok=(0.25,0.75), warn=(0.15,0.90))
        self._fs_value = 1.0            # full-scale in display units (e.g., µA)
        self._tick_decimals = 2
        self._tick_labels = [f"{i/10:.1f}" for i in range(11)] 
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
        lo, hi = self.status_bands.get("ok",(0,1))
        lw, hw = self.status_bands.get("warn",(0,1))
        return self.col_ok if lo<=f<=hi else (self.col_warn if lw<=f<=hw else self.col_alarm)

    def _draw_inner_dots(self, p, cx, cy, R):
        dot_count=48; seg_len=max(3.0, R*0.06); seg_th=max(2.0, R*0.028); ring_r=R*0.52
        for i in range(dot_count):
            ang = (360.0/dot_count)*i; p.save(); p.translate(cx,cy); p.rotate(ang); p.translate(ring_r,0)
            r = QtCore.QRectF(-seg_th/2, -seg_len/2, seg_th, seg_len)
            p.setBrush(QtGui.QColor(40,120,170,160)); p.drawRoundedRect(r, seg_th*0.35, seg_th*0.35)
            r2 = QtCore.QRectF(-seg_th/2, -seg_len/2, seg_th, seg_len*0.55)
            p.setBrush(QtGui.QColor(90,200,255,210)); p.drawRoundedRect(r2, seg_th*0.35, seg_th*0.35)
            p.restore()

    def _draw_ticks_labels(self, p, cx, cy, R):
        start, span = self.start_deg, self.span_deg
        dir_mult = -1.0 if self.clockwise else 1.0
        r_out, r_major_in, r_minor_in = R*0.94, R*0.86, R*0.90

        for i in range(0, 101, 5):
            frac = i/100.0
            a_deg = start + dir_mult * span * frac
            a = math.radians(a_deg)
            x1, y1 = cx + r_out * math.cos(a), cy - r_out * math.sin(a)
            x2, y2 = cx + r_major_in * math.cos(a), cy - r_major_in * math.sin(a)
            p.setPen(QtGui.QPen(self.c_tick_mi if i % 25 else self.c_tick_ma, 2 if i % 25 else 3))
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))



        f_px = max(8, int(min(self.width(), self.height()) * 0.030))
        f = QtGui.QFont("Segoe UI", f_px, QtGui.QFont.Weight.Medium)
        r_label = (R*0.76) * 0.92
        for i in range(11):
            frac = i / 10.0
            a_deg = start - span * frac if self.clockwise else start + span * frac
            a = math.radians(a_deg)
            tx, ty = cx + r_label * math.cos(a), cy - r_label * math.sin(a)

            # Label: show engineering ticks 0..FS instead of 0..1
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



    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        rect = self.rect(); s = min(rect.width(), rect.height()); cx, cy = rect.center().x(), rect.center().y(); R = s*0.46
        p.fillRect(rect, self.c_bg)

        # background arc
        rect_a = QtCore.QRectF(cx-R*0.76, cy-R*0.76, 2*R*0.76, 2*R*0.76)
        p.setPen(QtGui.QPen(self.c_cyan_dim, 8)); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawArc(rect_a, int(self.start_deg*16), int(-self.span_deg*16))




        # value arc
# --- base + brightened neon colors ---
        col_outer_cold = QtGui.QColor(80, 200, 255)   # brighter cyan
        col_outer_hot  = QtGui.QColor(255, 140, 90)   # warmer orange
        col_mid        = QtGui.QColor(150, 220, 255)  # pale core
# compute fill color
        f = clamp((self._value - 0.0) / ((1.0 - 0.0) or 1.0), 0.0, 1.0)
        if f < 0.5:
            col = QtGui.QColor(
                int(col_outer_cold.red()  *(1-2*f) + col_mid.red()  *(2*f)),
                int(col_outer_cold.green()*(1-2*f) + col_mid.green()*(2*f)),
                int(col_outer_cold.blue() *(1-2*f) + col_mid.blue() *(2*f))
                )
        else:
            t = (f-0.5)/0.5
            col = QtGui.QColor(
                int(col_mid.red()  *(1-t) + col_outer_hot.red()  *t),
                int(col_mid.green()*(1-t) + col_outer_hot.green()*t),
                int(col_mid.blue() *(1-t) + col_outer_hot.blue() *t)
                )

# outer arc — brighter cyan glow
        p.setPen(QtGui.QPen(QtGui.QColor(70, 200, 255, 220), 8))
        p.drawArc(rect_a, int(self.start_deg*16), int(-self.span_deg*16))
        p.setPen(QtGui.QPen(col, 10))
        p.drawArc(rect_a, int(self.start_deg*16), int(-self.span_deg*f*16))

# twin halo rings (inner bright cyan + dotted electric blue)
        r_inner1 = R * 0.36
        p.setPen(QtGui.QPen(QtGui.QColor(130, 230, 255, 200), 3))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(QtCore.QRectF(cx - r_inner1, cy - r_inner1, 2*r_inner1, 2*r_inner1))

        r_inner2 = R * 0.28
        dot_r   = max(2.2, R * 0.013)
        dot_col = QtGui.QColor(100, 200, 255, 220)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for k in range(48):
            ang = 2.0 * math.pi * (k / 48.0)
            px = cx + r_inner2 * math.cos(ang)
            py = cy + r_inner2 * math.sin(ang)
            p.setBrush(dot_col)
            p.drawEllipse(QtCore.QPointF(px, py), dot_r, dot_r)

# --- brighter needle and hub glow ---
        a = math.radians(self.start_deg - self.span_deg*f)
        dirx,diry = math.cos(a), -math.sin(a); nx,ny = -diry,dirx
        tip_r, base_r = R*0.76, R*0.18
        tip  = QtCore.QPointF(cx+dirx*tip_r, cy+diry*tip_r)
        base = QtCore.QPointF(cx-dirx*base_r, cy-diry*base_r)
        w_tip, w_base = R*0.015, R*0.035
        p1 = QtCore.QPointF(base.x()+nx*w_base, base.y()+ny*w_base)
        p2 = QtCore.QPointF(base.x()-nx*w_base, base.y()-ny*w_base)
        p3 = QtCore.QPointF(tip.x()-nx*w_tip,  tip.y()-ny*w_tip)
        p4 = QtCore.QPointF(tip.x()+nx*w_tip,  tip.y()+ny*w_tip)
        path = QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        grad = QtGui.QLinearGradient(base, tip)
        grad.setColorAt(0, QtGui.QColor(255,210,120))
        grad.setColorAt(1, QtGui.QColor(255,255,200,15))
        p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawPath(path)

# central glow hub
        hub_r = R*0.065
        rg = QtGui.QRadialGradient(QtCore.QPointF(cx,cy), hub_r*2.3)
        rg.setColorAt(0.0, QtGui.QColor(255,180,120,255))
        rg.setColorAt(0.6, QtGui.QColor(255,110,60,160))
        rg.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        p.setBrush(rg)
        p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))



        r_inner1 = R * 0.34
        p.setPen(QtGui.QPen(QtGui.QColor(120, 210, 255, 180), 2))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(QtCore.QRectF(cx - r_inner1, cy - r_inner1, 2*r_inner1, 2*r_inner1))

# dotted inner ring
        r_inner2 = R * 0.27
        dot_r   = max(2.0, R * 0.012)        # dot size scales with gauge
        dot_col = QtGui.QColor(110, 200, 255, 170)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for k in range(48):
            ang = 2.0 * math.pi * (k / 48.0)
            px = cx + r_inner2 * math.cos(ang)
            py = cy + r_inner2 * math.sin(ang)
            p.setBrush(dot_col)
            p.drawEllipse(QtCore.QPointF(px, py), dot_r, dot_r)



        # ticks + labels + needle
        self._draw_ticks_labels(p, cx, cy, R)
        a = math.radians(self.start_deg - self.span_deg*f); dirx,diry = math.cos(a), -math.sin(a); nx,ny = -diry,dirx
        tip_r, base_r = R*0.76, R*0.18
        tip = QtCore.QPointF(cx+dirx*tip_r, cy+diry*tip_r); base=QtCore.QPointF(cx-dirx*base_r, cy-diry*base_r)
        w_tip, w_base = R*0.015, R*0.035
        p1 = QtCore.QPointF(base.x()+nx*w_base, base.y()+ny*w_base); p2 = QtCore.QPointF(base.x()-nx*w_base, base.y()-ny*w_base)
        p3 = QtCore.QPointF(tip.x()-nx*w_tip,  tip.y()-ny*w_tip);   p4 = QtCore.QPointF(tip.x()+nx*w_tip,  tip.y()+ny*w_tip)
        path=QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        grad=QtGui.QLinearGradient(base, tip); grad.setColorAt(0, QtGui.QColor(255,200,120)); grad.setColorAt(1, QtGui.QColor(255,250,170,10))
        p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)

        # hub & readouts
        hub_r=R*0.06; rg=QtGui.QRadialGradient(QtCore.QPointF(cx,cy), hub_r*2.0)
        rg.setColorAt(0, QtGui.QColor(255,160,80,220)); rg.setColorAt(0.7, QtGui.QColor(255,110,40,120)); rg.setColorAt(1, QtGui.QColor(0,0,0,0))
        p.setBrush(rg); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

        big = self._big_text if self._big_text is not None else f"{self._value:.{self.display_decimals}f}"
        f_big = QtGui.QFont("Segoe UI", int(s*0.075), QtGui.QFont.Weight.DemiBold)
        p.setFont(f_big); p.setPen(self.c_readout)
        br = QtGui.QFontMetricsF(f_big).boundingRect(big)
        p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()/4), big)

        if self._big_unit:
            f2 = QtGui.QFont("Segoe UI", int(s*0.040))
            p.setFont(f2); p.setPen(QtGui.QColor(170,200,255))
            ub = QtGui.QFontMetricsF(f2).boundingRect(self._big_unit)
            p.drawText(QtCore.QPointF(cx - ub.width()/2, cy + br.height()*0.95), self._big_unit)

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
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        size = min(W, H) - 16
        cx, cy = W/2, H*0.62
        R = size/2
        p.fillRect(self.rect(), QtGui.QColor(0,0,0))

        # base arc
        p.setPen(QtGui.QPen(QtGui.QColor(60,120,180,110), int(size*0.07)))
        rect = QtCore.QRectF(cx-R, cy-R, 2*R, 2*R)
        p.drawArc(rect, int(-180*16), int(-180*16))

        # value arc
        if self._value is not None and self._value > 0:
            expv = math.log10(self._value)
            frac = (expv - self.min_exp)/((self.max_exp - self.min_exp) or 1.0)
            pen = QtGui.QPen(QtGui.QColor(255,190,70,220), int(size*0.07))
            p.setPen(pen)
            p.drawArc(rect, int(-180*16), int(-180*frac*16))

        # ticks + labels
        p.setPen(QtGui.QPen(QtGui.QColor(220,240,255,230), 2))
        f = QtGui.QFont("Segoe UI", int(0.10*R), QtGui.QFont.Weight.DemiBold)
        p.setFont(f)
        for e10 in range(self.min_exp, self.max_exp+1):
            ang = math.radians(self._exp_to_angle(e10))
            r1 = R*0.90; r2 = R*0.985
            x1 = cx + r1*math.cos(ang); y1 = cy - r1*math.sin(ang)
            x2 = cx + r2*math.cos(ang); y2 = cy - r2*math.sin(ang)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
            label = f"10^{e10}"
            fm = QtGui.QFontMetrics(f)
            p.setPen(QtGui.QColor(170,200,255))
            p.drawText(int(cx + R*0.74*math.cos(ang) - fm.horizontalAdvance(label)/2),
                       int(cy - R*0.74*math.sin(ang) + fm.height()/3),
                       label)

        # needle + hub + value text
        if self._value is not None and self._value > 0:
            ang = math.radians(self._exp_to_angle(math.log10(self._value)))
            dirx,diry = math.cos(ang), -math.sin(ang); nx,ny = -diry, dirx
            r_tip,r_base = R*0.93, R*0.42
            tip = QtCore.QPointF(cx+dirx*r_tip, cy+diry*r_tip)
            base= QtCore.QPointF(cx-dirx*r_base, cy-diry*r_base)
            w_tip,w_base = size*0.010, size*0.030
            p1 = QtCore.QPointF(base.x()+nx*w_base, base.y()+ny*w_base)
            p2 = QtCore.QPointF(base.x()-nx*w_base, base.y()-ny*w_base)
            p3 = QtCore.QPointF(tip.x()-nx*w_tip,  tip.y()-ny*w_tip)
            p4 = QtCore.QPointF(tip.x()+nx*w_tip,  tip.y()+ny*w_tip)
            path = QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
            grad = QtGui.QLinearGradient(base, tip)
            grad.setColorAt(0.0, QtGui.QColor(255,200,90)); grad.setColorAt(1.0, QtGui.QColor(255,230,120,10))
            p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)
            hub_r=size*0.052; rg=QtGui.QRadialGradient(QtCore.QPointF(cx,cy), hub_r*2.2)
            rg.setColorAt(0, QtGui.QColor(255,160,80,220)); rg.setColorAt(0.7, QtGui.QColor(255,110,40,140)); rg.setColorAt(1.0, QtGui.QColor(0,0,0,0))
            p.setBrush(rg); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

        p.setPen(QtGui.QColor(225,238,255))
        fval = QtGui.QFont("Consolas, Menlo", int(0.16*R), QtGui.QFont.Weight.Black)
        p.setFont(fval)
        vtxt = f"{(self._value if self._value>0 else 0.0):.2e} {self.units}"
        fm = QtGui.QFontMetrics(fval)
        p.drawText(QtCore.QRectF(cx-fm.horizontalAdvance(vtxt)/2, cy, fm.horizontalAdvance(vtxt)+6, fm.height()),
                   QtCore.Qt.AlignmentFlag.AlignCenter, vtxt)

# ----------------------- Tiles (plot + gauges) -----------------------
class _BeamTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=0.0, ymax=1.0, line=QtGui.QColor(160,100,255), title="Beam (µA)")
        self.gauge = NeonRadial01(value=0.0, title="Beam Current", unit="µA",
                                  status_bands=dict(ok=(0.25,0.75), warn=(0.15,0.90)))
        self.plot.setFixedWidth(300)
        lay.addWidget(self.plot, 0); lay.addWidget(self.gauge, 0)

    def set_from_uA(self, uA: float, fs_uA: float):
        fs_uA = max(1e-12, float(fs_uA))

        # A small readability floor is optional; comment out if not desired:
        # MIN_FS = 0.0005  # 0.5 mµA = 500 nA (example floor)
        # fs_eff = max(fs_uA, MIN_FS)
        fs_eff = fs_uA

        # choose tick precision based on FS (so 1 nA -> 0.0001 µA ticks)
        # FS examples: 0.001 µA (1 nA) -> 4 dp, 0.01 µA (10 nA) -> 3 dp, etc.
        if   fs_eff >= 1.0:   dec = 2
        elif fs_eff >= 0.1:   dec = 3
        elif fs_eff >= 0.01:  dec = 4
        else:                 dec = 4  # covers 0.001 µA (1 nA) → 4dp (0.0001 µA = 100 pA)

        # update gauge scale & readout
        self.gauge.set_fullscale(fs_eff, "µA", decimals=dec)
        norm = clamp(uA / fs_eff, 0.0, 1.0)
        self.gauge.set_value(norm)
        self.gauge.set_big_readout(uA, "µA", decimals=dec)

        # plot follows FS
        self.plot.add_point(uA)
        self.plot.set_range(0.0, fs_eff)


    def set_from_nA(self, i_nA: float, fs_nA: float):
        """
        Display the beam using the *natural* unit implied by the full-scale.
        fs_nA: range FS in nA (e.g., 1 → 1 nA, 100 → 100 nA, 1000 → 1 µA)
        i_nA : current in nA (already converted via LUT)
        """
        from beam_cal import beam_cal  # local import to avoid circular surprises

        # decide unit & scale factor from FS (nA)
        unit, scale = beam_cal.unit_for_fs(float(fs_nA))   # "pA"/"nA"/"µA", and nA -> unit
        val_disp = float(i_nA) * scale                     # display value in chosen unit
        fs_disp  = float(fs_nA) * scale                    # display FS in chosen unit

        # pick decimals based on FS (readability)
        if   fs_disp >= 1.0:   dec = 2
        elif fs_disp >= 0.1:   dec = 3
        elif fs_disp >= 0.01:  dec = 4
        else:                  dec = 4

        # update gauge scale, labels and big readout
        self.gauge.set_fullscale(fs_disp, unit, decimals=dec)
        norm = clamp(val_disp / max(1e-12, fs_disp), 0.0, 1.0)
        self.gauge.set_value(norm)
        self.gauge.set_big_readout(val_disp, unit, decimals=dec)

        # plot follows FS and unit; update title text
        self.plot.add_point(val_disp)
        self.plot.set_range(0.0, fs_disp)
        self.plot.title = f"Beam ({unit})"
        self.plot.update()




class _PowerTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=0.0, ymax=float(app_settings.rf_gauge_max_kv),
                                    line=QtGui.QColor(255,190,70), title="RF Power (kV)")
        self.gauge = NeonRadial01(value=0.0, title="Power", unit="kV",
                                  status_bands=dict(ok=(0.30,0.80), warn=(0.15,0.90)))
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

# ----------------------- Main Window -----------------------
class VacuumBeamMonitoringWindow(QtWidgets.QWidget):
    def __init__(self, data_queue, plot_rate_hz=None, seconds=None, bg_image=None):
        super().__init__()
        self.setWindowTitle("Vacuum • Beam • RF — Neon HUD")
        self.setStyleSheet("background-color: black;")  # solid black

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
        bm_val  = int(last.get("bitmask", 0)) if isinstance(last, dict) and "bitmask" in last else 0
        pkt_idx = last.get("beam_range_idx", None) if isinstance(last, dict) else None
        
        
        
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
            if rf is not None:
                self.rf.set_kV(float(rf), float(app_settings.rf_gauge_max_kv))

            # Beam: V -> nA (per range), display as µA using FS of current range
            if bv is not None:
                idx = beam_cal.select_index_for_pkt(last)
                rng = beam_cal.cfg.ranges[idx]
                unit, scale = beam_cal.unit_for_fs(rng.fs_nA)      # nA → pA/nA/µA
                i_nA = beam_cal.volts_to_nA(float(bv), idx)        # volts → nA
                disp = max(0.0, i_nA * scale)

                if unit == "pA":
                    uA, fs_uA = disp/1e6, (rng.fs_nA * scale)/1e6
                elif unit == "nA":
                    uA, fs_uA = disp/1e3, (rng.fs_nA * scale)/1e3
                else:  # µA
                    uA, fs_uA = disp, (rng.fs_nA * scale)

                self.beam.set_from_uA(uA, fs_uA)

                if self._overlay_on:
                    self._overlay.setText(
                        f"raw V: {float(bv):+.3f}\n"
                        f"idx: {idx}  FS: {rng.fs_nA:.3f} nA  unit:{unit}\n"
                        f"I: {i_nA:.3f} nA  disp: {disp:.3f} {unit}\n"
                        f"Beam plot FS: {fs_uA:.3f} µA"
                    )
                    self._overlay.adjustSize()
            else:


                if self._overlay_on:
                    self._overlay.setText(
                        f"raw V: {float(bv):+.3f}\n"
                        f"mode: {beam_cal.cfg.select_mode}  idx: {idx}  FS: {rng.fs_nA:.3f} nA  unit: {unit}\n"
                        f"I: {i_nA:.3f} nA  disp: {disp:.3f} {unit}\n"
                        f"Beam plot FS: {fs_uA:.3f} µA\n"
                        f"bitmask: 0x{bm_val:016X}  pkt_idx: {pkt_idx}"
                        )
                    self._overlay.adjustSize()









        except Exception as e:
            # Keep status green; just log the parsing issue for debugging
            print("[Vac/Beam] parse error:", repr(e))
