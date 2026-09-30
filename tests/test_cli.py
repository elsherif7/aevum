"""End-to-end CLI behaviour: arguments, exit codes, and a real scan."""
from __future__ import annotations

from conftest import needs_ffmpeg

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
