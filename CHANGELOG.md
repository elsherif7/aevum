# Changelog

All notable changes to Aevum are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- `FolderNode` and `ScanTree` named types in `_models.py` — the scan tree is
  now self-documenting instead of an anonymous 7-tuple
- GitHub Actions CI: pytest on Ubuntu / Windows / macOS × Python 3.8–3.12,
  ruff lint, and mypy type checking on every push and pull request
- Type annotations on `_models.py`, `_cache.py`, `_config.py`, `_scan.py`,
  `_apikey.py`, `_export.py`, `_youtube.py`, `_cli_json.py`, `_cli_helpers.py`
- `mypy>=1.10` and `ruff>=0.4` added to `[dev]` optional dependencies
- MIT license (`LICENSE` file) and full `pyproject.toml` metadata: author,
  license reference, readme, and PyPI classifiers
- Test suite expanded from 63 to 135 tests covering `_display`, `_youtube`,
  `_export`, `_dupes`, and native MP4/MKV binary parsers

### Changed
- `_config.py` is now a pure data-layer module (`load_config`, `save_config`,
  `CONFIG_DEFAULTS`). The three command implementations (`cmd_doctor`,
  `cmd_cache`, `cmd_config`) moved to `_cli_cmds.py` where all other `cmd_*`
  functions live
- `_resolve_alias` in `_cli_helpers.py` now always returns `str` — previously
  it returned `str | list` depending on token count, which silently broke
  callers doing `Path(result)`

### Fixed
- `scan_parallel` early-return paths returned raw `([], [], 0)` tuples instead
  of `ScanTree(...)` — caught by mypy
- `validate_export_path` in `_export.py` reassigned a `str` parameter to a
  `Path`, causing 6 downstream type errors — caught by mypy
- `_json_error` in `_cli_json.py` used implicit `Optional` on the `extra`
  parameter — caught by mypy

---

## [2.2.3] — Security & correctness hardening

### Security
- All subprocess calls use list form — no shell injection possible
- Output paths validated before writing — system directories blocked
- API keys stored in OS keyring (Windows Credential Manager / macOS Keychain /
  Linux Secret Service); falls back to Fernet-encrypted file, then plaintext
  with a warning
- API key format validated (`AIza...`) before storage
- CSV exports escape formula characters to prevent spreadsheet injection
- HTML exports use `html.escape()` on all user-controlled content
- Symlink loop detection via inode tracking; max recursion depth 30 levels
- YouTube API rate-limited to 100 requests/hour via persistent token bucket
  enforced across process invocations
- YouTube playlist pagination capped at 100,000 videos
- Atomic file writes (temp → rename) on all persistent state
- Duration values clamped to prevent integer overflow
- Quota tracker validated on load — negative values cannot bypass daily guard
- `LOCALAPPDATA` / `XDG_DATA_HOME` validated as absolute paths before use
- SHA-256 for cache keys (not SHA-1); BLAKE2b for duplicate detection

### Added
- `aevum recent` — show files added or modified within a time window
- `aevum top` — show top N files by duration or size
- `aevum history` — list past scan snapshots for a folder
- `aevum diff` — show what changed between the last two scans
- `aevum stats` — deep statistics: average, median, format distribution, size
  buckets, densest folder
- `aevum summary` — one-line overview of any folder
- HTML export format with dark theme, collapsible tree, sortable/searchable
  file table
- `--since` / `--until` date filters (relative: `7d`, `2w`; absolute:
  `2025-01-15`)
- `--exclude` flag to filter out folders by name pattern
- `--merge` flag to aggregate multiple scan targets into one grand total
- `--speed` flag to add custom playback speeds to the breakdown (repeatable)
- ASCII bar chart showing each folder's share of total duration
- YouTube support for `music.youtube.com`, `kids.youtube.com`,
  `gaming.youtube.com`
- Per-video YouTube cache — previously fetched videos skip the API entirely
- 247 supported file extensions covering all known video and audio formats
- `aevum appdata` — open the Aevum data folder in Explorer / Finder
- `aevum quota` — check YouTube API quota usage for today
- `aevum doctor` — environment check (ffprobe, API key, cache, Python version)
- `aevum update` / `aevum -U` — self-update via pip with animated progress bar
- `aevum clearpath` — clear saved project path used by `aevum update`
- Path alias system (`aevum alias set/list/remove`) supporting paths, flags,
  and command fragments
- Persistent config system with `aevum config get/set/list/reset`
- Duration cache keyed by mtime + size; 2-second tolerance for FAT32/exFAT
- Scan history snapshots (last 50 per folder) saved automatically after every
  scan
- Machine-readable JSON output (`--json`) for every subcommand
- Quiet mode (`-q`) for use in pipelines
- Fuzzy-match typo suggestions for unknown commands and sort fields
- Paths with spaces work without quotes on all platforms
- Watch mode (`aevum watch`) with configurable poll interval
- Folder comparison mode (`aevum compare`)
- Duplicate detection by size + partial BLAKE2b hash (`aevum dupes`)
- Batch multi-folder scanning with optional `--merge`
- `--depth N` to limit tree display depth
- `--ext`, `--folder`, `--min-duration`, `--max-duration` filters
- Named exit codes (0–5) documented in README and `_exit.py`

### Changed
- Monolithic `aevum.py` split into `aevum_pkg` package with focused modules
- Monolithic `_cli.py` (2108 lines) split into `_cli.py`, `_cli_args.py`,
  `_cli_cmds.py`, `_cli_helpers.py`, `_cli_json.py`, `_cli_update.py`
- Native MP4/MKV/WebM header parsing — ffprobe used only as fallback
- Parallel scanning with configurable thread pool (capped at 8 workers)
- Two-pass MKV parser: 2 MB first pass, 8 MB retry only if Info block not found
- Two-phase duplicate hashing: first-chunk hash for pre-filtering, full hash
  only on collisions
- History diff uses relative paths as keys — files with the same name in
  different subfolders are correctly distinguished
- `_build_tree` is O(n) — single ancestor walk per file

---

## [2.1.0]

### Added
- Alias system supporting flags, commands, and paths as values
- Aliases cannot be overwritten — remove first, then re-add
- `aevum update` works from any directory by saving the project path

### Changed
- CLI redesigned with subcommands (`scan`, `compare`, `dupes`, `export`,
  `watch`, `cache`, `config`, `alias`, `doctor`, `quota`, `version`)
- Help page updated with all flags, options, and subcommands

---

## [2.0.0]

### Added
- YouTube Data API v3 support — scan any video, playlist, or channel by URL
- Full audio format support (MP3, FLAC, WAV, AAC, OGG, and 100+ more)
- Extended video format support (247 extensions total)
- Playback speed breakdown (1×, 1.25×, 1.5×, 1.75×, 2×)
- Duplicate detection by size + partial hash
- Folder comparison mode
- Export to TXT, CSV, JSON
- Duration cache for near-instant repeat scans
- `--json` and `--quiet` flags
- Named exit codes

### Changed
- Parallel ffprobe scanning with thread pool
- Native MP4 and MKV header parsing (no ffprobe for common formats)
- O(n) tree builder replacing the previous recursive approach
- Interactive REPL replaced with full CLI subcommand interface

---

## [1.0.0] — Initial release

### Added
- Recursive folder scan for video files
- Duration tree with subfolder breakdown
- Grand total in days, hours, and minutes
- Top 10 longest files
- Basic terminal UI with ANSI colors
