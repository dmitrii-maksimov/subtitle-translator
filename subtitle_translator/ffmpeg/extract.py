"""ffmpeg wrappers for extracting subtitle streams to SRT files."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from ..utils import find_tool, make_startupinfo


def extract_srt_lenient(
    mkv_path: str, stream_index: int, out_path: Optional[str] = None
) -> Optional[str]:
    """Extract a single subtitle stream from a partial/still-downloading mkv.

    Adds ``-err_detect ignore_err`` and ``-fflags +genpts+igndts`` so ffmpeg
    keeps going past truncation at end of file. Returns the output SRT path,
    or ``None`` on hard failure.
    """
    if out_path is None:
        base, _ = os.path.splitext(mkv_path)
        out_path = base + f".live.stream{stream_index}.srt"
    cmd = [
        find_tool("ffmpeg"),
        "-y",
        "-err_detect",
        "ignore_err",
        "-fflags",
        "+genpts+igndts",
        "-i",
        mkv_path,
        "-map",
        f"0:{stream_index}",
        "-c:s",
        "srt",
        out_path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
    )
    if proc.returncode != 0 and not os.path.exists(out_path):
        return None
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return None
    return out_path


def extract_srt(mkv_path: str, stream_index: int, out_path: str) -> str:
    """Strict extraction of a subtitle stream to SRT for the normal batch
    flow. Raises ``RuntimeError`` on any ffmpeg error.
    """
    cmd = [
        find_tool("ffmpeg"),
        "-y",
        "-i",
        mkv_path,
        "-map",
        f"0:{stream_index}",
        "-c:s",
        "srt",
        out_path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg extract failed")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("ffmpeg produced no SRT output")
    return out_path
