# Aevum

**Media Library Scanner.**  
Point it at any folder or YouTube URL and instantly see the total duration — broken down by subfolder, with a grand total, size, playback speeds, and top longest files. Works on Windows, Linux, and macOS.

---

## Features

- Scans local folders recursively for video and audio files (247 supported formats)
- Displays duration and size per subfolder in a color-coded tree view
- **ASCII bar chart** showing each folder's share of total duration
- Grand total in days, hours, and minutes, plus total library size
- Playback speed breakdown (1x → 2x)
- Top 10 longest files listed after each scan (configurable)
- Duration cache — repeat scans are near-instant (keyed by mtime + size)
- Fast native MP4/MKV/WebM header parsing (ffprobe only as fallback)
- Parallel scanning with a configurable thread pool
- Duplicate detection by size + partial BLAKE2b hash
- Folder comparison mode — diff two libraries side by side
- Sortable tree (by name, duration, or count; ascending or descending)
- Filter by duration range, extension, subfolder name pattern, or date modified
- **Exclude folders** by name pattern (`--exclude trailers,samples`)
- **Date filters** — only show files modified in the last N days (`--since 30d`)
- Scan multiple folders at once, optionally merged into one grand total
- Watch mode — re-scan automatically when folder contents change
- Export results to TXT, CSV, JSON, or **HTML** (dark-theme, sortable, searchable)
- **Deep statistics** — average, median, shortest, longest, format distribution, size buckets (`aevum stats`)
- **One-line summary** — instant overview of any folder (`aevum summary`)
- **Scan history** — every scan is saved as a snapshot (`aevum history`)
- **Diff** — see exactly what changed between two scans (`aevum diff`)
- YouTube support — scan any video, playlist, or channel by URL
  - Supports: `youtube.com`, `youtu.be`, `music.youtube.com`, `kids.youtube.com`, `gaming.youtube.com`
  - Uses YouTube Data API v3 with yt-dlp as fallback
- Per-video YouTube cache — previously fetched videos skip the API entirely
- Path alias system — define short names for long folder paths
- Self-update command (`aevum update` / `aevum -U`)
- Machine-readable JSON output mode (`--json`) for scripting
- Quiet mode (`-q`) for use in pipelines
- Persistent config system (`sort`, `top`, `no_color`, `export_dir`, etc.)
- Clean, colored terminal UI with `--no-color` for plain output
- Full CLI with subcommands
- Fuzzy-match typo suggestions for unknown commands and sort fields
- Paths with spaces work without quotes

---

## Requirements

- **Python 3.10+** — https://python.org
- **FFmpeg** (includes `ffprobe`) — https://ffmpeg.org/download.html  
  After downloading, make sure FFmpeg is on your system PATH.
- **Optional**: `keyring` and `cryptography` for encrypted API key storage

---

## Installation

```bash
git clone https://github.com/elsherif7/aevum
cd aevum
pip install .

# Optional: encrypted API key storage
pip install keyring cryptography
```

### Update

```bash
aevum update
```

Shows a clean animated progress bar. Aevum saves your project folder path after the first update so you can run `aevum update` from anywhere. Use `aevum clearpath` to reset this.

Or manually:

```bash
cd aevum
pip install . --upgrade
```

### Uninstall

```bash
pip uninstall aevum
```

---

## Usage

### Scan a folder

```bash
aevum scan D:\Movies
aevum scan D:\Movies --sort duration --top 20
aevum scan D:\Movies --files --out report.csv
aevum scan D:\Movies --min-duration 5m --ext mkv,mp4
aevum scan D:\Movies --depth 2
aevum scan D:\Movies --exclude trailers,samples,extras
aevum scan D:\Movies --since 30d
aevum scan D:\Movies --since 2w
aevum scan D:\Movies --since 2025-01-01
aevum scan D:\Movies --until 2025-06-01
aevum scan D:\Movies --speed 1.5 --speed 2.5
```

### Scan multiple folders

```bash
aevum scan D:\Movies E:\Shows
aevum scan D:\Movies E:\Shows --merge
```

### Scan a YouTube URL

```bash
aevum scan https://youtube.com/@mkbhd
aevum scan https://youtube.com/playlist?list=PLxxx
aevum scan https://youtu.be/dQw4w9WgXcQ
aevum scan https://music.youtube.com/playlist?list=PLxxx
aevum scan https://kids.youtube.com/channel/UCxxx
```

### Watch mode

```bash
aevum watch D:\Movies
aevum watch D:\Movies --interval 10
aevum watch D:\Downloads --ext mkv,mp4 --min-duration 5m
```

### Compare two folders

```bash
aevum compare D:\Movies E:\Movies-Backup
```

### Find duplicates

```bash
aevum dupes D:\Movies
aevum dupes D:\Movies -o dupes.txt
```

### Show all files

```bash
aevum files D:\Movies
aevum files D:\Movies --sort name
```

### Export results

```bash
aevum export D:\Movies csv
aevum export D:\Movies json -o D:\Reports\library.json
aevum export D:\Movies html -o D:\Reports\library.html
aevum export https://youtube.com/@mkbhd json
```

### Deep statistics

```bash
aevum stats D:\Movies
aevum stats D:\Movies --json
```

Shows average, median, shortest and longest file, format distribution (MP4 vs MKV vs ...), size distribution buckets, and densest folder.

### One-line summary

```bash
aevum summary D:\Movies
aevum summary D:\Movies --json
```

Output: `Movies → 1,243 files | 312h 44m | 2.1 TB`

### Recent files

```bash
aevum recent D:\Movies
aevum recent D:\Movies --since 7d
aevum recent D:\Movies --since 2025-01-01 --limit 100
```

Shows files modified within the last 30 days by default, sorted newest first.

### Top files

```bash
aevum top D:\Movies
aevum top D:\Movies --by size
aevum top D:\Movies --limit 50 --by size
```

Shows top N files by duration (default) or by file size.



```bash
aevum history D:\Movies        # list all past scans
aevum diff D:\Movies           # what changed since last scan
```

Every `aevum scan` automatically saves a snapshot. `aevum diff` shows added and removed files with their durations.

### Aliases

```bash
aevum alias list
aevum alias set M D:\02-Media
aevum alias remove M          # also: aevum alias rm M
aevum scan M
```

Aliases can point to a path, a flag, or any command fragment. They cannot be overwritten — remove first, then re-add.

### Config

```bash
aevum config list
aevum config set sort duration:desc
aevum config set top 20
aevum config set yt_api_key AIzaSy...
aevum config reset
```

### Cache

```bash
aevum cache list
aevum cache clear
aevum cache clear D:\Movies
aevum cache path
```

### Other commands

```bash
aevum quota             # Check YouTube API quota usage for today
aevum appdata           # Open Aevum data folder in Explorer / Finder
aevum doctor            # Check environment (ffprobe, API key, cache)
aevum clearpath         # Clear saved project path used by 'aevum update'
aevum update            # Upgrade to latest version
aevum update --dry-run  # Preview what would run without upgrading
aevum version           # Print version
```

---

## All commands

```
aevum                           Show help and exit
aevum <path|url>                Quick scan (shorthand for 'aevum scan')
aevum scan      <path|url>      Scan one or more folders or YouTube URLs
aevum compare   <path> <path>   Compare two libraries side-by-side
aevum dupes     <path>          Find duplicate files
aevum export    <path> <fmt>    Scan and write results to a file
aevum watch     <path>          Re-scan automatically when folder changes
aevum files     <path>          Scan and show all files under each folder
aevum stats     <path>          Deep statistics: avg, median, formats, sizes
aevum summary   <path>          One-line summary of a folder
aevum history   <path>          Show past scan snapshots
aevum diff      <path>          Show what changed since the last scan
aevum recent    <path>          Show recently added or modified files
aevum top       <path>          Show top N files by duration or size
aevum alias                     Manage short aliases for folder paths
aevum cache                     Manage the duration cache
aevum config                    Read/write configuration
aevum doctor                    Check environment (ffprobe, API key, cache)
aevum quota                     Check YouTube API quota usage for today
aevum update                    Upgrade Aevum to the latest version
aevum clearpath                 Clear saved project path used by 'update'
aevum appdata                   Open the Aevum data folder
aevum version                   Print version and exit
```

Run `aevum <command> --help` for options on any command.

---

## Scan options

| Flag | Description |
|---|---|
| `-s, --sort FIELD[:DIR]` | Sort tree: `name`, `duration`, or `count`; optionally `:asc` or `:desc` |
| `-t, --top N` | Show top N longest files (default: 10, set 0 to hide) |
| `-f, --files` | Show individual files under each folder in the tree |
| `-o, --out FILE` | Write results to FILE (format inferred from extension) |
| `--format txt\|csv\|json\|html` | Explicit export format |
| `--depth N` | Limit tree display to N levels deep |
| `--merge` | Aggregate all target folders into one combined grand total |
| `--min-duration DURATION` | Exclude files shorter than this (e.g. `30s`, `5m`, `1h`, `1:30:00`) |
| `--max-duration DURATION` | Exclude files longer than this |
| `--ext EXT[,EXT]` | Only include these extensions, comma-separated (e.g. `mkv,mp4`) |
| `--folder PATTERN` | Only include files inside folders matching this glob (e.g. `Action*`) |
| `--exclude PATTERN[,PATTERN]` | Exclude folders matching these patterns (e.g. `trailers,samples`) |
| `--since DATE` | Only include files modified after this date (e.g. `7d`, `30d`, `2w`, `2025-01-15`) |
| `--until DATE` | Only include files modified before this date (same format as `--since`) |
| `--speed SPEED` | Add a custom playback speed to the breakdown — repeatable (e.g. `--speed 1.5 --speed 3`) |
| `--no-cache` | Bypass the duration cache and re-probe every file |
| `--no-color` | Strip ANSI colors from output |
| `--json` | Output machine-readable JSON to stdout |
| `-q, --quiet` | Suppress all decorative output (errors → stderr only) |

> **Note:** `-f/--files`, `-o/--out`, `--format`, `--depth`, and `--merge` are `scan`-only flags. `--min-duration`, `--max-duration`, `--ext`, `--folder`, `--exclude`, `--since`, `--until`, `--speed` also work on `watch`.

## Watch options

| Flag | Description |
|---|---|
| `-i, --interval SECONDS` | Poll interval in seconds (default: 5) |
| `--no-clear` | Don't clear the screen between updates |
| `-s, --sort FIELD[:DIR]` | Sort field (same as scan) |
| `-t, --top N` | Top N files to show |
| `--min-duration / --max-duration` | Duration filters (same as scan) |
| `--ext EXT[,EXT]` | Extension filter (same as scan) |
| `--folder PATTERN` | Subfolder name filter (same as scan) |
| `--exclude PATTERN[,PATTERN]` | Exclude folders by name pattern |
| `--since / --until DATE` | Date filters (same as scan) |
| `--speed SPEED` | Custom playback speed (same as scan) |
| `--no-cache` | Bypass cache |
| `--json` | Stream newline-delimited JSON (includes `watch_update`, `changed`, `total_sec_delta` fields) |

## Export options

| Flag | Description |
|---|---|
| `format` | Output format: `txt`, `csv`, `json`, or `html` (required positional argument) |
| `-o, --out FILE` | Output file path (auto-generated with timestamp if omitted) |
| `-s, --sort FIELD[:DIR]` | Sort order for the exported file |
| `--no-cache` | Bypass cache and re-probe every file |

## Stats / Summary options

| Flag | Description |
|---|---|
| `--exclude PATTERN[,PATTERN]` | Exclude folders by name pattern before computing stats |
| `--no-cache` | Bypass cache |

## Compare options

| Flag | Description |
|---|---|
| `-s, --sort FIELD[:DIR]` | Sort field used when scanning each folder |
| `--no-cache` | Bypass cache |

## Dupes options

| Flag | Description |
|---|---|
| `-o, --out FILE` | Write duplicate report to a text file |
| `--no-cache` | Bypass cache |

## Update options

| Flag | Description |
|---|---|
| `--dry-run` | Show the pip command that would run without actually upgrading |

## Alias subcommands

```bash
aevum alias list                  # list all aliases with type labels ([path], [command], [flag])
aevum alias set <name> <value>    # create alias — value can be a path, flag, or command fragment
aevum alias remove <name>         # remove an alias (also: alias rm <name>)
```

Aliases cannot be overwritten — remove first with `aevum alias rm <name>`, then re-add.

## Cache subcommands

```bash
aevum cache list                  # list all cache files with folder paths and sizes
aevum cache clear                 # delete all local + YouTube cache files
aevum cache clear <path>          # delete cache for one specific folder only
aevum cache path                  # print the cache directory path
```

## Config subcommands

```bash
aevum config list                 # show all keys and current values
aevum config get <key>            # print one value
aevum config set <key> <value>    # set a value
aevum config reset                # reset everything to defaults
```

## Global options

| Flag | Description |
|---|---|
| `--no-color` | Disable ANSI color output |
| `--json` | Machine-readable JSON output to stdout |
| `-q, --quiet` | Suppress decorative output; only errors go to stderr |
| `-h, --help` | Show help |
| `-V, --version` | Show version |
| `-U, --upgrade` | Alias for `aevum update` |

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Bad arguments / path not found / not a directory |
| 2 | Missing dependency (ffprobe not on PATH) |
| 3 | Scan failed / interrupted |
| 4 | Export / write failed |
| 5 | YouTube API error / auth failure |

---

## JSON / scripting mode

Every subcommand supports `--json` for machine-readable output:

```bash
aevum scan D:\Movies --json
aevum scan D:\Movies --json | python -m json.tool
aevum dupes D:\Movies --json | python -c "import sys,json; d=json.load(sys.stdin); print(d['groups_found'])"
aevum stats D:\Movies --json
aevum summary D:\Movies --json
aevum history D:\Movies --json
aevum diff D:\Movies --json
aevum doctor --json
aevum quota --json
aevum scan D:\Movies -q; echo "exit $?"
```

---

## Config reference

Config is stored at `%LOCALAPPDATA%\Aevum\config.json` on Windows, or `~/.local/share/Aevum/config.json` on Linux/macOS.

| Key | Default | Description |
|---|---|---|
| `sort` | `name:asc` | Default sort field and direction |
| `top` | `10` | Default number of top files to show (0 to hide) |
| `no_color` | `false` | Disable ANSI colors globally |
| `cache_enabled` | `true` | Enable/disable the duration cache |
| `export_dir` | `` | Default directory for exported files |
| `aliases` | `{}` | User-defined path shortcuts (e.g. `{"M": "D:\\02-Media"}`) |
| `yt_api_key` | `` | YouTube Data API v3 key (stored in OS keyring if available) |
| `project_dir` | `` | Path to Aevum source folder, saved automatically by `aevum update` |

---

## Cache

Aevum caches video durations so repeat scans of large libraries are near-instant. Each file is cached by its absolute path, mtime, and size — stale entries are automatically ignored. Use `--no-cache` to force a full re-probe, or `aevum cache clear <folder>` to remove the cache for a specific folder only.

The cache works correctly on all filesystems including FAT32 and exFAT (common on external drives and SD cards), which have 2-second mtime precision.

YouTube video durations are cached separately per video ID, so re-scanning a channel only fetches new uploads from the API.

Scan history snapshots are stored separately and kept for the last 50 scans per folder.

| Platform | Cache / data location |
|---|---|
| Windows | `%LOCALAPPDATA%\Aevum\` |
| Linux / macOS | `~/.local/share/Aevum/` |

Run `aevum appdata` to open the Aevum data folder directly.

---

## YouTube support

Aevum supports all YouTube properties:

| URL | Supported |
|---|---|
| `youtube.com` videos, playlists, channels | Yes |
| `youtu.be` short links | Yes |
| `m.youtube.com` mobile links | Yes |
| `music.youtube.com` | Yes |
| `kids.youtube.com` | Yes |
| `gaming.youtube.com` | Yes |

Requires a free YouTube Data API v3 key. Set it once with:

```bash
aevum config set yt_api_key AIzaSy...
```

---

## Supported formats

Aevum supports **247 file extensions** covering virtually every known video and audio format that FFmpeg can read.

### Video (selected)
`.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v` `.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` `.divx` `.rmvb` `.asf` `.m2ts` `.mts` `.m2v` `.f4v` `.mxf` `.dv` `.wtv` `.bdmv` `.nuv` `.av1` `.avif` `.ivf` `.y4m` `.mjpeg` `.mjpg` `.gxf` `.mlv` `.thp` `.swf` `.vc1` `.vp6` `.vp8` `.vp9` `.h264` `.h265` `.hevc` and more

### Audio (selected)
`.mp3` `.aac` `.flac` `.wav` `.ogg` `.wma` `.m4a` `.opus` `.aiff` `.ape` `.wv` `.tta` `.ac3` `.eac3` `.dts` `.dtshd` `.truehd` `.pcm` `.caf` `.flac` `.dsf` `.dff` `.tak` `.shn` `.qoa` `.hca` `.voc` `.aa` `.aax` `.mpc` `.qcp` `.bonk` `.osq` and more

---

## Project structure

```
aevum/
  aevum.py           — entry point
  pyproject.toml     — pip install config
  README.md
  .gitignore
  aevum_pkg/
    __init__.py      — public API surface
    _cli.py          — main(), arg parsing, subcommand dispatch
    _scan.py         — ffprobe, native parsers, tree builder, filters
    _youtube.py      — YouTube Data API v3 + per-video cache + quota tracking
    _display.py      — all print/display functions (tree, bar chart, stats)
    _compare.py      — folder comparison logic
    _dupes.py        — duplicate detection (BLAKE2b)
    _export.py       — TXT/CSV/JSON/HTML export + path validation
    _history.py      — scan history snapshots and diff
    _config.py       — config, cache commands, doctor
    _cache.py        — duration cache read/write
    _paths.py        — platform-aware AppData/XDG path resolution
    _color.py        — ANSI color singleton (clr)
    _apikey.py       — encrypted API key storage (keyring → Fernet → plaintext)
    _exit.py         — CLI exit codes
```

---

## Security

See [SECURITY.md](SECURITY.md) for the full security design, vulnerability reporting process, and known limitations.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License

MIT — see [LICENSE](LICENSE)
