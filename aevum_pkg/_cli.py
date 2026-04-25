"""
CLI entry point: argument parsing, subcommand dispatch, and main().
All business logic lives in the other modules.
"""
import argparse
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

from ._color   import R, G, Y, B, M, C, W, DIM, RST, LINE, clear, _disable_color
from ._scan    import (check_ffprobe, format_duration, format_size, _run_scan,
                       parse_duration_arg, apply_filters, rebuild_after_filter)
from ._youtube import _is_url, scan_url, _make_url_progress
from ._display import (print_results, print_url_results, print_banner,
                       print_post_scan_menu, _fuzzy_suggest)
from ._dupes   import find_duplicates, print_duplicates, print_dupe_warning
from ._compare import run_compare, print_comparison
from ._export  import export_results
from ._config  import (CONFIG_DEFAULTS, load_config, save_config,
                       cmd_doctor, cmd_cache, cmd_config, repl_config)
from ._youtube import load_api_key, prompt_api_key
from . import _exit as EX

__version__ = "1.0.0"


# ── JSON OUTPUT ───────────────────────────────────────────────────────

def _json_out(data: dict):
    """Write JSON to stdout and flush."""
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def _json_error(msg: str, code: int, extra: dict = None):
    d = {"status": "error", "code": code, "error": msg}
    if extra:
        d.update(extra)
    _json_out(d)
    sys.exit(code)


def _scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits):
    """Convert a completed local scan to a JSON-serialisable dict."""
    from ._export import _tree_to_dict
    subfolders, direct, root_bytes = tree
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "path":        str(Path(folder).resolve()),
        "total_files": total_count,
        "total_bytes": sum(sizes.values()),
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "cache_hits":  hits,
        "tree":        _tree_to_dict(Path(folder).name, total_sec, total_count, subfolders, direct),
        "files": [
            {
                "path":     str(p),
                "filename": p.name,
                "folder":   p.parent.name,
                "seconds":  round(s, 2),
                "bytes":    sizes.get(p, 0),
                "duration": format_duration(s)["hours_fmt"],
            }
            for p, s in sorted(durations.items(), key=lambda x: x[1], reverse=True)
        ],
    }


def _url_to_json(url, label, total_sec, total_count, entries):
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "url":         url,
        "label":       label,
        "total_files": total_count,
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "videos": [
            {
                "title":    e["title"],
                "channel":  e.get("channel", ""),
                "url":      e.get("url", ""),
                "seconds":  round(e["duration"], 2),
                "duration": format_duration(e["duration"])["hours_fmt"],
            }
            for e in sorted(entries, key=lambda x: x["duration"], reverse=True)
        ],
    }


def _dupes_to_json(groups, durations, sizes):
    total_wasted = 0.0
    out_groups   = []
    for group in groups:
        sec    = durations.get(group[0], 0.0)
        wasted = sec * (len(group) - 1)
        total_wasted += wasted
        out_groups.append({
            "copies":      len(group),
            "seconds":     round(sec, 2),
            "wasted_sec":  round(wasted, 2),
            "wasted_fmt":  format_duration(wasted)["hours_fmt"],
            "files": [{"path": str(p), "bytes": sizes.get(p, 0)} for p in group],
        })
    return {
        "status":           "ok",
        "command":          "dupes",
        "groups_found":     len(groups),
        "total_wasted_sec": round(total_wasted, 2),
        "total_wasted_fmt": format_duration(total_wasted)["hours_fmt"],
        "groups":           out_groups,
    }


def _compare_to_json(folder_a, folder_b, data_a, data_b):
    sec_a, count_a, _ = data_a
    sec_b, count_b, _ = data_b
    delta = sec_b - sec_a
    return {
        "status":  "ok",
        "command": "compare",
        "a": {
            "path":        str(Path(folder_a).resolve()),
            "total_files": count_a,
            "total_sec":   round(sec_a, 2),
            "duration":    format_duration(sec_a)["hours_fmt"],
        },
        "b": {
            "path":        str(Path(folder_b).resolve()),
            "total_files": count_b,
            "total_sec":   round(sec_b, 2),
            "duration":    format_duration(sec_b)["hours_fmt"],
        },
        "delta": {
            "seconds":  round(delta, 2),
            "duration": format_duration(abs(delta))["hours_fmt"],
            "sign":     "+" if delta >= 0 else "-",
            "files":    count_b - count_a,
        },
    }


# ── HELPERS ───────────────────────────────────────────────────────────

def _make_progress_bar(quiet=False, use_json=False):
    """Return None in machine-output modes; suppress all progress noise."""
    if quiet or use_json:
        return None
    def on_progress(done, total):
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "█" * filled + "░" * (24 - filled)
        print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
              end='', flush=True)
    return on_progress


def _require_ffprobe(context="", use_json=False):
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        if use_json:
            _json_error(
                f"ffprobe not found on PATH{ctx}. Install FFmpeg: https://ffmpeg.org/download.html",
                EX.ERR_DEPS,
            )
        print(f"\n  {R}[ERROR]{RST} ffprobe not found on PATH{ctx}.", file=sys.stderr)
        print(f"  {DIM}ffprobe is required for local folder scanning.{RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}", file=sys.stderr)
        print(f"  Then re-run:    {W}aevum doctor{RST}\n", file=sys.stderr)
        sys.exit(EX.ERR_DEPS)


def _resolve_sort(args, cfg):
    raw = getattr(args, 'sort', None) or cfg.get('sort') or 'name:asc'
    if ':' not in raw:
        defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
        raw = raw + ':' + defaults.get(raw, 'asc')
    return raw


def _resolve_top(args, cfg):
    v = getattr(args, 'top', None)
    return v if v is not None else cfg.get('top', 10)


def _resolve_out_format(out_path, explicit_fmt):
    if explicit_fmt:
        return explicit_fmt
    if out_path:
        ext = Path(out_path).suffix.lower().lstrip('.')
        if ext in ('txt', 'csv', 'json'):
            return ext
    return None


# ── ARG PARSING ───────────────────────────────────────────────────────

def _print_global_help():
    print(f"""
  {C}aevum {__version__}{RST}  {DIM}—{RST}  {W}Media Library Scanner{RST}

  {W}USAGE{RST}
    aevum [command] [options]
    aevum                           Open interactive shell
    aevum <path|url>                Quick scan (shorthand for 'aevum scan')

  {W}COMMANDS{RST}
    {G}scan{RST}      <path|url>            Scan a folder or YouTube URL
    {G}compare{RST}   <path> <path>         Compare two libraries side-by-side
    {G}dupes{RST}     <path>                Find duplicate-duration files
    {G}export{RST}    <path|url> <format>   Scan and write results to a file
    {G}watch{RST}     <path>                Re-scan automatically when folder changes
    {G}cache{RST}                           Manage the duration cache
    {G}config{RST}                          Read/write configuration
    {G}doctor{RST}                          Check environment (ffprobe, API key, cache)
    {G}version{RST}                         Print version and exit

  {W}GLOBAL OPTIONS{RST}
    --no-color                      Disable ANSI color output
    --json                          Machine-readable JSON output to stdout
    -q, --quiet                     Suppress decorative output (errors → stderr only)
    -h, --help                      Show this help
    -V, --version                   Show version

  {W}EXIT CODES{RST}
    0  success
    1  bad arguments / path not found
    2  missing dependency (ffprobe)
    3  scan error / interrupted
    4  export / write failed
    5  YouTube API error

  {W}PIPE EXAMPLES{RST}
    aevum scan D:\\Movies --json
    aevum scan D:\\Movies --json | python -m json.tool
    aevum dupes D:\\Movies --json | python -c "import sys,json; d=json.load(sys.stdin); print(d['groups_found'])"
    aevum scan D:\\Movies -q; echo "exit $?"

  {DIM}Run 'aevum <command> --help' for command-specific options.{RST}
""")


def _add_common_flags(p):
    """Attach --no-color / --json / --quiet to any subcommand parser."""
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--json",     action="store_true", help="Output JSON to stdout")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all decorative output; only errors go to stderr")


def _parse_args():
    argv = sys.argv[1:]

    # On Windows, "D:\" can arrive as two tokens: ["D:", "\"] — rejoin them.
    rejoined = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.endswith(':') and i + 1 < len(argv) and argv[i + 1] in ('\\', '/'):
            rejoined.append(tok + argv[i + 1])
            i += 2
        else:
            rejoined.append(tok)
            i += 1
    argv = rejoined

    if not argv or argv[0] in ('-h', '--help'):
        _print_global_help()
        sys.exit(EX.OK)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(EX.OK)

    subcommand = argv[0]
    SUBCOMMANDS = ('scan', 'compare', 'dupes', 'export', 'watch', 'cache', 'config', 'doctor', 'version', 'shell')

    if subcommand not in SUBCOMMANDS:
        from ._display import _fuzzy_suggest
        suggestion = _fuzzy_suggest(subcommand, list(SUBCOMMANDS))
        if (subcommand.startswith(('/', '\\', '.')) or
                ':' in subcommand or
                subcommand.startswith(('http://', 'https://', 'www.'))):
            argv       = ['scan'] + argv
            subcommand = 'scan'
        elif suggestion:
            print(f"\n  {R}aevum: '{subcommand}' is not a command.{RST}  {DIM}Did you mean{RST}  {W}{suggestion}{RST}{DIM}?{RST}\n")
            sys.exit(EX.ERR_ARGS)
        else:
            print(f"\n  {R}aevum: '{subcommand}' is not a command.{RST}  {DIM}Run{RST}  {W}aevum --help{RST}  {DIM}for a list of commands.{RST}\n")
            sys.exit(EX.ERR_ARGS)

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub, argv):
    if sub == 'scan':
        p = argparse.ArgumentParser(prog="aevum scan",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a local folder or YouTube URL and report total duration.",
            epilog=("Examples:\n"
                    "  aevum scan D:\\Movies\n"
                    "  aevum scan D:\\Movies --sort duration --top 20\n"
                    "  aevum scan D:\\Movies --json\n"
                    "  aevum scan https://youtube.com/@mkbhd --json\n"))
        p.add_argument("targets", nargs="*", default=[], metavar="PATH|URL")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("-f", "--files", action="store_true")
        p.add_argument("-o", "--out",   default=None, metavar="FILE")
        p.add_argument("--format", dest="fmt", choices=["txt","csv","json"], default=None)
        p.add_argument("--depth",  type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--merge", action="store_true",
                       help="Aggregate all targets into one combined grand total")
        p.add_argument("--min-duration", default=None, metavar="DURATION",
                       help="Exclude files shorter than this (e.g. 30s, 5m, 1h, 1:30:00)")
        p.add_argument("--max-duration", default=None, metavar="DURATION",
                       help="Exclude files longer than this")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]",
                       help="Only include these extensions, comma-separated (e.g. mkv,mp4)")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN",
                       help="Only include files inside folders matching this glob (e.g. 'Action*')")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'watch':
        p = argparse.ArgumentParser(prog="aevum watch",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Re-scan a folder automatically whenever its contents change.",
            epilog=("Examples:\n"
                    "  aevum watch D:\\Movies\n"
                    "  aevum watch D:\\Movies --interval 10\n"
                    "  aevum watch D:\\Movies --no-clear\n"
                    "  aevum watch D:\\Movies --json\n"
                    "  aevum watch D:\\Downloads --ext mkv,mp4 --min-duration 5m\n"))
        p.add_argument("folder", metavar="PATH")
        p.add_argument("-i", "--interval", type=float, default=5.0, metavar="SECONDS",
                       help="Poll interval in seconds (default: 5)")
        p.add_argument("--no-clear", action="store_true",
                       help="Don't clear the screen between updates")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("--min-duration", default=None, metavar="DURATION")
        p.add_argument("--max-duration", default=None, metavar="DURATION")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'compare':
        p = argparse.ArgumentParser(prog="aevum compare",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Compare the duration totals of two local libraries.",
            epilog=("Examples:\n"
                    "  aevum compare D:\\Movies E:\\Movies-Backup\n"
                    "  aevum compare D:\\Movies E:\\Movies-Backup --json\n"))
        p.add_argument("folder_a"); p.add_argument("folder_b")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'dupes':
        p = argparse.ArgumentParser(prog="aevum dupes",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Find duplicate files (by size + partial hash) in a folder.",
            epilog=("Examples:\n"
                    "  aevum dupes D:\\Movies\n"
                    "  aevum dupes D:\\Movies --json\n"
                    "  aevum dupes D:\\Movies -o dupes.txt\n"))
        p.add_argument("folder")
        p.add_argument("-o", "--out", default=None, metavar="FILE")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'export':
        p = argparse.ArgumentParser(prog="aevum export",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder or URL and write results directly to a file.",
            epilog=("Examples:\n"
                    "  aevum export D:\\Movies csv\n"
                    "  aevum export D:\\Movies json -o library.json\n"))
        p.add_argument("target", metavar="PATH|URL")
        p.add_argument("format", choices=["txt","csv","json"])
        p.add_argument("-o", "--out",  default=None, metavar="FILE")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'cache':
        p = argparse.ArgumentParser(prog="aevum cache",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage the duration cache.",
            epilog=("Subcommands:\n"
                    "  list          List all cache files\n"
                    "  clear         Delete all cache files\n"
                    "  clear <path>  Delete cache for a specific folder\n"
                    "  path          Print the cache directory path\n"))
        p.add_argument("action", nargs="?", default="list", choices=["list","clear","path"])
        p.add_argument("folder", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'config':
        p = argparse.ArgumentParser(prog="aevum config",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Read and write persistent configuration.",
            epilog=("Keys:  sort  top  no_color  cache_enabled  export_dir  yt_api_key\n\n"
                    "Examples:\n"
                    "  aevum config list\n"
                    "  aevum config set sort duration:desc\n"
                    "  aevum config set yt_api_key AIzaSy...\n"))
        p.add_argument("action", choices=["get","set","list","reset"])
        p.add_argument("key",   nargs="?", default=None)
        p.add_argument("value", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'doctor':
        p = argparse.ArgumentParser(prog="aevum doctor",
            description="Check environment: ffprobe, API key, cache, Python version.")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'version':
        print(f"aevum {__version__}"); sys.exit(EX.OK)

    if sub == 'shell':
        ns = types.SimpleNamespace(command='shell', no_color=False, json=False,
                                   quiet=False, sort=None, top=None)
        for a in argv:
            if a == '--no-color': ns.no_color = True
        return ns

    _print_global_help(); sys.exit(EX.ERR_ARGS)


# ── MAIN ──────────────────────────────────────────────────────────────

def _build_filters(args, use_json=False):
    """
    Parse filter-related args into a filters dict for apply_filters().
    Returns {} if no filters were requested.
    Exits with ERR_ARGS on bad input.
    """
    filters = {}
    for attr, key in (('min_duration', 'min_duration'), ('max_duration', 'max_duration')):
        raw = getattr(args, attr, None)
        if raw:
            try:
                filters[key] = parse_duration_arg(raw)
            except ValueError as e:
                if use_json:
                    _json_error(str(e), EX.ERR_ARGS)
                print(f"\n  {R}[ERROR]{RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
    raw_ext = getattr(args, 'ext', None)
    if raw_ext:
        filters['exts'] = {
            ('.' + x.lstrip('.').lower()) for x in raw_ext.split(',') if x.strip()
        }
    folder_pat = getattr(args, 'folder_pat', None)
    if folder_pat:
        filters['folder_pat'] = folder_pat
    return filters

def main():
    args = _parse_args()
    cfg  = load_config()

    use_json = getattr(args, 'json', False)
    quiet    = getattr(args, 'quiet', False) or use_json  # --json implies quiet UI

    if getattr(args, 'no_color', False) or cfg.get('no_color') or use_json:
        _disable_color()

    cmd = args.command

    # ── version ───────────────────────────────────────────────────────
    if cmd == 'version':
        if use_json:
            _json_out({"status": "ok", "version": __version__})
        else:
            print(f"aevum {__version__}")
        sys.exit(EX.OK)

    # ── doctor ────────────────────────────────────────────────────────
    if cmd == 'doctor':
        if use_json:
            import subprocess as _sp
            from ._cache import CACHE_DIR
            ffprobe_ok = check_ffprobe()
            try:
                r = _sp.run(['ffprobe', '-version'], capture_output=True, text=True)
                ffprobe_ver = r.stdout.splitlines()[0] if r.stdout else None
            except Exception:
                ffprobe_ver = None
            api_key = load_api_key()
            try:
                files = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
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
        cmd_doctor(cfg); sys.exit(EX.OK)

    # ── config ────────────────────────────────────────────────────────
    if cmd == 'config':
        cmd_config(args, cfg); sys.exit(EX.OK)

    # ── cache ─────────────────────────────────────────────────────────
    if cmd == 'cache':
        cmd_cache(args); sys.exit(EX.OK)

    # ── watch ─────────────────────────────────────────────────────────
    if cmd == 'watch':
        import time
        folder = Path(args.folder.strip().strip("'\""))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("watch", use_json)

        interval   = max(1.0, args.interval)
        no_clear   = args.no_clear or use_json  # never clear in JSON mode
        sort       = _resolve_sort(args, cfg)
        top        = _resolve_top(args, cfg)
        filters    = _build_filters(args, use_json)

        def _folder_snapshot(root):
            """Return a dict of {path: mtime} for all immediate subdirs + root."""
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
                folder, None, sort, use_cache=True)
            if filters:
                durations, sizes = apply_filters(durations, sizes, filters)
                total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                    folder, durations, sizes, sort)
            return total_sec, total_count, tree, durations, sizes, hits

        if not quiet:
            print(f"\n  {C}Watching{RST}  {W}{folder}{RST}  "
                  f"{DIM}(interval: {interval}s — Ctrl+C to stop){RST}\n")

        update_n  = 0
        last_snap = {}
        last_sec  = None

        while True:
            try:
                snap = _folder_snapshot(folder)
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
                            print(f"  {R}[ERROR]{RST} Scan failed: {e}", file=sys.stderr)
                        time.sleep(interval); continue

                    update_n += 1
                    ts = datetime.now().strftime("%H:%M:%S")

                    if use_json:
                        payload = _scan_to_json(folder, total_sec, total_count,
                                                tree, durations, sizes, hits)
                        payload["watch_update"]    = update_n
                        payload["timestamp"]       = datetime.now().isoformat()
                        payload["changed"]         = changed
                        payload["total_sec_delta"] = round(total_sec - (last_sec or total_sec), 2)
                        # newline-delimited JSON — one object per update
                        print(json.dumps(payload, ensure_ascii=False), flush=True)
                    else:
                        if not no_clear:
                            clear()
                        fmt = format_duration(total_sec)
                        delta_str = ""
                        if last_sec is not None and last_sec != total_sec:
                            delta  = total_sec - last_sec
                            sign   = "+" if delta >= 0 else ""
                            dfmt   = format_duration(abs(delta))["hours_fmt"]
                            dcol   = G if delta >= 0 else R
                            delta_str = f"  {dcol}{sign}{dfmt}{RST}"
                        print(f"  {C}{LINE}{RST}")
                        print(f"  {C}  Watching{RST}  {DIM}|{RST}  {W}{folder.name}{RST}  "
                              f"{DIM}|{RST}  {G}#{update_n}{RST}  {DIM}@ {ts}{RST}{delta_str}")
                        print(f"  {C}{LINE}{RST}")
                        print()
                        print_results(folder, total_sec, total_count, tree,
                                      durations, sizes, top, show_files=False)
                        print(f"  {DIM}Next check in {interval}s — Ctrl+C to stop{RST}\n")
                    last_sec = total_sec

                time.sleep(interval)

            except KeyboardInterrupt:
                if use_json:
                    print(json.dumps({"status": "stopped", "updates": update_n,
                                      "timestamp": datetime.now().isoformat()}), flush=True)
                else:
                    print(f"\n\n  {G}Watch stopped.{RST}  {DIM}{update_n} update(s) shown.{RST}\n")
                sys.exit(EX.OK)

    # ── compare ───────────────────────────────────────────────────────
    if cmd == 'compare':
        folder_a = Path(args.folder_a.strip().strip("'\""))
        folder_b = Path(args.folder_b.strip().strip("'\""))
        for f in (folder_a, folder_b):
            if not f.exists() or not f.is_dir():
                if use_json:
                    _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
                print(f"\n  {R}[ERROR]{RST} Not a valid folder: {f}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
        _require_ffprobe("compare", use_json)
        sort    = _resolve_sort(args, cfg)
        on_prog = _make_progress_bar(quiet, use_json)
        try:
            data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort,
                                         not args.no_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {Y}Cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
        if use_json:
            _json_out(_compare_to_json(folder_a, folder_b, data_a, data_b))
        else:
            print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(EX.OK)

    # ── dupes ─────────────────────────────────────────────────────────
    if cmd == 'dupes':
        folder = Path(args.folder.strip().strip("'\""))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("dupes", use_json)
        on_prog   = _make_progress_bar(quiet, use_json)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        if not quiet:
            print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", use_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
        if not quiet:
            probed = len(durations) - hits
            cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
            print(f"\r  {G}Done!{RST}  {W}{len(durations)}{RST} files found.{cache_info}".ljust(60))
        groups = find_duplicates(durations, sizes)
        if use_json:
            _json_out(_dupes_to_json(groups, durations, sizes))
            sys.exit(EX.OK)
        print_duplicates(groups, durations)
        if args.out:
            import io
            buf = io.StringIO()
            if not groups:
                buf.write("No duplicates found.\n")
            else:
                for i, group in enumerate(groups, 1):
                    sec = durations.get(group[0], 0.0)
                    buf.write(f"Group {i}  |  {format_duration(sec)['hours_fmt']}  |  {len(group)} copies\n")
                    for p in group:
                        buf.write(f"  -> {p}\n")
                    buf.write("\n")
            try:
                Path(args.out).write_text(buf.getvalue(), encoding="utf-8")
                if not quiet:
                    print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{args.out}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)

    # ── export ────────────────────────────────────────────────────────
    if cmd == 'export':
        raw       = args.target.strip().strip("'\"")
        sort      = _resolve_sort(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path  = args.out or None
        fmt       = args.format
        if _is_url(raw):
            url_prog = None if (quiet or use_json) else _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Fetch interrupted", EX.ERR_SCAN)
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
            except Exception as e:
                if use_json:
                    _json_error(str(e), EX.ERR_API)
                print(f"\n  {R}[ERROR]{RST} {e}\n", file=sys.stderr); sys.exit(EX.ERR_API)
            if not quiet:
                print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
            import io
            buf = io.StringIO()
            buf.write(f"AEVUM  |  {label}\n{'=' * 64}\n")
            buf.write(f"Total videos : {total_count}\n")
            buf.write(f"Duration     : {format_duration(total_sec)['hours_fmt']}\n\n")
            for e in sorted(entries, key=lambda x: x['duration'], reverse=True):
                buf.write(f"  {format_duration(e['duration'])['hours_fmt']}  |  {e['title']}\n")
            dest = Path(out_path) if out_path else Path(f"aevum_url_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}")
            try:
                dest.write_text(buf.getvalue(), encoding="utf-8")
                if not quiet:
                    print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                if use_json:
                    _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_EXPORT)
            sys.exit(EX.OK)

        folder = Path(raw)
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("export", use_json)
        on_prog = _make_progress_bar(quiet, use_json)
        if not quiet:
            print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_prog, sort, use_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
        if not quiet:
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} files found.".ljust(60))
        try:
            dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
            if not quiet:
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
        except Exception as e:
            if use_json:
                _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
            print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)


    # ── scan (headless) ───────────────────────────────────────────────
    if cmd == 'scan' and getattr(args, 'targets', None) is not None:
        targets   = [t.strip().strip("'\"") for t in args.targets]
        sort      = _resolve_sort(args, cfg)
        top       = _resolve_top(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path  = getattr(args, 'out', None)
        fmt       = _resolve_out_format(out_path, getattr(args, 'fmt', None))
        do_merge  = getattr(args, 'merge', False)

        if not targets:
            # No targets given — fall through to interactive shell
            pass
        elif len(targets) == 1:
            # ── single target (original behaviour) ───────────────────
            raw     = targets[0]
            filters = _build_filters(args, use_json)
            if _is_url(raw):
                url_prog = None if (quiet or use_json) else _make_url_progress()
                try:
                    total_sec, total_count, entries, label = scan_url(raw, url_prog)
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Fetch interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {Y}Fetch cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
                except Exception as e:
                    if use_json:
                        _json_error(str(e), EX.ERR_API)
                    print(f"\n  {R}[ERROR]{RST} {e}\n", file=sys.stderr); sys.exit(EX.ERR_API)
                if use_json:
                    _json_out(_url_to_json(raw, label, total_sec, total_count, entries))
                else:
                    if not quiet:
                        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
                    print_url_results(raw, label, total_sec, total_count, entries, top_n=top)
                sys.exit(EX.OK)

            folder = Path(raw)
            if not folder.exists():
                if use_json:
                    _json_error(f"Path not found: {folder}", EX.ERR_ARGS)
                print(f"\n  {R}[ERROR]{RST} Path not found: {folder}", file=sys.stderr)
                try:
                    sug = _fuzzy_suggest(folder.name,
                                         [p.name for p in folder.parent.iterdir() if p.is_dir()])
                    if sug:
                        print(f"  {DIM}Did you mean:{RST}  {W}{folder.parent / sug}{RST}", file=sys.stderr)
                except Exception:
                    pass
                print(); sys.exit(EX.ERR_ARGS)
            if not folder.is_dir():
                if use_json:
                    _json_error(f"That is a file, not a folder: {folder}", EX.ERR_ARGS)
                print(f"\n  {R}[ERROR]{RST} That is a file, not a folder: {folder}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            _require_ffprobe("scan", use_json)

            on_progress = _make_progress_bar(quiet, use_json)
            if not quiet:
                print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
            try:
                total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                    folder, on_progress, sort, use_cache)
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Scan interrupted", EX.ERR_SCAN)
                print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)

            # apply filters if any were requested
            if filters:
                durations, sizes = apply_filters(durations, sizes, filters)
                total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                    folder, durations, sizes, sort)
                if not quiet and not use_json:
                    removed = hits + (total_count - len(durations))  # rough
                    print(f"  {DIM}Filters applied — {len(durations)} files match.{RST}")

            if use_json:
                _json_out(_scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits))
                sys.exit(EX.OK)

            if not quiet:
                probed     = total_count - hits
                cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
                print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} files found.{cache_info}".ljust(60))
            print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                          show_files=getattr(args, 'files', False))
            groups = find_duplicates(durations, sizes)
            if not quiet:
                print_dupe_warning(groups)
            if fmt and out_path:
                try:
                    dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
                    if not quiet:
                        print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
                except Exception as e:
                    if use_json:
                        _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
                    print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                    sys.exit(EX.ERR_EXPORT)
            sys.exit(EX.OK)

        else:
            # ── multi-target batch ────────────────────────────────────
            _require_ffprobe("scan", use_json)
            filters = _build_filters(args, use_json)

            # validate all paths upfront before scanning anything
            folders = []
            for raw in targets:
                if _is_url(raw):
                    if use_json:
                        _json_error("Batch mode does not support URLs — use a single URL target", EX.ERR_ARGS)
                    print(f"\n  {R}[ERROR]{RST} Batch mode does not support URLs: {raw}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                f = Path(raw)
                if not f.exists() or not f.is_dir():
                    if use_json:
                        _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
                    print(f"\n  {R}[ERROR]{RST} Not a valid folder: {f}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                folders.append(f)

            # scan each folder
            results = []  # list of (folder, total_sec, total_count, tree, durations, sizes, hits)
            for i, folder in enumerate(folders, 1):
                if not quiet:
                    label_w = 40
                    print(f"  {DIM}[{i}/{len(folders)}]{RST}  {W}{folder.name:<{label_w}}{RST}  "
                          f"{DIM}scanning...{RST}", end='', flush=True)
                on_progress = _make_progress_bar(quiet=True)  # suppress bar in batch — too noisy
                try:
                    total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                        folder, on_progress, sort, use_cache)
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Scan interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(EX.ERR_SCAN)
                if filters:
                    durations, sizes = apply_filters(durations, sizes, filters)
                    total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                        folder, durations, sizes, sort)
                results.append((folder, total_sec, total_count, tree, durations, sizes, hits))
                if not quiet:
                    fmt_dur = format_duration(total_sec)["hours_fmt"]
                    print(f"\r  {G}[{i}/{len(folders)}]{RST}  {W}{folder.name:<{label_w}}{RST}  "
                          f"{Y}{fmt_dur}{RST}  {DIM}{total_count} files{RST}".ljust(80))

            if do_merge:
                # ── merged grand total ────────────────────────────────
                merged_sec   = sum(r[1] for r in results)
                merged_count = sum(r[2] for r in results)
                merged_dur   = {}
                merged_sizes = {}
                merged_hits  = sum(r[6] for r in results)
                for _, _, _, _, durations, sizes, _ in results:
                    merged_dur.update(durations)
                    merged_sizes.update(sizes)

                if use_json:
                    from ._scan import format_duration as _fd
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

                # human merged output
                fmt_merged = format_duration(merged_sec)
                print()
                print(f"  {C}{LINE}{RST}")
                print(f"  {W}  Batch Scan  {DIM}|{RST}  {len(folders)} folders  {DIM}(merged){RST}{RST}")
                print(f"  {C}{LINE}{RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {DIM}→{RST}  {W}{r[0].name:<35}{RST}  {Y}{fd}{RST}  {DIM}{r[2]} files{RST}")
                print()
                print(f"  {C}{LINE}{RST}")
                print(f"  {W}  Grand Total{RST}")
                print(f"  {C}{LINE}{RST}")
                total_bytes = sum(merged_sizes.values())
                print(f"  {W}  Total files   {DIM}:{RST}  {W}{merged_count}{RST}")
                print(f"  {W}  Total size    {DIM}:{RST}  {W}{format_size(total_bytes)}{RST}")
                print(f"  {W}  Days          {DIM}:{RST}  {W}{fmt_merged['days_fmt']}{RST}")
                print(f"  {W}  Hours         {DIM}:{RST}  {W}{fmt_merged['hours_fmt']}{RST}")
                print()
                print(f"  {C}{LINE}{RST}")
                print(f"  {W}  Playback Speed{RST}")
                print(f"  {C}{LINE}{RST}")
                for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
                    adj   = format_duration(merged_sec / speed)
                    slbl  = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
                    print(f"  {W}  {slbl:<6}        {DIM}:{RST}  {W}{adj['hours_fmt']}{RST}  {DIM}({adj['days_fmt']}){RST}")
                print()
                if top > 0:
                    from ._display import print_top_files
                    print_top_files(merged_dur, top)
            else:
                # ── per-folder output ─────────────────────────────────
                if use_json:
                    _json_out({
                        "status":  "ok",
                        "command": "scan",
                        "mode":    "batch",
                        "results": [
                            _scan_to_json(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
                            for r in results
                        ],
                    })
                    sys.exit(EX.OK)

                for folder, total_sec, total_count, tree, durations, sizes, hits in results:
                    if not quiet:
                        probed = total_count - hits
                        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
                    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                                  show_files=getattr(args, 'files', False))
                    groups = find_duplicates(durations, sizes)
                    if not quiet:
                        print_dupe_warning(groups)

                # batch summary footer
                batch_total_sec   = sum(r[1] for r in results)
                batch_total_count = sum(r[2] for r in results)
                batch_total_bytes = sum(sum(r[5].values()) for r in results)
                print(f"  {C}{LINE}{RST}")
                print(f"  {W}  Batch Summary  {DIM}|{RST}  {len(folders)} folders{RST}")
                print(f"  {C}{LINE}{RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {DIM}→{RST}  {W}{r[0].name:<35}{RST}  {Y}{fd}{RST}  {DIM}{r[2]} files{RST}")
                print()
                bfmt = format_duration(batch_total_sec)
                print(f"  {W}  Combined  {DIM}:{RST}  {W}{bfmt['hours_fmt']}{RST}  "
                      f"{DIM}|{RST}  {W}{batch_total_count} files{RST}  "
                      f"{DIM}|{RST}  {W}{format_size(batch_total_bytes)}{RST}")
                print()

            sys.exit(EX.OK)

    # ── INTERACTIVE / SHELL MODE ──────────────────────────────────────
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {Y}ffprobe not found on PATH.{RST}  {DIM}Local folder scanning won't work.{RST}")
        print(f"  Download FFmpeg from {C}https://ffmpeg.org/download.html{RST}\n")

    on_progress  = _make_progress_bar()
    last_scan    = {}
    current_sort = cfg.get('sort', 'name:asc')
    default_top  = cfg.get('top', 10)
    use_cache    = cfg.get('cache_enabled', True)

    while True:
        try:
            raw = input(f"  {C}aevum{RST}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {G}Goodbye!{RST}\n"); sys.exit(EX.OK)

        if not raw:
            continue
        raw = raw.strip().strip("'\"")
        if not raw:
            continue

        _init_map = {'1': 'scan', '2': 'clear', '3': 'quit'}
        if raw in _init_map:
            raw = _init_map[raw]

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {G}Goodbye!{RST}\n"); sys.exit(EX.OK)

        if raw.lower() in ('clear', 'c'):
            clear(); print_banner(); continue

        if raw.lower() in ('reset-key', 'apikey', 'api-key') or raw.lower().startswith('config set yt_api_key'):
            prompt_api_key(); continue

        if raw.lower().startswith('config '):
            parts = raw.split()
            repl_config(parts[1:], cfg); continue

        if raw.lower() == 'scan':
            print(f"\n  {DIM}Enter a folder path or YouTube URL to scan.{RST}\n"); continue

        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n"); continue
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=default_top)
            last_scan = {"folder": raw, "total_sec": total_sec, "total_count": total_count,
                         "tree": None, "durations": {e['title']: e['duration'] for e in entries},
                         "sizes": {}, "dupe_groups": [], "is_url": True,
                         "entries": entries, "label": label}
            print_post_scan_menu(current_sort); continue

        folder = Path(raw)
        if not folder.exists():
            print(f"\n  {R}[ERROR]{RST} Path not found: {raw}\n"); continue
        if not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} That is a file, not a folder.\n"); continue
        if not check_ffprobe():
            print(f"\n  {R}[ERROR]{RST} ffprobe not found. Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}\n"); continue

        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, current_sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n"); continue

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} files found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, default_top, show_files=False)
        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups)
        last_scan = {"folder": folder, "total_sec": total_sec, "total_count": total_count,
                     "tree": tree, "durations": durations, "sizes": sizes,
                     "dupe_groups": groups, "is_url": False}
        print_post_scan_menu(current_sort)

        while True:
            try:
                choice = input(f"  {C}aevum{RST}> ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n  {G}Goodbye!{RST}\n"); sys.exit(EX.OK)

            _menu_map = {'1': 'scan', '2': 'sort', '3': 'export', '4': 'clear', '5': 'quit', '6': 'duplicates'}
            if choice in _menu_map:
                choice = _menu_map[choice]

            _all_cmds  = ['scan', 'clear', 'export', 'sort', 'quit', 'exit', 'q', 'duplicates', 'dupes']
            first_word = choice.split()[0] if choice else ''

            if choice in ('quit', 'exit', 'q'):
                print(f"\n  {G}Goodbye!{RST}\n"); sys.exit(EX.OK)
            elif choice == 'clear':
                clear(); print_banner(); break
            elif choice == 'scan':
                break
            elif first_word == 'sort' or choice == 'sort':
                if last_scan.get("is_url"):
                    print(f"  {Y}Sort is not available for URL scans.{RST}\n")
                    print_post_scan_menu(current_sort); continue
                parts      = choice.split()
                field      = parts[1] if len(parts) >= 2 else None
                direc      = parts[2] if len(parts) >= 3 else None
                _field_opts = ('name', 'duration', 'count')
                _field_map  = {'1': 'name', '2': 'duration', '3': 'count'}
                while field not in _field_opts:
                    sug  = _fuzzy_suggest(field, list(_field_opts) + list(_field_map.keys())) if field else None
                    hint = f"  {DIM}Did you mean {W}{_field_map.get(sug, sug)}{RST}{DIM}?{RST}" if sug else ""
                    if field is not None:
                        print(f"  {R}Unknown.{RST}{hint}")
                    print(f"  {DIM}Sort by?{RST}  {G}1. name{RST}   {B}2. duration{RST}   {M}3. count{RST}   {DIM}0. back{RST}")
                    try:
                        field = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print(); field = 'back'
                    if field in _field_map: field = _field_map[field]
                    if field in ('back', '0', ''):
                        print_post_scan_menu(current_sort); field = None; break
                if field is None: continue
                _dir_aliases = {'asc': 'asc', 'ascending': 'asc', 'a': 'asc', '1': 'asc',
                                'desc': 'desc', 'descending': 'desc', 'd': 'desc', '2': 'desc'}
                dir_def      = 'asc' if field == 'name' else 'desc'
                dir_hint_str = (f"{G}1. ascending{RST} (a→z)   {B}2. descending{RST} (z→a)" if field == 'name'
                                else f"{G}1. descending{RST} (high→low)   {B}2. ascending{RST} (low→high)")
                while True:
                    if direc is None:
                        print(f"  {DIM}Direction?{RST}  {dir_hint_str}   {DIM}0. back{RST}  {DIM}[Enter = default]{RST}")
                        try:
                            direc = input(f"  {C}aevum{RST}> ").strip().lower()
                        except (KeyboardInterrupt, EOFError):
                            print(); direc = 'back'
                    if direc in ('back', '0'):
                        print_post_scan_menu(current_sort); direc = None; break
                    if direc == '': direc = dir_def
                    resolved = _dir_aliases.get(direc)
                    if resolved: direc = resolved; break
                    sug  = _fuzzy_suggest(direc, list(_dir_aliases.keys()))
                    hint = f"  {DIM}Did you mean {W}{sug}{RST}{DIM}?{RST}" if sug else ""
                    print(f"  {R}Unknown direction.{RST}{hint}"); direc = None
                if direc is None: continue
                current_sort = f"{field}:{direc}"
                _, _, new_tree, new_durations, new_sizes, _ = _run_scan(
                    last_scan["folder"], None, current_sort, True)
                last_scan.update(tree=new_tree, durations=new_durations, sizes=new_sizes)
                print_results(last_scan["folder"], last_scan["total_sec"],
                              last_scan["total_count"], new_tree, new_durations,
                              last_scan["sizes"], default_top, show_files=False)
                print_post_scan_menu(current_sort)
            elif first_word == 'export' or choice == 'export':
                if last_scan.get("is_url"):
                    print(f"  {Y}Export is not available for URL scans yet.{RST}\n")
                    print_post_scan_menu(current_sort); continue
                parts   = choice.split()
                fmt     = parts[1] if len(parts) >= 2 else None
                _fmt_opts = ('txt', 'csv', 'json')
                _fmt_map  = {'1': 'txt', '2': 'csv', '3': 'json'}
                while fmt not in _fmt_opts:
                    sug  = _fuzzy_suggest(fmt, list(_fmt_opts) + list(_fmt_map.keys())) if fmt else None
                    hint = f"  {DIM}Did you mean {W}{_fmt_map.get(sug, sug)}{RST}{DIM}?{RST}" if sug else ""
                    if fmt is not None: print(f"  {R}Unknown format.{RST}{hint}")
                    print(f"  {DIM}Export as?{RST}  {G}1. txt{RST}   {B}2. csv{RST}   {M}3. json{RST}   {DIM}0. back{RST}")
                    try:
                        fmt = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print(); fmt = 'back'
                    if fmt in _fmt_map: fmt = _fmt_map[fmt]
                    if fmt in ('back', '0', ''):
                        print_post_scan_menu(current_sort); fmt = None; break
                if fmt is None: continue
                out_dir  = cfg.get('export_dir') or None
                out_path = (Path(out_dir) / f"aevum_{Path(last_scan['folder']).name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}") if out_dir else None
                try:
                    dest = export_results(last_scan["folder"], last_scan["total_sec"],
                                          last_scan["total_count"], last_scan["tree"],
                                          last_scan["durations"], fmt, out_path)
                    print(f"\n  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
                except Exception as e:
                    print(f"\n  {R}Export failed:{RST} {e}\n")
            elif choice in ('duplicates', 'dupes'):
                if last_scan.get("is_url"):
                    print(f"  {Y}Duplicate detection is not available for URL scans.{RST}\n")
                    print_post_scan_menu(current_sort); continue
                print_duplicates(last_scan["dupe_groups"], last_scan["durations"])
                print_post_scan_menu(current_sort)
            else:
                sug = _fuzzy_suggest(first_word, _all_cmds) if first_word else None
                if sug:
                    print(f"  {R}Unknown command.{RST}  {DIM}Did you mean{RST}  {W}{sug}{RST}{DIM}?{RST}")
                else:
                    print(f"  {R}Invalid command.{RST} Type  {G}1. scan{RST}   {B}2. sort{RST}   {M}3. export{RST}   {Y}4. clear{RST}   {R}5. quit{RST}   {C}6. duplicates{RST}")
                print_post_scan_menu(current_sort)
