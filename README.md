# Aevum

A minimal CLI that scans a local folder or a YouTube URL and reports
total media duration, broken down by subfolder — so you can see how
large a video or audio library is without opening every folder by
hand.

> **🐍 Get it on PyPI:** [pypi.org/project/aevum](https://pypi.org/project/aevum/)
>
> New updates are published on the 7th of every even month (Feb,
> Apr, Jun, Aug, Oct, Dec).
>
> Security issues or broken/critical bugs are fixed and released
> immediately, outside that schedule.

---

## Structure

```
aevum/
├── aevum.py              # Entry point — delegates to aevum_pkg._cli:main
├── clean.py              # Removes pip-install build artifacts (build/, egg-info/, __pycache__)
├── pyproject.toml        # Packaging, ruff, mypy config
├── LICENSE               # GNU General Public License v3.0
└── aevum_pkg/
    ├── _cli.py           # Argument parsing + main() — the only command is 'scan'
    ├── _cli_cmds.py      # cmd_scan
    ├── _cli_helpers.py   # Progress bar, ffprobe availability check
    ├── _scan.py          # Local folder scanning (native MP4/MKV parsing + ffprobe fallback)
    ├── _youtube.py       # YouTube Data API v3 scanning (channels, playlists, videos)
    ├── _apikey.py        # YouTube API key storage
    ├── _display.py       # Human-readable output (tree, bar chart, top files)
    ├── _models.py        # FolderNode / ScanTree data types
    ├── _color.py         # ANSI color handling
    ├── _paths.py         # Platform-correct data directory paths
    └── _exit.py          # Exit code constants
```

---

## Installation

Requires Python 3.10+.

```
pip install .
```

Local folder scanning also requires `ffprobe` (part of
[FFmpeg](https://ffmpeg.org/download.html)) to be on your `PATH`.

---

## How it works

**Local folder** — `aevum scan <path>` walks every subfolder, reads
each media file's duration (a fast native MP4/MKV header parser first,
falling back to `ffprobe` for other formats), and prints a folder tree
with per-subfolder duration and size, a duration breakdown bar chart,
playback-speed conversions (1x/1.25x/1.5x/1.75x/2x), and the 10
longest files.

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

A few other things worth knowing:

- `scan` is required — there's no bare `aevum <path>` shorthand.
- A path containing spaces must be quoted, or it's rejected with a
  hint rather than guessed at.
- The first time you scan a YouTube URL, Aevum prompts for a free API
  key and saves it locally so you won't be asked again.

---

## YouTube API key

Get a free key in about two minutes:

1. Go to <https://console.cloud.google.com/>
2. Create a project → enable **YouTube Data API v3**
3. Credentials → Create API Key → paste it when Aevum asks

The key is saved to `~/.local/share/Aevum/yt_api_key.txt` on
Linux/macOS or `%APPDATA%\Aevum\yt_api_key.txt` on Windows, with
owner-only file permissions.

---

## Privacy

Aevum makes no network requests except to the YouTube Data API v3,
and only when you scan a YouTube URL. It does not collect, store, or
transmit any personal data — the only thing saved locally is the
YouTube API key you provide, so it doesn't need to be re-entered.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Bad arguments / path not found |
| 2 | Missing dependency (`ffprobe` not on `PATH`) |
| 3 | Scan failed or was interrupted |
| 5 | YouTube API error |

---

## Development

```
pip install -e ".[dev]"
ruff check .
mypy .
python3 clean.py   # remove build artifacts when you're done
```

---

## License

GNU General Public License v3.0 — see the [LICENSE](./LICENSE) file
for details.
