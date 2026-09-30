"""YouTube helpers that need no network or API key."""
from __future__ import annotations

import json

import pytest

from aevum_pkg import _youtube as yt

# ---------------------------------------------------------------------------
# ISO 8601 duration parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, seconds", [
    ("PT45S", 45),
    ("PT5M", 300),
    ("PT1H", 3600),
    ("PT1H2M3S", 3723),
    ("PT10M30S", 630),
    ("PT0S", 0),
])
def test_parse_iso8601_duration(raw, seconds):
    assert yt._parse_iso8601_duration(raw) == seconds


@pytest.mark.parametrize("raw", ["", None, "garbage", "1H"])
def test_parse_iso8601_duration_bad_input_is_zero(raw):
    assert yt._parse_iso8601_duration(raw) == 0.0


@pytest.mark.xfail(strict=True, reason="known bug: durations over 24 h ('P1DT...') parse as 0")
def test_parse_iso8601_duration_with_days():
    assert yt._parse_iso8601_duration("P1DT2H") == 26 * 3600


# ---------------------------------------------------------------------------
# URL detection and parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "https://youtube.com/watch?v=abc",
    "http://youtu.be/abc",
    "www.youtube.com/@somechannel",
    "youtube.com/playlist?list=PL123",
    "music.youtube.com/watch?v=abc",
])
def test_is_url_true(value):
    assert yt._is_url(value)


@pytest.mark.parametrize("value", ["D:\\Movies", "/home/user/Videos", "Movies", "."])
def test_is_url_false_for_paths(value):
    assert not yt._is_url(value)


def test_normalise_url_adds_scheme():
    assert yt._normalise_url("youtube.com/x") == "https://youtube.com/x"
    assert yt._normalise_url("http://youtube.com/x") == "http://youtube.com/x"


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ("https://youtu.be/dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ("https://www.youtube.com/shorts/abc123", ("video", "abc123")),
    ("https://www.youtube.com/playlist?list=PL123", ("playlist", "PL123")),
    ("https://www.youtube.com/@somechannel", ("channel_handle", "@somechannel")),
    ("https://www.youtube.com/c/SomeName", ("channel_handle", "SomeName")),
    ("https://www.youtube.com/user/SomeName", ("channel_handle", "SomeName")),
    ("https://www.youtube.com/channel/UCabc123", ("channel_id", "UCabc123")),
    ("https://music.youtube.com/watch?v=abc", ("video", "abc")),
])
def test_parse_yt_url(url, expected):
    assert yt._parse_yt_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://example.com/watch?v=abc",
    "https://www.youtube.com/",
    "https://www.youtube.com/feed/trending",
])
def test_parse_yt_url_rejects_unknown(url):
    assert yt._parse_yt_url(url) == (None, None)


# ---------------------------------------------------------------------------
# API key storage (redirected into tmp_path, never the real data dir)
# ---------------------------------------------------------------------------

VALID_KEY = "AIza" + "x" * 35


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "yt_api_key.txt"
    monkeypatch.setattr(yt, "YT_KEY_FILE", path)
    return path


def test_api_key_round_trip(key_file):
    assert yt.load_api_key() == ""          # nothing saved yet
    assert yt.save_api_key(VALID_KEY) is True
    assert yt.load_api_key() == VALID_KEY


@pytest.mark.parametrize("bad", ["", "not-a-key", "AIza" + "x" * 10, "BIza" + "x" * 35])
def test_save_api_key_rejects_bad_format(key_file, bad):
    assert yt.save_api_key(bad) is False
    assert not key_file.exists()


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX permissions only")
def test_saved_key_is_owner_only(key_file):
    yt.save_api_key(VALID_KEY)
    assert key_file.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Quota tracker and video cache
# ---------------------------------------------------------------------------

@pytest.fixture
def quota_file(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    monkeypatch.setattr(yt, "YT_QUOTA_FILE", path)
    return path


def test_quota_accumulates_and_reports(quota_file):
    assert yt._add_quota_usage(5) == 5
    assert yt._add_quota_usage(7) == 12
    used, remaining, pct = yt.get_quota_status()
    assert (used, remaining) == (12, yt.YT_QUOTA_DAILY_LIMIT - 12)
    assert pct == pytest.approx(0.12)


def test_quota_resets_on_a_new_day(quota_file):
    quota_file.write_text(json.dumps({"date": "2000-01-01", "units_used": 9000}))
    assert yt.get_quota_status()[0] == 0


@pytest.mark.parametrize("content", ["not json", '{"units_used": "lots"}', "[]"])
def test_quota_corrupt_file_is_treated_as_zero(quota_file, content):
    quota_file.write_text(content)
    assert yt._load_quota_tracker()[1] == 0


def test_quota_units_are_clamped_to_daily_limit(quota_file):
    quota_file.write_text(json.dumps({"date": "x", "units_used": 10**9}))
    assert yt._load_quota_tracker()[1] == yt.YT_QUOTA_DAILY_LIMIT


def test_video_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(yt, "YT_VCACHE_FILE", tmp_path / "cache.json")
    assert yt._load_yt_video_cache() == {}
    cache: dict = {}
    yt._merge_into_cache(cache, {"abc": {"id": "abc", "title": "T", "duration": 60.0}})
    loaded = yt._load_yt_video_cache()
    assert loaded["abc"]["title"] == "T"
    assert "cached_at" in loaded["abc"]


def test_video_cache_corrupt_file_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text("{oops")
    monkeypatch.setattr(yt, "YT_VCACHE_FILE", path)
    assert yt._load_yt_video_cache() == {}
