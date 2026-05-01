"""Main tab builder.

The Main tab holds the file/folder action row plus progress bars and
the log area. Settings save and dialog launches stay on
``MainWindow``; this builder only constructs widgets and wires them to
``window`` handlers.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..widgets.elided_label import ElidedLabel


_PROGRESS_STYLE = """
    QProgressBar {
        border: 2px solid grey;
        border-radius: 5px;
        text-align: center;
        height: 25px;
    }
    QProgressBar::chunk {
        background-color: #05B8CC;
        width: 20px;
    }
"""


def build_main_tab(window, parent):
    """Populate ``parent`` with the Main-tab UI for ``window``.

    Attaches: file_label, btn_translate, overwrite_checkbox, btn_cancel,
    batch_info_label, batch_progress, progress, log.
    """
    s = window.settings
    layout = QVBoxLayout(parent)

    file_layout = QHBoxLayout()
    window.file_label = ElidedLabel("No file selected")
    btn_browse = QPushButton("Open File (MKV or SRT)...")
    btn_browse.clicked.connect(window.on_browse)
    btn_browse_folder = QPushButton("Open Folder...")
    btn_browse_folder.clicked.connect(window.on_browse_folder)
    btn_live = QPushButton("File downloading (live)…")
    btn_live.clicked.connect(window.on_open_live_dialog)
    btn_kodi_follow = QPushButton("Following Kodi…")
    btn_kodi_follow.clicked.connect(window.on_open_kodi_follow_dialog)
    file_layout.addWidget(window.file_label, 1)
    file_layout.addWidget(btn_browse)
    file_layout.addWidget(btn_browse_folder)
    file_layout.addWidget(btn_live)
    file_layout.addWidget(btn_kodi_follow)
    layout.addLayout(file_layout)

    actions_layout = QHBoxLayout()
    window.btn_translate = QPushButton("Translate SRT")
    window.btn_translate.clicked.connect(window.on_translate)
    window.btn_translate.setEnabled(False)
    window.btn_translate.setVisible(False)
    window.overwrite_checkbox = QCheckBox("Overwrite the original file")
    window.overwrite_checkbox.setChecked(bool(getattr(s, "overwrite_original", False)))
    window.overwrite_checkbox.toggled.connect(window._on_settings_changed)
    window.btn_cancel = QPushButton("Cancel")
    window.btn_cancel.setEnabled(False)
    window.btn_cancel.clicked.connect(window.on_cancel)
    actions_layout.addWidget(window.btn_translate)
    actions_layout.addWidget(window.overwrite_checkbox)
    actions_layout.addStretch(1)
    actions_layout.addWidget(window.btn_cancel)
    layout.addLayout(actions_layout)

    window.batch_info_label = QLabel("Batch Progress:")
    window.batch_info_label.setVisible(False)
    window.batch_progress = QProgressBar()
    window.batch_progress.setVisible(False)
    window.progress = QProgressBar()

    window.batch_progress.setStyleSheet(_PROGRESS_STYLE)
    window.progress.setStyleSheet(_PROGRESS_STYLE)

    window.log = QTextEdit()
    window.log.setReadOnly(True)
    window.log.setMinimumHeight(60)
    layout.addWidget(window.batch_info_label)
    layout.addWidget(window.batch_progress)
    layout.addWidget(window.progress)
    layout.addWidget(window.log)
