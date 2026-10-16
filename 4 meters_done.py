# -*- coding: utf-8 -*-
"""
Cyclotron 2×2 HUD — Neon 0..1 gauges + Penning-style vacuum gauges.

Top-left:  Beam Current (µA)    — normalized neon radial + needle + status
Top-right: Power (kV)           — normalized neon radial + needle + status
Bottom:    TP2 / TP1 (mbar)     — semicircle logarithmic gauge (10⁻⁷..10⁻²) + needle + status

Each tile keeps a real-time time-domain plot. Demo timer generates motion.
"""

import math, sys, random, collections, os
from math import sin, cos, radians, log10
from PyQt6 import QtCore, QtGui, QtWidgets

# ───────────────────────────── Theme ───────────────────────────── #
class THEME:
    BG_TOP    = QtGui.QColor(7, 12, 22)
    BG_BOT    = QtGui.QColor(5, 9, 18)
    BG_VIGNET = QtGui.QColor(0, 0, 0, 170)
    BG_IMAGE  = ""  # optional path to background image

    CYAN      = QtGui.QColor( 70, 210, 255)
    TEAL      = QtGui.QColor( 30, 180, 255)
    BLUE      = QtGui.QColor( 78, 130, 255)
    VIOLET    = QtGui.QColor(160, 100, 255)
    AMBER     = QtGui.QColor(255, 190,  70)
    REDHOT    = QtGui.QColor(255,  90,  80)
    MINT      = QtGui.QColor(120, 255, 170)

    TEXT_MAIN = QtGui.QColor(225, 238, 255)
    TEXT_SOFT = QtGui.QColor(168, 190, 218)

    CARD_BORDER = QtGui.QColor(100, 210, 255, 90)
    CARD_GLOW   = QtGui.QColor( 30, 150, 220, 70)

def clamp(v,a,b): return a if v<a else b if v>b else v

# ───────────────────────── Helpers ───────────────────────── #
def exp_sup(e: int) -> str:
    """Return '10' with superscript exponent, e.g., 10⁻⁵."""
    sup = {"-":"⁻","0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}
    return "10" + "".join(sup.get(ch, ch) for ch in f"^{e}")

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
        p.setPen(QtGui.QPen(THEME.CARD_GLOW, 8));  p.drawPath(path)
        p.setPen(QtGui.QPen(THEME.CARD_BORDER, 1.3)); p.drawPath(path)

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

        # grid (use QPointF so floats are accepted)
        p.setPen(QtGui.QPen(self.col_grid, 1))
        rows, cols = 8, 6
        for i in range(1, rows):
            y = float(h) * (i / rows)
            p.drawLine(QtCore.QPointF(0.0, y), QtCore.QPointF(float(w), y))
        for j in range(1, cols):
            x = float(w) * (j / cols)
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

        p.setPen(QtGui.QPen(self.col_axes, 1)); p.drawLine(0, h-1, w, h-1)
        p.end()

# ────────────────────── Neon 0..1 Radial Gauge (needle + status) ───────────────────── #
class NeonRadial01(QtWidgets.QWidget):
    """
    Normalized (0..1) radial gauge with dotted inner ring, needle, status halo,
    and dual readout (big normalized, small physical). 0.5 is fixed at the top.
    """
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None, value=0.25, title="Gauge", unit="", *,
                 status_bands=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self._value = float(clamp(value, 0.0, 1.0))
        self.title  = title
        self.unit   = unit

        # geometry: big sweep, 0.5 at top (-90°)
        self.span_deg  = 280.0
        self.start_deg = 90.0 + self.span_deg/2.0
        self.clockwise = True

        # palette
        self.c_bg        = QtGui.QColor(6, 12, 20)
        self.c_tick_ma   = QtGui.QColor(200, 240, 255, 230)
        self.c_tick_mi   = QtGui.QColor(200, 240, 255, 140)
        self.c_cyan      = QtGui.QColor(140, 230, 255, 240)
        self.c_cyan_dim  = QtGui.QColor(60, 120, 180, 120)
        self.c_title     = THEME.TEXT_SOFT
        self.c_readout   = THEME.TEXT_MAIN

        # status colors / bands (in normalized units)
        self.col_ok    = QtGui.QColor(120,255,170,220)
        self.col_warn  = QtGui.QColor(255,190,70,220)
        self.col_alarm = QtGui.QColor(255,90,80,220)
        self.status_bands = status_bands or dict(ok=(0.2,0.8), warn=(0.1,0.9))

        # dotted ring
        self.dot_count      = 48
        self.dot_len_frac   = 0.06
        self.dot_thick_frac = 0.028
        self.dot_radius_frac= 0.52
        self.c_dot_main = QtGui.QColor(90, 200, 255, 210)
        self.c_dot_fade = QtGui.QColor(40, 120, 170, 160)

        # readout precisions
        self.display_decimals = 2
        self.phys_decimals    = 2
        self._phys_text       = ""

        # animation
        self._anim = QtCore.QPropertyAnimation(self, b"value", duration=220)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

    # property
    def getValue(self): return self._value
    def setValue(self, norm):
        v = clamp(float(norm), 0.0, 1.0)
        if abs(v-self._value) < 1e-9: return
        self._value = v; self.valueChanged.emit(v); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)

    def set_value(self, norm: float, animated=True):
        norm = clamp(float(norm), 0.0, 1.0)
        if animated:
            self._anim.stop(); self._anim.setStartValue(self._value); self._anim.setEndValue(norm); self._anim.start()
        else:
            self.setValue(norm)

    def set_physical(self, value: float|str, unit: str, decimals: int|None=None):
        """Set small physical readout under the big normalized number."""
        if isinstance(value, str):
            self._phys_text = f"{value} {unit}".rstrip()
        else:
            if decimals is None: decimals = self.phys_decimals
            try:
                self._phys_text = f"{float(value):.{decimals}f} {unit}".rstrip()
            except Exception:
                self._phys_text = f"{value} {unit}".rstrip()
        self.update()

    # helpers
    def _value_to_angle(self, norm):
        start, span = self.start_deg, self.span_deg
        return start - span * norm if self.clockwise else start + span * norm

    def _status_color(self, norm):
        ok_lo, ok_hi = self.status_bands.get("ok", (0.0, 1.0))
        warn_lo, warn_hi = self.status_bands.get("warn", (0.0, 1.0))
        if ok_lo <= norm <= ok_hi:   return self.col_ok
        if warn_lo <= norm <= warn_hi: return self.col_warn
        return self.col_alarm

    def _draw_inner_dotted_ring(self, p, cx, cy, R):
        ring_r = R * self.dot_radius_frac
        seg_len = max(3.0, R * self.dot_len_frac)
        seg_th  = max(2.0, R * self.dot_thick_frac)
        p.save()
        for i in range(self.dot_count):
            ang = (360.0 / self.dot_count) * i
            p.save(); p.translate(cx, cy); p.rotate(ang); p.translate(ring_r, 0)
            rect = QtCore.QRectF(-seg_th/2, -seg_len/2, seg_th, seg_len)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(self.c_dot_fade); p.drawRoundedRect(rect, seg_th*0.35, seg_th*0.35)
            hl = QtCore.QRectF(-seg_th/2, -seg_len/2, seg_th, seg_len*0.55)
            p.setBrush(self.c_dot_main); p.drawRoundedRect(hl, seg_th*0.35, seg_th*0.35)
            p.restore()
        p.restore()

    def _draw_ticks_labels(self, p, cx, cy, R):
        start, span = self.start_deg, self.span_deg
        dir_mult = -1.0 if self.clockwise else 1.0
        r_out, r_major_in, r_minor_in = R*0.94, R*0.86, R*0.90

        # major + minor ticks
        for i in range(10 + 1):
            frac = i / 10.0
            a_deg = start + dir_mult * span * frac
            a = radians(a_deg)
            x1, y1 = cx + r_out * cos(a), cy - r_out * sin(a)
            x2, y2 = cx + r_major_in * cos(a), cy - r_major_in * sin(a)
            if abs(frac - 0.5) < 1e-6:
                p.setPen(QtGui.QPen(QtGui.QColor(220,255,255,255), 3.2))
            else:
                p.setPen(QtGui.QPen(self.c_tick_ma, 2.2))
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))

            if i < 10:
                p.setPen(QtGui.QPen(self.c_tick_mi, 1.4))
                for m in range(1, 3):
                    f = (i + m/3.0) / 10.0
                    am = start + dir_mult * span * f
                    amr = radians(am)
                    xm1, ym1 = cx + r_out * cos(amr), cy - r_out * sin(amr)
                    xm2, ym2 = cx + r_minor_in * cos(amr), cy - r_minor_in * sin(amr)
                    p.drawLine(QtCore.QPointF(xm1,ym1), QtCore.QPointF(xm2,ym2))

        # labels 0.0..1.0
        f_px = max(8, int(min(self.width(), self.height()) * 0.030))
        f = QtGui.QFont("Segoe UI", f_px, QtGui.QFont.Weight.Medium)
        r_label = (R*0.76) * 0.92
        for i in range(11):
            frac = i / 10.0
            a_deg = start - span * frac if self.clockwise else start + span * frac
            a = radians(a_deg)
            tx, ty = cx + r_label * cos(a), cy - r_label * sin(a)
            s = f"{frac:.1f}"
            if abs(frac - 0.5) < 1e-6:
                fb = QtGui.QFont(f); fb.setWeight(QtGui.QFont.Weight.Bold); p.setFont(fb)
                p.setPen(QtGui.QColor(220,255,255,255))
            else:
                p.setFont(f); p.setPen(QtGui.QColor(200,235,255,220))
            fm = QtGui.QFontMetricsF(p.font())
            p.drawText(QtCore.QRectF(tx - fm.horizontalAdvance(s)/2,
                                     ty - fm.height()/2,
                                     fm.horizontalAdvance(s), fm.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, s)

    def _draw_needle(self, p, cx, cy, R, norm):
        a_deg = self._value_to_angle(norm); a = radians(a_deg)
        dirx, diry = cos(a), -sin(a); nx, ny = -diry, dirx
        r_tip = R * 0.76; r_base = R * 0.18
        tip  = QtCore.QPointF(cx + dirx*r_tip,  cy + diry*r_tip)
        base = QtCore.QPointF(cx - dirx*r_base, cy - diry*r_base)
        w_tip = R * 0.015; w_base = R * 0.035
        p1 = QtCore.QPointF(base.x()+nx*w_base, base.y()+ny*w_base)
        p2 = QtCore.QPointF(base.x()-nx*w_base, base.y()-ny*w_base)
        p3 = QtCore.QPointF(tip.x()-nx*w_tip,  tip.y()-ny*w_tip)
        p4 = QtCore.QPointF(tip.x()+nx*w_tip,  tip.y()+ny*w_tip)
        path = QtGui.QPainterPath(); path.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        grad = QtGui.QLinearGradient(base, tip); grad.setColorAt(0.0, QtGui.QColor(255,200,120)); grad.setColorAt(1.0, QtGui.QColor(255,250,170,10))
        p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(path)
        hub_r = R * 0.06
        glow = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), hub_r*2.0)
        glow.setColorAt(0.0, QtGui.QColor(255,160,80,220)); glow.setColorAt(0.7, QtGui.QColor(255,110,40,120)); glow.setColorAt(1.0, QtGui.QColor(0,0,0,0))
        p.setBrush(glow); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing |
                         QtGui.QPainter.RenderHint.TextAntialiasing)

        rect = self.rect()
        s = min(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        R = s * 0.46

        # background
        bg = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), R * 1.25)
        bg.setColorAt(0.0, QtGui.QColor(10,20,35))
        bg.setColorAt(1.0, self.c_bg)
        p.fillRect(rect, QtGui.QBrush(bg))

        # inner ring + center
        inner_ring_r = R * 0.40
        p.setPen(QtGui.QPen(QtGui.QColor(160,230,255,220), 4))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r, inner_ring_r)
        p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(0,0,0))
        p.drawEllipse(QtCore.QPointF(cx, cy), inner_ring_r-2.0, inner_ring_r-2.0)

        # dotted ring
        self._draw_inner_dotted_ring(p, cx, cy, R)

        # status halo color
        status_col = self._status_color(self._value)

        # value arc
        a_val = self._value_to_angle(self._value)
        start, span = self.start_deg, self.span_deg
        R_arc = R * 0.76
        rect_val = QtCore.QRectF(cx - R_arc, cy - R_arc, 2*R_arc, 2*R_arc)
        pen_bg = QtGui.QPen(self.c_cyan_dim, 8); pen_bg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen_fg = QtGui.QPen(status_col, 8);      pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg); p.drawArc(rect_val, int(start*16), int((-span if self.clockwise else span)*16))
        sweep = a_val - start; sweep = -abs(sweep) if self.clockwise else sweep
        p.setPen(pen_fg); p.drawArc(rect_val, int(start*16), int(sweep*16))

        # ticks + labels
        self._draw_ticks_labels(p, cx, cy, R)

        # needle
        self._draw_needle(p, cx, cy, R, self._value)

        # readout (0..1 big)
        txt = f"{self._value:.{self.display_decimals}f}"
        f = QtGui.QFont("Segoe UI", max(10, int(s*0.075)), QtGui.QFont.Weight.DemiBold)
        p.setFont(f); p.setPen(self.c_readout)
        br = QtGui.QFontMetricsF(f).boundingRect(txt)
        p.drawText(QtCore.QPointF(cx - br.width()/2, cy + br.height()/4), txt)

        # small physical line if set
        if self._phys_text:
            f2 = QtGui.QFont("Segoe UI", max(9, int(s*0.04)))
            p.setFont(f2); p.setPen(QtGui.QColor(150,210,255))
            fm2 = QtGui.QFontMetricsF(f2)
            p.drawText(QtCore.QPointF(cx - fm2.horizontalAdvance(self._phys_text)/2,
                                      cy + br.height()*0.95),
                       self._phys_text)

        # title
        if self.title:
            f3 = QtGui.QFont("Segoe UI", max(9, int(s*0.045)), QtGui.QFont.Weight.DemiBold)
            p.setFont(f3); p.setPen(self.c_title)
            p.drawText(QtCore.QRectF(0, cy + R*0.45, self.width(), R*0.16),
                       QtCore.Qt.AlignmentFlag.AlignHCenter, self.title)

        p.end()

# ────────────────────── Semicircle LOG Vacuum Gauge ───────────────────── #
class LogVacuumGauge(QtWidgets.QWidget):
    """
    Semicircle vacuum gauge with log scale 1e-7..1e-2 mbar:
      - Major labels: 10⁻⁷ … 10⁻² (like the Penning meter)
      - Minor ticks: 2..9 per decade
      - Needle + status halo (lower is better by default)
    """
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None, *, title="TP", unit="mbar",
                 vmin=1e-7, vmax=1e-2, value=3.2e-5,
                 ok=(0.00, 0.40), warn=(0.40, 0.70)):
        super().__init__(parent)
        self.setMinimumSize(300, 220)
        self.title, self.unit = title, unit
        self.vmin, self.vmax = float(vmin), float(vmax)
        self.lomin, self.lomax = math.log10(self.vmin), math.log10(self.vmax)
        self._value = self._clamp_v(float(value))
        self.ok_band, self.warn_band = ok, warn

        # colors
        self.c_bg       = QtGui.QColor(6, 12, 20)
        self.c_arc_dim  = QtGui.QColor(60, 120, 180, 110)
        self.c_tick_ma  = QtGui.QColor(220, 240, 255, 230)
        self.c_tick_mi  = QtGui.QColor(200, 230, 255, 150)
        self.c_title    = QtGui.QColor(168, 190, 218)
        self.c_value    = QtGui.QColor(225, 238, 255)

        self.col_ok     = QtGui.QColor(120,255,170,220)
        self.col_warn   = QtGui.QColor(255,190, 70,220)
        self.col_alarm  = QtGui.QColor(255, 90, 80,220)

        self.font_title = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Weight.DemiBold)
        self.font_value = QtGui.QFont("Segoe UI", 30, QtGui.QFont.Weight.Black)
        self.font_tick  = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.DemiBold)

        self._anim = QtCore.QPropertyAnimation(self, b"value", duration=220)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.InOutSine)

    # property
    def getValue(self): return self._value
    def setValue(self, v: float):
        v = self._clamp_v(float(v))
        if abs(v - self._value) < 1e-12: return
        self._value = v; self.valueChanged.emit(v); self.update()
    value = QtCore.pyqtProperty(float, fget=getValue, fset=setValue)

    def set_value(self, v: float, animated=True):
        v = self._clamp_v(float(v))
        if animated:
            self._anim.stop(); self._anim.setStartValue(self._value); self._anim.setEndValue(v); self._anim.start()
        else:
            self.setValue(v)

    # mapping
    def _clamp_v(self, v): return max(self.vmin, min(self.vmax, v))
    def _norm(self, v):
        lv = math.log10(self._clamp_v(v))
        return (lv - self.lomin) / ((self.lomax - self.lomin) or 1.0)  # 0 @ 1e-7, 1 @ 1e-2

    def _angle(self, v):
        # left (180°) = low pressure, right (0°) = high
        f = self._norm(v)
        return 180.0 - 180.0 * f

    def _status_color(self, f):
        lo_ok, hi_ok = self.ok_band
        lo_w,  hi_w  = self.warn_band
        if lo_ok <= f <= hi_ok: return self.col_ok
        if lo_w  <= f <= hi_w:  return self.col_warn
        return self.col_alarm

    # drawing
    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w,h = self.width(), self.height(); d = min(w,h)-2
        cx, cy = w/2, h/2 + d*0.04
        R  = d*0.48
        rect = QtCore.QRectF(cx-R, cy-R, 2*R, 2*R)

        # bg
        bg = QtGui.QRadialGradient(QtCore.QPointF(cx,cy), R*1.1)
        bg.setColorAt(0.0, QtGui.QColor(10,20,35)); bg.setColorAt(1.0, self.c_bg)
        p.fillRect(self.rect(), bg)

        # base band
        pen_base = QtGui.QPen(self.c_arc_dim, int(d*0.07))
        pen_base.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen_base); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(-180*16), int(-180*16))

        # status foreground sweep from left to current
        fcur = self._norm(self._value)
        col  = self._status_color(fcur)
        pen_fg = QtGui.QPen(col, int(d*0.07)); pen_fg.setCapStyle(QtCore.Qt.PenCapStyle.FlatCap)
        p.setPen(pen_fg)
        p.drawArc(rect, int(-180*16), int(-180*fcur*16))

        # ticks + decade labels
        p.setFont(self.font_tick)
        for e10 in range(-7, -1):  # -7 .. -2
            ang = math.radians(self._angle(10**e10))
            r1, r2 = R*0.90, R*0.985
            x1,y1 = cx + r1*math.cos(ang), cy - r1*math.sin(ang)
            x2,y2 = cx + r2*math.cos(ang), cy - r2*math.sin(ang)
            p.setPen(QtGui.QPen(self.c_tick_ma, 2))
            p.drawLine(QtCore.QPointF(x1,y1), QtCore.QPointF(x2,y2))

            # decade label 10^e
            label = exp_sup(e10)
            rt = R*0.73
            tx,ty = cx + rt*math.cos(ang), cy - rt*math.sin(ang)
            fm = QtGui.QFontMetrics(self.font_tick)
            p.setPen(self.c_tick_ma)
            p.drawText(QtCore.QRectF(tx-fm.horizontalAdvance(label)/2, ty-fm.height()/2,
                                     fm.horizontalAdvance(label), fm.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, label)

            # minor ticks 2..9
            for k in range(2,10):
                v = (10**e10) * k
                if v >= 10**(e10+1): continue
                angm = math.radians(self._angle(v))
                r1m, r2m = R*0.92, R*0.975
                xm1,ym1 = cx + r1m*math.cos(angm), cy - r1m*math.sin(angm)
                xm2,ym2 = cx + r2m*math.cos(angm), cy - r2m*math.sin(angm)
                p.setPen(QtGui.QPen(self.c_tick_mi, 1))
                p.drawLine(QtCore.QPointF(xm1,ym1), QtCore.QPointF(xm2,ym2))

        # needle
        ang = math.radians(self._angle(self._value))
        dirx, diry = math.cos(ang), -math.sin(ang)
        nx, ny = -diry, dirx
        r_tip, r_base = R*0.93, R*0.42
        tip  = QtCore.QPointF(cx + dirx*r_tip,  cy + diry*r_tip)
        base = QtCore.QPointF(cx - dirx*r_base, cy - diry*r_base)
        w_tip, w_base = d*0.010, d*0.030
        p1 = QtCore.QPointF(base.x()+nx*w_base, base.y()+ny*w_base)
        p2 = QtCore.QPointF(base.x()-nx*w_base, base.y()-ny*w_base)
        p3 = QtCore.QPointF(tip.x()-nx*w_tip,  tip.y()-ny*w_tip)
        p4 = QtCore.QPointF(tip.x()+nx*w_tip,  tip.y()+ny*w_tip)
        needle = QtGui.QPainterPath(); needle.addPolygon(QtGui.QPolygonF([p1,p2,p3,p4]))
        grad = QtGui.QLinearGradient(base, tip); grad.setColorAt(0, QtGui.QColor(255,200,90)); grad.setColorAt(1, QtGui.QColor(255,230,120,10))
        p.setBrush(grad); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawPath(needle)

        # hub glow
        hub_r = d*0.052
        rg = QtGui.QRadialGradient(QtCore.QPointF(cx,cy), hub_r*2.0)
        rg.setColorAt(0, QtGui.QColor(255,160,80,220)); rg.setColorAt(0.7, QtGui.QColor(255,110,40,120)); rg.setColorAt(1, QtGui.QColor(0,0,0,0))
        p.setBrush(rg); p.drawEllipse(QtCore.QRectF(cx-hub_r, cy-hub_r, 2*hub_r, 2*hub_r))

        # value (scientific) + unit
        p.setPen(self.c_value); p.setFont(self.font_value)
        vtxt = f"{self._value:.2e}"
        fm = QtGui.QFontMetrics(self.font_value)
        p.drawText(QtCore.QRectF(cx-fm.horizontalAdvance(vtxt)/2, cy-fm.height()*0.2,
                                 fm.horizontalAdvance(vtxt)+6, fm.height()),
                   QtCore.Qt.AlignmentFlag.AlignCenter, vtxt)

        if self.unit:
            f2 = QtGui.QFont("Segoe UI", 14, QtGui.QFont.Weight.DemiBold)
            p.setFont(f2); p.setPen(self.c_title)
            fm2 = QtGui.QFontMetrics(f2)
            p.drawText(QtCore.QRectF(cx-fm2.horizontalAdvance(self.unit)/2, cy+fm.height()*0.55,
                                     fm2.horizontalAdvance(self.unit)+6, fm2.height()),
                       QtCore.Qt.AlignmentFlag.AlignCenter, self.unit)

        # title
        if self.title:
            p.setFont(self.font_title); p.setPen(self.c_title)
            p.drawText(QtCore.QRectF(0, cy - R*0.35, w, 24),
                       QtCore.Qt.AlignmentFlag.AlignHCenter, self.title)

        p.end()

# ───────────────────────── Tiles (plot + gauges) ───────────────────────── #
class BeamTile(QtWidgets.QWidget):
    def __init__(self, min_uA=0.0, max_uA=120.0):
        super().__init__()
        self.min_uA, self.max_uA = float(min_uA), float(max_uA)
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot = TimeSeriesPlot(max_points=240, ymin=self.min_uA, ymax=self.max_uA, line=THEME.VIOLET)
        self.gauge = NeonRadial01(value=0.0, title="Beam Current",
                                  status_bands=dict(ok=(0.25,0.75), warn=(0.15,0.85)))
        self.gauge.phys_decimals = 1
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)

    def set_value(self, uA):
        uA = clamp(float(uA), self.min_uA, self.max_uA)
        norm = (uA - self.min_uA)/((self.max_uA - self.min_uA) or 1.0)
        self.gauge.set_value(norm)
        self.gauge.set_physical(uA, "µA", decimals=1)
        self.plot.add_point(uA)

class PowerTile(QtWidgets.QWidget):
    def __init__(self, min_kV=0.0, max_kV=100.0):
        super().__init__()
        self.min_kV, self.max_kV = float(min_kV), float(max_kV)
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot = TimeSeriesPlot(max_points=240, ymin=self.min_kV, ymax=self.max_kV, line=THEME.AMBER)
        self.gauge = NeonRadial01(value=0.0, title="Power",
                                  status_bands=dict(ok=(0.30,0.80), warn=(0.15,0.90)))
        self.gauge.phys_decimals = 0
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)

    def set_value(self, kV):
        kV = clamp(float(kV), self.min_kV, self.max_kV)
        norm = (kV - self.min_kV)/((self.max_kV - self.min_kV) or 1.0)
        self.gauge.set_value(norm)
        self.gauge.set_physical(kV, "kV", decimals=0)
        self.plot.add_point(kV)

class VacuumTile(QtWidgets.QWidget):
    """Vacuum tile: plot (log10) + semicircle log gauge with decade labels."""
    def __init__(self, title="TP1", v0=3.2e-5, vmin=1e-7, vmax=1e-2):
        super().__init__()
        inner = QtWidgets.QHBoxLayout(self); inner.setContentsMargins(8,8,8,8); inner.setSpacing(12)
        self.plot  = TimeSeriesPlot(max_points=240, ymin=-7, ymax=-2, line=THEME.CYAN)
        self.gauge = LogVacuumGauge(title=title, unit="mbar", vmin=vmin, vmax=vmax, value=v0,
                                    ok=(0.00,0.40), warn=(0.40,0.70))
        self.plot.setFixedWidth(300)
        inner.addWidget(self.plot,0); inner.addWidget(self.gauge,0)
        self.set_value(v0)

    def set_value(self, v):
        v = max(1e-12, float(v))
        self.gauge.set_value(v)
        self.plot.add_point(math.log10(v))

# ───────────────────────── Main Window ───────────────────────── #
class FourGaugeWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cyclotron HUD Panel — Neon + Penning Gauges")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        grid = QtWidgets.QGridLayout(self); grid.setContentsMargins(18,18,18,18); grid.setSpacing(18)

        self.w1 = HudCard(BeamTile());          grid.addWidget(self.w1,0,0)
        self.w2 = HudCard(PowerTile());         grid.addWidget(self.w2,0,1)
        self.w3 = HudCard(VacuumTile("TP2"));   grid.addWidget(self.w3,1,0)
        self.w4 = HudCard(VacuumTile("TP1"));   grid.addWidget(self.w4,1,1)

        self._timer = QtCore.QTimer(self, interval=80)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def paintEvent(self, e):
        # HUD background
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
        cur_uA = clamp((bt.plot._buf[-1] if bt.plot._buf else 60.0) + random.uniform(-1.2,1.2), 0, 120)
        bt.set_value(cur_uA)

        pt: PowerTile = self.w2.layout().itemAt(0).widget()
        cur_kV = clamp((pt.plot._buf[-1] if pt.plot._buf else 62.0) + random.uniform(-2.0,2.0), 0, 100)
        pt.set_value(cur_kV)

        for card in (self.w3, self.w4):
            vt: VacuumTile = card.layout().itemAt(0).widget()
            last_log = (vt.plot._buf[-1] if vt.plot._buf else math.log10(3.2e-5))
            base = last_log + random.uniform(-0.010, 0.010)
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
