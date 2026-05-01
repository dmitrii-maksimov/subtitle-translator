"""Unit tests for live_translate_mkv polling logic.

Mocks ffmpeg extraction and translation so we can verify:
  1. Translation is gated by `len(new_subs) >= window + overlap` while file grows.
  2. Final flush happens once size has been stable for `live_stable_threshold`.
  3. Existing translated lines are not re-translated.
"""

import os
import threading
import time

import pytest
import srt
from datetime import timedelta

import subtitle_translator.core.live_loop as mw
from subtitle_translator.models import AppSettings


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Make the live loop's `time.sleep` and per-second cancel polling instant."""
    monkeypatch.setattr(mw.time, "sleep", lambda *_: None)
    yield


def _make_subs(n, start_index=1):
    """Generate `n` srt.Subtitle entries with non-overlapping timecodes."""
    subs = []
    for i in range(n):
        idx = start_index + i
        subs.append(
            srt.Subtitle(
                index=idx,
                start=timedelta(seconds=i * 2),
                end=timedelta(seconds=i * 2 + 1, milliseconds=500),
                content=f"Line {idx}",
            )
        )
    return subs


def test_waits_until_threshold_then_translates(monkeypatch, tmp_path):
    """File size keeps growing; translate fires only when window+overlap accumulated."""
    mkv = tmp_path / "movie.mkv"
    mkv.write_bytes(b"\x00")  # placeholder; size mocked anyway

    settings = AppSettings(
        target_language="ru",
        window=10,
        overlap=4,
        live_poll_interval=5,
        live_stable_threshold=5,
        workers=1,
    )

    # Subtitle availability over time: each call returns one more line until enough,
    # then we let it grow well past the threshold.
    sub_counts = iter([5, 10, 14, 14, 14, 14])  # threshold=14
    mtimes = iter([1000.0, 1010.0, 1020.0, 1020.0, 1020.0, 1020.0])  # stops growing

    extract_calls = []

    def fake_extract(path, idx, out_path=None):
        try:
            count = next(sub_counts)
        except StopIteration:
            count = 14
        subs = _make_subs(count)
        target = out_path or str(mkv) + ".srt"
        with open(target, "w", encoding="utf-8") as f:
            f.write(srt.compose(subs))
        extract_calls.append(count)
        return target

    def fake_getmtime(p):
        try:
            return next(mtimes)
        except StopIteration:
            return 1020.0

    translate_calls = []

    def fake_translate_subs(
        entries,
        translator,
        settings,
        target_lang,
        sanitize=None,
        cancel_flag=None,
        fulllog=False,
    ):
        translate_calls.append(len(entries))
        # Mark as translated by appending [TR] suffix.
        out = []
        for i, e in enumerate(entries):
            out.append(
                srt.Subtitle(
                    index=i + 1,
                    start=e.start,
                    end=e.end,
                    content="[TR] " + e.content,
                )
            )
        return out
        # `return` from a non-generator helper exits — but translate_subs is a
        # generator. We turn this into a generator below via wrapper.

    # translate_subs is a generator; provide a generator that yields nothing
    # and returns the translated list (PEP 380).
    def fake_translate_gen(*args, **kwargs):
        translated = fake_translate_subs(*args, **kwargs)
        if False:
            yield  # makes it a generator
        return translated

    monkeypatch.setattr(mw, "extract_srt_lenient", fake_extract)
    monkeypatch.setattr(mw.os.path, "getmtime", fake_getmtime)
    monkeypatch.setattr(mw, "translate_subs", fake_translate_gen)

    cancel = threading.Event()
    gen = mw.live_translate_mkv(
        mkv_path=str(mkv),
        stream_index=0,
        target_lang="ru",
        settings=settings,
        translator=object(),
        kodi_client=None,
        sanitize=None,
        cancel_flag=cancel,
    )

    statuses = []
    for u in gen:
        statuses.append(u)
        if len(translate_calls) >= 1 or len(statuses) > 200:
            cancel.set()

    # Translate must fire exactly once during streaming (file still changing),
    # and the batch must consist of complete windows only — not the full 14.
    # window=10, overlap=4, threshold=14 → floor(14/10)*10 = 10 lines.
    assert len(translate_calls) == 1
    assert translate_calls[0] == 10

    # Output SRT contains the 10 translated lines plus the trailing CAPS
    # sentinel "SUBTITLES NOT TRANSLATED YET ..." appended after every batch write.
    out_srt = str(mkv).rsplit(".", 1)[0] + ".ru.translated.srt"
    assert os.path.exists(out_srt)
    with open(out_srt, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = list(srt.parse(text))
    real = [
        p for p in parsed if not p.content.startswith("SUBTITLES NOT TRANSLATED YET")
    ]
    assert len(real) == 10
    assert all(p.content.startswith("[TR] ") for p in real)
    sentinels = [
        p for p in parsed if p.content.startswith("SUBTITLES NOT TRANSLATED YET")
    ]
    assert len(sentinels) == 1, "Sentinel must be present exactly once"


def test_stable_below_threshold_keeps_polling_no_translate(monkeypatch, tmp_path):
    """When mtime is stable but new subs < threshold, loop must NOT translate;
    it should keep polling until externally cancelled."""
    mkv = tmp_path / "movie.mkv"
    mkv.write_bytes(b"\x00")

    settings = AppSettings(
        target_language="ru",
        window=5,
        overlap=2,
        live_poll_interval=5,
        live_stable_threshold=5,
        workers=1,
    )

    # mtime constant → file is "stable" from the very first poll.
    monkeypatch.setattr(mw.os.path, "getmtime", lambda p: 1000.0)

    def fake_extract(path, idx, out_path=None):
        subs = _make_subs(3)  # threshold = 5+2 = 7, so 3 < threshold
        target = out_path or str(mkv) + ".srt"
        with open(target, "w", encoding="utf-8") as f:
            f.write(srt.compose(subs))
        return target

    translate_calls = []

    def fake_translate_gen(*a, **kw):
        entries = kw.get("entries") or a[0]
        translate_calls.append(len(entries))
        if False:
            yield
        return [
            srt.Subtitle(index=i + 1, start=e.start, end=e.end, content="X")
            for i, e in enumerate(entries)
        ]

    monkeypatch.setattr(mw, "extract_srt_lenient", fake_extract)
    monkeypatch.setattr(mw, "translate_subs", fake_translate_gen)

    cancel = threading.Event()
    gen = mw.live_translate_mkv(
        mkv_path=str(mkv),
        stream_index=0,
        target_lang="ru",
        settings=settings,
        translator=object(),
        kodi_client=None,
        sanitize=None,
        cancel_flag=cancel,
    )

    statuses = []
    for u in gen:
        statuses.append(u)
        if len(statuses) > 30:
            cancel.set()

    # Loop must have polled and exited via cancel without translating.
    assert translate_calls == [], "Sub-threshold remainder must NOT be translated"
    assert any("Accumulated" in str(s) for s in statuses)
    out_srt = str(mkv).rsplit(".", 1)[0] + ".ru.translated.srt"
    assert not os.path.exists(out_srt)


def test_streaming_translates_only_full_windows_then_flushes_tail_on_stable(
    monkeypatch, tmp_path
):
    """Two-phase scenario:
    1) File still changing — translate only complete windows; partial tail kept.
    2) File becomes stable — flush leftover tail.
    """
    mkv = tmp_path / "movie.mkv"
    mkv.write_bytes(b"\x00")

    settings = AppSettings(
        target_language="ru",
        window=10,
        overlap=4,
        live_poll_interval=5,
        live_stable_threshold=5,
        workers=1,
    )

    # mtime: changes for first two extracts, then frozen → stable kicks in.
    mtimes = iter([1000.0, 1010.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0])
    monkeypatch.setattr(
        mw.os.path,
        "getmtime",
        lambda p: next(mtimes, 2000.0),
    )

    # Sub counts: 5 → 18 (>= threshold 14). After file freezes still 18.
    sub_counts = iter([5, 18, 18, 18, 18])

    def fake_extract(path, idx, out_path=None):
        n = next(sub_counts, 18)
        subs = _make_subs(n)
        target = out_path or str(mkv) + ".srt"
        with open(target, "w", encoding="utf-8") as f:
            f.write(srt.compose(subs))
        return target

    translate_calls = []

    def fake_translate_gen(*a, **kw):
        entries = kw.get("entries") or a[0]
        translate_calls.append(len(entries))
        if False:
            yield
        return [
            srt.Subtitle(index=i + 1, start=e.start, end=e.end, content="T")
            for i, e in enumerate(entries)
        ]

    monkeypatch.setattr(mw, "extract_srt_lenient", fake_extract)
    monkeypatch.setattr(mw, "translate_subs", fake_translate_gen)

    cancel = threading.Event()
    gen = mw.live_translate_mkv(
        mkv_path=str(mkv),
        stream_index=0,
        target_lang="ru",
        settings=settings,
        translator=object(),
        kodi_client=None,
        sanitize=None,
        cancel_flag=cancel,
    )

    for u in gen:
        if len(translate_calls) >= 2:
            cancel.set()
        if len(translate_calls) > 5:
            break

    # First batch must be a full window only (10), tail of 8 left for next.
    # Once stable, the remaining 8 lines flush in the second call.
    assert translate_calls[:2] == [10, 8], translate_calls

    out_srt = str(mkv).rsplit(".", 1)[0] + ".ru.translated.srt"
    with open(out_srt, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = list(srt.parse(text))
    real = [
        p for p in parsed if not p.content.startswith("SUBTITLES NOT TRANSLATED YET")
    ]
    assert len(real) == 18
    sentinels = [
        p for p in parsed if p.content.startswith("SUBTITLES NOT TRANSLATED YET")
    ]
    assert len(sentinels) == 1, "Sentinel must be present exactly once"


def test_cancel_flag_aborts(monkeypatch, tmp_path):
    """When cancel_flag is set externally, the loop must return promptly."""
    mkv = tmp_path / "movie.mkv"
    mkv.write_bytes(b"\x00")

    settings = AppSettings(
        target_language="ru",
        window=5,
        overlap=2,
        live_poll_interval=5,
        live_stable_threshold=600,  # long — must be cancel-driven
        workers=1,
    )

    # Each call returns just 1 sub — never reaches threshold.
    monkeypatch.setattr(mw.os.path, "getmtime", lambda p: 1000.0)

    def fake_extract(path, idx, out_path=None):
        subs = _make_subs(1)
        target = out_path or str(mkv) + ".srt"
        with open(target, "w", encoding="utf-8") as f:
            f.write(srt.compose(subs))
        return target

    monkeypatch.setattr(mw, "extract_srt_lenient", fake_extract)

    cancel = threading.Event()
    cancel.set()  # cancel before loop starts iterating

    gen = mw.live_translate_mkv(
        mkv_path=str(mkv),
        stream_index=0,
        target_lang="ru",
        settings=settings,
        translator=object(),
        kodi_client=None,
        sanitize=None,
        cancel_flag=cancel,
    )

    # Drain the generator. With cancel set, it must terminate quickly.
    list(gen)
