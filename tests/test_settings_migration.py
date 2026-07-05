"""Tests for the show_kodi default/migration logic in AppSettings.load()."""

import json

from subtitle_translator.models import AppSettings
from subtitle_translator import models


def _redirect_home(monkeypatch, home):
    """Point AppSettings load()/save() at a temp home directory."""
    monkeypatch.setattr(models.os.path, "expanduser", lambda p: str(home))


def _settings_path(home):
    return home / ".subtitle_translator_settings.json"


def test_fresh_install_hides_kodi(tmp_path, monkeypatch):
    _redirect_home(monkeypatch, tmp_path)
    # No settings file at all -> fresh install -> hidden.
    assert not _settings_path(tmp_path).exists()
    s = AppSettings.load()
    assert s.show_kodi is False


def test_upgrading_user_keeps_kodi_visible(tmp_path, monkeypatch):
    _redirect_home(monkeypatch, tmp_path)
    # Pre-1.4.2 config: file exists but has no show_kodi key.
    _settings_path(tmp_path).write_text(
        json.dumps({"api_key": "x", "target_language": "ru"}), encoding="utf-8"
    )
    s = AppSettings.load()
    assert s.show_kodi is True


def test_explicit_false_is_respected(tmp_path, monkeypatch):
    _redirect_home(monkeypatch, tmp_path)
    _settings_path(tmp_path).write_text(
        json.dumps({"show_kodi": False}), encoding="utf-8"
    )
    assert AppSettings.load().show_kodi is False


def test_explicit_true_is_respected(tmp_path, monkeypatch):
    _redirect_home(monkeypatch, tmp_path)
    _settings_path(tmp_path).write_text(
        json.dumps({"show_kodi": True}), encoding="utf-8"
    )
    assert AppSettings.load().show_kodi is True


def test_save_load_roundtrip(tmp_path, monkeypatch):
    _redirect_home(monkeypatch, tmp_path)
    s = AppSettings()
    s.show_kodi = True
    s.save()
    # File now contains the key -> value is round-tripped, not re-migrated.
    assert AppSettings.load().show_kodi is True

    s.show_kodi = False
    s.save()
    assert AppSettings.load().show_kodi is False
