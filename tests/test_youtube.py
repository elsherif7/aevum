"""YouTube helpers that need no network or API key."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from aevum_pkg import _youtube as yt


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Keep every state file (key, cache, quota, rate limiter) inside tmp_path."""
    monkeypatch.setattr(yt, "YT_KEY_FILE", tmp_path / "state" / "key.txt")
    monkeypatch.setattr(yt, "YT_QUOTA_FILE", tmp_path / "state" / "quota.json")
    monkeypatch.setattr(yt, "YT_VCACHE_FILE", tmp_path / "state" / "cache.json")
    limiter = yt._RateLimiter(max_calls=100, time_window=3600)
    limiter._state_file = tmp_path / "state" / "ratelimit.json"
    monkeypatch.setattr(yt, "youtube_limiter", limiter)

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


@pytest.mark.parametrize("raw, seconds", [
    ("P1DT2H", 26 * 3600),
    ("P2DT3H4M5S", 2 * 86400 + 3 * 3600 + 4 * 60 + 5),
    ("P1D", 86400),
    ("P0D", 0),
])
def test_parse_iso8601_duration_with_days(raw, seconds):
    assert yt._parse_iso8601_duration(raw) == seconds


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
    # a video link copied from inside a playlist means that one video
    ("https://www.youtube.com/watch?v=abc&list=PL123", ("video", "abc")),
    ("https://youtu.be/abc?list=PL123", ("video", "abc")),
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


# ---------------------------------------------------------------------------
# scan_url: order of checks
# ---------------------------------------------------------------------------

def test_scan_url_rejects_bad_url_before_prompting_for_a_key(monkeypatch):
    monkeypatch.setattr(yt, "load_api_key", lambda: "")

    def must_not_prompt():
        raise AssertionError("asked for an API key for an invalid URL")

    monkeypatch.setattr(yt, "prompt_api_key", must_not_prompt)
    with pytest.raises(ValueError, match="Could not parse"):
        yt.scan_url("https://example.com/watch?v=abc")


@pytest.mark.parametrize("answer", [None, ""])
def test_scan_url_raises_when_the_key_prompt_is_cancelled(monkeypatch, answer):
    monkeypatch.setattr(yt, "load_api_key", lambda: "")
    monkeypatch.setattr(yt, "prompt_api_key", lambda: answer)
    with pytest.raises(yt.ApiKeyCancelled):
        yt.scan_url("https://youtu.be/dQw4w9WgXcQ")


def test_has_playlist_param():
    assert yt._has_playlist_param("https://www.youtube.com/watch?v=abc&list=PL1")
    assert yt._has_playlist_param("youtu.be/abc?list=PL1")
    assert not yt._has_playlist_param("https://www.youtube.com/watch?v=abc")


# ---------------------------------------------------------------------------
# A fake YouTube API (no network)
# ---------------------------------------------------------------------------

class FakeApi:
    """
    Stands in for yt._yt_api_request.

    unavailable: video ids the API silently leaves out of its answer
    fail_after:  raise YouTubeLimitError on every call after this many calls
    channels:    {(param, value): channel item} for the 'channels' endpoint
    """
    def __init__(self, unavailable=(), fail_after=None, channels=None):
        self.unavailable = set(unavailable)
        self.fail_after  = fail_after
        self.channels    = channels or {}
        self.calls       = []          # (endpoint, params)

    def __call__(self, endpoint, params, api_key, quota_cost=None):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise yt.YouTubeLimitError("Hourly request limit reached.", kind="rate", retry_after=600)
        self.calls.append((endpoint, dict(params)))
        if endpoint == "videos":
            ids = params["id"].split(",")
            return {"items": [
                {
                    "id": v,
                    "snippet": {"title": f"Title {v}", "channelTitle": "Chan"},
                    "contentDetails": {"duration": "PT1M"},
                }
                for v in ids if v not in self.unavailable
            ]}
        if endpoint == "channels":
            item = self.channels.get((params.get("forHandle") and "forHandle" or
                                      params.get("forUsername") and "forUsername" or "id",
                                      params.get("forHandle") or params.get("forUsername") or params.get("id")))
            return {"items": [item] if item else []}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    def video_calls(self):
        return [c for c in self.calls if c[0] == "videos"]


def _ids(n):
    return [f"v{i:04d}" for i in range(n)]


@pytest.fixture
def fake_api(monkeypatch):
    def install(**kw):
        api = FakeApi(**kw)
        monkeypatch.setattr(yt, "_yt_api_request", api)
        return api
    return install


# ---------------------------------------------------------------------------
# Progress survives a rate/quota stop
# ---------------------------------------------------------------------------

def test_batches_fetched_before_a_limit_are_saved_and_not_refetched(fake_api):
    ids = _ids(230)                        # 5 batches of 50
    fake_api(fail_after=2)                 # 2 batches succeed, the 3rd is refused

    cache = {}
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._fetch_with_cache(ids, "key", cache)
    assert (exc.value.saved, exc.value.total) == (100, 230)

    # the work is on disk, not just in memory
    on_disk = yt._load_yt_video_cache()
    assert set(on_disk) == set(ids[:100])

    # a rerun only asks for what is missing: 130 videos = 3 batches
    api = fake_api()
    entries, hits, unavailable = yt._fetch_with_cache(ids, "key", yt._load_yt_video_cache())
    assert len(api.video_calls()) == 3
    assert (len(entries), hits, unavailable) == (230, 100, [])


def test_limit_error_reports_kind_and_wait(fake_api):
    fake_api(fail_after=0)
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._fetch_with_cache(_ids(10), "key", {})
    assert exc.value.kind == "rate" and exc.value.retry_after == 600


def test_progress_is_saved_when_interrupted_by_ctrl_c(monkeypatch):
    calls = {"n": 0}

    def api(endpoint, params, api_key, quota_cost=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return {"items": [
            {"id": v, "snippet": {"title": v, "channelTitle": ""}, "contentDetails": {"duration": "PT1M"}}
            for v in params["id"].split(",")
        ]}

    monkeypatch.setattr(yt, "_yt_api_request", api)
    with pytest.raises(KeyboardInterrupt):
        yt._fetch_with_cache(_ids(200), "key", {})
    assert len(yt._load_yt_video_cache()) == 100


def test_persist_false_never_touches_the_cache_file(fake_api):
    fake_api()
    yt._fetch_with_cache(_ids(10), "key", {}, persist=False)
    assert not yt.YT_VCACHE_FILE.exists()


# ---------------------------------------------------------------------------
# Unavailable videos are remembered (for a while)
# ---------------------------------------------------------------------------

def test_unavailable_videos_are_cached_and_not_requested_again(fake_api):
    ids = _ids(10)
    gone = {ids[3], ids[7]}
    api = fake_api(unavailable=gone)

    entries, hits, unavailable = yt._fetch_with_cache(ids, "key", yt._load_yt_video_cache())
    assert len(entries) == 8 and hits == 0 and set(unavailable) == gone
    assert len(api.video_calls()) == 1

    # second run: no API calls at all, same answer
    api2 = fake_api(unavailable=gone)
    entries, hits, unavailable = yt._fetch_with_cache(ids, "key", yt._load_yt_video_cache())
    assert api2.video_calls() == []
    assert len(entries) == 8 and hits == 8 and set(unavailable) == gone


def test_stale_unavailable_stub_is_checked_again(fake_api):
    ids = _ids(3)
    old = int(__import__("time").time()) - yt.UNAVAILABLE_TTL - 10
    cache = {ids[1]: {"id": ids[1], "unavailable": True, "cached_at": old}}
    api = fake_api()                       # the video is available now

    entries, hits, unavailable = yt._fetch_with_cache(ids, "key", cache)
    assert len(entries) == 3 and unavailable == []
    assert len(api.video_calls()) == 1


def test_cache_state():
    now = 1_000_000
    cache = {
        "ok":    {"title": "T", "duration": 5},
        "fresh": {"unavailable": True, "cached_at": now - 10},
        "stale": {"unavailable": True, "cached_at": now - yt.UNAVAILABLE_TTL - 1},
    }
    assert yt._cache_state(cache, "ok", now) == "hit"
    assert yt._cache_state(cache, "fresh", now) == "unavailable"
    assert yt._cache_state(cache, "stale", now) == "miss"
    assert yt._cache_state(cache, "nope", now) == "miss"


def test_single_video_scan_uses_the_cache(fake_api, monkeypatch):
    monkeypatch.setattr(yt, "load_api_key", lambda: "AIza" + "x" * 35)
    api = fake_api()
    first = yt.scan_url("https://youtu.be/abc12345678")
    second = yt.scan_url("https://youtu.be/abc12345678")
    assert first[3] == "Title abc12345678"           # label
    assert len(api.video_calls()) == 1               # second scan came from the cache
    assert second[4] == 1                            # cache_hits


# ---------------------------------------------------------------------------
# Limit detection in the real request function
# ---------------------------------------------------------------------------

def _http_error(code, reason, message="boom"):
    body = json.dumps({"error": {"message": message, "errors": [{"reason": reason}]}}).encode()
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


@pytest.mark.parametrize("code, reason", [
    (403, "quotaExceeded"),
    (403, "dailyLimitExceeded"),
    (403, "rateLimitExceeded"),
    (429, "whatever"),
])
def test_quota_and_rate_http_errors_become_limit_errors(monkeypatch, code, reason):
    def boom(*a, **k):
        raise _http_error(code, reason)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert exc.value.kind == "quota"


def test_other_http_errors_stay_plain_errors(monkeypatch):
    def boom(*a, **k):
        raise _http_error(403, "keyInvalid", "API key not valid")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="API key not valid") as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert not isinstance(exc.value, yt.YouTubeLimitError)
    assert "key=" not in str(exc.value)              # the API key never appears in errors


def test_local_hourly_limit_is_a_limit_error(monkeypatch):
    monkeypatch.setattr(yt.youtube_limiter, "allow_request", lambda: False)
    monkeypatch.setattr(yt.youtube_limiter, "wait_time", lambda: 125.0)
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert exc.value.kind == "rate" and exc.value.retry_after == 125.0
    assert isinstance(exc.value, PermissionError)    # old `except PermissionError` still works


# ---------------------------------------------------------------------------
# Channel lookup
# ---------------------------------------------------------------------------

CHANNEL = {
    "snippet": {"title": "My Channel"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
}


def _lookup_params(api):
    return [{k: v for k, v in p.items() if k != "part"} for _, p in api.calls]


def test_channel_id_uses_one_id_lookup(fake_api):
    api = fake_api(channels={("id", "UCabc"): CHANNEL})
    assert yt._yt_get_channel_uploads_playlist("UCabc", "key", "channel_id") == ("UUabc", "My Channel")
    assert _lookup_params(api) == [{"id": "UCabc"}]


def test_at_handle_uses_one_forhandle_lookup(fake_api):
    api = fake_api(channels={("forHandle", "@me"): CHANNEL})
    assert yt._yt_get_channel_uploads_playlist("@me", "key", "channel_handle")[0] == "UUabc"
    assert _lookup_params(api) == [{"forHandle": "@me"}]


def test_legacy_name_falls_back_to_forusername(fake_api):
    api = fake_api(channels={("forUsername", "OldName"): CHANNEL})
    assert yt._yt_get_channel_uploads_playlist("OldName", "key", "channel_handle")[0] == "UUabc"
    assert _lookup_params(api) == [{"forHandle": "OldName"}, {"forUsername": "OldName"}]


def test_missing_channel_returns_none(fake_api):
    fake_api()
    assert yt._yt_get_channel_uploads_playlist("@nobody", "key", "channel_handle") == (None, None)


def test_channel_lookup_does_not_swallow_limit_errors(fake_api):
    fake_api(fail_after=0)
    with pytest.raises(yt.YouTubeLimitError):
        yt._yt_get_channel_uploads_playlist("@me", "key", "channel_handle")


def test_scan_url_reports_unknown_channel(fake_api, monkeypatch):
    monkeypatch.setattr(yt, "load_api_key", lambda: "AIza" + "x" * 35)
    fake_api()
    with pytest.raises(ValueError, match="Could not find channel"):
        yt.scan_url("https://www.youtube.com/@nobody")
