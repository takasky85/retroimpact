"""Qt side of the vector icons: painted with QPainter in the current theme's colours, sharp at any size / DPI."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

from .icon_art import GRID, STROKE, art

SIZES = (16, 20, 24, 28, 32, 40, 48, 56, 64, 96)  # logical sizes pre-rendered; Qt picks the nearest for the DPI
_cache: dict = {}


def paint(p: QPainter, name: str, rect: QRectF, color: QColor, halo: Optional[QColor] = None) -> None:
    """Draw icon ``name`` filling ``rect``. ``halo`` adds a contrasting outline (used for mouse cursors)."""
    ic = art(name)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(rect.topLeft())
    p.scale(rect.width() / GRID, rect.height() / GRID)

    def draw(col: QColor, extra: float):
        pen = QPen(col, STROKE + extra)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pts, closed in ic.strokes:
            path = QPainterPath(QPointF(*pts[0]))
            for q in pts[1:]:
                path.lineTo(QPointF(*q))
            if closed:
                path.closeSubpath()
            p.drawPath(path)
        fp = QPen(col, extra) if extra else QPen(Qt.PenStyle.NoPen)
        fp.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(fp)
        p.setBrush(QBrush(col))
        for pts in ic.fills:
            p.drawPolygon(QPolygonF([QPointF(*q) for q in pts]))

    if halo is not None:
        draw(halo, 2.8)
    draw(color, 0.0)
    p.restore()


def _text(group: QPalette.ColorGroup = QPalette.ColorGroup.Active) -> QColor:
    return QApplication.palette().color(group, QPalette.ColorRole.ButtonText)


def pixmap(name: str, size: int = 24, color: Optional[QColor] = None, dpr: float = 1.0,
           halo: Optional[QColor] = None) -> QPixmap:
    px = QPixmap(max(1, int(round(size * dpr))), max(1, int(round(size * dpr))))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    paint(p, name, QRectF(0, 0, size, size), color or _text(), halo)
    p.end()
    return px


def icon(name: str) -> QIcon:
    """Theme-aware icon: normal and disabled colours come from the application palette (cached per palette)."""
    on, off = _text(), _text(QPalette.ColorGroup.Disabled)
    key = (name, on.rgba(), off.rgba())
    ic = _cache.get(key)
    if ic is None:
        ic = QIcon()
        for s in SIZES:
            ic.addPixmap(pixmap(name, s, on), QIcon.Mode.Normal, QIcon.State.Off)
            ic.addPixmap(pixmap(name, s, off), QIcon.Mode.Disabled, QIcon.State.Off)
        _cache[key] = ic
    return ic


def cursor_pixmap(name: str, size: int = 30) -> QPixmap:
    """Black glyph with a white halo so a cursor stays visible on any background."""
    return pixmap(name, size, QColor(18, 18, 18), 1.0, QColor(255, 255, 255))


def app_icon() -> QIcon:
    """The window / taskbar icon: a white printer on a dark rounded tile (the same on every theme)."""
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        px = QPixmap(s, s)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2b2d31"))
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
        paint(p, "printer", QRectF(s * 0.13, s * 0.13, s * 0.74, s * 0.74), QColor("#f4f4f4"))
        p.end()
        ic.addPixmap(px)
    return ic
