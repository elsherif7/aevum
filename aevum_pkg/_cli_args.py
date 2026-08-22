"""
Argument parsing for the Aevum CLI.

Only the 'scan' subcommand remains — every other subcommand (compare,
dupes, export, watch, history/diff, alias, cache, config, doctor,
quota, update, clearpath, appdata, files, stats, summary, top, recent,
version) was removed to keep this a minimal folder/YouTube duration
scanner.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from ._color import clr
from ._exit import EX

SUBCOMMANDS = ('scan',)


def _print_global_help(version: str) -> None:
    print(f"""
  {clr.C}aevum {version}{clr.RST}  {clr.DIM}--{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}Usage{clr.RST}
    aevum scan <path|url>           Scan a folder or YouTube URL
    aevum <path|url>                Same thing (shorthand for 'aevum scan')

  {clr.W}Global Options{clr.RST}
    --no-color                      Disable ANSI color output
    --json                          Machine-readable JSON output to stdout
    -q, --quiet                     Suppress decorative output (errors -> stderr only)
    -h, --help                      Show this help
    -V, --version                   Show version

  {clr.W}Exit Codes{clr.RST}
    0  success          1  bad arguments / path not found
    2  missing ffprobe  3  scan error / interrupted
    5  YouTube API error
""")


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    """Attach --no-color / --json / --quiet to any subcommand parser."""
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--json",     action="store_true", help="Output JSON to stdout")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all decorative output; only errors go to stderr")


# Flags that consume the next token as their value.
_VALUE_FLAGS = {
    '--speed', '-s', '--sort', '-t', '--top', '--depth',
    '--min-duration', '--max-duration', '--ext', '--folder',
}


def _parse_args(version: str) -> Any:
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

    # If no command word was given (e.g. aevum D:\path or aevum some/url),
    # find the first non-flag token to use as the command or path. Flags
    # and their values are collected separately.
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
        # No command word — treat the whole thing as a shorthand scan.
        argv = ['scan'] + argv
        subcommand = 'scan'

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub: str, argv: list[str]) -> Any:
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
    p.add_argument("--depth",  type=int, default=None, metavar="N")
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
