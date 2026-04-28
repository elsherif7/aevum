"""
Centralised AppData / XDG path resolution for Aevum.

All modules that need persistent storage import from here instead of
computing the base directory themselves.  This ensures:

  - Windows  : %LOCALAPPDATA%\\Aevum\\  (falls back to ~\\Aevum)
  - Linux/macOS: $XDG_DATA_HOME/Aevum/  (falls back to ~/.local/share/Aevum)

Previously every module duplicated the same pattern:
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / ...
which silently fell back to ~/Aevum on Linux instead of following XDG.
"""

import os
from pathlib import Path


def appdata_dir() -> Path:
    """
    Return the Aevum application-data directory for the current platform.
    The directory is NOT created here — callers must mkdir as needed.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        # XDG Base Directory Specification
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Aevum"


# ---------------------------------------------------------------------------
# Pre-built paths — import these directly instead of calling appdata_dir()
# each time, so the value is computed once per process.
# ---------------------------------------------------------------------------

APPDATA        = appdata_dir()
CACHE_DIR      = APPDATA / "cache"
CONFIG_FILE    = APPDATA / "config.json"
YT_KEY_FILE    = APPDATA / "yt_api_key.txt"
YT_QUOTA_FILE  = APPDATA / "yt_quota_tracker.json"
YT_VCACHE_FILE = APPDATA / "yt_video_cache.json"
