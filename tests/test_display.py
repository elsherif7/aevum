"""Output helpers: sanitising, fuzzy suggestions, and the bar."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aevum_pkg._display import (
    _bar,
    _fuzzy_suggest,
    _safe,
    print_bar_chart,
    print_results,
    print_tree,
    print_url_results,
)
from aevum_pkg._models import FolderNode, ScanTree


def test_safe_strips_ansi_and_control_characters():
    assert _safe("\x1b[31mred\x1b[0m") == "red"
    assert _safe("a\x00b\x07c\nd") == "abcd"


def test_safe_truncates_long_names():
    assert len(_safe("x" * 500)) == 200


@pytest.mark.parametrize("word, candidates, expected", [
    ("Movies", ["Movis", "Music", "Docs"], "Movis"),
    ("Downloads", ["Dowloads", "Desktop"], "Dowloads"),
    ("Movies", ["Completely", "Different"], None),
])
def test_fuzzy_suggest(word, candidates, expected):
    assert _fuzzy_suggest(word, candidates) == expected


def test_fuzzy_suggest_skips_huge_candidate_lists():
    assert _fuzzy_suggest("Movies", [f"Movie{i}" for i in range(100)]) is None


def test_bar_empty_when_total_is_zero():
    assert _bar(5, 0) == ""


def test_bar_shows_percentage():
    assert "50.0%" in _bar(50, 100)
    assert "100.0%" in _bar(500, 100)  # ratio is capped at 100%


# _safe: everything that could drive the terminal is removed

@pytest.mark.parametrize("hostile, expected", [
    ("a\x1b[31mred\x1b[0mb", "aredb"),               # colour (CSI ... m)
    ("a\x1b[2Jb", "ab"),                             # clear screen
    ("a\x1b[?25lb", "ab"),                           # private CSI (hide cursor)
    ("a\x1b[1;31;4mb", "ab"),                        # several parameters
    ("a\x1b]0;PWNED\x07b", "ab"),                    # OSC set window title, BEL-terminated
    ("a\x1b]0;PWNED\x1b\\b", "ab"),                  # OSC, ST-terminated
    ("a\x1bcb", "ab"),                               # ESC c = full terminal reset
    ("a\x1b(0b", "ab"),                              # switch to line-drawing charset
    ("a\x1b#8b", "ab"),                              # DEC screen alignment test
    ("a\x1bb", "a"),                                 # ESC + letter is itself a sequence
    ("a\x1b", "a"),                                  # trailing ESC
    ("a\u009b31mb", "a31mb"),                        # C1 CSI character
    ("a\u0085b\u009cb", "abb"),                      # other C1 controls
    ("a\x00b\x07c\x08d\x7fe", "abcde"),             # NUL, BEL, backspace, DEL
    ("line1\nline2\r\tend", "line1line2end"),        # newlines and tabs
    ("evil\u202etxt.4pm", "eviltxt.4pm"),            # right-to-left override
    ("a\u2066b\u2069c", "abc"),                      # isolates
])
def test_safe_removes_terminal_control_sequences(hostile, expected):
    assert _safe(hostile) == expected


def test_safe_keeps_normal_unicode():
    assert _safe("日本語 – café – 🎬") == "日本語 – café – 🎬"
    assert _safe("שלום") == "שלום"                 # RTL text without explicit overrides


def test_safe_replaces_unencodable_surrogates():
    # what Python produces for a file name containing invalid UTF-8 bytes
    out = _safe("bad-\udcff-name")
    assert out == "bad-?-name"
    out.encode("utf-8")                            # must not raise


def test_safe_accepts_non_strings():
    assert _safe(42) == "42"
    assert _safe(ValueError("x\x1b[2J")) == "x"


# Every place a name or title is printed goes through _safe

_OWN_COLOURS = re.compile(r"\x1b\[[0-9;]*m")
HOSTILE = "a\x1b[31mred\x1b]0;PWNED\x07\x1b[2J\u009bb\u202ec"


def _assert_clean(out: str):
    assert "a\x1b[31mred" not in out, "a hostile colour code reached the terminal"
    rest = _OWN_COLOURS.sub("", out)
    for ch in ("\x1b", "\x07", "\u009b", "\u202e"):
        assert ch not in rest, f"{ch!r} reached the terminal"


def _node(name: str, children=None) -> FolderNode:
    return FolderNode(name, 60.0, 1, 1000, children or [], [])


def test_print_tree_sanitises_folder_names(capsys):
    child = _node(HOSTILE)
    print_tree("root", 60.0, 1, [child], [])
    _assert_clean(capsys.readouterr().out)


def test_print_bar_chart_sanitises_labels(capsys):
    print_bar_chart([_node(HOSTILE), _node("other")], 120.0)
    _assert_clean(capsys.readouterr().out)


def test_print_results_sanitises_header_and_tree(capsys, tmp_path):
    tree = ScanTree([_node(HOSTILE)], [], 1000)
    print_results(tmp_path / HOSTILE.replace("/", "_"), 60.0, 1, tree,
                  {Path(HOSTILE.replace("/", "_")) / "x.mp4": 60.0})
    _assert_clean(capsys.readouterr().out)


def test_print_url_results_sanitises_everything_from_the_network(capsys):
    entries = [
        {"title": HOSTILE, "duration": 120.0, "channel": HOSTILE},
        {"title": "ok", "duration": 60.0, "channel": "Other"},
    ]
    print_url_results("https://youtu.be/x" + HOSTILE, HOSTILE, 180.0, 2, entries)
    _assert_clean(capsys.readouterr().out)


def test_print_url_results_survives_odd_cached_titles(capsys):
    entries = [{"title": 12345, "duration": 60.0, "channel": ["not", "a", "string"]}]
    print_url_results("https://youtu.be/x", "label", 60.0, 1, entries)
    assert "12345" in capsys.readouterr().out
