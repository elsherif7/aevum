import hashlib
import json
import os
from pathlib import Path

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "cache"


def _cache_key(root):
    """Stable filename for the cache of a given root folder."""
    h = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def load_cache(root):
    """
    Load the cache for this root folder.
    Returns a dict mapping absolute path string -> {mtime, size, duration}.
    Returns {} if no cache exists or it is unreadable.
    """
    path = _cache_key(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {entry["path"]: entry for entry in data}
    except Exception:
        return {}


def save_cache(root, durations):
    """
    Persist durations to the cache file for this root folder.
    durations: dict mapping Path -> seconds (float)
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for p, sec in durations.items():
            try:
                st = p.stat()
                entries.append({
                    "path":     str(p.resolve()),
                    "mtime":    st.st_mtime,
                    "size":     st.st_size,
                    "duration": sec,
                })
            except OSError:
                pass
        _cache_key(root).write_text(
            json.dumps(entries, indent=None, separators=(',', ':')),
            encoding="utf-8"
        )
    except Exception:
        pass  # cache write failure is never fatal
