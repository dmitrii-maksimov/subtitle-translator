"""``TrackSelectionDialog`` — per-file modal that asks the user which
subtitle tracks to translate and/or delete. Pre-fills carry-over from
the previous file's choices via ``initial_state``.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.track_matcher import stream_match_key
from ...models import FileDecision


class TrackSelectionDialog(QDialog):
    """Result is retrievable via ``get_decision()`` and ``carry_over_prefs()``
    regardless of whether the dialog was accepted, rejected or closed."""

    _COL_TRANSLATE = 100
    _COL_DELETE = 80
    _COL_STREAM = 200

    def __init__(self, parent, file_path, streams, initial_state, is_last_file):
        super().__init__(parent)
        self._file_path = file_path
        self._streams = list(streams)
        self._is_last = bool(is_last_file)
        self._rows = []
        self._decision = FileDecision(file_path=file_path, skipped=True)

        self.setWindowTitle(f"Tracks: {os.path.basename(file_path)}")
        self.setModal(True)
        self.resize(780, 520)

        pal = self.palette()
        text_color = pal.color(QPalette.WindowText)
        r, g, b = text_color.red(), text_color.green(), text_color.blue()
        muted_css = f"rgba({r},{g},{b},0.65)"
        sep_css = f"rgba({r},{g},{b},0.18)"
        row_alt_css = f"rgba({r},{g},{b},0.06)"

        self.setStyleSheet(
            f"""
            QDialog {{ background: palette(window); }}
            QLabel#dlg_title {{ font-size: 15pt; font-weight: 600; }}
            QLabel#dlg_subtitle {{ color: {muted_css}; font-size: 10pt; }}
            QLabel#col_header {{
                font-weight: 600;
                color: {muted_css};
                font-size: 9pt;
                letter-spacing: 1px;
            }}
            QFrame#row_sep {{ background: {sep_css}; max-height: 1px; border: none; }}
            QFrame.row_even {{ background: {row_alt_css}; border-radius: 6px; }}
            QFrame.row_odd  {{ background: transparent; border-radius: 6px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; }}
            QLabel.stream_cell {{
                font-family: "SF Mono", "Menlo", monospace;
                font-size: 10pt;
            }}
            QLabel.title_cell {{ font-size: 10pt; }}
            QPushButton#primary {{
                padding: 8px 18px;
                font-weight: 600;
            }}
            QPushButton#secondary {{
                padding: 8px 18px;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)

        title_lbl = QLabel(os.path.basename(file_path))
        title_lbl.setObjectName("dlg_title")
        title_lbl.setWordWrap(True)
        subtitle_lbl = QLabel(
            f"{len(self._streams)} subtitle track(s) — choose which to translate "
            "and/or delete."
        )
        subtitle_lbl.setObjectName("dlg_subtitle")
        subtitle_lbl.setWordWrap(True)
        root.addWidget(title_lbl)
        root.addWidget(subtitle_lbl)

        sep1 = QFrame()
        sep1.setObjectName("row_sep")
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Plain)
        root.addWidget(sep1)

        if not self._streams:
            empty = QLabel("No subtitle tracks found in this file.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: palette(mid); padding: 32px;")
            root.addWidget(empty, 1)
        else:
            headers_row = QHBoxLayout()
            headers_row.setContentsMargins(8, 4, 8, 4)
            headers_row.setSpacing(0)
            for text, width, align in (
                ("Stream", self._COL_STREAM, Qt.AlignLeft | Qt.AlignVCenter),
                ("Title / flags", None, Qt.AlignLeft | Qt.AlignVCenter),
                ("Translate", self._COL_TRANSLATE, Qt.AlignHCenter | Qt.AlignVCenter),
                ("Delete", self._COL_DELETE, Qt.AlignHCenter | Qt.AlignVCenter),
            ):
                lbl = QLabel(text.upper())
                lbl.setObjectName("col_header")
                lbl.setAlignment(align)
                if width is not None:
                    lbl.setFixedWidth(width)
                    headers_row.addWidget(lbl)
                else:
                    headers_row.addWidget(lbl, 1)
            root.addLayout(headers_row)

            sep2 = QFrame()
            sep2.setObjectName("row_sep")
            sep2.setFrameShape(QFrame.HLine)
            sep2.setFrameShadow(QFrame.Plain)
            root.addWidget(sep2)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            inner = QWidget()
            rows_v = QVBoxLayout(inner)
            rows_v.setContentsMargins(0, 4, 0, 4)
            rows_v.setSpacing(2)
            rows_v.setAlignment(Qt.AlignTop)

            for row_idx, st in enumerate(self._streams):
                row_widget = self._build_row_widget(
                    row_idx, st, initial_state, alternating=(row_idx % 2 == 0)
                )
                rows_v.addWidget(row_widget)

            rows_v.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.setSpacing(8)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("secondary")
        self.btn_cancel.setToolTip(
            "Abort the whole batch — no more files will be processed."
        )
        self.btn_skip = QPushButton("Skip")
        self.btn_skip.setObjectName("secondary")
        self.btn_skip.setToolTip("Skip this file only; move on to the next.")
        self.btn_save = QPushButton(
            "Save && Remux" if self._is_last else "Save && Continue"
        )
        self.btn_save.setObjectName("primary")
        self.btn_save.setDefault(True)
        self.btn_save.setEnabled(bool(self._streams))
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_skip)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)

        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_save.clicked.connect(self._on_save)

    def _build_row_widget(self, row_idx, st, initial_state, alternating):
        idx = st.get("index")
        tags = st.get("tags") or {}
        lang = tags.get("language") or "und"
        title = tags.get("title") or ""
        codec = st.get("codec_name") or "?"
        disp = st.get("disposition") or {}
        flags = []
        if disp.get("default"):
            flags.append("default")
        if disp.get("forced"):
            flags.append("forced")
        if disp.get("hearing_impaired"):
            flags.append("SDH")
        if disp.get("visual_impaired"):
            flags.append("VI")
        flags_str = " · ".join(flags)

        init = initial_state.get(idx) or {"translate": False, "delete": False}

        row = QFrame()
        row.setProperty("class", "row_even" if alternating else "row_odd")
        row.setMinimumHeight(36)
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(0)

        stream_lbl = QLabel(f"#{idx}  {lang}  ·  {codec}")
        stream_lbl.setProperty("class", "stream_cell")
        stream_lbl.setFixedWidth(self._COL_STREAM)
        h.addWidget(stream_lbl)

        bits = []
        if title:
            bits.append(f"“{title}”")
        if flags_str:
            bits.append(f"[{flags_str}]")
        title_lbl = QLabel(" ".join(bits) if bits else "—")
        title_lbl.setProperty("class", "title_cell")
        title_lbl.setWordWrap(True)
        h.addWidget(title_lbl, 1)

        chk_t = QCheckBox()
        chk_t.setChecked(bool(init.get("translate")))
        chk_t_wrap = QWidget()
        chk_t_wrap.setFixedWidth(self._COL_TRANSLATE)
        lw = QHBoxLayout(chk_t_wrap)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.addWidget(chk_t, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        h.addWidget(chk_t_wrap)

        chk_d = QCheckBox()
        chk_d.setChecked(bool(init.get("delete")))
        chk_d_wrap = QWidget()
        chk_d_wrap.setFixedWidth(self._COL_DELETE)
        rw = QHBoxLayout(chk_d_wrap)
        rw.setContentsMargins(0, 0, 0, 0)
        rw.addWidget(chk_d, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        h.addWidget(chk_d_wrap)

        chk_t.stateChanged.connect(
            lambda state, r=row_idx: self._on_translate_checked(r, state)
        )
        self._rows.append((chk_t, chk_d))
        return row

    def _on_translate_checked(self, row, state):
        # Radio-style: at most one "Translate" checkbox checked at a time.
        if not self._rows[row][0].isChecked():
            return
        for i, (chk_t, _) in enumerate(self._rows):
            if i != row and chk_t.isChecked():
                chk_t.blockSignals(True)
                chk_t.setChecked(False)
                chk_t.blockSignals(False)

    def _on_skip(self):
        self._decision = FileDecision(file_path=self._file_path, skipped=True)
        self.reject()

    def _on_cancel(self):
        self._decision = FileDecision(
            file_path=self._file_path, skipped=True, cancelled=True
        )
        self.reject()

    def _on_save(self):
        translate_idx = None
        delete_idxs = []
        for i, (chk_t, chk_d) in enumerate(self._rows):
            stream_idx = self._streams[i].get("index")
            if chk_t.isChecked() and translate_idx is None:
                translate_idx = stream_idx
            if chk_d.isChecked():
                delete_idxs.append(stream_idx)
        self._decision = FileDecision(
            file_path=self._file_path,
            translate_stream_index=translate_idx,
            delete_stream_indexes=delete_idxs,
            skipped=False,
        )
        self.accept()

    def closeEvent(self, event):
        super().closeEvent(event)

    def get_decision(self):
        return self._decision

    def carry_over_prefs(self):
        """Snapshot of current checkbox state keyed by (lang, title, codec).

        Used to pre-fill the next file's dialog. Only meaningful after Save."""
        prefs = {}
        for i, st in enumerate(self._streams):
            chk_t, chk_d = self._rows[i]
            prefs[stream_match_key(st)] = {
                "translate": chk_t.isChecked(),
                "delete": chk_d.isChecked(),
            }
        return prefs
