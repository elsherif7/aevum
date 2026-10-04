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


@pytest.mark.parametrize("target", ["", "   ", "''", '" "'])
def test_blank_target_is_rejected(run_cli, target):
    r = run_cli("scan", target)
    assert r.returncode == EX.ERR_ARGS
    assert "No target" in r.stderr


def test_existing_folder_wins_over_domain_like_name(tmp_path, monkeypatch):
    from aevum_pkg import _cli_cmds

    (tmp_path / "www.backup").mkdir()
    (tmp_path / "youtube.com").mkdir()
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(_cli_cmds, "_scan_folder", lambda raw: calls.append(("folder", raw)))
    monkeypatch.setattr(_cli_cmds, "_scan_youtube", lambda raw: calls.append(("youtube", raw)))
    _cli_cmds.cmd_scan("www.backup")
    _cli_cmds.cmd_scan("youtube.com")
    _cli_cmds.cmd_scan("www.other")
    _cli_cmds.cmd_scan("https://www.backup")
    assert calls == [("folder", "www.backup"), ("folder", "youtube.com"),
                     ("youtube", "www.other"), ("youtube", "https://www.backup")]


def test_unexpected_scan_error_exits_3(tmp_path, monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    def boom(*args, **kwargs):
        raise RuntimeError("boom\x1b[2J")

    monkeypatch.setattr(_cli_cmds, "check_ffprobe", lambda: True)
    monkeypatch.setattr(_cli_cmds, "_run_scan", boom)
    with pytest.raises(SystemExit) as exc:
        _cli_cmds.cmd_scan(str(tmp_path))
    assert exc.value.code == EX.ERR_SCAN
    assert "[ERROR] RuntimeError: boom" in capsys.readouterr().err


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


# options after `scan`

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


# YouTube targets: validation happens before the API key prompt

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


# Ctrl-C stops a running scan promptly

# The real CLI with a slow folder walk, so Ctrl-C lands while files are still being found.
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
    # fake ffprobe, 2 s per file: Ctrl-C should wait only for the probes already running
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


# Limit message and the video-in-playlist hint (in-process, scan_url faked)

def _run_scan_youtube(monkeypatch, capsys, url, fake_scan_url):
    from aevum_pkg import _cli_cmds
    monkeypatch.setattr(_cli_cmds, "scan_url", fake_scan_url)
    with pytest.raises(SystemExit) as exc:
        _cli_cmds._scan_youtube(url)
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


def test_rate_limit_message_says_progress_is_saved(monkeypatch, capsys):
    from aevum_pkg._youtube import YouTubeLimitError

    def fake(*a, **k):
        e = YouTubeLimitError("Hourly request limit reached (100 requests per hour).",
                              kind="rate", retry_after=1500)
        e.saved, e.total = 1200, 2450
        raise e

    code, out, err = _run_scan_youtube(monkeypatch, capsys, "https://youtube.com/@x", fake)
    assert code == EX.ERR_API
    assert "1,200 of 2,450 videos are saved" in err
    assert "in about 25 minutes" in err
    assert "Run the same command again" in err


@pytest.mark.parametrize("seconds, phrase", [
    (45, "in about 1 minute"),
    (60, "in about 1 minute"),
    (61, "in about 2 minutes"),
    (600, "in about 10 minutes"),
    (7140, "in about 119 minutes"),
    (7200, "in about 2 hours"),
    (169200, "in about 47 hours"),
    (200000, "in about 3 days"),
])
def test_wait_phrase(seconds, phrase):
    from aevum_pkg._cli_cmds import _wait_phrase
    assert _wait_phrase(seconds) == phrase


def test_long_retry_after_message_tells_the_user_when_to_return(monkeypatch, capsys):
    from aevum_pkg._youtube import YouTubeLimitError

    def fake(*a, **k):
        e = YouTubeLimitError("YouTube is limiting requests: slow down", kind="rate", retry_after=600.0)
        e.saved, e.total = 50, 150
        raise e

    code, out, err = _run_scan_youtube(monkeypatch, capsys, "https://youtube.com/@x", fake)
    assert code == EX.ERR_API
    assert "in about 10 minutes" in err
    assert "50 of 150 videos are saved" in err


def test_rate_limit_without_a_wait_time_says_a_minute_or_two(monkeypatch, capsys):
    from aevum_pkg._youtube import YouTubeLimitError

    def fake(*a, **k):
        raise YouTubeLimitError("YouTube is limiting requests: slow down", kind="rate")

    code, out, err = _run_scan_youtube(monkeypatch, capsys, "https://youtube.com/@x", fake)
    assert code == EX.ERR_API
    assert "in a minute or two" in err
    assert "daily quota" not in err


def test_quota_message_mentions_daily_reset(monkeypatch, capsys):
    from aevum_pkg._youtube import YouTubeLimitError

    def fake(*a, **k):
        raise YouTubeLimitError("YouTube API quota exceeded: x", kind="quota")

    code, out, err = _run_scan_youtube(monkeypatch, capsys, "https://youtube.com/@x", fake)
    assert code == EX.ERR_API
    assert "daily quota resets" in err
    assert "videos are saved" not in err            # nothing to report before any fetch


def test_video_link_with_playlist_gets_a_hint(monkeypatch, capsys):
    from aevum_pkg._youtube import YouTubeLimitError

    def fake(*a, **k):
        raise YouTubeLimitError("stop", kind="rate", retry_after=60)

    _, out, _ = _run_scan_youtube(
        monkeypatch, capsys, "https://www.youtube.com/watch?v=abc&list=PL1", fake)
    assert "only the video is scanned" in out

    _, out, _ = _run_scan_youtube(
        monkeypatch, capsys, "https://www.youtube.com/watch?v=abc", fake)
    assert "only the video is scanned" not in out


# Hostile and awkward names, end to end

def _own_colours_removed(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@needs_ffmpeg
@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot contain control characters")
def test_hostile_folder_names_cannot_drive_the_terminal(run_cli, clips, tmp_path):
    root = tmp_path / "lib"
    names = [
        "evil\x1b]0;PWNED\x07dir",       # set window title
        "clear\x1b[2Jscreen",             # clear screen
        "hide\x1b[?25lcursor",            # private CSI
        "c1\u009b31mred",                 # C1 control
        "rlo\u202etxt.4pm",               # bidi override
    ]
    import shutil
    for n in names:
        (root / n).mkdir(parents=True)
        shutil.copy(clips["clip.mp4"], root / n / "x.mp4")

    r = run_cli("scan", str(root))
    assert r.returncode == EX.OK, r.stderr
    rest = _own_colours_removed(r.stdout)
    for ch in ("\x1b", "\x07", "\u009b", "\u202e"):
        assert ch not in rest, f"{ch!r} reached the terminal"
    assert "evildir" in rest and "clearscreen" in rest      # names still readable


@pytest.mark.skipif(os.name == "nt", reason="control characters cannot be passed this way on Windows")
def test_hostile_text_echoed_in_error_messages_is_sanitised(run_cli):
    for args in (("scan", "\x1b]0;PWNED\x07nope"),       # path not found
                 ("scan", "--\x1b[2Jopt"),                # unknown option
                 ("scan", "https://example.com/\x1b[2J")):  # unsupported URL
        r = run_cli(*args)
        assert r.returncode == EX.ERR_ARGS
        rest = _own_colours_removed(r.stderr)
        assert "\x1b" not in rest and "\x07" not in rest
        assert "\x1b[2J" not in r.stderr


@needs_ffmpeg
def test_names_the_console_cannot_encode_do_not_crash_the_report(run_cli, clips, tmp_path):
    # a Japanese folder name with a cp1252 output encoding must not end in a UnicodeEncodeError
    import shutil
    root = tmp_path / "lib"
    (root / "日本語").mkdir(parents=True)
    shutil.copy(clips["clip.mp4"], root / "日本語" / "x.mp4")

    r = run_cli("scan", str(root), env={"PYTHONIOENCODING": "cp1252"})
    assert r.returncode == EX.OK, r.stderr
    assert "Traceback" not in r.stderr
    assert "1 files found" in r.stdout


@needs_ffmpeg
def test_scan_reports_skipped_duplicate_links(run_cli, library):
    src = sorted(library.rglob("*.mp4"))[0]
    try:
        src.with_name("zz_link.mp4").symlink_to(src)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    r = run_cli("scan", str(library))
    assert r.returncode == 0
    assert "1 duplicate file skipped" in r.stdout
    assert "zz_link" not in r.stdout


@needs_ffmpeg
def test_local_scan_says_media_files_not_videos(run_cli, library):
    r = run_cli("scan", str(library))
    assert r.returncode == 0
    assert "media files" in r.stdout
    assert "video" not in r.stdout.lower()


# summary lines for skipped items

def test_skip_summary_lines(capsys):
    from aevum_pkg._cli_cmds import _print_skip_summary

    _print_skip_summary({"unreadable_files": 3, "timed_out": 1, "unreadable_dirs": 2, "skipped_deep": 1})
    out = capsys.readouterr().out
    assert "(3 media files could not be read, 1 of them timed out)" in out
    assert "(2 folders could not be opened)" in out
    assert "(1 folder nested more than 100 levels deep not scanned)" in out
    assert "video" not in out.lower()


def test_skip_summary_is_silent_when_nothing_was_skipped(capsys):
    from aevum_pkg._cli_cmds import _print_skip_summary

    _print_skip_summary({"duplicates_skipped": 0, "unreadable_files": 0, "timed_out": 0,
                         "unreadable_dirs": 0, "skipped_deep": 0})
    assert capsys.readouterr().out == ""


@needs_ffmpeg
def test_scan_reports_unreadable_media_and_still_exits_zero(run_cli, library):
    r = run_cli("scan", str(library))
    assert r.returncode == 0
    assert "(1 media file could not be read)" in r.stdout


@needs_ffmpeg
def test_clean_scan_prints_no_skip_summary(run_cli, library):
    (library / "broken.mp4").unlink()
    r = run_cli("scan", str(library))
    assert r.returncode == 0
    assert "could not be" not in r.stdout


# discovery counter shown before the progress bar

def test_discovery_counter_prints_on_a_terminal(monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    times = iter([100.0, 100.01, 100.5, 100.51])
    monkeypatch.setattr(_cli_cmds, "_is_interactive", lambda: True)
    monkeypatch.setattr(_cli_cmds.time, "monotonic", lambda: next(times))
    on_discovered = _cli_cmds._make_discovery_counter()
    for count in (1, 2, 3, 4):
        on_discovered(count)
    out = capsys.readouterr().out
    assert "1 file found" in out
    assert "2 files found" not in out            # redrawn too soon after the first
    assert "3 files found" in out
    assert "4 files found" not in out


def test_discovery_counter_is_silent_when_not_a_terminal(monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    monkeypatch.setattr(_cli_cmds, "_is_interactive", lambda: False)
    on_discovered = _cli_cmds._make_discovery_counter()
    on_discovered(1)
    on_discovered(500)
    assert capsys.readouterr().out == ""


# argument parsing and the error paths of the folder scan

@pytest.mark.parametrize("argv, expected", [
    (["scan", "D:", "\\"], "D:\\"),            # Windows can split "D:\" into two arguments
    (["scan", "D:", "/"], "D:/"),
    (["scan", "D:"], "D:"),
    (["scan", "'my folder'"], "my folder"),
    (["scan", '  "x"  '], "x"),
])
def test_parse_target(monkeypatch, argv, expected):
    from aevum_pkg._cli import _parse_target

    monkeypatch.setattr(sys, "argv", ["aevum", *argv])
    assert _parse_target() == expected


def test_drive_letter_is_only_rejoined_with_a_slash(monkeypatch, capsys):
    from aevum_pkg._cli import _parse_target

    monkeypatch.setattr(sys, "argv", ["aevum", "scan", "D:", "movies"])
    with pytest.raises(SystemExit) as exc:
        _parse_target()
    assert exc.value.code == EX.ERR_ARGS
    assert "Too many arguments" in capsys.readouterr().err


def test_missing_ffprobe_exits_2_with_install_hint(tmp_path, monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    monkeypatch.setattr(_cli_cmds, "check_ffprobe", lambda: False)
    with pytest.raises(SystemExit) as exc:
        _cli_cmds.cmd_scan(str(tmp_path))
    assert exc.value.code == EX.ERR_DEPS
    err = capsys.readouterr().err
    assert "ffprobe not found" in err
    assert "ffmpeg.org" in err


def test_missing_folder_suggests_a_close_name(tmp_path, monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    (tmp_path / "Movies").mkdir()
    with pytest.raises(SystemExit) as exc:
        _cli_cmds.cmd_scan(str(tmp_path / "Movis"))
    assert exc.value.code == EX.ERR_ARGS
    err = capsys.readouterr().err
    assert "Path not found" in err
    assert str(tmp_path / "Movies") in err


def test_a_file_instead_of_a_folder_exits_1(tmp_path, capsys):
    from aevum_pkg import _cli_cmds

    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(SystemExit) as exc:
        _cli_cmds.cmd_scan(str(f))
    assert exc.value.code == EX.ERR_ARGS
    assert "not a folder" in capsys.readouterr().err


def test_ctrl_c_during_a_folder_scan_exits_3(tmp_path, monkeypatch, capsys):
    from aevum_pkg import _cli_cmds

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(_cli_cmds, "check_ffprobe", lambda: True)
    monkeypatch.setattr(_cli_cmds, "_run_scan", interrupted)
    with pytest.raises(SystemExit) as exc:
        _cli_cmds.cmd_scan(str(tmp_path))
    assert exc.value.code == EX.ERR_SCAN
    assert "Scan cancelled" in capsys.readouterr().out

