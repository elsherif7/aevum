import hashlib
import json
import os
import tempfile
from pathlib import Path

from ._paths import CACHE_DIR

# Validated field schema for cache entries — defined once at module level.
_CACHE_ENTRY_FIELDS = {
    "path":     str,
    "mtime":    (int, float),
    "size":     int,
    "duration": (int, float),
}


def _normalise_path(p):
    """
    Return a normalised absolute path with symlinks resolved.

    Security: Always resolve symlinks to prevent cache poisoning
    and TOCTOU attacks.

    S-05/S-10 fix: removed overly restrictive path validation that rejected
    valid media paths (network shares, removable drives, non-home directories).
    Path access control should happen at the scan entry point, not in the
    cache key function.  The cache accepts any path the OS allows the process
    to read.

    Issue 22 fix: on Windows, paths are lowercased so that a file cached as
    'C:\\Movies\\File.MKV' is still found when looked up as
    'c:\\movies\\file.mkv'. Case is significant on Linux/macOS, so we leave
    it unchanged there.
    """
    try:
        # Resolve symlinks FIRST to prevent attacks
        resolved = Path(p).resolve(strict=False)
        s = str(resolved)
        return s.lower() if os.name == "nt" else s
    except (OSError, RuntimeError) as e:
        raise PermissionError(f"Invalid path: {e}")


def _cache_key(root):
    """
    Stable filename for the cache of a given root folder.

    Security: Uses SHA-256 instead of SHA-1 (cryptographically broken).
    The hex digest is [0-9a-f] only, so the resulting filename can never
    escape CACHE_DIR via path traversal.
    """
    normalized = _normalise_path(root)
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def load_cache(root):
    """
    Load the cache for this root folder with validation.
    Returns {} if no cache exists, is unreadable, or exceeds size limit.
    """
    path = _cache_key(root)
    MAX_CACHE_SIZE = 50 * 1024 * 1024  # 50 MB hard limit
    try:
        if path.exists() and path.stat().st_size > MAX_CACHE_SIZE:
            print(f"  [WARN] Cache file too large (>{MAX_CACHE_SIZE//1024//1024} MB), ignoring.", file=__import__('sys').stderr)
            return {}
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)

        # Security: validate JSON structure
        if not isinstance(data, list):
            return {}

        validated = {}
        for entry in data:
            # Validate each entry is a dict with required fields
            if not isinstance(entry, dict):
                continue

            # Check all required fields exist and have correct types
            valid = True
            for field, expected_type in _CACHE_ENTRY_FIELDS.items():
                if field not in entry:
                    valid = False
                    break
                if not isinstance(entry[field], expected_type):
                    valid = False
                    break

            if not valid:
                continue

            # Normalize path and store validated entry
            try:
                normalized = _normalise_path(entry["path"])
                validated[normalized] = {
                    "mtime": float(entry["mtime"]),
                    "size": int(entry["size"]),
                    "duration": float(entry["duration"]),
                }
            except (PermissionError, ValueError, OSError):
                # Skip entries with invalid paths
                continue

        return validated
    except (json.JSONDecodeError, OSError, PermissionError):
        return {}


def get_cached_duration(path: Path, cache: dict) -> tuple:
    """
    Get duration from cache.

    H-12: use 2-second tolerance for mtime comparison (FAT/exFAT precision).

    Returns:
        (duration, hit) tuple - (0.0, False) if not in cache
    """
    key = str(path.resolve())
    if os.name == "nt":
        key = key.lower()

    entry = cache.get(key)
    if entry is not None:
        try:
            st = path.stat()
            if abs(st.st_mtime - entry["mtime"]) < 2.0 and st.st_size == entry["size"]:
                return entry["duration"], True
        except OSError:
            pass

    return 0.0, False


def save_cache(root, durations):
    """
    Persist durations to the cache file for this root folder.
    durations: dict mapping Path -> seconds (float).

    Security: Uses atomic write (temp file + rename) to prevent race conditions.
    Sets restrictive permissions (user read/write only).

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
            except (OSError, PermissionError):
                pass

        cache_path = _cache_key(root)

        # Security: Atomic write using temp file + rename
        temp_fd, temp_path = tempfile.mkstemp(
            dir=CACHE_DIR,
            prefix=".tmp_cache_",
            suffix=".json"
        )

        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                json.dump(entries, f, indent=None, separators=(',', ':'))

            # Set restrictive permissions (user read/write only)
            os.chmod(temp_path, 0o600)

            # Atomic rename — os.replace is atomic on all platforms including
            # modern Windows (Vista+); the explicit unlink is not needed and
            # widens the race window, so it has been removed.
            os.replace(temp_path, cache_path)
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    except Exception:
        pass  # cache write failure is never fatal
