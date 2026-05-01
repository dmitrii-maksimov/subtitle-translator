"""Kodi tab builder.

Mutates ``window`` by attaching widget references and wires signals to
its ``_on_kodi_*`` handlers. The handlers stay on ``MainWindow`` because
they trigger settings save and dialog launches.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


def build_kodi_tab(window, parent):
    """Populate ``parent`` with the Kodi-tab UI for ``window`` (a MainWindow).

    Attaches the following attributes to ``window``:
        kodi_host_input, kodi_port_input, kodi_user_input, kodi_password_input,
        kodi_discover_btn, kodi_ping_btn, kodi_status_label,
        kodi_source_input, kodi_source_browse_btn,
        local_parent_input, local_parent_browse_btn, kodi_preview_label,
        live_poll_input, live_stable_input, kodi_follow_buffer_input.
    """
    s = window.settings

    v = QVBoxLayout(parent)
    v.setContentsMargins(12, 12, 12, 12)

    # ── Connection box ───────────────────────────
    conn_box = QFrame()
    conn_box.setFrameShape(QFrame.StyledPanel)
    conn_layout = QFormLayout(conn_box)
    conn_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    conn_layout.addRow(QLabel("<b>Kodi connection</b>"))

    window.kodi_host_input = QLineEdit(s.kodi_host)
    window.kodi_host_input.setPlaceholderText("192.168.1.50")
    window.kodi_port_input = QLineEdit(str(s.kodi_port or 8080))
    window.kodi_user_input = QLineEdit(s.kodi_user or "kodi")
    window.kodi_password_input = QLineEdit(s.kodi_password)
    window.kodi_password_input.setEchoMode(QLineEdit.Password)

    conn_layout.addRow("Host:", window.kodi_host_input)
    conn_layout.addRow("Port:", window.kodi_port_input)
    conn_layout.addRow("User:", window.kodi_user_input)
    conn_layout.addRow("Password:", window.kodi_password_input)

    btn_row = QHBoxLayout()
    window.kodi_discover_btn = QPushButton("Find Kodi on network")
    window.kodi_ping_btn = QPushButton("Test connection")
    btn_row.addWidget(window.kodi_discover_btn)
    btn_row.addWidget(window.kodi_ping_btn)
    btn_row.addStretch(1)
    conn_layout.addRow(btn_row)

    window.kodi_status_label = QLabel("Status: ● Disconnected")
    conn_layout.addRow(window.kodi_status_label)

    v.addWidget(conn_box)

    # ── Path mapping box ──────────────────────────
    map_box = QFrame()
    map_box.setFrameShape(QFrame.StyledPanel)
    map_layout = QFormLayout(map_box)
    map_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    map_layout.addRow(QLabel("<b>Path mapping</b>"))

    window.kodi_source_input = QLineEdit(s.kodi_source_path)
    window.kodi_source_input.setPlaceholderText("smb://nas/movies")
    window.kodi_source_browse_btn = QPushButton("Pick Kodi folder")
    window.kodi_source_browse_btn.setEnabled(False)
    ks_row = QHBoxLayout()
    ks_row.addWidget(window.kodi_source_input, 1)
    ks_row.addWidget(window.kodi_source_browse_btn)
    map_layout.addRow("Kodi source:", ks_row)

    window.local_parent_input = QLineEdit(s.local_parent_path)
    window.local_parent_input.setPlaceholderText("/Volumes/movies")
    window.local_parent_browse_btn = QPushButton("Pick local parent folder")
    lp_row = QHBoxLayout()
    lp_row.addWidget(window.local_parent_input, 1)
    lp_row.addWidget(window.local_parent_browse_btn)
    map_layout.addRow("Local parent:", lp_row)

    window.kodi_preview_label = QLabel("Mapping preview: (specify both folders)")
    window.kodi_preview_label.setWordWrap(True)
    map_layout.addRow(window.kodi_preview_label)

    v.addWidget(map_box)

    # ── Live settings box ─────────────────────────
    live_box = QFrame()
    live_box.setFrameShape(QFrame.StyledPanel)
    live_layout = QFormLayout(live_box)
    live_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    live_layout.addRow(QLabel("<b>Live mode</b>"))

    window.live_poll_input = QLineEdit(str(s.live_poll_interval))
    window.live_stable_input = QLineEdit(str(s.live_stable_threshold))
    window.kodi_follow_buffer_input = QLineEdit(str(s.kodi_follow_buffer_min))
    live_layout.addRow("Poll interval (sec):", window.live_poll_input)
    live_layout.addRow("Stability for finish (sec):", window.live_stable_input)
    live_layout.addRow("Following buffer (min):", window.kodi_follow_buffer_input)
    v.addWidget(live_box)
    v.addStretch(1)

    # ── Wire change signals ───────────────────────
    for w in (
        window.kodi_host_input,
        window.kodi_port_input,
        window.kodi_user_input,
        window.kodi_password_input,
        window.kodi_source_input,
        window.local_parent_input,
        window.live_poll_input,
        window.live_stable_input,
        window.kodi_follow_buffer_input,
    ):
        w.textChanged.connect(window._on_kodi_settings_changed)

    # ── Buttons ───────────────────────────────────
    window.kodi_ping_btn.clicked.connect(window._on_kodi_ping_clicked)
    window.kodi_discover_btn.clicked.connect(window._on_kodi_discover_clicked)
    window.kodi_source_browse_btn.clicked.connect(window._on_kodi_browse_source_clicked)
    window.local_parent_browse_btn.clicked.connect(
        window._on_local_parent_browse_clicked
    )

    window._refresh_kodi_mapping_preview()
