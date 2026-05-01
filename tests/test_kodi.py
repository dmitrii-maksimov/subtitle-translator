import os
import pytest

from subtitle_translator.kodi_client import map_local_to_kodi


def test_happy_path_smb():
    out = map_local_to_kodi(
        "/Volumes/movies/Action/movie.mkv",
        "/Volumes/movies",
        "smb://nas/movies",
    )
    assert out == "smb://nas/movies/Action/movie.mkv"


def test_kodi_parent_with_trailing_slash():
    out = map_local_to_kodi(
        "/Volumes/movies/movie.mkv",
        "/Volumes/movies",
        "smb://nas/movies/",
    )
    assert out == "smb://nas/movies/movie.mkv"


def test_nested_path():
    out = map_local_to_kodi(
        "/Volumes/movies/2024/horror/x.mkv",
        "/Volumes/movies",
        "nfs://10.0.0.1/movies",
    )
    assert out == "nfs://10.0.0.1/movies/2024/horror/x.mkv"


def test_file_outside_parent_raises():
    with pytest.raises(ValueError):
        map_local_to_kodi(
            "/tmp/elsewhere.mkv",
            "/Volumes/movies",
            "smb://nas/movies",
        )


def test_empty_local_parent_raises():
    with pytest.raises(ValueError):
        map_local_to_kodi("/x/y.mkv", "", "smb://x")


def test_empty_kodi_parent_raises():
    with pytest.raises(ValueError):
        map_local_to_kodi("/x/y.mkv", "/x", "")


def test_windows_separator_normalized(monkeypatch):
    """If running on Windows, backslashes in relpath should be normalized."""
    if os.sep != "\\":
        pytest.skip("only meaningful on Windows")
    out = map_local_to_kodi(
        r"D:\movies\Action\movie.mkv",
        r"D:\movies",
        "smb://nas/movies",
    )
    assert out == "smb://nas/movies/Action/movie.mkv"
