"""Kodi-follow translation loop.

Watches an active Kodi player; while it plays a movie that lacks the
target-language subtitle, keeps a translated SRT roughly
``kodi_follow_buffer_min`` minutes ahead of playback. Survives a
still-downloading source file (mtime stability + window-only batching,
same as :mod:`subtitle_translator.core.live_loop`).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

import srt

from ..ffmpeg.extract import extract_srt_lenient
from ..ffmpeg.probe import ffprobe_subs_partial
from ..kodi_client import map_kodi_to_local, map_local_to_kodi
from .srt_io import (
    strip_sentinel,
    td_to_hms,
    write_translated_with_sentinel,
)
from .track_matcher import pick_source_subtitle_stream
from .translation_engine import translate_subs


def kodi_time_to_sec(t) -> int:
    if not t:
        return 0
    return (
        int(t.get("hours", 0)) * 3600
        + int(t.get("minutes", 0)) * 60
        + int(t.get("seconds", 0))
    )


def format_kodi_subtitle_state(result: dict) -> str:
    """One-line summary of Kodi's currently selected subtitle and on/off state.

    Pulls ``subtitleenabled`` and ``currentsubtitle`` from a
    ``Player.GetProperties`` result. Returns something like
    ``"Subs: ON · ger · Movie.ger.translated.srt"`` or ``"Subs: OFF"``.
    """
    enabled = bool(result.get("subtitleenabled"))
    cur = result.get("currentsubtitle") or {}
    state = "ON" if enabled else "OFF"
    lang = (cur.get("language") or "").strip()
    name = (cur.get("name") or "").strip()
    parts = []
    if lang:
        parts.append(lang)
    if name and name != lang:
        if len(name) > 50:
            name = "…" + name[-49:]
        parts.append(name)
    if not parts:
        return f"Subs: {state}"
    return f"Subs: {state} · " + " · ".join(parts)


def kodi_follow_translate(
    settings,
    translator,
    kodi_client,
    target_lang: str,
    sanitize: Optional[Callable[[str], str]] = None,
    cancel_flag: Optional[threading.Event] = None,
):
    """Watch Kodi; while it plays a movie that lacks the target-language
    subtitle, keep its translated SRT roughly ``kodi_follow_buffer_min``
    minutes ahead of playback.

    Generator yields ``int`` (progress 0-100) and ``str`` (status).
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
    buffer_sec = max(1, int(getattr(settings, "kodi_follow_buffer_min", 10) or 10)) * 60

    def _sleep(sec):
        for _ in range(int(sec)):
            if cancel_flag.is_set():
                return True
            time.sleep(1)
        return False

    last_kodi_file = None
    state = None

    yield "Following Kodi: started."

    while not cancel_flag.is_set():
        progress, error = kodi_client.get_player_progress()

        if error:
            yield f"Kodi connection error: {error}"
            if _sleep(poll_interval):
                return
            continue

        if progress is None:
            yield "Kodi: no active video player. Waiting..."
            last_kodi_file = None
            state = None
            if _sleep(poll_interval):
                return
            continue

        item = progress.get("_item") or {}
        kodi_file = item.get("file") or ""
        if not kodi_file:
            yield "Kodi: playing item has no file path. Waiting..."
            if _sleep(poll_interval):
                return
            continue

        if kodi_file != last_kodi_file:
            last_kodi_file = kodi_file
            state = None
            yield f"Kodi now playing: {os.path.basename(kodi_file)}"

        if state is None:
            try:
                local_path = map_kodi_to_local(
                    kodi_file,
                    settings.kodi_source_path,
                    settings.local_parent_path,
                )
            except Exception as e:
                yield f"Path mapping failed: {e}"
                if _sleep(poll_interval):
                    return
                continue

            if not os.path.exists(local_path):
                yield f"Local file not found yet: {local_path}"
                if _sleep(poll_interval):
                    return
                continue

            try:
                streams = ffprobe_subs_partial(local_path)
            except Exception as e:
                yield f"ffprobe failed: {e}"
                if _sleep(poll_interval):
                    return
                continue

            target_l = (target_lang or "").lower().strip()
            embedded_match = None
            for s in streams:
                tags = s.get("tags") or {}
                lang = (tags.get("language") or "").lower().strip()
                if lang and (lang.startswith(target_l) or target_l.startswith(lang)):
                    embedded_match = s
                    break
            if embedded_match is not None:
                idx = embedded_match.get("index")
                yield (
                    f"Target language ({target_lang}) found embedded in mkv "
                    f"(stream #{idx}). Skipping translation for this file."
                )
                switch_log = []
                try:
                    switched = kodi_client.enable_subtitle_by_lang(
                        target_lang, log_cb=switch_log.append
                    )
                except Exception as e:
                    switched = False
                    yield f"Kodi switch failed: {e}"
                for line in switch_log:
                    yield line
                if switched:
                    yield "Switched Kodi to existing target-language subtitle."
                state = {"skip": True, "local": local_path}
                if _sleep(poll_interval):
                    return
                continue

            if not streams:
                yield (
                    "No subtitle streams visible yet (file may still be "
                    "downloading)."
                )
                if _sleep(poll_interval):
                    return
                continue

            stream_index = pick_source_subtitle_stream(streams, target_lang)
            if stream_index is None:
                yield "No usable source subtitle (only target / ASS streams)."
                if _sleep(poll_interval):
                    return
                continue

            base, _ = os.path.splitext(local_path)
            out_srt = base + f".{target_lang}.translated.srt"
            extract_path = base + f".live.stream{stream_index}.srt"

            translated_count = 0
            existing_last_end_hms = "00:00:00"
            if os.path.exists(out_srt):
                try:
                    with open(out_srt, "r", encoding="utf-8", errors="ignore") as f:
                        existing = strip_sentinel(list(srt.parse(f.read())))
                    translated_count = len(existing)
                    if existing:
                        existing_last_end_hms = td_to_hms(existing[-1].end)
                except Exception:
                    translated_count = 0
                if translated_count > 0:
                    try:
                        write_translated_with_sentinel(out_srt, [])
                    except Exception:
                        pass

            try:
                kodi_sub_path = map_local_to_kodi(
                    out_srt,
                    settings.local_parent_path,
                    settings.kodi_source_path,
                )
            except Exception:
                kodi_sub_path = None

            state = {
                "skip": False,
                "local": local_path,
                "stream_index": stream_index,
                "out_srt": out_srt,
                "extract_path": extract_path,
                "kodi_sub_path": kodi_sub_path,
                "last_mtime": None,
                "mtime_stable_for": 0,
                "translated_count": translated_count,
            }
            yield (
                f"Tracking {os.path.basename(local_path)} stream #{stream_index} "
                f"(resuming from {translated_count} lines)"
            )

            if translated_count > 0 and kodi_sub_path and os.path.exists(out_srt):
                yield (
                    f"Pushing existing subtitles to Kodi "
                    f"({translated_count} lines, path: {kodi_sub_path})..."
                )
                push_log = []
                try:
                    kodi_client.set_subtitle(
                        kodi_sub_path,
                        target_lang=target_lang,
                        enable=True,
                        log_cb=push_log.append,
                    )
                    for line in push_log:
                        yield line
                    yield "Pushed subtitles to Kodi and switched ON."
                    try:
                        kodi_client.show_notification(
                            "Subtitle Translator",
                            f"Subtitles translated up to {existing_last_end_hms}",
                        )
                    except Exception:
                        pass
                except Exception as e:
                    for line in push_log:
                        yield line
                    yield f"Kodi push failed: {e}"

        if state.get("skip"):
            if _sleep(poll_interval):
                return
            continue

        try:
            cur_mtime = os.path.getmtime(state["local"])
        except OSError as e:
            yield f"Local file disappeared: {e}"
            state = None
            if _sleep(poll_interval):
                return
            continue

        if state["last_mtime"] is None or cur_mtime != state["last_mtime"]:
            state["mtime_stable_for"] = 0
            state["last_mtime"] = cur_mtime
        else:
            state["mtime_stable_for"] += poll_interval
        is_stable = state["mtime_stable_for"] >= stable_threshold

        srt_path = extract_srt_lenient(
            state["local"], state["stream_index"], state["extract_path"]
        )
        if not srt_path:
            yield "Subtitles not yet available, waiting..."
            if _sleep(poll_interval):
                return
            continue

        try:
            with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
                all_subs = list(srt.parse(f.read()))
        except Exception as e:
            yield f"SRT parse error: {e}"
            all_subs = []

        new_subs = all_subs[state["translated_count"] :]

        if state["translated_count"] == 0 and len(new_subs) < threshold_count:
            yield f"Accumulated {len(new_subs)}/{threshold_count} lines, waiting..."
            if _sleep(poll_interval):
                return
            continue

        cur_pos_sec = kodi_time_to_sec(progress.get("time"))
        if state["translated_count"] > 0:
            already_until_sec = int(
                all_subs[state["translated_count"] - 1].end.total_seconds()
            )
        else:
            already_until_sec = 0
        ahead_sec = already_until_sec - cur_pos_sec
        if ahead_sec >= buffer_sec:
            mins_ahead = ahead_sec // 60
            yield (
                f"Buffer ok: translated {mins_ahead} min ahead of playback. "
                "Waiting..."
            )
            if _sleep(poll_interval):
                return
            continue

        if is_stable:
            to_translate_count = len(new_subs)
        else:
            if len(new_subs) < window:
                yield (
                    f"Need {window - len(new_subs)} more lines for a full "
                    "window, waiting..."
                )
                if _sleep(poll_interval):
                    return
                continue
            to_translate_count = (len(new_subs) // window) * window

        if to_translate_count == 0:
            if _sleep(poll_interval):
                return
            continue

        batch = new_subs[:to_translate_count]
        yield (
            f"Translating {len(batch)} lines "
            f"(playback {cur_pos_sec // 60}:{cur_pos_sec % 60:02d}, "
            f"buffered {ahead_sec // 60} min ahead)..."
        )
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
            yield f"Translate failed: {e}"
            if _sleep(poll_interval):
                return
            continue

        if not translated_chunk:
            if _sleep(poll_interval):
                return
            continue

        new_entries = [
            srt.Subtitle(index=0, start=sub.start, end=sub.end, content=sub.content)
            for sub in translated_chunk
        ]
        write_translated_with_sentinel(state["out_srt"], new_entries)

        state["translated_count"] += len(translated_chunk)
        total_input = len(all_subs) or 1
        pct = int(100 * state["translated_count"] / max(total_input, 1))
        yield max(0, min(100, pct))
        yield (
            f"Wrote {state['translated_count']} lines → "
            f"{os.path.basename(state['out_srt'])}"
        )

        if state["kodi_sub_path"]:
            yield (
                f"Pushing subtitles to Kodi ({state['translated_count']} lines, "
                f"path: {state['kodi_sub_path']})..."
            )
            push_log = []
            try:
                kodi_client.set_subtitle(
                    state["kodi_sub_path"],
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
                yield f"Kodi push failed: {e}"

        if _sleep(poll_interval):
            return

    yield "Following Kodi: finished."
