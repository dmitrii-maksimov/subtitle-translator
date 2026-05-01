"""ffprobe wrappers for listing subtitle streams in MKV files."""

from __future__ import annotations

import json
import subprocess
from typing import List

from ..utils import find_tool, make_startupinfo


def ffprobe_subs_partial(mkv_path: str) -> List[dict]:
    """List subtitle streams in a (possibly still-downloading) mkv. Lenient."""
    cmd = [
        find_tool("ffprobe"),
        "-v",
        "error",
        "-err_detect",
        "ignore_err",
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
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return data.get("streams", []) or []


def ffprobe_subs(mkv_path: str) -> List[dict]:
    """Strict ffprobe: list all subtitle streams of a complete MKV.

    Used by the normal (non-live) batch flow. Raises ``RuntimeError`` on
    any ffprobe error.
    """
    cmd = [
        find_tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name,codec_type,disposition:stream_tags=language,title",
        "-of",
        "json",
        mkv_path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, startupinfo=make_startupinfo()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    return data.get("streams", []) or []
