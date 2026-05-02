"""
aevum_pkg — Media Library Scanner
Public API surface.

Issue 31 fix: only genuinely public symbols are re-exported here.
Private helpers (prefixed with _) are no longer imported into __init__.py;
they remain accessible via their own modules for internal use but are not
part of the package's declared public API.  This makes it safe to rename
or remove internals without accidentally breaking external callers who
relied on the accidental __init__.py exposure.

Intended public API
-------------------
From _scan:
    check_ffprobe       — verify ffprobe is on PATH
    get_duration        — return duration in seconds for a single file
    format_duration     — format seconds into a human-readable dict
    format_size         — format bytes into a human-readable string
    scan_parallel       — run a full parallel folder scan
    video_extensions    — tuple of all recognised media extensions

From _youtube:
    scan_url            — fetch duration data for a YouTube URL
    load_api_key        — read the stored YouTube API key
    save_api_key        — persist a YouTube API key
    get_quota_status    — return today's quota usage figures

From _display:
    print_results       — print a human-readable scan result
    print_url_results   — print a human-readable YouTube result
From _dupes:
    find_duplicates     — detect duplicate files by size + hash
    print_duplicates    — print duplicate groups to stdout

From _compare:
    run_compare         — scan two folders for comparison
    print_comparison    — print the side-by-side comparison

From _export:
    export_results      — write scan results to TXT/CSV/JSON
    export_url_results  — write YouTube scan results to TXT/CSV/JSON

From _config:
    load_config         — read persistent config from disk
    save_config         — write persistent config to disk
    CONFIG_DEFAULTS     — dict of default config values

From _cache:
    load_cache          — read the duration cache for a folder
    save_cache          — write the duration cache for a folder

From _paths:
    APPDATA             — platform-correct Aevum data directory (Path)
    CACHE_DIR           — cache subdirectory (Path)
    CONFIG_FILE         — config file path (Path)

From _color:
    clr                 — ANSI color singleton
    LINE                — separator line string
    clear               — clear-screen helper
"""

__version__ = "1.0.0"

# ── Scan ────────────────────────────────────────────────────────────────────
from ._scan import (
    check_ffprobe,
    get_duration,
    format_duration,
    format_size,
    scan_parallel,
    video_extensions,
)

# ── YouTube ─────────────────────────────────────────────────────────────────
from ._youtube import (
    scan_url,
    get_quota_status,
)

# ── API Key (Secure Storage) ─────────────────────────────────────────────────
from ._apikey import (
    load_api_key,
    save_api_key,
)

# ── Display ─────────────────────────────────────────────────────────────────
from ._display import (
    print_results,
    print_url_results,
)

# ── Duplicates ──────────────────────────────────────────────────────────────
from ._dupes import (
    find_duplicates,
    print_duplicates,
)

# ── Compare ─────────────────────────────────────────────────────────────────
from ._compare import (
    run_compare,
    print_comparison,
)

# ── Export ───────────────────────────────────────────────────────────────────
from ._export import (
    export_results,
    export_url_results,
)

# ── Config ───────────────────────────────────────────────────────────────────
from ._config import (
    load_config,
    save_config,
    CONFIG_DEFAULTS,
)

# ── Cache ────────────────────────────────────────────────────────────────────
from ._cache import (
    load_cache,
    save_cache,
)

# ── Paths ────────────────────────────────────────────────────────────────────
from ._paths import (
    APPDATA,
    CACHE_DIR,
    CONFIG_FILE,
)

# ── Color ────────────────────────────────────────────────────────────────────
from ._color import clr, LINE, clear

# ── Exit codes (re-exported as a module for external scripts) ────────────────
from . import _exit

__all__ = [
    # scan
    "check_ffprobe", "get_duration", "format_duration", "format_size",
    "scan_parallel", "video_extensions",
    # youtube
    "scan_url", "load_api_key", "save_api_key", "get_quota_status",
    # display
    "print_results", "print_url_results",
    # dupes
    "find_duplicates", "print_duplicates",
    # compare
    "run_compare", "print_comparison",
    # export
    "export_results", "export_url_results",
    # config
    "load_config", "save_config", "CONFIG_DEFAULTS",
    # cache
    "load_cache", "save_cache",
    # paths
    "APPDATA", "CACHE_DIR", "CONFIG_FILE",
    # color
    "clr", "LINE", "clear",
    # exit codes
    "_exit",
    # version
    "__version__",
]
