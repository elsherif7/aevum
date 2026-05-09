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
