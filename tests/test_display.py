"""Output helpers: sanitising, fuzzy suggestions, and the bar."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aevum_pkg._display import (
    _bar,
    _fuzzy_suggest,
    print_bar_chart,
    print_results,
    print_tree,
    print_url_results,
)
from aevum_pkg._models import FolderNode, ScanTree
from aevum_pkg._scan import _build_tree
from aevum_pkg._text import _safe, warn


def test_safe_strips_ansi_and_control_characters():
    assert _safe("\x1b[31mred\x1b[0m") == "red"
    assert _safe("a\x00b\x07c\nd") == "abc d"


def test_safe_truncates_long_names():
    assert len(_safe("x" * 500)) == 200


@pytest.mark.parametrize("word, candidates, expected", [
    ("Movies", ["Movis", "Music", "Docs"], "Movis"),
    ("Downloads", ["Dowloads", "Desktop"], "Dowloads"),
    ("Movies", ["Completely", "Different"], None),
])
def test_fuzzy_suggest(word, candidates, expected):
    assert _fuzzy_suggest(word, candidates) == expected


@pytest.mark.parametrize("word, candidate, expected", [
    ("Mo", "Mp", "Mp"),                  # one edit is fine for a short name
    ("Mo", "Xy", None),                  # two edits is not
    ("Docs", "Doc", "Doc"),
    ("Docs", "Dcos", None),              # a transposition is two edits
    ("Download", "Downlaod", "Downlaod"),
    ("Download", "Dxwnlxax", None),      # three edits
])
def test_fuzzy_suggest_distance_depends_on_name_length(word, candidate, expected):
    assert _fuzzy_suggest(word, [candidate]) == expected


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
    ("line1\nline2\r\tend", "line1 line2  end"),     # newlines and tabs become spaces
    ("a\u2028b\u2029c", "a b c"),                    # Unicode line and paragraph separators
    ("a\u200eb\u200fc\u061cd", "abcd"),              # invisible direction marks
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


def test_warn_sanitises_and_goes_to_stderr(capsys):
    warn("bad\x1b[2J\nname")
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "  [WARN] bad name\n"


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


# bar chart, tree sizes and ordering

def test_bar_chart_needs_at_least_two_rows(capsys):
    print_bar_chart([_node("only")], 60.0)
    assert capsys.readouterr().out == ""

    print_bar_chart([_node("a"), _node("b")], 120.0)
    assert "Duration Breakdown" in capsys.readouterr().out

    print_bar_chart([_node("a")], 120.0, [(Path("/r/x.mp4"), 60.0)])   # a folder plus root files
    assert "Duration Breakdown" in capsys.readouterr().out


def test_results_with_a_single_group_have_no_breakdown(capsys, tmp_path):
    tree = ScanTree([], [(tmp_path / "x.mp4", 60.0)], 1000, 1000)
    print_results(tmp_path, 60.0, 1, tree, {tmp_path / "x.mp4": 60.0})
    out = capsys.readouterr().out
    assert "Duration Breakdown" not in out
    assert "Grand Total" in out


def test_tree_shows_root_file_size_without_touching_the_disk(capsys):
    root = Path("/does/not/exist/root")
    durations = {root / "a.mp4": 10.0, root / "sub" / "b.mp4": 20.0}
    sizes = {root / "a.mp4": 3072, root / "sub" / "b.mp4": 2048}
    tree = _build_tree(root, durations, sizes)
    assert tree.direct_bytes == 3072
    assert tree.children[0].direct_bytes == 2048
    assert tree.root_bytes == 5120

    print_tree("root", 30.0, 2, tree.children, tree.direct_files,
               fbytes=tree.root_bytes, direct_bytes=tree.direct_bytes)
    out = capsys.readouterr().out
    assert "(no folder)" in out
    assert "3.0 KB" in out


def test_models_default_direct_bytes_to_zero():
    assert FolderNode("n", 1.0, 1, 1, [], []).direct_bytes == 0
    assert ScanTree([], [], 0).direct_bytes == 0


def test_folders_that_differ_only_by_case_keep_a_stable_order():
    root = Path("/r")
    files = [(root / "a" / "x.mp4", 1.0), (root / "A" / "y.mp4", 1.0),
             (root / "b" / "z.mp4", 1.0), (root / "B" / "w.mp4", 1.0)]
    orders = set()
    for items in (files, files[::-1], files[1:] + files[:1]):
        tree = _build_tree(root, dict(items))
        orders.add(tuple(c.name for c in tree.children))
    assert orders == {("A", "a", "B", "b")}
