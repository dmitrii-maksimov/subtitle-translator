"""ffmpeg remux helpers.

Two flavors:
* :func:`remux_drop_streams` — copy an MKV while excluding the given
  subtitle streams. No new SRT track.
* :func:`remux_with_translated_srt` — same idea, plus mux a freshly
  translated SRT as a new subtitle track with language and title
  metadata.

Both yield ``str`` log lines (FFmpeg command, stderr) and return the
output path. Raise ``RuntimeError`` on ffmpeg failure.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Iterable, Iterator, List

from ..utils import find_tool, make_startupinfo


def _run_ffmpeg(cmd) -> Iterator[str]:
    """Run ffmpeg, yielding the command line and (on failure) stderr.

    Raises ``RuntimeError`` if ffmpeg exits non-zero.
    """
    yield "FFmpeg command:"
    yield " ".join(shlex.quote(x) for x in cmd)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
    )
    if proc.returncode != 0:
        yield f"FFmpeg exit code: {proc.returncode}"
        if proc.stderr:
            yield "FFmpeg stderr:"
            for line in proc.stderr.splitlines():
                yield line
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")


def remux_drop_streams(
    mkv_path: str,
    streams: List[dict],
    delete_indexes: Iterable[int],
    out_path: str,
) -> Iterator[str]:
    """Yield log lines while remuxing ``mkv_path`` to ``out_path`` with the
    given subtitle stream indexes excluded. ``streams`` is the full
    ffprobe stream list — used to compute the kept stream indexes.
    """
    exclude_set = {int(x) for x in delete_indexes}
    kept_subs = [st for st in streams if st.get("index") not in exclude_set]

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
    cmd += ["-c", "copy", "-max_interleave_delta", "0", out_path]

    yield from _run_ffmpeg(cmd)


def remux_with_translated_srt(
    mkv_path: str,
    srt_path: str,
    streams: List[dict],
    delete_indexes: Iterable[int],
    iso3: str,
    title: str,
    out_path: str,
) -> Iterator[str]:
    """Yield log lines while remuxing ``mkv_path`` with ``srt_path`` muxed
    in as a new subtitle track. Subtitle streams whose original index is
    in ``delete_indexes`` are dropped. ``streams`` is the full ffprobe
    stream list.
    """
    exclude_set = {int(x) for x in delete_indexes}
    kept_input_subs = [st for st in streams if st.get("index") not in exclude_set]
    new_track_index = len(kept_input_subs)

    ffmpeg_cmd = find_tool("ffmpeg")

    if exclude_set:
        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i",
            mkv_path,
            "-f",
            "srt",
            "-i",
            srt_path,
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
    else:
        cmd = [
            ffmpeg_cmd,
            "-y",
            "-i",
            mkv_path,
            "-f",
            "srt",
            "-i",
            srt_path,
            "-map",
            "0",
        ]

    cmd += [
        "-map",
        "1:0",
        "-c",
        "copy",
        "-max_interleave_delta",
        "0",
        f"-c:s:{new_track_index}",
        "srt",
        f"-metadata:s:s:{new_track_index}",
        f"language={iso3}",
        f"-metadata:s:s:{new_track_index}",
        f"title={title}",
        out_path,
    ]

    yield from _run_ffmpeg(cmd)
