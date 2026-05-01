"""Content sanitizer for translated subtitle text.

The chat model occasionally leaks SRT artifacts (numeric indices,
``HH:MM:SS,mmm --> ...`` timestamp lines) into the response body. This
module strips those at the edges while preserving the original line
breaks within each cue.
"""

from __future__ import annotations

import re


_TS_RE = re.compile(
    r"^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{3}$"
)
_IDX_RE = re.compile(r"^\d{1,5}$")


def sanitize_content(text: str) -> str:
    """Drop pure index or timestamp lines if the model leaked them.

    Preserves all other line breaks; CRLF normalization is done by the
    final SRT writer, not here.
    """
    if not text:
        return ""
    tmp = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = tmp.split("\n")
    cleaned = []
    for ln in lines:
        stripped = ln.strip()
        if _IDX_RE.match(stripped) or _TS_RE.match(stripped):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)
