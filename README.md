# Subtitle Translator

A cross-platform desktop GUI for extracting, translating, and re-muxing subtitles inside MKV files.

Built with Python, PySide6 (Qt 6), and any OpenAI-compatible Chat Completions API.

## Features

- Load an MKV file and list all embedded subtitle tracks (via ffprobe).
- Extract a selected subtitle track to SRT.
- Translate subtitles using a configurable Chat Completions API (OpenAI, local proxy, etc.).
- Re-mux the translated SRT back into the MKV as a new subtitle track.
- Batch mode: process multiple MKV files in one go.
- Parallel translation with configurable workers, window size, and overlap context.
- Progress reporting and cancellable operations.
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
| Model | Chat model name | `gpt-4o-mini` |
| Target Language | Language to translate into | `ru` |
| Workers | Parallel translation threads | `5` |
| Window | Subtitles per translation chunk | `25` |
| Overlap | Context overlap between chunks | `10` |

## How it works

1. **Extract** — ffprobe detects subtitle tracks; ffmpeg extracts the selected track to a temporary SRT file.
2. **Translate** — The SRT is split into overlapping windows. Each window is sent to the Chat API in parallel. Overlap ensures consistent translations across chunk boundaries.
3. **Re-mux** — The translated SRT is muxed back into the MKV as an additional subtitle track using ffmpeg. The original file is never modified; a new `.translated.mkv` is created.

## Project structure

```
subtitle_translator/
    __init__.py          # Package marker
    __main__.py          # Entry point (launches the Qt app)
    models.py            # AppSettings dataclass and persistence
    services.py          # TranslationService: prompts and API calls
    utils.py             # ffmpeg/ffprobe discovery and installation
    main_window.py       # GUI (PySide6)
```

## License

MIT
