"""Parallel subtitle translation via OpenAI-compatible chat API.

The main public entry point is :func:`translate_subs`, a generator that
yields progress updates and returns the translated list of
``srt.Subtitle`` entries via PEP 380 (``ordered = yield from
translate_subs(...)``).

The function is intentionally Qt-agnostic. UI integration is done by
:class:`subtitle_translator.ui.workers.WorkerThread`, which converts
yielded ``int``/``str`` values into Qt signals.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

import srt


def translate_subs(
    entries: List[srt.Subtitle],
    translator,
    settings,
    target_lang: str,
    sanitize: Optional[Callable[[str], str]] = None,
    cancel_flag: Optional[threading.Event] = None,
    fulllog: bool = False,
):
    """Translate a list of ``srt.Subtitle`` entries via OpenAI-compatible API.

    Generator: yields ``int`` (progress 0-100), ``str`` (status), and any
    fulllog ``str`` lines. Returns the translated ``list[srt.Subtitle]``
    via PEP 380 (``ordered = yield from translate_subs(...)``).

    Window/overlap/parallelism are read from ``settings``; cancellation is
    cooperative via ``cancel_flag`` (a ``threading.Event``). ``sanitize``
    is applied to every translated content string before assembly.
    """
    if not entries:
        return []
    if cancel_flag is None:
        cancel_flag = threading.Event()
    if sanitize is None:
        sanitize = lambda s: s  # noqa: E731

    window = max(1, int(getattr(settings, "window", 25) or 25))
    overlap = max(0, int(getattr(settings, "overlap", 10) or 10))
    n = len(entries)
    step = max(1, window)
    core_ranges = [(s, min(s + window, n)) for s in range(0, n, step)]
    half = overlap // 2
    groups = []
    for core_start, core_end in core_ranges:
        trans_start = max(0, core_start - half)
        trans_end = min(n, core_end + half)
        groups.append((core_start, core_end, trans_start, trans_end))

    translated_entries = {}
    total_groups = len(groups)
    max_workers = max(1, min(10, int(getattr(settings, "workers", 5) or 5)))

    def translate_group(task_id, core_start, core_end, trans_start, trans_end):
        group_local = entries[trans_start:trans_end]
        prompt_local = translator.build_prompt(group_local, target_lang)
        result = translator.chat_translate(prompt_local)
        if isinstance(result, tuple):
            text, dbg = result
        else:
            text, dbg = result, None
        try:
            segs = list(srt.parse(text))
            if segs:
                return (
                    task_id,
                    core_start,
                    core_end,
                    trans_start,
                    trans_end,
                    "ok",
                    segs,
                    dbg,
                )
        except Exception:
            pass
        numbered = {}
        cur_idx = None
        buff = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line.strip().endswith(":") and line.strip()[:-1].isdigit():
                if cur_idx is not None:
                    numbered[cur_idx] = "\n".join(buff)
                cur_idx = int(line.strip()[:-1])
                buff = []
            else:
                buff.append(line)
        if cur_idx is not None:
            numbered[cur_idx] = "\n".join(buff)
        if numbered:
            mapped = []
            for orig in group_local:
                mapped.append(numbered.get(orig.index, ""))
            return (
                task_id,
                core_start,
                core_end,
                trans_start,
                trans_end,
                "numbered",
                mapped,
                dbg,
            )
        contents = [
            c.strip()
            for c in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        contents = [c for c in contents if c != ""]
        expected = len(group_local)
        if len(contents) < expected:
            contents += [""] * (expected - len(contents))
        elif len(contents) > expected:
            contents = contents[:expected]
        return (
            task_id,
            core_start,
            core_end,
            trans_start,
            trans_end,
            "fallback",
            contents,
            dbg,
        )

    yield f"Submitting {total_groups} groups to {max_workers} workers..."
    completed = 0
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(translate_group, i, core_s, core_e, trans_s, trans_e): (
                i,
                core_s,
                core_e,
                trans_s,
                trans_e,
            )
            for i, (core_s, core_e, trans_s, trans_e) in enumerate(groups, 1)
        }
        for fut in as_completed(futures):
            if cancel_flag.is_set():
                yield "Cancellation requested. Waiting for running tasks to finish..."
                break
            i, core_s, core_e, trans_s, trans_e = futures[fut]
            try:
                res = fut.result()
                results[i] = res
                if fulllog:
                    dbg = (
                        res[-1]
                        if isinstance(res, (list, tuple)) and len(res) >= 8
                        else None
                    )
                    if isinstance(dbg, dict):
                        try:
                            req_str = json.dumps(
                                {
                                    "url": dbg.get("url"),
                                    "headers": dbg.get("headers"),
                                    "body": dbg.get("body"),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            resp_str = json.dumps(
                                dbg.get("response_json"),
                                ensure_ascii=False,
                                indent=2,
                            )
                            yield f"[FullLog] Request (group {i}):\n{req_str}"
                            yield f"[FullLog] Response (group {i}):\nHTTP {dbg.get('status')}\n{resp_str}"
                        except Exception:
                            pass
                completed += 1
                pct = int(completed / total_groups * 80)
                yield pct
                yield f"Translated group {i}/{total_groups} (core {core_s+1}-{core_e}, translated {trans_s+1}-{trans_e})"
            except Exception as err:
                raise RuntimeError(f"Group {i} failed: {err}")

    if cancel_flag.is_set():
        return []

    for gi, (core_start, core_end, trans_start, trans_end) in enumerate(groups, 1):
        if gi not in results:
            continue
        res_tuple = results[gi]
        _, r_core_s, r_core_e, r_trans_s, r_trans_e, kind, payload = res_tuple[:7]
        group_local = entries[r_trans_s:r_trans_e]
        core_rel_start = max(0, r_core_s - r_trans_s)
        core_rel_end = max(core_rel_start, min(len(group_local), r_core_e - r_trans_s))
        if kind == "ok":
            if len(payload) != len(group_local):
                raise RuntimeError(
                    f"Model returned {len(payload)} segments, expected "
                    f"{len(group_local)} for translated window indices "
                    f"{group_local[0].index}-{group_local[-1].index}. "
                    "Aborting batch to avoid subtitle drift."
                )
            for idx_in_group in range(core_rel_start, core_rel_end):
                orig = entries[r_trans_s + idx_in_group]
                seg_content = (
                    payload[idx_in_group].content if idx_in_group < len(payload) else ""
                )
                clean = sanitize(seg_content)
                translated_entries[orig.index] = srt.Subtitle(
                    index=orig.index, start=orig.start, end=orig.end, content=clean
                )
        else:
            if len(payload) != len(group_local):
                raise RuntimeError(
                    f"Line count mismatch in translated window "
                    f"{group_local[0].index}-{group_local[-1].index}: "
                    f"got {len(payload)}, expected {len(group_local)}. "
                    "Aborting batch to avoid subtitle drift."
                )
            for idx_in_group in range(core_rel_start, core_rel_end):
                orig = entries[r_trans_s + idx_in_group]
                text = payload[idx_in_group] if idx_in_group < len(payload) else ""
                clean = sanitize(text)
                translated_entries[orig.index] = srt.Subtitle(
                    index=orig.index, start=orig.start, end=orig.end, content=clean
                )

    if len(translated_entries) != len(entries):
        missing = [e.index for e in entries if e.index not in translated_entries]
        raise RuntimeError(
            f"Missing translated entries for indices: "
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )

    ordered = [translated_entries[idx] for idx in sorted(translated_entries.keys())]
    ordered = list(srt.sort_and_reindex(ordered, start_index=1))
    return ordered
