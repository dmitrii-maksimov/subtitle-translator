"""``KodiDiscoveryDialog`` — list Kodi instances found on the LAN.

Discovery runs in a background ``QThread`` and updates the list as
hosts are found. SSDP first, fallback subnet scan.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ...kodi_client import discover_kodi


class KodiDiscoveryDialog(QDialog):
    def __init__(self, parent, port_hint: int = 8080):
        super().__init__(parent)
        self.setWindowTitle("Find Kodi on the network")
        self.resize(420, 360)
        self.selected = None
        self._port_hint = int(port_hint or 8080)

        v = QVBoxLayout(self)
        self._list = QListWidget()
        v.addWidget(self._list)

        self._status = QLabel("Scanning network...")
        v.addWidget(self._status)

        btn_row = QHBoxLayout()
        self._rescan_btn = QPushButton("Rescan")
        self._cancel_btn = QPushButton("Cancel")
        self._pick_btn = QPushButton("Pick")
        self._pick_btn.setEnabled(False)
        btn_row.addWidget(self._rescan_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._pick_btn)
        v.addLayout(btn_row)

        self._list.itemSelectionChanged.connect(
            lambda: self._pick_btn.setEnabled(self._list.currentItem() is not None)
        )
        self._list.itemDoubleClicked.connect(lambda *_: self._accept_selection())
        self._pick_btn.clicked.connect(self._accept_selection)
        self._cancel_btn.clicked.connect(self.reject)
        self._rescan_btn.clicked.connect(self._start_scan)

        self._thread = None
        self._start_scan()

    def _start_scan(self):
        self._list.clear()
        self._pick_btn.setEnabled(False)
        self._status.setText("Scanning network...")
        self._rescan_btn.setEnabled(False)

        port = self._port_hint

        class _DiscoverThread(QThread):
            done = Signal(list)
            progress = Signal(int, int)

            def __init__(self, p):
                super().__init__()
                self._p = p

            def run(self):
                try:
                    found = discover_kodi(
                        port_hint=self._p,
                        progress_cb=lambda d, t: self.progress.emit(d, t),
                    )
                    self.done.emit(found)
                except Exception:
                    self.done.emit([])

        self._thread = _DiscoverThread(port)
        self._thread.progress.connect(self._on_progress)
        self._thread.done.connect(self._on_done)
        self._thread.start()

    def _on_progress(self, done, total):
        self._status.setText(f"Scanning {done}/{total}...")

    def _on_done(self, found):
        self._rescan_btn.setEnabled(True)
        if not found:
            self._status.setText("Nothing found. Enter host manually.")
            return
        self._status.setText(f"Found: {len(found)}")
        for f in found:
            label = f"{f['name']} — {f['ip']}:{f['port']}  [{f.get('source', '')}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, f)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _accept_selection(self):
        item = self._list.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.UserRole)
        self.accept()
