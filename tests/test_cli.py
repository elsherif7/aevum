"""End-to-end CLI behaviour: arguments, exit codes, and a real scan."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import ROOT, needs_ffmpeg

from aevum_pkg import __version__
from aevum_pkg._exit import EX


def test_no_arguments_prints_help(run_cli):
    r = run_cli()
    assert r.returncode == EX.OK
    assert "Usage" in r.stdout


def test_help_flag(run_cli):
    r = run_cli("--help")
    assert r.returncode == EX.OK
    assert "aevum scan" in r.stdout


def test_version_flag(run_cli):
    r = run_cli("--version")
    assert r.returncode == EX.OK
    assert r.stdout.strip() == f"aevum {__version__}"


def test_missing_scan_command(run_cli, tmp_path):
    r = run_cli(str(tmp_path))
    assert r.returncode == EX.ERR_ARGS
    assert "Missing 'scan'" in r.stderr


def test_scan_without_target(run_cli):
    r = run_cli("scan")
    assert r.returncode == EX.ERR_ARGS
    assert "No target" in r.stderr


def test_unquoted_path_with_spaces_is_rejected(run_cli):
    r = run_cli("scan", "my", "folder")
    assert r.returncode == EX.ERR_ARGS
    assert "quotes" in r.stderr


def test_nonexistent_path(run_cli, tmp_path):
    r = run_cli("scan", str(tmp_path / "nope"))
    assert r.returncode == EX.ERR_ARGS
    assert "Path not found" in r.stderr


def test_file_instead_of_folder(run_cli, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    r = run_cli("scan", str(f))
    assert r.returncode == EX.ERR_ARGS
    assert "not a folder" in r.stderr


def test_misspelled_folder_gets_a_suggestion(run_cli, tmp_path):
    (tmp_path / "Movies").mkdir()
    r = run_cli("scan", str(tmp_path / "Movis"))
    assert r.returncode == EX.ERR_ARGS
    assert "Did you mean" in r.stderr
    assert "Movies" in r.stderr


@needs_ffmpeg
def test_scan_folder_end_to_end(run_cli, library):
    r = run_cli("scan", str(library))
    assert r.returncode == EX.OK, r.stderr
    for section in ("Duration Breakdown", "Grand Total", "Playback Speed", "Top 10 Longest Files"):
        assert section in r.stdout
    assert "4 files found" in r.stdout
    assert "Season1" in r.stdout


@needs_ffmpeg
def test_scan_folder_with_spaces_when_quoted(run_cli, library, tmp_path):
    spaced = tmp_path / "My Videos"
    library.rename(spaced)
    r = run_cli("scan", str(spaced))
    assert r.returncode == EX.OK, r.stderr
    assert "4 files found" in r.stdout


@needs_ffmpeg
def test_scan_folder_without_media(run_cli, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("x")
    r = run_cli("scan", str(tmp_path / "docs"))
    assert r.returncode == EX.OK
    assert "0 files found" in r.stdout


@needs_ffmpeg
def test_scan_dot_shows_the_real_folder_name(run_cli, library):
    r = run_cli("scan", ".", cwd=library)
    assert r.returncode == EX.OK, r.stderr
    assert "4 files found" in r.stdout
    assert "library" in r.stdout          # not a blank root label


# ---------------------------------------------------------------------------
# options after `scan`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_scan_help_flag_prints_help(run_cli, flag):
    r = run_cli("scan", flag)
    assert r.returncode == EX.OK
    assert "Usage" in r.stdout


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_scan_version_flag(run_cli, flag):
    r = run_cli("scan", flag)
    assert r.returncode == EX.OK
    assert r.stdout.strip() == f"aevum {__version__}"


@pytest.mark.parametrize("args", [("--json",), ("--json", "somefolder")])
def test_scan_rejects_unknown_options(run_cli, args):
    r = run_cli("scan", *args)
    assert r.returncode == EX.ERR_ARGS
    assert "Unknown option" in r.stderr


@needs_ffmpeg
def test_folder_name_starting_with_dash_is_still_a_path(run_cli, tmp_path):
    (tmp_path / "-weird").mkdir()
    r = run_cli("scan", "-weird", cwd=tmp_path)
    assert r.returncode == EX.OK, r.stderr
    assert "0 files found" in r.stdout


# ---------------------------------------------------------------------------
# YouTube targets: validation happens before the API key prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://example.com/watch?v=abc",
    "https://www.youtube.com/feed/trending",
    "www.example.com/video",
])
def test_unsupported_url_is_rejected_without_asking_for_a_key(run_cli, url):
    r = run_cli("scan", url)
    assert r.returncode == EX.ERR_ARGS
    assert "Not a supported YouTube URL" in r.stderr
    assert "API key" not in r.stdout


@pytest.mark.parametrize("stdin", ["", "\n", "not-a-real-key\n"])
def test_cancelling_the_api_key_prompt_is_a_clean_cancel(run_cli, stdin):
    r = run_cli("scan", "https://youtu.be/dQw4w9WgXcQ", stdin=stdin)
    assert r.returncode == EX.ERR_SCAN
    assert "cancelled" in r.stdout
    assert "Done!" not in r.stdout          # no fake "0 videos" report
    assert "Grand Total" not in r.stdout


# ---------------------------------------------------------------------------
# Ctrl-C stops a running scan promptly
# ---------------------------------------------------------------------------

# Runs the real CLI, but with a slow folder walk, so Ctrl-C lands while files
# are still being discovered and the worker queue is filling up.
_SLOW_WALK_RUNNER = """
import os, sys, time
real_scandir = os.scandir
def slow_scandir(path):
    time.sleep(0.1)
    return real_scandir(path)
os.scandir = slow_scandir
sys.path.insert(0, {root!r})
sys.argv = ["aevum", "scan", {media!r}]
from aevum_pkg._cli import main
main()
"""


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell script and SIGINT")
def test_ctrl_c_during_file_discovery_does_not_wait_for_the_queue(tmp_path):
    # Fake ffprobe: 2 s per file. Without the fix, Ctrl-C waited for every file
    # queued so far (tens of seconds); with it, only for the few already running.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ffprobe = fake_bin / "ffprobe"
    ffprobe.write_text('#!/bin/sh\n[ "$1" = "-version" ] && exit 0\nsleep 2\necho 5.0\n')
    ffprobe.chmod(0o755)

    media = tmp_path / "media"
    for d in range(60):
        (media / f"d{d}").mkdir(parents=True)
        for i in range(4):
            (media / f"d{d}" / f"f{i}.flac").write_bytes(b"x")

    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    runner = _SLOW_WALK_RUNNER.format(root=str(ROOT), media=str(media))
    proc = subprocess.Popen(
        [sys.executable, "-c", runner],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT,
    )
    try:
        time.sleep(1.5)                      # workers busy, walk still in progress
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=60)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode == EX.ERR_SCAN
    assert "Scan cancelled" in out
    assert elapsed < 6, f"Ctrl-C took {elapsed:.1f}s to take effect"
