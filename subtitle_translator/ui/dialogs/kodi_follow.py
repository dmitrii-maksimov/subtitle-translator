"""``KodiFollowDialog`` — auto-translate mode driven by Kodi.

Watches the active Kodi player; whenever Kodi plays a movie that does
not yet have the target-language subtitle, this dialog runs the
:func:`subtitle_translator.core.kodi_follow.kodi_follow_translate`
loop, which translates the source subtitle in batches paced by playback
position.
"""

from __future__ import annotations

import os
import threading

import srt
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ...core.kodi_follow import format_kodi_subtitle_state, kodi_follow_translate
from ...core.srt_io import strip_sentinel, td_to_hms
from ...kodi_client import KodiClient, map_kodi_to_local
from ..workers import WorkerThread


class KodiFollowDialog(QDialog):
    def __init__(self, parent, settings, translator, sanitize=None):
        super().__init__(parent)
        self.setWindowTitle("Following Kodi")
        self.resize(640, 540)

        self._settings = settings
        self._translator = translator
        self._sanitize = sanitize or (lambda s: s)
        self._cancel_flag = threading.Event()
        self._worker = None

        v = QVBoxLayout(self)

        v.addWidget(QLabel(f"<b>Target language:</b> {settings.target_language}"))
        self._buffer_label = QLabel(
            f"Buffer ahead of playback: {settings.kodi_follow_buffer_min} min"
        )
        v.addWidget(self._buffer_label)

        status_row = QHBoxLayout()
        self._kodi_status_label = QLabel("Kodi: idle")
        self._kodi_status_label.setWordWrap(True)
        self._kodi_pause_btn = QPushButton("⏯")
        self._kodi_pause_btn.setToolTip("Play / Pause Kodi")
        self._kodi_pause_btn.setFixedWidth(48)
        status_row.addWidget(self._kodi_status_label, 1)
        status_row.addWidget(self._kodi_pause_btn)
        v.addLayout(status_row)
        self._kodi_pause_btn.clicked.connect(self._on_kodi_pause)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        v.addWidget(self._progress)

        v.addWidget(QLabel("Log:"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        v.addWidget(self._log, 1)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start watching")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._close_btn = QPushButton("Close")
        btn_row.addStretch(1)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addWidget(self._close_btn)
        v.addLayout(btn_row)

        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)
        self._close_btn.clicked.connect(self.reject)

        self._status_client = None
        self._status_timer = None
        self._status_thread = None
        if settings.kodi_host:
            try:
                self._status_client = KodiClient(
                    host=settings.kodi_host,
                    port=settings.kodi_port,
                    user=settings.kodi_user,
                    password=settings.kodi_password,
                )
                self._start_status_poller()
            except Exception:
                pass

    def _log_line(self, text):
        self._log.append(text)

    def _on_kodi_pause(self):
        client = self._status_client
        if client is None:
            QMessageBox.warning(self, "Kodi", "Kodi host is not configured.")
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
        self._tick_status()

    # ── status header poller ───────────────────────────

    def _start_status_poller(self):
        if self._status_timer is not None:
            try:
                self._status_timer.stop()
            except Exception:
                pass
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._tick_status)
        self._status_timer.start()
        self._tick_status()

    def _stop_status_poller(self):
        if self._status_timer is not None:
            try:
                self._status_timer.stop()
            except Exception:
                pass
            self._status_timer = None
        if self._status_thread is not None:
            try:
                self._status_thread.wait(1500)
            except Exception:
                pass
            self._status_thread = None

    def _tick_status(self):
        client = self._status_client
        if client is None:
            return
        if self._status_thread is not None and self._status_thread.isRunning():
            return

        class _Fetch(QThread):
            done = Signal(object)

            def __init__(self, c):
                super().__init__()
                self._c = c

            def run(self):
                try:
                    self.done.emit(self._c.get_player_progress())
                except Exception as e:
                    self.done.emit((None, f"{type(e).__name__}: {e}"))

        self._status_thread = _Fetch(client)
        self._status_thread.done.connect(self._on_status_done)
        self._status_thread.start()

    def _on_status_done(self, payload):
        result, error = (None, None)
        if isinstance(payload, tuple) and len(payload) == 2:
            result, error = payload
        if error:
            short = error if len(error) < 120 else error[:117] + "..."
            self._kodi_status_label.setStyleSheet("color: #c44;")
            self._kodi_status_label.setText(f"Kodi: ⚠ {short}")
            return
        if not result:
            self._kodi_status_label.setStyleSheet("color: #888;")
            self._kodi_status_label.setText("Kodi: no active video player")
            return

        def fmt(t):
            if not t:
                return "00:00:00"
            return (
                f"{int(t.get('hours', 0)):02d}:"
                f"{int(t.get('minutes', 0)):02d}:"
                f"{int(t.get('seconds', 0)):02d}"
            )

        item = result.get("_item") or {}
        kodi_file = item.get("file") or ""
        base = kodi_file.rstrip("/").rsplit("/", 1)[-1] if kodi_file else "?"
        cur = fmt(result.get("time"))
        total = fmt(result.get("totaltime"))
        pct = result.get("percentage") or 0
        speed = result.get("speed", 1)
        playing = "▶" if speed else "⏸"

        last_sub = self._last_translated_timestamp_for(kodi_file) or "—"

        sub_state = format_kodi_subtitle_state(result)
        self._kodi_status_label.setStyleSheet("")
        self._kodi_status_label.setText(
            f"Kodi: {playing} {cur} / {total} ({pct:.1f}%)  "
            f"|  Translated up to: {last_sub}\nPlaying: {base}\n{sub_state}"
        )

    def _last_translated_timestamp_for(self, kodi_file):
        if not kodi_file:
            return None
        try:
            local = map_kodi_to_local(
                kodi_file,
                self._settings.kodi_source_path,
                self._settings.local_parent_path,
            )
        except Exception:
            return None
        target_lang = self._settings.target_language or "ru"
        base, _ = os.path.splitext(local)
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

    # ── control ────────────────────────────────────────

    def _on_start(self):
        if not self._settings.kodi_host:
            QMessageBox.warning(self, "Kodi", "Configure Kodi host first.")
            return
        if not (self._settings.kodi_source_path and self._settings.local_parent_path):
            QMessageBox.warning(
                self,
                "Kodi",
                "Set Kodi source path and Local parent folder on the Kodi tab.",
            )
            return

        client = KodiClient(
            host=self._settings.kodi_host,
            port=self._settings.kodi_port,
            user=self._settings.kodi_user,
            password=self._settings.kodi_password,
        )

        self._cancel_flag.clear()
        self._progress.setValue(0)
        self._log_line("──── Watching Kodi ────")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._worker = WorkerThread(
            kodi_follow_translate,
            settings=self._settings,
            translator=self._translator,
            kodi_client=client,
            target_lang=self._settings.target_language or "ru",
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

    def _on_finished(self, ok, msg=""):
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if ok:
            self._log_line("──── Stopped ────")
        else:
            self._log_line(f"[Error] {msg}")

    def reject(self):
        self._stop_status_poller()
        if self._worker is not None and self._worker.isRunning():
            self._cancel_flag.set()
            self._worker.wait(2000)
        super().reject()
