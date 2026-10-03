# Aevum

A minimal CLI that scans a local folder or a YouTube URL and reports
total media duration, broken down by subfolder — so you can see how
large a video or audio library is without opening every folder by
hand.

---

## Structure

```
aevum/
├── aevum.py              # Entry point — delegates to aevum_pkg._cli:main
├── pyproject.toml        # Packaging, ruff, mypy config
├── LICENSE               # GNU General Public License v3.0
├── scripts/
│   └── clean.py          # Dev tool: removes build artifacts (build/, egg-info/, __pycache__)
├── tests/                # pytest suite (needs ffmpeg/ffprobe on PATH; no network or API key)
│   ├── conftest.py       # Shared fixtures: ffmpeg-generated media, isolated CLI runner
│   ├── test_cli.py       # Arguments, exit codes, end-to-end scans
│   ├── test_color.py     # NO_COLOR / FORCE_COLOR, terminal vs piped output
│   ├── test_display.py   # Output sanitizing, fuzzy suggestions, bar
│   ├── test_project.py   # pyproject.toml settings, Python-version floor
│   ├── test_scan.py      # Duration parsing (MP4/MKV/ffprobe), folder tree
│   └── test_youtube.py   # URL/duration parsing, API key, retries, cache
└── aevum_pkg/
    ├── _cli.py           # Argument parsing + main() — the only command is 'scan'
    ├── _cli_cmds.py      # cmd_scan, progress bar, ffprobe availability check
    ├── _scan.py          # Local folder scanning (native MP4/MKV parsing + ffprobe fallback)
    ├── _youtube.py       # YouTube Data API v3 scanning (channels, playlists, videos) + API key storage
    ├── _display.py       # Human-readable output (tree, bar chart, top files)
    ├── _models.py        # FolderNode / ScanTree data types
    ├── _text.py          # Sanitizing untrusted text for the terminal, scanner warnings
    ├── _color.py         # ANSI color handling (off when output isn't a terminal)
    ├── _paths.py         # Platform-correct data directory paths
    └── _exit.py          # Exit code constants
```

---

## Requirements

- Python 3.11 or newer (tested on 3.11 to 3.14)
- `ffprobe` (part of [FFmpeg](https://ffmpeg.org/download.html)) on
  your `PATH`, needed for local folder scanning
- A free YouTube Data API v3 key, needed only for scanning YouTube
  URLs — see [YouTube API key](#youtube-api-key) below

---

## Installation

Aevum is installed from source, straight from GitHub. It needs
`pip` with `setuptools` 77 or newer, which pip fetches for you in the
build step.

1. Clone the repo:
   ```
   git clone https://github.com/elsherif7/aevum
   cd aevum
   ```
2. Install it:
   ```
   pip install .
   ```
3. To update later, pull the latest changes and install again:
   ```
   git pull
   pip install .
   ```

This gives you the `aevum` command.

**Without cloning:** install (or update) straight from GitHub.

```
pip install --force-reinstall git+https://github.com/elsherif7/aevum
```

---

## Development

Install in editable mode with the dev tools, then lint, type-check,
test, and clean up when you're done:

```
pip install -e ".[dev]"
ruff check .
mypy
pytest
python3 scripts/clean.py   # remove build artifacts when you're done
```

---

## How it works

**Local folder** — `aevum scan <path>` walks every subfolder, reads
each media file's duration (a fast native MP4/MKV header parser first,
falling back to `ffprobe` for other formats), and prints a folder tree
with per-subfolder duration and size, a duration breakdown bar chart,
playback-speed conversions (1x/1.25x/1.5x/1.75x/2x), and the 10
longest files. `ffprobe` gets 30 seconds per file, and folders more
than 100 levels below the one you scan are not scanned. A `.ts`, `.mod`
or `.scm` file that is plain text (source code, not media) is ignored.

**YouTube URL** — `aevum scan <url>` accepts a channel, playlist, or
single video URL, fetches duration data via the YouTube Data API v3,
and prints the same kind of summary for the videos it finds.

```
aevum scan D:\Movies
aevum scan "/home/user/My Videos"
aevum scan https://youtube.com/@somechannel
aevum scan https://youtube.com/playlist?list=...
aevum scan https://youtube.com/watch?v=...
```

Video links can also be `youtu.be/ID`, `/shorts/ID`, `/live/ID` or
`/embed/ID`. The `https://` part is optional.

A few other things worth knowing:

- If a folder exists with a name that looks like a web address (for
  example `www.backup`), it is scanned as a folder.
- A file that can be reached by more than one path (a hardlink, or a
  symlink to a file) is counted once, and Aevum says how many
  duplicates it skipped.
- If something could not be counted, a short line after "Done!" says so:
  media files that could not be read (and how many of those timed out),
  folders that could not be opened, and folders nested too deep. The
  totals leave those out, and the exit code is still 0.
- Colors are used only when output goes to a terminal. Set `NO_COLOR=1`
  to turn them off, or `FORCE_COLOR=1` to keep them when piping. When
  output is piped or redirected, the progress bar is left out.
- `scan` is required — there's no bare `aevum <path>` shorthand.
- A path containing spaces must be quoted, or it's rejected with a
  hint rather than guessed at.
- The first time you scan a YouTube URL, Aevum prompts for a free API
  key and saves it locally so you won't be asked again.
- A link to a video inside a playlist (`watch?v=...&list=...`) scans
  just that video. Use the `/playlist?list=...` link to scan the whole
  playlist.
- Aevum keeps no quota count of its own. YouTube decides when you've hit
  a limit. If it says the daily quota is used up part-way through a big
  channel or playlist, the videos already fetched are saved, and you can
  run the same command again after the quota resets (midnight Pacific
  Time) to carry on without fetching them twice. Brief rate limits and
  temporary network or server errors are retried automatically. If
  YouTube asks you to wait more than 30 seconds, Aevum stops, keeps what
  it fetched, and tells you when to try again.
- Private, deleted, or region-blocked videos are remembered for 7 days,
  so reruns don't spend quota asking about them again.

---

## YouTube API key

Get a free key in about two minutes:

1. Go to <https://console.cloud.google.com/>
2. Create a project → enable **YouTube Data API v3**
3. Credentials → Create API Key → paste it when Aevum asks

The key is saved as plain text to `~/.local/share/Aevum/yt_api_key.txt`
on Linux/macOS or `%LOCALAPPDATA%\Aevum\yt_api_key.txt` on Windows.

- On Linux/macOS the file is created readable by your user only
  (mode 600).
- On Windows Aevum doesn't change the file's permissions. It sits in
  your profile folder, which other standard user accounts can't read by
  default.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Bad arguments / path not found |
| 2 | Missing dependency (`ffprobe` not on `PATH`) |
| 3 | Scan failed or was interrupted |
| 4 | Not used |
| 5 | YouTube API error |

---

## Privacy

Aevum makes no network requests except to the YouTube Data API v3,
and only when you scan a YouTube URL. It does not collect, store, or
transmit any personal data — the only thing saved locally is the
YouTube API key you provide, so it doesn't need to be re-entered.

---

## License

GNU General Public License v3.0 — see the [LICENSE](./LICENSE) file
for details.
