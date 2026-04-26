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

from ._color   import clr, LINE, clear, _disable_color
from ._scan    import (check_ffprobe, format_duration, format_size, _run_scan,
                       parse_duration_arg, apply_filters, rebuild_after_filter)
from ._youtube import _is_url, scan_url, _make_url_progress, get_quota_status
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


def _run_pip_upgrade(src_dir, quiet=False):
    """
    Run pip install --upgrade with a clean animated bar instead of pip's
    verbose output. Returns the process returncode.
    """
    import subprocess as _sp
    import threading

    pip_cmd = [sys.executable, "-m", "pip", "install", str(src_dir), "--upgrade", "-q"]

    if quiet:
        result = _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        return result.returncode

    _frames = [
        "████░░░░░░░░░░░░░░░░░░░░",
        "████████░░░░░░░░░░░░░░░░",
        "████████████░░░░░░░░░░░░",
        "████████████████░░░░░░░░",
        "████████████████████░░░░",
        "████████████████████████",
    ]
    _done = threading.Event()
    _rc   = [0]

    def _worker():
        r = _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.PIPE)
        _rc[0] = r.returncode
        _worker.err = r.stderr.decode(errors="replace").strip() if r.returncode != 0 else ""
        _done.set()

    _worker.err = ""
    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    frame_i = 0
    while not _done.wait(timeout=0.2):
        bar = _frames[frame_i % len(_frames)]
        print(f"\r  {clr.C}Installing...{clr.RST}  {clr.Y}{bar}{clr.RST}  ", end="", flush=True)
        frame_i += 1

    t.join()

    if _rc[0] == 0:
        print(f"\r  {clr.G}Done!{clr.RST}          {clr.G}{'█' * 24}{clr.RST}  ")
        print(f"\n  {clr.G}[OK]{clr.RST}  Aevum updated successfully.\n")
    else:
        print(f"\r  {clr.R}[FAIL]{clr.RST} pip install failed (exit {_rc[0]}).  ")
        if _worker.err:
            for line in _worker.err.splitlines()[-6:]:
                print(f"  {clr.DIM}{line}{clr.RST}")
        print()

    return _rc[0]


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
        print(f"\r  {clr.C}Scanning...{clr.RST}  {bar}  {clr.Y}{done}/{total}{clr.RST}  {clr.DIM}({pct}%){clr.RST}",
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
        print(f"\n  {clr.R}[ERROR]{clr.RST} ffprobe not found on PATH{ctx}.", file=sys.stderr)
        print(f"  {clr.DIM}ffprobe is required for local folder scanning.{clr.RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}", file=sys.stderr)
        print(f"  Then re-run:    {clr.W}aevum doctor{clr.RST}\n", file=sys.stderr)
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
  {clr.C}aevum {__version__}{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}USAGE{clr.RST}
    aevum [command] [options]
    aevum                           Open interactive shell
    aevum <path|url>                Quick scan (shorthand for 'aevum scan')

  {clr.W}COMMANDS{clr.RST}
    {clr.G}scan{clr.RST}      <path|url>            Scan a folder or YouTube URL
    {clr.G}compare{clr.RST}   <path> <path>         Compare two libraries side-by-side
    {clr.G}dupes{clr.RST}     <path>                Find duplicate-duration files
    {clr.G}export{clr.RST}    <path|url> <format>   Scan and write results to a file
    {clr.G}watch{clr.RST}     <path>                Re-scan automatically when folder changes
    {clr.G}alias{clr.RST}                           Manage short aliases for folder paths
    {clr.G}cache{clr.RST}                           Manage the duration cache
    {clr.G}quota{clr.RST}                           Check YouTube API quota usage
    {clr.G}update{clr.RST}                          Upgrade Aevum to the latest version
    {clr.G}clearpath{clr.RST}                       Clear saved project path for updates
    {clr.G}config{clr.RST}                          Read/write configuration
    {clr.G}doctor{clr.RST}                          Check environment (ffprobe, API key, cache)
    {clr.G}version{clr.RST}                         Print version and exit

  {clr.W}GLOBAL OPTIONS{clr.RST}
    --no-color                      Disable ANSI color output
    --json                          Machine-readable JSON output to stdout
    -q, --quiet                     Suppress decorative output (errors → stderr only)
    -h, --help                      Show this help
    -V, --version                   Show version

  {clr.W}EXIT CODES{clr.RST}
    0  success
    1  bad arguments / path not found
    2  missing dependency (ffprobe)
    3  scan error / interrupted
    4  export / write failed
    5  YouTube API error

  {clr.W}PIPE EXAMPLES{clr.RST}
    aevum scan D:\\Movies --json
    aevum scan D:\\Movies --json | python -m json.tool
    aevum dupes D:\\Movies --json | python -c "import sys,json; d=json.load(sys.stdin); print(d['groups_found'])"
    aevum scan D:\\Movies -q; echo "exit $?"

  {clr.DIM}Run 'aevum <command> --help' for command-specific options.{clr.RST}
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

    # No arguments — enter interactive shell mode
    if not argv:
        import types
        return types.SimpleNamespace(command='shell', no_color=False, json=False,
                                     quiet=False, sort=None, top=None)
    
    # Help flag explicitly requested
    if argv[0] in ('-h', '--help'):
        _print_global_help()
        sys.exit(EX.OK)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(EX.OK)
    if argv[0] in ('-U', '--upgrade'):
        argv = ['update'] + argv[1:]

    subcommand = argv[0]
    SUBCOMMANDS = ('scan', 'compare', 'dupes', 'export', 'watch', 'cache', 'config', 'alias', 'doctor', 'quota', 'version', 'update', 'clearpath', 'shell')

    if subcommand not in SUBCOMMANDS:
        from ._display import _fuzzy_suggest
        suggestion = _fuzzy_suggest(subcommand, list(SUBCOMMANDS))
        if (subcommand.startswith(('/', '\\', '.')) or
                ':' in subcommand or
                subcommand.startswith(('http://', 'https://', 'www.'))):
            # Separate flag tokens (start with -) from path tokens, then
            # join all non-flag tokens as a single path so unquoted paths
            # with spaces work: aevum D:\My Folder\With Spaces
            flags      = [t for t in argv if t.startswith('-')]
            path_parts = [t for t in argv if not t.startswith('-')]
            joined_path = ' '.join(path_parts)
            argv       = ['scan', joined_path] + flags
            subcommand = 'scan'
        elif suggestion:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  {clr.DIM}Did you mean{clr.RST}  {clr.W}{suggestion}{clr.RST}{clr.DIM}?{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)
        else:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  {clr.DIM}Run{clr.RST}  {clr.W}aevum --help{clr.RST}  {clr.DIM}for a list of commands.{clr.RST}\n")
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

    if sub == 'update':
        p = argparse.ArgumentParser(prog="aevum update",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Upgrade Aevum to the latest version via pip.",
            epilog=("Examples:\n"
                    "  aevum update\n"
                    "  aevum -U\n"))
        p.add_argument("--dry-run", action="store_true",
                       help="Show what would run without actually upgrading")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'alias':
        p = argparse.ArgumentParser(prog="aevum alias",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage short aliases for long folder paths.",
            epilog=("Examples:\n"
                    "  aevum alias list\n"
                    "  aevum alias set M D:\\02-Media\n"
                    "  aevum alias set Media D:\\02-Media\n"
                    "  aevum alias remove M\n"
                    "  aevum scan M\n"))
        p.add_argument("action", nargs="?", default="list",
                       choices=["list", "set", "remove", "rm"])
        p.add_argument("name",  nargs="?", default=None,
                       help="Alias name (e.g. M, Media)")
        p.add_argument("path",  nargs="?", default=None,
                       help="Full folder path to bind the alias to")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'doctor':
        p = argparse.ArgumentParser(prog="aevum doctor",
            description="Check environment: ffprobe, API key, cache, Python version.")
        _add_common_flags(p)
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'quota':
        p = argparse.ArgumentParser(prog="aevum quota",
            description="Check YouTube API quota usage for today.")
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
                print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
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

def _repl_alias(parts, cfg):
    """Handle 'alias ...' typed inside the interactive REPL."""
    import types as _types
    action   = parts[0] if parts else "list"
    name     = parts[1] if len(parts) > 1 else None
    path_val = parts[2] if len(parts) > 2 else None
    ns = _types.SimpleNamespace(command="alias", action=action,
                                name=name, path=path_val, no_color=False)
    # Delegate to the same logic as the CLI command by re-using main() internals.
    # Simpler: just inline the same short logic here.
    aliases = cfg.setdefault("aliases", {})
    if action == "list":
        if not aliases:
            print(f"  {clr.DIM}No aliases defined.{clr.RST}  "
                  f"Add one with: {clr.W}alias set <name> <path>{clr.RST}\n")
            return
        from ._color import LINE
        print()
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Aliases{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for k, v in sorted(aliases.items()):
            exists = Path(v).is_dir()
            status = f"{clr.G}✓{clr.RST}" if exists else f"{clr.R}✗ (not found){clr.RST}"
            print(f"  {clr.G}{k:<15}{clr.RST}  {clr.W}{v}{clr.RST}  {status}")
        print()
    elif action in ("remove", "rm"):
        if not name:
            print(f"  {clr.R}[ERROR]{clr.RST} Usage: alias remove <name>\n"); return
        if name not in aliases:
            print(f"  {clr.Y}[WARN]{clr.RST}  Alias '{name}' not found.\n"); return
        del aliases[name]
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  Alias '{name}' removed.\n")
    elif action == "set":
        if not name or not path_val:
            print(f"  {clr.R}[ERROR]{clr.RST} Usage: alias set <name> <path>\n"); return
        resolved = Path(path_val.strip().strip("'\""))
        if not resolved.exists():
            print(f"  {clr.Y}[WARN]{clr.RST}  Path does not exist: {resolved}")
        aliases[name] = str(resolved)
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{resolved}{clr.RST}\n")
    else:
        print(f"  {clr.DIM}Usage: alias list | alias set <name> <path> | alias remove <name>{clr.RST}\n")


def _resolve_alias(raw, cfg):
    """
    If ``raw`` matches a known alias (case-insensitive), return the real path.
    Otherwise return ``raw`` unchanged.
    """
    aliases = cfg.get("aliases") or {}
    return aliases.get(raw) or aliases.get(raw.upper()) or aliases.get(raw.lower()) or raw


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

    # ── update ───────────────────────────────────────────────────────────
    if cmd == 'update':
        import subprocess as _sp

        def _find_project_dir():
            """Return the saved or auto-detected project dir, or None."""
            # 1. Saved in config
            saved = cfg.get('project_dir', '')
            if saved and (Path(saved) / "pyproject.toml").exists():
                return Path(saved)
            # 2. cwd
            if (Path.cwd() / "pyproject.toml").exists():
                return Path.cwd()
            return None

        _src_dir = _find_project_dir()

        # Saved path no longer valid — tell user and ask again
        saved_path = cfg.get('project_dir', '')
        if saved_path and not (Path(saved_path) / "pyproject.toml").exists():
            print(f"\n  {clr.Y}[WARN]{clr.RST}  Saved project path no longer exists: {clr.W}{saved_path}{clr.RST}")
            print(f"  The project folder may have moved or been renamed.")
            _src_dir = None

        if _src_dir is None:
            print(f"\n  {clr.Y}Aevum project folder not found.{clr.RST}")
            print(f"  {clr.DIM}Option 1:{clr.RST}  cd to your Aevum folder and run {clr.W}aevum update{clr.RST} from there.")
            print(f"  {clr.DIM}Option 2:{clr.RST}  Paste the path to your Aevum folder below and it will be saved for next time.")
            print()
            try:
                pasted = input(f"  {clr.C}Aevum folder path{clr.RST} (or Enter to cancel)> ").strip().strip("'\"")
            except (KeyboardInterrupt, EOFError):
                print(); sys.exit(EX.OK)
            if not pasted:
                sys.exit(EX.OK)
            _src_dir = Path(pasted)
            if not (_src_dir / "pyproject.toml").exists():
                print(f"\n  {clr.R}[ERROR]{clr.RST} No pyproject.toml found at {_src_dir}. Please check the path.\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            cfg['project_dir'] = str(_src_dir)
            save_config(cfg)
            print(f"  {clr.G}[OK]{clr.RST}  Path saved. You can run {clr.W}aevum update{clr.RST} from anywhere now.\n")

        if getattr(args, 'dry_run', False):
            pip_cmd = [sys.executable, "-m", "pip", "install", str(_src_dir), "--upgrade", "-q"]
            print(f"  {clr.DIM}Would run:{clr.RST}  {clr.W}{' '.join(pip_cmd)}{clr.RST}")
            sys.exit(EX.OK)
        if not quiet:
            print(f"  {clr.W}Upgrading Aevum from{clr.RST}  {clr.C}{_src_dir}{clr.RST}\n")
        rc = _run_pip_upgrade(_src_dir, quiet=quiet)
        sys.exit(rc)
    
    # ── clearpath ────────────────────────────────────────────────────────
    if cmd == 'clearpath':
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
    
    # ── alias ────────────────────────────────────────────────────────────
    if cmd == 'alias':
        aliases  = cfg.setdefault("aliases", {})
        action   = getattr(args, 'action', 'list') or 'list'
        name     = getattr(args, 'name', None)
        path_val = getattr(args, 'path', None)

        if action == 'list':
            if not aliases:
                print(f"  {clr.DIM}No aliases defined.{clr.RST}  "
                      f"Add one with: {clr.W}aevum alias set <name> <path>{clr.RST}\n")
                sys.exit(EX.OK)
            from ._color import LINE
            print()
            print(f"  {clr.C}{LINE}{clr.RST}")
            print(f"  {clr.W}  Aliases{clr.RST}")
            print(f"  {clr.C}{LINE}{clr.RST}")
            for k, v in sorted(aliases.items()):
                exists = Path(v).is_dir()
                status = f"{clr.G}✓{clr.RST}" if exists else f"{clr.R}✗ (not found){clr.RST}"
                print(f"  {clr.G}{k:<15}{clr.RST}  {clr.W}{v}{clr.RST}  {status}")
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
                print(f"  {clr.R}[ERROR]{clr.RST} Usage: aevum alias set <name> <path>", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            resolved = Path(path_val.strip().strip("'\""))
            if not resolved.exists():
                print(f"  {clr.Y}[WARN]{clr.RST}  Path does not exist: {resolved}")
            aliases[name] = str(resolved)
            save_config(cfg)
            print(f"  {clr.G}[OK]{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{resolved}{clr.RST}")
            sys.exit(EX.OK)

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

    # ── quota ─────────────────────────────────────────────────────────
    if cmd == 'quota':
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
                "status": "ok",
                "command": "quota",
                "quota_used": used,
                "quota_remaining": remaining,
                "quota_limit": 10000,
                "percent_used": round(pct, 2),
            })
        else:
            from ._color import LINE
            print()
            print(f"  {clr.C}{LINE}{clr.RST}")
            print(f"  {clr.C}  YouTube API Quota{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}Today's Usage{clr.RST}")
            print(f"  {clr.C}{LINE}{clr.RST}")
            print()
            
            if pct < 50:
                status_col = clr.G
                status = "Good"
            elif pct < 80:
                status_col = clr.Y
                status = "Moderate"
            else:
                status_col = clr.R
                status = "High"
            
            print(f"  {clr.W}  Used       {clr.DIM}:{clr.RST}  {status_col}{used:,} units{clr.RST}  {clr.DIM}({pct:.1f}%){clr.RST}")
            print(f"  {clr.W}  Remaining  {clr.DIM}:{clr.RST}  {clr.G}{remaining:,} units{clr.RST}")
            print(f"  {clr.W}  Daily Limit{clr.DIM}:{clr.RST}  {clr.W}10,000 units{clr.RST}")
            print(f"  {clr.W}  Status     {clr.DIM}:{clr.RST}  {status_col}{status}{clr.RST}")
            print()
            print(f"  {clr.DIM}Note: This tracks Aevum's usage only. Other apps using your")
            print(f"  API key won't be counted. Quota resets daily at midnight PT.{clr.RST}")
            print()
        sys.exit(EX.OK)

    # ── cache ─────────────────────────────────────────────────────────
    if cmd == 'cache':
        cmd_cache(args); sys.exit(EX.OK)

    # ── watch ─────────────────────────────────────────────────────────
    if cmd == 'watch':
        import time
        folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
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
            print(f"\n  {clr.C}Watching{clr.RST}  {clr.W}{folder}{clr.RST}  "
                  f"{clr.DIM}(interval: {interval}s — Ctrl+C to stop){clr.RST}\n")

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
                            print(f"  {clr.R}[ERROR]{clr.RST} Scan failed: {e}", file=sys.stderr)
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
                            delta_str = f"  {dcol}{sign}{dfmt}{clr.RST}"
                        print(f"  {clr.C}{LINE}{clr.RST}")
                        print(f"  {clr.C}  Watching{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{folder.name}{clr.RST}  "
                              f"{clr.DIM}|{clr.RST}  {clr.G}#{update_n}{clr.RST}  {clr.DIM}@ {ts}{clr.RST}{delta_str}")
                        print(f"  {clr.C}{LINE}{clr.RST}")
                        print()
                        print_results(folder, total_sec, total_count, tree,
                                      durations, sizes, top, show_files=False)
                        print(f"  {clr.DIM}Next check in {interval}s — Ctrl+C to stop{clr.RST}\n")
                    last_sec = total_sec

                time.sleep(interval)

            except KeyboardInterrupt:
                if use_json:
                    print(json.dumps({"status": "stopped", "updates": update_n,
                                      "timestamp": datetime.now().isoformat()}), flush=True)
                else:
                    print(f"\n\n  {clr.G}Watch stopped.{clr.RST}  {clr.DIM}{update_n} update(s) shown.{clr.RST}\n")
                sys.exit(EX.OK)

    # ── compare ───────────────────────────────────────────────────────
    if cmd == 'compare':
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
        try:
            data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort,
                                         not args.no_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
        if use_json:
            _json_out(_compare_to_json(folder_a, folder_b, data_a, data_b))
        else:
            print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(EX.OK)

    # ── dupes ─────────────────────────────────────────────────────────
    if cmd == 'dupes':
        folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("dupes", use_json)
        on_prog   = _make_progress_bar(quiet, use_json)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        if not quiet:
            print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
        try:
            _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", use_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
        if not quiet:
            probed = len(durations) - hits
            cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{len(durations)}{clr.RST} files found.{cache_info}".ljust(100))
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
                    print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{args.out}{clr.RST}\n")
            except Exception as e:
                print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)

    # ── export ────────────────────────────────────────────────────────
    if cmd == 'export':
        raw       = _resolve_alias(args.target.strip().strip("'\""), cfg)
        sort      = _resolve_sort(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path  = args.out or None
        fmt       = args.format
        if _is_url(raw):
            url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
            try:
                total_sec, total_count, entries, label, cache_hits = scan_url(raw, url_prog, use_cache=not getattr(args, 'no_cache', False))
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Fetch interrupted", EX.ERR_SCAN)
                print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
            except Exception as e:
                if use_json:
                    _json_error(str(e), EX.ERR_API)
                print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr); sys.exit(EX.ERR_API)
            if not quiet:
                api_fetched = total_count - cache_hits
                yt_info = f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}" if api_fetched > 0 else f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}"
                print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}".ljust(100))
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
                    print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
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
                folder, on_prog, sort, use_cache)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
        if not quiet:
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))
        try:
            dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
            if not quiet:
                print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
        except Exception as e:
            if use_json:
                _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
            print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)


    # ── scan (headless) ───────────────────────────────────────────────
    if cmd == 'scan' and getattr(args, 'targets', None) is not None:
        targets = [t.strip().strip("'\"") for t in args.targets]

        # If the user typed an unquoted path with spaces, argparse splits it
        # into multiple tokens (e.g. ["D:\\My", "Folder", "With", "Spaces"]).
        # Heuristic: if the first token looks like a Windows/Unix path (not a URL,
        # not a flag, contains a drive letter or path separator) and none of the
        # other tokens look like independent paths, join them all as one path.
        if len(targets) > 1 and not any(_is_url(t) for t in targets):
            first = targets[0]
            first_looks_like_path = (
                (':' in first) or
                first.startswith(('/', '\\', '.'))
            )
            rest_look_like_paths = any(
                (':' in t or t.startswith(('/', '\\'))) for t in targets[1:]
            )
            if first_looks_like_path and not rest_look_like_paths:
                targets = [' '.join(targets)]
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
            raw     = _resolve_alias(targets[0], cfg)
            filters = _build_filters(args, use_json)
            if _is_url(raw):
                url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
                try:
                    total_sec, total_count, entries, label, cache_hits = scan_url(raw, url_prog, use_cache=not getattr(args, 'no_cache', False))
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Fetch interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
                except Exception as e:
                    if use_json:
                        _json_error(str(e), EX.ERR_API)
                    print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr); sys.exit(EX.ERR_API)
                if use_json:
                    _json_out(_url_to_json(raw, label, total_sec, total_count, entries))
                else:
                    if not quiet:
                        api_fetched = total_count - cache_hits
                        yt_info = f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}" if api_fetched > 0 else f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}"
                        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}".ljust(100))
                    print_url_results(raw, label, total_sec, total_count, entries, top_n=top)
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
                print(); sys.exit(EX.ERR_ARGS)
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
                    folder, on_progress, sort, use_cache)
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Scan interrupted", EX.ERR_SCAN)
                print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)

            # apply filters if any were requested
            if filters:
                durations, sizes = apply_filters(durations, sizes, filters)
                total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                    folder, durations, sizes, sort)
                if not quiet and not use_json:
                    removed = hits + (total_count - len(durations))  # rough
                    print(f"  {clr.DIM}Filters applied — {len(durations)} files match.{clr.RST}")

            if use_json:
                _json_out(_scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits))
                sys.exit(EX.OK)

            if not quiet:
                probed     = total_count - hits
                cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
                print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.{cache_info}".ljust(100))
            print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                          show_files=getattr(args, 'files', False))
            groups = find_duplicates(durations, sizes)
            if not quiet:
                print_dupe_warning(groups, folder)
            if fmt and out_path:
                try:
                    dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
                    if not quiet:
                        print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
                except Exception as e:
                    if use_json:
                        _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
                    print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
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
                    print(f"\n  {clr.R}[ERROR]{clr.RST} Batch mode does not support URLs: {raw}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                f = Path(raw)
                if not f.exists() or not f.is_dir():
                    if use_json:
                        _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
                    print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {f}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                folders.append(f)

            # scan each folder
            results = []  # list of (folder, total_sec, total_count, tree, durations, sizes, hits)
            for i, folder in enumerate(folders, 1):
                if not quiet:
                    label_w = 40
                    print(f"  {clr.DIM}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                          f"{clr.DIM}scanning...{clr.RST}", end='', flush=True)
                on_progress = _make_progress_bar(quiet=True)  # suppress bar in batch — too noisy
                try:
                    total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                        folder, on_progress, sort, use_cache)
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Scan interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n"); sys.exit(EX.ERR_SCAN)
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
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Batch Scan  {clr.DIM}|{clr.RST}  {len(folders)} folders  {clr.DIM}(merged){clr.RST}{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {clr.DIM}→{clr.RST}  {clr.W}{r[0].name:<35}{clr.RST}  {clr.Y}{fd}{clr.RST}  {clr.DIM}{r[2]} files{clr.RST}")
                print()
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Grand Total{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                total_bytes = sum(merged_sizes.values())
                print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{merged_count}{clr.RST}")
                print(f"  {clr.W}  Total size    {clr.DIM}:{clr.RST}  {clr.W}{format_size(total_bytes)}{clr.RST}")
                print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['days_fmt']}{clr.RST}")
                print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['hours_fmt']}{clr.RST}")
                print()
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Playback Speed{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
                    adj   = format_duration(merged_sec / speed)
                    slbl  = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
                    print(f"  {clr.W}  {slbl:<6}        {clr.DIM}:{clr.RST}  {clr.W}{adj['hours_fmt']}{clr.RST}  {clr.DIM}({adj['days_fmt']}){clr.RST}")
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
                        cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
                    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                                  show_files=getattr(args, 'files', False))
                    groups = find_duplicates(durations, sizes)
                    if not quiet:
                        print_dupe_warning(groups, folder)

                # batch summary footer
                batch_total_sec   = sum(r[1] for r in results)
                batch_total_count = sum(r[2] for r in results)
                batch_total_bytes = sum(sum(r[5].values()) for r in results)
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Batch Summary  {clr.DIM}|{clr.RST}  {len(folders)} folders{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {clr.DIM}→{clr.RST}  {clr.W}{r[0].name:<35}{clr.RST}  {clr.Y}{fd}{clr.RST}  {clr.DIM}{r[2]} files{clr.RST}")
                print()
                bfmt = format_duration(batch_total_sec)
                print(f"  {clr.W}  Combined  {clr.DIM}:{clr.RST}  {clr.W}{bfmt['hours_fmt']}{clr.RST}  "
                      f"{clr.DIM}|{clr.RST}  {clr.W}{batch_total_count} files{clr.RST}  "
                      f"{clr.DIM}|{clr.RST}  {clr.W}{format_size(batch_total_bytes)}{clr.RST}")
                print()

            sys.exit(EX.OK)

    # ── INTERACTIVE / SHELL MODE ──────────────────────────────────────
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {clr.Y}ffprobe not found on PATH.{clr.RST}  {clr.DIM}Local folder scanning won't work.{clr.RST}")
        print(f"  Download FFmpeg from {clr.C}https://ffmpeg.org/download.html{clr.RST}\n")

    on_progress  = _make_progress_bar()
    last_scan    = {}
    current_sort = cfg.get('sort', 'name:asc')
    default_top  = cfg.get('top', 10)
    use_cache    = cfg.get('cache_enabled', True)

    while True:
        try:
            raw = input(f"  {clr.C}aevum{clr.RST}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {clr.G}Goodbye!{clr.RST}\n"); sys.exit(EX.OK)

        if not raw:
            continue
        raw = raw.strip().strip("'\"")
        if not raw:
            continue

        _init_map = {'1': 'scan', '2': 'clear', '3': 'quit'}
        if raw in _init_map:
            raw = _init_map[raw]

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {clr.G}Goodbye!{clr.RST}\n"); sys.exit(EX.OK)

        if raw.lower() in ('clear', 'c'):
            clear(); print_banner(); continue

        if raw.lower() in ('reset-key', 'apikey', 'api-key') or raw.lower().startswith('config set yt_api_key'):
            prompt_api_key(); continue

        if raw.lower().startswith('config '):
            parts = raw.split()
            repl_config(parts[1:], cfg); continue

        if raw.lower().startswith('alias'):
            parts = raw.split()
            _repl_alias(parts[1:], cfg); continue

        if raw.lower() in ('update', 'upgrade'):
            import subprocess as _sp
            _saved = cfg.get('project_dir', '')
            _src_dir = None
            
            # Check if saved path exists and is valid
            if _saved and (Path(_saved) / "pyproject.toml").exists():
                _src_dir = Path(_saved)
            
            # Warn if saved path is invalid
            if _saved and not (Path(_saved) / "pyproject.toml").exists():
                print(f"  {clr.Y}[WARN]{clr.RST}  Saved project path no longer exists: {clr.W}{_saved}{clr.RST}")
                _src_dir = None
            
            # If we have a valid saved path, just run the update
            if _src_dir is not None:
                print(f"  {clr.W}Upgrading Aevum from{clr.RST}  {clr.C}{_src_dir}{clr.RST}\n")
                _run_pip_upgrade(_src_dir)
                continue
            
            # No saved path - check if we're in the project folder
            if (Path.cwd() / "pyproject.toml").exists():
                print(f"  {clr.G}You're already in the Aevum project folder.{clr.RST}")
                print(f"  {clr.W}Run:{clr.RST}  {clr.C}pip install . --upgrade{clr.RST}\n")
                continue
            
            # Not in project folder and no saved path - show options
            print(f"  {clr.Y}Aevum project folder not found.{clr.RST}")
            print()
            print(f"  {clr.DIM}Option 1:{clr.RST}  Go to your Aevum folder and run: {clr.C}pip install . --upgrade{clr.RST}")
            print(f"  {clr.DIM}Option 2:{clr.RST}  Enter the path below to save it for future updates")
            print()
            
            try:
                _pasted = input(f"  {clr.C}Aevum folder path{clr.RST} (or Enter to cancel)> ").strip().strip("'\"")
            except (KeyboardInterrupt, EOFError):
                print(); continue
            
            if not _pasted:
                continue
            
            _src_dir = Path(_pasted)
            if not (_src_dir / "pyproject.toml").exists():
                print(f"  {clr.R}[ERROR]{clr.RST} No pyproject.toml at {_src_dir}.\n")
                continue
            
            cfg['project_dir'] = str(_src_dir)
            save_config(cfg)
            print(f"  {clr.G}[OK]{clr.RST}  Path saved. You can run {clr.W}aevum update{clr.RST} from anywhere now.\n")
            print(f"  {clr.W}Upgrading Aevum from{clr.RST}  {clr.C}{_src_dir}{clr.RST}\n")
            _run_pip_upgrade(_src_dir)
            continue
        
        if raw.lower() in ('clearpath', 'clear-path'):
            if 'project_dir' in cfg:
                _cleared = cfg['project_dir']
                del cfg['project_dir']
                save_config(cfg)
                print(f"  {clr.G}[OK]{clr.RST}  Saved path cleared: {clr.DIM}{_cleared}{clr.RST}\n")
            else:
                print(f"  {clr.DIM}No saved path to clear.{clr.RST}\n")
            continue

        if raw.lower() == 'scan':
            print(f"\n  {clr.DIM}Enter a folder path or YouTube URL to scan.{clr.RST}\n"); continue

        if _is_url(raw):
            url_prog = _make_progress_bar()
            try:
                total_sec, total_count, entries, label, cache_hits = scan_url(raw, url_prog, use_cache=use_cache)
            except KeyboardInterrupt:
                print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n"); continue
            api_fetched = total_count - cache_hits
            yt_info = f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}" if api_fetched > 0 else f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}"
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}".ljust(100))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=default_top)
            last_scan = {"folder": raw, "total_sec": total_sec, "total_count": total_count,
                         "tree": None, "durations": {e['title']: e['duration'] for e in entries},
                         "sizes": {}, "dupe_groups": [], "is_url": True,
                         "entries": entries, "label": label}
            print_post_scan_menu(current_sort); continue

        raw    = _resolve_alias(raw, cfg)
        folder = Path(raw)
        if not folder.exists():
            print(f"\n  {clr.R}[ERROR]{clr.RST} Path not found: {raw}\n"); continue
        if not folder.is_dir():
            print(f"\n  {clr.R}[ERROR]{clr.RST} That is a file, not a folder.\n"); continue
        if not check_ffprobe():
            print(f"\n  {clr.R}[ERROR]{clr.RST} ffprobe not found. Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}\n"); continue

        print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, current_sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n"); continue

        probed     = total_count - hits
        cache_info = f"  {clr.W}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} files found.{clr.RST}{cache_info}".ljust(100))
        print_results(folder, total_sec, total_count, tree, durations, sizes, default_top, show_files=False)
        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups, folder)
        last_scan = {"folder": folder, "total_sec": total_sec, "total_count": total_count,
                     "tree": tree, "durations": durations, "sizes": sizes,
                     "dupe_groups": groups, "is_url": False}
        print_post_scan_menu(current_sort)

        while True:
            try:
                choice = input(f"  {clr.C}aevum{clr.RST}> ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n  {clr.G}Goodbye!{clr.RST}\n"); sys.exit(EX.OK)

            _menu_map = {'1': 'scan', '2': 'sort', '3': 'export', '4': 'clear', '5': 'quit', '6': 'duplicates'}
            if choice in _menu_map:
                choice = _menu_map[choice]

            _all_cmds  = ['scan', 'clear', 'export', 'sort', 'quit', 'exit', 'q', 'duplicates', 'dupes']
            first_word = choice.split()[0] if choice else ''

            if choice in ('quit', 'exit', 'q'):
                print(f"\n  {clr.G}Goodbye!{clr.RST}\n"); sys.exit(EX.OK)
            elif choice == 'clear':
                clear(); print_banner(); break
            elif choice == 'scan':
                break
            elif first_word == 'sort' or choice == 'sort':
                if last_scan.get("is_url"):
                    print(f"  {clr.Y}Sort is not available for URL scans.{clr.RST}\n")
                    print_post_scan_menu(current_sort); continue
                parts      = choice.split()
                field      = parts[1] if len(parts) >= 2 else None
                direc      = parts[2] if len(parts) >= 3 else None
                _field_opts = ('name', 'duration', 'count')
                _field_map  = {'1': 'name', '2': 'duration', '3': 'count'}
                while field not in _field_opts:
                    sug  = _fuzzy_suggest(field, list(_field_opts) + list(_field_map.keys())) if field else None
                    hint = f"  {clr.DIM}Did you mean {clr.W}{_field_map.get(sug, sug)}{clr.RST}{clr.DIM}?{clr.RST}" if sug else ""
                    if field is not None:
                        print(f"  {clr.R}Unknown.{clr.RST}{hint}")
                    print(f"  {clr.DIM}Sort by?{clr.RST}  {clr.G}1. name{clr.RST}   {clr.B}2. duration{clr.RST}   {clr.M}3. count{clr.RST}   {clr.DIM}0. back{clr.RST}")
                    try:
                        field = input(f"  {clr.C}aevum{clr.RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print(); field = 'back'
                    if field in _field_map: field = _field_map[field]
                    if field in ('back', '0', ''):
                        print_post_scan_menu(current_sort); field = None; break
                if field is None: continue
                _dir_aliases = {'asc': 'asc', 'ascending': 'asc', 'a': 'asc', '1': 'asc',
                                'desc': 'desc', 'descending': 'desc', 'd': 'desc', '2': 'desc'}
                dir_def      = 'asc' if field == 'name' else 'desc'
                dir_hint_str = (f"{clr.G}1. ascending{clr.RST} (a→z)   {clr.B}2. descending{clr.RST} (z→a)" if field == 'name'
                                else f"{clr.G}1. descending{clr.RST} (high→low)   {clr.B}2. ascending{clr.RST} (low→high)")
                while True:
                    if direc is None:
                        print(f"  {clr.DIM}Direction?{clr.RST}  {dir_hint_str}   {clr.DIM}0. back{clr.RST}  {clr.DIM}[Enter = default]{clr.RST}")
                        try:
                            direc = input(f"  {clr.C}aevum{clr.RST}> ").strip().lower()
                        except (KeyboardInterrupt, EOFError):
                            print(); direc = 'back'
                    if direc in ('back', '0'):
                        print_post_scan_menu(current_sort); direc = None; break
                    if direc == '': direc = dir_def
                    resolved = _dir_aliases.get(direc)
                    if resolved: direc = resolved; break
                    sug  = _fuzzy_suggest(direc, list(_dir_aliases.keys()))
                    hint = f"  {clr.DIM}Did you mean {clr.W}{sug}{clr.RST}{clr.DIM}?{clr.RST}" if sug else ""
                    print(f"  {clr.R}Unknown direction.{clr.RST}{hint}"); direc = None
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
                    print(f"  {clr.Y}Export is not available for URL scans yet.{clr.RST}\n")
                    print_post_scan_menu(current_sort); continue
                parts   = choice.split()
                fmt     = parts[1] if len(parts) >= 2 else None
                _fmt_opts = ('txt', 'csv', 'json')
                _fmt_map  = {'1': 'txt', '2': 'csv', '3': 'json'}
                while fmt not in _fmt_opts:
                    sug  = _fuzzy_suggest(fmt, list(_fmt_opts) + list(_fmt_map.keys())) if fmt else None
                    hint = f"  {clr.DIM}Did you mean {clr.W}{_fmt_map.get(sug, sug)}{clr.RST}{clr.DIM}?{clr.RST}" if sug else ""
                    if fmt is not None: print(f"  {clr.R}Unknown format.{clr.RST}{hint}")
                    print(f"  {clr.DIM}Export as?{clr.RST}  {clr.G}1. txt{clr.RST}   {clr.B}2. csv{clr.RST}   {clr.M}3. json{clr.RST}   {clr.DIM}0. back{clr.RST}")
                    try:
                        fmt = input(f"  {clr.C}aevum{clr.RST}> ").strip().lower()
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
                    print(f"\n  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
                except Exception as e:
                    print(f"\n  {clr.R}Export failed:{clr.RST} {e}\n")
            elif choice in ('duplicates', 'dupes'):
                if last_scan.get("is_url"):
                    print(f"  {clr.Y}Duplicate detection is not available for URL scans.{clr.RST}\n")
                    print_post_scan_menu(current_sort); continue
                print_duplicates(last_scan["dupe_groups"], last_scan["durations"])
                print_post_scan_menu(current_sort)
            else:
                sug = _fuzzy_suggest(first_word, _all_cmds) if first_word else None
                if sug:
                    print(f"  {clr.R}Unknown command.{clr.RST}  {clr.DIM}Did you mean{clr.RST}  {clr.W}{sug}{clr.RST}{clr.DIM}?{clr.RST}")
                else:
                    print(f"  {clr.R}Invalid command.{clr.RST} Type  {clr.G}1. scan{clr.RST}   {clr.B}2. sort{clr.RST}   {clr.M}3. export{clr.RST}   {clr.Y}4. clear{clr.RST}   {clr.R}5. quit{clr.RST}   {clr.C}6. duplicates{clr.RST}")
                print_post_scan_menu(current_sort)
