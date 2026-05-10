"""
Aevum test suite.

Covers pure functions (no filesystem, no ffprobe) and cache/history
round-trips (temp directories only).  No network calls, no subprocess.
"""
import json
import os
import time
from pathlib import Path

import pytest

# ── imports under test ────────────────────────────────────────────────────
from aevum_pkg._scan import (
    format_duration,
    format_size,
    parse_duration_arg,
    parse_since_arg,
    apply_filters,
    rebuild_after_filter,
    video_extensions,
    _VIDEO_EXT_SET,
)
from aevum_pkg._cache import load_cache, save_cache, _cache_key, _normalise_path
from aevum_pkg._history import save_snapshot, diff_to_json, load_history, _history_key
from aevum_pkg._config import load_config, CONFIG_DEFAULTS


# ═══════════════════════════════════════════════════════════════════════════
# format_duration
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatDuration:
    def test_zero(self):
        r = format_duration(0)
        assert r["hours_fmt"]   == "00h 00m 00s"
        assert r["minutes_fmt"] == "0m 00s"
        assert r["days_fmt"]    == "0d 00h 00m 00s"

    def test_negative_clamped_to_zero(self):
        r = format_duration(-100)
        assert r["hours_fmt"] == "00h 00m 00s"

    def test_one_minute(self):
        r = format_duration(60)
        assert r["hours_fmt"]   == "00h 01m 00s"
        assert r["minutes_fmt"] == "1m 00s"

    def test_one_hour(self):
        r = format_duration(3600)
        assert r["hours_fmt"]   == "01h 00m 00s"
        assert r["days_fmt"]    == "0d 01h 00m 00s"

    def test_one_day(self):
        r = format_duration(86400)
        assert r["days_fmt"]  == "1d 00h 00m 00s"
        assert r["hours_fmt"] == "24h 00m 00s"

    def test_mixed(self):
        # 1h 30m 45s = 5445s
        r = format_duration(5445)
        assert r["hours_fmt"]   == "01h 30m 45s"
        assert r["minutes_fmt"] == "90m 45s"

    def test_max_clamp(self):
        # 100 years + 1 second should be clamped
        over = 100 * 365 * 86400 + 1
        r_over   = format_duration(over)
        r_max    = format_duration(100 * 365 * 86400)
        assert r_over["hours_fmt"] == r_max["hours_fmt"]

    def test_returns_all_three_keys(self):
        r = format_duration(1234)
        assert set(r.keys()) == {"days_fmt", "hours_fmt", "minutes_fmt"}


# ═══════════════════════════════════════════════════════════════════════════
# format_size
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatSize:
    def test_bytes(self):
        assert format_size(0)    == "0 B"
        assert format_size(512)  == "512 B"
        assert format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_size(1024)       == "1.0 KB"
        assert format_size(1536)       == "1.5 KB"
        assert format_size(1024 * 999) == "999.0 KB"

    def test_megabytes(self):
        assert format_size(1_048_576)       == "1.0 MB"
        assert format_size(1_048_576 * 512) == "512.0 MB"

    def test_gigabytes(self):
        assert format_size(1_073_741_824)     == "1.00 GB"
        assert format_size(1_073_741_824 * 2) == "2.00 GB"

    def test_boundary_kb_to_mb(self):
        # 1 MB - 1 byte should still be KB
        assert "KB" in format_size(1_048_575)
        # exactly 1 MB
        assert format_size(1_048_576) == "1.0 MB"


# ═══════════════════════════════════════════════════════════════════════════
# parse_duration_arg
# ═══════════════════════════════════════════════════════════════════════════

class TestParseDurationArg:
    def test_seconds(self):
        assert parse_duration_arg("30s") == 30.0
        assert parse_duration_arg("0s")  == 0.0

    def test_minutes(self):
        assert parse_duration_arg("5m")  == 300.0
        assert parse_duration_arg("90m") == 5400.0

    def test_hours(self):
        assert parse_duration_arg("1h")   == 3600.0
        assert parse_duration_arg("1.5h") == 5400.0

    def test_combined(self):
        assert parse_duration_arg("1h30m")    == 5400.0
        assert parse_duration_arg("1h30m45s") == pytest.approx(5445.0)

    def test_colon_format_hms(self):
        assert parse_duration_arg("1:30:00") == 5400.0
        assert parse_duration_arg("0:05:00") == 300.0

    def test_colon_format_hm(self):
        # 1:30 means 1h 30m = 5400s
        assert parse_duration_arg("1:30") == 5400.0

    def test_plain_number(self):
        assert parse_duration_arg("5400") == 5400.0
        assert parse_duration_arg("0")    == 0.0

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            parse_duration_arg("banana")
        with pytest.raises(ValueError):
            parse_duration_arg("")
        with pytest.raises(ValueError):
            parse_duration_arg("1x")

    def test_capped_at_one_year(self):
        one_year = 365 * 24 * 3600
        assert parse_duration_arg("9999h") == one_year


# ═══════════════════════════════════════════════════════════════════════════
# parse_since_arg
# ═══════════════════════════════════════════════════════════════════════════

class TestParseSinceArg:
    def test_days_relative(self):
        before = time.time()
        ts = parse_since_arg("7d")
        after  = time.time()
        # Should be approximately 7 days ago
        assert pytest.approx(ts, abs=2) == before - 7 * 86400

    def test_weeks_relative(self):
        before = time.time()
        ts = parse_since_arg("2w")
        assert pytest.approx(ts, abs=2) == before - 14 * 86400

    def test_uppercase_unit(self):
        ts_lower = parse_since_arg("7d")
        ts_upper = parse_since_arg("7D")
        assert pytest.approx(ts_lower, abs=1) == ts_upper

    def test_absolute_date(self):
        ts = parse_since_arg("2025-01-15")
        import datetime
        dt = datetime.datetime(2025, 1, 15, 0, 0, 0)
        assert ts == pytest.approx(dt.timestamp(), abs=1)

    def test_absolute_datetime(self):
        ts = parse_since_arg("2025-01-15T10:30")
        import datetime
        dt = datetime.datetime(2025, 1, 15, 10, 30, 0)
        assert ts == pytest.approx(dt.timestamp(), abs=1)

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            parse_since_arg("yesterday")
        with pytest.raises(ValueError):
            parse_since_arg("2025/01/15")
        with pytest.raises(ValueError):
            parse_since_arg("")


# ═══════════════════════════════════════════════════════════════════════════
# video_extensions / _VIDEO_EXT_SET
# ═══════════════════════════════════════════════════════════════════════════

class TestVideoExtensions:
    def test_common_extensions_present(self):
        for ext in ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.flac', '.wav'):
            assert ext in _VIDEO_EXT_SET

    def test_frozenset_matches_tuple(self):
        assert _VIDEO_EXT_SET == frozenset(video_extensions)

    def test_all_lowercase(self):
        for ext in video_extensions:
            assert ext == ext.lower(), f"Extension not lowercase: {ext}"

    def test_all_start_with_dot(self):
        for ext in video_extensions:
            assert ext.startswith('.'), f"Extension missing dot: {ext}"

    def test_no_duplicates_in_frozenset(self):
        # frozenset deduplicates — length should equal unique count
        assert len(_VIDEO_EXT_SET) == len(set(video_extensions))


# ═══════════════════════════════════════════════════════════════════════════
# apply_filters
# ═══════════════════════════════════════════════════════════════════════════

class TestApplyFilters:
    """
    apply_filters works on dicts keyed by Path objects.
    We use tmp_path to create real Path objects so suffix/parent work.
    """

    @pytest.fixture
    def sample_files(self, tmp_path):
        """
        Create a small fake durations/sizes dict with real Path objects.
        Structure:
          root/Action/film.mkv      120s
          root/Action/short.mp4     30s
          root/Comedy/funny.mkv     90s
          root/Comedy/tiny.avi      10s
        """
        files = {
            tmp_path / "Action" / "film.mkv":   120.0,
            tmp_path / "Action" / "short.mp4":   30.0,
            tmp_path / "Comedy" / "funny.mkv":   90.0,
            tmp_path / "Comedy" / "tiny.avi":    10.0,
        }
        sizes = {p: 1000 for p in files}
        return files, sizes

    def test_no_filters_returns_all(self, sample_files):
        durations, sizes = sample_files
        out_d, out_s = apply_filters(durations, sizes, {})
        assert out_d == durations

    def test_min_duration(self, sample_files):
        durations, sizes = sample_files
        out_d, _ = apply_filters(durations, sizes, {"min_duration": 60.0})
        assert len(out_d) == 2
        for sec in out_d.values():
            assert sec >= 60.0

    def test_max_duration(self, sample_files):
        durations, sizes = sample_files
        out_d, _ = apply_filters(durations, sizes, {"max_duration": 30.0})
        assert len(out_d) == 2
        for sec in out_d.values():
            assert sec <= 30.0

    def test_ext_filter(self, sample_files):
        durations, sizes = sample_files
        out_d, _ = apply_filters(durations, sizes, {"exts": {".mkv"}})
        assert len(out_d) == 2
        for p in out_d:
            assert p.suffix.lower() == ".mkv"

    def test_exclude_folder(self, sample_files):
        durations, sizes = sample_files
        out_d, _ = apply_filters(durations, sizes, {"exclude": {"action"}})
        assert len(out_d) == 2
        for p in out_d:
            assert p.parent.name.lower() != "action"

    def test_min_and_ext_combined(self, sample_files):
        durations, sizes = sample_files
        out_d, _ = apply_filters(durations, sizes, {
            "min_duration": 60.0,
            "exts": {".mkv"},
        })
        # Only film.mkv (120s) and funny.mkv (90s) qualify
        assert len(out_d) == 2

    def test_empty_durations(self):
        out_d, out_s = apply_filters({}, {}, {"min_duration": 10.0})
        assert out_d == {}
        assert out_s == {}


# ═══════════════════════════════════════════════════════════════════════════
# cache round-trip
# ═══════════════════════════════════════════════════════════════════════════

class TestCacheRoundTrip:
    """
    save_cache / load_cache using a real temp directory.
    We monkey-patch CACHE_DIR so tests never touch the real user cache.
    """

    @pytest.fixture(autouse=True)
    def patch_cache_dir(self, tmp_path, monkeypatch):
        import aevum_pkg._cache as _cache_mod
        monkeypatch.setattr(_cache_mod, "CACHE_DIR", tmp_path / "cache")

    def _make_real_files(self, tmp_path, names):
        """Create real empty files so stat() works."""
        paths = []
        for name in names:
            p = tmp_path / name
            p.write_bytes(b"")
            paths.append(p)
        return paths

    def test_save_then_load(self, tmp_path):
        files = self._make_real_files(tmp_path, ["a.mkv", "b.mp4"])
        durations = {files[0]: 120.0, files[1]: 60.0}
        root = tmp_path

        save_cache(root, durations)
        loaded = load_cache(root)

        # Keys are normalised paths; values should match
        for p, sec in durations.items():
            from aevum_pkg._cache import _normalise_path
            key = _normalise_path(p)
            assert key in loaded
            assert loaded[key]["duration"] == sec

    def test_load_missing_returns_empty(self, tmp_path):
        result = load_cache(tmp_path / "nonexistent_folder")
        assert result == {}

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        import aevum_pkg._cache as _cache_mod
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        key_path = _cache_mod._cache_key(tmp_path)
        key_path.write_text("not valid json", encoding="utf-8")
        result = load_cache(tmp_path)
        assert result == {}

    def test_load_wrong_root_type_returns_empty(self, tmp_path):
        # A cache file that contains a dict instead of a list
        import aevum_pkg._cache as _cache_mod
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        key_path = _cache_mod._cache_key(tmp_path)
        key_path.write_text('{"not": "a list"}', encoding="utf-8")
        result = load_cache(tmp_path)
        assert result == {}

    def test_mtime_tolerance(self, tmp_path):
        """
        Cache entry with mtime within 2s of actual mtime should still hit.
        Simulates FAT32 2-second precision.
        """
        files = self._make_real_files(tmp_path, ["fat.mkv"])
        p = files[0]
        actual_mtime = p.stat().st_mtime

        import aevum_pkg._cache as _cache_mod
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        from aevum_pkg._cache import _normalise_path, _cache_key
        import json as _json

        # Write a cache entry with mtime offset by 1.5s (within tolerance)
        entry = [{
            "path":     _normalise_path(p),
            "mtime":    actual_mtime + 1.5,
            "size":     p.stat().st_size,
            "duration": 99.0,
        }]
        _cache_key(tmp_path).write_text(_json.dumps(entry), encoding="utf-8")

        loaded = load_cache(tmp_path)
        key = _normalise_path(p)
        assert key in loaded
        assert loaded[key]["duration"] == 99.0

    def test_oversized_cache_ignored(self, tmp_path):
        import aevum_pkg._cache as _cache_mod
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        key_path = _cache_mod._cache_key(tmp_path)
        # Write a file larger than 50 MB limit
        key_path.write_bytes(b"x" * (51 * 1024 * 1024))
        result = load_cache(tmp_path)
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════
# history / diff
# ═══════════════════════════════════════════════════════════════════════════

class TestHistory:
    """
    save_snapshot / diff_to_json using a real temp directory.
    Monkey-patches HISTORY_DIR so tests never touch the real user data.
    """

    @pytest.fixture(autouse=True)
    def patch_history_dir(self, tmp_path, monkeypatch):
        import aevum_pkg._history as _hist_mod
        monkeypatch.setattr(_hist_mod, "HISTORY_DIR", tmp_path / "history")

    def _make_paths(self, root, specs):
        """
        specs: list of (relative_str, seconds) e.g. [("Action/film.mkv", 120.0)]
        Returns a durations dict with real Path objects.
        """
        durations = {}
        for rel, sec in specs:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
            durations[p] = sec
        return durations

    def test_snapshot_uses_relative_paths(self, tmp_path):
        """Keys in the snapshot must be relative paths, not bare filenames."""
        root = tmp_path / "Library"
        root.mkdir()
        durations = self._make_paths(root, [
            ("Action/film.mkv",   120.0),
            ("Comedy/film.mkv",    90.0),   # same filename, different folder
        ])
        save_snapshot(root, 210.0, 2, 2000, durations)
        history = load_history(root)
        assert len(history) == 1
        files = history[0]["files"]
        # Both files must be present as distinct keys
        assert len(files) == 2
        keys = set(files.keys())
        # Keys should contain the subfolder, not just the bare filename
        assert not any(k == "film.mkv" for k in keys), \
            "Keys should be relative paths, not bare filenames"
        # Both relative paths should be present
        sep = os.sep
        assert any("Action" in k for k in keys)
        assert any("Comedy" in k for k in keys)

    def test_diff_detects_added_file(self, tmp_path):
        root = tmp_path / "Library"
        root.mkdir()

        # Snapshot 1: one file
        dur1 = self._make_paths(root, [("Action/ep01.mkv", 60.0)])
        save_snapshot(root, 60.0, 1, 1000, dur1)

        # Snapshot 2: two files
        dur2 = self._make_paths(root, [
            ("Action/ep01.mkv", 60.0),
            ("Action/ep02.mkv", 60.0),
        ])
        save_snapshot(root, 120.0, 2, 2000, dur2)

        result = diff_to_json(root)
        assert result["status"] == "ok"
        assert result["delta_count"] == 1
        assert len(result["added"]) == 1
        assert len(result["removed"]) == 0

    def test_diff_detects_removed_file(self, tmp_path):
        root = tmp_path / "Library"
        root.mkdir()

        dur1 = self._make_paths(root, [
            ("Action/ep01.mkv", 60.0),
            ("Action/ep02.mkv", 60.0),
        ])
        save_snapshot(root, 120.0, 2, 2000, dur1)

        dur2 = self._make_paths(root, [("Action/ep01.mkv", 60.0)])
        save_snapshot(root, 60.0, 1, 1000, dur2)

        result = diff_to_json(root)
        assert result["delta_count"] == -1
        assert len(result["removed"]) == 1
        assert len(result["added"]) == 0

    def test_diff_same_name_different_folder_are_distinct(self, tmp_path):
        """
        The core bug we fixed: Action/ep01.mkv and Comedy/ep01.mkv must be
        treated as different files, not the same file.
        """
        root = tmp_path / "Library"
        root.mkdir()

        # Snapshot 1: ep01 in Action
        dur1 = self._make_paths(root, [("Action/ep01.mkv", 60.0)])
        save_snapshot(root, 60.0, 1, 1000, dur1)

        # Snapshot 2: ep01 moved to Comedy (different folder, same filename)
        dur2 = self._make_paths(root, [("Comedy/ep01.mkv", 60.0)])
        save_snapshot(root, 60.0, 1, 1000, dur2)

        result = diff_to_json(root)
        # Should detect 1 added and 1 removed, not "no changes"
        assert len(result["added"])   == 1
        assert len(result["removed"]) == 1

    def test_diff_no_changes(self, tmp_path):
        root = tmp_path / "Library"
        root.mkdir()
        dur = self._make_paths(root, [("Action/ep01.mkv", 60.0)])
        save_snapshot(root, 60.0, 1, 1000, dur)
        save_snapshot(root, 60.0, 1, 1000, dur)

        result = diff_to_json(root)
        assert result["added"]   == []
        assert result["removed"] == []

    def test_diff_requires_two_snapshots(self, tmp_path):
        root = tmp_path / "Library"
        root.mkdir()
        result = diff_to_json(root)
        assert result["status"] == "error"

    def test_history_capped_at_50(self, tmp_path):
        root = tmp_path / "Library"
        root.mkdir()
        dur = self._make_paths(root, [("ep.mkv", 10.0)])
        for _ in range(55):
            save_snapshot(root, 10.0, 1, 100, dur)
        history = load_history(root)
        assert len(history) == 50


# ═══════════════════════════════════════════════════════════════════════════
# load_config
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadConfig:
    """
    load_config using a temp config file.
    Monkey-patches CONFIG_FILE so tests never touch the real user config.
    """

    @pytest.fixture(autouse=True)
    def patch_config_file(self, tmp_path, monkeypatch):
        import aevum_pkg._config as _cfg_mod
        self.config_path = tmp_path / "config.json"
        monkeypatch.setattr(_cfg_mod, "CONFIG_FILE", self.config_path)

    def test_missing_file_returns_defaults(self):
        cfg = load_config()
        assert cfg == CONFIG_DEFAULTS

    def test_valid_config_loaded(self):
        self.config_path.write_text(
            '{"sort": "duration:desc", "top": 20}', encoding="utf-8"
        )
        cfg = load_config()
        assert cfg["sort"] == "duration:desc"
        assert cfg["top"]  == 20

    def test_corrupt_json_returns_defaults(self):
        self.config_path.write_text("not json", encoding="utf-8")
        cfg = load_config()
        assert cfg == CONFIG_DEFAULTS

    def test_wrong_root_type_returns_defaults(self):
        self.config_path.write_text("[1, 2, 3]", encoding="utf-8")
        cfg = load_config()
        assert cfg == CONFIG_DEFAULTS

    def test_invalid_sort_uses_default(self):
        self.config_path.write_text('{"sort": "invalid:value"}', encoding="utf-8")
        cfg = load_config()
        assert cfg["sort"] == CONFIG_DEFAULTS["sort"]

    def test_top_out_of_range_uses_default(self):
        self.config_path.write_text('{"top": 999}', encoding="utf-8")
        cfg = load_config()
        assert cfg["top"] == CONFIG_DEFAULTS["top"]

    def test_aliases_loaded(self):
        self.config_path.write_text(
            '{"aliases": {"M": "D:\\\\Movies", "spd": "--speed 1.5"}}',
            encoding="utf-8"
        )
        cfg = load_config()
        assert cfg["aliases"]["M"]   == "D:\\Movies"
        assert cfg["aliases"]["spd"] == "--speed 1.5"

    def test_aliases_with_special_chars_now_accepted(self):
        """
        After the alias fix, values with +, (, ), ~, % etc. must load fine.
        Previously these were silently dropped by the overly strict regex.
        """
        self.config_path.write_text(
            '{"aliases": {"p": "/home/user/My Files (2024)/"}}',
            encoding="utf-8"
        )
        cfg = load_config()
        assert cfg["aliases"]["p"] == "/home/user/My Files (2024)/"

    def test_alias_exceeding_length_limit_skipped(self):
        long_value = "x" * 5000
        self.config_path.write_text(
            f'{{"aliases": {{"k": "{long_value}"}}}}', encoding="utf-8"
        )
        cfg = load_config()
        assert "k" not in cfg["aliases"]

    def test_bare_sort_normalised(self):
        """B-06: bare 'duration' should become 'duration:desc'."""
        self.config_path.write_text('{"sort": "duration"}', encoding="utf-8")
        cfg = load_config()
        assert cfg["sort"] == "duration:desc"


# ═══════════════════════════════════════════════════════════════════════════
# _display — _safe and _fuzzy_suggest
# ═══════════════════════════════════════════════════════════════════════════

class TestSafe:
    """_safe strips ANSI codes and control characters from display strings."""

    from aevum_pkg._display import _safe

    def test_plain_string_unchanged(self):
        from aevum_pkg._display import _safe
        assert _safe("hello") == "hello"

    def test_strips_ansi_color(self):
        from aevum_pkg._display import _safe
        assert _safe("\033[91mred\033[0m") == "red"

    def test_strips_ansi_cursor_move(self):
        from aevum_pkg._display import _safe
        assert _safe("\033[2Jhello") == "hello"

    def test_strips_control_chars(self):
        from aevum_pkg._display import _safe
        # null byte, bell, backspace
        assert _safe("a\x00b\x07c\x08d") == "abcd"

    def test_truncates_at_maxlen(self):
        from aevum_pkg._display import _safe
        long = "x" * 300
        assert len(_safe(long)) == 200

    def test_empty_string(self):
        from aevum_pkg._display import _safe
        assert _safe("") == ""

    def test_unicode_preserved(self):
        from aevum_pkg._display import _safe
        assert _safe("日本語") == "日本語"

    def test_mixed_ansi_and_text(self):
        from aevum_pkg._display import _safe
        result = _safe("\033[92mOK\033[0m  filename.mkv")
        assert result == "OK  filename.mkv"


class TestFuzzySuggest:
    """_fuzzy_suggest returns the closest candidate within edit-distance 2."""

    def test_exact_match(self):
        from aevum_pkg._display import _fuzzy_suggest
        assert _fuzzy_suggest("scan", ["scan", "watch", "dupes"]) == "scan"

    def test_one_typo(self):
        from aevum_pkg._display import _fuzzy_suggest
        # "scna" is 1 transposition away from "scan"
        assert _fuzzy_suggest("scna", ["scan", "watch", "dupes"]) == "scan"

    def test_two_typos(self):
        from aevum_pkg._display import _fuzzy_suggest
        # "wacth" is 2 edits from "watch"
        assert _fuzzy_suggest("wacth", ["scan", "watch", "dupes"]) == "watch"

    def test_three_typos_returns_none(self):
        from aevum_pkg._display import _fuzzy_suggest
        # "wxyzh" is more than 2 edits from anything
        assert _fuzzy_suggest("wxyzh", ["scan", "watch", "dupes"]) is None

    def test_no_candidates_returns_none(self):
        from aevum_pkg._display import _fuzzy_suggest
        assert _fuzzy_suggest("scan", []) is None

    def test_too_many_candidates_returns_none(self):
        from aevum_pkg._display import _fuzzy_suggest
        # Guard: >50 candidates → skip to avoid O(n*m) slowdown
        candidates = [f"cmd{i}" for i in range(51)]
        assert _fuzzy_suggest("cmd0", candidates) is None

    def test_word_too_long_returns_none(self):
        from aevum_pkg._display import _fuzzy_suggest
        long_word = "x" * 51
        assert _fuzzy_suggest(long_word, ["scan"]) is None

    def test_no_close_match_returns_none(self):
        from aevum_pkg._display import _fuzzy_suggest
        assert _fuzzy_suggest("zzz", ["scan", "watch"]) is None


# ═══════════════════════════════════════════════════════════════════════════
# _youtube — URL parsing and ISO 8601 duration parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestParseYtUrl:
    """_parse_yt_url classifies YouTube URLs into (kind, id) pairs."""

    def _parse(self, url):
        from aevum_pkg._youtube import _parse_yt_url, _normalise_url
        return _parse_yt_url(_normalise_url(url))

    def test_video_watch(self):
        kind, vid = self._parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert kind == "video"
        assert vid  == "dQw4w9WgXcQ"

    def test_video_short_url(self):
        kind, vid = self._parse("https://youtu.be/dQw4w9WgXcQ")
        assert kind == "video"
        assert vid  == "dQw4w9WgXcQ"

    def test_video_shorts(self):
        kind, vid = self._parse("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert kind == "video"
        assert vid  == "dQw4w9WgXcQ"

    def test_playlist(self):
        kind, pid = self._parse("https://www.youtube.com/playlist?list=PLxxx123")
        assert kind == "playlist"
        assert pid  == "PLxxx123"

    def test_channel_handle(self):
        kind, handle = self._parse("https://www.youtube.com/@mkbhd")
        assert kind   == "channel_handle"
        assert handle == "@mkbhd"

    def test_channel_id(self):
        kind, cid = self._parse("https://www.youtube.com/channel/UCxxxxxx")
        assert kind == "channel_id"
        assert cid  == "UCxxxxxx"

    def test_channel_user(self):
        kind, name = self._parse("https://www.youtube.com/user/mkbhd")
        assert kind == "channel_handle"
        assert name == "mkbhd"

    def test_music_youtube(self):
        kind, pid = self._parse("https://music.youtube.com/playlist?list=PLmusic")
        assert kind == "playlist"
        assert pid  == "PLmusic"

    def test_unknown_domain_returns_none(self):
        kind, vid = self._parse("https://vimeo.com/123456")
        assert kind is None
        assert vid  is None

    def test_normalise_adds_https(self):
        from aevum_pkg._youtube import _normalise_url
        assert _normalise_url("youtube.com/watch?v=abc").startswith("https://")

    def test_normalise_preserves_https(self):
        from aevum_pkg._youtube import _normalise_url
        url = "https://youtube.com/watch?v=abc"
        assert _normalise_url(url) == url


class TestParseIso8601Duration:
    """_parse_iso8601_duration converts PT strings to seconds."""

    def _parse(self, s):
        from aevum_pkg._youtube import _parse_iso8601_duration
        return _parse_iso8601_duration(s)

    def test_hours_minutes_seconds(self):
        assert self._parse("PT1H30M45S") == pytest.approx(5445.0)

    def test_hours_only(self):
        assert self._parse("PT2H") == pytest.approx(7200.0)

    def test_minutes_only(self):
        assert self._parse("PT45M") == pytest.approx(2700.0)

    def test_seconds_only(self):
        assert self._parse("PT30S") == pytest.approx(30.0)

    def test_zero(self):
        assert self._parse("PT0S") == pytest.approx(0.0)

    def test_empty_string_returns_zero(self):
        assert self._parse("") == pytest.approx(0.0)

    def test_none_returns_zero(self):
        assert self._parse(None) == pytest.approx(0.0)

    def test_capped_at_one_year(self):
        # PT9999H is more than a year — should be clamped
        one_year = 365 * 86400
        assert self._parse("PT9999H") == pytest.approx(one_year)

    def test_fractional_seconds(self):
        assert self._parse("PT1M30.5S") == pytest.approx(90.5)

    def test_hours_and_seconds_no_minutes(self):
        assert self._parse("PT1H30S") == pytest.approx(3630.0)


# ═══════════════════════════════════════════════════════════════════════════
# _export — sanitize_csv_field and validate_export_path
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeCsvField:
    """sanitize_csv_field prevents spreadsheet formula injection."""

    def _san(self, v):
        from aevum_pkg._export import sanitize_csv_field
        return sanitize_csv_field(v)

    def test_plain_string_unchanged(self):
        assert self._san("hello world") == "hello world"

    def test_empty_string_unchanged(self):
        assert self._san("") == ""

    def test_equals_sign_prefixed(self):
        result = self._san("=SUM(A1:A10)")
        assert not result.startswith("=")

    def test_plus_sign_prefixed(self):
        result = self._san("+cmd|calc")
        assert not result.startswith("+")

    def test_at_sign_prefixed(self):
        result = self._san("@SUM(1+1)")
        assert not result.startswith("@")

    def test_minus_sign_prefixed(self):
        result = self._san("-2+3")
        assert not result.startswith("-")

    def test_null_bytes_removed(self):
        assert "\x00" not in self._san("hello\x00world")

    def test_control_chars_removed(self):
        # BEL, BS, VT, FF are stripped
        assert self._san("a\x07b\x08c") == "abc"

    def test_normal_path_unchanged(self):
        # Paths starting with a letter are safe
        result = self._san("D:\\Movies\\film.mkv")
        assert result == "D:\\Movies\\film.mkv"

    def test_unicode_preserved(self):
        assert self._san("日本語ファイル.mkv") == "日本語ファイル.mkv"


class TestValidateExportPath:
    """validate_export_path blocks system dirs and bad extensions."""

    def _validate(self, path, scan_folder=None):
        from aevum_pkg._export import validate_export_path
        return validate_export_path(path, scan_folder or Path("."))

    def test_valid_txt_path(self, tmp_path):
        dest = tmp_path / "report.txt"
        result = self._validate(str(dest), tmp_path)
        assert result == dest.resolve()

    def test_valid_csv_path(self, tmp_path):
        dest = tmp_path / "report.csv"
        result = self._validate(str(dest), tmp_path)
        assert result == dest.resolve()

    def test_valid_json_path(self, tmp_path):
        dest = tmp_path / "report.json"
        result = self._validate(str(dest), tmp_path)
        assert result == dest.resolve()

    def test_valid_html_path(self, tmp_path):
        dest = tmp_path / "report.html"
        result = self._validate(str(dest), tmp_path)
        assert result == dest.resolve()

    def test_invalid_extension_raises(self, tmp_path):
        dest = tmp_path / "report.exe"
        with pytest.raises(ValueError, match="Invalid extension"):
            self._validate(str(dest), tmp_path)

    def test_nonexistent_parent_raises(self, tmp_path):
        dest = tmp_path / "nonexistent_dir" / "report.txt"
        with pytest.raises(ValueError, match="does not exist"):
            self._validate(str(dest), tmp_path)

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only system dir check")
    def test_windows_system_dir_blocked(self, tmp_path):
        import os as _os
        win_dir = Path(_os.environ.get("SystemRoot", r"C:\Windows"))
        dest = win_dir / "report.txt"
        with pytest.raises(PermissionError, match="system directory"):
            self._validate(str(dest), tmp_path)

    @pytest.mark.skipif(os.name == "nt", reason="Unix-only system dir check")
    def test_unix_etc_blocked(self, tmp_path):
        dest = Path("/etc/report.txt")
        with pytest.raises(PermissionError, match="system directory"):
            self._validate(str(dest), tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# _dupes — find_duplicates
# ═══════════════════════════════════════════════════════════════════════════

class TestFindDuplicates:
    """find_duplicates detects files with identical size + content hash."""

    def _make_file(self, path: Path, content: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_identical_files_detected(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        content = b"x" * 1000
        a = self._make_file(tmp_path / "a" / "file.mkv", content)
        b = self._make_file(tmp_path / "b" / "file.mkv", content)
        durations = {a: 60.0, b: 60.0}
        sizes     = {a: len(content), b: len(content)}
        groups = find_duplicates(durations, sizes)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_different_content_not_duplicates(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        a = self._make_file(tmp_path / "a.mkv", b"aaa" * 100)
        b = self._make_file(tmp_path / "b.mkv", b"bbb" * 100)
        durations = {a: 60.0, b: 60.0}
        sizes     = {a: 300, b: 300}
        groups = find_duplicates(durations, sizes)
        assert groups == []

    def test_different_sizes_not_duplicates(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        a = self._make_file(tmp_path / "a.mkv", b"x" * 100)
        b = self._make_file(tmp_path / "b.mkv", b"x" * 200)
        durations = {a: 60.0, b: 60.0}
        sizes     = {a: 100, b: 200}
        groups = find_duplicates(durations, sizes)
        assert groups == []

    def test_three_identical_files_one_group(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        content = b"z" * 500
        files = [
            self._make_file(tmp_path / f"f{i}.mkv", content)
            for i in range(3)
        ]
        durations = {f: 30.0 for f in files}
        sizes     = {f: len(content) for f in files}
        groups = find_duplicates(durations, sizes)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_empty_input_returns_empty(self):
        from aevum_pkg._dupes import find_duplicates
        assert find_duplicates({}, {}) == []

    def test_single_file_no_duplicates(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        a = self._make_file(tmp_path / "a.mkv", b"unique content here")
        durations = {a: 10.0}
        sizes     = {a: len(b"unique content here")}
        assert find_duplicates(durations, sizes) == []

    def test_string_keys_normalised_to_path(self, tmp_path):
        """Issue 28: sizes dict with str keys should work correctly."""
        from aevum_pkg._dupes import find_duplicates
        content = b"q" * 800
        a = self._make_file(tmp_path / "a.mkv", content)
        b = self._make_file(tmp_path / "b.mkv", content)
        durations = {a: 60.0, b: 60.0}
        # Pass sizes with string keys (as if deserialised from JSON)
        sizes_str = {str(a): len(content), str(b): len(content)}
        groups = find_duplicates(durations, sizes_str)
        assert len(groups) == 1

    def test_zero_byte_files_ignored(self, tmp_path):
        from aevum_pkg._dupes import find_duplicates
        a = self._make_file(tmp_path / "a.mkv", b"")
        b = self._make_file(tmp_path / "b.mkv", b"")
        durations = {a: 0.0, b: 0.0}
        sizes     = {a: 0, b: 0}
        # Zero-size files are excluded from duplicate detection
        assert find_duplicates(durations, sizes) == []


# ═══════════════════════════════════════════════════════════════════════════
# _scan — native MP4 and MKV parsers
# ═══════════════════════════════════════════════════════════════════════════

class TestNativeParsers:
    """
    _read_mp4_duration and _read_mkv_duration parse binary headers without
    calling ffprobe.  We craft minimal valid binary fixtures in memory and
    write them to tmp_path.
    """

    # ── MP4 helpers ──────────────────────────────────────────────────────

    def _make_mp4(self, tmp_path: Path, duration_sec: float,
                  timescale: int = 1000) -> Path:
        """
        Write a minimal MP4 file containing only a moov/mvhd box.
        Uses version 0 (32-bit timestamps and duration).
        """
        import struct

        duration_units = int(duration_sec * timescale)

        # mvhd box (version 0): 8-byte header + 100 bytes of fields
        # version(1) + flags(3) + ctime(4) + mtime(4) + timescale(4) +
        # duration(4) + rate(4) + volume(2) + reserved(10) + matrix(36) +
        # pre_defined(24) + next_track_id(4) = 100 bytes of payload
        mvhd_payload = (
            b"\x00\x00\x00\x00"          # version=0, flags=0
            + b"\x00" * 8                # creation + modification time
            + struct.pack(">I", timescale)
            + struct.pack(">I", duration_units)
            + b"\x00" * (100 - 16)       # rate, volume, reserved, matrix, etc.
        )
        mvhd_size = 8 + len(mvhd_payload)
        mvhd = struct.pack(">I", mvhd_size) + b"mvhd" + mvhd_payload

        # moov box wrapping mvhd
        moov_size = 8 + len(mvhd)
        moov = struct.pack(">I", moov_size) + b"moov" + mvhd

        # ftyp box (minimal, just to make it look like a real MP4)
        ftyp = struct.pack(">I", 16) + b"ftyp" + b"mp42" + b"\x00" * 4

        data = ftyp + moov
        p = tmp_path / "test.mp4"
        p.write_bytes(data)
        return p

    def test_mp4_duration_parsed(self, tmp_path):
        from aevum_pkg._scan import _read_mp4_duration
        p = self._make_mp4(tmp_path, duration_sec=90.0, timescale=1000)
        result = _read_mp4_duration(str(p))
        assert result == pytest.approx(90.0, abs=0.01)

    def test_mp4_duration_one_hour(self, tmp_path):
        from aevum_pkg._scan import _read_mp4_duration
        p = self._make_mp4(tmp_path, duration_sec=3600.0, timescale=90000)
        result = _read_mp4_duration(str(p))
        assert result == pytest.approx(3600.0, abs=0.01)

    def test_mp4_empty_file_returns_none(self, tmp_path):
        from aevum_pkg._scan import _read_mp4_duration
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        assert _read_mp4_duration(str(p)) is None

    def test_mp4_garbage_returns_none(self, tmp_path):
        from aevum_pkg._scan import _read_mp4_duration
        p = tmp_path / "garbage.mp4"
        p.write_bytes(b"\xff" * 64)
        # Should not raise — returns None or 0
        result = _read_mp4_duration(str(p))
        assert result is None or result == 0.0

    # ── MKV helpers ──────────────────────────────────────────────────────

    def _write_vint(self, value: int) -> bytes:
        """Encode an EBML variable-length integer (size descriptor)."""
        if value < 0x7E:          # 1-byte: 0xxxxxxx with leading 1
            return bytes([value | 0x80])
        if value < 0x3FFE:        # 2-byte
            return bytes([(value >> 8) | 0x40, value & 0xFF])
        if value < 0x1FFFFE:      # 3-byte
            return bytes([(value >> 16) | 0x20, (value >> 8) & 0xFF, value & 0xFF])
        # 4-byte
        return bytes([
            (value >> 24) | 0x10,
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ])

    def _write_ebml_id(self, eid: int) -> bytes:
        """Write a 4-byte EBML element ID."""
        import struct
        return struct.pack(">I", eid)

    def _make_mkv(self, tmp_path: Path, duration_sec: float,
                  timescale_ns: int = 1_000_000) -> Path:
        """
        Write a minimal MKV/EBML file that the _read_mkv_duration parser
        can successfully parse.

        The parser (_try_parse) iterates the raw byte stream looking for
        element ID 0x1549A966 (Info).  It skips unknown elements by their
        declared size.  The Info block must therefore appear at the top
        level of the stream (not nested inside a Segment), because the
        parser does not recurse into Segment elements — it advances past
        them via esize.

        Structure written:
          EBML header  (minimal, 9 bytes)
          Info element (0x1549A966)
            TimestampScale (0x2AD7B1)
            Duration       (0x4489, 64-bit double)
        """
        import struct

        duration_units = duration_sec * 1_000_000_000 / timescale_ns
        dur_bytes = struct.pack(">d", duration_units)

        # TimestampScale element (ID 0x2AD7B1, 3-byte ID)
        ts_val = timescale_ns.to_bytes(4, "big")
        ts_elem = b"\x2A\xD7\xB1" + self._write_vint(len(ts_val)) + ts_val

        # Duration element (ID 0x4489, 2-byte ID, 8-byte double value)
        dur_elem = b"\x44\x89" + self._write_vint(len(dur_bytes)) + dur_bytes

        # Info element (ID 0x1549A966, 4-byte ID)
        info_payload = ts_elem + dur_elem
        info_elem = b"\x15\x49\xA9\x66" + self._write_vint(len(info_payload)) + info_payload

        # Minimal EBML header (ID 0x1A45DFA3, size=4, one sub-element)
        ebml_header = b"\x1A\x45\xDF\xA3" + self._write_vint(4) + b"\x42\x86\x81\x01"

        # Place Info directly after EBML header — no Segment wrapper.
        # The parser reads the flat stream and will find Info at this level.
        data = ebml_header + info_elem
        p = tmp_path / "test.mkv"
        p.write_bytes(data)
        return p

    def test_mkv_duration_parsed(self, tmp_path):
        from aevum_pkg._scan import _read_mkv_duration
        p = self._make_mkv(tmp_path, duration_sec=120.0)
        result = _read_mkv_duration(str(p))
        assert result == pytest.approx(120.0, abs=0.1)

    def test_mkv_empty_file_returns_none(self, tmp_path):
        from aevum_pkg._scan import _read_mkv_duration
        p = tmp_path / "empty.mkv"
        p.write_bytes(b"")
        assert _read_mkv_duration(str(p)) is None

    def test_mkv_garbage_returns_none(self, tmp_path):
        from aevum_pkg._scan import _read_mkv_duration
        p = tmp_path / "garbage.mkv"
        p.write_bytes(b"\xff" * 64)
        result = _read_mkv_duration(str(p))
        assert result is None or result == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# _scan — rebuild_after_filter
# ═══════════════════════════════════════════════════════════════════════════

class TestRebuildAfterFilter:
    """rebuild_after_filter recomputes totals and tree after filtering."""

    def _make_files(self, tmp_path, specs):
        durations = {}
        sizes = {}
        for rel, sec in specs:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
            durations[p] = sec
            sizes[p] = 100
        return durations, sizes

    def test_empty_after_filter_returns_zero_totals(self, tmp_path):
        durations, sizes = self._make_files(tmp_path, [("a/f.mkv", 60.0)])
        total_sec, total_count, tree, d, s = rebuild_after_filter(
            tmp_path, {}, {}, "name:asc"
        )
        assert total_sec == 0.0
        assert total_count == 0

    def test_totals_correct_after_filter(self, tmp_path):
        durations, sizes = self._make_files(tmp_path, [
            ("Action/a.mkv", 120.0),
            ("Comedy/b.mkv",  60.0),
        ])
        total_sec, total_count, tree, d, s = rebuild_after_filter(
            tmp_path, durations, sizes, "name:asc"
        )
        assert total_sec   == pytest.approx(180.0)
        assert total_count == 2

    def test_tree_is_scan_tree_instance(self, tmp_path):
        from aevum_pkg._models import ScanTree
        durations, sizes = self._make_files(tmp_path, [("a/f.mkv", 30.0)])
        _, _, tree, _, _ = rebuild_after_filter(
            tmp_path, durations, sizes, "name:asc"
        )
        assert isinstance(tree, ScanTree)
