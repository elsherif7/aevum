"""Where Aevum keeps its key and cache: environment values are used only when safe."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aevum_pkg import _paths

ON_WINDOWS = os.name == "nt"
posix_only = pytest.mark.skipif(ON_WINDOWS, reason="XDG_DATA_HOME is the POSIX branch")
windows_only = pytest.mark.skipif(not ON_WINDOWS, reason="LOCALAPPDATA is the Windows branch")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    fake_home = tmp_path.resolve() / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


@posix_only
def test_xdg_data_home_is_used_when_absolute_and_resolved(tmp_path, monkeypatch, home):
    data = tmp_path.resolve() / "data"
    data.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    assert _paths._appdata_dir() == data / "Aevum"


@posix_only
@pytest.mark.parametrize("value", ["", "relative/dir", "~/data"])
def test_unset_empty_or_relative_xdg_data_home_falls_back(monkeypatch, home, value):
    monkeypatch.setenv("XDG_DATA_HOME", value)
    assert _paths._appdata_dir() == home / ".local" / "share" / "Aevum"


@posix_only
def test_missing_xdg_data_home_falls_back(monkeypatch, home):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert _paths._appdata_dir() == home / ".local" / "share" / "Aevum"


@posix_only
def test_symlinked_xdg_data_home_is_rejected(tmp_path, monkeypatch, home):
    real = tmp_path.resolve() / "real"
    real.mkdir()
    link = tmp_path.resolve() / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    monkeypatch.setenv("XDG_DATA_HOME", str(link))
    assert _paths._appdata_dir() == home / ".local" / "share" / "Aevum"


@windows_only
def test_localappdata_is_used_when_absolute_and_resolved(tmp_path, monkeypatch, home):
    data = tmp_path.resolve() / "Local"
    data.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(data))
    assert _paths._appdata_dir() == data / "Aevum"


@windows_only
@pytest.mark.parametrize("value", ["", "relative\\dir", "\\\\server\\share\\Aevum"])
def test_unset_relative_or_unc_localappdata_falls_back(monkeypatch, home, value):
    monkeypatch.setenv("LOCALAPPDATA", value)
    assert _paths._appdata_dir() == home / "AppData" / "Local" / "Aevum"


def test_state_files_live_in_the_data_directory():
    assert _paths.YT_KEY_FILE == _paths.APPDATA / "yt_api_key.txt"
    assert _paths.YT_VCACHE_FILE == _paths.APPDATA / "yt_video_cache.json"
    assert _paths.APPDATA.name == "Aevum"
