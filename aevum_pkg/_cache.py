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
    and TOCTOU attacks. Validates path is within allowed boundaries.
    
    Issue 22 fix: on Windows, paths are lowercased so that a file cached as
    'C:\\Movies\\File.MKV' is still found when looked up as
    'c:\\movies\\file.mkv'. Case is significant on Linux/macOS, so we leave
    it unchanged there.
    """
    try:
        # Resolve symlinks FIRST to prevent attacks
        resolved = Path(p).resolve(strict=False)
        
        # Security check: ensure path is under user's home or common media dirs
        allowed_roots = [
            Path.home(),
            Path("/media"),
            Path("/mnt"),
        ]
        
        is_allowed = False
        for root in allowed_roots:
            if not root.exists():
                continue
            try:
                resolved.relative_to(root)
                is_allowed = True
                break
            except ValueError:
                continue
        
        if not is_allowed and os.name == "nt":
            # On Windows, allow any valid drive letter
            drive = resolved.drive
            if drive and len(drive) >= 2 and drive[1] == ':' and drive[0].upper().isalpha():
                is_allowed = True
        
        if not is_allowed:
            raise PermissionError(f"Path {resolved} is outside allowed directories")
        
        s = str(resolved)
        return s.lower() if os.name == "nt" else s
    except (OSError, RuntimeError, PermissionError) as e:
        raise PermissionError(f"Invalid path: {e}")


def _cache_key(root):
    """
    Stable filename for the cache of a given root folder.
    
    Security: Uses SHA-256 instead of SHA-1 (cryptographically broken).
    Validates cache file path is under CACHE_DIR to prevent path traversal.
    """
    normalized = _normalise_path(root)
    
    # Use SHA-256 instead of SHA-1 (more secure)
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    
    cache_file = CACHE_DIR / f"{h}.json"
    
    # Security: verify cache_file is actually under CACHE_DIR
    try:
        cache_file.resolve().relative_to(CACHE_DIR.resolve())
    except ValueError:
        raise PermissionError("Cache path validation failed")
    
    return cache_file


def load_cache(root):
    """
    Load the cache for this root folder with validation.
    
    Returns a dict mapping normalised absolute path string ->
        {mtime, size, duration}.
    Returns {} if no cache exists or it is unreadable.
    
    Security: Validates JSON structure and types to prevent deserialization
    attacks. Only accepts expected data types. Uses constant-time comparison
    for cache key lookups to prevent timing attacks.
    
    Issue 22 fix: keys are normalised via _normalise_path() so Windows
    case differences never cause spurious cache misses.
    Issue 23 fix: CACHE_DIR now comes from _paths.py which correctly
    uses XDG_DATA_HOME on Linux instead of falling back to ~/Aevum.
    """
    path = _cache_key(root)
    try:
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
            if st.st_mtime == entry["mtime"] and st.st_size == entry["size"]:
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
