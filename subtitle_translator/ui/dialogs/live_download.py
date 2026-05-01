"""``LiveDownloadDialog`` — translate subtitles from a still-downloading
.mkv. Polls file size every N sec, re-extracts subs, translates
incrementally, and (optionally) reloads them on a connected Kodi
instance.
"""

from __future__ import annotations

import os
import threading

import srt
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ...core.kodi_follow import format_kodi_subtitle_state
from ...core.live_loop import live_translate_mkv
from ...core.srt_io import strip_sentinel, td_to_hms
from ...ffmpeg.probe import ffprobe_subs_partial
from ...kodi_client import KodiClient, map_local_to_kodi
from ..workers import WorkerThread


class LiveDownloadDialog(QDialog):
    def __init__(self, parent, settings, translator, sanitize=None):
        super().__init__(parent)
        self.setWindowTitle("Live mode: still-downloading file")
        self.resize(620, 520)

        self._settings = settings
        self._translator = translator
        self._sanitize = sanitize or (lambda s: s)
        self._cancel_flag = threading.Event()
        self._kodi_playback_started = threading.Event()
        self._worker = None
        self._streams = []
        self._kodi_progress_client = None
        self._kodi_progress_thread = None
        self._kodi_progress_timer = None

        v = QVBoxLayout(self)

        f_row = QHBoxLayout()
        self._file_input = QLineEdit()
        self._file_input.setPlaceholderText(
            "Path to a still-downloading .mkv (sequential download enabled)"
        )
        self._file_browse_btn = QPushButton("Browse...")
        f_row.addWidget(QLabel("File:"))
        f_row.addWidget(self._file_input, 1)
        f_row.addWidget(self._file_browse_btn)
        v.addLayout(f_row)

        self._size_label = QLabel("Size: —")
        v.addWidget(self._size_label)

        s_row = QHBoxLayout()
        self._track_combo = QComboBox()
        self._track_refresh_btn = QPushButton("Refresh tracks")
        s_row.addWidget(QLabel("Sub-track:"))
        s_row.addWidget(self._track_combo, 1)
        s_row.addWidget(self._track_refresh_btn)
        v.addLayout(s_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        v.addWidget(self._progress)

        kodi_progress_row = QHBoxLayout()
        self._kodi_progress_label = QLabel("Kodi: not playing")
        self._kodi_progress_label.setStyleSheet("color: #888;")
        self._kodi_progress_label.setWordWrap(True)
        self._kodi_pause_btn = QPushButton("⏯")
        self._kodi_pause_btn.setToolTip("Play / Pause Kodi")
        self._kodi_pause_btn.setFixedWidth(48)
        self._kodi_pause_btn.setEnabled(False)
        kodi_progress_row.addWidget(self._kodi_progress_label, 1)
        kodi_progress_row.addWidget(self._kodi_pause_btn)
        v.addLayout(kodi_progress_row)

        v.addWidget(QLabel("Log:"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        v.addWidget(self._log, 1)

        btn_row = QHBoxLayout()
        self._kodi_play_btn = QPushButton("Play on Kodi")
        self._start_btn = QPushButton("Start translation")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._close_btn = QPushButton("Close")
        btn_row.addWidget(self._kodi_play_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addWidget(self._close_btn)
        v.addLayout(btn_row)

        self._file_browse_btn.clicked.connect(self._on_browse)
        self._track_refresh_btn.clicked.connect(self._on_refresh_tracks)
        self._file_input.textChanged.connect(self._on_file_changed)
        self._kodi_play_btn.clicked.connect(self._on_kodi_play)
        self._kodi_pause_btn.clicked.connect(self._on_kodi_pause)
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)
        self._close_btn.clicked.connect(self.reject)

        self._refresh_kodi_button_state()

        if self._settings.kodi_host:
            try:
                client = KodiClient(
                    host=self._settings.kodi_host,
                    port=self._settings.kodi_port,
                    user=self._settings.kodi_user,
                    password=self._settings.kodi_password,
                )
                self._start_kodi_progress_poller(client)
            except Exception:
                pass

    # ── helpers ───────────────────────────────────────

    def _log_line(self, text):
        self._log.append(text)

    def _on_browse(self):
        start = self._settings.last_dir or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a still-downloading .mkv", start, "MKV (*.mkv);;All (*)"
        )
        if path:
            self._file_input.setText(path)

    def _on_file_changed(self, path):
        path = (path or "").strip()
        if path and os.path.exists(path):
            try:
                size = os.path.getsize(path)
                self._size_label.setText(f"Size: {size:,} bytes")
            except OSError:
                self._size_label.setText("Size: —")
        else:
            self._size_label.setText("Size: —")
        self._refresh_kodi_button_state()

    def _on_refresh_tracks(self):
        path = self._file_input.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Live", "Pick a .mkv file first.")
            return
        try:
            self._streams = ffprobe_subs_partial(path)
        except Exception as e:
            QMessageBox.critical(self, "ffprobe", str(e))
            self._streams = []
            return
        self._track_combo.clear()
        if not self._streams:
            self._track_combo.addItem(
                "(no subtitles — file not downloaded enough yet?)", -1
            )
            return
        for s in self._streams:
            idx = s.get("index")
            tags = s.get("tags") or {}
            lang = tags.get("language", "und")
            title = tags.get("title", "")
            codec = s.get("codec_name", "?")
            label = f"#{idx} [{lang}] {title}  ({codec})".strip()
            self._track_combo.addItem(label, idx)

    def _selected_stream_index(self):
        if self._track_combo.count() == 0:
            return None
        idx = self._track_combo.currentData()
        if idx is None or idx == -1:
            return None
        return int(idx)

    def _refresh_kodi_button_state(self):
        ok, why = self._can_play_in_kodi()
        self._kodi_play_btn.setEnabled(ok)
        self._kodi_play_btn.setToolTip("" if ok else why)

    def _can_play_in_kodi(self):
        if not self._settings.kodi_host:
            return False, "Kodi is not configured (Kodi tab)."
        if not self._settings.local_parent_path:
            return False, "Local parent folder is not set."
        if not self._settings.kodi_source_path:
            return False, "Kodi source path is not set."
        path = self._file_input.text().strip()
        if not path:
            return False, "Pick a file first."
        try:
            map_local_to_kodi(
                path,
                self._settings.local_parent_path,
                self._settings.kodi_source_path,
            )
        except Exception as e:
            return False, str(e)
        return True, ""

    # ── actions ───────────────────────────────────────

    def _on_kodi_play(self):
        ok, why = self._can_play_in_kodi()
        if not ok:
            QMessageBox.warning(self, "Kodi", why)
            return
        path = self._file_input.text().strip()
        try:
            kodi_path = map_local_to_kodi(
                path,
                self._settings.local_parent_path,
                self._settings.kodi_source_path,
            )
        except Exception as e:
            QMessageBox.critical(self, "Kodi", str(e))
            return
        client = KodiClient(
            host=self._settings.kodi_host,
            port=self._settings.kodi_port,
            user=self._settings.kodi_user,
            password=self._settings.kodi_password,
        )
        try:
            client.play_file(kodi_path)
            self._log_line(f"Kodi: started {kodi_path}")
        except Exception as e:
            QMessageBox.critical(self, "Kodi", str(e))
            return

        self._kodi_playback_started.set()
        self._kodi_pause_btn.setEnabled(True)
        self._start_kodi_progress_poller(client)

        target_lang = self._settings.target_language or "ru"
        base, _ = os.path.splitext(path)
        local_srt = base + f".{target_lang}.translated.srt"
        if not os.path.exists(local_srt):
            self._log_line(
                "(Translation not created yet — will attach after first batch.)"
            )
            return

        try:
            kodi_srt = map_local_to_kodi(
                local_srt,
                self._settings.local_parent_path,
                self._settings.kodi_source_path,
            )
        except Exception as e:
            self._log_line(f"⚠ Could not map SRT for Kodi: {e}")
            return

        attached = False
        last_err = "no attempt made"
        push_log = []
        for _ in range(8):
            try:
                client.set_subtitle(
                    kodi_srt,
                    target_lang=self._settings.target_language,
                    enable=True,
                    log_cb=push_log.append,
                )
                attached = True
                break
            except Exception as e:
                last_err = str(e)
                threading.Event().wait(0.5)
        for line in push_log:
            self._log_line(line)
        if attached:
            self._log_line(f"Pushed subtitles to Kodi and switched ON ({kodi_srt})")
        else:
            self._log_line(f"⚠ Kodi rejected subtitle push: {last_err}")

    def _on_start(self):
        path = self._file_input.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Live", "Pick a file first.")
            return
        idx = self._selected_stream_index()
        if idx is None:
            QMessageBox.warning(self, "Live", "Pick a sub-track first.")
            return

        target_lang = self._settings.target_language or "ru"
        kodi_client = None
        if self._settings.kodi_host:
            kodi_client = KodiClient(
                host=self._settings.kodi_host,
                port=self._settings.kodi_port,
                user=self._settings.kodi_user,
                password=self._settings.kodi_password,
            )

        kodi_subtitle_path = None
        if (
            kodi_client is not None
            and self._settings.local_parent_path
            and self._settings.kodi_source_path
        ):
            base, _ = os.path.splitext(path)
            local_srt = base + f".{target_lang}.translated.srt"
            try:
                kodi_subtitle_path = map_local_to_kodi(
                    local_srt,
                    self._settings.local_parent_path,
                    self._settings.kodi_source_path,
                )
                self._log_line(f"Kodi subtitle path: {kodi_subtitle_path}")
            except Exception as e:
                self._log_line(f"⚠ Could not map SRT for Kodi: {e}")

        self._cancel_flag.clear()
        self._progress.setValue(0)
        self._log_line("──── Start ────")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._worker = WorkerThread(
            live_translate_mkv,
            mkv_path=path,
            stream_index=idx,
            target_lang=target_lang,
            settings=self._settings,
            translator=self._translator,
            kodi_client=kodi_client,
            kodi_subtitle_path=kodi_subtitle_path,
            kodi_playback_started=self._kodi_playback_started,
            sanitize=self._sanitize,
            cancel_flag=self._cancel_flag,
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.status.connect(self._log_line)
        self._worker.finished_ok.connect(lambda *_: self._on_finished(True))
        self._worker.failed.connect(lambda msg: self._on_finished(False, msg))
        self._worker.start()

    def _on_stop(self):
        self._cancel_flag.set()
        self._log_line("[Stop] Cancellation requested...")

    def _on_kodi_pause(self):
        client = self._kodi_progress_client
        if client is None:
            QMessageBox.warning(self, "Kodi", "Start playback first.")
            return
        try:
            speed = client.play_pause()
        except Exception as e:
            self._log_line(f"⚠ Play/Pause failed: {e}")
            return
        if speed is None:
            self._log_line("⚠ Play/Pause: no active player.")
        else:
            self._log_line("Kodi: " + ("▶ playing" if speed else "⏸ paused"))
        self._tick_kodi_progress()

    # ── Kodi progress poller ──────────────────────────

    def _start_kodi_progress_poller(self, client):
        self._kodi_progress_client = client
        if self._kodi_progress_timer is not None:
            try:
                self._kodi_progress_timer.stop()
            except Exception:
                pass
        self._kodi_progress_timer = QTimer(self)
        self._kodi_progress_timer.setInterval(1000)
        self._kodi_progress_timer.timeout.connect(self._tick_kodi_progress)
        self._kodi_progress_timer.start()
        self._tick_kodi_progress()

    def _stop_kodi_progress_poller(self):
        if self._kodi_progress_timer is not None:
            try:
                self._kodi_progress_timer.stop()
            except Exception:
                pass
            self._kodi_progress_timer = None
        if self._kodi_progress_thread is not None:
            try:
                self._kodi_progress_thread.wait(1500)
            except Exception:
                pass
            self._kodi_progress_thread = None

    def _last_translated_timestamp(self):
        path = self._file_input.text().strip()
        if not path:
            return None
        target_lang = self._settings.target_language or "ru"
        base, _ = os.path.splitext(path)
        srt_path = base + f".{target_lang}.translated.srt"
        if not os.path.exists(srt_path):
            return None
        try:
            with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
                entries = strip_sentinel(list(srt.parse(f.read())))
            if not entries:
                return None
            return td_to_hms(entries[-1].end)
        except Exception:
            return None

    def _tick_kodi_progress(self):
        client = self._kodi_progress_client
        if client is None:
            return
        if (
            self._kodi_progress_thread is not None
            and self._kodi_progress_thread.isRunning()
        ):
            return

        class _ProgressFetch(QThread):
            done = Signal(object)

            def __init__(self, c):
                super().__init__()
                self._c = c

            def run(self):
                try:
                    self.done.emit(self._c.get_player_progress())
                except Exception as e:
                    self.done.emit((None, f"{type(e).__name__}: {e}"))

        self._kodi_progress_thread = _ProgressFetch(client)
        self._kodi_progress_thread.done.connect(self._on_kodi_progress_done)
        self._kodi_progress_thread.start()

    def _on_kodi_progress_done(self, payload):
        last_sub = self._last_translated_timestamp() or "—"

        result, error = (None, None)
        if isinstance(payload, tuple) and len(payload) == 2:
            result, error = payload

        if error:
            short = error if len(error) < 120 else error[:117] + "..."
            self._kodi_progress_label.setText(
                f"Kodi: ⚠ connection lost — {short}  "
                f"|  Translated up to: {last_sub}"
            )
            self._kodi_progress_label.setStyleSheet("color: #c44;")
            return

        if not result:
            self._kodi_progress_label.setText(
                f"Kodi: not playing  |  Translated up to: {last_sub}"
            )
            self._kodi_progress_label.setStyleSheet("color: #888;")
            return

        def fmt(t):
            if not t:
                return "00:00:00"
            return (
                f"{int(t.get('hours', 0)):02d}:"
                f"{int(t.get('minutes', 0)):02d}:"
                f"{int(t.get('seconds', 0)):02d}"
            )

        cur = fmt(result.get("time"))
        total = fmt(result.get("totaltime"))
        pct = result.get("percentage") or 0
        speed = result.get("speed", 1)
        playing = "▶" if speed else "⏸"

        item = result.get("_item") or {}
        kodi_file = item.get("file") or ""
        kodi_basename = kodi_file.rstrip("/").rsplit("/", 1)[-1] if kodi_file else ""

        local_path = self._file_input.text().strip()
        local_base = os.path.basename(local_path) if local_path else ""
        if kodi_basename and local_base and kodi_basename.lower() == local_base.lower():
            if not self._kodi_playback_started.is_set():
                self._kodi_playback_started.set()
                self._log_line(
                    f"Kodi already playing {kodi_basename} — auto-attach enabled."
                )
            self._kodi_pause_btn.setEnabled(True)
        else:
            if self._kodi_playback_started.is_set():
                self._kodi_playback_started.clear()
                self._log_line(
                    "Kodi is playing a different file — auto-attach disabled."
                )

        if kodi_basename:
            file_line = f"\nPlaying: {kodi_basename}"
        elif item.get("title"):
            file_line = f"\nPlaying: {item['title']}"
        else:
            file_line = ""

        sub_state = format_kodi_subtitle_state(result)
        self._kodi_progress_label.setStyleSheet("")
        self._kodi_progress_label.setText(
            f"Kodi: {playing} {cur} / {total} ({pct:.1f}%)  "
            f"|  Translated up to: {last_sub}{file_line}\n{sub_state}"
        )

    def _on_finished(self, ok, msg=""):
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if ok:
            self._log_line("──── Done ────")
        else:
            self._log_line(f"[Error] {msg}")

    def reject(self):
        self._stop_kodi_progress_poller()
        if self._worker is not None and self._worker.isRunning():
            self._cancel_flag.set()
            self._worker.wait(2000)
        super().reject()
