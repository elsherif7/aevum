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
    """Keep every state file (key, cache, quota) inside tmp_path."""
    monkeypatch.setattr(yt, "YT_KEY_FILE", tmp_path / "state" / "key.txt")
    monkeypatch.setattr(yt, "YT_QUOTA_FILE", tmp_path / "state" / "quota.json")
    monkeypatch.setattr(yt, "YT_VCACHE_FILE", tmp_path / "state" / "cache.json")

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


class _FakeResponse:
    """What urllib.request.urlopen returns, as a context manager."""
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


@pytest.fixture
def sleeps(monkeypatch):
    """Record time.sleep calls instead of actually waiting."""
    waited = []
    monkeypatch.setattr(yt.time, "sleep", waited.append)
    return waited


def _scripted_urlopen(monkeypatch, *outcomes):
    """urlopen that yields each outcome in turn: an exception is raised, a dict is returned."""
    calls = []
    script = list(outcomes)

    def fake(*a, **k):
        calls.append(1)
        outcome = script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


@pytest.mark.parametrize("code, reason", [
    (403, "quotaExceeded"),
    (403, "dailyLimitExceeded"),
])
def test_daily_quota_errors_stop_at_once_without_retrying(monkeypatch, sleeps, code, reason):
    calls = _scripted_urlopen(monkeypatch, _http_error(code, reason))
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert exc.value.kind == "quota"
    assert len(calls) == 1 and sleeps == []            # waiting would not help until midnight PT


@pytest.mark.parametrize("code, reason", [
    (429, "rateLimitExceeded"),
    (429, "whatever"),
    (403, "rateLimitExceeded"),
    (403, "userRateLimitExceeded"),
])
def test_rate_limit_errors_are_retried_with_backoff_then_succeed(monkeypatch, sleeps, code, reason):
    calls = _scripted_urlopen(
        monkeypatch, _http_error(code, reason), _http_error(code, reason), {"items": ["ok"]})
    assert yt._yt_api_request("videos", {"id": "x"}, "key") == {"items": ["ok"]}
    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_rate_limit_that_never_clears_gives_up_with_a_rate_error(monkeypatch, sleeps):
    errors = [_http_error(429, "rateLimitExceeded") for _ in range(4)]
    calls = _scripted_urlopen(monkeypatch, *errors)
    with pytest.raises(yt.YouTubeLimitError) as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert exc.value.kind == "rate"
    assert len(calls) == 4 and sleeps == [1, 2, 4]     # first try + 3 retries


def test_retry_after_header_is_honoured_but_capped(monkeypatch, sleeps):
    from email.message import Message

    def with_header(value):
        hdrs = Message()
        hdrs["Retry-After"] = value
        body = json.dumps({"error": {"message": "slow down",
                                     "errors": [{"reason": "rateLimitExceeded"}]}}).encode()
        return urllib.error.HTTPError("https://x", 429, "err", hdrs, io.BytesIO(body))

    _scripted_urlopen(monkeypatch, with_header("7"), with_header("5000"),
                      with_header("Wed, 21 Oct 2026 07:28:00 GMT"), {"items": []})
    yt._yt_api_request("videos", {"id": "x"}, "key")
    assert sleeps == [7, 60, 4]       # 7 s as told; 5000 capped to 60; a date is ignored -> backoff


def test_other_http_errors_stay_plain_errors_and_are_not_retried(monkeypatch, sleeps):
    calls = _scripted_urlopen(monkeypatch, _http_error(403, "keyInvalid", "API key not valid"))
    with pytest.raises(RuntimeError, match="API key not valid") as exc:
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert not isinstance(exc.value, yt.YouTubeLimitError)
    assert "key=" not in str(exc.value)              # the API key never appears in errors
    assert len(calls) == 1 and sleeps == []


def test_a_server_error_without_json_is_a_plain_error(monkeypatch, sleeps):
    err = urllib.error.HTTPError("https://x", 500, "oops", {}, io.BytesIO(b"<html>"))
    _scripted_urlopen(monkeypatch, err)
    with pytest.raises(RuntimeError, match="500"):
        yt._yt_api_request("videos", {"id": "x"}, "key")


def test_there_is_no_local_request_limit(monkeypatch, tmp_path):
    # Aevum used to refuse requests after 100 per hour. YouTube's own quota and
    # rate-limit responses are the only limits now, and nothing is written to disk
    # to count requests.
    assert not hasattr(yt, "youtube_limiter") and not hasattr(yt, "_RateLimiter")
    calls = _scripted_urlopen(monkeypatch, *[{"items": []}] * 300)
    for _ in range(300):
        yt._yt_api_request("videos", {"id": "x"}, "key")
    assert len(calls) == 300
    assert not list((tmp_path / "state").glob("*ratelimit*"))


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


# ---------------------------------------------------------------------------
# API key file: private from the first byte
# ---------------------------------------------------------------------------

posix_only = pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX permissions only")


def _mode(path):
    return path.stat().st_mode & 0o777


@posix_only
def test_key_file_is_private_even_with_a_permissive_umask(key_file):
    import os
    old = os.umask(0)                      # worst case: everything would be 0666
    try:
        assert yt.save_api_key(VALID_KEY)
    finally:
        os.umask(old)
    assert _mode(key_file) == 0o600


@posix_only
def test_key_file_does_not_depend_on_a_later_chmod(key_file, monkeypatch):
    # The old code wrote with default permissions and then called chmod, leaving a
    # window where the key was readable. Now chmod is not needed at all.
    import os
    monkeypatch.setattr(os, "chmod", lambda *a, **k: (_ for _ in ()).throw(AssertionError("chmod used")))
    old = os.umask(0)
    try:
        assert yt.save_api_key(VALID_KEY)
    finally:
        os.umask(old)
    assert _mode(key_file) == 0o600


@posix_only
def test_saving_over_a_world_readable_key_file_makes_it_private(key_file):
    key_file.parent.mkdir(parents=True)
    key_file.write_text("old")
    key_file.chmod(0o644)
    assert yt.save_api_key(VALID_KEY)
    assert _mode(key_file) == 0o600
    assert yt.load_api_key() == VALID_KEY


@posix_only
def test_new_data_directory_is_owner_only(key_file):
    assert yt.save_api_key(VALID_KEY)
    assert _mode(key_file.parent) == 0o700


def test_failed_save_leaves_no_temp_file_and_no_key(key_file, monkeypatch):
    import os

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    assert yt.save_api_key(VALID_KEY) is False
    assert list(key_file.parent.iterdir()) == []         # nothing left behind
    assert not key_file.exists()


def test_key_is_never_printed_or_in_error_messages(monkeypatch, capsys):
    import urllib.error
    import urllib.request

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as exc:
        yt._yt_api_request("videos", {"id": "x"}, VALID_KEY)
    assert VALID_KEY not in str(exc.value)
    assert VALID_KEY not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Pacific date without a time zone database
# ---------------------------------------------------------------------------

def _zone():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo("America/Los_Angeles")
    except Exception:
        pytest.skip("no time zone database on this system")


def test_pacific_fallback_matches_the_real_time_zone_database():
    import datetime
    zone = _zone()
    utc = datetime.datetime(2025, 1, 1)
    end = datetime.datetime(2029, 1, 1)
    step = datetime.timedelta(minutes=30)
    bad = []
    while utc < end:
        expected = utc.replace(tzinfo=datetime.timezone.utc).astimezone(zone).strftime("%Y-%m-%d")
        if yt._pacific_date_without_tzdata(utc) != expected:
            bad.append(utc)
        utc += step
    assert not bad, f"{len(bad)} mismatches, first at {bad[0]}"


@pytest.mark.parametrize("utc, expected", [
    # The quota resets at midnight Pacific: 07:00 UTC in summer (PDT), 08:00 UTC in winter (PST).
    ((2026, 7, 1, 6, 59), "2026-06-30"),
    ((2026, 7, 1, 7, 0), "2026-07-01"),
    ((2026, 12, 1, 7, 59), "2026-11-30"),
    ((2026, 12, 1, 8, 0), "2026-12-01"),
    # Around the 2026 clock changes (8 March and 1 November) the date is unaffected.
    ((2026, 3, 8, 9, 59), "2026-03-08"),
    ((2026, 3, 8, 10, 0), "2026-03-08"),
    ((2026, 11, 1, 8, 59), "2026-11-01"),
    ((2026, 11, 1, 9, 0), "2026-11-01"),
])
def test_pacific_fallback_known_moments(utc, expected):
    import datetime
    assert yt._pacific_date_without_tzdata(datetime.datetime(*utc)) == expected


def test_current_pt_date_works_when_the_time_zone_database_is_missing(monkeypatch):
    import datetime
    import zoneinfo

    def missing(*a, **k):
        raise zoneinfo.ZoneInfoNotFoundError("America/Los_Angeles")

    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)

    def now():
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    before = yt._pacific_date_without_tzdata(now())
    got = yt._get_current_date_pt()
    after = yt._pacific_date_without_tzdata(now())
    assert got in (before, after)


# ---------------------------------------------------------------------------
# A damaged or tampered cache file cannot crash a scan
# ---------------------------------------------------------------------------

GOOD = {"title": "T", "duration": 60.0, "cached_at": 1}


@pytest.mark.parametrize("entry, ok", [
    (GOOD, True),
    ({"title": "T", "duration": 60}, True),                                 # no cached_at (older cache)
    ({"unavailable": True, "cached_at": 5}, True),
    ("a string", False),
    ([1, 2], False),
    (None, False),
    ({**GOOD, "title": 5}, False),                                          # title not a string
    ({**GOOD, "duration": "60"}, False),
    ({**GOOD, "duration": float("nan")}, False),
    ({**GOOD, "duration": float("inf")}, False),
    ({**GOOD, "duration": -1}, False),
    ({**GOOD, "duration": 10**12}, False),                                  # absurdly long
    ({**GOOD, "duration": True}, False),
    ({"unavailable": True, "cached_at": "yesterday"}, False),
    ({**GOOD, "cached_at": float("nan")}, False),
])
def test_valid_cache_entry(entry, ok):
    assert yt._valid_cache_entry(entry) is ok


def test_loading_drops_bad_entries_and_keeps_good_ones():
    yt.YT_VCACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    yt.YT_VCACHE_FILE.write_text(
        '{"good": {"title": "T", "duration": 60, "cached_at": 1},'
        ' "nan": {"title": "T", "duration": NaN},'
        ' "list": [1, 2],'
        ' "badstub": {"unavailable": true, "cached_at": "x"}}')
    assert set(yt._load_yt_video_cache()) == {"good"}


@pytest.mark.parametrize("content", ["[]", "42", '"text"', "null"])
def test_loading_a_cache_that_is_not_an_object_gives_empty(content):
    yt.YT_VCACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    yt.YT_VCACHE_FILE.write_text(content)
    assert yt._load_yt_video_cache() == {}


def test_a_scan_recovers_from_a_damaged_cache(fake_api):
    yt.YT_VCACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    yt.YT_VCACHE_FILE.write_text('{"v0001": {"title": 5, "duration": NaN}}')
    api = fake_api()
    entries, hits, unavailable = yt._fetch_with_cache(["v0001"], "key", yt._load_yt_video_cache())
    assert len(entries) == 1 and hits == 0
    assert len(api.video_calls()) == 1                # the bad entry was refetched
