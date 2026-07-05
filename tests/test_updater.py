"""Tests for subtitle_translator.updater (Qt-free)."""

import subtitle_translator.updater as updater


# ---- version comparison -------------------------------------------------

def test_is_newer_basic():
    assert updater.is_newer("1.5.0", "1.4.0")
    assert updater.is_newer("1.4.1", "1.4.0")
    assert updater.is_newer("2.0.0", "1.9.9")


def test_is_newer_handles_v_prefix():
    assert updater.is_newer("v1.5.0", "1.4.0")
    assert not updater.is_newer("v1.4.0", "1.4.0")


def test_not_newer_when_equal_or_older():
    assert not updater.is_newer("1.4.0", "1.4.0")
    assert not updater.is_newer("1.3.9", "1.4.0")


def test_not_newer_when_remote_unparseable():
    assert not updater.is_newer("", "1.4.0")
    assert not updater.is_newer("latest", "1.4.0")


# ---- platform asset selection ------------------------------------------

def test_asset_windows(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.os, "name", "nt")
    assert updater._platform_asset_name() == updater._ASSET_WINDOWS


def test_asset_macos(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.os, "name", "posix")
    assert updater._platform_asset_name() == updater._ASSET_MACOS


def test_asset_linux_appimage(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setenv("APPIMAGE", "/tmp/SubtitleTranslator-linux.AppImage")
    assert updater._platform_asset_name() == updater._ASSET_APPIMAGE


def test_asset_linux_deb(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert updater._platform_asset_name() == updater._ASSET_DEB


# ---- check_for_update ---------------------------------------------------

class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _release(tag, assets):
    return {
        "tag_name": tag,
        "body": "release notes here",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": assets,
    }


def test_check_returns_info_for_newer(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setenv("APPIMAGE", "/tmp/app.AppImage")
    monkeypatch.setattr(updater, "current_version", lambda: "1.4.0")

    payload = _release("v1.5.0", [
        {"name": updater._ASSET_APPIMAGE,
         "browser_download_url": "https://dl/app.AppImage", "size": 12345},
        {"name": updater._ASSET_WINDOWS,
         "browser_download_url": "https://dl/setup.exe", "size": 999},
    ])
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, payload))

    info = updater.check_for_update()
    assert info is not None
    assert info.version == "1.5.0"
    assert info.asset_name == updater._ASSET_APPIMAGE
    assert info.asset_url == "https://dl/app.AppImage"
    assert info.asset_size == 12345
    assert info.notes == "release notes here"


def test_check_strips_release_page_footer_from_notes(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.os, "name", "nt")
    monkeypatch.setattr(updater, "current_version", lambda: "1.4.0")

    body = (
        "### Fixed\n- something\n\n"
        f"{updater.IN_APP_NOTES_MARKER}\n\n"
        "## Downloads\n| Platform | File |\n"
    )
    payload = _release("v1.5.0", [
        {"name": updater._ASSET_WINDOWS, "browser_download_url": "u", "size": 1},
    ])
    payload["body"] = body
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, payload))

    info = updater.check_for_update()
    assert info is not None
    assert info.notes == "### Fixed\n- something"
    assert "Downloads" not in info.notes


def test_check_returns_none_when_not_newer(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: "1.4.0")
    payload = _release("v1.4.0", [
        {"name": updater._ASSET_WINDOWS, "browser_download_url": "u", "size": 1},
    ])
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, payload))
    assert updater.check_for_update() is None


def test_check_returns_none_when_no_matching_asset(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setattr(updater, "current_version", lambda: "1.4.0")
    payload = _release("v1.5.0", [
        {"name": updater._ASSET_WINDOWS, "browser_download_url": "u", "size": 1},
    ])
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(200, payload))
    assert updater.check_for_update() is None


def test_check_returns_none_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise updater.requests.RequestException("no network")
    monkeypatch.setattr(updater.requests, "get", boom)
    assert updater.check_for_update() is None


def test_check_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResp(404, {}))
    assert updater.check_for_update() is None
