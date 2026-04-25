# Aevum

**Media Library Scanner for Windows.**  
Point it at any folder or YouTube URL and instantly see the total duration — broken down by subfolder, with a grand total, size, playback speeds, and top longest files.

---

## Features

- Scans local folders recursively for video and audio files
- Displays duration and size per subfolder in a tree view
- Grand total in days, hours, and minutes, plus total library size
- Playback speed breakdown (1x → 2x)
- Top 10 longest files listed after each scan
- Duration cache — repeat scans are near-instant (keyed by mtime + size)
- Fast native MP4/MKV/WebM header parsing (ffprobe only as fallback)
- Parallel scanning with a configurable thread pool
- Duplicate detection by size + partial hash
- Folder comparison mode — diff two libraries side by side
- Sortable tree (by name, duration, or count; ascending or descending)
- Export results to TXT, CSV, or JSON
- YouTube support — scan any video, playlist, or channel by URL
- Persistent config system (`sort`, `top`, `no_color`, `export_dir`, etc.)
- Clean, colored terminal UI with `--no-color` for plain output
- Interactive REPL mode and full headless CLI with subcommands

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
```

### Scan a YouTube URL

```
aevum scan https://youtube.com/@mkbhd
aevum scan https://youtube.com/playlist?list=PLxxx
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
aevum cache path
```

### Environment check

```
aevum doctor
```

---

## All commands

```
aevum                           Open interactive shell
aevum <path|url>                Quick scan (shorthand for 'aevum scan')
aevum scan      <path|url>      Scan a folder or YouTube URL
aevum compare   <path> <path>   Compare two libraries side-by-side
aevum dupes     <path>          Find duplicate files
aevum export    <path> <fmt>    Scan and write results to a file
aevum cache                     Manage the duration cache
aevum config                    Read/write configuration
aevum doctor                    Check environment (ffprobe, API key, cache)
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
| `--no-cache` | Bypass the duration cache and re-probe every file |
| `--no-color` | Strip ANSI colors from output |

---

## Supported formats

**Video:** `.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v` `.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` and many more

**Audio:** `.mp3` `.aac` `.flac` `.wav` `.ogg` `.wma` `.m4a` `.opus` `.flac` and many more

---

## Project structure

```
Aevum/
  aevum.py           — entry point
  pyproject.toml     — pip install config
  aevum_pkg/
    _cli.py          — main(), arg parsing, REPL
    _scan.py         — ffprobe, native parsers, tree builder
    _youtube.py      — YouTube Data API v3 integration
    _display.py      — all print/display functions
    _dupes.py        — duplicate detection
    _compare.py      — folder comparison
    _export.py       — TXT/CSV/JSON export
    _config.py       — config, cache commands, doctor
    _cache.py        — cache read/write
    _color.py        — ANSI color constants
```

---

## Cache

Aevum caches video durations in `%LOCALAPPDATA%\Aevum\cache\` so repeat scans of large libraries are near-instant. Each file is cached by its absolute path, mtime, and size — stale entries are automatically ignored. Use `--no-cache` to force a full re-probe.

---

## Config

Config is stored at `%LOCALAPPDATA%\Aevum\config.json`.

| Key | Default | Description |
|---|---|---|
| `sort` | `name:asc` | Default sort field and direction |
| `top` | `10` | Default number of top files to show |
| `no_color` | `false` | Disable ANSI colors globally |
| `cache_enabled` | `true` | Enable/disable the duration cache |
| `export_dir` | `` | Default directory for exported files |
| `yt_api_key` | `` | YouTube Data API v3 key |
