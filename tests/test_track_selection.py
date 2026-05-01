"""Unit tests for the per-file track selection carry-over matcher.

These tests cover the pure-Python matcher used by `TrackSelectionDialog` to
pre-fill checkboxes for the next file based on the previous file's choices.
No Qt event loop is required.
"""

from subtitle_translator.core.track_matcher import (
    match_initial_state,
    stream_match_key as _stream_match_key,
)


def _stream(index, lang=None, title=None, codec="subrip"):
    tags = {}
    if lang is not None:
        tags["language"] = lang
    if title is not None:
        tags["title"] = title
    return {"index": index, "codec_name": codec, "tags": tags}


def test_exact_match_preserves_state():
    streams = [
        _stream(0, lang="eng", title="Full", codec="subrip"),
        _stream(1, lang="rus", title="Forced", codec="subrip"),
    ]
    prev = {
        ("eng", "Full", "subrip"): {"translate": True, "delete": False},
        ("rus", "Forced", "subrip"): {"translate": False, "delete": True},
    }
    result = match_initial_state(streams, prev)
    assert result[0] == {"translate": True, "delete": False}
    assert result[1] == {"translate": False, "delete": True}


def test_no_match_defaults_unchecked():
    streams = [_stream(0, lang="jpn", title="Sign", codec="ass")]
    prev = {("eng", "Full", "subrip"): {"translate": True, "delete": False}}
    result = match_initial_state(streams, prev)
    assert result[0] == {"translate": False, "delete": False}


def test_partial_match():
    streams = [
        _stream(2, lang="eng", title="Full", codec="subrip"),  # match
        _stream(3, lang="eng", title="SDH", codec="subrip"),  # no match
    ]
    prev = {("eng", "Full", "subrip"): {"translate": True, "delete": True}}
    result = match_initial_state(streams, prev)
    assert result[2] == {"translate": True, "delete": True}
    assert result[3] == {"translate": False, "delete": False}


def test_empty_streams():
    assert match_initial_state([], {}) == {}


def test_codec_difference_breaks_match():
    streams = [_stream(0, lang="eng", title="Full", codec="ass")]
    prev = {("eng", "Full", "subrip"): {"translate": True, "delete": False}}
    result = match_initial_state(streams, prev)
    assert result[0] == {"translate": False, "delete": False}


def test_missing_tags_fall_back_to_und_and_empty():
    # Stream with no tags at all → key is ("und", "", codec)
    streams = [{"index": 5, "codec_name": "subrip"}]
    prev = {("und", "", "subrip"): {"translate": False, "delete": True}}
    result = match_initial_state(streams, prev)
    assert result[5] == {"translate": False, "delete": True}


def test_stream_match_key_normalization():
    assert _stream_match_key({"index": 0, "codec_name": "subrip"}) == (
        "und",
        "",
        "subrip",
    )
    assert _stream_match_key(
        {"index": 0, "codec_name": "ass", "tags": {"language": "rus"}}
    ) == ("rus", "", "ass")
