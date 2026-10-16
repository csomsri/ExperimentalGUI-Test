# -*- coding: utf-8 -*-
"""
Created on Tue Oct  7 06:20:34 2025

@author: clasa
"""

# gauges_panel_hud_degrade.py
# PyQt6 — 2×2 HUD dashboard (modern gradient style)
#   [Beam Current] [Power (kV) — semicircle]
#   [TP2 (mbar)  ] [TP1 (mbar) — semicircle]
#
# Press D/Space to toggle the built-in demo motion.

from __future__ import annotations
import math, sys, random, collections, os
from PyQt6 import QtCore, QtGui, QtWidgets

# ───────────────────────────── Theme ───────────────────────────── #
class THEME:
    # background
    BG_TOP    = QtGui.QColor(7, 12, 22)
    BG_BOT    = QtGui.QColor(5, 9, 18)
    BG_VIGNET = QtGui.QColor(0, 0, 0, 170)
    BG_IMAGE  = ""  # set to a path if you want an image background

    # neon palette
    CYAN      = QtGui.QColor( 70, 210, 255)
    TEAL      = QtGui.QColor( 30, 180, 255)
    BLUE      = QtGui.QColor( 78, 130, 255)
    VIOLET    = QtGui.QColor(160, 100, 255)
    CORAL     = QtGui.QColor(255, 110,  90)
    REDHOT    = QtGui.QColor(255, 90,  80)
    AMBER     = QtGui.QColor(255, 190,  70)
    YELLOW    = QtGui.QColor(255, 230, 100)
    MINT      = QtGui.QColor(120, 255, 170)

    TEXT_MAIN = QtGui.QColor(225, 238, 255)
    TEXT_SOFT = QtGui.QColor(168, 190, 218)

    CARD_BORDER = QtGui.QColor(100, 210, 255, 90)
    CARD_GLOW   = QtGui.QColor( 30, 150, 220, 70)

def clamp(v,a,b): return a if v<a else b if v>b else v

# ───────────────────────── HUD Card ───────────────────────── #
class HudCard(QtWidgets.QFrame):
    def __init__(self, child: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(10,10,10,10)
        lay.addWidget(child)

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rf = QtCore.QRectF(self.rect().adjusted(2,2,-2,-2))
        path = QtGui.QPainterPath(); path.addRoundedRect(rf, 14.0, 14.0)

        glass = QtGui.QLinearGradient(rf.topLeft(), rf.bottomLeft())
        glass.setColorAt(0, QtGui.QColor(255,255,255,18))
        glass.setColorAt(1, QtGui.QColor(255,255,255, 6))
        p.fillPath(path, glass)

        p.setPen(QtGui.QPen(THEME.CARD_GLOW, 8));  p.drawPath(path)         # glow
        p.setPen(QtGui.QPen(THEME.CARD_BORDER, 1.3)); p.drawPath(path)      # border

# ───────────────────────── Cyberpunk Plot ───────────────────────── #
class TimeSeriesPlot(QtWidgets.QWidget):
    def __init__(self, parent=None, max_points=240, ymin=0.0, ymax=100.0, line=None):
        super().__init__(parent)
        self.setMinimumSize(260, 180)
        self.max_points = int(max_points)
        self._buf = collections.deque(maxlen=self.max_points)
        self._ymin, self._ymax = float(ymin), float(ymax)

        self.col_bg_top = QtGui.QColor(13, 19, 35)
        self.col_bg_bot = QtGui.QColor(9, 14, 26)
        self.col_grid   = QtGui.QColor(150, 200, 255, 35)
        self.col_axes   = QtGui.QColor(160, 210, 255, 70)
        self.col_line   = QtGui.QColor(line) if line else THEME.CYAN
        self.col_fill   = QtGui.QColor(self.col_line.red(), self.col_line.green(), self.col_line.blue(), 55)

        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    def add_point(self, v: float): self._buf.append(float(v)); self.update()
    def set_range(self, ymin: float, ymax: float): self._ymin, self._ymax = float(ymin), float(ymax); self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        grad = QtGui.QLinearGradient(0.0, 0.0, 0.0, float(h))
        grad.setColorAt(0, self.col_bg_top); grad.setColorAt(1, self.col_bg_bot)
        p.fillRect(self.rect(), grad)

        # faint light band (HUD)
        streak = QtGui.QLinearGradient(0.0, 0.0, float(w), 0.0)
        streak.setColorAt(0.15, QtGui.QColor(255,255,255,18))
        streak.setColorAt(0.16, QtGui.QColor(255,255,255, 0))
        streak.setColorAt(0.85, QtGui.QColor(255,255,255,12))
        streak.setColorAt(0.86, QtGui.QColor(255,255,255, 0))
        p.fillRect(self.rect(), streak)

        # grid
        p.setPen(QtGui.QPen(self.col_grid, 1))
        rows, cols = 4, 6
        for i in range(1, rows):
            y = h * (i / rows)
            p.drawLine(QtCore.QPointF(0.0, y), QtCore.QPointF(float(w), y))
        for j in range(1, cols):
            x = w * (j / cols)
            p.drawLine(QtCore.QPointF(x, 0.0), QtCore.QPointF(x, float(h)))

        if len(self._buf) < 2: p.end(); return

        vr = (self._ymax - self._ymin) or 1.0
        pts = [QtCore.QPoint(int(w * (i / (self.max_points-1))),
                             int(h - ((self._buf[i] - self._ymin) / vr) * h))
               for i in range(len(self._buf))]

        poly = QtGui.QPolygon([QtCore.QPoint(0, h)] + pts + [QtCore.QPoint(pts[-1].x(), h)])
        p.setBrush(self.col_fill); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPolygon(poly)

        pen = QtGui.QPen(self.col_line, 2)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.drawPolyline(QtGui.QPolygon(pts))

        p.setPen(QtGui.QPen(self.col_axes, 1))
        p.drawLine(QtCore.QPoint(0, h-1), QtCore.QPoint(w, h-1))
        p.end()

# ───────────────────────── Circular Neon Gauge (Beam) ───────────────────────── #
class NeonGauge(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal(float)
    def __init__(self, parent=None, min_value=0.0, max_value=120.0, value=86.8,
                 start_deg=220.0, span_deg=280.0, unit="µA", title="Beam Current", decimals=1):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self._min, self._max = float(min_value), float(max_value)
        self._value = clamp(float(value), self._min, self._max)
        self._start, self._span = float(start_deg), float(span_deg)
        self._unit, self._title, self._decimals = unit, title, int(decimals)

        self.col_bg      = QtGui.QColor(10, 12, 24)
        self.col_ring    = THEME.REDHOT
        self.col_ring2   = THEME.VIOLET
        self.col_tick    = QtGui.QColor(255, 210, 170)
        self.col_text    = THEME.TEXT_MAIN
        self.col_needle  = QtGui.QColor(255, 190, 110, 230)
        self.col_arc_dim = QtGui.QColor(120, 60, 200, 65)

        self._cached_size = QtCore.QSize(); self._layer_static = QtGui.QPixmap()
        self._font_main  = QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.DemiBold)
        self._font_big   = QtGui.QFont("Segoe UI", 28, QtGui.QFont.Weight.Black)
        self._font_title = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Weight.DemiBold)

        self._anim = QtCore.QPropertyAnimation(self, b"value", duration=220)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

    def getValue(self): return self._value
    def setValue(self, v: float):
        v = clamp(float(v), self._min, self._max)
        if abs(v - self._value) <= 1e-9: return
        self._value = v; self.valueChanged.emit(v); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)

    def set_value(self, v: float, animated=True):
        v = clamp(float(v), self._min, self._max)
        if animated:
            self._anim.stop(); self._anim.setStartValue(self._value); self._anim.setEndValue(v); self._anim.start()
        else: self.setValue(v)

    def _val_to_angle(self, v): 
        f = clamp((v - self._min) / ((self._max - self._min) or 1.0), 0, 1)
        return self._start - f*self._span

    def _rebuild_static(self, size: QtCore.QSize):
        self._cached_size = size
        pm = QtGui.QPixmap(size); pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        w, h = size.width(), size.height()
        d = min(w, h) - 2; cx, cy = w/2, h/2
        r_outer = d*0.48; r_inner = d*0.36
        rect_outer = QtCore.QRectF(cx-r_outer, cy-r_outer, 2*r_outer, 2*r_outer)
        rect_inner = QtCore.QRectF(cx-r_inner, cy-r_inner, 2*r_inner, 2*r_inner)
        angle0 = -self._start

        p.fillRect(self.rect(), self.col_bg)

        # deeper “degradation” glow: dark inner → colored edge
        ann = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), -self._start)
        ann.setColorAt(0.00, THEME.REDHOT)
        ann.setColorAt(0.55, THEME.AMBER)
        ann.setColorAt(1.00, THEME.REDHOT)
        pen = QtGui.QPen(); pen.setBrush(ann); pen.setWidth(int(d*0.070)); pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawArc(rect_outer, int(angle0*16), int(-self._span*16))

        # inner ring (violet)
        pen2 = QtGui.QPen(self.col_ring2, 7)
        pen2.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap); pen2.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen2); p.drawArc(rect_inner, int(angle0*16), int(-self._span*16))

        # ticks + labels
        p.setFont(self._font_main); p.setPen(self.col_tick)
        steps = 16
        for i in range(steps+1):
            f = i/steps; v = self._min + f*(self._max - self._min)
            ang = math.radians(self._val_to_angle(v))
            r1 = r_inner*0.92; r2 = r_outer*(0.98 if i%2==0 else 0.965)
            x1,y1 = cx + r1*math.cos(ang), cy - r1*math.sin(ang)
            x2,y2 = cx + r2*math.cos(ang), cy - r2*math.sin(ang)
            p.setPen(QtGui.QPen(self.col_tick, 2 if i%2==0 else 1))
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))
            if i%2==0:
                rt = r_inner*0.78; tx,ty = cx + rt*math.cos(ang), cy - rt*math.sin(ang)
                s = f"{v:.0f}"; b = QtGui.QFontMetrics(p.font()).boundingRect(s)
                p.drawText(QtCore.QRectF(tx-b.width()/2, ty-b.height()/2, b.width(), b.height()),
                           QtCore.Qt.AlignmentFlag.AlignCenter, s)

        # decorative inner segments
        seg_rect = QtCore.QRectF(cx - r_inner*0.98, cy - r_inner*0.98, 2*r_inner*0.98, 2*r_inner*0.98)
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(self.col_arc_dim)
        segments, gap = 22, 2.4; usable = self._span - segments*gap*0.5
        for i in range(segments):
            a = angle0 - (i*(usable/segments) + gap/2)*16
            span = -(usable/segments - gap)*16
            p.drawPie(seg_rect, int(a), int(span))

        p.setFont(self._font_title); p.setPen(THEME.TEXT_SOFT)
        p.drawText(QtCore.QRectF(0, cy + r_inner*0.56, w, 26),
                   QtCore.Qt.AlignmentFlag.AlignHCenter, self._title)
        p.end(); self._layer_static = pm

    def paintEvent(self, e):
        if self._layer_static.isNull() or self.size() != self._cached_size: self._rebuild_static(self.size())
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.drawPixmap(0,0,self._layer_static)

        w, h = self.width(), self.height(); d = min(w, h) - 2
        cx, cy = w/2, h/2; r_outer = d*0.48; r_inner = d*0.36
        ang = math.radians(self._val_to_angle(self._value))
        tip_r, base_r = r_outer*0.96, r_inner*0.42
        tip  = QtCore.QPointF(cx + tip_r*math.cos(ang), cy - tip_r*math.sin(ang))
        base = QtCore.QPointF(cx - base_r*math.cos(ang), cy + base_r*math.sin(ang))
        ortho = ang + math.pi/2; w1,w2 = d*0.010,d*0.030

        p1 = QtCore.QPointF(base.x()+w2*math.cos(ortho), base.y()-w2*math.sin(ortho))
        p2 = QtCore.QPointF(base.x()-w2*math.cos(ortho), base.y()+w2*math.sin(ortho))
        p3 = QtCore.QPointF(tip.x()-w1*math.cos(ortho),  tip.y()+w1*math.sin(ortho))
        p4 = QtCore.QPointF(tip.x()+w1*math.cos(ortho),  tip.y()-w1*math.sin(ortho))

        path = QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        beam = QtGui.QLinearGradient(base, tip)
        beam.setColorAt(0.0, QtGui.QColor(255, 200, 120))
        beam.setColorAt(1.0, QtGui.QColor(255, 250, 170, 10))
        p.setBrush(beam); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)

        hub_r = d*0.048
        glow = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), hub_r*2.2)
        glow.setColorAt(0.0, QtGui.QColor(255,130,70,220))
        glow.setColorAt(0.6, QtGui.QColor(255,90,40,140))
        glow.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        p.setBrush(glow); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

        p.setPen(self.col_text); p.setFont(self._font_big)
        s = f"{self._value:.{self._decimals}f} {self._unit}"; m = QtGui.QFontMetrics(p.font())
        p.drawText(QtCore.QRectF(cx-m.horizontalAdvance(s)/2, cy-m.height()*0.1, m.horizontalAdvance(s)+6, m.height()),
                   QtCore.Qt.AlignmentFlag.AlignCenter, s)
        p.end()

# ───────────── Semicircle Power (kV) with “degradation” gradient ───────────── #
class SemiArcPowerGauge(QtWidgets.QWidget):
    def __init__(self, value=62):
        super().__init__(); self.setMinimumSize(300,220)
        self._min, self._max = 0.0, 100.0
        self._value = clamp(float(value), self._min, self._max)
        self._cached_size = QtCore.QSize(); self._layer_static = QtGui.QPixmap()
        self.font_value = QtGui.QFont("Segoe UI", 32, QtGui.QFont.Weight.Black)

    def getValue(self): return self._value
    def setValue(self, v): self._value = clamp(float(v), self._min, self._max); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)
    def set_value(self, v, animated=True): self.setValue(v)

    def _angle(self, v): f=clamp((v-self._min)/((self._max-self._min) or 1.0),0,1); return 180.0 - f*180.0

    def _rebuild(self, size):
        self._cached_size=size
        pm=QtGui.QPixmap(size); pm.fill(QtCore.Qt.GlobalColor.transparent)
        p=QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        w,h=size.width(),size.height(); d=min(w,h)-2; cx,cy=w/2,h/2 + d*0.04
        r=d*0.48; rect=QtCore.QRectF(cx-r,cy-r,2*r,2*r)

        # gradient band: deep orange → yellow → pale
        con = QtGui.QConicalGradient(QtCore.QPointF(cx,cy), -180.0)
        con.setColorAt(0.00, THEME.AMBER)
        con.setColorAt(0.55, THEME.YELLOW)
        con.setColorAt(1.00, THEME.AMBER)
        pen = QtGui.QPen(); pen.setBrush(con); pen.setWidth(int(d*0.070))
        pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen); p.drawArc(rect,int(-180*16), int(-180*16))

        # subtle inner aperture (dark center) to mimic the photo
        inner = QtGui.QRadialGradient(QtCore.QPointF(cx,cy), r*0.85)
        inner.setColorAt(0.0, QtGui.QColor(0,0,0,200))
        inner.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        p.setBrush(inner); p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(QtCore.QRectF(cx-r*0.72, cy-r*0.72, 2*r*0.72, 2*r*0.72))

        # ticks + labels (white)
        p.setPen(QtGui.QPen(THEME.TEXT_MAIN,2))
        for i in range(0,11):
            v=i*10; ang=math.radians(self._angle(v))
            r1=r*0.92; r2=r*(0.98 if v%20==0 else 0.965)
            x1,y1=cx+r1*math.cos(ang), cy-r1*math.sin(ang)
            x2,y2=cx+r2*math.cos(ang), cy-r2*math.sin(ang)
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))
            if v%20==0:
                rt=r*0.78; tx,ty=cx+rt*math.cos(ang), cy-rt*math.sin(ang)
                s=f"{v}"; fm=QtGui.QFontMetrics(QtGui.QFont("Segoe UI",10,QtGui.QFont.Weight.DemiBold))
                p.drawText(QtCore.QRectF(tx-fm.horizontalAdvance(s)/2, ty-fm.height()/2, fm.horizontalAdvance(s), fm.height()),
                           QtCore.Qt.AlignmentFlag.AlignCenter, s)
        p.end(); self._layer_static=pm

    def paintEvent(self,e):
        size=self.size()
        if self._layer_static.isNull() or size!=self._cached_size: self._rebuild(size)
        p=QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        p.drawPixmap(0,0,self._layer_static)
        w,h=self.width(),self.height(); d=min(w,h)-2; cx,cy=w/2,h/2 + d*0.04
        r=d*0.48; r_inner=d*0.36
        ang=math.radians(self._angle(self._value))
        tip_r=r*0.93; base_r=r_inner*0.42
        tip=QtCore.QPointF(cx+tip_r*math.cos(ang), cy-tip_r*math.sin(ang))
        base=QtCore.QPointF(cx-base_r*math.cos(ang), cy+base_r*math.sin(ang))
        ortho=ang+math.pi/2; w1,w2=d*0.010,d*0.030
        p1=QtCore.QPointF(base.x()+w2*math.cos(ortho), base.y()-w2*math.sin(ortho))
        p2=QtCore.QPointF(base.x()-w2*math.cos(ortho), base.y()+w2*math.sin(ortho))
        p3=QtCore.QPointF(tip.x()-w1*math.cos(ortho),  tip.y()+w1*math.sin(ortho))
        p4=QtCore.QPointF(tip.x()+w1*math.cos(ortho),  tip.y()-w1*math.sin(ortho))
        path=QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        beam=QtGui.QLinearGradient(base,tip); beam.setColorAt(0, QtGui.QColor(255,200,90)); beam.setColorAt(1, QtGui.QColor(255,230,120,10))
        p.setBrush(beam); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)
        # hub & text
        hub_r=d*0.052; rg=QtGui.QRadialGradient(QtCore.QPointF(cx,cy), hub_r*2.2)
        rg.setColorAt(0, QtGui.QColor(255,160,80,220)); rg.setColorAt(0.7, QtGui.QColor(255,110,40,140)); rg.setColorAt(1, QtGui.QColor(0,0,0,0))
        p.setBrush(rg); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))
        p.setPen(THEME.TEXT_MAIN); f=self.font_value; p.setFont(f); fm=QtGui.QFontMetrics(f)
        s=f"{self._value:.0f} kV"
        p.drawText(QtCore.QRectF(cx-fm.horizontalAdvance(s)/2, cy-fm.height()*0.2, fm.horizontalAdvance(s)+6, fm.height()),
                   QtCore.Qt.AlignmentFlag.AlignCenter, s)
        p.end()

# ───────────── Semicircle Vacuum (log mbar) with superscripts ───────────── #
def exp_superscript(n: int) -> str:
    sup = {"-":"⁻","0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}
    return "10" + "".join(sup.get(ch, ch) for ch in f"^{n}")

class SemiArcVacuumGauge(QtWidgets.QWidget):
    def __init__(self, value=3.2e-5, title="TP1"):
        super().__init__(); self.setMinimumSize(300,220)
        self.vmin, self.vmax = 1e-7, 1e-2
        self._value = max(self.vmin, min(self.vmax, float(value)))
        self.title = title
        self._cached_size=QtCore.QSize(); self._layer_static=QtGui.QPixmap()
        self.font_value = QtGui.QFont("Segoe UI", 32, QtGui.QFont.Weight.Black)
        self.font_tick  = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.DemiBold)  # slightly larger so labels look crisp

    def getValue(self): return self._value
    def setValue(self, v): self._value=max(self.vmin,min(self.vmax,float(v))); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)
    def set_value(self,v,animated=True): self.setValue(v)

    def _frac(self,v): lv=math.log10(max(min(v,self.vmax),self.vmin)); return (lv-math.log10(self.vmin))/(math.log10(self.vmax)-math.log10(self.vmin))
    def _angle(self,v): return 180.0 - clamp(self._frac(v),0,1)*180.0

    def _rebuild(self,size):
        self._cached_size=size
        pm=QtGui.QPixmap(size); pm.fill(QtCore.Qt.GlobalColor.transparent)
        p=QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        w,h=size.width(),size.height(); d=min(w,h)-2; cx,cy=w/2,h/2 + d*0.04
        r=d*0.48; rect=QtCore.QRectF(cx-r,cy-r,2*r,2*r)

        # arc band: cyan→teal “degradation”
        con=QtGui.QConicalGradient(QtCore.QPointF(cx,cy), -180.0)
        con.setColorAt(0.00, THEME.CYAN); con.setColorAt(1.00, THEME.TEAL)
        pen=QtGui.QPen(); pen.setBrush(con); pen.setWidth(int(d*0.070)); pen.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen); p.drawArc(rect, int(-180*16), int(-180*16))

        # title
        if self.title:
            p.setFont(QtGui.QFont("Segoe UI", 12, QtGui.QFont.Weight.Black)); p.setPen(THEME.TEXT_SOFT)
            p.drawText(QtCore.QRectF(0, cy - r*0.30, w, 22), QtCore.Qt.AlignmentFlag.AlignHCenter, self.title)

        # ticks + decade labels (use superscripts), positioned slightly inside to avoid clipping
        p.setFont(self.font_tick); p.setPen(THEME.TEXT_MAIN)
        for e in range(-7,-1):
            ang=math.radians(self._angle(10**e)); r1=r*0.92; r2=r*0.985
            x1,y1=cx+r1*math.cos(ang), cy-r1*math.sin(ang)
            x2,y2=cx+r2*math.cos(ang), cy-r2*math.sin(ang)
            p.setPen(QtGui.QPen(THEME.TEXT_MAIN,2)); p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))

            # label
            label = exp_superscript(e)          # '10⁻⁵'
            rt = r*0.76                          # a hair more inwards so digits don’t clip
            tx, ty = cx + rt*math.cos(ang), cy - rt*math.sin(ang)
            fm = QtGui.QFontMetrics(self.font_tick)
            p.setPen(THEME.TEXT_SOFT)
            p.drawText(QtCore.QRectF(tx-fm.horizontalAdvance(label)/2, ty-fm.height()/2,
                                     fm.horizontalAdvance(label), fm.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, label)

            # minor ticks (2..9)
            for k in range(2,10):
                v=(10**e)*k
                if v>=10**(e+1): continue
                angm=math.radians(self._angle(v)); r1m=r*0.94; r2m=r*0.975
                p.setPen(QtGui.QPen(QtGui.QColor(180,220,255,160),1))
                xm1,ym1=cx+r1m*math.cos(angm), cy-r1m*math.sin(angm)
                xm2,ym2=cx+r2m*math.cos(angm), cy-r2m*math.sin(angm)
                p.drawLine(QtCore.QPointF(xm1,ym1), QtCore.QPointF(xm2,ym2))
        p.end(); self._layer_static=pm

    def paintEvent(self,e):
        size=self.size()
        if self._layer_static.isNull() or size!=self._cached_size: self._rebuild(size)
        p=QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing,True)
        p.drawPixmap(0,0,self._layer_static)
        w,h=self.width(),self.height(); d=min(w,h)-2; cx,cy=w/2,h/2 + d*0.04
        r=d*0.48; r_inner=d*0.36
        ang=math.radians(self._angle(self._value)); tip_r=r*0.93; base_r=r_inner*0.42
        tip=QtCore.QPointF(cx+tip_r*math.cos(ang), cy-tip_r*math.sin(ang))
        base=QtCore.QPointF(cx-base_r*math.cos(ang), cy+base_r*math.sin(ang))
        ortho=ang+math.pi/2; w1,w2=d*0.010,d*0.030
        p1=QtCore.QPointF(base.x()+w2*math.cos(ortho), base.y()-w2*math.sin(ortho))
        p2=QtCore.QPointF(base.x()-w2*math.cos(ortho), base.y()+w2*math.sin(ortho))
        p3=QtCore.QPointF(tip.x()-w1*math.cos(ortho),  tip.y()+w1*math.sin(ortho))
        p4=QtCore.QPointF(tip.x()+w1*math.cos(ortho),  tip.y()-w1*math.sin(ortho))
        path=QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        beam=QtGui.QLinearGradient(base,tip); beam.setColorAt(0, THEME.MINT); beam.setColorAt(1, QtGui.QColor(THEME.MINT.red(),THEME.MINT.green(),THEME.MINT.blue(), 10))
        p.setBrush(beam); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)
        p.setPen(THEME.MINT); p.setFont(self.font_value)
        s=f"{self._value:.2e} mbar"; fm=QtGui.QFontMetrics(self.font_value)
        p.drawText(QtCore.QRectF(cx-fm.horizontalAdvance(s)/2, cy-fm.height()*0.15, fm.horizontalAdvance(s)+6, fm.height()),
                   QtCore.Qt.AlignmentFlag.AlignCenter, s)
        p.end()

# ───────────────────────── Tiles (plot + gauge) ───────────────────────── #
class BeamTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot = TimeSeriesPlot(max_points=240, ymin=0, ymax=120, line=THEME.VIOLET)
        self.gauge = NeonGauge(min_value=0, max_value=120, value=86.8, unit="µA", title="Beam Current", decimals=1)
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)
    def set_value(self, v): self.gauge.set_value(v); self.plot.add_point(v)

class PowerTile(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot = TimeSeriesPlot(max_points=240, ymin=0, ymax=100, line=THEME.AMBER)
        self.gauge = SemiArcPowerGauge(62)
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)
    def set_value(self, v): self.gauge.set_value(v); self.plot.add_point(v)

class VacuumTile(QtWidgets.QWidget):
    def __init__(self, title="TP1", v0=3.2e-5):
        super().__init__()
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=-7, ymax=-2, line=THEME.CYAN)
        self.gauge = SemiArcVacuumGauge(v0, title)
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)
    def set_value(self, v): v=max(1e-12,float(v)); self.gauge.set_value(v); self.plot.add_point(math.log10(v))

# ───────────────────────── Main Window ───────────────────────── #
class FourGaugeWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cyclotron HUD Panel — Degradation Style")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        grid = QtWidgets.QGridLayout(self); grid.setContentsMargins(18,18,18,18); grid.setSpacing(18)

        self.w1 = HudCard(BeamTile());         grid.addWidget(self.w1,0,0)
        self.w2 = HudCard(PowerTile());        grid.addWidget(self.w2,0,1)
        self.w3 = HudCard(VacuumTile("TP2"));  grid.addWidget(self.w3,1,0)
        self.w4 = HudCard(VacuumTile("TP1"));  grid.addWidget(self.w4,1,1)

        self._timer = QtCore.QTimer(self, interval=80)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # HUD background
    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rf = QtCore.QRectF(self.rect())
        if THEME.BG_IMAGE and os.path.exists(THEME.BG_IMAGE):
            img = QtGui.QPixmap(THEME.BG_IMAGE).scaled(rf.size().toSize(),
                        QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        QtCore.Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0,0,img)
        else:
            g = QtGui.QLinearGradient(rf.topLeft(), rf.bottomLeft())
            g.setColorAt(0, THEME.BG_TOP); g.setColorAt(1, THEME.BG_BOT)
            p.fillRect(rf, g)
        vgrad = QtGui.QRadialGradient(rf.center(), float(max(rf.width(), rf.height())))
        vgrad.setColorAt(0.75, QtGui.QColor(0,0,0,0))
        vgrad.setColorAt(1.00, THEME.BG_VIGNET)
        p.fillRect(rf, vgrad)
        p.setPen(QtGui.QPen(QtGui.QColor(140,200,255,60), 1))
        p.drawLine(QtCore.QPointF(12.0, 12.0), QtCore.QPointF(rf.width()-12.0, 12.0))
        p.drawLine(QtCore.QPointF(12.0, rf.height()-12.0), QtCore.QPointF(rf.width()-12.0, rf.height()-12.0))
        p.end()

    # demo motion
    def _tick(self):
        bt: BeamTile = self.w1.layout().itemAt(0).widget()
        bv = clamp(bt.gauge.getValue() + random.uniform(-1.2,1.2), 0, 120); bt.set_value(bv)

        pt: PowerTile = self.w2.layout().itemAt(0).widget()
        kv = clamp(pt.gauge.getValue() + random.uniform(-2.0,2.0), 0, 100); pt.set_value(kv)

        for card in (self.w3, self.w4):
            vt: VacuumTile = card.layout().itemAt(0).widget()
            cur = max(vt.gauge.getValue(), 1e-9)
            base = math.log10(cur) + random.uniform(-0.010, 0.010)
            vv = 10**clamp(base, -7, -2)
            vt.set_value(vv)

# ───────────────────────── Run ───────────────────────── #
def main():
    app = QtWidgets.QApplication(sys.argv)
    win = FourGaugeWindow()
    win.resize(1520, 920)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
