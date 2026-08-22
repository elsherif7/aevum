"""
Small stateless helpers shared across CLI command handlers.
"""
import sys

from ._cli_json import _json_error
from ._color import clr
from ._exit import EX
from ._scan import check_ffprobe, parse_duration_arg, parse_since_arg


def _make_progress_bar(quiet=False, use_json=False):
    """
    Return a progress callback, or None in machine-output modes.

    Issue 15 fix: guard against total == 0 inside the callback itself so
    that any caller passing total=0 directly gets a no-op instead of a
    ZeroDivisionError.
    """
    if quiet or use_json:
        return None

    def on_progress(done, total):
        if total <= 0:   # Issue 15
            return
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "\u2588" * filled + "\u2591" * (24 - filled)
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
        print(f"  Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}\n", file=sys.stderr)
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


def _use_cache(args, cfg):
    """
    Issue 33 fix: single authoritative helper for the use_cache flag so that
    all callers derive it the same way. The no_cache arg may not be present
    on all namespaces, so we use getattr with a default.
    """
    if getattr(args, 'no_cache', False):
        return False
    return cfg.get('cache_enabled', True)


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
    raw_exclude = getattr(args, 'exclude', None)
    if raw_exclude:
        filters['exclude'] = {
            x.strip().lower() for x in raw_exclude.split(',') if x.strip()
        }
    for attr, key in (('since', 'since'), ('until', 'until')):
        raw = getattr(args, attr, None)
        if raw:
            try:
                filters[key] = parse_since_arg(raw)
            except ValueError as e:
                if use_json:
                    _json_error(str(e), EX.ERR_ARGS)
                print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
    return filters
