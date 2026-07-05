"""Build the application QIcon from the embedded PNG data.

Kept separate from __main__ so both the entry point and MainWindow can set
the icon. The icon is embedded (subtitle_translator/icon_data.py) rather than
shipped as a file, so it works identically from source and from a PyInstaller
bundle without any --add-data plumbing.
"""
from __future__ import annotations

import base64

from PySide6.QtGui import QIcon, QPixmap

from ..icon_data import ICON_PNGS


def app_icon() -> QIcon:
    icon = QIcon()
    for b64 in ICON_PNGS.values():
        pm = QPixmap()
        pm.loadFromData(base64.b64decode(b64), "PNG")
        if not pm.isNull():
            icon.addPixmap(pm)
    return icon
