"""
Centralised AppData / XDG path resolution for Aevum.
Imports nothing from the package — safe to import from anywhere.
"""

import os
from pathlib import Path


def _appdata_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Aevum"


APPDATA        = _appdata_dir()
CACHE_DIR      = APPDATA / "cache"
CONFIG_FILE    = APPDATA / "config.json"
YT_KEY_FILE    = APPDATA / "yt_api_key.txt"
YT_QUOTA_FILE  = APPDATA / "yt_quota_tracker.json"
YT_VCACHE_FILE = APPDATA / "yt_video_cache.json"
