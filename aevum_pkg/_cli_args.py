"""
Argument parsing and subcommand dispatch for the Aevum CLI.
"""
import argparse
import sys
import types
from pathlib import Path

from ._color   import clr
from ._config  import load_config
from ._exit    import EX
from ._display import _fuzzy_suggest
from ._cli_helpers import _expand_aliases_in_argv


def _split_two_paths(parts):
    """
    Given a list of tokens that represent two paths (possibly with spaces),
    find the best split point by trying each position and checking which
    candidate paths exist on disk. Falls back to splitting at the midpoint.
    """
    if not parts:
        return '', ''
    for i in range(1, len(parts)):
        a = ' '.join(parts[:i])
        b = ' '.join(parts[i:])
        if Path(a).exists() and Path(b).exists():
            return a, b
    mid = len(parts) // 2
    return ' '.join(parts[:mid]), ' '.join(parts[mid:])


def _join_path_tokens(parts):
    """
    Given a list of tokens that may represent a single path with spaces,
    find the longest prefix that exists on disk. Falls back to joining all.
    Tries progressively longer joins so 'D:\\foo bar baz' is found even if
    'D:\\foo' also exists.
    """
    if not parts:
        return ''
    best = ' '.join(parts)
    if Path(best).exists():
        return best
    for i in range(len(parts), 0, -1):
        candidate = ' '.join(parts[:i])
        if Path(candidate).exists():
            return candidate
    return best

# Imported lazily in _parse_args to avoid circular imports at module level.
# __version__ is imported in _cli.py and passed through _print_global_help.


def _print_global_help(version):
    print(f"""
  {clr.C}aevum {version}{clr.RST}  {clr.DIM}--{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}Usage{clr.RST}
    aevum [command] [options]
    aevum <path|url>                Quick scan (shorthand for 'aevum scan')

  {clr.W}Commands{clr.RST}
    scan      <path|url>            Scan a folder or YouTube URL
    compare   <path> <path>         Compare two libraries side-by-side
    dupes     <path>                Find duplicate files (by size + hash)
    export    <path|url> <format>   Scan and write results to a file
    watch     <path>                Re-scan automatically when folder changes
    files     <path>                Scan and show all files under each folder
    stats     <path>                Deep statistics: avg, median, formats, sizes
    summary   <path>                One-line summary of a folder
    history   <path>                Show past scan snapshots for a folder
    diff      <path>                Show what changed since the last scan
    recent    <path>                Show recently added or modified files
    top       <path>                Show top N files by duration or size
    alias                           Manage aliases (paths, flags, or any shorthand)
    cache                           Manage the duration cache
    config                          Read/write configuration
    quota                           Check YouTube API quota usage
    doctor                          Check environment (ffprobe, API key, cache)
    update                          Upgrade Aevum to the latest version
    clearpath                       Clear saved project path for updates
    appdata                         Open the Aevum data folder
    version                         Print version and exit

  {clr.W}Scan flags{clr.RST}  {clr.DIM}(aevum scan / aevum files / aevum watch){clr.RST}
    -s, --sort FIELD[:DIR]          Sort: name, duration, count  (e.g. duration:desc)
    -t, --top N                     Show top N longest files (default 10, 0 to hide)
    -f, --files                     Show individual files in the tree  {clr.DIM}(scan only){clr.RST}
    -o, --out FILE                  Write results to FILE  {clr.DIM}(scan only){clr.RST}
    --format txt|csv|json|html      Explicit export format  {clr.DIM}(scan only){clr.RST}
    --depth N                       Limit tree to N levels deep  {clr.DIM}(scan only){clr.RST}
    --merge                         Combine multiple targets into one total  {clr.DIM}(scan only){clr.RST}
    --min-duration DURATION         Skip files shorter than this (30s, 5m, 1h, 1:30:00)
    --max-duration DURATION         Skip files longer than this
    --ext EXT[,EXT]                 Only include these extensions (mkv,mp4)
    --folder PATTERN                Only include folders matching this glob
    --exclude PATTERN[,PATTERN]     Exclude folders by name (trailers,samples)
    --since DATE                    Only files modified after this (7d, 30d, 2w, 2025-01-15)
    --until DATE                    Only files modified before this
    --speed SPEED                   Add custom playback speed to breakdown (repeatable)
    --no-cache                      Bypass cache, re-probe every file

  {clr.W}Watch-only flags{clr.RST}
    -i, --interval SECONDS          Poll interval in seconds (default 5)
    --no-clear                      Don't clear screen between updates

  {clr.W}Recent flags{clr.RST}
    --since DATE                    Show files modified after this (default: 30d)
    --limit N                       Max files to show (default: 50)

  {clr.W}Alias subcommands{clr.RST}
    aevum alias list                List all aliases with type labels
    aevum alias set <name> <value>  Create a new alias (path, flag, or command)
    aevum alias remove <name>       Remove an alias  (also: alias rm <name>)

  {clr.W}Cache subcommands{clr.RST}
    aevum cache list                List all cache files and sizes
    aevum cache clear               Delete all cache files
    aevum cache clear <path>        Delete cache for one specific folder
    aevum cache path                Print the cache directory path

  {clr.W}Config subcommands{clr.RST}
    aevum config list               Show all config keys and values
    aevum config get <key>          Print one config value
    aevum config set <key> <value>  Set a config value
    aevum config reset              Reset all config to defaults
    {clr.DIM}Keys: sort  top  no_color  cache_enabled  export_dir  yt_api_key{clr.RST}

  {clr.W}Update flags{clr.RST}
    --dry-run                       Show what would run without actually upgrading

  {clr.W}Global Options{clr.RST}
    --no-color                      Disable ANSI color output
    --json                          Machine-readable JSON output to stdout
    -q, --quiet                     Suppress decorative output (errors -> stderr only)
    -h, --help                      Show this help
    -V, --version                   Show version
    -U, --upgrade                   Alias for 'aevum update'

  {clr.W}Exit Codes{clr.RST}
    0  success          1  bad arguments / path not found
    2  missing ffprobe  3  scan error / interrupted
    4  export failed    5  YouTube API error

  {clr.DIM}Run 'aevum <command> --help' for full options on any command.{clr.RST}
""")


def _add_common_flags(p):
    """Attach --no-color / --json / --quiet to any subcommand parser."""
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--json",     action="store_true", help="Output JSON to stdout")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all decorative output; only errors go to stderr")


# Flags that consume the next token as their value — used in two places below.
_VALUE_FLAGS = {
    '--speed', '-s', '--sort', '-t', '--top', '-o', '--out',
    '--format', '--depth', '-i', '--interval',
    '--min-duration', '--max-duration', '--ext', '--folder',
}

SUBCOMMANDS = (
    'scan', 'compare', 'dupes', 'export', 'watch', 'cache', 'config',
    'alias', 'doctor', 'quota', 'version', 'update', 'clearpath',
    'appdata', 'files', 'stats', 'summary', 'history', 'diff', 'recent', 'top',
)


def _parse_args(version):
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

    if not argv:
        _print_global_help(version)
        sys.exit(EX.OK)

    if argv[0] in ('-h', '--help'):
        _print_global_help(version)
        sys.exit(EX.OK)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {version}")
        sys.exit(EX.OK)
    if argv[0] in ('-U', '--upgrade'):
        argv = ['update'] + argv[1:]

    # Expand aliases early so every subcommand benefits.
    # We load config here just for alias expansion; main() reloads it normally.
    try:
        _early_cfg = load_config()
        # Never expand aliases when the user is managing aliases themselves —
        # expansion would corrupt the name/value tokens (e.g. 'man' → its path).
        if argv[0] != 'alias':
            argv = _expand_aliases_in_argv(argv, _early_cfg)
    except Exception:
        pass  # never let alias expansion crash startup

    # If no command word was given (e.g. aevum --speed 0.5 D:\path or
    # aevum D:\path) find the first non-flag token to use as the command
    # or path.  Flags and their values are collected separately.
    flags_before = []
    first_pos    = None
    _j = 0
    while _j < len(argv):
        tok = argv[_j]
        if tok.startswith('-'):
            flags_before.append(tok)
            bare = tok.split('=')[0]
            if '=' not in tok and bare in _VALUE_FLAGS and _j + 1 < len(argv):
                _j += 1
                flags_before.append(argv[_j])
        else:
            first_pos = _j
            break
        _j += 1

    if first_pos is None:
        _print_global_help(version)
        sys.exit(EX.OK)

    # Rebuild argv with flags moved after the first positional token
    if flags_before:
        argv = argv[first_pos:first_pos+1] + flags_before + argv[first_pos+1:]

    subcommand = argv[0]

    if subcommand not in SUBCOMMANDS:
        suggestion = _fuzzy_suggest(subcommand, list(SUBCOMMANDS))
        if (subcommand.startswith(('/', '\\', '.')) or
                ':' in subcommand or
                subcommand.startswith(('http://', 'https://', 'www.'))):
            # No command word — treat as shorthand scan.
            flags      = []
            path_parts = []
            _k = 0
            while _k < len(argv):
                t = argv[_k]
                if t.startswith('-'):
                    flags.append(t)
                    bare = t.split('=')[0]
                    if '=' not in t and bare in _VALUE_FLAGS and _k + 1 < len(argv):
                        _k += 1
                        flags.append(argv[_k])
                else:
                    path_parts.append(t)
                _k += 1
            if len(path_parts) == 1:
                argv = ['scan', path_parts[0]] + flags
            else:
                argv = ['scan'] + path_parts + flags
            subcommand = 'scan'
        elif suggestion:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  "
                  f"{clr.DIM}Did you mean{clr.RST}  {clr.W}{suggestion}{clr.RST}{clr.DIM}?{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)
        else:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  "
                  f"{clr.DIM}Run{clr.RST}  {clr.W}aevum --help{clr.RST}  "
                  f"{clr.DIM}for a list of commands.{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub, argv):
    if sub == 'files':
        p = argparse.ArgumentParser(prog="aevum files",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder and show every video file listed under its folder.",
            epilog="Examples:\n  aevum files D:\\Movies\n  aevum files D:\\Movies --sort name\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--exclude", default=None, metavar="PATTERN[,PATTERN]",
                       help="Exclude folders matching these patterns, comma-separated (e.g. trailers,samples)")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

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
        p.add_argument("--format", dest="fmt", choices=["txt", "csv", "json", "html"], default=None)
        p.add_argument("--depth",  type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--merge", action="store_true",
                       help="Aggregate all targets into one combined grand total")
        p.add_argument("--min-duration", default=None, metavar="DURATION")
        p.add_argument("--max-duration", default=None, metavar="DURATION")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN")
        p.add_argument("--exclude", default=None, metavar="PATTERN[,PATTERN]",
                       help="Exclude folders matching these patterns, comma-separated (e.g. trailers,samples)")
        p.add_argument("--since", default=None, metavar="DATE",
                       help="Only include files modified after this date (e.g. 7d, 30d, 2w, 2025-01-15)")
        p.add_argument("--until", default=None, metavar="DATE",
                       help="Only include files modified before this date")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'watch':
        p = argparse.ArgumentParser(prog="aevum watch",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Re-scan a folder automatically whenever its contents change.",
            epilog=("Examples:\n"
                    "  aevum watch D:\\Movies\n"
                    "  aevum watch D:\\Movies --interval 10\n"
                    "  aevum watch D:\\Downloads --ext mkv,mp4 --min-duration 5m\n"))
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("-i", "--interval", type=float, default=5.0, metavar="SECONDS")
        p.add_argument("--no-clear", action="store_true")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("--min-duration", default=None, metavar="DURATION")
        p.add_argument("--max-duration", default=None, metavar="DURATION")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN")
        p.add_argument("--exclude", default=None, metavar="PATTERN[,PATTERN]",
                       help="Exclude folders matching these patterns, comma-separated")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'compare':
        p = argparse.ArgumentParser(prog="aevum compare",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Compare the duration totals of two local libraries.",
            epilog="Examples:\n  aevum compare D:\\Movies E:\\Movies-Backup\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        folder_a, folder_b = _split_two_paths(args.folder_parts)
        args.folder_a = folder_a
        args.folder_b = folder_b
        args.command  = sub
        return args

    if sub == 'dupes':
        p = argparse.ArgumentParser(prog="aevum dupes",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Find duplicate files (by size + partial hash) in a folder.",
            epilog="Examples:\n  aevum dupes D:\\Movies\n  aevum dupes D:\\Movies --json\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("-o", "--out", default=None, metavar="FILE")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'export':
        p = argparse.ArgumentParser(prog="aevum export",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder or URL and write results directly to a file.",
            epilog="Examples:\n  aevum export D:\\Movies csv\n  aevum export D:\\Movies json -o library.json\n")
        p.add_argument("target", metavar="PATH|URL")
        p.add_argument("format", choices=["txt", "csv", "json", "html"])
        p.add_argument("-o", "--out",  default=None, metavar="FILE")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'cache':
        p = argparse.ArgumentParser(prog="aevum cache",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage the duration cache.",
            epilog=("Subcommands:\n"
                    "  list          List all cache files\n"
                    "  clear         Delete all cache files\n"
                    "  clear <path>  Delete cache for a specific folder\n"
                    "  path          Print the cache directory path\n"))
        p.add_argument("action", nargs="?", default="list", choices=["list", "clear", "path"])
        p.add_argument("folder", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'config':
        p = argparse.ArgumentParser(prog="aevum config",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Read and write persistent configuration.",
            epilog=("Keys:  sort  top  no_color  cache_enabled  export_dir  yt_api_key\n\n"
                    "Examples:\n"
                    "  aevum config list\n"
                    "  aevum config set sort duration:desc\n"
                    "  aevum config set yt_api_key AIzaSy...\n"))
        p.add_argument("action", choices=["get", "set", "list", "reset"])
        p.add_argument("key",   nargs="?", default=None)
        p.add_argument("value", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'update':
        p = argparse.ArgumentParser(prog="aevum update",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Upgrade Aevum to the latest version via pip.",
            epilog="Examples:\n  aevum update\n  aevum -U\n")
        p.add_argument("--dry-run", action="store_true",
                       help="Show what would run without actually upgrading")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'alias':
        # Manual parse: argparse chokes on flag-like values (--speed) and
        # alias expansion must be skipped to avoid corrupting name tokens.
        no_color = '--no-color' in argv
        tokens   = [t for t in argv if t != '--no-color']
        ACTIONS  = ('list', 'set', 'remove', 'rm')
        action   = 'list'
        name     = None
        path_val = None
        if tokens:
            if tokens[0] in ACTIONS:
                action = tokens[0]
                if len(tokens) >= 2:
                    name = tokens[1]
                if len(tokens) >= 3:
                    path_val = ' '.join(tokens[2:])
        return types.SimpleNamespace(
            command='alias', action=action, name=name, path=path_val,
            no_color=no_color, json=False, quiet=False,
        )

    if sub == 'doctor':
        p = argparse.ArgumentParser(prog="aevum doctor",
            description="Check environment: ffprobe, API key, cache, Python version.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'quota':
        p = argparse.ArgumentParser(prog="aevum quota",
            description="Check YouTube API quota usage for today.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'version':
        # handled before dispatch in _parse_args, but kept for completeness
        return types.SimpleNamespace(command='version', no_color=False, json=False, quiet=False)

    if sub == 'appdata':
        return types.SimpleNamespace(command='appdata', no_color=False, json=False, quiet=False)

    if sub == 'clearpath':
        p = argparse.ArgumentParser(prog="aevum clearpath",
            description="Clear the saved Aevum project path used by 'aevum update'.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'top':
        p = argparse.ArgumentParser(prog="aevum top",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Show top N files by duration or size.",
            epilog=("Examples:\n"
                    "  aevum top D:\\Movies\n"
                    "  aevum top D:\\Movies --by size\n"
                    "  aevum top D:\\Movies --limit 50 --by size\n"))
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("--by", choices=["duration", "size"], default="duration",
                       help="Sort by duration (default) or size")
        p.add_argument("--limit", type=int, default=20, metavar="N",
                       help="Number of files to show (default: 20)")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'recent':
        p = argparse.ArgumentParser(prog="aevum recent",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Show files added or modified within a recent time window.",
            epilog=("Examples:\n"
                    "  aevum recent D:\\Movies\n"
                    "  aevum recent D:\\Movies --since 7d\n"
                    "  aevum recent D:\\Movies --since 2025-01-01 --limit 100\n"))
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("--since", default="30d", metavar="DATE",
                       help="Show files modified after this date (default: 30d)")
        p.add_argument("--limit", type=int, default=50, metavar="N",
                       help="Max files to show (default: 50)")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'history':
        p = argparse.ArgumentParser(prog="aevum history",
            description="Show past scan snapshots for a folder.")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'diff':
        p = argparse.ArgumentParser(prog="aevum diff",
            description="Show what changed between the last two scans of a folder.")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'stats':
        p = argparse.ArgumentParser(prog="aevum stats",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Show deep statistics for a media library.",
            epilog="Examples:\n  aevum stats D:\\Movies\n  aevum stats D:\\Movies --json\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--exclude", default=None, metavar="PATTERN[,PATTERN]",
                       help="Exclude folders matching these patterns, comma-separated")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    if sub == 'summary':
        p = argparse.ArgumentParser(prog="aevum summary",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Print a single-line summary of a media folder.",
            epilog="Examples:\n  aevum summary D:\\Movies\n  aevum summary D:\\Movies --json\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--exclude", default=None, metavar="PATTERN[,PATTERN]",
                       help="Exclude folders matching these patterns, comma-separated")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = _join_path_tokens(args.folder_parts)
        args.command = sub
        return args

    _print_global_help("?")
    sys.exit(EX.ERR_ARGS)
