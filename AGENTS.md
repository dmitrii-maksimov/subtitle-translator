# Agent instructions

Universal instructions for AI coding agents working in this repository
(Claude Code, and any tool that reads `AGENTS.md`). These rules are
**mandatory**, not suggestions.

## 1. Every release must document what changed

Releases are cut by pushing a `vX.Y.Z` git tag, which triggers
`.github/workflows/release.yml`. The GitHub release notes are generated from
`CHANGELOG.md` by `scripts/extract_changelog.py`.

**Before tagging a release you MUST:**
1. Add a `## X.Y.Z` section at the top of `CHANGELOG.md` (newest first)
   describing what was **Added / Changed / Fixed** in that version, in
   user-facing terms. Never ship a release whose notes are just the generic
   app description.
2. Set the matching `__version__` in `subtitle_translator/__init__.py`
   (CI re-injects it from the tag via `scripts/set_version.py`, but keep the
   source in sync).
3. Use the same version for the tag, the `CHANGELOG.md` header, and
   `__init__.py`.

The release workflow **fails** if `CHANGELOG.md` has no section for the tag —
this is intentional. Do not work around it by faking an entry; write real notes.

## 2. Keep README.md in sync

On **every commit / PR**, verify that `README.md` still matches the actual
state of the app (features, install steps, download table, platform support,
settings, requirements). If a change makes the README inaccurate, update the
README in the same commit/PR. A change that alters user-visible behavior is not
complete until the README reflects it.

## 3. Versioning

- Semantic-ish `MAJOR.MINOR.PATCH`. Bump PATCH for fixes, MINOR for features.
- The single source of truth at runtime is `subtitle_translator/__init__.py`
  (`__version__`), surfaced in the window title and the Settings tab.

## 4. Before committing

- Run `python -m pytest tests/ -q` and keep it green.
- Byte-compile changed modules if a full GUI run isn't possible in the
  environment (PySide6 GUI can't always run headless).
