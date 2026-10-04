"""
Argument parsing and dispatch.

'scan' takes exactly one target (a folder or a YouTube URL) and no flags.
The word 'scan' is required, and a path containing spaces must be quoted.
"""
from __future__ import annotations

import os
import sys
from typing import NoReturn

from aevum_pkg import __version__

from ._cli_cmds import cmd_scan
from ._color import clr, eclr
from ._exit import EX
from ._text import _safe

_NO_TARGET = "No target specified. Usage: aevum scan <path|url>"


def _print_help() -> None:
    print(f"""
  {clr.C}aevum {__version__}{clr.RST}  {clr.DIM}--{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}Usage{clr.RST}
    aevum scan <path|url>           Scan a folder or YouTube URL
    aevum scan "path with spaces"   Quote paths that contain spaces

  {clr.W}Other{clr.RST}
    -h, --help                      Show this help
    -V, --version                   Show version

  {clr.W}Exit Codes{clr.RST}
    0  success          1  bad arguments / path not found
    2  missing ffprobe  3  scan error / interrupted
    5  YouTube API error
""")


def _error(message: str) -> NoReturn:
    print(f"\n  {eclr.R}[ERROR]{eclr.RST} {message}\n", file=sys.stderr)
    sys.exit(EX.ERR_ARGS)


def _exit_on_info_flag(token: str) -> None:
    if token in ('-h', '--help'):
        _print_help()
        sys.exit(EX.OK)
    if token in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(EX.OK)


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
    _exit_on_info_flag(argv[0])

    if argv[0] != 'scan':
        _error("Missing 'scan' command. Usage: aevum scan <path|url>")

    tokens = argv[1:]
    if not tokens:
        _error(_NO_TARGET)
    _exit_on_info_flag(tokens[0])
    # there are no options, so a leading dash is only valid if it names a real path
    if tokens[0].startswith('-') and not os.path.exists(tokens[0]):
        _error(f"Unknown option: {_safe(tokens[0])}. "
               f"'aevum scan' takes no options, only a path or URL.")
    if len(tokens) > 1:
        _error("Too many arguments. "
               "If your path contains spaces, wrap it in quotes: aevum scan \"my path\"")

    target = tokens[0].strip().strip("'\"").strip()
    if not target:
        _error(_NO_TARGET)
    return target


def _harden_streams() -> None:
    """
    Never crash while printing: a name the console encoding can't represent would
    otherwise raise UnicodeEncodeError after the scan has finished.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main():
    _harden_streams()
    target = _parse_target()
    cmd_scan(target)
