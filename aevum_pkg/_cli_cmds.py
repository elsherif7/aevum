"""
Command handler functions for every Aevum CLI subcommand.

Each cmd_* function receives (args, cfg, use_json, quiet) and is responsible
for one subcommand only. main() in _cli.py dispatches to these.
"""
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ._color   import clr, LINE, clear
from ._scan    import (format_duration, format_size, _run_scan,
                       apply_filters, rebuild_after_filter)
from ._youtube import _is_url, scan_url, _make_url_progress, get_quota_status
from ._display import (print_results, print_url_results, print_stats,
                       print_recent, print_top, _fuzzy_suggest)
from ._dupes   import find_duplicates, print_duplicates, print_dupe_warning
from ._compare import run_compare, print_comparison
from ._export  import export_results, export_url_results
from ._config  import save_config, cmd_doctor as _config_doctor, cmd_cache, cmd_config
from ._apikey  import load_api_key
from ._paths   import CACHE_DIR
from ._exit    import EX
from ._history import save_snapshot, print_history, print_diff, history_to_json, diff_to_json
from ._cli_json    import (_json_out, _json_error, _scan_to_json, _url_to_json,
                            _dupes_to_json, _compare_to_json)
from ._cli_helpers import (_make_progress_bar, _require_ffprobe, _resolve_sort,
                            _resolve_top, _resolve_out_format, _use_cache,
                            _resolve_alias, _build_filters)
from ._cli_update  import _do_update, _open_appdata


# ---------------------------------------------------------------------------
# Simple / utility commands
# ---------------------------------------------------------------------------

def cmd_version(args, cfg, use_json, quiet, version):
    if use_json:
        _json_out({"status": "ok", "version": version})
    else:
        print(f"aevum {version}")
    sys.exit(EX.OK)


def cmd_update(args, cfg, use_json, quiet):
    rc = _do_update(cfg,
                    dry_run=getattr(args, 'dry_run', False),
                    quiet=quiet)
    sys.exit(rc)


def cmd_clearpath(args, cfg, use_json, quiet):
    if 'project_dir' in cfg:
        cleared = cfg['project_dir']
        del cfg['project_dir']
        save_config(cfg)
        if use_json:
            _json_out({"status": "ok", "message": "Saved path cleared", "path": cleared})
        else:
            print(f"  {clr.G}[OK]{clr.RST}  Saved path cleared: {clr.DIM}{cleared}{clr.RST}")
    else:
        if use_json:
            _json_out({"status": "ok", "message": "No saved path to clear"})
        else:
            print(f"  {clr.DIM}No saved path to clear.{clr.RST}")
    sys.exit(EX.OK)


def cmd_appdata(args, cfg, use_json, quiet):
    folder = _open_appdata()
    if use_json:
        _json_out({"status": "ok", "path": str(folder)})
    else:
        print(f"  {clr.G}[OK]{clr.RST}  Opened  {clr.W}{folder}{clr.RST}")
    sys.exit(EX.OK)


def cmd_doctor(args, cfg, use_json, quiet):
    if use_json:
        import subprocess as _sp
        from ._scan import check_ffprobe
        ffprobe_ok = check_ffprobe()
        try:
            r = _sp.run(['ffprobe', '-version'], capture_output=True, text=True)
            ffprobe_ver = r.stdout.splitlines()[0] if r.stdout else None
        except Exception:
            ffprobe_ver = None
        api_key = load_api_key()
        try:
            files       = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
            cache_bytes = sum(f.stat().st_size for f in files)
        except Exception:
            files = []; cache_bytes = 0
        _json_out({
            "status":          "ok",
            "command":         "doctor",
            "python":          sys.version.split()[0],
            "ffprobe":         ffprobe_ok,
            "ffprobe_version": ffprobe_ver,
            "yt_api_key_set":  bool(api_key),
            "cache_files":     len(files),
            "cache_bytes":     cache_bytes,
            "cache_dir":       str(CACHE_DIR),
        })
        sys.exit(EX.OK)
    _config_doctor(cfg)
    sys.exit(EX.OK)


def cmd_config_dispatch(args, cfg, use_json, quiet):
    cmd_config(args, cfg)
    sys.exit(EX.OK)


def cmd_cache_dispatch(args, cfg, use_json, quiet):
    cmd_cache(args)
    sys.exit(EX.OK)


def cmd_quota(args, cfg, use_json, quiet):
    api_key = load_api_key()
    if not api_key:
        if use_json:
            _json_out({"status": "error", "error": "No YouTube API key set"})
        else:
            print(f"\n  {clr.R}[ERROR]{clr.RST} No YouTube API key set.\n")
            print(f"  Set it with: {clr.W}aevum config set yt_api_key <key>{clr.RST}\n")
        sys.exit(EX.ERR_ARGS)
    used, remaining, pct = get_quota_status()
    if use_json:
        _json_out({
            "status":           "ok",
            "command":          "quota",
            "quota_used":       used,
            "quota_remaining":  remaining,
            "quota_limit":      10000,
            "percent_used":     round(pct, 2),
        })
    else:
        quota_col  = clr.G if pct < 50 else (clr.Y if pct < 80 else clr.R)
        status_lbl = "Good" if pct < 50 else ("Moderate" if pct < 80 else "High")
        print()
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.C}  YouTube API Quota{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}Today's Usage{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        print()
        print(f"  {clr.W}  Used       {clr.DIM}:{clr.RST}  {quota_col}{used:,} units{clr.RST}  {clr.DIM}({pct:.1f}%){clr.RST}")
        print(f"  {clr.W}  Remaining  {clr.DIM}:{clr.RST}  {clr.G}{remaining:,} units{clr.RST}")
        print(f"  {clr.W}  Daily Limit{clr.DIM}:{clr.RST}  {clr.W}10,000 units{clr.RST}")
        print(f"  {clr.W}  Status     {clr.DIM}:{clr.RST}  {quota_col}{status_lbl}{clr.RST}")
        print()
        print(f"  {clr.DIM}Note: This tracks Aevum's usage only. Quota resets daily at midnight PT.{clr.RST}")
        print()
    sys.exit(EX.OK)


# ---------------------------------------------------------------------------
# alias
# ---------------------------------------------------------------------------

def cmd_alias(args, cfg, use_json, quiet):
    aliases  = cfg.setdefault("aliases", {})
    action   = getattr(args, 'action', 'list') or 'list'
    name     = getattr(args, 'name', None)
    path_val = getattr(args, 'path', None)

    if action == 'list':
        if not aliases:
            print(f"  {clr.W}No aliases defined.{clr.RST}")
            print(f"  Add one with: {clr.W}aevum alias set <name> <value>{clr.RST}\n")
            sys.exit(EX.OK)

        _SUBCOMMANDS = {
            'scan', 'compare', 'dupes', 'export', 'watch', 'cache',
            'config', 'alias', 'doctor', 'quota', 'version', 'update',
            'clearpath', 'appdata', 'files',
        }

        def _alias_type(v):
            import shlex
            try:
                tokens = shlex.split(v)
            except ValueError:
                tokens = v.split()
            if not tokens:
                return 'unknown', None
            first = tokens[0]
            _is_path = (
                first.startswith(('/', '\\', '.')) or
                (len(first) >= 2 and first[1] == ':')
            )
            if _is_path:
                exists = Path(v.strip("'\"")).exists()
                return 'path', exists
            if first.lower() in _SUBCOMMANDS:
                return 'command', None
            if any(t.startswith('-') for t in tokens):
                return 'flag', None
            return 'unknown', None

        grouped = {'command': [], 'flag': [], 'path': [], 'unknown': []}
        for k, v in sorted(aliases.items()):
            kind, extra = _alias_type(v)
            grouped[kind].append((k, v, extra))

        print()
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Aliases{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")

        first_section = True
        for kind in ('command', 'flag', 'path', 'unknown'):
            items = grouped[kind]
            if not items:
                continue
            if not first_section:
                print(f"  {clr.DIM}  {chr(9472) * 40}{clr.RST}")
            first_section = False
            for k, v, extra in items:
                if kind == 'path':
                    status = f"{clr.B}[path]{clr.RST}" + (f"  {clr.R}\u2717 not found{clr.RST}" if not extra else "")
                elif kind == 'command':
                    status = f"{clr.M}[command]{clr.RST}"
                elif kind == 'flag':
                    status = f"{clr.C}[flag]{clr.RST}"
                else:
                    status = f"{clr.R}[unknown]{clr.RST}"
                print(f"  {clr.G}{k:<15}{clr.RST}  {clr.W}{v:<35}{clr.RST}  {status}")
        print()
        sys.exit(EX.OK)

    if action in ('remove', 'rm'):
        if not name:
            print(f"  {clr.R}[ERROR]{clr.RST} Alias name required.", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        if name not in aliases:
            print(f"  {clr.Y}[WARN]{clr.RST}  Alias '{name}' not found.")
            sys.exit(EX.OK)
        del aliases[name]
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  Alias '{name}' removed.")
        sys.exit(EX.OK)

    if action == 'set':
        if not name or not path_val:
            print(f"  {clr.R}[ERROR]{clr.RST} Usage: aevum alias set <name> <value>", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        value = path_val
        if name in aliases:
            print(f"  {clr.R}[ERROR]{clr.RST}  Alias '{clr.W}{name}{clr.RST}' already exists  "
                  f"{clr.DIM}\u2192{clr.RST}  {clr.W}{aliases[name]}{clr.RST}")
            print(f"  {clr.DIM}Remove it first with:{clr.RST}  {clr.W}aevum alias rm {name}{clr.RST}")
            sys.exit(EX.ERR_ARGS)
        # Enforce the same length limits that load_config applies, so a value
        # that would be silently dropped on next load is rejected here instead.
        if len(name) > 50:
            print(f"  {clr.R}[ERROR]{clr.RST} Alias name too long (max 50 characters).", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        if len(value) > 4096:
            print(f"  {clr.R}[ERROR]{clr.RST} Alias value too long (max 4096 characters).", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _looks_like_path = (
            value.startswith(('/', '\\', '.')) or
            (len(value) >= 2 and value[1] == ':')
        )
        if _looks_like_path and not Path(value).exists():
            print(f"  {clr.Y}[WARN]{clr.RST}  Path does not exist: {value}")
        aliases[name] = value
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{value}{clr.RST}")
        sys.exit(EX.OK)

    sys.exit(EX.OK)


# ---------------------------------------------------------------------------
# top / recent / history / diff / stats / summary
# ---------------------------------------------------------------------------

def cmd_top(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("top", use_json)
    on_progress = _make_progress_bar(quiet, use_json)
    use_cache   = _use_cache(args, cfg)
    _, _, _, durations, sizes, _ = _run_scan(folder, on_progress, "name:asc", use_cache)
    if not quiet:
        print()
    by    = getattr(args, 'by', 'duration')
    limit = getattr(args, 'limit', 20)
    if use_json:
        if by == "size":
            ranked = sorted(sizes.items(), key=lambda x: x[1], reverse=True)
            ranked = [(p, s, durations.get(p, 0.0)) for p, s in ranked if p in durations]
        else:
            ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
            ranked = [(p, sizes.get(p, 0), s) for p, s in ranked]
        _json_out({
            "status":  "ok",
            "command": "top",
            "path":    str(folder.resolve()),
            "by":      by,
            "files": [
                {
                    "path":     str(p),
                    "filename": p.name,
                    "folder":   p.parent.name,
                    "seconds":  round(sec, 2),
                    "bytes":    fb,
                    "duration": format_duration(sec)["hours_fmt"],
                    "size":     format_size(fb),
                }
                for p, fb, sec in ranked[:limit]
            ],
        })
    else:
        print_top(folder, durations, sizes, n=limit, by=by)
    sys.exit(EX.OK)


def cmd_recent(args, cfg, use_json, quiet):
    from ._scan import parse_since_arg
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("recent", use_json)
    try:
        since_ts = parse_since_arg(args.since)
    except ValueError as e:
        if use_json:
            _json_error(str(e), EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    on_progress = _make_progress_bar(quiet, use_json)
    use_cache   = _use_cache(args, cfg)
    total_sec, total_count, tree, durations, sizes, hits = _run_scan(
        folder, on_progress, "name:asc", use_cache)
    if not quiet:
        print()
    if use_json:
        import datetime as _dt
        entries = []
        for path, sec in durations.items():
            try:
                mtime = path.stat().st_mtime
                if mtime >= since_ts:
                    entries.append({
                        "path":     str(path),
                        "filename": path.name,
                        "folder":   path.parent.name,
                        "seconds":  round(sec, 2),
                        "bytes":    sizes.get(path, 0),
                        "duration": format_duration(sec)["hours_fmt"],
                        "modified": _dt.datetime.fromtimestamp(mtime).isoformat(),
                    })
            except OSError:
                pass
        entries.sort(key=lambda x: x["modified"], reverse=True)
        _json_out({
            "status":      "ok",
            "command":     "recent",
            "path":        str(folder.resolve()),
            "since":       args.since,
            "total_found": len(entries),
            "files":       entries[:args.limit],
        })
    else:
        print_recent(folder, durations, sizes, since_ts, limit=args.limit)
    sys.exit(EX.OK)


def cmd_history(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    if use_json:
        _json_out(history_to_json(folder))
    else:
        print_history(folder)
    sys.exit(EX.OK)


def cmd_diff(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    if use_json:
        _json_out(diff_to_json(folder))
    else:
        print_diff(folder)
    sys.exit(EX.OK)


def cmd_stats(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("stats", use_json)
    on_progress = _make_progress_bar(quiet, use_json)
    use_cache   = _use_cache(args, cfg)
    total_sec, total_count, tree, durations, sizes, hits = _run_scan(
        folder, on_progress, "name:asc", use_cache)
    if not quiet:
        print()
    filters = _build_filters(args, use_json)
    if filters:
        durations, sizes = apply_filters(durations, sizes, filters)
        total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
            folder, durations, sizes, "name:asc")
    if use_json:
        import statistics as _stats
        secs_list = list(durations.values())
        ext_counts: dict = {}
        for p in durations:
            e = p.suffix.lower()
            ext_counts[e] = ext_counts.get(e, 0) + 1
        _json_out({
            "status":       "ok",
            "command":      "stats",
            "path":         str(folder.resolve()),
            "total_files":  total_count,
            "total_sec":    round(total_sec, 2),
            "avg_sec":      round(total_sec / total_count, 2) if total_count else 0,
            "median_sec":   round(_stats.median(secs_list), 2) if secs_list else 0,
            "min_sec":      round(min(secs_list), 2) if secs_list else 0,
            "max_sec":      round(max(secs_list), 2) if secs_list else 0,
            "total_bytes":  sum(sizes.values()),
            "formats":      ext_counts,
        })
    else:
        print_stats(folder, durations, sizes)
    sys.exit(EX.OK)


def cmd_summary(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("summary", use_json)
    use_cache   = _use_cache(args, cfg)
    total_sec, total_count, _tree, durations, sizes, _hits = _run_scan(
        folder, None, "name:asc", use_cache)
    filters = _build_filters(args, use_json)
    if filters:
        durations, sizes = apply_filters(durations, sizes, filters)
        total_sec, total_count, _tree, durations, sizes = rebuild_after_filter(
            folder, durations, sizes, "name:asc")
    total_bytes = sum(sizes.values())
    dur_fmt     = format_duration(total_sec)["hours_fmt"]
    size_fmt    = format_size(total_bytes)
    if use_json:
        _json_out({
            "status":      "ok",
            "command":     "summary",
            "path":        str(folder.resolve()),
            "name":        folder.name,
            "total_files": total_count,
            "total_sec":   round(total_sec, 2),
            "total_bytes": total_bytes,
            "duration":    dur_fmt,
            "size":        size_fmt,
        })
    elif quiet:
        print(f"{folder.name}  {total_count:,} files  {dur_fmt}  {size_fmt}")
    else:
        print(f"\n  {clr.W}{folder.name}{clr.RST}  {clr.DIM}\u2192{clr.RST}  "
              f"{clr.Y}{total_count:,} files{clr.RST}  {clr.DIM}|{clr.RST}  "
              f"{clr.W}{dur_fmt}{clr.RST}  {clr.DIM}|{clr.RST}  "
              f"{clr.W}{size_fmt}{clr.RST}\n")
    sys.exit(EX.OK)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

def cmd_watch(args, cfg, use_json, quiet):
    import time as _time
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("watch", use_json)

    interval = max(1.0, args.interval)
    if args.interval < 1.0 and not quiet:
        print(f"  {clr.Y}[WARN]{clr.RST}  Interval clamped to minimum 1 second.", file=sys.stderr)
    no_clear      = args.no_clear or use_json
    sort          = _resolve_sort(args, cfg)
    top           = _resolve_top(args, cfg)
    filters       = _build_filters(args, use_json)
    uc            = _use_cache(args, cfg)
    custom_speeds = getattr(args, 'speeds', None) or None

    def _folder_snapshot(root):
        snap = {}
        try:
            snap[str(root)] = root.stat().st_mtime
            for entry in os.scandir(root):
                if entry.is_dir(follow_symlinks=False):
                    snap[entry.path] = entry.stat().st_mtime
        except OSError:
            pass
        return snap

    def _do_scan():
        total_sec, total_count, tree, durations, sizes, hits = _run_scan(
            folder, None, sort, use_cache=uc)
        if filters:
            durations, sizes = apply_filters(durations, sizes, filters)
            total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                folder, durations, sizes, sort)
        return total_sec, total_count, tree, durations, sizes, hits

    if not quiet:
        print(f"\n  {clr.C}Watching{clr.RST}  {clr.W}{folder}{clr.RST}  "
              f"{clr.DIM}(interval: {interval}s \u2014 Ctrl+C to stop){clr.RST}\n")

    update_n  = 0
    last_snap = {}
    last_sec  = None

    while True:
        try:
            snap    = _folder_snapshot(folder)
            changed = snap != last_snap

            if changed or update_n == 0:
                last_snap = snap
                try:
                    total_sec, total_count, tree, durations, sizes, hits = _do_scan()
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    if use_json:
                        print(json.dumps({"status": "error", "error": str(e),
                                          "timestamp": datetime.now().isoformat()}), flush=True)
                    else:
                        print(f"  {clr.R}[ERROR]{clr.RST} Scan failed: {e}", file=sys.stderr)
                    _time.sleep(interval)
                    continue

                update_n += 1
                ts = datetime.now().strftime("%H:%M:%S")

                if use_json:
                    payload = _scan_to_json(folder, total_sec, total_count,
                                            tree, durations, sizes, hits)
                    payload["watch_update"]    = update_n
                    payload["timestamp"]       = datetime.now().isoformat()
                    payload["changed"]         = changed
                    payload["total_sec_delta"] = round(total_sec - (last_sec or total_sec), 2)
                    print(json.dumps(payload, ensure_ascii=False), flush=True)
                else:
                    if not no_clear:
                        clear()
                    delta_str = ""
                    if last_sec is not None and last_sec != total_sec:
                        delta     = total_sec - last_sec
                        sign      = "+" if delta >= 0 else ""
                        dfmt      = format_duration(abs(delta))["hours_fmt"]
                        dcol      = clr.G if delta >= 0 else clr.R
                        delta_str = f"  {dcol}{sign}{dfmt}{clr.RST}"
                    print(f"  {clr.C}{LINE}{clr.RST}")
                    print(f"  {clr.C}  Watching{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{folder.name}{clr.RST}  "
                          f"{clr.DIM}|{clr.RST}  {clr.G}#{update_n}{clr.RST}  "
                          f"{clr.DIM}@ {ts}{clr.RST}{delta_str}")
                    print(f"  {clr.C}{LINE}{clr.RST}")
                    print()
                    print_results(folder, total_sec, total_count, tree,
                                  durations, sizes, top, show_files=False,
                                  max_depth=getattr(args, 'depth', None) or 50,
                                  speeds=custom_speeds)
                    print(f"  {clr.DIM}Next check in {interval}s \u2014 Ctrl+C to stop{clr.RST}\n")
                last_sec = total_sec

            _time.sleep(interval)

        except KeyboardInterrupt:
            if use_json:
                print(json.dumps({"status": "stopped", "updates": update_n,
                                  "timestamp": datetime.now().isoformat()}), flush=True)
            else:
                print(f"\n\n  {clr.G}Watch stopped.{clr.RST}  {clr.DIM}{update_n} update(s) shown.{clr.RST}\n")
            sys.exit(EX.OK)


# ---------------------------------------------------------------------------
# compare / dupes / export / files
# ---------------------------------------------------------------------------

def cmd_compare(args, cfg, use_json, quiet):
    folder_a = Path(_resolve_alias(args.folder_a.strip().strip("'\""), cfg))
    folder_b = Path(_resolve_alias(args.folder_b.strip().strip("'\""), cfg))
    for f in (folder_a, folder_b):
        if not f.exists() or not f.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {f}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
    _require_ffprobe("compare", use_json)
    sort    = _resolve_sort(args, cfg)
    on_prog = _make_progress_bar(quiet, use_json)
    uc      = _use_cache(args, cfg)
    try:
        data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort, uc, quiet=quiet)
    except KeyboardInterrupt:
        if use_json:
            _json_error("Scan interrupted", EX.ERR_SCAN)
        print(f"\n\n  {clr.Y}Cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    if use_json:
        _json_out(_compare_to_json(folder_a, folder_b, data_a, data_b))
    else:
        print_comparison(folder_a, folder_b, data_a, data_b)
    sys.exit(EX.OK)


def cmd_dupes(args, cfg, use_json, quiet):
    folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("dupes", use_json)
    on_prog = _make_progress_bar(quiet, use_json)
    uc      = _use_cache(args, cfg)
    if not quiet:
        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    try:
        _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", uc)
    except KeyboardInterrupt:
        if use_json:
            _json_error("Scan interrupted", EX.ERR_SCAN)
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    if not quiet:
        probed     = len(durations) - hits
        cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{len(durations)}{clr.RST} files found.{cache_info}".ljust(100))
    groups = find_duplicates(durations, sizes)
    if use_json:
        _json_out(_dupes_to_json(groups, durations, sizes))
        sys.exit(EX.OK)
    print_duplicates(groups, durations)
    if args.out:
        buf = io.StringIO()
        if not groups:
            buf.write("No duplicates found.\n")
        else:
            for i, group in enumerate(groups, 1):
                from ._dupes import _group_wasted
                median_sec, _ = _group_wasted(group, durations)
                buf.write(f"Group {i}  |  {format_duration(median_sec)['hours_fmt']}  |  {len(group)} copies\n")
                for p in group:
                    buf.write(f"  -> {p}\n")
                buf.write("\n")
        try:
            Path(args.out).write_text(buf.getvalue(), encoding="utf-8")
            if not quiet:
                print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{args.out}{clr.RST}\n")
        except Exception as e:
            print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
    sys.exit(EX.OK)


def cmd_export(args, cfg, use_json, quiet):
    raw      = _resolve_alias(args.target.strip().strip("'\""), cfg)
    sort     = _resolve_sort(args, cfg)
    uc       = _use_cache(args, cfg)
    out_path = args.out or None
    fmt      = args.format

    if _is_url(raw):
        url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
        try:
            total_sec, total_count, entries, label, cache_hits, unavailable_count = \
                scan_url(raw, url_prog, use_cache=uc)
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
        if not quiet:
            api_fetched  = total_count - cache_hits
            yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                            if api_fetched > 0 else
                            f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
            unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
        try:
            dest = export_url_results(raw, label, total_sec, total_count, entries, fmt, out_path)
            if not quiet:
                print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{dest}{clr.RST}\n")
        except Exception as e:
            if use_json:
                _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
            print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)

    folder = Path(raw)
    if not folder.exists() or not folder.is_dir():
        if use_json:
            _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("export", use_json)
    on_prog = _make_progress_bar(quiet, use_json)
    if not quiet:
        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    try:
        total_sec, total_count, tree, durations, sizes, hits = _run_scan(
            folder, on_prog, sort, uc)
    except KeyboardInterrupt:
        if use_json:
            _json_error("Scan interrupted", EX.ERR_SCAN)
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    if not quiet:
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))
    try:
        dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
        if not quiet:
            print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{dest}{clr.RST}\n")
    except Exception as e:
        if use_json:
            _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
        print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
        sys.exit(EX.ERR_EXPORT)
    sys.exit(EX.OK)


def cmd_files(args, cfg, use_json, quiet):
    folder_raw = _resolve_alias(args.folder.strip().strip("'\""), cfg)
    folder     = Path(folder_raw)
    if not folder.exists() or not folder.is_dir():
        print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
    _require_ffprobe("files", use_json)
    sort    = _resolve_sort(args, cfg)
    top     = _resolve_top(args, cfg)
    uc      = _use_cache(args, cfg)
    on_prog = _make_progress_bar(quiet, use_json)
    if not quiet:
        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
    try:
        total_sec, total_count, tree, durations, sizes, hits = _run_scan(
            folder, on_prog, sort, uc)
    except KeyboardInterrupt:
        print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
        sys.exit(EX.ERR_SCAN)
    if not quiet:
        probed     = total_count - hits
        cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.{cache_info}".ljust(100))
    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                  show_files=True, max_depth=getattr(args, 'depth', None) or 50,
                  speeds=getattr(args, 'speeds', None) or None)
    sys.exit(EX.OK)


# ---------------------------------------------------------------------------
# scan  (single target + multi-target batch)
# ---------------------------------------------------------------------------

def cmd_scan(args, cfg, use_json, quiet):
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
    uc            = _use_cache(args, cfg)
    out_path      = getattr(args, 'out', None)
    fmt           = _resolve_out_format(out_path, getattr(args, 'fmt', None))
    do_merge      = getattr(args, 'merge', False)
    max_d         = getattr(args, 'depth', None) or 50
    custom_speeds = getattr(args, 'speeds', None) or None

    if len(targets) == 1:
        _cmd_scan_single(args, cfg, use_json, quiet,
                         targets[0], sort, top, uc, out_path, fmt, max_d, custom_speeds)
    else:
        _cmd_scan_batch(args, cfg, use_json, quiet,
                        targets, sort, top, uc, fmt, out_path, do_merge, max_d, custom_speeds)


def _cmd_scan_single(args, cfg, use_json, quiet,
                     raw_target, sort, top, uc, out_path, fmt, max_d, custom_speeds):
    raw     = _resolve_alias(raw_target, cfg)
    filters = _build_filters(args, use_json)

    if _is_url(raw):
        url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
        try:
            total_sec, total_count, entries, label, cache_hits, unavailable_count = \
                scan_url(raw, url_prog, use_cache=uc)
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
        total_sec, total_count, tree, durations, sizes, hits = _run_scan(
            folder, on_progress, sort, uc)
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
        _json_out(_scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits))
        sys.exit(EX.OK)

    if not quiet:
        probed     = total_count - hits
        cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.{cache_info}".ljust(100))

    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                  show_files=getattr(args, 'files', False), max_depth=max_d,
                  speeds=custom_speeds)

    # Auto-save history snapshot after every successful scan
    try:
        save_snapshot(Path(folder), total_sec, total_count,
                      sum(sizes.values()), durations)
    except Exception:
        pass  # history is best-effort, never crash the scan

    groups = find_duplicates(durations, sizes)
    if not quiet:
        print_dupe_warning(groups, folder)

    if fmt and out_path:
        try:
            dest = export_results(folder, total_sec, total_count, tree,
                                  durations, fmt, out_path)
            if not quiet:
                print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{dest}{clr.RST}\n")
        except Exception as e:
            if use_json:
                _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
            print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
    sys.exit(EX.OK)


def _cmd_scan_batch(args, cfg, use_json, quiet,
                    targets, sort, top, uc, fmt, out_path, do_merge, max_d, custom_speeds):
    _require_ffprobe("scan", use_json)
    filters = _build_filters(args, use_json)

    # Issue 19 fix: resolve aliases for every target in the batch loop
    folders = []
    for raw in [_resolve_alias(t, cfg) for t in targets]:
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
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, sort, uc)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if filters:
            durations, sizes = apply_filters(durations, sizes, filters)
            total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                folder, durations, sizes, sort)
        results.append((folder, total_sec, total_count, tree, durations, sizes, hits))
        if not quiet:
            fmt_dur = format_duration(total_sec)["hours_fmt"]
            print(f"\r  {clr.G}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                  f"{clr.Y}{fmt_dur}{clr.RST}  {clr.DIM}{total_count} files{clr.RST}".ljust(100))

    if do_merge:
        _print_merged(results, folders, use_json, quiet, custom_speeds)
    else:
        _print_batch_separate(results, use_json, quiet, top, max_d, custom_speeds)


def _print_merged(results, folders, use_json, quiet, custom_speeds):
    merged_sec   = sum(r[1] for r in results)
    merged_count = sum(r[2] for r in results)
    merged_dur   = {}
    merged_sizes = {}
    merged_hits  = sum(r[6] for r in results)
    for _, _, _, _, dur, sz, _ in results:
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
            "cache_hits":  merged_hits,
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


def _print_batch_separate(results, use_json, quiet, top, max_d, custom_speeds):
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

    for folder, total_sec, total_count, tree, durations, sizes, hits in results:
        print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                      show_files=False, max_depth=max_d, speeds=custom_speeds)
    sys.exit(EX.OK)
