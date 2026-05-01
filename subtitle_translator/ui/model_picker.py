"""Model picker UI: dropdown of chat models with prices, plus a Custom
mode that swaps the dropdown for a manual ``QLineEdit``.

All functions take ``window`` (a ``MainWindow``) and mutate its
attributes. Settings save and effective-model sync stay on ``window``
(via :meth:`MainWindow._on_settings_changed`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ..pricing import MODEL_PRICING, format_pricing, get_pricing
from .widgets.model_price_delegate import MODEL_PRICE_ROLE, ModelPriceDelegate


def build_model_picker(window) -> QWidget:
    """Composite widget: combo + Refresh + Custom checkbox + custom input.

    Attaches to ``window``: ``model_combo``, ``model_custom_input``,
    ``btn_refresh_models``, ``model_custom_checkbox``.
    """
    s = window.settings

    wrap = QWidget()
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    window.model_combo = QComboBox()
    window.model_combo.setSizeAdjustPolicy(
        QComboBox.AdjustToMinimumContentsLengthWithIcon
    )
    window.model_combo.setItemDelegate(ModelPriceDelegate(window.model_combo))

    window.model_custom_input = QLineEdit(s.model)
    window.model_custom_input.setPlaceholderText(
        "Enter custom model id, e.g. gpt-4o-mini"
    )

    window.btn_refresh_models = QPushButton("Refresh")
    window.btn_refresh_models.setToolTip(
        "Fetch the list of available models from the API endpoint."
    )
    # Compact the Refresh button so the Model row matches the height of
    # the other single-line form rows.
    window.btn_refresh_models.setStyleSheet("QPushButton { padding: 1px 12px; }")
    window.btn_refresh_models.setFixedHeight(window.model_combo.sizeHint().height())

    window.model_custom_checkbox = QCheckBox("Custom")
    window.model_custom_checkbox.setToolTip(
        "Type a model id manually (useful for local proxies or unlisted models)."
    )
    window.model_custom_checkbox.setChecked(bool(getattr(s, "use_custom_model", False)))

    row.addWidget(window.model_combo, 1)
    row.addWidget(window.model_custom_input, 1)
    row.addWidget(window.btn_refresh_models)
    row.addWidget(window.model_custom_checkbox)

    initial_models = list(s.cached_models or []) or sorted(MODEL_PRICING.keys())
    populate_model_combo(window, initial_models, s.model)
    apply_custom_model_mode(window, bool(getattr(s, "use_custom_model", False)))

    window.model_combo.currentIndexChanged.connect(window._on_model_combo_changed)
    window.btn_refresh_models.clicked.connect(window._on_refresh_models)
    window.model_custom_checkbox.toggled.connect(window._on_model_custom_toggled)
    window.model_custom_input.textChanged.connect(window._on_settings_changed)
    return wrap


def populate_model_combo(window, models, selected):
    combo = window.model_combo
    combo.blockSignals(True)
    combo.clear()
    seen = set()
    if selected and selected not in models:
        add_combo_item(window, selected, is_current_marker=True)
        seen.add(selected)
    for m in models:
        if m in seen:
            continue
        seen.add(m)
        add_combo_item(window, m)
    if selected:
        for i in range(combo.count()):
            if combo.itemData(i) == selected:
                combo.setCurrentIndex(i)
                break
    combo.blockSignals(False)


def add_combo_item(window, model_id, is_current_marker=False):
    """Add one combo item with pricing metadata for the delegate.

    Item text is the compact fallback used by the closed combo display
    (and accessibility). ``Qt.UserRole`` stores the raw id. UserRole+1
    stores the full pricing dict for ``ModelPriceDelegate``.
    """
    price_dict = get_pricing(model_id)
    short = format_pricing(model_id)
    if is_current_marker:
        label = f"{model_id}  (current)"
    elif short:
        label = f"{model_id}   ·   {short}"
    else:
        label = model_id
    window.model_combo.addItem(label, model_id)
    last = window.model_combo.count() - 1
    window.model_combo.setItemData(last, price_dict, MODEL_PRICE_ROLE)


def apply_custom_model_mode(window, on: bool):
    """Swap combo <-> manual input in the same slot of the single row.

    Refresh is hidden together with the combo (it drives the combo's
    list and is meaningless while in Custom mode).
    """
    window.model_combo.setVisible(not on)
    window.btn_refresh_models.setVisible(not on)
    window.model_custom_input.setVisible(on)
    window.model_custom_input.setEnabled(on)


def sync_effective_model(window):
    """Push the currently active source (combo or manual) into settings."""
    s = window.settings
    if getattr(s, "use_custom_model", False):
        val = window.model_custom_input.text().strip()
    else:
        data = window.model_combo.currentData()
        val = (data or s.model or "").strip()
    if val:
        s.model = val


# Aliases used by Qt: keep the underscore-prefixed names referenced from
# main_window methods that delegate to these helpers.
_ = (Qt,)  # silence "unused" diagnostic for re-export below
