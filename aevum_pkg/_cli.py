"""
CLI entry point.

  _cli_args.py    — argument parsing (single target, no flags)
  _cli_cmds.py    — cmd_scan, the only command
  _cli_helpers.py — small stateless helpers
  _cli.py         — this file: main() only
"""
from aevum_pkg import __version__

from ._cli_args import _parse_args
from ._cli_cmds import cmd_scan


def main():
    args = _parse_args(__version__)
    cmd_scan(args)
