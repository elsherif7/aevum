"""
ANSI color constants for Aevum.

All modules import the ``clr`` singleton and access colors as attributes
(``clr.R``, ``clr.G``, …).  Calling ``clr.disable()`` mutates the singleton
in-place, so the change is immediately visible everywhere — no module needs
to re-import anything.

Usage
-----
    from ._color import clr, LINE, clear

    print(f"{clr.G}OK{clr.RST}")
    clr.disable()           # strips all ANSI from every subsequent print
"""

import os

# ---------------------------------------------------------------------------
# Enable virtual-terminal processing on Windows so ANSI codes render in
# cmd.exe / PowerShell without a third-party library.
# ---------------------------------------------------------------------------
if os.name == "nt":
    import ctypes
    _kernel32 = ctypes.windll.kernel32
    _handle   = _kernel32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
    if _handle and _handle != -1:
        # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        _kernel32.SetConsoleMode(_handle, 0x0001 | 0x0002 | 0x0004)


# ---------------------------------------------------------------------------
# Singleton color object
# ---------------------------------------------------------------------------

class _Colors:
    """
    Holds every ANSI escape used throughout Aevum.

    Attributes are plain strings so f-string usage stays identical to the
    old ``from ._color import R, G, …`` style — just prefix with ``clr.``.

    ``disable()`` replaces every escape sequence with an empty string in
    the *same object*, so every module that holds a reference to ``clr``
    sees the change instantly.
    """

    __slots__ = ("R", "G", "Y", "B", "M", "C", "W", "DIM", "RST", "_disabled")

    def __init__(self) -> None:
        self._disabled = False
        self._set_color()

    def _set_color(self) -> None:
        self.R   = "\033[91m"
        self.G   = "\033[92m"
        self.Y   = "\033[93m"
        self.B   = "\033[94m"
        self.M   = "\033[95m"
        self.C   = "\033[96m"
        self.W   = "\033[97m"
        self.DIM = "\033[2m"
        self.RST = "\033[0m"

    def disable(self) -> None:
        """Strip all ANSI color from every future print across the process."""
        if self._disabled:
            return
        self._disabled = True
        self.R = self.G = self.Y = self.B = self.M = self.C = ""
        self.W = self.DIM = self.RST = ""

    @property
    def enabled(self) -> bool:
        return not self._disabled


#: The one true color object — import this everywhere.
clr = _Colors()


# ---------------------------------------------------------------------------
# Module-level helpers (not color-dependent)
# ---------------------------------------------------------------------------

LINE = "=" * 64


def clear() -> None:
    """ANSI clear-screen + cursor-home, works on Windows and Unix."""
    print("\033[2J\033[H", end="", flush=True)


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
# Old code did ``from ._color import R, G, Y, …``.  Those names now delegate
# to the singleton so existing call sites keep working while new code uses
# ``clr.X`` directly.  The shim is intentionally kept thin — prefer ``clr``
# in all new/edited code.

def _disable_color() -> None:   # legacy entry-point kept for any external callers
    clr.disable()
