"""Live translation loop for still-downloading MKV files.

Polls the file for growth, re-extracts the chosen subtitle stream
leniently, translates new full windows, and (optionally) tells Kodi to
reload the external SRT after each successful append.

Pure logic: no Qt, no UI. The :class:`subtitle_translator.ui.workers.WorkerThread`
adapts the yielded ``int``/``str`` updates into Qt signals.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import srt

from ..ffmpeg.extract import extract_srt_lenient
from .srt_io import (
    strip_sentinel,
    td_to_hms,
    write_translated_with_sentinel,
)
from .translation_engine import translate_subs


def live_translate_mkv(
    mkv_path: str,
    stream_index: int,
    target_lang: str,
    settings,
    translator,
    kodi_client=None,
    kodi_subtitle_path: Optional[str] = None,
    kodi_playback_started: Optional[threading.Event] = None,
    sanitize: Optional[Callable[[str], str]] = None,
    cancel_flag: Optional[threading.Event] = None,
):
    """Translate subtitles from a still-downloading mkv in a polling loop.

    Generator yielding ``int`` (progress 0-100) and ``str`` (status). On
    every iteration it:
      1. Checks file mtime; warns when it has been unchanged for
         ``live_stable_threshold`` seconds (download likely finished).
      2. Re-extracts the subtitle stream leniently.
      3. Translates the new tail ONLY when there are at least
         ``window + overlap`` untranslated entries. Threshold is mandatory:
         the loop never translates a sub-threshold remainder on its own.
      4. Tells Kodi to reload subtitles after each successful append.

    Loop terminates only via ``cancel_flag``. If mtime is stable and there
    are not enough new lines, the loop keeps polling so the user can
    decide when to stop.
    """
    if cancel_flag is None:
        cancel_flag = threading.Event()
    if sanitize is None:
        sanitize = lambda s: s  # noqa: E731

    poll_interval = max(5, int(getattr(settings, "live_poll_interval", 30) or 30))
    stable_threshold = max(5, int(getattr(settings, "live_stable_threshold", 30) or 30))
    window = max(1, int(getattr(settings, "window", 25) or 25))
    overlap = max(0, int(getattr(settings, "overlap", 10) or 10))
    threshold_count = window + overlap

    base, _ext = os.path.splitext(mkv_path)
    out_srt = base + f".{target_lang}.translated.srt"
    extract_path = base + f".live.stream{stream_index}.srt"

    last_mtime = None
    mtime_stable_for = 0
    last_stable_announced = False
    translated_count = 0

    if os.path.exists(out_srt):
        try:
            with open(out_srt, "r", encoding="utf-8", errors="ignore") as f:
                existing = strip_sentinel(list(srt.parse(f.read())))
            translated_count = len(existing)
        except Exception:
            translated_count = 0
        if translated_count > 0:
            try:
                write_translated_with_sentinel(out_srt, [])
            except Exception:
                pass

    def _sleep_with_cancel(sec):
        for _ in range(int(sec)):
            if cancel_flag.is_set():
                return True
            time.sleep(1)
        return False

    if translated_count > 0:
        yield (
            f"Live mode: resuming from line {translated_count} "
            f"(already in {os.path.basename(out_srt)})."
        )
    else:
        yield "Live mode: started."

    while not cancel_flag.is_set():
        try:
            cur_mtime = os.path.getmtime(mkv_path)
        except OSError as e:
            yield f"File missing: {e}"
            return

        if last_mtime is None or cur_mtime != last_mtime:
            mtime_stable_for = 0
            last_mtime = cur_mtime
            last_stable_announced = False
            yield (
                f"mtime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cur_mtime))}"
            )
        else:
            mtime_stable_for += poll_interval

        is_stable = mtime_stable_for >= stable_threshold
        if is_stable and not last_stable_announced:
            yield (
                f"File unchanged for {mtime_stable_for}s — likely finished "
                "downloading. Waiting for new lines to reach threshold."
            )
            last_stable_announced = True

        srt_path = extract_srt_lenient(mkv_path, stream_index, extract_path)
        if not srt_path:
            yield "Subtitles not yet available, waiting..."
            if _sleep_with_cancel(poll_interval):
                return
            continue

        try:
            with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
                all_subs = list(srt.parse(f.read()))
        except Exception as e:
            yield f"SRT parse error: {e}"
            all_subs = []

        new_subs = all_subs[translated_count:]
        total_input = len(all_subs) or 1

        if translated_count == 0 and len(new_subs) < threshold_count:
            yield (
                f"Accumulated {len(new_subs)}/{threshold_count} new lines, waiting..."
            )
            if _sleep_with_cancel(poll_interval):
                return
            continue

        if is_stable:
            to_translate_count = len(new_subs)
        else:
            if len(new_subs) < window:
                yield (
                    f"Waiting for {window - len(new_subs)} more lines to fill the next "
                    f"full window (have {len(new_subs)})..."
                )
                if _sleep_with_cancel(poll_interval):
                    return
                continue
            to_translate_count = (len(new_subs) // window) * window

        if to_translate_count == 0:
            yield "Nothing to translate, waiting..."
            if _sleep_with_cancel(poll_interval):
                return
            continue

        batch = new_subs[:to_translate_count]
        yield f"Translating {len(batch)} new lines..."
        try:
            translated_chunk = yield from translate_subs(
                entries=batch,
                translator=translator,
                settings=settings,
                target_lang=target_lang,
                sanitize=sanitize,
                cancel_flag=cancel_flag,
                fulllog=bool(getattr(settings, "fulllog", False)),
            )
        except Exception as e:
            yield f"Translate failed for batch: {e}"
            if _sleep_with_cancel(poll_interval):
                return
            continue

        if not translated_chunk:
            if _sleep_with_cancel(poll_interval):
                return
            continue

        new_entries = [
            srt.Subtitle(index=0, start=sub.start, end=sub.end, content=sub.content)
            for sub in translated_chunk
        ]
        write_translated_with_sentinel(out_srt, new_entries)

        translated_count += len(translated_chunk)
        pct = int(100 * translated_count / max(total_input, 1))
        yield max(0, min(100, pct))
        yield f"Wrote {translated_count} lines → {os.path.basename(out_srt)}"

        kodi_active = (
            kodi_client is not None
            and getattr(kodi_client, "host", "")
            and kodi_playback_started is not None
            and kodi_playback_started.is_set()
        )
        if kodi_active:
            sub_path_for_kodi = kodi_subtitle_path or out_srt
            yield (
                f"Pushing subtitles to Kodi ({translated_count} lines, "
                f"path: {sub_path_for_kodi})..."
            )
            push_log = []
            try:
                kodi_client.set_subtitle(
                    sub_path_for_kodi,
                    target_lang=target_lang,
                    enable=True,
                    log_cb=push_log.append,
                )
                for line in push_log:
                    yield line
                yield "Pushed subtitles to Kodi and switched ON."
                last_end_hms = (
                    td_to_hms(translated_chunk[-1].end)
                    if translated_chunk
                    else "00:00:00"
                )
                try:
                    kodi_client.show_notification(
                        "Subtitle Translator",
                        f"Subtitles translated up to {last_end_hms}",
                    )
                except Exception:
                    pass
            except Exception as e:
                for line in push_log:
                    yield line
                yield f"Kodi subtitle push failed: {e}"
                if not kodi_subtitle_path:
                    yield (
                        "Hint: Kodi reads files over the network — a local "
                        "path will not work. Configure Kodi source / Local "
                        "parent on the Kodi tab."
                    )

        if _sleep_with_cancel(poll_interval):
            return

    yield "Live mode: finished."
