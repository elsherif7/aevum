"""Color and progress output: off unless the stream is a terminal."""

import os
import subprocess
import sys

import pytest
from conftest import ROOT

from aevum_pkg._color import _Colors, color_enabled, is_tty


class _Stream:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _BrokenStream:
    def isatty(self):
        raise ValueError("I/O operation on closed file")


# the shared terminal check

def test_is_tty_handles_terminals_pipes_and_broken_streams():
    assert is_tty(_Stream(True)) is True
    assert is_tty(_Stream(False)) is False
    assert is_tty(_BrokenStream()) is False
    assert is_tty(None) is False
    assert is_tty(object()) is False   # type: ignore[arg-type]  # no isatty at all


# the decision function

@pytest.mark.parametrize("env, tty, expected", [
    ({}, True, True),
    ({}, False, False),
    ({"NO_COLOR": "1"}, True, False),
    ({"NO_COLOR": ""}, True, True),              # empty means "not set"
    ({"FORCE_COLOR": "1"}, False, True),
    ({"FORCE_COLOR": ""}, False, False),
    ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, True, False),   # NO_COLOR wins
    ({"TERM": "dumb"}, True, False),
    ({"TERM": "dumb", "FORCE_COLOR": "1"}, True, True),
    ({"TERM": "xterm-256color"}, True, True),
])
def test_color_enabled(env, tty, expected):
    assert color_enabled(_Stream(tty), env) is expected


def test_color_enabled_survives_missing_or_closed_stream():
    assert color_enabled(None, {}) is False
    assert color_enabled(_BrokenStream(), {}) is False


def test_disabled_colors_are_empty_strings():
    off, on = _Colors(False), _Colors(True)
    for name in _Colors.__slots__:
        assert getattr(off, name) == ""
        assert getattr(on, name).startswith("\033[")


# whole program, piped (no terminal)

def _run(*args, env=None):
    base = {k: v for k, v in os.environ.items()
            if k not in ("NO_COLOR", "FORCE_COLOR", "TERM")}
    return subprocess.run(
        [sys.executable, str(ROOT / "aevum.py"), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**base, "PYTHONIOENCODING": "utf-8", **(env or {})},
        cwd=ROOT, timeout=120,
    )


def test_piped_output_has_no_escape_codes(tmp_path):
    assert "\x1b" not in _run("--help").stdout
    r = _run("scan", str(tmp_path / "missing"))
    assert "\x1b" not in r.stderr and "[ERROR]" in r.stderr


def test_force_color_colors_piped_output(tmp_path):
    assert "\x1b[" in _run("--help", env={"FORCE_COLOR": "1"}).stdout
    r = _run("scan", str(tmp_path / "missing"), env={"FORCE_COLOR": "1"})
    assert "\x1b[" in r.stderr


def test_no_color_beats_force_color():
    r = _run("--help", env={"FORCE_COLOR": "1", "NO_COLOR": "1"})
    assert "\x1b" not in r.stdout


def test_piped_scan_has_no_carriage_return_progress(library):
    r = _run("scan", str(library))
    assert r.returncode == 0
    assert "\r" not in r.stdout
    assert "Scanning..." not in r.stdout
    assert "Done!" in r.stdout


# whole program on a real terminal (POSIX only)

def _run_on_tty(*args, env=None) -> str:
    if sys.platform == "win32":   # also tells mypy the pty calls below are POSIX-only
        raise NotImplementedError("needs a POSIX pty")
    import pty

    base = {k: v for k, v in os.environ.items()
            if k not in ("NO_COLOR", "FORCE_COLOR")}
    base["TERM"] = "xterm"
    base.update(env or {})
    master, slave = pty.openpty()
    try:
        p = subprocess.Popen(
            [sys.executable, str(ROOT / "aevum.py"), *args],
            stdin=slave, stdout=slave, stderr=slave, env=base, cwd=ROOT,
            close_fds=True)
        os.close(slave)
        chunks = []
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:      # Linux: EIO once the child has exited
                break
            if not data:
                break
            chunks.append(data)
        p.wait(timeout=120)
    finally:
        os.close(master)
    return b"".join(chunks).decode("utf-8", "replace")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_terminal_gets_colors_and_in_place_progress(library):
    out = _run_on_tty("scan", str(library))
    assert "\x1b[" in out
    assert "\r" in out and "Scanning..." in out


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
@pytest.mark.parametrize("env", [{"NO_COLOR": "1"}, {"TERM": "dumb"}])
def test_terminal_respects_no_color_and_dumb(env):
    assert "\x1b" not in _run_on_tty("--help", env=env)


def test_every_tree_depth_color_exists():
    # print_tree picks its color by name, so a missing code would crash a scan.
    from aevum_pkg._display import _DEPTH_ATTRS
    for name in _DEPTH_ATTRS:
        assert name in _Colors.__slots__
