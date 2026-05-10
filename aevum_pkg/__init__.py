"""
aevum_pkg — Media Library Scanner
Public API surface.

Only genuinely public symbols are re-exported here. Private helpers
(prefixed with _) remain accessible via their own modules for internal
use but are not part of the declared public API.

Public API
----------
From _scan:
    check_ffprobe       — verify ffprobe is on PATH
    get_duration        — return duration in seconds for a single file
    format_duration     — format seconds into a human-readable dict
    format_size         — format bytes into a human-readable string
    scan_parallel       — run a full parallel folder scan
    video_extensions    — tuple of all recognised media extensions

From _youtube:
    scan_url            — fetch duration data for a YouTube URL
    get_quota_status    — return today's quota usage figures

From _apikey:
    load_api_key        — read the stored YouTube API key
    save_api_key        — persist a YouTube API key

From _display:
    print_results       — print a human-readable scan result
    print_url_results   — print a human-readable YouTube result

From _dupes:
    find_duplicates     — detect duplicate files by size + hash
    print_duplicates    — print duplicate groups to stdout

From _export:
    export_results      — write scan results to TXT/CSV/JSON/HTML
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

From _models:
    FolderNode          — named type for one node in the scan tree
    ScanTree            — named type for the top-level scan result
"""

__version__ = "2.3.0"

# ── Scan ────────────────────────────────────────────────────────────────────
# ── API Key (Secure Storage) ─────────────────────────────────────────────────
from ._apikey import (
    load_api_key,
    save_api_key,
)

# ── Cache ────────────────────────────────────────────────────────────────────
from ._cache import (
    load_cache,
    save_cache,
)

# ── Color ────────────────────────────────────────────────────────────────────
from ._color import LINE, clear, clr

# ── Config ───────────────────────────────────────────────────────────────────
from ._config import (
    CONFIG_DEFAULTS,
    load_config,
    save_config,
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

# ── Export ───────────────────────────────────────────────────────────────────
from ._export import (
    export_results,
    export_url_results,
)

# ── Models ───────────────────────────────────────────────────────────────────
from ._models import FolderNode, ScanTree

# ── Paths ────────────────────────────────────────────────────────────────────
from ._paths import APPDATA, CACHE_DIR, CONFIG_FILE
from ._scan import (
    check_ffprobe,
    format_duration,
    format_size,
    get_duration,
    scan_parallel,
    video_extensions,
)

# ── YouTube ─────────────────────────────────────────────────────────────────
from ._youtube import (
    get_quota_status,
    scan_url,
)

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
    # models
    "FolderNode", "ScanTree",
    # version
    "__version__",
]
