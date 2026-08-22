"""
Command handler functions for the Aevum CLI.

Only 'scan' remains — all other subcommands (compare, dupes, export,
watch, history/diff, alias, cache, config, doctor, quota, update,
clearpath, appdata, files, stats, summary, top, recent, version) were
removed to keep this a minimal folder/YouTube duration scanner.

The local on-disk duration cache (_cache.py) was also removed, so every
folder scan always re-probes every file with ffprobe/native parsing.
YouTube's own per-video API-response cache in _youtube.py is a separate
mechanism and is untouched by that removal.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ._cli_helpers import _build_filters, _make_progress_bar, _require_ffprobe, _resolve_sort, _resolve_top
from ._cli_json import _json_error, _json_out, _scan_to_json, _url_to_json
from ._color import LINE, clr
from ._display import _fuzzy_suggest, print_results, print_url_results
from ._exit import EX
from ._scan import _run_scan, apply_filters, format_duration, format_size, rebuild_after_filter
from ._youtube import _is_url, scan_url


def cmd_scan(args, cfg, use_json, quiet) -> None:
    targets = [t.strip().strip("'\"") for t in args.targets]

    if not targets:
        print(f"\n  {clr.R}[ERROR]{clr.RST} No target specified. Usage: aevum scan <path|url>\n",
              file=sys.stderr)
        sys.exit(EX.ERR_ARGS)

    # Collapse multi-token unquoted paths (spaces in path name).
    # Issue 20 fix: only collapse when the remaining tokens don't look like
    # independent paths — if they do, treat them as a real batch scan.
    if len(targets) > 1 and not any(_is_url(t) for t in targets):
        first = targets[0]
        first_looks_like_path = ':' in first or first.startswith(('/', '\\', '.'))
        rest_look_like_paths  = any(
            ':' in t or t.startswith(('/', '\\')) for t in targets[1:]
        )
        if first_looks_like_path and not rest_look_like_paths:
            targets = [' '.join(targets)]

    sort          = _resolve_sort(args, cfg)
    top           = _resolve_top(args, cfg)
    do_merge      = getattr(args, 'merge', False)
    max_d         = getattr(args, 'depth', None) or 50
    custom_speeds = getattr(args, 'speeds', None) or None

    if len(targets) == 1:
        _cmd_scan_single(args, cfg, use_json, quiet,
                         targets[0], sort, top, max_d, custom_speeds)
    else:
        _cmd_scan_batch(args, cfg, use_json, quiet,
                        targets, sort, top, do_merge, max_d, custom_speeds)


def _cmd_scan_single(args: Any, cfg: dict[str, Any], use_json: bool, quiet: bool,
                     raw_target: str, sort: str, top: int,
                     max_d: int, custom_speeds: list[float] | None) -> None:
    raw     = raw_target
    filters = _build_filters(args, use_json)

    if _is_url(raw):
        url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
        try:
            total_sec, total_count, entries, label, cache_hits, unavailable_count = \
                scan_url(raw, url_prog, use_cache=True)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Fetch interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        except Exception as e:
            if use_json:
                _json_error(str(e), EX.ERR_API)
            print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_API)
        if use_json:
            _json_out(_url_to_json(raw, label, total_sec, total_count, entries))
        else:
            if not quiet:
                api_fetched  = total_count - cache_hits
                yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                                if api_fetched > 0 else
                                f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
                unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
                print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
            print_url_results(raw, label, total_sec, total_count, entries,
                              top_n=top, unavailable_count=unavailable_count,
                              speeds=custom_speeds)
        sys.exit(EX.OK)

    folder = Path(raw)
    if not folder.exists():
        if use_json:
            _json_error(f"Path not found: {folder}", EX.ERR_ARGS)
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
        if use_json:
            _json_error(f"That is a file, not a folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} That is a file, not a folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("scan", use_json)

    on_progress = _make_progress_bar(quiet, use_json)
    if not quiet:
        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    try:
        total_sec, total_count, tree, durations, sizes = _run_scan(
            folder, on_progress, sort)
    except KeyboardInterrupt:
        if use_json:
            _json_error("Scan interrupted", EX.ERR_SCAN)
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)

    if filters:
        durations, sizes = apply_filters(durations, sizes, filters)
        total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
            folder, durations, sizes, sort)

    if use_json:
        _json_out(_scan_to_json(folder, total_sec, total_count, tree, durations, sizes))
        sys.exit(EX.OK)

    if not quiet:
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))

    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                  show_files=getattr(args, 'files', False), max_depth=max_d,
                  speeds=custom_speeds)
    sys.exit(EX.OK)


def _cmd_scan_batch(args: Any, cfg: dict[str, Any], use_json: bool, quiet: bool,
                    targets: list[str], sort: str, top: int,
                    do_merge: bool, max_d: int, custom_speeds: list[float] | None) -> None:
    _require_ffprobe("scan", use_json)
    filters = _build_filters(args, use_json)

    folders = []
    for raw in targets:
        if _is_url(raw):
            if use_json:
                _json_error("Batch mode does not support URLs — use a single URL target", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Batch mode does not support URLs: {raw}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        f = Path(raw)
        if not f.exists() or not f.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {f}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        folders.append(f)

    results = []
    for i, folder in enumerate(folders, 1):
        if not quiet:
            label_w = 40
            print(f"  {clr.DIM}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                  f"{clr.DIM}scanning...{clr.RST}", end='', flush=True)
        on_progress = _make_progress_bar(quiet=True)
        try:
            total_sec, total_count, tree, durations, sizes = _run_scan(
                folder, on_progress, sort)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if filters:
            durations, sizes = apply_filters(durations, sizes, filters)
            total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                folder, durations, sizes, sort)
        results.append((folder, total_sec, total_count, tree, durations, sizes))
        if not quiet:
            fmt_dur = format_duration(total_sec)["hours_fmt"]
            print(f"\r  {clr.G}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                  f"{clr.Y}{fmt_dur}{clr.RST}  {clr.DIM}{total_count} files{clr.RST}".ljust(100))

    if do_merge:
        _print_merged(results, folders, use_json, quiet, custom_speeds)
    else:
        _print_batch_separate(results, use_json, quiet, top, max_d, custom_speeds)


def _print_merged(results: list, folders: list[Path], use_json: bool, quiet: bool,
                  custom_speeds: list[float] | None) -> None:
    merged_sec   = sum(r[1] for r in results)
    merged_count = sum(r[2] for r in results)
    merged_dur   = {}
    merged_sizes = {}
    for _, _, _, _, dur, sz in results:
        merged_dur.update(dur)
        merged_sizes.update(sz)

    if use_json:
        _json_out({
            "status":      "ok",
            "command":     "scan",
            "mode":        "batch_merged",
            "paths":       [str(f.resolve()) for f in folders],
            "total_files": merged_count,
            "total_bytes": sum(merged_sizes.values()),
            "total_sec":   round(merged_sec, 2),
            "duration":    format_duration(merged_sec),
            "per_folder": [
                {
                    "path":        str(r[0].resolve()),
                    "name":        r[0].name,
                    "total_files": r[2],
                    "total_sec":   round(r[1], 2),
                    "duration":    format_duration(r[1])["hours_fmt"],
                }
                for r in results
            ],
            "files": [
                {
                    "path":     str(p),
                    "filename": p.name,
                    "folder":   p.parent.name,
                    "seconds":  round(s, 2),
                    "duration": format_duration(s)["hours_fmt"],
                }
                for p, s in sorted(merged_dur.items(), key=lambda x: x[1], reverse=True)
            ],
        })
        sys.exit(EX.OK)

    fmt_merged  = format_duration(merged_sec)
    total_bytes = sum(merged_sizes.values())
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Batch Scan  {clr.DIM}|{clr.RST}  {len(folders)} folders  {clr.DIM}(merged){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    for r in results:
        fd = format_duration(r[1])["hours_fmt"]
        print(f"  {clr.DIM}\u2192{clr.RST}  {clr.W}{r[0].name:<35}{clr.RST}  {clr.Y}{fd}{clr.RST}  {clr.DIM}{r[2]} files{clr.RST}")
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Grand Total{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{merged_count}{clr.RST}")
    print(f"  {clr.W}  Total size    {clr.DIM}:{clr.RST}  {clr.W}{format_size(total_bytes)}{clr.RST}")
    print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['days_fmt']}{clr.RST}")
    print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Minutes       {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['minutes_fmt']}{clr.RST}")
    print()
    sys.exit(EX.OK)


def _print_batch_separate(results: list, use_json: bool, quiet: bool,
                          top: int, max_d: int,
                          custom_speeds: list[float] | None) -> None:
    if use_json:
        _json_out({
            "status":  "ok",
            "command": "scan",
            "mode":    "batch",
            "folders": [
                {
                    "path":        str(r[0].resolve()),
                    "name":        r[0].name,
                    "total_files": r[2],
                    "total_sec":   round(r[1], 2),
                    "duration":    format_duration(r[1])["hours_fmt"],
                    "total_bytes": sum(r[5].values()),
                }
                for r in results
            ],
        })
        sys.exit(EX.OK)

    for folder, total_sec, total_count, tree, durations, sizes in results:
        print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                      show_files=False, max_depth=max_d, speeds=custom_speeds)
    sys.exit(EX.OK)
