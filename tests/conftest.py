"""
Shared fixtures for the Aevum test suite.

Real media files are generated with ffmpeg (a few seconds of silence or a
sine tone) so the tests need no binary assets in the repo and no network.
Everything that needs ffmpeg/ffprobe is skipped when they aren't installed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# Every generated clip is this long (seconds).
CLIP_SECONDS = 3

# name -> ffmpeg audio codec
_CLIPS = {
    "clip.mp4":  "aac",
    "clip.mkv":  "libvorbis",
    "clip.webm": "libopus",
    "clip.mp3":  "libmp3lame",
}


def _make_clip(dest: Path, codec: str) -> None:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_SECONDS}",
         "-c:a", codec, str(dest)],
        check=True,
    )


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> dict[str, Path]:
    """Generated media files, keyed by file name (clip.mp4, clip.mkv, ...)."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    base = tmp_path_factory.mktemp("clips")
    out = {}
    for name, codec in _CLIPS.items():
        _make_clip(base / name, codec)
        out[name] = base / name
    return out


@pytest.fixture
def library(tmp_path, clips) -> Path:
    """
    A small library folder:

        library/
        ├── a.mp4
        ├── notes.txt            (not media, must be ignored)
        ├── broken.mp4           (garbage bytes, must be ignored)
        ├── empty/               (no files)
        └── Season1/
            ├── ep1.mkv
            ├── ep2.webm
            └── extras/
                └── song.mp3
    """
    root = tmp_path / "library"
    (root / "Season1" / "extras").mkdir(parents=True)
    (root / "empty").mkdir()
    shutil.copy(clips["clip.mp4"], root / "a.mp4")
    shutil.copy(clips["clip.mkv"], root / "Season1" / "ep1.mkv")
    shutil.copy(clips["clip.webm"], root / "Season1" / "ep2.webm")
    shutil.copy(clips["clip.mp3"], root / "Season1" / "extras" / "song.mp3")
    (root / "notes.txt").write_text("not media")
    (root / "broken.mp4").write_bytes(os.urandom(4096))
    return root


@pytest.fixture
def run_cli(tmp_path):
    """
    Run the real CLI (aevum.py) in a subprocess.

    HOME / LOCALAPPDATA / XDG_DATA_HOME point at a throwaway directory so a
    test can never read or write the developer's real Aevum data (API key,
    cache, quota files).

    ANSI color codes are stripped from stdout/stderr so tests can match
    plain text.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "LOCALAPPDATA": str(fake_home / "AppData" / "Local"),
            "XDG_DATA_HOME": str(fake_home / ".local" / "share"),
            "PYTHONIOENCODING": "utf-8",
        }
        r = subprocess.run(
            [sys.executable, str(ROOT / "aevum.py"), *args],
            capture_output=True, text=True, encoding="utf-8",
            input=stdin, env=env, cwd=ROOT, timeout=120,
        )
        r.stdout = _ANSI.sub("", r.stdout)
        r.stderr = _ANSI.sub("", r.stderr)
        return r

    return run
