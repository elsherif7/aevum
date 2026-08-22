# Aevum

A minimal CLI that scans a local folder or a YouTube URL and reports
total media duration, broken down by subfolder.

## Usage

```
aevum scan <path|url>
aevum <path|url>          # 'scan' is optional
```

That's it — one command, one required argument, no flags.

### Examples

```
aevum scan D:\Movies
aevum /home/user/Videos
aevum https://youtube.com/@somechannel
aevum https://youtube.com/playlist?list=...
aevum https://youtube.com/watch?v=...
```

A local path prints a folder tree with per-subfolder duration and
size, a duration breakdown bar chart, playback-speed conversions
(1x/1.25x/1.5x/1.75x/2x), and the 10 longest files. A YouTube URL
(channel, playlist, or single video) prints the same kind of summary
for the videos it finds.

## Installation

Requires Python 3.10+.

```
pip install .
```

Local folder scanning also requires `ffprobe` (part of
[FFmpeg](https://ffmpeg.org/download.html)) to be on your `PATH`.

## YouTube scanning

Scanning a YouTube URL needs a free YouTube Data API v3 key. The
first time you scan a URL, Aevum will prompt you to paste one in and
save it locally (`~/.local/share/Aevum/yt_api_key.txt` on Linux/macOS,
`%APPDATA%\Aevum\yt_api_key.txt` on Windows) so you won't be asked
again. To get a key:

1. Go to <https://console.cloud.google.com/>
2. Create a project → enable **YouTube Data API v3**
3. Credentials → Create API Key → paste it when Aevum asks

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Bad arguments / path not found |
| 2 | Missing dependency (`ffprobe` not on `PATH`) |
| 3 | Scan failed or was interrupted |
| 5 | YouTube API error |

## Development

```
pip install -e ".[dev]"
ruff check .
mypy .
```

## License

MIT (per `pyproject.toml`).
