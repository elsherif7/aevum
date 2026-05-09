"""
CLI entry point: argument parsing, subcommand dispatch, and main().
All business logic lives in the other modules.

Split from the original monolithic 2100-line file into:
  _cli_json.py    — JSON serialisers
  _cli_helpers.py — small stateless helpers
  _cli_update.py  — self-update / pip logic
  _cli_args.py    — argparse definitions and subcommand dispatch
  _cli_cmds.py    — one cmd_* function per subcommand
  _cli.py         — this file: main() only
"""
import sys

from ._color   import _disable_color
from ._config  import load_config
from ._exit    import EX
from ._cli_args import _parse_args
from ._cli_cmds import (
    cmd_version, cmd_update, cmd_clearpath, cmd_appdata,
    cmd_alias, cmd_doctor, cmd_config_dispatch, cmd_cache_dispatch,
    cmd_quota, cmd_top, cmd_recent, cmd_history, cmd_diff,
    cmd_stats, cmd_summary, cmd_watch, cmd_compare, cmd_dupes,
    cmd_export, cmd_files, cmd_scan,
)

from aevum_pkg import __version__


def main():
    args = _parse_args(__version__)
    cfg  = load_config()

    use_json = getattr(args, 'json', False)
    quiet    = getattr(args, 'quiet', False) or use_json

    if getattr(args, 'no_color', False) or cfg.get('no_color') or use_json:
        _disable_color()

    cmd = args.command

    _DISPATCH = {
        'version':   lambda: cmd_version(args, cfg, use_json, quiet, __version__),
        'update':    lambda: cmd_update(args, cfg, use_json, quiet),
        'clearpath': lambda: cmd_clearpath(args, cfg, use_json, quiet),
        'appdata':   lambda: cmd_appdata(args, cfg, use_json, quiet),
        'alias':     lambda: cmd_alias(args, cfg, use_json, quiet),
        'doctor':    lambda: cmd_doctor(args, cfg, use_json, quiet),
        'config':    lambda: cmd_config_dispatch(args, cfg, use_json, quiet),
        'cache':     lambda: cmd_cache_dispatch(args, cfg, use_json, quiet),
        'quota':     lambda: cmd_quota(args, cfg, use_json, quiet),
        'top':       lambda: cmd_top(args, cfg, use_json, quiet),
        'recent':    lambda: cmd_recent(args, cfg, use_json, quiet),
        'history':   lambda: cmd_history(args, cfg, use_json, quiet),
        'diff':      lambda: cmd_diff(args, cfg, use_json, quiet),
        'stats':     lambda: cmd_stats(args, cfg, use_json, quiet),
        'summary':   lambda: cmd_summary(args, cfg, use_json, quiet),
        'watch':     lambda: cmd_watch(args, cfg, use_json, quiet),
        'compare':   lambda: cmd_compare(args, cfg, use_json, quiet),
        'dupes':     lambda: cmd_dupes(args, cfg, use_json, quiet),
        'export':    lambda: cmd_export(args, cfg, use_json, quiet),
        'files':     lambda: cmd_files(args, cfg, use_json, quiet),
        'scan':      lambda: cmd_scan(args, cfg, use_json, quiet),
    }

    handler = _DISPATCH.get(cmd)
    if handler:
        handler()
    else:
        print(f"\n  Unknown command: {cmd}\n", file=sys.stderr)
        sys.exit(EX.ERR_ARGS)
