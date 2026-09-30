"""Output helpers: sanitising, fuzzy suggestions, and the bar."""
from __future__ import annotations

import pytest

from aevum_pkg._display import _bar, _fuzzy_suggest, _safe


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
