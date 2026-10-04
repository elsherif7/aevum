"""Per-user data directory. Imports nothing from the package."""

import os
from pathlib import Path


def _appdata_dir() -> Path:
    """LOCALAPPDATA and XDG_DATA_HOME are used only when absolute and already resolved."""
    home = Path.home()
    if os.name == "nt":
        raw = os.environ.get("LOCALAPPDATA", "")
        if raw:
            candidate = Path(raw)
            # UNC paths are rejected too
            if (candidate.is_absolute()
                    and not str(candidate).startswith("\\\\")
                    and candidate.resolve() == candidate):
                return candidate / "Aevum"
        return home / "AppData" / "Local" / "Aevum"

    raw = os.environ.get("XDG_DATA_HOME", "")
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.resolve() == candidate:
            return candidate / "Aevum"
    return home / ".local" / "share" / "Aevum"


APPDATA        = _appdata_dir()
YT_KEY_FILE    = APPDATA / "yt_api_key.txt"
YT_VCACHE_FILE = APPDATA / "yt_video_cache.json"
