from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap, QPolygonF


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor("#8b5cf6"))
        g.setColorAt(1, QColor("#ec4899"))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.setBrush(QColor("white"))
        s = size
        p.drawPolygon(QPolygonF([QPointF(s * 0.38, s * 0.28), QPointF(s * 0.38, s * 0.72), QPointF(s * 0.74, s * 0.5)]))
        p.end()
        icon.addPixmap(pm)
    return icon
