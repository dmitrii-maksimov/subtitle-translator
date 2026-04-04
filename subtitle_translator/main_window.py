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
    QMainWindow, QWidget, QFileDialog, QMessageBox, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QLineEdit, QComboBox, QProgressBar, QTextEdit, QCheckBox, QTabWidget, QDialog, QFormLayout, QProgressDialog,
    QListWidgetItem, QSizePolicy
)
from PySide6.QtGui import QPainter


from .models import AppSettings
from .utils import check_ffmpeg_available, install_ffmpeg, get_base_dir


class WorkerThread(QThread):
    progress = Signal(int)
    status = Signal(str)
    batch_info = Signal(int, str)
    request_input = Signal(str, object) # type, data
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
                elif isinstance(update, tuple) and len(update) == 3 and update[0] == 'batch':
                    # ('batch', percent, text)
                    self.batch_info.emit(update[1], update[2])
                elif isinstance(update, tuple) and len(update) == 3 and update[0] == 'input':
                    # ('input', type, data)
                    self.request_input.emit(update[1], update[2])
                elif isinstance(update, tuple) and len(update) == 3 and update[0] == 'settings_update':
                     # ('settings_update', key_value_dict, None) or just pass dict as second arg
                     # Let's simple pass ('settings_update', lang, title)
                     self.request_input.emit('update_ui_settings', (update[1], update[2]))
            self.finished_ok.emit("done")
        except Exception as e:
            self.failed.emit(str(e))


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

from .services import TranslationService

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Translator")
        self.settings = AppSettings.load()
        self.translator = TranslationService(self.settings)
        # Set defaults for new fields if absent/empty
        if getattr(self.settings, 'overlap', None) is None:
            self.settings.overlap = 10
        if not getattr(self.settings, 'main_prompt_template', None):
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
        if not getattr(self.settings, 'system_role', None):
            self.settings.system_role = (
                "You translate subtitles. Output must be ONLY the translated lines, one per input line, without indices, timestamps, or any additional labels."
            )

        self.mkv_path: Optional[str] = None
        self.subtitle_streams: List[dict] = []
        self.input_is_srt: bool = False
        self.src_srt_path: Optional[str] = None
        self.batch_files: List[str] = []
        
        # User interaction sync
        self._user_input_event = threading.Event()
        self._user_input_result = None

        self._build_ui()
        # Check ffmpeg availability early
        self.check_and_offer_install_ffmpeg()


    def check_and_offer_install_ffmpeg(self):
        if check_ffmpeg_available():
            return

        reply = QMessageBox.question(
            self, 
            "FFmpeg Missing", 
            "FFmpeg is required for this application but was not found.\n\nDo you want to download and install it automatically?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.download_ffmpeg()
        else:
            QMessageBox.warning(self, "Restricted Functionality", "Without FFmpeg, you cannot extract or remux subtitles. Only standalone SRT translation will work.")

    def download_ffmpeg(self):
        progress = QProgressDialog("Downloading and installing FFmpeg...", "Cancel", 0, 100, self)
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
                QMessageBox.information(self, "Success", "FFmpeg installed successfully.")
            else:
                if msg == "Cancelled by user":
                    pass
                else:
                    QMessageBox.critical(self, "Error", f"Failed to install FFmpeg: {msg}")

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
        ts_re = re.compile(r"^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{3}$")
        idx_re = re.compile(r"^\d{1,5}$")
        # Work in a non-destructive way regarding internal spaces and line breaks
        # Normalize temporarily to split, but we won't strip each line
        tmp = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = tmp.split("\n")
        cleaned = []
        for ln in lines:
            if idx_re.match(ln.strip()) or ts_re.match(ln.strip()):
                # skip spurious control lines
                continue
            cleaned.append(ln)
        # Join back with LF; final CRLF will be applied at write stage
        return "\n".join(cleaned)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        # Tabs: Main and Settings
        self.tabs = QTabWidget()
        main_tab = QWidget()
        settings_tab = QWidget()
        self.tabs.addTab(main_tab, "Main")
        self.tabs.addTab(settings_tab, "Settings")
        main_layout.addWidget(self.tabs)

        # Main tab layout
        layout = QVBoxLayout(main_tab)

        # File controls and list will be on Main tab, settings on Settings tab

        # Settings tab UI
        settings_layout_v = QVBoxLayout(settings_tab)
        form = QFormLayout()
        self.lang_input = QLineEdit(self.settings.target_language)
        self.lang_input.setPlaceholderText("Target language, e.g. ru, en, es...")
        self.api_key_input = QLineEdit(self.settings.api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("OpenAI API Key")
        self.model_input = QLineEdit(self.settings.model)
        self.model_input.setPlaceholderText("Model, e.g., gpt-4o-mini")
        self.api_base_input = QLineEdit(self.settings.api_base)
        self.api_base_input.setPlaceholderText("API Base URL (OpenAI-compatible)")
        self.workers_input = QLineEdit(str(self.settings.workers))
        self.workers_input.setPlaceholderText("Workers (1-10)")
        self.window_input = QLineEdit(str(getattr(self.settings, 'window', 25)))
        self.window_input.setPlaceholderText("Window (1-200)")
        # Overlap context size (number of subtitles, symmetric half applied)
        self.overlap_input = QLineEdit(str(getattr(self.settings, 'overlap', 10)))
        self.overlap_input.setPlaceholderText("Overlap (0-200)")
        self.fulllog_checkbox = QCheckBox("Full log (requests && responses)")
        self.fulllog_checkbox.setChecked(bool(getattr(self.settings, 'fulllog', False)))
        self.extra_prompt_input = QLineEdit(self.settings.extra_prompt if hasattr(self.settings, 'extra_prompt') else "")
        self.extra_prompt_input.setPlaceholderText("Optional extra instruction for translation (will be enforced)")
        
        # Default source settings (for batch mode)
        self.def_src_lang_input = QLineEdit(self.settings.default_source_lang)
        self.def_src_lang_input.setPlaceholderText("Default source lang (e.g. eng)")
        self.def_src_title_input = QLineEdit(self.settings.default_source_title)
        self.def_src_title_input.setPlaceholderText("Default source title substring (e.g. Full)")

        # Main Prompt Template (multiline)
        self.main_prompt_text = QTextEdit()
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

        # System role
        self.system_role_text = QTextEdit()
        self.system_role_text.setPlainText(self.settings.system_role)
        btn_reset_system = QPushButton("Reset system role to default")
        def reset_system():
            self.system_role_text.setPlainText("You translate subtitles. Output must be ONLY the translated lines, one per input line, without indices, timestamps, or any additional labels.")
            self._on_settings_changed()
        btn_reset_system.clicked.connect(reset_system)

        form.addRow("Target language:", self.lang_input)
        form.addRow("API Key:", self.api_key_input)
        form.addRow("Model:", self.model_input)
        form.addRow("API Base:", self.api_base_input)
        form.addRow("Workers:", self.workers_input)
        form.addRow("Window:", self.window_input)
        form.addRow("Overlap:", self.overlap_input)
        form.addRow("Full log:", self.fulllog_checkbox)
        form.addRow("Extra prompt:", self.extra_prompt_input)
        form.addRow("Default Src Lang:", self.def_src_lang_input)
        form.addRow("Default Src Title:", self.def_src_title_input)
        settings_layout_v.addLayout(form)
        settings_layout_v.addWidget(QLabel("Main prompt template (uses placeholders {header}, {extra}, {src_block}):"))
        settings_layout_v.addWidget(self.main_prompt_text)
        settings_layout_v.addWidget(btn_reset_main_prompt)
        settings_layout_v.addSpacing(8)
        settings_layout_v.addWidget(QLabel("System role (chat system message):"))
        settings_layout_v.addWidget(self.system_role_text)
        settings_layout_v.addWidget(btn_reset_system)

        # File controls
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

        # Streams list
        layout.addWidget(QLabel("Subtitle tracks:"))
        self.streams_list = QListWidget()
        layout.addWidget(self.streams_list)
        # Show full details as tooltip on hover
        self.streams_list.itemEntered.connect(lambda item: None)  # placeholder to allow tooltips

        # Actions
        actions_layout = QHBoxLayout()
        self.btn_extract = QPushButton("Extract Subtitles")
        self.btn_extract.clicked.connect(self.on_extract)
        self.btn_extract.setEnabled(False)
        self.btn_translate = QPushButton("Translate && Remux")
        self.btn_translate.clicked.connect(self.on_translate)
        self.btn_translate.setEnabled(False)
        # Overwrite checkbox
        self.overwrite_checkbox = QCheckBox("Overwrite the original file")
        self.overwrite_checkbox.setChecked(bool(getattr(self.settings, 'overwrite_original', False)))
        self.overwrite_checkbox.toggled.connect(self._on_settings_changed)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        actions_layout.addWidget(self.btn_extract)
        actions_layout.addWidget(self.btn_translate)
        actions_layout.addWidget(self.overwrite_checkbox)
        actions_layout.addWidget(self.btn_cancel)
        layout.addLayout(actions_layout)

        # Progress and log
        self.batch_info_label = QLabel("Batch Progress:")
        self.batch_info_label.setVisible(False)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.progress = QProgressBar()
        
        # Style progress bars: thicker, centered text
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
        layout.addWidget(self.batch_info_label)
        layout.addWidget(self.batch_progress)
        layout.addWidget(self.progress)
        layout.addWidget(self.log)

        # Connect inputs to save settings on change
        for w in [self.lang_input, self.api_key_input, self.model_input, self.api_base_input, 
                  self.workers_input, self.window_input, self.overlap_input, self.extra_prompt_input,
                  self.def_src_lang_input, self.def_src_title_input]:
            w.textChanged.connect(self._on_settings_changed)
        self.main_prompt_text.textChanged.connect(self._on_settings_changed)
        self.system_role_text.textChanged.connect(self._on_settings_changed)
        self.fulllog_checkbox.toggled.connect(self._on_settings_changed)
        # already connected: self.overwrite_checkbox.toggled

        self.resize(1100, 700)

    def closeEvent(self, event):
        # Ensure we kill any running workers/processes on exit
        if hasattr(self, 'worker') and self.worker.isRunning():
            self._cancel_flag.set()
            self.worker.wait(1000) # wait a bit
            if self.worker.isRunning():
                self.worker.terminate() # force kill if needed
        super().closeEvent(event)

    def log_msg(self, msg: str):
        self.log.append(msg)

    def on_cancel(self):
        try:
            if hasattr(self, '_cancel_flag'):
                self._cancel_flag.set()
                self.log_msg("Cancel requested...")
                self.btn_cancel.setEnabled(False)
        except Exception:
            pass

    def _on_worker_done(self, success: bool, err: Optional[str] = None):
        # Reset UI buttons regardless of outcome
        self.btn_cancel.setEnabled(False)
        self.btn_translate.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Error", err or "Unknown error")
        else:
            if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
                self.log_msg("Cancelled.")
                QMessageBox.information(self, "Cancelled", "Translation was cancelled")
            else:
                QMessageBox.information(self, "Done", "Translation complete")

    def _on_settings_changed(self):
        self.settings.target_language = self.lang_input.text().strip()
        self.settings.api_key = self.api_key_input.text().strip()
        self.settings.model = self.model_input.text().strip()
        self.settings.api_base = self.api_base_input.text().strip()
        # Optional prompts
        try:
            self.settings.extra_prompt = self.extra_prompt_input.text()
        except Exception:
            pass
        self.settings.default_source_lang = self.def_src_lang_input.text().strip()
        self.settings.default_source_title = self.def_src_title_input.text().strip()
        try:
            self.settings.main_prompt_template = self.main_prompt_text.toPlainText()
        except Exception:
            pass
        try:
            self.settings.system_role = self.system_role_text.toPlainText()
        except Exception:
            pass
        # validate workers
        try:
            workers_val = int(self.workers_input.text().strip())
        except Exception:
            workers_val = self.settings.workers or 5
        workers_val = max(1, min(10, workers_val))
        self.settings.workers = workers_val
        self.workers_input.blockSignals(True)
        self.workers_input.setText(str(workers_val))
        self.workers_input.blockSignals(False)
        # validate window size
        try:
            window_val = int(self.window_input.text().strip())
        except Exception:
            window_val = getattr(self.settings, 'window', 25) or 25
        window_val = max(1, min(200, window_val))
        self.settings.window = window_val
        self.window_input.blockSignals(True)
        self.window_input.setText(str(window_val))
        self.window_input.blockSignals(False)
        # validate overlap size
        try:
            overlap_val = int(self.overlap_input.text().strip())
        except Exception:
            overlap_val = getattr(self.settings, 'overlap', 10) or 10
        overlap_val = max(0, min(200, overlap_val))
        self.settings.overlap = overlap_val
        self.overlap_input.blockSignals(True)
        self.overlap_input.setText(str(overlap_val))
        self.overlap_input.blockSignals(False)
        # fulllog
        try:
            self.settings.fulllog = bool(self.fulllog_checkbox.isChecked())
        except Exception:
            self.settings.fulllog = False
        # overwrite_original
        try:
            self.settings.overwrite_original = bool(self.overwrite_checkbox.isChecked())
        except Exception:
            self.settings.overwrite_original = False
        self.settings.save()

    def on_browse(self):
        start_dir = self.settings.last_dir or ""
        files, _ = QFileDialog.getOpenFileNames(self, "Select files", start_dir, "Video/Subtitles (*.mkv *.srt *.str);;MKV Files (*.mkv);;SRT Files (*.srt *.str)")
        if not files:
            return
        
        # Save directory of the first file
        try:
            self.settings.last_dir = os.path.dirname(files[0])
            self.settings.save()
        except Exception:
            pass

        if len(files) == 1:
            # Single file mode
            path = files[0]
            self.mkv_path = path
            self.batch_files = []
            self.file_label.setText(path)
            self.batch_info_label.setVisible(False)
            self.batch_progress.setVisible(False)
            
            # Detect file type
            ext = os.path.splitext(path)[1].lower()
            self.streams_list.clear() # from PySide6.QtWidgets import QListWidgetItem
            
            if ext in (".srt", ".str"):
                # SRT/STR mode: we will translate a standalone subtitle and save next to it
                self.input_is_srt = True
                self.src_srt_path = path
                self.subtitle_streams = []
                self.streams_list.addItem(QListWidgetItem("Standalone subtitle file (no tracks)"))
                self.btn_extract.setEnabled(False)
                self.btn_translate.setEnabled(True)
                self.btn_translate.setText("Translate SRT")
                # Overwrite original checkbox does not apply to SRT-only mode
                self.overwrite_checkbox.setEnabled(False)
                return
            else:
                self.input_is_srt = False
                self.src_srt_path = None
                self.btn_translate.setText("Translate && Remux")
                self.overwrite_checkbox.setEnabled(True)
            try:
                self.subtitle_streams = self._ffprobe_subs(path)
                self.streams_list.clear()
                for st in self.subtitle_streams:
                    lang = st.get("tags", {}).get("language", "und")
                    title = st.get("tags", {}).get("title")
                    codec = st.get("codec_name", "unknown")
                    index = st.get("index")
                    disp = st.get("disposition", {}) or {}
                    flags = []
                    if disp.get("default"):
                        flags.append("default")
                    if disp.get("forced"):
                        flags.append("forced")
                    if disp.get("hearing_impaired"):
                        flags.append("SDH")
                    if disp.get("visual_impaired"):
                        flags.append("VI")
                    flags_str = ("; ".join(flags)) if flags else ""
                    parts = [f"Stream #{index}", f"[{codec}]", f"lang={lang}"]
                    if title:
                        parts.append(f"title=\"{title}\"")
                    if flags_str:
                        parts.append(flags_str)
                    item_text = " ".join(parts)
                    # from PySide6.QtWidgets import QListWidgetItem
                    qitem = QListWidgetItem(item_text)
                    # Build tooltip with more details
                    tip_lines = []
                    tip_lines.append(f"index: {index}")
                    tip_lines.append(f"codec: {codec}")
                    tip_lines.append(f"language: {lang}")
                    if title:
                        tip_lines.append(f"title: {title}")
                    if flags_str:
                        tip_lines.append(f"flags: {flags_str}")
                    br = st.get("bit_rate")
                    if br:
                        tip_lines.append(f"bitrate: {br}")
                    # include raw tags keys to help distinguish releases
                    tags = st.get("tags", {}) or {}
                    for k, v in tags.items():
                        if k not in ("language", "title"):
                            tip_lines.append(f"tag {k}: {v}")
                    qitem.setToolTip("\n".join(tip_lines))
                    self.streams_list.addItem(qitem)
                self.btn_extract.setEnabled(len(self.subtitle_streams) > 0)
                self.btn_translate.setEnabled(len(self.subtitle_streams) > 0)
            except Exception as e:
                QMessageBox.critical(self, "ffprobe error", str(e))
        else:
            # Batch mode (multiple files)
            self.batch_files = files
            self.mkv_path = None # Clear single file selection
            self.input_is_srt = False
            self.src_srt_path = None
            self.subtitle_streams = []
            self.streams_list.clear()

            self.file_label.setText(f"Selected {len(files)} files")
            
            self.streams_list.addItem(QListWidgetItem(f"Batch Mode: {len(files)} files queued."))
            self.streams_list.addItem(QListWidgetItem(f"Default Lang: {self.settings.default_source_lang}"))
            self.streams_list.addItem(QListWidgetItem(f"Default Title: {self.settings.default_source_title}"))
            
            self.btn_extract.setEnabled(False)
            self.btn_translate.setText("Batch Translate && Remux")
            self.btn_translate.setEnabled(True)
            self.overwrite_checkbox.setEnabled(True)
            
            self.batch_info_label.setVisible(True)
            self.batch_info_label.setText("Batch Progress: 0 / " + str(len(files)))
            self.batch_progress.setVisible(True)
            self.batch_progress.setValue(0)

    def _ffprobe_subs(self, mkv_path: str) -> List[dict]:
        base_dir = get_base_dir()
        ffprobe_exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffprobe = os.path.join(base_dir, ffprobe_exe)
        
        ffprobe_cmd = ffprobe if os.path.exists(ffprobe) else "ffprobe"
        cmd = [
            ffprobe_cmd, "-v", "error", "-select_streams", "s",
            "-show_entries", "stream=index,codec_name,codec_type,disposition,bit_rate:stream_tags=language,title",
            "-of", "json", mkv_path,
        ]
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
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

        # Scan for MKV files
        mkv_files = []
        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith('.mkv'):
                    mkv_files.append(os.path.join(root, f))
        
        if not mkv_files:
            QMessageBox.information(self, "No Files", "No MKV files found in selected folder.")
            return

        self.batch_files = mkv_files
        self.mkv_path = None # Clear single file selection
        self.input_is_srt = False
        self.src_srt_path = None
        self.subtitle_streams = []
        self.streams_list.clear()

        self.file_label.setText(f"Folder: {folder} ({len(mkv_files)} MKV files)")
        
        self.streams_list.addItem(QListWidgetItem(f"Batch Mode: {len(mkv_files)} files queued."))
        self.streams_list.addItem(QListWidgetItem(f"Default Lang: {self.settings.default_source_lang}"))
        self.streams_list.addItem(QListWidgetItem(f"Default Title: {self.settings.default_source_title}"))
        
        self.btn_extract.setEnabled(False)
        self.btn_translate.setText("Batch Translate && Remux")
        self.btn_translate.setEnabled(True)
        self.overwrite_checkbox.setEnabled(True)
        
        self.batch_info_label.setVisible(True)
        self.batch_info_label.setText("Batch Progress: 0 / " + str(len(mkv_files)))
        self.batch_progress.setVisible(True)
        self.batch_progress.setValue(0)

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
            
            # Check lang match
            if target_lang and target_lang not in lang:
                continue
            
            # Check title match if specified
            if target_title and target_title not in title:
                continue
                
            # If we are here, it matches both criteria
            return idx
            
        return None

    def on_extract(self):
        idx = self.streams_list.currentRow()
        if idx < 0:
            QMessageBox.information(self, "Select", "Select a subtitle track first")
            return
        stream_index = self.subtitle_streams[idx]["index"]
        try:
            out_srt = self._extract_srt(self.mkv_path, stream_index)
            self.log_msg(f"Extracted to {out_srt}")
            # Inform user that extraction is temporary and file may be removed after remux
            self.log_msg("Note: .srt files are temporary and will be removed after remux.")
        except Exception as e:
            QMessageBox.critical(self, "Extract error", str(e))

    def _extract_srt(self, mkv_path: str, stream_index: int) -> str:
        base, _ = os.path.splitext(mkv_path)
        out_srt = base + f".stream{stream_index}.srt"
        
        base_dir = get_base_dir()
        ffmpeg_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        ffmpeg = os.path.join(base_dir, ffmpeg_exe)

        ffmpeg_cmd = ffmpeg if os.path.exists(ffmpeg) else "ffmpeg"
        cmd = [
            ffmpeg_cmd, "-y", "-i", mkv_path, "-map", f"0:{stream_index}", out_srt
        ]
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")
        return out_srt

    def on_translate(self):
        target_lang = self.settings.target_language or "ru"
        # Two modes: SRT-only or MKV stream translate+remux
        if getattr(self, 'input_is_srt', False):
            src_srt = self.src_srt_path
            if not src_srt or not os.path.exists(src_srt):
                QMessageBox.critical(self, "Error", "Subtitle file not found")
                return
        else:
            # Check if batch mode
            if hasattr(self, 'batch_files') and self.batch_files:
                # Batch mode
                self._cancel_flag = threading.Event()
                self.btn_translate.setEnabled(False)
                self.btn_cancel.setEnabled(True)
                self.worker = WorkerThread(self._batch_translate_and_remux, target_lang)
                self.worker.progress.connect(self.progress.setValue)
                self.worker.status.connect(self.log_msg)
                self.worker.batch_info.connect(self._on_batch_info)
                self.worker.request_input.connect(self._on_worker_request_input)
                self.worker.finished_ok.connect(lambda _: self._on_worker_done(success=True))
                self.worker.failed.connect(lambda err: self._on_worker_done(success=False, err=err))
                self.worker.start()
                return

            idx = self.streams_list.currentRow()
            if idx < 0:
                QMessageBox.information(self, "Select", "Select a subtitle track first")
                return
            stream_index = self.subtitle_streams[idx]["index"]
            try:
                src_srt = self._extract_srt(self.mkv_path, stream_index)
            except Exception as e:
                QMessageBox.critical(self, "Extract error", str(e))
                return

        # Check for free disk space before starting (expensive translation)
        try:
            mkv_size = os.path.getsize(self.mkv_path)
            # Estimate needed space: file size + 100MB buffer for subs/overhead
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
                reply = QMessageBox.warning(self, "Insufficient Disk Space", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.No:
                    return
        except Exception as e:
            # If check fails (e.g. permission), just log/warn but don't block
            self.log_msg(f"Warning: Could not check disk space: {e}")

        self._cancel_flag = threading.Event()
        self.btn_translate.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker = WorkerThread(self._translate_and_remux, src_srt, target_lang, mkv_path=self.mkv_path)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.log_msg)
        self.worker.finished_ok.connect(lambda _: self._on_worker_done(success=True))
        self.worker.failed.connect(lambda err: self._on_worker_done(success=False, err=err))
        self.worker.start()

    def _on_batch_info(self, val, text):
        self.batch_progress.setValue(val)
        if text:
            self.batch_info_label.setText(text)

    def _on_worker_request_input(self, req_type, data):
        if req_type == 'select_stream':
            # data is (filename, streams_list)
            fname, streams = data
            self._user_input_result = None
            
            # Show dialog
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Select stream for {fname}")
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel(f"No match found for settings.\nFile: {fname}\nPlease select a subtitle stream:"))
            
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
            
            chk_default = QCheckBox("Update Default Settings (Lang & Title) from selection")
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
                
                # If update defaults requested, we need to extract lang/title
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
            
        elif req_type == 'update_ui_settings':
            # data is (lang, title)
            lang, title = data
            self.def_src_lang_input.setText(lang)
            self.def_src_title_input.setText(title)
            
            # Update main list if in batch mode
            if self.streams_list.count() >= 3:
                # Assuming index 1 and 2 are the default settings as added in on_browse_folder
                # We can't be 100% sure but it's highly likely if we are running.
                # A safer way would be to search or store refs, but this is simple enough.
                item_lang = self.streams_list.item(1)
                item_title = self.streams_list.item(2)
                if item_lang.text().startswith("Default Lang:"):
                    item_lang.setText(f"Default Lang: {lang}")
                if item_title.text().startswith("Default Title:"):
                    item_title.setText(f"Default Title: {title}")

    def _batch_translate_and_remux(self, target_lang: str):
        total_files = len(self.batch_files)
        yield ('batch', 0, f"Batch Progress: 0 / {total_files}")
        yield f"Starting batch processing of {total_files} files..."
        
        for i, fpath in enumerate(self.batch_files, 1):
            if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
                yield "Batch cancelled."
                break
                
            fname = os.path.basename(fpath)
            # Update batch progress
            pct = int(((i - 1) / total_files) * 100)
            yield ('batch', pct, f"Processing file {i} of {total_files}: {fname}")
            
            yield f"[{i}/{total_files}] Processing {fname}..."
            
            # Find stream
            idx = self._find_best_stream(fpath)
            
            streams = []
            if idx is None:
                # Ask user via signal
                try:
                    streams = self._ffprobe_subs(fpath)
                except Exception:
                    pass
                
                if not streams:
                     yield f"Skip {fname}: No subtitle streams found."
                     continue
                     
                self._user_input_event.clear()
                self._user_input_result = None
                yield ('input', 'select_stream', (fname, streams))
                
                # Wait for main thread to set the event
                self._user_input_event.wait()
                
                if self._user_input_result is None:
                     yield f"Skip {fname}: User skipped selection."
                     continue
                     
                idx, update_defaults, new_lang, new_title = self._user_input_result
                
                if update_defaults:
                    # Update settings via main thread context? 
                    # Actually settings are thread-safe(ish) but better to signal updates or Just do it here?
                    # Since we are in worker, accessing self.settings directly might be racy if user uses UI same time. 
                    # But here the user is blocked by modal dialog anyway.
                    # We can update settings object, main thread UI might need refresh.
                    pass 
                    # We will update them - but the UI textboxes won't auto-update unless we signal back.
                    # Let's just update the logical settings for now.
                    self.settings.default_source_lang = new_lang
                    self.settings.default_source_title = new_title
                    try:
                        self.settings.save()
                    except Exception:
                        pass
                    # Yield special tuple to update UI
                    yield ('settings_update', new_lang, new_title)
                    yield f"Updated default settings to Lang={new_lang}, Title={new_title}"

            if idx is None:
                 # Should not happen if logic is correct
                 yield f"Skip {fname}: No stream selected."
                 continue
            
            # IMPORTANT: Update self.subtitle_streams for _translate_and_remux logic to work correct
            # especially for calculating existing_subs_count and reusing titles.
            # We might already have streams from interactive selection or probe.
            if not streams:
                try:
                    streams = self._ffprobe_subs(fpath)
                except Exception:
                    streams = []
            self.subtitle_streams = streams
                
            # Extract
            try:
                yield f"Extracting stream #{idx} from {fname}..."
                src_srt = self._extract_srt(fpath, idx)
            except Exception as e:
                yield f"Error extracting {fname}: {e}"
                continue
                
            # Translate & Remux
            # Reuse _translate_and_remux logic via 'yield from'
            # Note: _translate_and_remux expects to be a generator
            try:
                # We need to wrap it to catch exceptions from it too if any
                for progress_item in self._translate_and_remux(src_srt, target_lang, mkv_path=fpath):
                    # Pass through strings (status updates) but maybe scale or ignore integer progress for batch
                    # Or just yield them to show activity
                    yield progress_item
            except Exception as e:
                yield f"Error processing {fname}: {e}"
            
            # Clean up temp SRT
            if os.path.exists(src_srt) and not getattr(self.settings, 'debug_keep_srt', False):
                 try:
                     os.remove(src_srt)
                 except Exception:
                     pass

        yield ('batch', 100, f"Done. Processed {total_files} files.")
        yield 100
        yield "Batch processing finished."


    def _translate_and_remux(self, src_srt: str, target_lang: str, mkv_path: str = None):
        # Generator yielding progress updates and messages
        if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
            yield "Cancelled."
            return
        yield "Reading SRT..."
        with open(src_srt, "r", encoding="utf-8", errors="ignore") as f:
            srt_text = f.read()
        entries = list(srt.parse(srt_text))
        if not entries:
            raise RuntimeError("No subtitles parsed from SRT")

        window = max(1, int(getattr(self.settings, 'window', 25) or 25))
        overlap = max(0, int(getattr(self.settings, 'overlap', 10) or 10))
        # Implement symmetric half-overlap context. For overlap=10, half=5.
        # We translate extended ranges but only keep the core (non-overlap) part from each batch.
        n = len(entries)
        # Step equals the core window; overlap is used only as symmetric context
        step = max(1, window)
        core_ranges = [(s, min(s + window, n)) for s in range(0, n, step)]
        half = overlap // 2
        # Build translation ranges expanded by half on both sides
        groups = []  # list of (core_start, core_end, trans_start, trans_end)
        for core_start, core_end in core_ranges:
            trans_start = max(0, core_start - half)
            trans_end = min(n, core_end + half)
            groups.append((core_start, core_end, trans_start, trans_end))

        translated_entries = {}
        total_groups = len(groups)
        max_workers = max(1, min(10, int(getattr(self.settings, 'workers', 5) or 5)))

        def translate_group(task_id:int, core_start:int, core_end:int, trans_start:int, trans_end:int):
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
                    return (task_id, core_start, core_end, trans_start, trans_end, 'ok', segs, dbg)
            except Exception:
                pass
            # Try to parse our numbered format
            numbered = {}
            cur_idx = None
            buff = []
            for line in text.replace('\r\n','\n').replace('\r','\n').split('\n'):
                # Preserve blank lines inside content; only strip for header detection
                if line.strip().endswith(':') and line.strip()[:-1].isdigit():
                    if cur_idx is not None:
                        numbered[cur_idx] = '\n'.join(buff)
                    cur_idx = int(line.strip()[:-1])
                    buff = []
                else:
                    buff.append(line)
            if cur_idx is not None:
                numbered[cur_idx] = '\n'.join(buff)
            if numbered:
                # Map back to group order by original indices
                mapped = []
                for orig in group_local:
                    mapped.append(numbered.get(orig.index, ""))
                return (task_id, core_start, core_end, trans_start, trans_end, 'numbered', mapped, dbg)
            # Fallback: treat as plain lines; keep exactly the number of lines as inputs
            contents = [c.strip() for c in text.replace('\r\n','\n').replace('\r','\n').split("\n")]
            contents = [c for c in contents if c != ""]
            # If count mismatches, pad with empty or truncate
            expected = len(group_local)
            if len(contents) < expected:
                contents += [""] * (expected - len(contents))
            elif len(contents) > expected:
                contents = contents[:expected]
            return (task_id, core_start, core_end, trans_start, trans_end, 'fallback', contents, dbg)

        yield f"Submitting {total_groups} groups to {max_workers} workers..."
        completed = 0
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(translate_group, i, core_s, core_e, trans_s, trans_e): (i, core_s, core_e, trans_s, trans_e) for i, (core_s, core_e, trans_s, trans_e) in enumerate(groups, 1)}
            for fut in as_completed(futures):
                if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
                    yield "Cancellation requested. Waiting for running tasks to finish..."
                    break
                i, core_s, core_e, trans_s, trans_e = futures[fut]
                try:
                    res = fut.result()
                    results[i] = res
                    # If fulllog enabled and debug present, emit logs
                    if getattr(self.settings, 'fulllog', False):
                        # Result tuple layout: (task_id, core_start, core_end, trans_start, trans_end, kind, payload, dbg)
                        dbg = res[-1] if isinstance(res, (list, tuple)) and len(res) >= 8 else None
                        if isinstance(dbg, dict):
                            try:
                                req_str = json.dumps({"url": dbg.get("url"), "headers": dbg.get("headers"), "body": dbg.get("body")}, ensure_ascii=False, indent=2)
                                resp_str = json.dumps(dbg.get("response_json"), ensure_ascii=False, indent=2)
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

        # Assemble results in order with overlap rules
        if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
            yield "Cancelled before assembling results."
            return
        for gi, (core_start, core_end, trans_start, trans_end) in enumerate(groups, 1):
            # Unpack result tuple which may include debug info at the end
            res_tuple = results[gi]
            # result: (task_id, core_start, core_end, trans_start, trans_end, kind, payload, [dbg])
            _, r_core_s, r_core_e, r_trans_s, r_trans_e, kind, payload = res_tuple[:7]
            group_local = entries[r_trans_s:r_trans_e]
            # Keep only core range inside this translated batch
            core_rel_start = max(0, r_core_s - r_trans_s)
            core_rel_end = max(core_rel_start, min(len(group_local), r_core_e - r_trans_s))
            if kind == 'ok':
                # Align by order; slice only core indices
                if len(payload) != len(group_local):
                    yield f"[Warning] Model returned {len(payload)} segments, expected {len(group_local)} for translated window indices {group_local[0].index}-{group_local[-1].index}."
                for idx_in_group in range(core_rel_start, core_rel_end):
                    orig = entries[r_trans_s + idx_in_group]
                    seg_content = payload[idx_in_group].content if idx_in_group < len(payload) else ""
                    clean = self._sanitize_content(seg_content)
                    translated_entries[orig.index] = srt.Subtitle(index=orig.index, start=orig.start, end=orig.end, content=clean)
            else:
                if len(payload) != len(group_local):
                    yield f"[Warning] Line count mismatch in translated window {group_local[0].index}-{group_local[-1].index}: got {len(payload)}, expected {len(group_local)}."
                for idx_in_group in range(core_rel_start, core_rel_end):
                    orig = entries[r_trans_s + idx_in_group]
                    text = payload[idx_in_group] if idx_in_group < len(payload) else ""
                    clean = self._sanitize_content(text)
                    translated_entries[orig.index] = srt.Subtitle(index=orig.index, start=orig.start, end=orig.end, content=clean)

        # Rebuild SRT with original timing
        if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
            yield "Cancelled before writing SRT."
            return
        yield "Building translated SRT..."
        # Validate coverage: ensure every original entry has a translation
        if len(translated_entries) != len(entries):
            missing = [e.index for e in entries if e.index not in translated_entries]
            raise RuntimeError(f"Missing translated entries for indices: {missing[:10]}{'...' if len(missing)>10 else ''}")
        # Convert dict to list sorted by original index
        ordered = [translated_entries[idx] for idx in sorted(translated_entries.keys())]
        # Ensure order and reindex to strict sequential indices starting from 1
        # srt.sort_and_reindex returns a generator in some versions; ensure we keep a list
        ordered = list(srt.sort_and_reindex(ordered, start_index=1))
        # Compose with strict defaults
        translated_srt_text = srt.compose(ordered)
        # Compose already creates correct SRT structure (index, timestamps, text, blank line between cues).
        # Validate round-trip parse and entry count
        try:
            parsed_back = list(srt.parse(translated_srt_text))
            if len(parsed_back) != len(ordered):
                raise ValueError(f"SRT validation failed: expected {len(ordered)} entries, got {len(parsed_back)}")
        except Exception as e:
            raise RuntimeError(f"Generated SRT is invalid: {e}")
        # Enforce CRLF endings and final newline for better player compatibility; keep UTF-8 without BOM
        translated_srt_text = translated_srt_text.replace("\r\n", "\n").replace("\r", "\n")
        if not translated_srt_text.endswith("\n"):
            translated_srt_text += "\n"
        translated_srt_text = translated_srt_text.replace("\n", "\r\n")
        base, _ = os.path.splitext(src_srt)
        # Default output name
        out_srt = base + f".{target_lang}.translated.srt"
        # If this is a standalone SRT/STR translation, append model and window to the filename
        if getattr(self, 'input_is_srt', False):
            model = (getattr(self.settings, 'model', '') or '').strip()
            window_sz = int(getattr(self.settings, 'window', 25) or 25)
            # Sanitize model for filesystem (keep alnum, dash, underscore, dot)
            safe_model = ''.join(ch if (ch.isalnum() or ch in ('-', '_', '.')) else '_' for ch in model) or 'model'
            out_srt = base + f".{target_lang}.translated.{safe_model}.w{window_sz}.srt"
        with open(out_srt, "w", encoding="utf-8", newline="") as f:
            f.write(translated_srt_text)

        # If we are translating a standalone SRT/STR file, skip remux and just finish here
        if getattr(self, 'input_is_srt', False) or (self.mkv_path and os.path.splitext(self.mkv_path)[1].lower() not in ('.mkv',)):
            yield 100
            yield f"Done. Output: {out_srt}"
            return
        # Remux into MKV as new subtitle track
        if hasattr(self, '_cancel_flag') and self._cancel_flag.is_set():
            yield "Cancelled before remux."
            return
        # Use passed mkv_path or fallback to self.mkv_path
        remux_src = mkv_path or self.mkv_path
        if not remux_src:
             yield "No MKV path provided for remux."
             return
             
        overwrite = bool(getattr(self.settings, 'overwrite_original', False))
        if overwrite:
            out_mkv = os.path.splitext(remux_src)[0] + ".__tmp_translated__.mkv"
            yield "Remuxing and overwriting the original MKV with translated subtitles..."
        else:
            out_mkv = os.path.splitext(remux_src)[0] + ".translated.mkv"
            yield "Remuxing new MKV with translated subtitles..."
        base_dir = get_base_dir()
        ffmpeg_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        ffmpeg = os.path.join(base_dir, ffmpeg_exe)
        ffmpeg_cmd = ffmpeg if os.path.exists(ffmpeg) else "ffmpeg"
        # Try to reuse source subtitle title for the new translated track
        src_title = None
        try:
            sel_idx = None
            if self.streams_list.currentRow() >= 0:
                sel_idx = self.subtitle_streams[self.streams_list.currentRow()].get("index")
            for st in self.subtitle_streams:
                if sel_idx is not None and st.get("index") == sel_idx:
                    src_title = (st.get("tags") or {}).get("title")
                    break
        except Exception:
            src_title = None
        if not src_title:
            src_title = None

        # Number of existing subtitle streams (0-based index of the new track in the output file)
        existing_subs_count = len(self.subtitle_streams)

        # Infer English value for the MKV lang= tag from the user's language field
        # Check persistent cache first
        if self.settings.cached_source_lang_input == target_lang and self.settings.cached_tag_lang and self.settings.cached_iso3:
            tag_lang = self.settings.cached_tag_lang
            iso3 = self.settings.cached_iso3
        else:
            try:
                tag_lang = self._infer_lang_for_tag(target_lang)
                yield f"Normalized language for MKV tag: '{tag_lang}' (from '{target_lang}')"
            except Exception as _e:
                tag_lang = 'und'
                yield f"[Warning] Could not normalize language for MKV tag, using '{tag_lang}'. Error: {_e}"
    
            # Build Title with normalized language and ISO 639-2 code
            try:
                iso3 = self._infer_iso3(target_lang)
                yield f"ISO 639-2 code inferred: '{iso3}' (from '{target_lang}')"
            except Exception as _e:
                iso3 = 'und'
                yield f"[Warning] Could not infer ISO 639-2 code via chat. Using '{iso3}'. Error: {_e}"
            
            # Update persistent cache
            self.settings.cached_source_lang_input = target_lang
            self.settings.cached_tag_lang = tag_lang
            self.settings.cached_iso3 = iso3
            try:
                self.settings.save()
            except Exception:
                pass

        if not iso3 or not isinstance(iso3, str) or len(iso3) != 3:
            iso3 = 'und'
        if not src_title:
            src_title = f"Translated [{iso3}] ({tag_lang})"
        else:
            src_title = f"{src_title} | Translated [{iso3}] ({tag_lang})"

        # Build ffmpeg command:
        # [inputs] -> [maps] -> [codecs] -> [metadata for the NEW track] -> [output]
        cmd = [
            ffmpeg_cmd, "-y",
            "-i", remux_src,
            "-f", "srt", "-i", out_srt,
            "-map", "0",
            "-map", "1:0",
            "-c", "copy",
            "-max_interleave_delta", "0",
            "-c:s:" + str(existing_subs_count), "srt",
            "-metadata:s:s:" + str(existing_subs_count), f"language={iso3}",
            "-metadata:s:s:" + str(existing_subs_count), f"title={src_title}",
            out_mkv,
        ]

        # Log the command before running
        try:
            import shlex
            yield "FFmpeg command:"
            yield " ".join(shlex.quote(x) for x in cmd)
        except Exception:
            pass

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
        if proc.returncode != 0:
            # Log exit code and stderr so the user can copy from the log window
            yield f"FFmpeg exit code: {proc.returncode}"
            if proc.stderr:
                yield "FFmpeg stderr:"
                # Split into lines to preserve formatting in the log widget
                for line in proc.stderr.splitlines():
                    yield line
            # Even on failure, try to remove temporary SRT files to avoid leftovers
            try:
                if os.path.exists(out_srt):
                    os.remove(out_srt)
                if os.path.exists(src_srt):
                    os.remove(src_srt)
            except Exception:
                pass
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed during remux")

        # After successful remux: if overwrite requested, replace original file
        try:
            if overwrite:
                # Ensure ffmpeg output exists
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

        # Cleanup temporary SRT files after successful remux
        try:
            # Only delete temp files if they were generated from MKV extraction
            if not getattr(self, 'input_is_srt', False):
                if os.path.exists(out_srt):
                    os.remove(out_srt)
                if os.path.exists(src_srt):
                    os.remove(src_srt)
        except Exception:
            # Non-fatal: if we can't remove, just log it
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
            # keep only ascii letters, digits and spaces
            allowed = ''.join(ch for ch in s if (ch.isalnum() and ord(ch) < 128) or ch == ' ')
            # collapse spaces
            parts = [p for p in allowed.split(' ') if p]
            out = ' '.join(parts)
            return out[:30] if out else out
        # Try API first
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
        # Mapping removed by request; rely solely on chat output, then ASCII cleanup as fallback.
        rl = (raw_lang or '').strip().lower()
        # Simple ascii-only fallback
        ascii_only = ''.join(ch for ch in rl if (ch.isalnum() and ord(ch) < 128) or ch == ' ')
        ascii_only = ' '.join([p for p in ascii_only.split(' ') if p])
        return ascii_only[:30] if ascii_only else 'und'


    def _infer_iso3(self, raw_lang: str) -> str:
        """Infer ISO 639-2 code using chat first; fallback to 'und'."""
        try:
            res = self.translator.chat_infer_iso3(raw_lang)
            if isinstance(res, tuple):
                code, dbg = res
            else:
                code, dbg = res, None
            code = (code or '').strip().lower()
            if len(code) == 3 and code.isalpha():
                return code
        except Exception:
            pass
        rl = (raw_lang or '').strip().lower()
        if len(rl) == 3 and rl.isalpha():
            return rl
        return 'und'
