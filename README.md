# Aevum

**Media Library Scanner.**  
Point it at any folder or YouTube URL and instantly see the total duration — broken down by subfolder, with a grand total, size, playback speeds, and top longest files. Works on Windows, Linux, and macOS.

---

## Features

- Scans local folders recursively for video and audio files
- Displays duration and size per subfolder in a color-coded tree view
- Grand total in days, hours, and minutes, plus total library size
- Playback speed breakdown (1x → 2x)
- Top 10 longest files listed after each scan (configurable)
- Duration cache — repeat scans are near-instant (keyed by mtime + size)
- Fast native MP4/MKV/WebM header parsing (ffprobe only as fallback)
- Parallel scanning with a configurable thread pool
- Duplicate detection by size + partial SHA-1 hash
- Folder comparison mode — diff two libraries side by side
- Sortable tree (by name, duration, or count; ascending or descending)
- Filter by duration range, extension, or subfolder name pattern
- Scan multiple folders at once, optionally merged into one grand total
- Watch mode — re-scan automatically when folder contents change
- Export results to TXT, CSV, or JSON
- YouTube support — scan any video, playlist, or channel by URL
- Path alias system — define short names for long folder paths
- Self-update command (`aevum update` / `aevum -U`)
- Machine-readable JSON output mode (`--json`) for scripting
- Quiet mode (`-q`) for use in pipelines
- Persistent config system (`sort`, `top`, `no_color`, `export_dir`, etc.)
- Clean, colored terminal UI with `--no-color` for plain output
- Interactive REPL mode and full headless CLI with subcommands
- Fuzzy-match typo suggestions for unknown commands and sort fields

---

## Requirements

- **Python 3.8+** — https://python.org
- **FFmpeg** (includes `ffprobe`) — https://ffmpeg.org/download.html  
  After downloading, make sure FFmpeg is on your system PATH.

---

## Installation

```
git clone <repo-url>
cd Aevum
pip install .
```

That's it. `aevum` will work from any terminal window after install.

### Update

```
aevum update
```

Shows a clean animated progress bar instead of pip's verbose output. Aevum remembers your project folder path after the first update so you can run `aevum update` from anywhere.

Or manually from the project folder:

```
cd Aevum
pip install . --upgrade
```

### Uninstall

```
pip uninstall aevum
```

---

## Usage

### Interactive mode (REPL)

```
aevum
```

Type any folder path or YouTube URL at the prompt. After each scan a menu appears with options to scan again, sort, export, check duplicates, or quit.

### Headless mode — scan a folder

```
aevum scan D:\Movies
aevum scan D:\Movies --sort duration --top 20
aevum scan D:\Movies --files --out report.csv
aevum scan D:\Movies --min-duration 5m --ext mkv,mp4
```

### Scan multiple folders

```
aevum scan D:\Movies E:\Shows
aevum scan D:\Movies E:\Shows --merge
```

### Scan a YouTube URL

```
aevum scan https://youtube.com/@mkbhd
aevum scan https://youtube.com/playlist?list=PLxxx
aevum scan https://youtu.be/dQw4w9WgXcQ
```

### Watch mode — live re-scan

```
aevum watch D:\Movies
aevum watch D:\Movies --interval 10
aevum watch D:\Downloads --ext mkv,mp4 --min-duration 5m
```

### Compare two folders

```
aevum compare D:\Movies E:\Movies-Backup
```

### Find duplicates

```
aevum dupes D:\Movies
aevum dupes D:\Movies -o dupes.txt
```

### Export results

```
aevum export D:\Movies csv
aevum export D:\Movies json -o D:\Reports\library.json
```

### Aliases

```
aevum alias list
aevum alias set M D:\02-Media
aevum alias remove M
aevum scan M
```

### Config

```
aevum config list
aevum config set sort duration:desc
aevum config set top 20
aevum config set yt_api_key AIzaSy...
aevum config reset
```

### Cache management

```
aevum cache list
aevum cache clear
aevum cache clear D:\Movies
aevum cache path
```

### Check YouTube API quota

```
aevum quota
```

### Open AppData folder

```
aevum appdata
```

### Environment check

```
aevum doctor
```

### Self-update

```
aevum update
aevum update --dry-run
aevum -U
```

---

## All commands

```
aevum                           Open interactive shell
aevum <path|url>                Quick scan (shorthand for 'aevum scan')
aevum scan      <path|url>      Scan one or more folders or YouTube URLs
aevum compare   <path> <path>   Compare two libraries side-by-side
aevum dupes     <path>          Find duplicate files
aevum export    <path> <fmt>    Scan and write results to a file
aevum watch     <path>          Re-scan automatically when folder changes
aevum files     <path>          Scan and show all files under each folder
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
| `--format txt\|csv\|json` | Explicit export format |
| `--depth N` | Limit tree display to N levels deep |
| `--merge` | Aggregate all target folders into one combined grand total |
| `--min-duration DURATION` | Exclude files shorter than this (e.g. `30s`, `5m`, `1h`, `1:30:00`) |
| `--max-duration DURATION` | Exclude files longer than this |
| `--ext EXT[,EXT]` | Only include these extensions, comma-separated (e.g. `mkv,mp4`) |
| `--folder PATTERN` | Only include files inside folders matching this glob (e.g. `Action*`) |
| `--no-cache` | Bypass the duration cache and re-probe every file |
| `--no-color` | Strip ANSI colors from output |
| `--json` | Output machine-readable JSON to stdout |
| `-q, --quiet` | Suppress all decorative output (errors → stderr only) |

---

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

---

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

```
aevum scan D:\Movies --json
aevum scan D:\Movies --json | python -m json.tool
aevum dupes D:\Movies --json | python -c "import sys,json; d=json.load(sys.stdin); print(d['groups_found'])"
aevum scan D:\Movies -q; echo "exit $?"
```

---

## Supported formats

### Video

`.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v` `.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` `.divx` `.rmvb` `.asf` `.m2ts` `.mts` `.m2v` `.f4v` `.f4p` `.nsv` `.roq` `.yuv` `.mxf` `.drc` `.gifv` `.mng` `.qt` `.rm` `.amv` `.svi` `.3g2` `.mpe` `.mpv` `.m1v` `.m2p` `.m4p` `.mpeg1` `.mpeg2` `.mpeg4` `.h264` `.h265` `.hevc` `.avchd` `.ogm` `.ogx` `.dv` `.dvr` `.dvr-ms` `.rec` `.wtv` `.bdmv` `.iso` `.evo` `.ifo` `.mod` `.tod` `.trp` `.tp` `.pva` `.nuv` `.fli` `.flc` `.flic` `.smk` `.bik` `.bik2` `.webp`

### Audio

`.mp3` `.aac` `.flac` `.wav` `.ogg` `.wma` `.m4a` `.opus` `.aiff` `.aif` `.aifc` `.ape` `.wv` `.tta` `.mka` `.mpa` `.mp2` `.ac3` `.eac3` `.dts` `.dtshd` `.truehd` `.thd` `.pcm` `.caf` `.ra` `.ram` `.oga` `.spx` `.amr` `.awb` `.gsm` `.au` `.snd` `.vox` `.8svx` `.iff` `.svx` `.f32` `.f64` `.s8` `.s16` `.s24` `.s32` `.u8` `.u16` `.u24` `.u32` `.w64` `.rf64` `.bwf` `.mid` `.midi` `.kar` `.xmf` `.mxmf` `.rtttl` `.rtx` `.ota` `.imy` `.mp1`

---

## Project structure

```
Aevum/
  aevum.py           — entry point
  pyproject.toml     — pip install config
  aevum_pkg/
    _cli.py          — main(), arg parsing, REPL, subcommand dispatch
    _scan.py         — ffprobe, native parsers, tree builder, filters
    _youtube.py      — YouTube Data API v3 integration
    _display.py      — all print/display functions
    _dupes.py        — duplicate detection
    _compare.py      — folder comparison
    _export.py       — TXT/CSV/JSON export
    _config.py       — config, cache commands, doctor
    _cache.py        — cache read/write
    _color.py        — ANSI color singleton
    _exit.py         — named exit codes
```

---

## Cache

Aevum caches video durations so repeat scans of large libraries are near-instant. Each file is cached by its absolute path, mtime, and size — stale entries are automatically ignored. Use `--no-cache` to force a full re-probe, or `aevum cache clear <folder>` to remove the cache for a specific folder only.

| Platform | Cache location |
|---|---|
| Windows | `%LOCALAPPDATA%\Aevum\cache\` |
| Linux / macOS | `~/.local/share/Aevum/cache\` (falls back to `~`) |

Run `aevum appdata` to open the Aevum data folder directly.

---

## Config

Config is stored at `%LOCALAPPDATA%\Aevum\config.json` on Windows, or `~/.local/share/Aevum/config.json` on Linux/macOS.

| Key | Default | Description |
|---|---|---|
| `sort` | `name:asc` | Default sort field and direction |
| `top` | `10` | Default number of top files to show |
| `no_color` | `false` | Disable ANSI colors globally |
| `cache_enabled` | `true` | Enable/disable the duration cache |
| `export_dir` | `` | Default directory for exported files |
| `aliases` | `{}` | User-defined path shortcuts (e.g. `{"M": "D:\\02-Media"}`) |
| `yt_api_key` | `` | YouTube Data API v3 key (stored separately in `yt_api_key.txt`) |
