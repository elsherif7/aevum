"""
Aevum CLI entry point: argument parsing and dispatch, in one file.

'scan' takes exactly one target (a folder path or a YouTube URL) and no
flags whatsoever — this is a "point it at a target, get the result"
tool. cmd_scan (the only command) lives in _cli_cmds.py.
"""
from __future__ import annotations

import sys

from aevum_pkg import __version__

from ._cli_cmds import cmd_scan
from ._color import clr
from ._exit import EX


def _print_help() -> None:
    print(f"""
  {clr.C}aevum {__version__}{clr.RST}  {clr.DIM}--{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}Usage{clr.RST}
    aevum scan <path|url>           Scan a folder or YouTube URL
    aevum <path|url>                Same thing (shorthand for 'aevum scan')

  {clr.W}Other{clr.RST}
    -h, --help                      Show this help
    -V, --version                   Show version

  {clr.W}Exit Codes{clr.RST}
    0  success          1  bad arguments / path not found
    2  missing ffprobe  3  scan error / interrupted
    5  YouTube API error
""")


def _parse_target() -> str:
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
        _print_help()
        sys.exit(EX.OK)
    if argv[0] in ('-h', '--help'):
        _print_help()
        sys.exit(EX.OK)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(EX.OK)

    # Optional leading 'scan' word — allowed but not required.
    tokens = argv[1:] if argv[0] == 'scan' else argv
    if not tokens:
        print(f"\n  {clr.R}[ERROR]{clr.RST} No target specified. Usage: aevum scan <path|url>\n",
              file=sys.stderr)
        sys.exit(EX.ERR_ARGS)

    # Join remaining tokens so an unquoted path with spaces still works
    # as a single target (no batch mode — this is exactly one target).
    return ' '.join(tokens).strip().strip("'\"")


def main():
    target = _parse_target()
    cmd_scan(target)
