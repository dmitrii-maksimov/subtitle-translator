"""Pure helpers for picking and matching subtitle tracks.

Used by the per-file selection dialog (carry-over of user choices across
a batch) and by automatic source-track picking in live/follow modes.
"""

from __future__ import annotations

from typing import Optional


def stream_match_key(stream: dict):
    """Tuple used to match equivalent subtitle tracks across files."""
    tags = stream.get("tags") or {}
    lang = tags.get("language") or "und"
    title = tags.get("title") or ""
    codec = stream.get("codec_name") or ""
    return (lang, title, codec)


def match_initial_state(streams, previous_prefs):
    """Return ``{stream_index: {"translate": bool, "delete": bool}}``
    pre-filled from ``previous_prefs``, keyed by ``(lang, title, codec)``."""
    result = {}
    for st in streams:
        key = stream_match_key(st)
        prefs = previous_prefs.get(key) or {"translate": False, "delete": False}
        result[st.get("index")] = {
            "translate": bool(prefs.get("translate")),
            "delete": bool(prefs.get("delete")),
        }
    return result


def pick_source_subtitle_stream(streams, target_lang) -> Optional[int]:
    """Pick the best source subtitle stream from ``streams``.

    Priority:
      1. Skip the target language itself.
      2. Skip ASS / SSA codecs (they translate badly).
      3. Prefer English (eng/en).
      4. Within a language, prefer titles in {"", "Full"} over anything
         else, with SDH / hearing-impaired tracks ranked last.
      5. If no English remains, fall back to the same scoring on other
         non-target languages.

    Returns the chosen stream's ``index`` (int) or ``None`` if nothing fits.
    """
    target_lower = (target_lang or "").lower().strip()

    def is_target_lang(lang):
        if not target_lower:
            return False
        return lang.startswith(target_lower) or target_lower.startswith(lang)

    def title_rank(title, disposition):
        title_l = (title or "").strip().lower()
        if "sdh" in title_l or "hearing" in title_l:
            return 2
        if (disposition or {}).get("hearing_impaired"):
            return 2
        if title_l in ("", "full"):
            return 0
        return 1

    candidates = []
    for s in streams:
        codec = (s.get("codec_name") or "").lower()
        if codec in ("ass", "ssa"):
            continue
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "").lower().strip()
        if is_target_lang(lang):
            continue
        title = tags.get("title") or ""
        t_rank = title_rank(title, s.get("disposition"))
        if lang in ("eng", "en"):
            l_rank = 0
        else:
            l_rank = 1
        candidates.append(((l_rank, t_rank, s.get("index", 999)), s))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1].get("index")


# Legacy private aliases — kept while older imports migrate.
_stream_match_key = stream_match_key
_pick_source_subtitle_stream = pick_source_subtitle_stream
