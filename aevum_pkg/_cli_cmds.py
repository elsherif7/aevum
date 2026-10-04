"""The scan command: dispatch, progress bar, and the YouTube limit message."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from ._color import clr, eclr
from ._display import _fuzzy_suggest, print_results, print_url_results
from ._exit import EX
from ._scan import MAX_DEPTH, _run_scan, check_ffprobe
from ._text import _safe
from ._youtube import (
    ApiKeyCancelled,
    YouTubeLimitError,
    _has_playlist_param,
    _is_url,
    _normalise_url,
    _parse_yt_url,
    scan_url,
)


def _is_interactive() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


# rewinds the progress line; empty when stdout is not a terminal
_CR = "\r" if _is_interactive() else ""


def _make_progress_bar():
    interactive = _is_interactive()

    def on_progress(done, total):
        if not interactive or total <= 0:
            return
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "█" * filled + "░" * (24 - filled)
        print(f"\r  {clr.C}Scanning...{clr.RST}  {bar}  {clr.Y}{done}/{total}{clr.RST}  {clr.DIM}({pct}%){clr.RST}",
              end='', flush=True)

    return on_progress


def _require_ffprobe() -> None:
    if not check_ffprobe():
        print(f"\n  {eclr.R}[ERROR]{eclr.RST} ffprobe not found on PATH.", file=sys.stderr)
        print(f"  {eclr.DIM}ffprobe is required for local folder scanning.{eclr.RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {eclr.C}https://ffmpeg.org/download.html{eclr.RST}\n", file=sys.stderr)
        sys.exit(EX.ERR_DEPS)


def cmd_scan(raw: str) -> None:
    # a folder named like a domain ("www.backup") is still a folder
    if _is_url(raw) and (raw.lower().startswith(("http://", "https://")) or not os.path.isdir(raw)):
        _scan_youtube(raw)
    else:
        _scan_folder(raw)


def _wait_phrase(seconds: float) -> str:
    """'in about 5 minutes', 'in about 3 hours', ... for a wait YouTube asked for."""
    mins = max(1, math.ceil(seconds / 60))
    if mins < 120:
        return f"in about {mins} minute{'s' if mins != 1 else ''}"
    hours = math.ceil(mins / 60)
    if hours < 48:
        return f"in about {hours} hours"
    return f"in about {math.ceil(hours / 24)} days"


def _print_limit_message(e: YouTubeLimitError) -> None:
    """Explain a rate or quota stop. Progress is already saved."""
    print(f"\n\n  {eclr.Y}[LIMIT]{eclr.RST} {_safe(e, 500)}", file=sys.stderr)
    if e.kind == 'quota':
        when = "after YouTube's daily quota resets (midnight Pacific Time)"
    elif e.retry_after is not None:
        when = _wait_phrase(e.retry_after)
    else:
        when = "in a minute or two"
    if e.total:
        print(f"  {eclr.W}{e.saved:,} of {e.total:,} videos are saved.{eclr.RST}", file=sys.stderr)
    print(f"  Run the same command again {when} to continue. "
          f"Saved videos are not fetched twice.\n", file=sys.stderr)


def _scan_youtube(raw: str) -> None:
    # reject links we can't use before asking for an API key or touching the network
    kind, _ = _parse_yt_url(_normalise_url(raw))
    if kind is None:
        print(f"\n  {eclr.R}[ERROR]{eclr.RST} Not a supported YouTube URL: {_safe(raw, 500)}", file=sys.stderr)
        print(f"  {eclr.DIM}Use a video, playlist, or channel link, e.g. "
              f"youtube.com/watch?v=ID, /playlist?list=ID, /@handle, /channel/ID{eclr.RST}\n",
              file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    if kind == 'video' and _has_playlist_param(raw):
        print(f"  {clr.DIM}This link is a video inside a playlist, so only the video is "
              f"scanned. To scan the whole playlist, use its /playlist?list=... link.{clr.RST}")

    url_prog = _make_progress_bar()
    try:
        total_sec, total_count, entries, label, cache_hits, unavailable_count = \
            scan_url(raw, url_prog, use_cache=True)
    except (KeyboardInterrupt, ApiKeyCancelled):
        print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    except YouTubeLimitError as e:
        _print_limit_message(e)
        sys.exit(EX.ERR_API)
    except Exception as e:
        print(f"\n  {eclr.R}[ERROR]{eclr.RST} {_safe(e, 500)}\n", file=sys.stderr)
        sys.exit(EX.ERR_API)

    api_fetched  = total_count - cache_hits
    yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                    if api_fetched > 0 else
                    f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
    unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
    print(f"{_CR}  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
    print_url_results(raw, label, total_sec, total_count, entries,
                      unavailable_count=unavailable_count)
    sys.exit(EX.OK)


def _print_skip_summary(stats: dict[str, int]) -> None:
    files = stats.get("unreadable_files", 0)
    if files:
        timed_out = stats.get("timed_out", 0)
        extra = f", {timed_out} of them timed out" if timed_out else ""
        noun = "media file" if files == 1 else "media files"
        print(f"  {clr.DIM}({files} {noun} could not be read{extra}){clr.RST}")
    dirs = stats.get("unreadable_dirs", 0)
    if dirs:
        noun = "folder" if dirs == 1 else "folders"
        print(f"  {clr.DIM}({dirs} {noun} could not be opened){clr.RST}")
    deep = stats.get("skipped_deep", 0)
    if deep:
        noun = "folder" if deep == 1 else "folders"
        print(f"  {clr.DIM}({deep} {noun} nested more than {MAX_DEPTH} levels deep not scanned){clr.RST}")


def _scan_folder(raw: str) -> None:
    folder = Path(raw)
    if not folder.exists():
        print(f"\n  {eclr.R}[ERROR]{eclr.RST} Path not found: {_safe(str(folder), 500)}", file=sys.stderr)
        try:
            sug = _fuzzy_suggest(folder.name,
                                 [p.name for p in folder.parent.iterdir() if p.is_dir()])
            if sug:
                print(f"  {eclr.DIM}Did you mean:{eclr.RST}  {eclr.W}{_safe(str(folder.parent / sug), 500)}{eclr.RST}", file=sys.stderr)
        except Exception:
            pass
        print()
        sys.exit(EX.ERR_ARGS)
    if not folder.is_dir():
        print(f"\n  {eclr.R}[ERROR]{eclr.RST} That is a file, not a folder: {_safe(str(folder), 500)}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe()

    on_progress = _make_progress_bar()
    if _is_interactive():
        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    stats: dict[str, int] = {}
    try:
        total_sec, total_count, tree, durations, sizes = _run_scan(folder, on_progress, stats)
    except KeyboardInterrupt:
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    except Exception as e:
        print(f"\n\n  {eclr.R}[ERROR]{eclr.RST} {_safe(type(e).__name__)}: {_safe(e, 500)}\n",
              file=sys.stderr)
        sys.exit(EX.ERR_SCAN)

    print(f"{_CR}  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))
    skipped = stats.get("duplicates_skipped", 0)
    if skipped:
        noun = "file" if skipped == 1 else "files"
        print(f"  {clr.DIM}({skipped} duplicate {noun} skipped: hardlinks or symlinks "
              f"to files already counted){clr.RST}")
    _print_skip_summary(stats)
    print_results(folder, total_sec, total_count, tree, durations, sizes)
    sys.exit(EX.OK)
