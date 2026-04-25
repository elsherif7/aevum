"""
CLI entry point: argument parsing, subcommand dispatch, and main().
All business logic lives in the other modules.
"""
import argparse
import sys
import types
from datetime import datetime
from pathlib import Path

from ._color   import R, G, Y, B, M, C, W, DIM, RST, LINE, clear, _disable_color
from ._scan    import check_ffprobe, format_duration, format_size, _run_scan
from ._youtube import _is_url, scan_url, _make_url_progress
from ._display import (print_results, print_url_results, print_banner,
                       print_post_scan_menu, _fuzzy_suggest)
from ._dupes   import find_duplicates, print_duplicates, print_dupe_warning
from ._compare import run_compare, print_comparison
from ._export  import export_results
from ._config  import (CONFIG_DEFAULTS, load_config, save_config,
                       cmd_doctor, cmd_cache, cmd_config, repl_config)
from ._youtube import load_api_key, prompt_api_key

__version__ = "1.0.0"


# ── HELPERS ───────────────────────────────────────────────────────────

def _make_progress_bar():
    def on_progress(done, total):
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "█" * filled + "░" * (24 - filled)
        print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
              end='', flush=True)
    return on_progress


def _require_ffprobe(context=""):
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        print(f"\n  {R}[ERROR]{RST} ffprobe not found on PATH{ctx}.")
        print(f"  {DIM}ffprobe is required for local folder scanning.{RST}")
        print(f"  Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}")
        print(f"  Then re-run:    {W}aevum doctor{RST}\n")
        sys.exit(2)


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
    {G}cache{RST}                           Manage the duration cache
    {G}config{RST}                          Read/write configuration
    {G}doctor{RST}                          Check environment (ffprobe, API key, cache)
    {G}version{RST}                         Print version and exit

  {W}GLOBAL OPTIONS{RST}
    --no-color                      Disable ANSI color output
    -h, --help                      Show this help
    -V, --version                   Show version

  {W}EXAMPLES{RST}
    aevum scan D:\\Movies
    aevum scan D:\\Movies --sort duration --top 20
    aevum scan https://youtube.com/@mkbhd
    aevum export D:\\Movies json -o library.json
    aevum dupes D:\\Movies
    aevum compare D:\\Movies E:\\Backup
    aevum config set sort duration:desc
    aevum doctor

  {DIM}Run 'aevum <command> --help' for command-specific options.{RST}
""")


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
        sys.exit(0)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(0)

    subcommand = argv[0]
    SUBCOMMANDS = ('scan', 'compare', 'dupes', 'export', 'cache', 'config', 'doctor', 'version', 'shell')

    if subcommand not in SUBCOMMANDS:
        # Check if it looks like a typo of a real subcommand — git-style suggestion
        from ._display import _fuzzy_suggest
        suggestion = _fuzzy_suggest(subcommand, list(SUBCOMMANDS))
        # If it looks like a path or URL, treat as implicit scan
        if (subcommand.startswith(('/', '\\', '.')) or
                ':' in subcommand or
                subcommand.startswith(('http://', 'https://', 'www.'))):
            argv       = ['scan'] + argv
            subcommand = 'scan'
        elif suggestion:
            print(f"\n  {R}aevum: '{subcommand}' is not a command.{RST}  {DIM}Did you mean{RST}  {W}{suggestion}{RST}{DIM}?{RST}\n")
            sys.exit(1)
        else:
            print(f"\n  {R}aevum: '{subcommand}' is not a command.{RST}  {DIM}Run{RST}  {W}aevum --help{RST}  {DIM}for a list of commands.{RST}\n")
            sys.exit(1)

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub, argv):
    if sub == 'scan':
        p = argparse.ArgumentParser(prog="aevum scan",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a local folder or YouTube URL and report total duration.",
            epilog=("Examples:\n"
                    "  aevum scan D:\\Movies\n"
                    "  aevum scan D:\\Movies --sort duration --top 20\n"
                    "  aevum scan D:\\Movies --files --out report.csv\n"
                    "  aevum scan https://youtube.com/@mkbhd\n"))
        p.add_argument("target", nargs="?", default=None, metavar="PATH|URL",
                       help="local folder path or YouTube URL")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("-f", "--files", action="store_true")
        p.add_argument("-o", "--out",   default=None, metavar="FILE")
        p.add_argument("--format", dest="fmt", choices=["txt","csv","json"], default=None)
        p.add_argument("--depth",  type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'compare':
        p = argparse.ArgumentParser(prog="aevum compare",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Compare the duration totals of two local libraries.",
            epilog=("Examples:\n"
                    "  aevum compare D:\\Movies E:\\Movies-Backup\n"))
        p.add_argument("folder_a"); p.add_argument("folder_b")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'dupes':
        p = argparse.ArgumentParser(prog="aevum dupes",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Find duplicate files (by size + partial hash) in a folder.",
            epilog=("Examples:\n"
                    "  aevum dupes D:\\Movies\n"
                    "  aevum dupes D:\\Movies -o dupes.txt\n"))
        p.add_argument("folder")
        p.add_argument("-o", "--out", default=None, metavar="FILE")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
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
        p.add_argument("--no-color", action="store_true")
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
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv); args.command = sub; return args

    if sub == 'version':
        print(f"aevum {__version__}"); sys.exit(0)

    if sub == 'shell':
        ns = types.SimpleNamespace(command='shell', no_color=False, sort=None, top=None)
        for a in argv:
            if a == '--no-color': ns.no_color = True
        return ns

    _print_global_help(); sys.exit(1)


# ── MAIN ──────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    cfg  = load_config()

    if getattr(args, 'no_color', False) or cfg.get('no_color'):
        _disable_color()

    cmd = args.command

    if cmd == 'version':
        print(f"aevum {__version__}"); sys.exit(0)

    if cmd == 'doctor':
        cmd_doctor(cfg); sys.exit(0)

    if cmd == 'config':
        cmd_config(args, cfg); sys.exit(0)

    if cmd == 'cache':
        cmd_cache(args); sys.exit(0)

    if cmd == 'compare':
        folder_a = Path(args.folder_a.strip().strip("'\""))
        folder_b = Path(args.folder_b.strip().strip("'\""))
        for f in (folder_a, folder_b):
            if not f.exists() or not f.is_dir():
                print(f"\n  {R}[ERROR]{RST} Not a valid folder: {f}\n", file=sys.stderr)
                sys.exit(1)
        _require_ffprobe("compare")
        sort    = _resolve_sort(args, cfg)
        on_prog = _make_progress_bar()
        data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort, not args.no_cache)
        print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(0)

    if cmd == 'dupes':
        folder = Path(args.folder.strip().strip("'\""))
        if not folder.exists() or not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(1)
        _require_ffprobe("dupes")
        on_prog   = _make_progress_bar()
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", use_cache)
        probed     = len(durations) - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{len(durations)}{RST} files found.{cache_info}".ljust(60))
        groups = find_duplicates(durations, sizes)
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
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{args.out}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr); sys.exit(4)
        sys.exit(0)

    if cmd == 'export':
        raw       = args.target.strip().strip("'\"")
        sort      = _resolve_sort(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path  = args.out or None
        fmt       = args.format
        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n"); sys.exit(0)
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
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr); sys.exit(4)
            sys.exit(0)
        folder = Path(raw)
        if not folder.exists() or not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr); sys.exit(1)
        _require_ffprobe("export")
        on_prog = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(folder, on_prog, sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(0)
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} files found.".ljust(60))
        try:
            dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
            print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
        except Exception as e:
            print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr); sys.exit(4)
        sys.exit(0)

    if cmd == 'scan' and getattr(args, 'target', None) is not None:
        raw       = args.target.strip().strip("'\"")
        sort      = _resolve_sort(args, cfg)
        top       = _resolve_top(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path  = getattr(args, 'out', None)
        fmt       = _resolve_out_format(out_path, getattr(args, 'fmt', None))

        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n"); sys.exit(0)
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=top)
            sys.exit(0)

        folder = Path(raw)
        if not folder.exists():
            print(f"\n  {R}[ERROR]{RST} Path not found: {folder}", file=sys.stderr)
            try:
                sug = _fuzzy_suggest(folder.name, [p.name for p in folder.parent.iterdir() if p.is_dir()])
                if sug:
                    print(f"  {DIM}Did you mean:{RST}  {W}{folder.parent / sug}{RST}", file=sys.stderr)
            except Exception:
                pass
            print(); sys.exit(1)
        if not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} That is a file, not a folder: {folder}\n", file=sys.stderr); sys.exit(1)
        _require_ffprobe("scan")

        on_progress = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(folder, on_progress, sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n"); sys.exit(0)

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} files found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                      show_files=getattr(args, 'files', False))
        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups)
        if fmt and out_path:
            try:
                dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr); sys.exit(4)
        sys.exit(0)

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
            print(f"\n\n  {G}Goodbye!{RST}\n"); sys.exit(0)

        if not raw:
            continue
        raw = raw.strip().strip("'\"")
        if not raw:
            continue

        _init_map = {'1': 'scan', '2': 'clear', '3': 'quit'}
        if raw in _init_map:
            raw = _init_map[raw]

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {G}Goodbye!{RST}\n"); sys.exit(0)

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
                print(f"\n\n  {G}Goodbye!{RST}\n"); sys.exit(0)

            _menu_map = {'1': 'scan', '2': 'sort', '3': 'export', '4': 'clear', '5': 'quit', '6': 'duplicates'}
            if choice in _menu_map:
                choice = _menu_map[choice]

            _all_cmds  = ['scan', 'clear', 'export', 'sort', 'quit', 'exit', 'q', 'duplicates', 'dupes']
            first_word = choice.split()[0] if choice else ''

            if choice in ('quit', 'exit', 'q'):
                print(f"\n  {G}Goodbye!{RST}\n"); sys.exit(0)
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
