"""``KodiBrowseDialog`` — browse Kodi sources/directories via
``Files.GetSources``/``GetDirectory`` and pick a folder."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ...kodi_client import KodiClient


class KodiBrowseDialog(QDialog):
    def __init__(self, parent, client: "KodiClient"):
        super().__init__(parent)
        self.setWindowTitle("Pick a folder in Kodi")
        self.resize(540, 460)
        self.selected_path = None
        self._client = client
        self._stack = []

        v = QVBoxLayout(self)
        self._crumb = QLabel("Sources")
        self._crumb.setWordWrap(True)
        v.addWidget(self._crumb)

        self._list = QListWidget()
        v.addWidget(self._list)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        btn_row = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.setEnabled(False)
        self._pick_btn = QPushButton("Pick this folder")
        self._pick_btn.setEnabled(False)
        self._cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self._back_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._pick_btn)
        v.addLayout(btn_row)

        self._back_btn.clicked.connect(self._go_back)
        self._pick_btn.clicked.connect(self._accept_current)
        self._cancel_btn.clicked.connect(self.reject)

        self._load_sources()

    def _set_loading(self, msg):
        self._list.clear()
        self._list.addItem(QListWidgetItem(msg))
        self._list.setEnabled(False)

    def _load_sources(self):
        self._set_loading("Loading sources...")
        try:
            sources = self._client.get_sources("video")
        except Exception as e:
            self._list.clear()
            self._list.setEnabled(True)
            self._list.addItem(QListWidgetItem(f"Error: {e}"))
            return
        self._list.clear()
        self._list.setEnabled(True)
        self._stack = []
        self._back_btn.setEnabled(False)
        self._pick_btn.setEnabled(False)
        self._crumb.setText("Sources")
        for s in sources:
            label = f"{s.get('label', s.get('file', '?'))}  [{s.get('file', '')}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, {"file": s.get("file", ""), "is_dir": True})
            self._list.addItem(item)

    def _load_directory(self, path):
        self._set_loading(f"Loading {path}...")
        try:
            items = self._client.get_directory(path, "video")
        except Exception as e:
            self._list.clear()
            self._list.setEnabled(True)
            self._list.addItem(QListWidgetItem(f"Error: {e}"))
            return
        self._list.clear()
        self._list.setEnabled(True)
        self._crumb.setText(" / ".join(self._stack))
        self._pick_btn.setEnabled(True)
        for it in items:
            label = it.get("label") or it.get("file") or "?"
            file = it.get("file", "")
            ftype = it.get("filetype", "")
            is_dir = ftype == "directory"
            disp = f"📁 {label}" if is_dir else f"   {label}"
            entry = QListWidgetItem(disp)
            entry.setData(Qt.UserRole, {"file": file, "is_dir": is_dir})
            self._list.addItem(entry)

    def _on_item_double_clicked(self, item):
        data = item.data(Qt.UserRole)
        if not data or not data.get("is_dir"):
            return
        path = data.get("file", "")
        if not path:
            return
        self._stack.append(path)
        self._back_btn.setEnabled(True)
        self._load_directory(path)

    def _go_back(self):
        if not self._stack:
            return
        self._stack.pop()
        if not self._stack:
            self._load_sources()
        else:
            self._load_directory(self._stack[-1])

    def _accept_current(self):
        if not self._stack:
            return
        self.selected_path = self._stack[-1]
        self.accept()
