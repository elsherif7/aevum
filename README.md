# Aevum

**Video library duration scanner for Windows.**  
Point it at any folder and instantly see the total watch time — broken down by subfolder, with a grand total at the end.

---

## Features

- Recursively scans any folder for video files
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
- Supports external drives, USB sticks, network paths — anything Windows can see
- Drag-and-drop a folder into the terminal window
- Clean, colored terminal UI with `--no-color` for plain output

---

## Requirements

- **Python 3** — https://python.org
- **FFmpeg** (includes `ffprobe`) — https://ffmpeg.org/download.html  
  After downloading, make sure FFmpeg is on your system PATH.

---

## Installation

1. Download or clone this repo
2. Right-click `install.bat` → **Run as administrator**
3. Open a new Command Prompt and type:

```
aevum
```

The installer copies `aevum.py` to `%LOCALAPPDATA%\Aevum` and drops a launcher into your Python Scripts folder so `aevum` works from any terminal.

---

## Usage

### Interactive mode

```
aevum
```

Then enter any folder path at the prompt. After each scan a menu appears with options to scan again, sort, export, check duplicates, or quit.

### Headless mode

```
aevum D:\Movies
```

Scans the folder, prints results, and exits. Combine with flags for automation:

```
aevum D:\Movies --export csv
aevum D:\Movies --sort duration --top 20 --no-color
```

### Subcommands

```
aevum compare D:\Movies E:\Backup    # side-by-side comparison of two folders
aevum dupes D:\Movies                # find duplicate video files
```

---

## Example output

```
  ================================================================
    A E V U M  |  Video Library Duration Scanner
  ================================================================

  aevum> D:\Movies
  Scanning...  ████████████████░░░░░░░░  288/312  (92%)

  Done!  312 videos found.  (180 cached, 132 probed)

  ================================================================
    Video Library  |  Folder Summary
  ================================================================

  Movies
      +--  438h 12m 05s  |  312 videos  |  241.3 GB

      1.  Action
          +--  82h 44m 11s  |  58 videos  |  61.2 GB
      ...

  ================================================================
    Grand Total
  ================================================================
  Total videos  :  312
  Total size    :  241.3 GB
  Days          :  18d 06h 12m 05s
  Hours         :  438h 12m 05s
  Minutes       :  26292m 05s
  ================================================================
    Playback Speed
  ================================================================
  1x      :  438h 12m 05s  (18d 06h 12m 05s)
  1.25x   :  350h 33m 38s  (14d 14h 33m 38s)
  ...
  ================================================================
```

---

## Interactive commands

| Input | Action |
|---|---|
| Any folder path | Scan that folder |
| `clear` / `c` | Clear the screen |
| `exit` / `quit` / `q` | Quit |
| `Ctrl+C` | Cancel a scan or quit |

Post-scan menu options: `scan`, `sort`, `export`, `clear`, `quit`, `duplicates`

---

## CLI flags

| Flag | Description |
|---|---|
| `--export` / `-e` `txt\|csv\|json` | Export results to a file |
| `--out` / `-o` `PATH` | Output path for `--export` (default: auto-named next to folder) |
| `--sort` / `-s` `FIELD[:DIR]` | Sort tree: `name`, `duration`, or `count`; optionally `:asc` or `:desc` |
| `--top` / `-t` `N` | Show top N longest files (default: 10, set 0 to hide) |
| `--files` / `-f` | Show individual files under each folder in the tree |
| `--no-cache` | Bypass the duration cache and re-probe every file |
| `--no-color` | Strip ANSI colors from output |
| `--version` / `-v` | Print version and exit |

---

## Supported formats

`.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v` `.mpg` `.mpeg` `.3gp` `.ts` `.vob` `.ogv` `.divx` `.rmvb` `.asf` `.m2ts`

---

## Cache

Aevum caches video durations in `%LOCALAPPDATA%\Aevum\cache\` so repeat scans of large libraries are near-instant. Each file is cached by its absolute path, mtime, and size — stale entries are automatically ignored. Use `--no-cache` to force a full re-probe.

---

## Uninstall

Run `uninstall.bat` as administrator. It removes the app folder and launcher — nothing else is touched.
