# -*- coding: utf-8 -*-
"""
Created on Wed Sep 17 18:31:45 2025

@author: clasa
"""

# SpinWheelCalWindow.py — touch-friendly wheel selector + neon bubble +/- for Raw→Eng
from math import atan2, degrees, cos, sin, pi
from PyQt6.QtCore import Qt, QPointF, QRectF, QEvent, QSize
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPolygonF
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

# graceful fallback if neon_style missing
try:
    from neon_style import NeonBackgroundMixin, BubbleButton, make_glow_label
except Exception:
    from PyQt6.QtWidgets import QLabel, QPushButton
    class NeonBackgroundMixin:
        def init_neon_background(self, *_a, **_k): pass
        def paint_neon_background(self, *_a, **_k): pass
    def make_glow_label(t, parent=None, group=0):
        lab = QLabel(t, parent); lab.setAlignment(Qt.AlignmentFlag.AlignCenter); return lab
    class BubbleButton(QPushButton): pass

from scaling import Scaler, all_keys, label_for, group_for

BACKGROUND_IMAGE = r"C:\Users\clasa\Downloads\background_thinned.png"


class WheelWidget(QWidget):
    def __init__(self, items, on_changed, parent=None):
        super().__init__(parent)
        self.items = items[:]          # [(key, label)]
        self.on_changed = on_changed
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._angle_deg = 0.0
        self._dragging = False
        self._drag_last_angle = None
        self._slice_deg = 360.0 / max(1, len(self.items))
        self.setMinimumSize(420, 420)

    def sizeHint(self): return QSize(520, 520)

    def _normalize(self, a):
        while a < 0: a += 360.0
        while a >= 360.0: a -= 360.0
        return a

    def _current_index(self):
        a = self._normalize(-self._angle_deg)
        idx = int((a + self._slice_deg/2.0) // self._slice_deg) % len(self.items)
        return idx

    def current_key(self): return self.items[self._current_index()][0]

    def set_to_index(self, idx: int):
        idx = max(0, min(len(self.items)-1, idx))
        self._angle_deg = -idx * self._slice_deg
        self.update()
        if self.on_changed: self.on_changed(idx)

    # touch/mouse drag
    def _angle_from_pos(self, pos):
        c = self.rect().center()
        return degrees(atan2(pos.y() - c.y(), pos.x() - c.x()))
    def _begin_drag(self, pos): self._dragging = True; self._drag_last_angle = self._angle_from_pos(pos)
    def _update_drag(self, pos):
        if not self._dragging: return
        a_now = self._angle_from_pos(pos); da = a_now - self._drag_last_angle
        self._drag_last_angle = a_now; self._angle_deg += da; self.update()
    def _end_drag(self):
        if not self._dragging: return
        self._dragging = False
        cur = self._normalize(-self._angle_deg)
        snapped = round(cur / self._slice_deg) * self._slice_deg
        self._angle_deg = -snapped; self.update()
        if self.on_changed: self.on_changed(self._current_index())

    def event(self, e):
        if e.type() == QEvent.Type.TouchBegin:
            pts = e.points()
            if pts: self._begin_drag(pts[0].position()); return True
        elif e.type() == QEvent.Type.TouchUpdate:
            pts = e.points()
            if pts: self._update_drag(pts[0].position()); return True
        elif e.type() in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
            self._end_drag(); return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._begin_drag(e.position())
        super().mousePressEvent(e)
    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton: self._update_drag(e.position())
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._end_drag()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.TextAntialiasing)
        rect = self.rect(); size = min(rect.width(), rect.height()) - 20
        cx, cy = rect.center().x(), rect.center().y()
        wheel_rect = QRectF(cx - size/2, cy - size/2, size, size)

        N = len(self.items)
        if not N: return

        # rotate wheel
        p.save(); p.translate(cx, cy); p.rotate(self._angle_deg); p.translate(-cx, -cy)

        fonts = QFont(self.font()); fonts.setPointSizeF(max(10.0, size * 0.035)); p.setFont(fonts)
        palette = [QColor(0, 207, 255), QColor(52, 248, 130), QColor(255, 100, 130),
                   QColor(255, 210, 85), QColor(140, 120, 255), QColor(255, 140, 90)]
        border = QPen(QColor(0, 0, 0, 220), 2)

        # segments
        for i in range(N):
            start_deg16 = int((i * 360 / N) * 16)
            span_deg16  = int((360 / N) * 16)
            p.setPen(border); p.setBrush(QBrush(palette[i % len(palette)]))
            p.drawPie(wheel_rect, start_deg16, span_deg16)

        # labels (radial)
        p.setPen(QColor(0, 0, 0))
        for i in range(N):
            mid_angle = (i + 0.5) * (2 * pi / N)
            r = size * 0.33
            x = cx + r * cos(mid_angle); y = cy - r * sin(mid_angle)
            label = self.items[i][1]
            w = size * 0.36; h = size * 0.10
            p.drawText(QRectF(x - w/2, y - h/2, w, h), Qt.AlignmentFlag.AlignCenter, label)

        p.restore()

        # center hub
        p.setPen(QPen(QColor(0, 255, 255, 220), 2))
        p.setBrush(QBrush(QColor(0, 40, 70, 180)))
        p.drawEllipse(QPointF(cx, cy), size*0.08, size*0.08)

        # fixed pointer (right side)
        tri = QPolygonF([
            QPointF(cx + size*0.52, cy),
            QPointF(cx + size*0.45, cy - size*0.06),
            QPointF(cx + size*0.45, cy + size*0.06),
        ])
        p.setPen(QPen(QColor(20, 220, 255), 3))
        p.setBrush(QColor(0, 160, 230, 230))
        p.drawPolygon(tri)


class SpinWheelCalWindow(NeonBackgroundMixin, QWidget):
    def __init__(self, scaler: Scaler | None = None):
        super().__init__()
        self.scaler = scaler or Scaler()
        self.setWindowTitle("Touch Spin-Wheel Calibration — CNL")
        self.setGeometry(120, 80, 1180, 780)
        self.init_neon_background(BACKGROUND_IMAGE, dimmer=0.10)

        keys = all_keys()
        items = [(k, f"{group_for(k)} • {label_for(k)}" if group_for(k) else label_for(k)) for k in keys]
        self.items = items
        self.wheel = WheelWidget(self.items, on_changed=self._on_wheel_changed)

        # readouts
        self.lbl_title = make_glow_label("—", self, group=0)
        self.read_gain = make_glow_label("Gain: 1.000000", self, group=1)
        self.read_offs = make_glow_label("Offset: 0.000000", self, group=1)

        # bubble controls
        self.btn_gain_up   = BubbleButton(self, group=0); self.btn_gain_up.setToolTip("Gain +")
        self.btn_gain_down = BubbleButton(self, group=0); self.btn_gain_down.setToolTip("Gain −")
        self.btn_offs_up   = BubbleButton(self, group=2); self.btn_offs_up.setToolTip("Offset +")
        self.btn_offs_down = BubbleButton(self, group=2); self.btn_offs_down.setToolTip("Offset −")

        self.lab_gain = make_glow_label("Raw→Eng Gain", self, group=0)
        self.lab_offs = make_glow_label("Raw→Eng Offset", self, group=2)

        self.btn_save   = BubbleButton(self, group=3); self.lab_save   = make_glow_label("Save", self, group=3)
        self.btn_reload = BubbleButton(self, group=3); self.lab_reload = make_glow_label("Reload", self, group=3)

        # wiring
        self.btn_gain_up.clicked.connect(lambda: self._nudge_gain(+self._gain_step()))
        self.btn_gain_down.clicked.connect(lambda: self._nudge_gain(-self._gain_step()))
        self.btn_offs_up.clicked.connect(lambda: self._nudge_offs(+self._offs_step()))
        self.btn_offs_down.clicked.connect(lambda: self._nudge_offs(-self._offs_step()))
        self.btn_save.clicked.connect(self._save); self.btn_reload.clicked.connect(self._reload)

        # layout
        root = QHBoxLayout(self)
        left = QVBoxLayout(); left.addWidget(self.wheel, 1)
        right = QVBoxLayout()
        right.addWidget(self.lbl_title); right.addWidget(self.read_gain); right.addWidget(self.read_offs)
        right.addSpacing(16)

        # two columns of bubbles
        col = QHBoxLayout(); col.addStretch(1)
        col1 = QVBoxLayout(); col1.addWidget(self.btn_gain_up); col1.addWidget(self.btn_gain_down); col1.addWidget(self.lab_gain)
        col2 = QVBoxLayout(); col2.addWidget(self.btn_offs_up); col2.addWidget(self.btn_offs_down); col2.addWidget(self.lab_offs)
        col.addLayout(col1); col.addSpacing(24); col.addLayout(col2); col.addStretch(1)
        right.addLayout(col); right.addStretch(1)

        sr = QHBoxLayout(); sr.addStretch(1)
        s1 = QVBoxLayout(); s1.addWidget(self.btn_save);   s1.addWidget(self.lab_save)
        s2 = QVBoxLayout(); s2.addWidget(self.btn_reload); s2.addWidget(self.lab_reload)
        sr.addLayout(s1); sr.addSpacing(20); sr.addLayout(s2); sr.addStretch(1)
        right.addLayout(sr)

        root.addLayout(left, 3); root.addLayout(right, 2)

        self.wheel.set_to_index(0)

    # steps
    def _gain_step(self): return 0.05
    def _offs_step(self): return 0.10

    # selection change
    def _on_wheel_changed(self, idx: int):
        key, label = self.items[idx]
        self.lbl_title.setText(f"Selected: {label}")
        self.scaler.reload_if_changed()
        ent = self.scaler.data.get(key, {})
        rt = ent.get("raw_to_eng", {})
        g = float(rt.get("gain", 1.0)); b = float(rt.get("offset", 0.0))
        self.read_gain.setText(f"Gain: {g:.6f}"); self.read_offs.setText(f"Offset: {b:.6f}")

    def _current_key(self):
        return self.items[self.wheel._current_index()][0]

    def _nudge_gain(self, delta):
        k = self._current_key()
        self.scaler.reload_if_changed()
        ent = self.scaler.data.get(k, {})
        rt = ent.setdefault("raw_to_eng", {"type":"linear","gain":1.0,"offset":0.0})
        rt["gain"] = float(rt.get("gain", 1.0)) + float(delta)
        self.read_gain.setText(f"Gain: {rt['gain']:.6f}")

    def _nudge_offs(self, delta):
        k = self._current_key()
        self.scaler.reload_if_changed()
        ent = self.scaler.data.get(k, {})
        rt = ent.setdefault("raw_to_eng", {"type":"linear","gain":1.0,"offset":0.0})
        rt["offset"] = float(rt.get("offset", 0.0)) + float(delta)
        self.read_offs.setText(f"Offset: {rt['offset']:.6f}")

    def _save(self): self.scaler.save()
    def _reload(self): self.scaler.load(); self._on_wheel_changed(self.wheel._current_index())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.TextAntialiasing)
        self.paint_neon_background(p, self)
