"""Small Qt plots for pre-trial GP predictions and candidate comparisons."""
import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QPen


class GPStatisticsPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(240)
        self.points = []
        self.title = ''
        self.axis = ''

    def set_points(self, title, axis, points):
        self.title, self.axis, self.points = title, axis, points
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#101a28'))
        painter.setPen(QColor('#d9e5f4'))
        painter.drawText(12, 23, self.title)
        painter.drawText(12, self.height() - 8, self.axis)
        points = [p for p in self.points if all(math.isfinite(v) for v in p[:4])]
        if not points:
            painter.drawText(18, 65, 'GP statistics become available after initialization.')
            return
        xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
        ymin = min(min(p[2], p[4]) if p[4] is not None else p[2] for p in points)
        ymax = max(max(p[3], p[4]) if p[4] is not None else p[3] for p in points)
        if xmax == xmin:
            xmin, xmax = xmin - .5, xmax + .5
        padding = max((ymax - ymin) * .1, 1e-6)
        ymin, ymax = ymin - padding, ymax + padding
        left, top, width, height = 80, 45, max(1, self.width()-105), max(1, self.height()-85)
        painter.drawText(5, top + 8, f'{ymax:.3g}')
        painter.drawText(5, top + height, f'{ymin:.3g}')
        painter.drawText(left, top + height + 16, f'{xmin:.3g}')
        painter.drawText(left + width - 50, top + height + 16, f'{xmax:.3g}')
        def xy(x, y):
            return int(left + (x-xmin)/(xmax-xmin)*width), int(top + (ymax-y)/(ymax-ymin)*height)
        for x, mean, low, high, observed, selected in points:
            painter.setPen(QPen(QColor('#64d7ee') if selected else QColor('#69859e'), 2 if selected else 1))
            px, ly = xy(x, low)
            _, hy = xy(x, high)
            painter.drawLine(px, ly, px, hy)
            _, my = xy(x, mean)
            painter.drawEllipse(px-3, my-3, 6, 6)
            if observed is not None and math.isfinite(observed):
                painter.setPen(QPen(QColor('#ffc369'), 2))
                _, oy = xy(x, observed)
                painter.drawLine(px-4, oy-4, px+4, oy+4)
                painter.drawLine(px-4, oy+4, px+4, oy-4)
