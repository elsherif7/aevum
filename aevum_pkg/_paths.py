"""
Centralised AppData / XDG path resolution for Aevum.
Imports nothing from the package — safe to import from anywhere.
"""

import os
from pathlib import Path


def _appdata_dir() -> Path:
    """
    Resolve the platform-correct Aevum data directory.
    
    H-10: validate environment variable values before using them as paths.
    If the env var contains a relative path or path traversal sequences,
    fall back to the home directory to prevent data landing in unexpected
    locations.
    """
    if os.name == "nt":
        raw = os.environ.get("LOCALAPPDATA", "")
        if raw:
            candidate = Path(raw)
            # Must be absolute — reject relative paths and traversal attempts
            if candidate.is_absolute():
                return candidate / "Aevum"
        return Path.home() / "AppData" / "Local" / "Aevum"
    else:
        raw = os.environ.get("XDG_DATA_HOME", "")
        if raw:
            candidate = Path(raw)
            if candidate.is_absolute():
                return candidate / "Aevum"
        return Path.home() / ".local" / "share" / "Aevum"


APPDATA        = _appdata_dir()
CACHE_DIR      = APPDATA / "cache"
CONFIG_FILE    = APPDATA / "config.json"
YT_KEY_FILE    = APPDATA / "yt_api_key.txt"
YT_QUOTA_FILE  = APPDATA / "yt_quota_tracker.json"
YT_VCACHE_FILE = APPDATA / "yt_video_cache.json"
