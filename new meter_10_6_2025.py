# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 19:10:56 2025

@author: clasa
"""

# -*- coding: utf-8 -*-
"""
Neon radial gauge — metallic annular ring, clockwise sweep, 0..1 labels.
Forward-only needle, black center, proper draw order.
"""

from math import sin, cos, radians
import sys, random
from PyQt6 import QtCore, QtGui, QtWidgets


def p_polar(cx, cy, r, a_deg):
    a = radians(a_deg)
    return QtCore.QPointF(cx + r * cos(a), cy - r * sin(a))


class NeonRadialGauge(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None, min_v=0.0, max_v=240.0, value=160.0, unit="km/h"):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

        # scale
        self._min, self._max = float(min_v), float(max_v)
        self._value = float(value)
        self._unit = unit
        self._anim_target = self._value

        # CLOCKWISE sweep
        self.clockwise = True

        # CORONA MODE: "metal" or "color"
        self.corona_mode = "metal"

        # animation timers
        self._t = 0.0
        self._pulse = QtCore.QTimer(self)
        self._pulse.timeout.connect(self._tick)
        self._pulse.start(16)
        self._lerp = QtCore.QTimer(self)
        self._lerp.timeout.connect(self._step_to_target)
        self._lerp.start(20)

        # palette
        self.c_bg = QtGui.QColor(6, 12, 20)
        self.c_tick_major = QtGui.QColor(200, 240, 255, 230)
        self.c_tick_minor = QtGui.QColor(200, 240, 255, 140)
        self.c_ring = QtGui.QColor(120, 220, 255, 180)
        self.c_cyan = QtGui.QColor(140, 230, 255, 240)
        self.c_cyan_dim = QtGui.QColor(60, 120, 180, 120)

        # needle config
        self.nd_front_len = 0.76
        self.nd_base_from_c = 0.12
        self.nd_base_w = 8.0
        self.nd_tip_w = 2.0
        self.nd_color_tip = QtGui.QColor(255, 120, 70)
        self.nd_color_base = QtGui.QColor(180, 40, 30)
        self.nd_edge_highlight = QtGui.QColor(255, 255, 255, 200)
        self.nd_glow = QtGui.QColor(255, 90, 40, 90)

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ---------------------------------------------------------
    # core internals
    def setValue(self, v: float):
        v = max(self._min, min(self._max, float(v)))
        self._anim_target = v

    def value(self) -> float:
        return self._value

    def _step_to_target(self):
        v, t = self._value, self._anim_target
        v += (t - v) * 0.12
        if abs(t - v) < 0.01:
            v = t
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
            self.update()

    def _tick(self):
        self._t += 0.016
        self.update()

    def _value_to_angle(self, v):
        start, span = -220.0, 280.0
        r = 0 if self._max == self._min else (v - self._min) / (self._max - self._min)
        return start - span * r if self.clockwise else start + span * r

    # ---------------------------------------------------------
    # helpers
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

    def _draw_norm_labels(self, p, cx, cy, radius, start_deg, span_deg, steps=10):
        f_px = max(8, int(min(self.width(), self.height()) * 0.030))
        f = QtGui.QFont("Segoe UI", f_px, QtGui.QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QtGui.QPen(QtGui.QColor(200, 235, 255, 220)))
        r_label = radius * 0.92
        for i in range(steps + 1):
            frac = i / steps
            a_deg = start_deg - span_deg * frac if self.clockwise else start_deg + span_deg * frac
            a = radians(a_deg)
            tx, ty = cx + r_label * cos(a), cy - r_label * sin(a)
            s = f"{frac:.1f}"
            fm = QtGui.QFontMetricsF(f)
            p.drawText(QtCore.QRectF(tx - fm.horizontalAdvance(s)/2,
                                     ty - fm.height()/2,
                                     fm.horizontalAdvance(s),
                                     fm.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, s)

    def _draw_arc_ticks(self, p, cx, cy, R, start_deg, span_deg, majors=10, minors_between=2):
        dir_mult = -1.0 if self.clockwise else 1.0
        r_out, r_major_in, r_minor_in = R*0.94, R*0.86, R*0.90
        p.setPen(QtGui.QPen(self.c_tick_major, 2.2))
        for i in range(majors + 1):
            frac = i / majors
            a_deg = start_deg + dir_mult * span_deg * frac
            a = radians(a_deg)
            x1, y1 = cx + r_out * cos(a), cy - r_out * sin(a)
            x2, y2 = cx + r_major_in * cos(a), cy - r_major_in * sin(a)
            p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))
            if i < majors and minors_between > 0:
                p.setPen(QtGui.QPen(self.c_tick_minor, 1.4))
                for m in range(1, minors_between + 1):
                    f = (i + m / (minors_between + 1)) / majors
                    am = start_deg + dir_mult * span_deg * f
                    amr = radians(am)
                    xm1, ym1 = cx + r_out * cos(amr), cy - r_out * sin(amr)
                    xm2, ym2 = cx + r_minor_in * cos(amr), cy - r_minor_in * sin(amr)
                    p.drawLine(QtCore.QPointF(xm1, ym1), QtCore.QPointF(xm2, ym2))
                p.setPen(QtGui.QPen(self.c_tick_major, 2.2))

    # ---------------------------------------------------------
    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                         QtGui.QPainter.RenderHint.TextAntialiasing)

        rect = self.rect()
        s = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        R = s * 0.46

        # background gradient
        bg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R * 1.25)
        bg.setColorAt(0.0, QtGui.QColor(10, 20, 35))
        bg.setColorAt(1.0, self.c_bg)
        p.fillRect(rect, QtGui.QBrush(bg))

        # --- CORONA ANNULAR BAND ---
        r_in, r_out = R*0.36, R*0.56
        if self.corona_mode == "color":
            ring = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
            inner_stop = r_in / r_out
            ring.setColorAt(0.0, QtGui.QColor(0,0,0,0))
            ring.setColorAt(inner_stop, QtGui.QColor(240,60,60,220))
            ring.setColorAt(1.0, QtGui.QColor(130,20,20,180))
            self._fill_annulus(p, cx, cy, r_in, r_out, ring)
        else:
            base = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), r_out)
            inner_stop = r_in / r_out
            base.setColorAt(0.0, QtGui.QColor(0,0,0,0))
            base.setColorAt(inner_stop, QtGui.QColor(110,120,130,220))
            base.setColorAt(1.0, QtGui.QColor(50,55,60,220))
            self._fill_annulus(p, cx, cy, r_in, r_out, base)
            sheen = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), -90.0)
            sheen.setColorAt(0.00, QtGui.QColor(255,255,255,0))
            sheen.setColorAt(0.47, QtGui.QColor(255,255,255,70))
            sheen.setColorAt(0.53, QtGui.QColor(255,255,255,70))
            sheen.setColorAt(1.00, QtGui.QColor(255,255,255,0))
            self._fill_annulus(p, cx, cy, r_in, r_out, sheen)
            p.setPen(QtGui.QPen(QtGui.QColor(255,255,255,50), 1.2))
            p.drawEllipse(QtCore.QPointF(cx, cy), r_out, r_out)
            p.setPen(QtGui.QPen(QtGui.QColor(0,0,0,90), 1.2))
            p.drawEllipse(QtCore.QPointF(cx, cy), r_in, r_in)

        # --- INNER CYAN RING ---
        inner_ring_r = R * 0.40
        p.setPen(QtGui.QPen(QtGui.QColor(160,230,255,220), 4))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r, inner_ring_r)

        # --- BLACK CENTER ---
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(0,0,0))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r - 2.0, inner_ring_r - 2.0)

        # --- VALUE ARC ---
        a_val = self._value_to_angle(self._value)
        start, span = -220.0, 280.0
        R_arc = R * 0.76
        rect_val = QtCore.QRectF(cx - R_arc, cy - R_arc, 2*R_arc, 2*R_arc)
        p.save()
        pen_bg = QtGui.QPen(self.c_cyan_dim, 8); pen_bg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen_fg = QtGui.QPen(self.c_cyan, 8);     pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        p.drawArc(rect_val, int(start*16), int((-span if self.clockwise else span)*16))
        sweep = a_val - start
        if self.clockwise: sweep = -abs(sweep)
        p.setPen(pen_fg)
        p.drawArc(rect_val, int(start*16), int(sweep*16))
        p.restore()

        # --- TICKS + LABELS ---
        self._draw_arc_ticks(p, cx, cy, R, start_deg=start, span_deg=span, majors=10, minors_between=2)
        self._draw_norm_labels(p, cx, cy, R_arc, start_deg=start, span_deg=span, steps=10)

        # --- NEEDLE ---
        self._draw_speedo_needle(p, cx, cy, R, a_val)

        # --- NUMERIC READOUT ---
        txt = f"{self._value:0.0f}"
        f = QtGui.QFont("Segoe UI", max(10, int(s*0.075)), QtGui.QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QtGui.QColor(200,240,255))
        br = QtGui.QFontMetricsF(f).boundingRect(txt)
        p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()/4), txt)
        if self._unit:
            f2 = QtGui.QFont("Segoe UI", max(8, int(s*0.04)))
            p.setFont(f2)
            p.setPen(QtGui.QColor(150,210,255))
            p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()*0.9), self._unit)

    # ---------------------------------------------------------
    def _draw_speedo_needle(self, p, cx, cy, R, a_deg):
        a = radians(a_deg)
        dirx, diry = cos(a), -sin(a)
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

        # glow
        glow_path = QtGui.QPainterPath()
        for pt in (baseL, tipL, tipR, baseR):
            if glow_path.isEmpty(): glow_path.moveTo(pt)
            else: glow_path.lineTo(pt)
        glow_path.closeSubpath()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(self.nd_glow)
        p.drawPath(glow_path)

        # needle fill gradient
        grad = QtGui.QLinearGradient(tipL, baseR)
        grad.setColorAt(0.0, self.nd_color_tip)
        grad.setColorAt(1.0, self.nd_color_base)

        path = QtGui.QPainterPath()
        for pt in (baseL, tipL, tipR, baseR):
            if path.isEmpty(): path.moveTo(pt)
            else: path.lineTo(pt)
        path.closeSubpath()
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        p.drawPath(path)

        pen_hi = QtGui.QPen(self.nd_edge_highlight, 2.0)
        pen_hi.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_hi)
        p.drawLine(tipR, baseR)


class Demo(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Neon Radial Gauge — Metallic Annular Ring Fixed")
        self.g = NeonRadialGauge(min_v=0, max_v=240, value=27, unit="km/h")
        self.g.setMinimumSize(420, 420)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 2400)
        self.slider.valueChanged.connect(lambda v: self.g.setValue(v/10.0))

        btn = QtWidgets.QPushButton("Random value")
        btn.clicked.connect(lambda: self.g.setValue(random.uniform(0, 240)))

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.g, 1)
        lay.addWidget(self.slider)
        lay.addWidget(btn)
        self.slider.setValue(int(self.g.value()*10))

        self._drift = QtCore.QTimer(self)
        self._drift.timeout.connect(self._auto)
        self._drift.start(2200)

    def _auto(self):
        v = self.g.value() + random.uniform(-12, 12)
        self.g.setValue(max(self.g._min, min(self.g._max, v)))
        self.slider.setValue(int(self.g.value()*10))


if __name__ == "__main__":
    # Re-use the existing Qt app if you're in Spyder/IPython, else create one
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    w = Demo()
    w.resize(560, 640)
    w.show()                     # ← important
    sys.exit(app.exec())         # ← enters the event loop

