"""
Palette Board — Canvas-side temporary paint area.

Freehand brush strokes on a QImage canvas.
Colour source: Krita foreground colour (set externally).
Canvas width expands with widget; height is fixed at 120px.
"""

from PyQt5.QtCore import Qt, QPoint, QSize
from PyQt5.QtGui import QColor, QImage, QMouseEvent, QPaintEvent, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

import logging

class PaletteBoard(QWidget):
    """Freehand paintable board with dynamic-width canvas."""

    CANVAS_HEIGHT = 120

    def __init__(self, parent=None, color_provider=None, color_setter=None):
        super().__init__(parent)

        # Color source — injected from outside, never call Krita directly
        self._color_provider = color_provider
        self._color_setter = color_setter

        # Canvas — width expands with widget, height fixed
        self._canvas = QImage(1, self.CANVAS_HEIGHT, QImage.Format_RGB32)
        self._canvas.fill(QColor(40, 40, 40))  # #282828

        # Brush state
        self._drawing = False
        self._last_pos = QPoint()
        self._brush_color = QColor(200, 200, 200)
        self._brush_size = 20

        # Track whether user has painted (for blank-state hint)
        self._has_painted = False

        # Cursor overlay state
        self._cursor_pos = QPoint(-100, -100)
        self._picker_mode = False
        self.setMouseTracking(True)

        self.setMinimumHeight(self.CANVAS_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #2a2a2a; border: 1px solid #444;")

    # ---- Public API --------------------------------------------------------

    def set_brush_size(self, size: int):
        self._brush_size = max(2, min(80, size))

    def clear(self):
        self._canvas.fill(QColor(40, 40, 40))
        self._has_painted = False
        self.update()

    def get_canvas(self):
        return self._canvas.copy()

    def set_canvas(self, canvas: QImage):
        if canvas is not None and not canvas.isNull():
            self._canvas = canvas.copy()
            self._has_painted = True
            self.update()

    # ---- Canvas helpers ----------------------------------------------------

    def _ensure_canvas(self):
        w = max(self.width(), 1)
        h = self.CANVAS_HEIGHT
        if self._canvas.width() != w or self._canvas.height() != h:
            old = self._canvas
            self._canvas = QImage(w, h, QImage.Format_RGB32)
            self._canvas.fill(QColor(40, 40, 40))
            if old is not None and not old.isNull():
                painter = QPainter(self._canvas)
                painter.drawImage(0, 0, old)
                painter.end()

    # ---- Painting ----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)

        # Draw canvas top-left aligned (fills full widget width)
        painter.drawImage(0, 0, self._canvas)

        # Blank-state hint (shown until first brush stroke)
        if not self._has_painted:
            painter.setPen(QPen(QColor(100, 100, 100)))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Paint your palette here",
            )

        # Cursor overlay (brush circle or picker crosshair)
        cx = self._cursor_pos.x()
        cy = self._cursor_pos.y()
        if 0 <= cx < self.width() and 0 <= cy < self.height():
            if self._picker_mode:
                painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
                cross = 8
                painter.drawLine(cx - cross, cy, cx + cross, cy)
                painter.drawLine(cx, cy - cross, cx, cy + cross)
                painter.drawEllipse(cx - 2, cy - 2, 4, 4)
            else:
                radius = self._brush_size / 2.0
                painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(
                    int(cx - radius), int(cy - radius),
                    self._brush_size, self._brush_size
                )

    # ---- Mouse interaction -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._ensure_canvas()
            cx = max(0, min(event.pos().x(), self._canvas.width() - 1))
            cy = max(0, min(event.pos().y(), self._canvas.height() - 1))

            if event.modifiers() & Qt.ShiftModifier:
                # Shift+click: pick color from board and set as Krita foreground
                picked = self._canvas.pixelColor(cx, cy)
                if picked.isValid():
                    self._brush_color = QColor(picked.red(), picked.green(), picked.blue(), 255)
                    logging.getLogger("ct_navigator").debug(f"picked: {self._brush_color.name()}")
                    if self._color_setter is not None:
                        try:
                            self._color_setter(self._brush_color)
                        except Exception as e:
                            logging.getLogger("ct_navigator").debug(f"setter error: {e}")
                    self.update()
            else:
                # Normal click: paint with current Krita foreground color
                self._drawing = True
                self._has_painted = True
                self._last_pos = event.pos()
                raw = self._get_brush_color()
                self._brush_color = QColor(raw.red(), raw.green(), raw.blue(), 255)
                logging.getLogger("ct_navigator").debug(f"mousePress: brush={self._brush_color.name()}")
                painter = QPainter(self._canvas)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.setPen(
                    QPen(
                        self._brush_color,
                        self._brush_size,
                        Qt.SolidLine,
                        Qt.RoundCap,
                        Qt.RoundJoin,
                    )
                )
                painter.drawPoint(self._last_pos)
                painter.end()
                self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self._cursor_pos = event.pos()
        self._picker_mode = bool(event.modifiers() & Qt.ShiftModifier)
        if self._drawing and (event.buttons() & Qt.LeftButton):
            self._ensure_canvas()
            painter = QPainter(self._canvas)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.setPen(
                QPen(
                    self._brush_color,
                    self._brush_size,
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            painter.drawLine(self._last_pos, event.pos())
            painter.end()
            self._last_pos = event.pos()
            self.update()
            logging.getLogger("ct_navigator").debug(f"mouseMove: brush={self._brush_color.name()} drawing={self._drawing}")
        else:
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drawing = False
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.setCursor(Qt.BlankCursor)
        from PyQt5.QtWidgets import QApplication
        self._picker_mode = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        self._cursor_pos = QPoint(-100, -100)
        self.update()
        super().leaveEvent(event)

    # ---- Sizing ------------------------------------------------------------

    def resizeEvent(self, event):
        self._ensure_canvas()
        super().resizeEvent(event)

    def _get_brush_color(self):
        """Fetch current color from the injected provider."""
        if self._color_provider is not None:
            try:
                color = self._color_provider()
                if color is not None and color.isValid():
                    return QColor(color.red(), color.green(), color.blue(), 255)
            except Exception as e:
                logging.getLogger("ct_navigator").debug(f"provider error: {e}")
        return QColor(255, 255, 255)

