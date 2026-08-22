"""
CLI entry point: argument parsing, subcommand dispatch, and main().
All business logic lives in the other modules.

  _cli_json.py    — JSON serialisers
  _cli_helpers.py — small stateless helpers
  _cli_args.py    — argparse definitions and subcommand dispatch
  _cli_cmds.py    — the scan command
  _cli.py         — this file: main() only

Only 'scan' remains as a subcommand — the config file/system was
removed along with every other subcommand, so there is no persisted
config to load; cfg is always an empty dict and every setting falls
back to its built-in default.
"""
import sys

from aevum_pkg import __version__

from ._cli_args import _parse_args
from ._cli_cmds import cmd_scan
from ._color import _disable_color
from ._exit import EX


def main():
    args = _parse_args(__version__)
    cfg  = {}

    use_json = getattr(args, 'json', False)
    quiet    = getattr(args, 'quiet', False) or use_json

    if getattr(args, 'no_color', False) or use_json:
        _disable_color()

    cmd = args.command

    _DISPATCH = {
        'scan': lambda: cmd_scan(args, cfg, use_json, quiet),
    }

    handler = _DISPATCH.get(cmd)
    if handler:
        handler()
    else:
        print(f"\n  Unknown command: {cmd}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
