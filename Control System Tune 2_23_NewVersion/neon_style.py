# neon_style.py — minimal neon helpers (PyQt6)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QBrush, QIcon
from PyQt6.QtWidgets import QWidget, QLabel, QPushButton, QGraphicsDropShadowEffect

class NeonBackgroundMixin:
    def init_neon_background(self, image_path: str, dimmer: float = 0.10):
        self._bg_pix = QPixmap(image_path) if image_path else QPixmap()
        self._bg_dimmer = max(0.0, min(1.0, float(dimmer)))

    def paint_neon_background(self, p: QPainter, w: QWidget):
        if not hasattr(self, "_bg_pix") or self._bg_pix.isNull():
            p.fillRect(w.rect(), QColor(5, 10, 18))
        else:
            p.drawPixmap(
                w.rect(),
                self._bg_pix.scaled(
                    w.rect().size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        p.fillRect(w.rect(), QColor(0, 0, 0, int(255 * self._bg_dimmer)))

# color groups
_COLORS = [QColor(0,220,255), QColor(80,255,140), QColor(255,110,160), QColor(255,210,90)]
def _c(i): return _COLORS[i % len(_COLORS)]

def make_glow_label(text: str, parent: QWidget | None = None, group: int = 0) -> QLabel:
    lab = QLabel(text, parent)
    col = _c(group)
    lab.setStyleSheet(
        "font-size:16px;font-weight:600;letter-spacing:0.5px;"
        f"color: rgba({col.red()},{col.green()},{col.blue()},235);"
    )
    eff = QGraphicsDropShadowEffect(lab)
    eff.setBlurRadius(28); eff.setOffset(0, 0)
    eff.setColor(QColor(col.red(), col.green(), col.blue(), 180))
    lab.setGraphicsEffect(eff)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab

class BubbleButton(QPushButton):
    def __init__(self, parent: QWidget | None = None, group: int = 0, icon_path: str | None = None):
        super().__init__(parent); self._col = _c(group)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True); self.setFixedSize(138, 138)
        if icon_path:
            self.setIcon(QIcon(icon_path)); self.setIconSize(QSize(82, 82))
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect(); d = min(r.width(), r.height()); rad = d/2 - 2
        # rim
        p.setPen(QPen(QColor(self._col.red(), self._col.green(), self._col.blue(), 220), 3))
        p.setBrush(QBrush(QColor(self._col.red(), self._col.green(), self._col.blue(), 60)))
        p.drawEllipse(r.center(), rad, rad)
        # inner disc
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(QColor(10, 18, 28)))
        p.drawEllipse(r.center(), rad*0.78, rad*0.78)
        # icon
        if not self.icon().isNull():
            sz = self.iconSize()
            self.icon().paint(p, (r.width()-sz.width())//2, (r.height()-sz.height())//2, sz.width(), sz.height())
