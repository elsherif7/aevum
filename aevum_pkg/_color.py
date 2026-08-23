"""
ANSI color constants for Aevum.

All modules import the ``clr`` singleton and access colors as attributes
(``clr.R``, ``clr.G``, …).

Usage
-----
    from ._color import clr, LINE

    print(f"{clr.G}OK{clr.RST}")
"""

import os

# ---------------------------------------------------------------------------
# Enable virtual-terminal processing on Windows so ANSI codes render in
# cmd.exe / PowerShell without a third-party library.
# ---------------------------------------------------------------------------
if os.name == "nt":
    import ctypes
    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    _handle   = _kernel32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
    # INVALID_HANDLE_VALUE is -1 as a signed pointer; compare via c_void_p
    _INVALID  = ctypes.c_void_p(-1).value
    if _handle and ctypes.c_void_p(_handle).value != _INVALID:
        # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        _kernel32.SetConsoleMode(_handle, 0x0001 | 0x0002 | 0x0004)


# ---------------------------------------------------------------------------
# Singleton color object
# ---------------------------------------------------------------------------

class _Colors:
    """Holds every ANSI escape used throughout Aevum."""

    __slots__ = ("R", "G", "Y", "B", "M", "C", "W", "DIM", "RST")

    def __init__(self) -> None:
        self.R   = "\033[91m"
        self.G   = "\033[92m"
        self.Y   = "\033[93m"
        self.B   = "\033[94m"
        self.M   = "\033[95m"
        self.C   = "\033[96m"
        self.W   = "\033[97m"
        self.DIM = "\033[2m"
        self.RST = "\033[0m"


#: The one true color object — import this everywhere.
clr = _Colors()


# ---------------------------------------------------------------------------
# Module-level helpers (not color-dependent)
# ---------------------------------------------------------------------------

LINE = "=" * 64
