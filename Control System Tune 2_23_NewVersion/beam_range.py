# beam_range.py
"""
Beam range helpers + UI widgets (neon octagon buttons in 3 columns).

- Labels are ranges: "100 pA - 1 nA", ..., "3 µA - 30 µA".
- 4-bit mux code in bits 0..3 (optionally mirrored to 4..7).
- Optional legacy one-hot (bits 28..37).
- Parse precedence: explicit index -> 4-bit nibble -> one-hot.
- U64<->double helpers (transport’s last double).
- Packet builder for 51/52-double layouts.

Widgets:
  * BeamRangePad      : 10 big neon octagon buttons (touch-friendly).
  * BeamRangeControl  : compact combobox + Apply (optional).
"""

from __future__ import annotations
from typing import Iterable, Optional, List
import struct

# ------------------------- labels -------------------------
RANGE_LABELS: List[str] = [
    "100 pA - 1 nA",
    "300 pA - 3 nA",
    "1 nA - 10 nA",
    "3 nA - 30 nA",
    "10 nA - 100 nA",
    "30 nA - 300 nA",
    "100 nA - 1 µA",
    "300 nA - 3 µA",
    "1 µA - 10 µA",
    "3 µA - 30 µA",
]
RANGE_NAMES = RANGE_LABELS  # back-compat alias
NUM_RANGES = 10
_U64_MASK = 0xFFFFFFFFFFFFFFFF

# ------------------------- codec --------------------------
class RangeCodec:
    """Encode/decode beam range into a U64 bitmask."""

    @staticmethod
    def encode_4bit(u64_base: int, idx: int, mirror: bool = True) -> int:
        """Put idx (0..15) into bits 0..3 (and 4..7 if mirror)."""
        idx = max(0, min(15, int(idx)))
        u64 = int(u64_base) & _U64_MASK
        u64 &= ~0xFF  # clear 0..7
        low = idx & 0x0F
        hi  = (idx & 0x0F) << 4 if mirror else 0
        return (u64 | hi | low) & _U64_MASK

    @staticmethod
    def decode_4bit(bitmask_u64: int) -> int:
        return int(bitmask_u64) & 0x0F

    @staticmethod
    def encode_onehot(u64_base: int, idx: int) -> int:
        """Set one-hot bit 28+idx for 10 ranges."""
        idx = max(0, min(9, int(idx)))
        u64 = int(u64_base) & _U64_MASK
        u64 &= ~(((1 << 10) - 1) << 28)
        return (u64 | (1 << (28 + idx))) & _U64_MASK

    @staticmethod
    def decode_onehot(bitmask_u64: int) -> Optional[int]:
        x = int(bitmask_u64)
        for i in range(10):
            if (x >> (28 + i)) & 1:
                return i
        return None

    @staticmethod
    def compose(u64_base: int, idx: int, mode: str = "4bit", mirror: bool = True) -> int:
        """mode: '4bit' | 'onehot' | 'both'."""
        u = int(u64_base) & _U64_MASK
        if mode in ("4bit", "both"):
            u = RangeCodec.encode_4bit(u, idx, mirror=mirror)
        if mode in ("onehot", "both"):
            u = RangeCodec.encode_onehot(u, idx)
        return u & _U64_MASK

    @staticmethod
    def parse(bitmask_u64: int, explicit_idx: Optional[int] = None) -> int:
        """Prefer explicit idx; else 4-bit nibble; else one-hot. Clamp 0..9."""
        if explicit_idx is not None:
            return max(0, min(9, int(explicit_idx)))
        idx = RangeCodec.decode_4bit(bitmask_u64)
        if 0 <= idx <= 9:
            return idx
        oh = RangeCodec.decode_onehot(bitmask_u64)
        return max(0, min(9, oh if oh is not None else 0))

    @staticmethod
    def u64_to_double(u: int) -> float:
        return float(int(u) & _U64_MASK)

    @staticmethod
    def double_to_u64(d: float) -> int:
        return int(d) & _U64_MASK  # we only use bits <= 37

# ------------------------- packet builder -------------------
def build_packet_51_52(
    ts_unix: float,
    trim14: Iterable[float],
    srcex18: Iterable[float],
    transport10: Iterable[float],
    vac5: Iterable[float],
    rf_kV: float,
    beam_v_raw: float,
    bitmask_u64: int,
    beam_range_idx: Optional[int] = None,
) -> bytes:
    """Pack 51/52 double REQ: [..., (opt idx), bitmask_as_double]."""
    t14 = list(trim14); s18 = list(srcex18); t10 = list(transport10); v5 = list(vac5)
    assert len(t14) == 14 and len(s18) == 18 and len(t10) == 10 and len(v5) == 5
    tail = [float(rf_kV), float(beam_v_raw)]
    if beam_range_idx is not None:
        tail.append(float(beam_range_idx))
    doubles = [float(ts_unix)] + t14 + s18 + t10 + v5 + tail + [RangeCodec.u64_to_double(bitmask_u64)]
    return struct.pack("<" + "d"*len(doubles), *doubles)

# =========================== PyQt6 UI =========================
try:
    from PyQt6.QtCore import pyqtSignal, Qt, QRectF, QSize
    from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton,
        QButtonGroup, QSizePolicy, QComboBox, QPushButton, QGridLayout
    )

    class _OctoButton(QWidget):
        """Neon double-outline octagon button with persistent selection light."""
        clicked = pyqtSignal()

        def __init__(self, label: str, parent=None):
            super().__init__(parent)
            self._label = label
            self._selected = False
            self._pressed = False
            self.setMinimumHeight(72)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        def setSelected(self, on: bool):
            if self._selected != on:
                self._selected = on
                self.update()

        def setLabel(self, label: str):
            self._label = label
            self.update()

        def sizeHint(self): return QSize(260, 72)
        def minimumSizeHint(self): return QSize(200, 72)

        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._pressed = True; self.update()

        def mouseReleaseEvent(self, e):
            if self._pressed and e.button() == Qt.MouseButton.LeftButton:
                self._pressed = False; self.update()
                if self.rect().contains(e.position().toPoint()):
                    self.clicked.emit()

        def _octo_path(self, w: float, h: float, margin: float, cut: float) -> QPainterPath:
            x0, y0 = margin, margin
            x1, y1 = w - margin, h - margin
            c = cut
            p = QPainterPath()
            p.moveTo(x0 + c, y0); p.lineTo(x1 - c, y0); p.lineTo(x1, y0 + c)
            p.lineTo(x1, y1 - c); p.lineTo(x1 - c, y1); p.lineTo(x0 + c, y1)
            p.lineTo(x0, y1 - c); p.lineTo(x0, y0 + c); p.closeSubpath()
            return p

        def paintEvent(self, _):
            w, h = self.width(), self.height()
            p = QPainter(self)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

            # background
            p.fillRect(self.rect(), QColor(10, 14, 24))

            # palette
            neon  = QColor(110, 230, 255)
            neon2 = QColor(150, 255, 255)
            glow  = QColor(110, 230, 255, 70)
            fill_idle = QColor(15, 30, 45, 140)
            fill_sel  = QColor(18, 60, 80, 170)
            fill_prs  = QColor(25, 120, 150, 190)

            # body + strokes
            body = self._octo_path(w, h, margin=8,  cut=14)
            inner= self._octo_path(w, h, margin=14, cut=11)
            p.fillPath(body, fill_prs if self._pressed else (fill_sel if self._selected else fill_idle))
            p.setPen(QPen(neon2 if self._selected else neon, 2.4)); p.drawPath(body)
            p.setPen(QPen(neon, 1.6)); p.drawPath(inner)

            # inner glow lines
            p.setPen(QPen(glow, 1.2))
            p.drawLine(22, 16, w - 22, 16)
            p.drawLine(22, h - 16, w - 22, h - 16)

            # pilot light (persists when selected)
            led_r = 7; led_x, led_y = w - 22, 22
            p.setPen(Qt.PenStyle.NoPen)
            if self._selected:
                p.setBrush(QColor(120, 255, 255, 230)); p.drawEllipse(led_x - led_r, led_y - led_r, 2*led_r, 2*led_r)
                p.setBrush(QColor(120, 255, 255, 120)); p.drawEllipse(led_x - led_r - 3, led_y - led_r - 3, 2*(led_r+3), 2*(led_r+3))
            else:
                p.setBrush(QColor(70, 100, 110, 120)); p.drawEllipse(led_x - led_r, led_y - led_r, 2*led_r, 2*led_r)

            # centered label
            p.setPen(QColor(220, 245, 250))
            f = QFont("Segoe UI"); f.setBold(True); f.setPixelSize(int(h * 0.34))
            p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self._label)

    class BeamRangePad(QWidget):
        """
        Touchscreen range selector: neon octagon buttons in 3 columns.
        Modes: Analog (follow), Digital (set), Auto (programmatic set).
        """
        rangeSelected = pyqtSignal(int, int)  # (idx, bitmask_u64)

        def __init__(self, parent=None, base_bitmask_u64: int = 0,
                     use_onehot_also: bool = False, mirror_nibble: bool = True,
                     columns: int = 3):
            super().__init__(parent)
            self._mode = "analog"
            self._base = int(base_bitmask_u64) & _U64_MASK
            self._use_onehot = bool(use_onehot_also)
            self._mirror = bool(mirror_nibble)
            self._idx = 0
            self._cols = max(1, int(columns))

            self.setStyleSheet("background:#0d1321; color:#e6faff;")
            v = QVBoxLayout(self); v.setContentsMargins(12,12,12,12); v.setSpacing(10)

            # Mode chips
            row = QHBoxLayout(); row.setSpacing(8)
            self.btnAnalog  = QToolButton(); self.btnAnalog.setText("Analog");  self.btnAnalog.setCheckable(True)
            self.btnDigital = QToolButton(); self.btnDigital.setText("Digital"); self.btnDigital.setCheckable(True)
            self.btnAuto    = QToolButton(); self.btnAuto.setText("Auto");      self.btnAuto.setCheckable(True)
            for b in (self.btnAnalog, self.btnDigital, self.btnAuto):
                b.setStyleSheet("""
                    QToolButton {
                        background: #0f2230; border: 1px solid #66d6ff; border-radius: 10px;
                        padding: 6px 12px; color:#bfefff; font-weight:600;
                    }
                    QToolButton:checked { background:#114b5f; color:#e6ffff; }
                """)
                row.addWidget(b)
            v.addLayout(row)
            group = QButtonGroup(self); group.setExclusive(True)
            for b in (self.btnAnalog, self.btnDigital, self.btnAuto): group.addButton(b)
            self.btnAnalog.setChecked(True)
            self.btnAnalog.clicked.connect(lambda _checked=False: self._set_mode("analog"))
            self.btnDigital.clicked.connect(lambda _checked=False: self._set_mode("digital"))
            self.btnAuto.clicked.connect(lambda _checked=False: self._set_mode("auto"))

            # Grid: 3 columns
            grid = QGridLayout(); grid.setSpacing(10)
            self._buttons: List[_OctoButton] = []
            for idx, nm in enumerate(RANGE_LABELS):
                b = _OctoButton(nm, self)
                b.clicked.connect(lambda i=idx: self._on_tap(i))  # fixed: no stray '_' arg
                r = idx // self._cols; c = idx % self._cols
                grid.addWidget(b, r, c); self._buttons.append(b)
            v.addLayout(grid)

            self.lbl = QLabel("Following hardware"); self.lbl.setStyleSheet("color:#8adfff;")
            v.addWidget(self.lbl)

            self._refresh_enabled()

        # public API
        def set_base_bitmask(self, u64_base: int):
            self._base = int(u64_base) & _U64_MASK

        def follow_from_u64(self, u64: int, explicit_idx: int | None = None):
            if self._mode != "analog": return
            idx = RangeCodec.parse(u64, explicit_idx)
            self._highlight(idx)
            self.lbl.setText(f"Following hardware: {RANGE_LABELS[idx]}")

        def follow_from_bitmask_double(self, dbl: float, explicit_idx: int | None = None):
            self.follow_from_u64(RangeCodec.double_to_u64(dbl), explicit_idx)

        def request_set(self, idx: int):
            self._on_tap(max(0, min(NUM_RANGES-1, int(idx))))

        # internals
        def _set_mode(self, m: str):
            self._mode = m; self._refresh_enabled()
            self.lbl.setText("Software control" if m != "analog" else "Following hardware")

        def _refresh_enabled(self):
            for b in self._buttons: b.setEnabled(self._mode != "analog")

        def _highlight(self, idx: int):
            self._idx = idx
            for i, b in enumerate(self._buttons): b.setSelected(i == idx)

        def _on_tap(self, idx: int):
            self._highlight(idx)  # keep selection + pilot light ON
            if self._mode == "analog": return
            u64 = RangeCodec.compose(self._base, idx,
                                     mode=("both" if self._use_onehot else "4bit"),
                                     mirror=self._mirror)
            self.rangeSelected.emit(idx, u64)
            self.lbl.setText(f"Set to {RANGE_LABELS[idx]} (pending send)")

    # Optional compact control (dropdown)
    class BeamRangeControl(QWidget):
        rangeSelected = pyqtSignal(int, int)
        def __init__(self, parent=None, base_bitmask_u64: int = 0,
                     use_onehot_also: bool = False, mirror_nibble: bool = True, mode: str = "analog"):
            super().__init__(parent)
            self._mode = mode; self._base = int(base_bitmask_u64) & _U64_MASK
            self._use_onehot = bool(use_onehot_also); self._mirror = bool(mirror_nibble)
            lay = QVBoxLayout(self)
            row1 = QHBoxLayout()
            self.rbAnalog = QToolButton(); self.rbAnalog.setText("Analog"); self.rbAnalog.setCheckable(True)
            self.rbDigital= QToolButton(); self.rbDigital.setText("Digital"); self.rbDigital.setCheckable(True)
            self.rbAuto   = QToolButton(); self.rbAuto.setText("Auto");     self.rbAuto.setCheckable(True)
            for b in (self.rbAnalog, self.rbDigital, self.rbAuto):
                b.setStyleSheet("QToolButton{background:#0f2230;border:1px solid #66d6ff;border-radius:8px;padding:4px 10px;color:#bfefff;font-weight:600;} QToolButton:checked{background:#114b5f;color:#e6ffff;}")
                row1.addWidget(b)
            group = QButtonGroup(self); group.setExclusive(True)
            for b in (self.rbAnalog, self.rbDigital, self.rbAuto): group.addButton(b)
            (self.rbAnalog if mode=="analog" else self.rbDigital if mode=="digital" else self.rbAuto).setChecked(True)
            lay.addLayout(row1)
            row2 = QHBoxLayout()
            self.cb = QComboBox(); self.cb.addItems(RANGE_LABELS)
            self.btnApply = QPushButton("Apply")
            row2.addWidget(QLabel("Beam range:")); row2.addWidget(self.cb, 1); row2.addWidget(self.btnApply)
            lay.addLayout(row2)
            self.lbl = QLabel("Following hardware"); lay.addWidget(self.lbl)
            self.rbAnalog.clicked.connect(lambda _checked=False: self._set_mode("analog"))
            self.rbDigital.clicked.connect(lambda _checked=False: self._set_mode("digital"))
            self.rbAuto.clicked.connect(lambda _checked=False: self._set_mode("auto"))
            self.btnApply.clicked.connect(self._apply)
            self._refresh()
        def set_base_bitmask(self, u64_base: int): self._base = int(u64_base) & _U64_MASK
        def follow_from_bitmask_double(self, dbl: float, explicit_idx: int | None = None):
            if self._mode != "analog": return
            idx = RangeCodec.parse(RangeCodec.double_to_u64(dbl), explicit_idx)
            self.cb.setCurrentIndex(idx); self.lbl.setText(f"Following hardware: {RANGE_LABELS[idx]}")
        def _set_mode(self, m: str): self._mode = m; self._refresh(); self.lbl.setText("Software control" if m!="analog" else "Following hardware")
        def _refresh(self): self.btnApply.setEnabled(self._mode == "digital")
        def _apply(self):
            if self._mode != "digital": return
            idx = self.cb.currentIndex()
            u64 = RangeCodec.compose(self._base, idx, mode=("both" if self._use_onehot else "4bit"), mirror=True)
            self.rangeSelected.emit(idx, u64); self.lbl.setText(f"Set to {RANGE_LABELS[idx]} (pending send)")

except Exception:
    BeamRangePad = None   # type: ignore
    BeamRangeControl = None  # type: ignore
