"""``ElidedLabel`` — a ``QLabel`` that elides the middle of long text
when the available width shrinks. ``setText``/``text`` keep the raw
string so the elision is purely a render-time concern."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._raw_text = text

    def setText(self, text):
        self._raw_text = text
        super().setText(text)
        self.update()

    def text(self):
        return self._raw_text

    def paintEvent(self, event):
        painter = QPainter(self)
        metrics = self.fontMetrics()
        elided = metrics.elidedText(self._raw_text, Qt.ElideMiddle, self.width())
        painter.drawText(self.rect(), self.alignment(), elided)
