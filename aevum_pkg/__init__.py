"""
aevum_pkg — Media Library Scanner
Public API surface.

Only genuinely public symbols are re-exported here. Private helpers
(prefixed with _) remain accessible via their own modules for internal
use but are not part of the declared public API.

This is the minimal build: only local-folder and YouTube scanning
remain. The config, local duration cache, duplicate-detection, export,
history, compare, watch, and alias features (and their modules) were
removed to keep the tool small and single-purpose. Every folder scan
always re-probes every file. (YouTube's own per-video API-response
cache in _youtube.py is a separate mechanism and was not removed.)

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

From _paths:
    APPDATA             — platform-correct Aevum data directory (Path)

From _color:
    clr                 — ANSI color singleton
    LINE                — separator line string
    clear               — clear-screen helper

From _models:
    FolderNode          — named type for one node in the scan tree
    ScanTree            — named type for the top-level scan result
"""

__version__ = "1.0.0"

# ── API Key (Secure Storage) ─────────────────────────────────────────────────
from ._apikey import (
    load_api_key,
    save_api_key,
)

# ── Color ────────────────────────────────────────────────────────────────────
from ._color import LINE, clear, clr

# ── Display ─────────────────────────────────────────────────────────────────
from ._display import (
    print_results,
    print_url_results,
)

# ── Models ───────────────────────────────────────────────────────────────────
from ._models import FolderNode, ScanTree

# ── Paths ────────────────────────────────────────────────────────────────────
from ._paths import APPDATA

# ── Scan ─────────────────────────────────────────────────────────────────────
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
    # paths
    "APPDATA",
    # color
    "clr", "LINE", "clear",
    # models
    "FolderNode", "ScanTree",
    # version
    "__version__",
]
