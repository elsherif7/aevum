"""
Centralised AppData / XDG path resolution for Aevum.
Imports nothing from the package — safe to import from anywhere.
"""

import os
from pathlib import Path


def _appdata_dir() -> Path:
    """
    Resolve the platform-correct Aevum data directory.
    Validates env vars to prevent path traversal attacks.
    """
    home = Path.home()
    if os.name == "nt":
        raw = os.environ.get("LOCALAPPDATA", "")
        if raw:
            candidate = Path(raw)
            # Must be absolute and must not be a UNC path (\\server\share)
            # and must resolve to the same path (no traversal sequences)
            if (candidate.is_absolute()
                    and not str(candidate).startswith('\\\\')
                    and candidate.resolve() == candidate):
                return candidate / "Aevum"
        return home / "AppData" / "Local" / "Aevum"
    else:
        raw = os.environ.get("XDG_DATA_HOME", "")
        if raw:
            candidate = Path(raw)
            if (candidate.is_absolute()
                    and candidate.resolve() == candidate):
                return candidate / "Aevum"
        return home / ".local" / "share" / "Aevum"


APPDATA        = _appdata_dir()
CACHE_DIR      = APPDATA / "cache"
CONFIG_FILE    = APPDATA / "config.json"
YT_KEY_FILE    = APPDATA / "yt_api_key.txt"
YT_QUOTA_FILE  = APPDATA / "yt_quota_tracker.json"
YT_VCACHE_FILE = APPDATA / "yt_video_cache.json"
