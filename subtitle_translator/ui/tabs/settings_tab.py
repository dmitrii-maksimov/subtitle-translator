"""Settings tab builder.

Mutates ``window`` by attaching widget references and wires
``textChanged``/``toggled`` to ``window._on_settings_changed``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..model_picker import build_model_picker


_DEFAULT_MAIN_PROMPT = (
    "{header}\n"
    "- Keep numbering (e.g., 12:, 43:, ...)\n"
    "- Do not change the number of lines or merge/split cues\n"
    "- Preserve line breaks within each numbered block exactly as in the input\n"
    "- Return ONLY the translated text blocks with the same numbering, no timestamps, no extra comments{extra}\n\n"
    "- New subtitles don't have to contain any characters in original language\n"
    "Example:\n"
    "1:\nHello!\n42:\nHow are you?\n\n"
    "Text:\n{src_block}"
)
_DEFAULT_SYSTEM_ROLE = (
    "You translate subtitles. Output must be ONLY the translated lines, "
    "one per input line, without indices, timestamps, or any additional labels."
)


def build_settings_tab(window, parent):
    """Populate ``parent`` with the Settings-tab UI for ``window``.

    Attaches: lang_input, api_key_input, api_base_input, workers_input,
    window_input, overlap_input, fulllog_checkbox, extra_prompt_input,
    main_prompt_text, system_role_text.
    """
    s = window.settings

    settings_layout_v = QVBoxLayout(parent)
    settings_layout_v.setContentsMargins(12, 12, 12, 12)
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

    window.lang_input = QLineEdit(s.target_language)
    window.lang_input.setPlaceholderText("Target language, e.g. ru, en, es...")
    window.api_key_input = QLineEdit(s.api_key)
    window.api_key_input.setEchoMode(QLineEdit.Password)
    window.api_key_input.setPlaceholderText("OpenAI API Key")
    window.api_base_input = QLineEdit(s.api_base)
    window.api_base_input.setPlaceholderText("API Base URL (OpenAI-compatible)")
    window.workers_input = QLineEdit(str(s.workers))
    window.workers_input.setPlaceholderText("Workers (1-10)")
    window.window_input = QLineEdit(str(getattr(s, "window", 25)))
    window.window_input.setPlaceholderText("Window (1-200)")
    window.overlap_input = QLineEdit(str(getattr(s, "overlap", 10)))
    window.overlap_input.setPlaceholderText("Overlap (0-200)")
    window.fulllog_checkbox = QCheckBox("Full log (requests && responses)")
    window.fulllog_checkbox.setChecked(bool(getattr(s, "fulllog", False)))
    window.extra_prompt_input = QLineEdit(getattr(s, "extra_prompt", "") or "")
    window.extra_prompt_input.setPlaceholderText(
        "Optional extra instruction for translation (will be enforced)"
    )

    window.main_prompt_text = QTextEdit()
    window.main_prompt_text.setMinimumHeight(60)
    window.main_prompt_text.setPlainText(s.main_prompt_template)
    btn_reset_main_prompt = QPushButton("Reset main prompt to default")

    def reset_main_prompt():
        window.main_prompt_text.setPlainText(_DEFAULT_MAIN_PROMPT)
        window._on_settings_changed()

    btn_reset_main_prompt.clicked.connect(reset_main_prompt)

    window.system_role_text = QTextEdit()
    window.system_role_text.setMinimumHeight(40)
    window.system_role_text.setPlainText(s.system_role)
    btn_reset_system = QPushButton("Reset system role to default")

    def reset_system():
        window.system_role_text.setPlainText(_DEFAULT_SYSTEM_ROLE)
        window._on_settings_changed()

    btn_reset_system.clicked.connect(reset_system)

    form.addRow("Target language:", window.lang_input)
    form.addRow("API Key:", window.api_key_input)
    form.addRow("Model:", build_model_picker(window))
    form.addRow("API Base:", window.api_base_input)
    form.addRow("Workers:", window.workers_input)
    form.addRow("Window:", window.window_input)
    form.addRow("Overlap:", window.overlap_input)
    form.addRow("Full log:", window.fulllog_checkbox)
    form.addRow("Extra prompt:", window.extra_prompt_input)
    settings_layout_v.addLayout(form)
    settings_layout_v.addWidget(
        QLabel(
            "Main prompt template (uses placeholders {header}, {extra}, {src_block}):"
        )
    )
    settings_layout_v.addWidget(window.main_prompt_text)
    settings_layout_v.addWidget(btn_reset_main_prompt)
    settings_layout_v.addSpacing(8)
    settings_layout_v.addWidget(QLabel("System role (chat system message):"))
    settings_layout_v.addWidget(window.system_role_text)
    settings_layout_v.addWidget(btn_reset_system)

    for w in (
        window.lang_input,
        window.api_key_input,
        window.api_base_input,
        window.workers_input,
        window.window_input,
        window.overlap_input,
        window.extra_prompt_input,
    ):
        w.textChanged.connect(window._on_settings_changed)
    window.main_prompt_text.textChanged.connect(window._on_settings_changed)
    window.system_role_text.textChanged.connect(window._on_settings_changed)
    window.fulllog_checkbox.toggled.connect(window._on_settings_changed)
