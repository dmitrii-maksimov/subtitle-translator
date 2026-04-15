import os
import re
import json
import threading
import subprocess
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import shutil
import srt
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QLineEdit,
    QComboBox,
    QProgressBar,
    QTextEdit,
    QCheckBox,
    QTabWidget,
    QDialog,
    QFormLayout,
    QProgressDialog,
    QListWidgetItem,
    QSizePolicy,
    QGridLayout,
    QScrollArea,
    QFrame,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QApplication,
)
from PySide6.QtGui import QPainter, QPalette, QColor


from .models import AppSettings, FileDecision
from .utils import check_ffmpeg_available, install_ffmpeg, make_startupinfo, find_tool


class WorkerThread(QThread):
    progress = Signal(int)
    status = Signal(str)
    batch_info = Signal(int, str)
    request_input = Signal(str, object)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            for update in self.func(*self.args, **self.kwargs):
                if isinstance(update, int):
                    self.progress.emit(update)
                elif isinstance(update, str):
                    self.status.emit(update)
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "batch"
                ):
                    self.batch_info.emit(update[1], update[2])
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "input"
                ):
                    self.request_input.emit(update[1], update[2])
                elif (
                    isinstance(update, tuple)
                    and len(update) == 3
                    and update[0] == "settings_update"
                ):
                    self.request_input.emit(
                        "update_ui_settings", (update[1], update[2])
                    )
            self.finished_ok.emit("done")
        except Exception as e:
            self.failed.emit(str(e))


class _ModelFetcherThread(QThread):
    """One-shot thread: calls translator.list_models() and emits the result.

    Emits ``done(models: list, error: str)``. On success ``error`` is empty.
    On failure ``models`` is empty and ``error`` contains the message."""

    done = Signal(list, str)

    def __init__(self, translator):
        super().__init__()
        self._translator = translator

    def run(self):
        try:
            models = self._translator.list_models()
            self.done.emit(models, "")
        except Exception as e:
            self.done.emit([], str(e))


_MODEL_PRICE_ROLE = Qt.UserRole + 1


class _ModelPriceDelegate(QStyledItemDelegate):
    """Render a QComboBox dropdown item as 4 aligned columns:
    current-marker | model id (elided middle) | input price | output price.

    The closed combo still uses the plain item text; only the open popup
    is redrawn with columns. Prices come from UserRole+1 (a dict or None).

    The ``combo`` constructor argument lets the delegate highlight the
    currently-selected row with a small bullet, independent of hover."""

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
        opt.text = ""  # we draw the text ourselves
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        price = index.data(_MODEL_PRICE_ROLE)
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
            painter.drawText(marker_rect, int(Qt.AlignCenter), "\u25cf")

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


def _stream_match_key(stream: dict):
    """Tuple used to match equivalent subtitle tracks across files."""
    tags = stream.get("tags") or {}
    lang = tags.get("language") or "und"
    title = tags.get("title") or ""
    codec = stream.get("codec_name") or ""
    return (lang, title, codec)


def match_initial_state(streams, previous_prefs):
    """Return {stream_index: {"translate": bool, "delete": bool}} pre-filled
    from previous_prefs, keyed by (lang, title, codec)."""
    result = {}
    for st in streams:
        key = _stream_match_key(st)
        prefs = previous_prefs.get(key) or {"translate": False, "delete": False}
        result[st.get("index")] = {
            "translate": bool(prefs.get("translate")),
            "delete": bool(prefs.get("delete")),
        }
    return result


class TrackSelectionDialog(QDialog):
    """Per-file modal: pick which subtitle tracks to translate / delete.

    Result is retrievable via `get_decision()` and `carry_over_prefs()`
    regardless of whether the dialog was accepted, rejected or closed."""

    _COL_TRANSLATE = 100
    _COL_DELETE = 80
    _COL_STREAM = 200

    def __init__(self, parent, file_path, streams, initial_state, is_last_file):
        super().__init__(parent)
        self._file_path = file_path
        self._streams = list(streams)
        self._is_last = bool(is_last_file)
        self._rows = []  # List[Tuple[QCheckBox(translate), QCheckBox(delete)]]
        self._decision = FileDecision(file_path=file_path, skipped=True)

        self.setWindowTitle(f"Tracks: {os.path.basename(file_path)}")
        self.setModal(True)
        self.resize(780, 520)

        # Theme-aware colors: derive everything from the current palette so the
        # dialog looks right in both light and dark system themes.
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
            f"{len(self._streams)} subtitle track(s) \u2014 choose which to translate "
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
        flags_str = " \u00b7 ".join(flags)

        init = initial_state.get(idx) or {"translate": False, "delete": False}

        row = QFrame()
        row.setProperty("class", "row_even" if alternating else "row_odd")
        row.setMinimumHeight(36)
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(0)

        stream_lbl = QLabel(f"#{idx}  {lang}  \u00b7  {codec}")
        stream_lbl.setProperty("class", "stream_cell")
        stream_lbl.setFixedWidth(self._COL_STREAM)
        h.addWidget(stream_lbl)

        bits = []
        if title:
            bits.append(f"\u201c{title}\u201d")
        if flags_str:
            bits.append(f"[{flags_str}]")
        title_lbl = QLabel(" ".join(bits) if bits else "\u2014")
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
        # Emulate radio: at most one "Translate" checkbox checked at a time.
        # Read the current state from the widget itself — Qt.CheckState enum
        # vs int comparison is inconsistent across PySide6 versions.
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
        """Abort the whole batch: mark this decision as cancelled and close."""
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
        # Closing via X is treated as Skip (decision already initialized as skipped).
        super().closeEvent(event)

    def get_decision(self):
        return self._decision

    def carry_over_prefs(self):
        """Snapshot of current checkbox state keyed by (lang, title, codec).

        Used to pre-fill the next file's dialog. Only meaningful after Save."""
        prefs = {}
        for i, st in enumerate(self._streams):
            chk_t, chk_d = self._rows[i]
            prefs[_stream_match_key(st)] = {
                "translate": chk_t.isChecked(),
                "delete": chk_d.isChecked(),
            }
        return prefs


from .services import TranslationService


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Translator")
        self.settings = AppSettings.load()
        self.translator = TranslationService(self.settings)
        if getattr(self.settings, "overlap", None) is None:
            self.settings.overlap = 10
        if not getattr(self.settings, "main_prompt_template", None):
            self.settings.main_prompt_template = (
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
        if not getattr(self.settings, "system_role", None):
            self.settings.system_role = "You translate subtitles. Output must be ONLY the translated lines, one per input line, without indices, timestamps, or any additional labels."

        self.mkv_path: Optional[str] = None
        self.subtitle_streams: List[dict] = []
        self.input_is_srt: bool = False
        self.src_srt_path: Optional[str] = None
        self.batch_files: List[str] = []

        self._user_input_event = threading.Event()
        self._user_input_result = None

        self._build_ui()
        self.check_and_offer_install_ffmpeg()

    def check_and_offer_install_ffmpeg(self):
        if check_ffmpeg_available():
            return

        reply = QMessageBox.question(
            self,
            "FFmpeg Missing",
            "FFmpeg is required for this application but was not found.\n\nDo you want to download and install it automatically?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.download_ffmpeg()
        else:
            QMessageBox.warning(
                self,
                "Restricted Functionality",
                "Without FFmpeg, you cannot extract or remux subtitles. Only standalone SRT translation will work.",
            )

    def download_ffmpeg(self):
        progress = QProgressDialog(
            "Downloading and installing FFmpeg...", "Cancel", 0, 100, self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        cancel_event = threading.Event()
        progress.canceled.connect(cancel_event.set)

        class InstallWorker(QThread):
            progress_sig = Signal(int)
            finished_sig = Signal(bool, str)

            def run(self):
                try:

                    def cb(p):
                        self.progress_sig.emit(p)

                    install_ffmpeg(progress_callback=cb, cancel_event=cancel_event)
                    self.finished_sig.emit(True, "")
                except InterruptedError:
                    self.finished_sig.emit(False, "Cancelled by user")
                except Exception as e:
                    self.finished_sig.emit(False, str(e))

        self._install_worker = InstallWorker()
        self._install_worker.progress_sig.connect(progress.setValue)

        def on_finished(success, msg):
            progress.close()
            if success:
                QMessageBox.information(
                    self, "Success", "FFmpeg installed successfully."
                )
            else:
                if msg == "Cancelled by user":
                    pass
                else:
                    QMessageBox.critical(
                        self, "Error", f"Failed to install FFmpeg: {msg}"
                    )

        self._install_worker.finished_sig.connect(on_finished)
        self._install_worker.start()

    def _sanitize_content(self, text: str) -> str:
        """
        Keep translated subtitle content as-is preserving original line breaks.
        Only remove accidentally included SRT index or timestamp lines at the edges.
        Do not normalize or trim line endings here; CRLF handling is done at final write.
        """
        if not text:
            return ""
        # Do minimal cleanup: drop pure index or timestamp lines if model leaked them
        ts_re = re.compile(
            r"^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{3}$"
        )
        idx_re = re.compile(r"^\d{1,5}$")
        tmp = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = tmp.split("\n")
        cleaned = []
        for ln in lines:
            if idx_re.match(ln.strip()) or ts_re.match(ln.strip()):
                continue
            cleaned.append(ln)
        return "\n".join(cleaned)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        # Tabs: Main and Settings, each wrapped in a QScrollArea so the
        # window can be resized small/narrow without clipping content.
        self.tabs = QTabWidget()

        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_tab = QWidget()
        main_scroll.setWidget(main_tab)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_tab = QWidget()
        settings_scroll.setWidget(settings_tab)

        self.tabs.addTab(main_scroll, "Main")
        self.tabs.addTab(settings_scroll, "Settings")
        main_layout.addWidget(self.tabs)

        layout = QVBoxLayout(main_tab)

        settings_layout_v = QVBoxLayout(settings_tab)
        settings_layout_v.setContentsMargins(12, 12, 12, 12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lang_input = QLineEdit(self.settings.target_language)
        self.lang_input.setPlaceholderText("Target language, e.g. ru, en, es...")
        self.api_key_input = QLineEdit(self.settings.api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("OpenAI API Key")
        self.api_base_input = QLineEdit(self.settings.api_base)
        self.api_base_input.setPlaceholderText("API Base URL (OpenAI-compatible)")
        self.workers_input = QLineEdit(str(self.settings.workers))
        self.workers_input.setPlaceholderText("Workers (1-10)")
        self.window_input = QLineEdit(str(getattr(self.settings, "window", 25)))
        self.window_input.setPlaceholderText("Window (1-200)")
        self.overlap_input = QLineEdit(str(getattr(self.settings, "overlap", 10)))
        self.overlap_input.setPlaceholderText("Overlap (0-200)")
        self.fulllog_checkbox = QCheckBox("Full log (requests && responses)")
        self.fulllog_checkbox.setChecked(bool(getattr(self.settings, "fulllog", False)))
        self.extra_prompt_input = QLineEdit(
            self.settings.extra_prompt if hasattr(self.settings, "extra_prompt") else ""
        )
        self.extra_prompt_input.setPlaceholderText(
            "Optional extra instruction for translation (will be enforced)"
        )

        self.main_prompt_text = QTextEdit()
        # Allow the widget to shrink — default size hint otherwise keeps
        # the window from being resized below ~700 px tall.
        self.main_prompt_text.setMinimumHeight(60)
        self.main_prompt_text.setPlainText(self.settings.main_prompt_template)
        btn_reset_main_prompt = QPushButton("Reset main prompt to default")

        def reset_main_prompt():
            self.main_prompt_text.setPlainText(
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
            self._on_settings_changed()

        btn_reset_main_prompt.clicked.connect(reset_main_prompt)

        self.system_role_text = QTextEdit()
        self.system_role_text.setMinimumHeight(40)
        self.system_role_text.setPlainText(self.settings.system_role)
        btn_reset_system = QPushButton("Reset system role to default")

        def reset_system():
            self.system_role_text.setPlainText(
                "You translate subtitles. Output must be ONLY the translated lines, one per input line, without indices, timestamps, or any additional labels."
            )
            self._on_settings_changed()

        btn_reset_system.clicked.connect(reset_system)

        form.addRow("Target language:", self.lang_input)
        form.addRow("API Key:", self.api_key_input)
        form.addRow("Model:", self._build_model_picker())
        form.addRow("API Base:", self.api_base_input)
        form.addRow("Workers:", self.workers_input)
        form.addRow("Window:", self.window_input)
        form.addRow("Overlap:", self.overlap_input)
        form.addRow("Full log:", self.fulllog_checkbox)
        form.addRow("Extra prompt:", self.extra_prompt_input)
        settings_layout_v.addLayout(form)
        settings_layout_v.addWidget(
            QLabel(
                "Main prompt template (uses placeholders {header}, {extra}, {src_block}):"
            )
        )
        settings_layout_v.addWidget(self.main_prompt_text)
        settings_layout_v.addWidget(btn_reset_main_prompt)
        settings_layout_v.addSpacing(8)
        settings_layout_v.addWidget(QLabel("System role (chat system message):"))
        settings_layout_v.addWidget(self.system_role_text)
        settings_layout_v.addWidget(btn_reset_system)

        file_layout = QHBoxLayout()
        self.file_label = ElidedLabel("No file selected")
        btn_browse = QPushButton("Open File (MKV or SRT)...")
        btn_browse.clicked.connect(self.on_browse)
        btn_browse_folder = QPushButton("Open Folder...")
        btn_browse_folder.clicked.connect(self.on_browse_folder)
        file_layout.addWidget(self.file_label, 1)
        file_layout.addWidget(btn_browse)
        file_layout.addWidget(btn_browse_folder)
        layout.addLayout(file_layout)

        actions_layout = QHBoxLayout()
        self.btn_translate = QPushButton("Translate SRT")
        self.btn_translate.clicked.connect(self.on_translate)
        self.btn_translate.setEnabled(False)
        self.btn_translate.setVisible(False)
        self.overwrite_checkbox = QCheckBox("Overwrite the original file")
        self.overwrite_checkbox.setChecked(
            bool(getattr(self.settings, "overwrite_original", False))
        )
        self.overwrite_checkbox.toggled.connect(self._on_settings_changed)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        actions_layout.addWidget(self.btn_translate)
        actions_layout.addWidget(self.overwrite_checkbox)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.btn_cancel)
        layout.addLayout(actions_layout)

        self.batch_info_label = QLabel("Batch Progress:")
        self.batch_info_label.setVisible(False)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.progress = QProgressBar()

        style = """
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
        self.batch_progress.setStyleSheet(style)
        self.progress.setStyleSheet(style)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(60)
        layout.addWidget(self.batch_info_label)
        layout.addWidget(self.batch_progress)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)

        for w in [
            self.lang_input,
            self.api_key_input,
            self.api_base_input,
            self.workers_input,
            self.window_input,
            self.overlap_input,
            self.extra_prompt_input,
        ]:
            w.textChanged.connect(self._on_settings_changed)
        self.main_prompt_text.textChanged.connect(self._on_settings_changed)
        self.system_role_text.textChanged.connect(self._on_settings_changed)
        self.fulllog_checkbox.toggled.connect(self._on_settings_changed)

        self.resize(1100, 700)
        # Allow the user to shrink the window small — Qt's implicit minimum
        # size comes from the sum of child sizeHints, which was ~700 px tall.
        self.setMinimumSize(500, 300)

    def closeEvent(self, event):
        if hasattr(self, "worker") and self.worker.isRunning():
            self._cancel_flag.set()
            self.worker.wait(1000)  # wait a bit
            if self.worker.isRunning():
                self.worker.terminate()  # force kill if needed
        super().closeEvent(event)

    def log_msg(self, msg: str):
        self.log.append(msg)

    def on_cancel(self):
        try:
            if hasattr(self, "_cancel_flag"):
                self._cancel_flag.set()
                self.log_msg("Cancel requested...")
                self.btn_cancel.setEnabled(False)
        except Exception:
            pass

    def _on_worker_done(self, success: bool, err: Optional[str] = None):
        self.btn_cancel.setEnabled(False)
        self.btn_translate.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Error", err or "Unknown error")
        else:
            if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
                self.log_msg("Cancelled.")
                QMessageBox.information(self, "Cancelled", "Translation was cancelled")
            else:
                QMessageBox.information(self, "Done", "Translation complete")

    def _on_settings_changed(self):
        self.settings.target_language = self.lang_input.text().strip()
        self.settings.api_key = self.api_key_input.text().strip()
        self._sync_effective_model()
        self.settings.api_base = self.api_base_input.text().strip()
        try:
            self.settings.extra_prompt = self.extra_prompt_input.text()
        except Exception:
            pass
        try:
            self.settings.main_prompt_template = self.main_prompt_text.toPlainText()
        except Exception:
            pass
        try:
            self.settings.system_role = self.system_role_text.toPlainText()
        except Exception:
            pass
        try:
            workers_val = int(self.workers_input.text().strip())
        except Exception:
            workers_val = self.settings.workers or 5
        workers_val = max(1, min(10, workers_val))
        self.settings.workers = workers_val
        self.workers_input.blockSignals(True)
        self.workers_input.setText(str(workers_val))
        self.workers_input.blockSignals(False)
        try:
            window_val = int(self.window_input.text().strip())
        except Exception:
            window_val = getattr(self.settings, "window", 25) or 25
        window_val = max(1, min(200, window_val))
        self.settings.window = window_val
        self.window_input.blockSignals(True)
        self.window_input.setText(str(window_val))
        self.window_input.blockSignals(False)
        try:
            overlap_val = int(self.overlap_input.text().strip())
        except Exception:
            overlap_val = getattr(self.settings, "overlap", 10) or 10
        overlap_val = max(0, min(200, overlap_val))
        self.settings.overlap = overlap_val
        self.overlap_input.blockSignals(True)
        self.overlap_input.setText(str(overlap_val))
        self.overlap_input.blockSignals(False)
        try:
            self.settings.fulllog = bool(self.fulllog_checkbox.isChecked())
        except Exception:
            self.settings.fulllog = False
        try:
            self.settings.overwrite_original = bool(self.overwrite_checkbox.isChecked())
        except Exception:
            self.settings.overwrite_original = False
        self.settings.save()

    def _build_model_picker(self):
        """Composite widget: combo + Refresh + Custom checkbox + custom input.

        The combo lists models fetched from the API (cached in settings),
        annotated with pricing if known. Refresh hits /v1/models. Custom
        toggle swaps the combo for a manual QLineEdit so users can type an
        arbitrary id (e.g. for a local proxy)."""
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.model_combo = QComboBox()
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.model_combo.setItemDelegate(_ModelPriceDelegate(self.model_combo))

        self.model_custom_input = QLineEdit(self.settings.model)
        self.model_custom_input.setPlaceholderText(
            "Enter custom model id, e.g. gpt-4o-mini"
        )

        self.btn_refresh_models = QPushButton("Refresh")
        self.btn_refresh_models.setToolTip(
            "Fetch the list of available models from the API endpoint."
        )
        # Compact the Refresh button so the Model row matches the height of
        # the other single-line form rows (API Key, API Base, …). We keep
        # the native push-button look but cap padding so it doesn't hang
        # below the combo.
        self.btn_refresh_models.setStyleSheet("QPushButton { padding: 1px 12px; }")
        self.btn_refresh_models.setFixedHeight(self.model_combo.sizeHint().height())

        self.model_custom_checkbox = QCheckBox("Custom")
        self.model_custom_checkbox.setToolTip(
            "Type a model id manually (useful for local proxies or unlisted models)."
        )
        self.model_custom_checkbox.setChecked(
            bool(getattr(self.settings, "use_custom_model", False))
        )

        # Combo and manual-input share the same slot — only one is visible at
        # a time, so the row never grows taller and Refresh/Custom don't move.
        row.addWidget(self.model_combo, 1)
        row.addWidget(self.model_custom_input, 1)
        row.addWidget(self.btn_refresh_models)
        row.addWidget(self.model_custom_checkbox)

        from .pricing import MODEL_PRICING

        initial_models = list(self.settings.cached_models or []) or sorted(
            MODEL_PRICING.keys()
        )
        self._populate_model_combo(initial_models, self.settings.model)
        self._apply_custom_model_mode(
            bool(getattr(self.settings, "use_custom_model", False))
        )

        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        self.btn_refresh_models.clicked.connect(self._on_refresh_models)
        self.model_custom_checkbox.toggled.connect(self._on_model_custom_toggled)
        self.model_custom_input.textChanged.connect(self._on_settings_changed)
        return wrap

    def _populate_model_combo(self, models, selected):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        seen = set()
        if selected and selected not in models:
            self._add_combo_item(selected, is_current_marker=True)
            seen.add(selected)
        for m in models:
            if m in seen:
                continue
            seen.add(m)
            self._add_combo_item(m)
        if selected:
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == selected:
                    self.model_combo.setCurrentIndex(i)
                    break
        self.model_combo.blockSignals(False)

    def _add_combo_item(self, model_id, is_current_marker=False):
        """Add one combo item with pricing metadata for the delegate.

        Item text is the compact fallback used by the closed combo display
        (and accessibility). ``Qt.UserRole`` stores the raw id. UserRole+1
        stores the full pricing dict for ``_ModelPriceDelegate``."""
        from .pricing import get_pricing, format_pricing

        price_dict = get_pricing(model_id)
        short = format_pricing(model_id)
        if is_current_marker:
            label = f"{model_id}  (current)"
        elif short:
            label = f"{model_id}   \u00b7   {short}"
        else:
            label = model_id
        self.model_combo.addItem(label, model_id)
        last = self.model_combo.count() - 1
        self.model_combo.setItemData(last, price_dict, _MODEL_PRICE_ROLE)

    def _apply_custom_model_mode(self, on: bool):
        """Swap combo <-> manual input in the same slot of the single row.

        Refresh is hidden together with the combo (it drives the combo's
        list and is meaningless while in Custom mode)."""
        self.model_combo.setVisible(not on)
        self.btn_refresh_models.setVisible(not on)
        self.model_custom_input.setVisible(on)
        self.model_custom_input.setEnabled(on)

    def _sync_effective_model(self):
        """Push the currently active source (combo or manual) into settings."""
        if getattr(self.settings, "use_custom_model", False):
            val = self.model_custom_input.text().strip()
        else:
            data = self.model_combo.currentData()
            val = (data or self.settings.model or "").strip()
        if val:
            self.settings.model = val

    def _on_model_combo_changed(self, _idx):
        if getattr(self.settings, "use_custom_model", False):
            return
        self._sync_effective_model()
        self.settings.save()

    def _on_model_custom_toggled(self, checked: bool):
        self.settings.use_custom_model = bool(checked)
        self._apply_custom_model_mode(bool(checked))
        # Seed the manual input with the current combo selection on first switch
        if checked and not self.model_custom_input.text().strip():
            self.model_custom_input.setText(self.settings.model or "")
        self._sync_effective_model()
        self.settings.save()

    def _on_refresh_models(self):
        if not self.settings.api_key:
            QMessageBox.warning(
                self,
                "API key required",
                "Set your API key in this tab before refreshing the model list.",
            )
            return
        self.btn_refresh_models.setEnabled(False)
        self.btn_refresh_models.setText("Fetching\u2026")
        self._models_thread = _ModelFetcherThread(self.translator)
        self._models_thread.done.connect(self._on_models_fetched)
        self._models_thread.start()

    def _on_models_fetched(self, models, error):
        self.btn_refresh_models.setEnabled(True)
        self.btn_refresh_models.setText("Refresh")
        if error:
            QMessageBox.critical(self, "Refresh models", error)
            return
        from .pricing import is_text_completion_model

        filtered = sorted({m for m in models if is_text_completion_model(m)})
        self.settings.cached_models = filtered
        self.settings.save()
        self._populate_model_combo(filtered, self.settings.model)
        self.log_msg(
            f"Fetched {len(models)} models from API ({len(filtered)} text-capable kept)."
        )

    def on_browse(self):
        start_dir = self.settings.last_dir or ""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select files",
            start_dir,
            "Video/Subtitles (*.mkv *.srt *.str);;MKV Files (*.mkv);;SRT Files (*.srt *.str)",
        )
        if not files:
            return

        try:
            self.settings.last_dir = os.path.dirname(files[0])
            self.settings.save()
        except Exception:
            pass

        if len(files) == 1:
            path = files[0]
            self.mkv_path = path
            self.batch_files = []
            self.file_label.setText(path)
            self.batch_info_label.setVisible(False)
            self.batch_progress.setVisible(False)

            ext = os.path.splitext(path)[1].lower()

            if ext in (".srt", ".str"):
                self.input_is_srt = True
                self.src_srt_path = path
                self.subtitle_streams = []
                self.btn_translate.setEnabled(True)
                self.btn_translate.setVisible(True)
                # Overwrite original checkbox does not apply to SRT-only mode
                self.overwrite_checkbox.setEnabled(False)
                return
            else:
                self.input_is_srt = False
                self.src_srt_path = None
                self.btn_translate.setVisible(False)
                self.btn_translate.setEnabled(False)
                self.overwrite_checkbox.setEnabled(True)
            try:
                self.subtitle_streams = self._ffprobe_subs(path)
            except Exception as e:
                QMessageBox.critical(self, "ffprobe error", str(e))
                return
            self._run_track_selection_loop([path])
        else:
            self.batch_files = files
            self.mkv_path = None  # Clear single file selection
            self.input_is_srt = False
            self.src_srt_path = None
            self.subtitle_streams = []

            self.file_label.setText(f"Selected {len(files)} files")

            self.btn_translate.setVisible(False)
            self.btn_translate.setEnabled(False)
            self.overwrite_checkbox.setEnabled(True)

            self.batch_info_label.setVisible(True)
            self.batch_info_label.setText("Batch Progress: 0 / " + str(len(files)))
            self.batch_progress.setVisible(True)
            self.batch_progress.setValue(0)

            self._run_track_selection_loop(files)

    def _ffprobe_subs(self, mkv_path: str) -> List[dict]:
        cmd = [
            find_tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_name,codec_type,disposition,bit_rate:stream_tags=language,title",
            "-of",
            "json",
            mkv_path,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
        data = json.loads(proc.stdout)
        return data.get("streams", [])

    def on_browse_folder(self):
        start_dir = self.settings.last_dir or ""
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", start_dir)
        if not folder:
            return

        try:
            self.settings.last_dir = folder
            self.settings.save()
        except Exception:
            pass

        mkv_files = []
        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".mkv"):
                    mkv_files.append(os.path.join(root, f))

        if not mkv_files:
            QMessageBox.information(
                self, "No Files", "No MKV files found in selected folder."
            )
            return

        self.batch_files = mkv_files
        self.mkv_path = None  # Clear single file selection
        self.input_is_srt = False
        self.src_srt_path = None
        self.subtitle_streams = []

        self.file_label.setText(f"Folder: {folder} ({len(mkv_files)} MKV files)")

        self.btn_translate.setVisible(False)
        self.btn_translate.setEnabled(False)
        self.overwrite_checkbox.setEnabled(True)

        self.batch_info_label.setVisible(True)
        self.batch_info_label.setText("Batch Progress: 0 / " + str(len(mkv_files)))
        self.batch_progress.setVisible(True)
        self.batch_progress.setValue(0)

        self._run_track_selection_loop(mkv_files)

    def _find_best_stream(self, mkv_path: str) -> Optional[int]:
        """Find best stream index matching default settings."""
        try:
            streams = self._ffprobe_subs(mkv_path)
        except Exception:
            return None

        target_lang = (self.settings.default_source_lang or "eng").lower()
        target_title = (self.settings.default_source_title or "").lower()

        best_candidate = None

        for st in streams:
            idx = st.get("index")
            lang = (st.get("tags", {}).get("language") or "").lower()
            title = (st.get("tags", {}).get("title") or "").lower()

            if target_lang and target_lang not in lang:
                continue

            if target_title and target_title not in title:
                continue

            return idx

        return None

    def _extract_srt(self, mkv_path: str, stream_index: int) -> str:
        base, _ = os.path.splitext(mkv_path)
        out_srt = base + f".stream{stream_index}.srt"
        cmd = [
            find_tool("ffmpeg"),
            "-y",
            "-i",
            mkv_path,
            "-map",
            f"0:{stream_index}",
            out_srt,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")
        return out_srt

    def on_translate(self):
        """Trigger translation for a standalone .srt/.str file.

        MKV flows are driven by the TrackSelectionDialog loop instead and
        never hit this handler — `btn_translate` is hidden for MKV mode.
        """
        target_lang = self.settings.target_language or "ru"
        if not getattr(self, "input_is_srt", False):
            return  # Defensive: button should not be reachable for MKV
        src_srt = self.src_srt_path
        if not src_srt or not os.path.exists(src_srt):
            QMessageBox.critical(self, "Error", "Subtitle file not found")
            return

        try:
            mkv_size = os.path.getsize(self.mkv_path)
            needed_bytes = mkv_size + (100 * 1024 * 1024)

            output_dir = os.path.dirname(os.path.abspath(self.mkv_path))
            total, used, free = shutil.disk_usage(output_dir)

            if free < needed_bytes:
                msg = (
                    f"Low disk space detected on {output_dir}.\n\n"
                    f"Free: {free / (1024**3):.2f} GB\n"
                    f"Estimated needed: {needed_bytes / (1024**3):.2f} GB\n\n"
                    "Remuxing creates a new copy of the video file. If you proceed, "
                    "the process will likely fail with 'No space left on device'.\n\n"
                    "Do you want to continue anyway?"
                )
                reply = QMessageBox.warning(
                    self,
                    "Insufficient Disk Space",
                    msg,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
        except Exception as e:
            self.log_msg(f"Warning: Could not check disk space: {e}")

        self._cancel_flag = threading.Event()
        self.btn_translate.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker = WorkerThread(
            self._translate_and_remux, src_srt, target_lang, mkv_path=self.mkv_path
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.log_msg)
        self.worker.finished_ok.connect(lambda _: self._on_worker_done(success=True))
        self.worker.failed.connect(
            lambda err: self._on_worker_done(success=False, err=err)
        )
        self.worker.start()

    def _on_batch_info(self, val, text):
        self.batch_progress.setValue(val)
        if text:
            self.batch_info_label.setText(text)

    def _on_worker_request_input(self, req_type, data):
        if req_type == "select_stream":
            fname, streams = data
            self._user_input_result = None

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Select stream for {fname}")
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            layout.addWidget(
                QLabel(
                    f"No match found for settings.\nFile: {fname}\nPlease select a subtitle stream:"
                )
            )

            list_widget = QListWidget()
            for st in streams:
                idx = st.get("index")
                lang = st.get("tags", {}).get("language", "und")
                title = st.get("tags", {}).get("title", "")
                codec = st.get("codec_name", "")
                text = f"Stream #{idx} [{codec}] lang={lang} title='{title}'"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st)
                list_widget.addItem(item)

            layout.addWidget(list_widget)

            chk_default = QCheckBox(
                "Update Default Settings (Lang & Title) from selection"
            )
            chk_default.setChecked(True)
            layout.addWidget(chk_default)

            btns = QHBoxLayout()
            btn_ok = QPushButton("Select")
            btn_skip = QPushButton("Skip File")
            btns.addWidget(btn_ok)
            btns.addWidget(btn_skip)
            layout.addLayout(btns)

            def on_ok():
                if not list_widget.currentItem():
                    QMessageBox.warning(dlg, "Selection", "Please select a stream.")
                    return
                st = list_widget.currentItem().data(Qt.UserRole)
                idx = st.get("index")
                update_defaults = chk_default.isChecked()

                lang = st.get("tags", {}).get("language", "eng")
                title = st.get("tags", {}).get("title", "")

                self._user_input_result = (idx, update_defaults, lang, title)
                dlg.accept()

            def on_skip():
                self._user_input_result = None
                dlg.reject()

            btn_ok.clicked.connect(on_ok)
            btn_skip.clicked.connect(on_skip)

            dlg.exec()
            self._user_input_event.set()

        elif req_type == "update_ui_settings":
            # Legacy path (the Default Src Lang/Title inputs were removed
            # when the per-file popup took over batch selection). The
            # settings fields are still updated in the worker; we just
            # don't need to echo them to UI widgets anymore.
            pass

    def _batch_translate_and_remux(self, target_lang: str, file_decisions=None):
        if file_decisions:
            file_list = list(file_decisions.keys())
        else:
            file_list = list(self.batch_files or [])
        total_files = len(file_list)
        yield ("batch", 0, f"Batch Progress: 0 / {total_files}")
        yield f"Starting batch processing of {total_files} files..."

        for i, fpath in enumerate(file_list, 1):
            if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
                yield "Batch cancelled."
                break

            fname = os.path.basename(fpath)
            pct = int(((i - 1) / total_files) * 100)
            yield ("batch", pct, f"Processing file {i} of {total_files}: {fname}")

            yield f"[{i}/{total_files}] Processing {fname}..."

            decision = (file_decisions or {}).get(fpath)
            if decision is not None:
                if decision.skipped:
                    # Skipped files should not be in dict, but be defensive
                    yield f"Skip {fname}: marked as skipped."
                    continue
                idx = decision.translate_stream_index
                delete_indexes = list(decision.delete_stream_indexes or [])
                try:
                    streams = self._ffprobe_subs(fpath)
                except Exception:
                    streams = []
                self.subtitle_streams = streams

                if idx is None:
                    if not delete_indexes:
                        yield f"Skip {fname}: nothing to translate or delete."
                        continue
                    try:
                        for progress_item in self._remux_delete_only(
                            fpath, delete_indexes
                        ):
                            yield progress_item
                    except Exception as e:
                        yield f"Error processing {fname}: {e}"
                    continue

                try:
                    yield f"Extracting stream #{idx} from {fname}..."
                    src_srt = self._extract_srt(fpath, idx)
                except Exception as e:
                    yield f"Error extracting {fname}: {e}"
                    continue
                try:
                    for progress_item in self._translate_and_remux(
                        src_srt,
                        target_lang,
                        mkv_path=fpath,
                        delete_indexes=delete_indexes or None,
                        source_stream_index=idx,
                    ):
                        yield progress_item
                except Exception as e:
                    yield f"Error processing {fname}: {e}"
                if os.path.exists(src_srt) and not getattr(
                    self.settings, "debug_keep_srt", False
                ):
                    try:
                        os.remove(src_srt)
                    except Exception:
                        pass
                continue

            idx = self._find_best_stream(fpath)

            streams = []
            if idx is None:
                try:
                    streams = self._ffprobe_subs(fpath)
                except Exception:
                    pass

                if not streams:
                    yield f"Skip {fname}: No subtitle streams found."
                    continue

                self._user_input_event.clear()
                self._user_input_result = None
                yield ("input", "select_stream", (fname, streams))

                self._user_input_event.wait()

                if self._user_input_result is None:
                    yield f"Skip {fname}: User skipped selection."
                    continue

                idx, update_defaults, new_lang, new_title = self._user_input_result

                if update_defaults:
                    self.settings.default_source_lang = new_lang
                    self.settings.default_source_title = new_title
                    try:
                        self.settings.save()
                    except Exception:
                        pass
                    yield ("settings_update", new_lang, new_title)
                    yield f"Updated default settings to Lang={new_lang}, Title={new_title}"

            if idx is None:
                yield f"Skip {fname}: No stream selected."
                continue

            if not streams:
                try:
                    streams = self._ffprobe_subs(fpath)
                except Exception:
                    streams = []
            self.subtitle_streams = streams

            try:
                yield f"Extracting stream #{idx} from {fname}..."
                src_srt = self._extract_srt(fpath, idx)
            except Exception as e:
                yield f"Error extracting {fname}: {e}"
                continue

            try:
                for progress_item in self._translate_and_remux(
                    src_srt, target_lang, mkv_path=fpath, source_stream_index=idx
                ):
                    yield progress_item
            except Exception as e:
                yield f"Error processing {fname}: {e}"

            if os.path.exists(src_srt) and not getattr(
                self.settings, "debug_keep_srt", False
            ):
                try:
                    os.remove(src_srt)
                except Exception:
                    pass

        yield ("batch", 100, f"Done. Processed {total_files} files.")
        yield 100
        yield "Batch processing finished."

    def _run_track_selection_loop(self, files):
        """Show TrackSelectionDialog for each file, collect FileDecisions,
        then start processing if at least one file was not skipped.

        carry_over_prefs survives only across non-skipped files."""
        decisions = {}
        carry_over = {}
        cancelled = False
        for i, fpath in enumerate(files):
            is_last = i == len(files) - 1
            try:
                streams = self._ffprobe_subs(fpath)
            except Exception as e:
                QMessageBox.warning(
                    self, "ffprobe error", f"{os.path.basename(fpath)}: {e}"
                )
                streams = []
            initial = match_initial_state(streams, carry_over)
            dlg = TrackSelectionDialog(
                self, fpath, streams, initial, is_last_file=is_last
            )
            dlg.exec()
            d = dlg.get_decision()
            if getattr(d, "cancelled", False):
                cancelled = True
                break
            if not d.skipped:
                decisions[fpath] = d
                carry_over = dlg.carry_over_prefs()

        self._file_decisions = decisions
        if cancelled:
            self.log_msg("Batch cancelled by user — nothing will be processed.")
            return
        if decisions:
            self._start_batch_from_decisions()
        else:
            self.log_msg("All files skipped — nothing to do.")

    def _start_batch_from_decisions(self):
        """Launch the worker on collected per-file decisions."""
        target_lang = self.settings.target_language or "ru"
        self._cancel_flag = threading.Event()
        self._user_input_event = threading.Event()
        self.btn_translate.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker = WorkerThread(
            self._batch_translate_and_remux,
            target_lang,
            file_decisions=self._file_decisions,
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.log_msg)
        self.worker.batch_info.connect(self._on_batch_info)
        self.worker.request_input.connect(self._on_worker_request_input)
        self.worker.finished_ok.connect(lambda _: self._on_worker_done(success=True))
        self.worker.failed.connect(
            lambda err: self._on_worker_done(success=False, err=err)
        )
        self.worker.start()

    def _remux_delete_only(self, mkv_path: str, delete_indexes):
        """Remux a single MKV dropping the specified subtitle stream indexes.

        Used when the user marked tracks for deletion but didn't pick one for
        translation — no extract/translate, just an ffmpeg copy with explicit
        -map exclusions."""
        if not delete_indexes:
            yield f"No streams to delete in {os.path.basename(mkv_path)}"
            return
        try:
            streams = self._ffprobe_subs(mkv_path)
        except Exception as e:
            yield f"ffprobe failed for {os.path.basename(mkv_path)}: {e}"
            return
        exclude_set = set(int(x) for x in delete_indexes)
        kept_subs = [st for st in streams if st.get("index") not in exclude_set]

        overwrite = bool(getattr(self.settings, "overwrite_original", False))
        if overwrite:
            out_mkv = os.path.splitext(mkv_path)[0] + ".__tmp_translated__.mkv"
            yield f"Remuxing (delete only) and overwriting {os.path.basename(mkv_path)}..."
        else:
            out_mkv = os.path.splitext(mkv_path)[0] + ".translated.mkv"
            yield f"Remuxing (delete only) {os.path.basename(mkv_path)}..."

        ffmpeg_cmd = find_tool("ffmpeg")
        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i",
            mkv_path,
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-map",
            "0:t?",
            "-map",
            "0:d?",
        ]
        for st in kept_subs:
            cmd += ["-map", f"0:{st.get('index')}"]
        cmd += ["-c", "copy", "-max_interleave_delta", "0", out_mkv]

        try:
            import shlex

            yield "FFmpeg command:"
            yield " ".join(shlex.quote(x) for x in cmd)
        except Exception:
            pass

        proc = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
        )
        if proc.returncode != 0:
            yield f"FFmpeg exit code: {proc.returncode}"
            if proc.stderr:
                yield "FFmpeg stderr:"
                for line in proc.stderr.splitlines():
                    yield line
            raise RuntimeError(
                proc.stderr.strip() or "ffmpeg failed during delete-only remux"
            )

        if overwrite and os.path.exists(out_mkv):
            try:
                os.replace(out_mkv, mkv_path)
                yield "Original MKV overwritten with deletion result."
            except Exception as e:
                yield f"[Warning] Could not overwrite original file: {e}. Kept new file as {out_mkv}"

    def _translate_and_remux(
        self,
        src_srt: str,
        target_lang: str,
        mkv_path: str = None,
        delete_indexes=None,
        source_stream_index=None,
    ):
        if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
            yield "Cancelled."
            return
        yield "Reading SRT..."
        with open(src_srt, "r", encoding="utf-8", errors="ignore") as f:
            srt_text = f.read()
        entries = list(srt.parse(srt_text))
        if not entries:
            raise RuntimeError("No subtitles parsed from SRT")

        window = max(1, int(getattr(self.settings, "window", 25) or 25))
        overlap = max(0, int(getattr(self.settings, "overlap", 10) or 10))
        # Implement symmetric half-overlap context. For overlap=10, half=5.
        # We translate extended ranges but only keep the core (non-overlap) part from each batch.
        n = len(entries)
        step = max(1, window)
        core_ranges = [(s, min(s + window, n)) for s in range(0, n, step)]
        half = overlap // 2
        groups = []  # list of (core_start, core_end, trans_start, trans_end)
        for core_start, core_end in core_ranges:
            trans_start = max(0, core_start - half)
            trans_end = min(n, core_end + half)
            groups.append((core_start, core_end, trans_start, trans_end))

        translated_entries = {}
        total_groups = len(groups)
        max_workers = max(1, min(10, int(getattr(self.settings, "workers", 5) or 5)))

        def translate_group(
            task_id: int,
            core_start: int,
            core_end: int,
            trans_start: int,
            trans_end: int,
        ):
            group_local = entries[trans_start:trans_end]
            prompt_local = self.translator.build_prompt(group_local, target_lang)
            result = self.translator.chat_translate(prompt_local)
            if isinstance(result, tuple):
                text, dbg = result
            else:
                text, dbg = result, None
            # We expect plain lines corresponding 1:1 to inputs; be strict in splitting
            # First, try to parse as SRT in case model ignored instructions
            try:
                segs = list(srt.parse(text))
                if segs:
                    return (
                        task_id,
                        core_start,
                        core_end,
                        trans_start,
                        trans_end,
                        "ok",
                        segs,
                        dbg,
                    )
            except Exception:
                pass
            # Try to parse our numbered format
            numbered = {}
            cur_idx = None
            buff = []
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                # Preserve blank lines inside content; only strip for header detection
                if line.strip().endswith(":") and line.strip()[:-1].isdigit():
                    if cur_idx is not None:
                        numbered[cur_idx] = "\n".join(buff)
                    cur_idx = int(line.strip()[:-1])
                    buff = []
                else:
                    buff.append(line)
            if cur_idx is not None:
                numbered[cur_idx] = "\n".join(buff)
            if numbered:
                # Map back to group order by original indices
                mapped = []
                for orig in group_local:
                    mapped.append(numbered.get(orig.index, ""))
                return (
                    task_id,
                    core_start,
                    core_end,
                    trans_start,
                    trans_end,
                    "numbered",
                    mapped,
                    dbg,
                )
            # Fallback: treat as plain lines; keep exactly the number of lines as inputs
            contents = [
                c.strip()
                for c in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            ]
            contents = [c for c in contents if c != ""]
            # If count mismatches, pad with empty or truncate
            expected = len(group_local)
            if len(contents) < expected:
                contents += [""] * (expected - len(contents))
            elif len(contents) > expected:
                contents = contents[:expected]
            return (
                task_id,
                core_start,
                core_end,
                trans_start,
                trans_end,
                "fallback",
                contents,
                dbg,
            )

        yield f"Submitting {total_groups} groups to {max_workers} workers..."
        completed = 0
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(translate_group, i, core_s, core_e, trans_s, trans_e): (
                    i,
                    core_s,
                    core_e,
                    trans_s,
                    trans_e,
                )
                for i, (core_s, core_e, trans_s, trans_e) in enumerate(groups, 1)
            }
            for fut in as_completed(futures):
                if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
                    yield "Cancellation requested. Waiting for running tasks to finish..."
                    break
                i, core_s, core_e, trans_s, trans_e = futures[fut]
                try:
                    res = fut.result()
                    results[i] = res
                    if getattr(self.settings, "fulllog", False):
                        dbg = (
                            res[-1]
                            if isinstance(res, (list, tuple)) and len(res) >= 8
                            else None
                        )
                        if isinstance(dbg, dict):
                            try:
                                req_str = json.dumps(
                                    {
                                        "url": dbg.get("url"),
                                        "headers": dbg.get("headers"),
                                        "body": dbg.get("body"),
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                                resp_str = json.dumps(
                                    dbg.get("response_json"),
                                    ensure_ascii=False,
                                    indent=2,
                                )
                                yield f"[FullLog] Request (group {i}):\n{req_str}"
                                yield f"[FullLog] Response (group {i}):\nHTTP {dbg.get('status')}\n{resp_str}"
                            except Exception:
                                pass
                    completed += 1
                    pct = int(completed / total_groups * 80)
                    yield pct
                    yield f"Translated group {i}/{total_groups} (core {core_s+1}-{core_e}, translated {trans_s+1}-{trans_e})"
                except Exception as err:
                    raise RuntimeError(f"Group {i} failed: {err}")

        if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
            yield "Cancelled before assembling results."
            return
        for gi, (core_start, core_end, trans_start, trans_end) in enumerate(groups, 1):
            res_tuple = results[gi]
            _, r_core_s, r_core_e, r_trans_s, r_trans_e, kind, payload = res_tuple[:7]
            group_local = entries[r_trans_s:r_trans_e]
            core_rel_start = max(0, r_core_s - r_trans_s)
            core_rel_end = max(
                core_rel_start, min(len(group_local), r_core_e - r_trans_s)
            )
            if kind == "ok":
                if len(payload) != len(group_local):
                    yield f"[Warning] Model returned {len(payload)} segments, expected {len(group_local)} for translated window indices {group_local[0].index}-{group_local[-1].index}."
                for idx_in_group in range(core_rel_start, core_rel_end):
                    orig = entries[r_trans_s + idx_in_group]
                    seg_content = (
                        payload[idx_in_group].content
                        if idx_in_group < len(payload)
                        else ""
                    )
                    clean = self._sanitize_content(seg_content)
                    translated_entries[orig.index] = srt.Subtitle(
                        index=orig.index, start=orig.start, end=orig.end, content=clean
                    )
            else:
                if len(payload) != len(group_local):
                    yield f"[Warning] Line count mismatch in translated window {group_local[0].index}-{group_local[-1].index}: got {len(payload)}, expected {len(group_local)}."
                for idx_in_group in range(core_rel_start, core_rel_end):
                    orig = entries[r_trans_s + idx_in_group]
                    text = payload[idx_in_group] if idx_in_group < len(payload) else ""
                    clean = self._sanitize_content(text)
                    translated_entries[orig.index] = srt.Subtitle(
                        index=orig.index, start=orig.start, end=orig.end, content=clean
                    )

        if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
            yield "Cancelled before writing SRT."
            return
        yield "Building translated SRT..."
        if len(translated_entries) != len(entries):
            missing = [e.index for e in entries if e.index not in translated_entries]
            raise RuntimeError(
                f"Missing translated entries for indices: {missing[:10]}{'...' if len(missing)>10 else ''}"
            )
        ordered = [translated_entries[idx] for idx in sorted(translated_entries.keys())]
        ordered = list(srt.sort_and_reindex(ordered, start_index=1))
        translated_srt_text = srt.compose(ordered)
        try:
            parsed_back = list(srt.parse(translated_srt_text))
            if len(parsed_back) != len(ordered):
                raise ValueError(
                    f"SRT validation failed: expected {len(ordered)} entries, got {len(parsed_back)}"
                )
        except Exception as e:
            raise RuntimeError(f"Generated SRT is invalid: {e}")
        translated_srt_text = translated_srt_text.replace("\r\n", "\n").replace(
            "\r", "\n"
        )
        if not translated_srt_text.endswith("\n"):
            translated_srt_text += "\n"
        translated_srt_text = translated_srt_text.replace("\n", "\r\n")
        base, _ = os.path.splitext(src_srt)
        out_srt = base + f".{target_lang}.translated.srt"
        if getattr(self, "input_is_srt", False):
            model = (getattr(self.settings, "model", "") or "").strip()
            window_sz = int(getattr(self.settings, "window", 25) or 25)
            # Sanitize model for filesystem (keep alnum, dash, underscore, dot)
            safe_model = (
                "".join(
                    ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_"
                    for ch in model
                )
                or "model"
            )
            out_srt = base + f".{target_lang}.translated.{safe_model}.w{window_sz}.srt"
        with open(out_srt, "w", encoding="utf-8", newline="") as f:
            f.write(translated_srt_text)

        if getattr(self, "input_is_srt", False) or (
            self.mkv_path
            and os.path.splitext(self.mkv_path)[1].lower() not in (".mkv",)
        ):
            yield 100
            yield f"Done. Output: {out_srt}"
            return
        if hasattr(self, "_cancel_flag") and self._cancel_flag.is_set():
            yield "Cancelled before remux."
            return
        remux_src = mkv_path or self.mkv_path
        if not remux_src:
            yield "No MKV path provided for remux."
            return

        overwrite = bool(getattr(self.settings, "overwrite_original", False))
        if overwrite:
            out_mkv = os.path.splitext(remux_src)[0] + ".__tmp_translated__.mkv"
            yield "Remuxing and overwriting the original MKV with translated subtitles..."
        else:
            out_mkv = os.path.splitext(remux_src)[0] + ".translated.mkv"
            yield "Remuxing new MKV with translated subtitles..."
        ffmpeg_cmd = find_tool("ffmpeg")
        src_title = None
        if source_stream_index is not None:
            try:
                for st in self.subtitle_streams:
                    if st.get("index") == source_stream_index:
                        src_title = (st.get("tags") or {}).get("title")
                        break
            except Exception:
                src_title = None

        # Number of subtitle streams that will be COPIED from input (excluding any
        # the user marked for deletion). This is the 0-based index of the new
        # translated track in the output file.
        exclude_set = set(int(x) for x in (delete_indexes or []))
        kept_input_subs = [
            st for st in self.subtitle_streams if st.get("index") not in exclude_set
        ]
        existing_subs_count = len(kept_input_subs)

        if (
            self.settings.cached_source_lang_input == target_lang
            and self.settings.cached_tag_lang
            and self.settings.cached_iso3
        ):
            tag_lang = self.settings.cached_tag_lang
            iso3 = self.settings.cached_iso3
        else:
            try:
                tag_lang = self._infer_lang_for_tag(target_lang)
                yield f"Normalized language for MKV tag: '{tag_lang}' (from '{target_lang}')"
            except Exception as _e:
                tag_lang = "und"
                yield f"[Warning] Could not normalize language for MKV tag, using '{tag_lang}'. Error: {_e}"

            try:
                iso3 = self._infer_iso3(target_lang)
                yield f"ISO 639-2 code inferred: '{iso3}' (from '{target_lang}')"
            except Exception as _e:
                iso3 = "und"
                yield f"[Warning] Could not infer ISO 639-2 code via chat. Using '{iso3}'. Error: {_e}"

            self.settings.cached_source_lang_input = target_lang
            self.settings.cached_tag_lang = tag_lang
            self.settings.cached_iso3 = iso3
            try:
                self.settings.save()
            except Exception:
                pass

        if not iso3 or not isinstance(iso3, str) or len(iso3) != 3:
            iso3 = "und"
        if not src_title:
            src_title = f"Translated [{iso3}] ({tag_lang})"
        else:
            src_title = f"{src_title} | Translated [{iso3}] ({tag_lang})"

        if exclude_set:
            cmd = [
                ffmpeg_cmd,
                "-y",
                "-i",
                remux_src,
                "-f",
                "srt",
                "-i",
                out_srt,
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-map",
                "0:t?",
                "-map",
                "0:d?",
            ]
            for st in kept_input_subs:
                cmd += ["-map", f"0:{st.get('index')}"]
            cmd += [
                "-map",
                "1:0",
                "-c",
                "copy",
                "-max_interleave_delta",
                "0",
                "-c:s:" + str(existing_subs_count),
                "srt",
                "-metadata:s:s:" + str(existing_subs_count),
                f"language={iso3}",
                "-metadata:s:s:" + str(existing_subs_count),
                f"title={src_title}",
                out_mkv,
            ]
        else:
            cmd = [
                ffmpeg_cmd,
                "-y",
                "-i",
                remux_src,
                "-f",
                "srt",
                "-i",
                out_srt,
                "-map",
                "0",
                "-map",
                "1:0",
                "-c",
                "copy",
                "-max_interleave_delta",
                "0",
                "-c:s:" + str(existing_subs_count),
                "srt",
                "-metadata:s:s:" + str(existing_subs_count),
                f"language={iso3}",
                "-metadata:s:s:" + str(existing_subs_count),
                f"title={src_title}",
                out_mkv,
            ]

        try:
            import shlex

            yield "FFmpeg command:"
            yield " ".join(shlex.quote(x) for x in cmd)
        except Exception:
            pass

        proc = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
        )
        if proc.returncode != 0:
            yield f"FFmpeg exit code: {proc.returncode}"
            if proc.stderr:
                yield "FFmpeg stderr:"
                for line in proc.stderr.splitlines():
                    yield line
            try:
                if os.path.exists(out_srt):
                    os.remove(out_srt)
                if os.path.exists(src_srt):
                    os.remove(src_srt)
            except Exception:
                pass
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed during remux")

        try:
            if overwrite:
                if os.path.exists(out_mkv):
                    try:
                        os.replace(out_mkv, mkv_path)
                        out_mkv = mkv_path
                        yield "Original MKV has been overwritten with translated version."
                    except Exception as e:
                        yield f"[Warning] Could not overwrite original file: {e}. Keeping new file as {out_mkv}"
                else:
                    yield "[Warning] Expected temporary output file not found after remux; cannot overwrite original."
        except Exception:
            pass

        try:
            if not getattr(self, "input_is_srt", False):
                if os.path.exists(out_srt):
                    os.remove(out_srt)
                if os.path.exists(src_srt):
                    os.remove(src_srt)
        except Exception:
            try:
                yield f"[Warning] Could not remove temporary files: {out_srt} or {src_srt}"
            except Exception:
                pass

        yield 100
        yield f"Done. Output: {out_mkv}"

    def _infer_lang_for_tag(self, raw_lang: str) -> str:
        """
        Convert user's language input to a short English phrase for MKV lang= tag.
        Tries chat normalization first; falls back to simple mappings/ascii cleanup.
        Always returns a non-empty string; if everything fails, returns 'und'.
        """

        def sanitize(s: str) -> str:
            s = s.lower().strip()
            allowed = "".join(
                ch for ch in s if (ch.isalnum() and ord(ch) < 128) or ch == " "
            )
            parts = [p for p in allowed.split(" ") if p]
            out = " ".join(parts)
            return out[:30] if out else out

        try:
            res = self.translator.chat_normalize_lang(raw_lang)
            if isinstance(res, tuple):
                text, dbg = res
            else:
                text, dbg = res, None
            out = sanitize(text)
            if out:
                return out
        except Exception:
            pass
        rl = (raw_lang or "").strip().lower()
        ascii_only = "".join(
            ch for ch in rl if (ch.isalnum() and ord(ch) < 128) or ch == " "
        )
        ascii_only = " ".join([p for p in ascii_only.split(" ") if p])
        return ascii_only[:30] if ascii_only else "und"

    def _infer_iso3(self, raw_lang: str) -> str:
        """Infer ISO 639-2 code using chat first; fallback to 'und'."""
        try:
            res = self.translator.chat_infer_iso3(raw_lang)
            if isinstance(res, tuple):
                code, dbg = res
            else:
                code, dbg = res, None
            code = (code or "").strip().lower()
            if len(code) == 3 and code.isalpha():
                return code
        except Exception:
            pass
        rl = (raw_lang or "").strip().lower()
        if len(rl) == 3 and rl.isalpha():
            return rl
        return "und"
