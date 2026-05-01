"""Main application window: tab composition, batch translation flow,
Kodi integration, and live/follow mode launchers.

This module imports the actual orchestration logic from ``..core``,
ffmpeg helpers from ``..ffmpeg``, and dialog classes from
``.dialogs``. It is the only Qt entry point with a ``QMainWindow``
subclass; ``__main__`` instantiates and shows it.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import List, Optional

import srt
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.sanitize import sanitize_content
from ..core.translation_engine import translate_subs
from ..core.track_matcher import match_initial_state
from ..ffmpeg.extract import extract_srt
from ..ffmpeg.probe import ffprobe_subs
from ..ffmpeg.remux import remux_drop_streams, remux_with_translated_srt
from ..kodi_client import KodiClient, map_local_to_kodi
from ..models import AppSettings
from ..services import TranslationService
from ..utils import (
    check_ffmpeg_available,
    install_ffmpeg,
)
from .tabs.kodi_tab import build_kodi_tab
from .tabs.main_tab import build_main_tab
from .tabs.settings_tab import build_settings_tab
from .model_picker import (
    add_combo_item,
    apply_custom_model_mode,
    build_model_picker,
    populate_model_combo,
    sync_effective_model,
)
from .dialogs.kodi_browse import KodiBrowseDialog
from .dialogs.kodi_discovery import KodiDiscoveryDialog
from .dialogs.kodi_follow import KodiFollowDialog
from .dialogs.live_download import LiveDownloadDialog
from .dialogs.track_selection import TrackSelectionDialog
from .widgets.elided_label import ElidedLabel
from .widgets.model_price_delegate import (
    MODEL_PRICE_ROLE as _MODEL_PRICE_ROLE,
    ModelPriceDelegate as _ModelPriceDelegate,
)
from .workers import WorkerThread, _ModelFetcherThread


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
        return sanitize_content(text)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

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

        kodi_scroll = QScrollArea()
        kodi_scroll.setWidgetResizable(True)
        kodi_scroll.setFrameShape(QFrame.NoFrame)
        kodi_tab = QWidget()
        kodi_scroll.setWidget(kodi_tab)

        self.tabs.addTab(main_scroll, "Main")
        self.tabs.addTab(settings_scroll, "Settings")
        self.tabs.addTab(kodi_scroll, "Kodi")
        main_layout.addWidget(self.tabs)

        build_kodi_tab(self, kodi_tab)
        build_settings_tab(self, settings_tab)
        build_main_tab(self, main_tab)

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

    def _on_kodi_settings_changed(self):
        """Push Kodi tab inputs into AppSettings and persist."""
        try:
            self.settings.kodi_host = self.kodi_host_input.text().strip()
        except Exception:
            pass
        try:
            port_val = int(self.kodi_port_input.text().strip())
        except Exception:
            port_val = self.settings.kodi_port or 8080
        port_val = max(1, min(65535, port_val))
        self.settings.kodi_port = port_val
        try:
            self.settings.kodi_user = self.kodi_user_input.text().strip()
        except Exception:
            pass
        try:
            self.settings.kodi_password = self.kodi_password_input.text()
        except Exception:
            pass
        try:
            self.settings.kodi_source_path = self.kodi_source_input.text().strip()
        except Exception:
            pass
        try:
            self.settings.local_parent_path = self.local_parent_input.text().strip()
        except Exception:
            pass
        try:
            poll = int(self.live_poll_input.text().strip())
            self.settings.live_poll_interval = max(5, min(600, poll))
        except Exception:
            pass
        try:
            stable = int(self.live_stable_input.text().strip())
            self.settings.live_stable_threshold = max(5, min(600, stable))
        except Exception:
            pass
        try:
            buf = int(self.kodi_follow_buffer_input.text().strip())
            self.settings.kodi_follow_buffer_min = max(1, min(120, buf))
        except Exception:
            pass
        self.settings.save()
        self._refresh_kodi_mapping_preview()

    def _refresh_kodi_mapping_preview(self):
        """Show example of local→kodi mapping with current settings."""
        try:
            local_parent = self.settings.local_parent_path or ""
            kodi_parent = self.settings.kodi_source_path or ""
            if not local_parent or not kodi_parent:
                self.kodi_preview_label.setText(
                    "Mapping preview: (specify both folders)"
                )
                return
            sample = os.path.join(local_parent, "example.mkv")
            mapped = map_local_to_kodi(sample, local_parent, kodi_parent)
            self.kodi_preview_label.setText(f"Preview: {sample}\n  → {mapped}")
        except Exception as e:
            self.kodi_preview_label.setText(f"Mapping preview: {e}")

    def _build_kodi_tab(self, parent):
        build_kodi_tab(self, parent)

    # ── Kodi tab handlers ─────────────────────────────

    def _make_kodi_client(self):
        return KodiClient(
            host=self.settings.kodi_host,
            port=self.settings.kodi_port,
            user=self.settings.kodi_user,
            password=self.settings.kodi_password,
            timeout=5.0,
        )

    def _on_kodi_ping_clicked(self):
        self._on_kodi_settings_changed()
        if not self.settings.kodi_host:
            QMessageBox.warning(self, "Kodi", "Specify a host first.")
            return
        self.kodi_status_label.setText("Status: ⏳ Checking...")
        self.kodi_ping_btn.setEnabled(False)

        client = self._make_kodi_client()

        class _PingThread(QThread):
            done = Signal(bool, str)

            def __init__(self, c):
                super().__init__()
                self._c = c

            def run(self):
                try:
                    ok, reason = self._c.ping_with_reason()
                    if ok:
                        ver = self._c.get_version()
                        self.done.emit(True, ver)
                    else:
                        self.done.emit(False, reason)
                except Exception as e:
                    self.done.emit(False, f"{type(e).__name__}: {e}")

        self._ping_thread = _PingThread(client)

        def on_done(ok, info):
            self.kodi_ping_btn.setEnabled(True)
            if ok:
                self.kodi_status_label.setText(
                    f"Status: ● Connected{(' (v' + info + ')') if info else ''}"
                )
                self.kodi_source_browse_btn.setEnabled(True)
            else:
                hint = ""
                low = (info or "").lower()
                if any(
                    k in low for k in ("unreachable", "timed out", "refused", "errno")
                ):
                    hint = (
                        "  ⚠ On macOS, open System Settings → "
                        "Privacy & Security → Local Network and enable the app."
                    )
                self.kodi_status_label.setText(
                    f"Status: ● Disconnected — {info or 'no response'}{hint}"
                )
                self.kodi_status_label.setWordWrap(True)
                self.kodi_source_browse_btn.setEnabled(False)

        self._ping_thread.done.connect(on_done)
        self._ping_thread.start()

    def _on_kodi_discover_clicked(self):
        dlg = KodiDiscoveryDialog(self, port_hint=self.settings.kodi_port or 8080)
        if dlg.exec() == QDialog.Accepted:
            picked = dlg.selected
            if picked:
                self.kodi_host_input.setText(picked["ip"])
                self.kodi_port_input.setText(str(picked["port"]))
                self._on_kodi_settings_changed()

    def _on_kodi_browse_source_clicked(self):
        client = self._make_kodi_client()
        dlg = KodiBrowseDialog(self, client)
        if dlg.exec() == QDialog.Accepted and dlg.selected_path:
            self.kodi_source_input.setText(dlg.selected_path)
            self._on_kodi_settings_changed()

    def _on_local_parent_browse_clicked(self):
        start = self.settings.local_parent_path or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "Pick the local folder (root corresponding to Kodi source)", start
        )
        if path:
            self.local_parent_input.setText(path)
            self._on_kodi_settings_changed()

    def _build_model_picker(self):
        return build_model_picker(self)

    def _populate_model_combo(self, models, selected):
        populate_model_combo(self, models, selected)

    def _add_combo_item(self, model_id, is_current_marker=False):
        add_combo_item(self, model_id, is_current_marker)

    def _apply_custom_model_mode(self, on: bool):
        apply_custom_model_mode(self, on)

    def _sync_effective_model(self):
        sync_effective_model(self)

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
        from ..pricing import is_text_completion_model

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
        return ffprobe_subs(mkv_path)

    def on_open_live_dialog(self):
        dlg = LiveDownloadDialog(
            self,
            settings=self.settings,
            translator=self.translator,
            sanitize=self._sanitize_content,
        )
        dlg.exec()

    def on_open_kodi_follow_dialog(self):
        dlg = KodiFollowDialog(
            self,
            settings=self.settings,
            translator=self.translator,
            sanitize=self._sanitize_content,
        )
        dlg.exec()

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
        return extract_srt(mkv_path, stream_index, out_srt)

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

        overwrite = bool(getattr(self.settings, "overwrite_original", False))
        if overwrite:
            out_mkv = os.path.splitext(mkv_path)[0] + ".__tmp_translated__.mkv"
            yield f"Remuxing (delete only) and overwriting {os.path.basename(mkv_path)}..."
        else:
            out_mkv = os.path.splitext(mkv_path)[0] + ".translated.mkv"
            yield f"Remuxing (delete only) {os.path.basename(mkv_path)}..."

        yield from remux_drop_streams(mkv_path, streams, delete_indexes, out_mkv)

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

        cancel_flag = getattr(self, "_cancel_flag", None) or threading.Event()
        ordered = yield from translate_subs(
            entries=entries,
            translator=self.translator,
            settings=self.settings,
            target_lang=target_lang,
            sanitize=self._sanitize_content,
            cancel_flag=cancel_flag,
            fulllog=bool(getattr(self.settings, "fulllog", False)),
        )

        if cancel_flag.is_set():
            yield "Cancelled before writing SRT."
            return
        yield "Building translated SRT..."
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

        src_title = None
        if source_stream_index is not None:
            try:
                for st in self.subtitle_streams:
                    if st.get("index") == source_stream_index:
                        src_title = (st.get("tags") or {}).get("title")
                        break
            except Exception:
                src_title = None

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

        try:
            yield from remux_with_translated_srt(
                mkv_path=remux_src,
                srt_path=out_srt,
                streams=self.subtitle_streams,
                delete_indexes=delete_indexes or [],
                iso3=iso3,
                title=src_title,
                out_path=out_mkv,
            )
        except RuntimeError:
            try:
                if os.path.exists(out_srt):
                    os.remove(out_srt)
                if os.path.exists(src_srt):
                    os.remove(src_srt)
            except Exception:
                pass
            raise

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
