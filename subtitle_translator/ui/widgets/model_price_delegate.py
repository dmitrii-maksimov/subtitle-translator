"""Custom ``QStyledItemDelegate`` for the model picker dropdown.

Renders each item as four columns:
``[ current-marker | model id (elided middle) | input price | output price ]``.
The closed combo still uses the plain item text; only the open popup is
redrawn here. Prices come from ``UserRole + 1`` (``MODEL_PRICE_ROLE``)
as a dict or ``None``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)


MODEL_PRICE_ROLE = Qt.UserRole + 1
# Legacy alias kept for in-tree callers using the underscore name.
_MODEL_PRICE_ROLE = MODEL_PRICE_ROLE


class ModelPriceDelegate(QStyledItemDelegate):
    """Highlights the current selection with a bullet and shows price
    columns aligned on the right."""

    PADDING = 10
    MARKER_COL = 18
    IN_COL = 110
    OUT_COL = 120

    def __init__(self, combo):
        super().__init__(combo)
        self._combo = combo

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        price = index.data(MODEL_PRICE_ROLE)
        model_id = index.data(Qt.UserRole) or index.data() or ""

        rect = option.rect
        fm = painter.fontMetrics()

        marker_rect = rect.adjusted(self.PADDING, 0, 0, 0)
        marker_rect.setWidth(self.MARKER_COL)
        out_rect = rect.adjusted(0, 0, -self.PADDING, 0)
        out_rect.setLeft(out_rect.right() - self.OUT_COL)
        in_rect = rect.adjusted(0, 0, 0, 0)
        in_rect.setLeft(out_rect.left() - self.IN_COL)
        in_rect.setRight(out_rect.left() - 2)
        id_rect = rect.adjusted(self.PADDING + self.MARKER_COL, 0, 0, 0)
        id_rect.setRight(in_rect.left() - 2)

        selected = bool(option.state & QStyle.State_Selected)
        is_current = index.row() == self._combo.currentIndex()

        if selected:
            fg = option.palette.color(QPalette.HighlightedText)
            muted = fg
        else:
            fg = option.palette.color(QPalette.Text)
            muted = QColor(fg.red(), fg.green(), fg.blue(), 165)

        painter.save()

        if is_current:
            painter.setPen(fg)
            painter.drawText(marker_rect, int(Qt.AlignCenter), "●")

        painter.setPen(fg)
        id_text = fm.elidedText(str(model_id), Qt.ElideMiddle, id_rect.width())
        painter.drawText(id_rect, int(Qt.AlignLeft | Qt.AlignVCenter), id_text)

        if price is not None:
            painter.setPen(muted)
            in_text = f"${price.get('input', 0):g} in"
            out_text = f"${price.get('output', 0):g} out /1M"
            painter.drawText(in_rect, int(Qt.AlignRight | Qt.AlignVCenter), in_text)
            painter.drawText(out_rect, int(Qt.AlignRight | Qt.AlignVCenter), out_text)
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 24))
        return s


# Legacy alias kept for in-tree callers using the underscore name.
_ModelPriceDelegate = ModelPriceDelegate
