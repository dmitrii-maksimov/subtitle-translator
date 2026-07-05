"""Assisted auto-update via GitHub Releases.

Qt-free logic so it can be unit-tested in isolation. The UI layer
(``ui/main_window.py``) drives the download (reusing ``utils.download_file``)
and the progress dialog, then calls :func:`apply_update`.

The update check never raises: any network/parse error yields ``None`` so a
failed check can never block or crash app startup.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

import requests

from . import __version__

GITHUB_REPO = "dmitrii-maksimov/subtitle-translator"

# Everything after this marker in a release body is for the GitHub release page
# only (download table, requirements, install notes) and is stripped from the
# in-app "What's new" dialog. It's an HTML comment, so it's invisible on GitHub.
IN_APP_NOTES_MARKER = "<!-- release-page-only -->"
_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

# Stable, version-less asset names published by the release workflow.
_ASSET_WINDOWS = "SubtitleTranslator-Setup.exe"
_ASSET_MACOS = "SubtitleTranslator-macOS.dmg"
_ASSET_APPIMAGE = "SubtitleTranslator-linux.AppImage"
_ASSET_DEB = "SubtitleTranslator-linux.deb"


@dataclass
class UpdateInfo:
    version: str          # release version, without a leading "v"
    notes: str            # release body (markdown)
    html_url: str         # release page
    asset_name: str       # matched asset file name
    asset_url: str        # browser_download_url
    asset_size: int       # bytes (0 if unknown)


def current_version() -> str:
    return __version__


def _parse_version(s: str) -> tuple:
    """Turn 'v1.4.0' / '1.4.0-rc1' into a comparable tuple of ints."""
    return tuple(int(x) for x in re.findall(r"\d+", s or ""))


def is_newer(remote: str, local: str) -> bool:
    """True if *remote* version is strictly newer than *local*."""
    r, l = _parse_version(remote), _parse_version(local)
    if not r:
        return False
    return r > l


def _platform_asset_name() -> Optional[str]:
    """Asset file name to update the currently-running app, or None."""
    if sys.platform == "win32" or os.name == "nt":
        return _ASSET_WINDOWS
    if sys.platform == "darwin":
        return _ASSET_MACOS
    # Linux
    if os.environ.get("APPIMAGE"):
        return _ASSET_APPIMAGE
    return _ASSET_DEB


def _select_asset(assets: list, wanted: str) -> Optional[dict]:
    for a in assets or []:
        if a.get("name") == wanted:
            return a
    return None


def check_for_update(timeout: int = 10) -> Optional[UpdateInfo]:
    """Query GitHub for the latest release.

    Returns an :class:`UpdateInfo` when a strictly newer version with a
    matching platform asset exists, else ``None``. Never raises.
    """
    try:
        resp = requests.get(
            _LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    tag = (data.get("tag_name") or "").lstrip("v")
    if not tag or not is_newer(tag, current_version()):
        return None

    wanted = _platform_asset_name()
    asset = _select_asset(data.get("assets", []), wanted) if wanted else None
    if not asset:
        return None

    notes = (data.get("body") or "").split(IN_APP_NOTES_MARKER, 1)[0].strip()

    return UpdateInfo(
        version=tag,
        notes=notes,
        html_url=data.get("html_url") or RELEASES_PAGE_URL,
        asset_name=asset.get("name", wanted),
        asset_url=asset.get("browser_download_url", ""),
        asset_size=int(asset.get("size") or 0),
    )


def apply_update(path: str) -> None:
    """Launch the downloaded installer/asset so the user can complete the
    update, or (AppImage) self-replace and relaunch.

    The caller is expected to quit the application right after this returns
    (except the AppImage branch, which re-execs and never returns).
    """
    if sys.platform == "win32" or os.name == "nt":
        # Run the Inno Setup installer; the app must exit so files can be replaced.
        subprocess.Popen([path])
        return

    if sys.platform == "darwin":
        # Mount the DMG in Finder; the user drags the app to /Applications.
        subprocess.Popen(["open", path])
        return

    # Linux
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        try:
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(path, appimage)  # atomic within same filesystem
            os.execv(appimage, [appimage])  # relaunch; does not return
        except OSError:
            # Fall back to revealing the download for a manual swap.
            subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])
        return

    # .deb — open in the graphical package installer.
    subprocess.Popen(["xdg-open", path])
