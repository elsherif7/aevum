import hashlib
import json
import os
from pathlib import Path

from ._paths import CACHE_DIR


def _normalise_path(p):
    """
    Return a normalised absolute path string used as a cache key.

    Issue 22 fix: on Windows, paths are lowercased so that a file cached as
    'C:\\Movies\\File.MKV' is still found when looked up as
    'c:\\movies\\file.mkv'. Case is significant on Linux/macOS, so we leave
    it unchanged there.
    """
    s = str(Path(p).resolve())
    return s.lower() if os.name == "nt" else s


def _cache_key(root):
    """Stable filename for the cache of a given root folder."""
    h = hashlib.sha1(_normalise_path(root).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def load_cache(root):
    """
    Load the cache for this root folder.
    Returns a dict mapping normalised absolute path string ->
        {mtime, size, duration}.
    Returns {} if no cache exists or it is unreadable.

    Issue 22 fix: keys are normalised via _normalise_path() so Windows
    case differences never cause spurious cache misses.
    Issue 23 fix: CACHE_DIR now comes from _paths.py which correctly
    uses XDG_DATA_HOME on Linux instead of falling back to ~/Aevum.
    """
    path = _cache_key(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {_normalise_path(entry["path"]): entry for entry in data}
    except Exception:
        return {}


def save_cache(root, durations):
    """
    Persist durations to the cache file for this root folder.
    durations: dict mapping Path -> seconds (float).

    Issue 22 fix: the 'path' field stored in JSON is also normalised so
    future loads produce consistent keys.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for p, sec in durations.items():
            try:
                st = p.stat()
                entries.append({
                    "path":     _normalise_path(p),
                    "mtime":    st.st_mtime,
                    "size":     st.st_size,
                    "duration": sec,
                })
            except OSError:
                pass
        _cache_key(root).write_text(
            json.dumps(entries, indent=None, separators=(',', ':')),
            encoding="utf-8",
        )
    except Exception:
        pass  # cache write failure is never fatal
