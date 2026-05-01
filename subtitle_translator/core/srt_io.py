"""SRT helpers: timecode formatting and sentinel marker management.

The sentinel is a placeholder subtitle appended after the last translated
entry while a movie is still downloading or being followed. It tells the
viewer (in CAPS) that translation has not caught up yet so they should
pause. Removed and re-appended on every new batch so it always sits at
the tail of the SRT.
"""

from __future__ import annotations

import os
from datetime import timedelta

import srt


SENTINEL_TEXT = "SUBTITLES NOT TRANSLATED YET — MOVIE STILL DOWNLOADING — PLEASE PAUSE"


def td_to_hms(td) -> str:
    """Format a ``datetime.timedelta`` (or ``None``) as ``HH:MM:SS``."""
    if td is None:
        return "00:00:00"
    s = int(td.total_seconds())
    return td_to_hms_secs(s)


def td_to_hms_secs(s: int) -> str:
    """Format an integer second count as ``HH:MM:SS``."""
    s = int(s)
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def is_sentinel(sub) -> bool:
    return getattr(sub, "content", "").strip() == SENTINEL_TEXT


def strip_sentinel(entries):
    """Drop trailing sentinel entry if present."""
    if entries and is_sentinel(entries[-1]):
        return entries[:-1]
    return entries


def make_sentinel(after_end):
    """Build the sentinel sub starting right after ``after_end`` and lasting
    long enough to cover the rest of any reasonable movie."""
    start = after_end + timedelta(milliseconds=1)
    end = after_end + timedelta(hours=4)
    return srt.Subtitle(index=0, start=start, end=end, content=SENTINEL_TEXT)


def write_translated_with_sentinel(out_srt, new_entries):
    """Read ``out_srt``, strip any trailing sentinel, append ``new_entries``,
    re-append a fresh sentinel, re-index, and write the whole file.

    ``new_entries`` is a list of ``srt.Subtitle`` whose ``index`` will be
    overwritten — pass them with arbitrary indexes (e.g. 0).
    """
    existing = []
    if os.path.exists(out_srt):
        try:
            with open(out_srt, "r", encoding="utf-8", errors="ignore") as f:
                existing = list(srt.parse(f.read()))
        except Exception:
            existing = []
    existing = strip_sentinel(existing)
    all_entries = list(existing) + list(new_entries)
    if all_entries:
        last_end = all_entries[-1].end
        all_entries.append(make_sentinel(last_end))
    for i, e in enumerate(all_entries, 1):
        e.index = i
    text = srt.compose(all_entries).replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    text = text.replace("\n", "\r\n")
    with open(out_srt, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# Legacy private aliases — preserved for any in-tree callers that haven't
# been migrated yet. New code should use the public names above.
_td_to_hms = td_to_hms
_td_to_hms_secs = td_to_hms_secs
_is_sentinel = is_sentinel
_strip_sentinel = strip_sentinel
_make_sentinel = make_sentinel
_write_translated_with_sentinel = write_translated_with_sentinel
