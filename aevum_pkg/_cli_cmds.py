"""
Command handler for the Aevum CLI.

'scan' takes exactly one target and no flags: point it at a local
folder path or a YouTube URL and it prints the result. No JSON mode,
no quiet mode, no batch/merge mode, no filters, no sort/top options —
those all depended on flags that have been removed.

The small progress-bar and ffprobe-check helpers used to live in
_cli_helpers.py; folded in here since this is their only caller.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from ._color import clr
from ._display import _fuzzy_suggest, print_results, print_url_results
from ._exit import EX
from ._scan import _run_scan, check_ffprobe
from ._youtube import (
    ApiKeyCancelled,
    YouTubeLimitError,
    _has_playlist_param,
    _is_url,
    _normalise_url,
    _parse_yt_url,
    scan_url,
)


def _make_progress_bar():
    """
    Return a progress callback that renders a text progress bar to stdout.

    Issue 15 fix: guard against total == 0 inside the callback itself so
    that any caller passing total=0 directly gets a no-op instead of a
    ZeroDivisionError.
    """
    def on_progress(done, total):
        if total <= 0:   # Issue 15
            return
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "\u2588" * filled + "\u2591" * (24 - filled)
        print(f"\r  {clr.C}Scanning...{clr.RST}  {bar}  {clr.Y}{done}/{total}{clr.RST}  {clr.DIM}({pct}%){clr.RST}",
              end='', flush=True)

    return on_progress


def _require_ffprobe(context: str = "") -> None:
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        print(f"\n  {clr.R}[ERROR]{clr.RST} ffprobe not found on PATH{ctx}.", file=sys.stderr)
        print(f"  {clr.DIM}ffprobe is required for local folder scanning.{clr.RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}\n", file=sys.stderr)
        sys.exit(EX.ERR_DEPS)


def cmd_scan(raw: str) -> None:
    if _is_url(raw):
        _scan_youtube(raw)
    else:
        _scan_folder(raw)


def _print_limit_message(e: YouTubeLimitError) -> None:
    """Explain a rate/quota stop and how to continue (progress is already saved)."""
    print(f"\n\n  {clr.Y}[LIMIT]{clr.RST} {e}", file=sys.stderr)
    if e.kind == 'rate' and e.retry_after:
        mins = max(1, math.ceil(e.retry_after / 60))
        when = f"in about {mins} minute{'s' if mins != 1 else ''}"
    elif e.kind == 'quota':
        when = "after YouTube's daily quota resets (midnight Pacific Time)"
    else:
        when = "later"
    if e.total:
        print(f"  {clr.W}{e.saved:,} of {e.total:,} videos are saved.{clr.RST}", file=sys.stderr)
    print(f"  Run the same command again {when} to continue. "
          f"Saved videos are not fetched twice.\n", file=sys.stderr)


def _scan_youtube(raw: str) -> None:
    # Reject links we can't use before asking for an API key or touching the network.
    kind, _ = _parse_yt_url(_normalise_url(raw))
    if kind is None:
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a supported YouTube URL: {raw}", file=sys.stderr)
        print(f"  {clr.DIM}Use a video, playlist, or channel link, e.g. "
              f"youtube.com/watch?v=ID, /playlist?list=ID, /@handle, /channel/ID{clr.RST}\n",
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
        print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
        sys.exit(EX.ERR_API)

    api_fetched  = total_count - cache_hits
    yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                    if api_fetched > 0 else
                    f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
    unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
    print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
    print_url_results(raw, label, total_sec, total_count, entries,
                      unavailable_count=unavailable_count)
    sys.exit(EX.OK)


def _scan_folder(raw: str) -> None:
    folder = Path(raw)
    if not folder.exists():
        print(f"\n  {clr.R}[ERROR]{clr.RST} Path not found: {folder}", file=sys.stderr)
        try:
            sug = _fuzzy_suggest(folder.name,
                                 [p.name for p in folder.parent.iterdir() if p.is_dir()])
            if sug:
                print(f"  {clr.DIM}Did you mean:{clr.RST}  {clr.W}{folder.parent / sug}{clr.RST}", file=sys.stderr)
        except Exception:
            pass
        print()
        sys.exit(EX.ERR_ARGS)
    if not folder.is_dir():
        print(f"\n  {clr.R}[ERROR]{clr.RST} That is a file, not a folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("scan")

    on_progress = _make_progress_bar()
    print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    try:
        total_sec, total_count, tree, durations, sizes = _run_scan(folder, on_progress)
    except KeyboardInterrupt:
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)

    print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))
    print_results(folder, total_sec, total_count, tree, durations, sizes)
    sys.exit(EX.OK)
