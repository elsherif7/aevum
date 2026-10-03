"""
ANSI color handling for Aevum.

Two color objects are exported, because stdout and stderr can be
redirected independently:

    from ._color import clr, eclr, LINE

    print(f"{clr.G}OK{clr.RST}")                       # stdout
    print(f"{eclr.R}[ERROR]{eclr.RST}", file=sys.stderr)  # stderr

Colors are switched off (every code becomes an empty string) unless the
stream is a terminal. The rules follow CPython's own ``_colorize``:

  1. NO_COLOR set to anything non-empty  -> off
  2. FORCE_COLOR set to anything non-empty -> on
  3. TERM=dumb                            -> off
  4. otherwise                            -> on only if the stream is a TTY
"""

import os
import sys
from collections.abc import Mapping
from typing import TextIO

_CODES = {
    "R":   "\033[91m",
    "G":   "\033[92m",
    "Y":   "\033[93m",
    "B":   "\033[94m",
    "M":   "\033[95m",
    "C":   "\033[96m",
    "W":   "\033[97m",
    "DIM": "\033[2m",
    "RST": "\033[0m",
}


def color_enabled(stream: TextIO | None, env: Mapping[str, str] | None = None) -> bool:
    """Decide whether ANSI colors should be written to *stream*."""
    if env is None:
        env = os.environ
    if env.get("NO_COLOR"):
        return False
    if env.get("FORCE_COLOR"):
        return True
    if env.get("TERM") == "dumb":
        return False
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _enable_windows_vt(std_handle: int) -> bool:
    """Turn on ANSI processing for one Windows console handle (-11 out, -12 err).

    Returns True if the console accepted it. Never raises.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(std_handle)
        invalid = ctypes.c_void_p(-1).value
        if not handle or ctypes.c_void_p(handle).value == invalid:
            return False
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False  # not a console (redirected)
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class _Colors:
    """Holds every ANSI escape used throughout Aevum (empty when disabled)."""

    __slots__ = ("R", "G", "Y", "B", "M", "C", "W", "DIM", "RST")

    R: str
    G: str
    Y: str
    B: str
    M: str
    C: str
    W: str
    DIM: str
    RST: str

    def __init__(self, enabled: bool) -> None:
        for name, code in _CODES.items():
            setattr(self, name, code if enabled else "")


def _make(stream: TextIO, std_handle: int) -> _Colors:
    enabled = color_enabled(stream)
    if enabled and sys.platform == "win32":
        # A forced color request on a redirected stream can't be "enabled" on
        # the console; the escapes are still wanted by whoever reads the pipe.
        _enable_windows_vt(std_handle)
    return _Colors(enabled)


#: Colors for text written to stdout.
clr = _make(sys.stdout, -11)
#: Colors for text written to stderr.
eclr = _make(sys.stderr, -12)

LINE = "=" * 64
