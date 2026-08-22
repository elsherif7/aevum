"""
Command handler for the Aevum CLI.

'scan' takes exactly one target and no flags: point it at a local
folder path or a YouTube URL and it prints the result. No JSON mode,
no quiet mode, no batch/merge mode, no filters, no sort/top options —
those all depended on flags that have been removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ._cli_helpers import _make_progress_bar, _require_ffprobe
from ._color import clr
from ._display import _fuzzy_suggest, print_results, print_url_results
from ._exit import EX
from ._scan import _run_scan
from ._youtube import _is_url, scan_url


def cmd_scan(args) -> None:
    raw = args.target

    if _is_url(raw):
        _scan_youtube(raw)
    else:
        _scan_folder(raw)


def _scan_youtube(raw: str) -> None:
    url_prog = _make_progress_bar()
    try:
        total_sec, total_count, entries, label, cache_hits, unavailable_count = \
            scan_url(raw, url_prog, use_cache=True)
    except KeyboardInterrupt:
        print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
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
