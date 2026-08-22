"""
Small stateless helpers shared across CLI command handlers.

Only what a flag-free 'scan' needs remains: a progress-bar callback and
the ffprobe-availability check. Sort/top/filter resolution helpers were
removed along with the flags that used to feed them.
"""
import sys

from ._color import clr
from ._exit import EX
from ._scan import check_ffprobe


def _make_progress_bar():
    """
    Return a progress callback that renders a text progress bar to stdout.

    Issue 15 fix: guard against total == 0 inside the callback itself so
    that any caller passing total=0 directly gets a no-op instead of a
    ZeroDivisionError.
    """
    def on_progress(done, total):
        if total <= 0:   # Issue 15
            return
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "\u2588" * filled + "\u2591" * (24 - filled)
        print(f"\r  {clr.C}Scanning...{clr.RST}  {bar}  {clr.Y}{done}/{total}{clr.RST}  {clr.DIM}({pct}%){clr.RST}",
              end='', flush=True)

    return on_progress


def _require_ffprobe(context: str = "") -> None:
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        print(f"\n  {clr.R}[ERROR]{clr.RST} ffprobe not found on PATH{ctx}.", file=sys.stderr)
        print(f"  {clr.DIM}ffprobe is required for local folder scanning.{clr.RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}\n", file=sys.stderr)
        sys.exit(EX.ERR_DEPS)
