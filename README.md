# Subtitle Translator

A cross-platform desktop GUI for extracting, translating, and re-muxing subtitles inside MKV files.

Built with Python, PySide6 (Qt 6), and any OpenAI-compatible Chat Completions API.

## Features

- Pick an MKV file, several MKV files, or a whole folder — a popup
  appears per file listing every subtitle track (via ffprobe).
- For each track, tick **Translate** (one per file) and/or **Delete**.
  A **Save & Continue** button moves to the next file; **Skip** leaves
  the file alone; **Cancel** aborts the whole batch.
- Selections carry over: if the next file has a track with the same
  `(language, title, codec)`, the checkboxes are pre-filled — one pass
  of setup for a whole TV-show folder.
- Translate a chosen track using any OpenAI-compatible Chat Completions
  API (OpenAI, local proxy, etc.).
- Re-mux the translated SRT back into the MKV as a new track, and drop
  any tracks marked for deletion in the same pass.
- Standalone `.srt` / `.str` files: translated in place without remux.
- Parallel translation with configurable workers, window size, and
  overlap context.
- Progress reporting and cancellable operations.
- Theme-aware UI (light and dark).
- Model picker in Settings: dropdown populated from `/v1/models` with
  input/output price per 1M tokens shown inline (dates back to the last
  time the pricing table was refreshed locally); a **Refresh** button
  re-fetches the list, and a **Custom** checkbox lets you type an
  arbitrary model id for local proxies or unlisted models.
- Auto-download of ffmpeg on Windows if not found.

## Requirements

- Python 3.9+
- ffmpeg and ffprobe (see [Installing ffmpeg](#installing-ffmpeg))
- An OpenAI-compatible API key and endpoint

## Quick start

```bash
# Clone the repository
git clone https://github.com/dmitrii-maksimov/subtitle-translator.git
cd subtitle-translator

# Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python -m subtitle_translator
```

## Installing on macOS

1. Download the latest `SubtitleTranslator-macOS.dmg` from the [Releases](https://github.com/dmitrii-maksimov/subtitle-translator/releases) page.
2. Open the DMG and drag **SubtitleTranslator** into **Applications**.
3. On first launch macOS may show _"Apple could not verify…"_ — the app is not notarized. To bypass, run once in Terminal:

```bash
xattr -cr /Applications/SubtitleTranslator.app
```

Or: right-click the app in Applications → **Open** → **Open**.

## Installing ffmpeg

The app needs `ffmpeg` and `ffprobe` available in PATH (or next to the executable).

- **Windows**: The app can download ffmpeg automatically on first launch. Alternatively, download from https://ffmpeg.org/download.html and add to PATH.
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` (Debian/Ubuntu) or equivalent for your distro.

## Building a standalone executable

### Windows

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --noconsole ^
    --name SubtitleTranslator ^
    subtitle_translator\__main__.py
```

The resulting `SubtitleTranslator.exe` will be in the `dist/` folder. Place `ffmpeg.exe` and `ffprobe.exe` next to it (or ensure they are in PATH).

### macOS

```bash
pip install pyinstaller pillow
# Generate the .icns icon (requires macOS iconutil, included with Xcode CLT)
python make_icon.py
# Build the app bundle
pyinstaller --noconfirm --onedir --noconsole \
    --name SubtitleTranslator \
    --icon subtitle_translator/SubtitleTranslator.icns \
    subtitle_translator/__main__.py
```

The app bundle will be in `dist/SubtitleTranslator.app`. Use `--onedir` (not `--onefile`) to avoid a double Dock icon and a spurious second launch on macOS.

Make sure ffmpeg is installed via Homebrew (`brew install ffmpeg`). The app searches `/opt/homebrew/bin` and `/usr/local/bin` automatically, so it works even when launched from Finder.

## Configuration

All settings are stored in `~/.subtitle_translator_settings.json` and can be edited through the Settings tab in the GUI:

| Setting | Description | Default |
|---|---|---|
| API Key | Your OpenAI-compatible API key | — |
| API Base URL | API endpoint | `https://api.openai.com/v1` |
| Model | Chat model id (pick from the combo or tick **Custom** to type) | `gpt-4o-mini` |
| Target Language | Language to translate into | `ru` |
| Workers | Parallel translation threads | `5` |
| Window | Subtitles per translation chunk | `25` |
| Overlap | Context overlap between chunks | `10` |

## How it works

1. **Select** — open a file, multiple files, or a folder. For every
   MKV a modal lists its subtitle tracks. Tick one track as the
   translation source and any number of tracks for deletion.
2. **Extract** — ffmpeg pulls the chosen track to a temporary SRT file.
3. **Translate** — The SRT is split into overlapping windows. Each
   window is sent to the Chat API in parallel. Overlap ensures
   consistent translations across chunk boundaries.
4. **Re-mux** — The translated SRT is muxed back into the MKV as a new
   track; tracks marked for deletion are excluded via explicit
   `-map` whitelisting. By default the original file is preserved and
   a new `.translated.mkv` is created (toggle **Overwrite the original
   file** to replace it in place).

## Project structure

The codebase is split by concern: pure orchestration in `core/`,
ffmpeg subprocess wrappers in `ffmpeg/`, Qt UI in `ui/` (with one file
per dialog / tab / widget). The Qt-free modules in `core/` and
`ffmpeg/` can be imported and tested without a display.

```
subtitle_translator/
    __init__.py
    __main__.py                  # Entry point (launches the Qt app)
    models.py                    # AppSettings, FileDecision dataclasses
    services.py                  # TranslationService: prompts + /v1/models + /v1/chat/completions
    pricing.py                   # Local snapshot of OpenAI per-token prices
    utils.py                     # ffmpeg/ffprobe discovery and installation
    kodi_client.py               # Kodi JSON-RPC client + LAN discovery + path mapping
    main_window.py               # Compatibility shim, re-exports MainWindow

    core/                        # Pure logic, no Qt
        srt_io.py                # Sentinel + timecode helpers
        track_matcher.py         # Stream picker, match_initial_state
        sanitize.py              # Strip stray index/timestamp lines from model output
        translation_engine.py    # translate_subs (parallel windowed translation)
        live_loop.py             # live_translate_mkv: translate while file downloads
        kodi_follow.py           # kodi_follow_translate: stay N min ahead of Kodi playback

    ffmpeg/                      # Subprocess wrappers, no Qt
        probe.py                 # ffprobe_subs / ffprobe_subs_partial
        extract.py               # extract_srt / extract_srt_lenient
        remux.py                 # remux_drop_streams / remux_with_translated_srt

    ui/                          # Qt only
        main_window.py           # MainWindow: tab composition + signal routing
        workers.py               # WorkerThread, _ModelFetcherThread
        model_picker.py          # Model dropdown (combo + Custom + Refresh)
        widgets/
            elided_label.py
            model_price_delegate.py
        dialogs/
            track_selection.py
            kodi_discovery.py
            kodi_browse.py
            live_download.py
            kodi_follow.py
        tabs/
            main_tab.py
            settings_tab.py
            kodi_tab.py

tests/
    test_track_selection.py      # Carry-over matcher (no Qt)
    test_live_loop.py            # live_translate_mkv polling logic (no Qt)
    test_kodi.py                 # map_local_to_kodi
    test_pricing.py              # Pricing lookup and id normalization
```

## Running tests

```bash
pip install pytest
QT_QPA_PLATFORM=offscreen python -m pytest -v
```

`QT_QPA_PLATFORM=offscreen` lets Qt construct without a display, which
is required because the tests import the main module.

## License

MIT
