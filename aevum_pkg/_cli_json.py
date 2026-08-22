"""
JSON serialisation helpers for the Aevum CLI.

All functions that convert internal scan data into JSON-serialisable
dicts live here so _cli.py stays thin. Only scan-related serialisers
remain — the dupes/compare ones were removed along with those commands.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ._scan import format_duration


def _json_out(data: dict):
    """Write JSON to stdout and flush."""
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def _json_error(msg: str, code: int, extra: dict | None = None) -> None:
    d = {"status": "error", "code": code, "error": msg}
    if extra:
        d.update(extra)
    _json_out(d)
    sys.exit(code)


def _tree_to_dict(name, seconds, count, children: list, direct_files=None):
    """Recursively convert a ScanTree/FolderNode structure into a plain dict."""
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "direct":    [{"file": p.name, "seconds": round(s, 2)} for p, s in (direct_files or [])],
        "children":  [_tree_to_dict(n.name, n.total_sec, n.total_count, n.children, n.direct_files)
                      for n in children],
    }


def _scan_to_json(folder, total_sec, total_count, tree, durations, sizes):
    """Convert a completed local scan to a JSON-serialisable dict."""
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "path":        str(Path(folder).resolve()),
        "total_files": total_count,
        "total_bytes": sum(sizes.values()),
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "tree":        _tree_to_dict(Path(folder).name, total_sec, total_count,
                                     tree.children, tree.direct_files),
        "files": [
            {
                "path":     str(p),
                "filename": p.name,
                "folder":   p.parent.name,
                "seconds":  round(s, 2),
                "bytes":    sizes.get(p, 0),
                "duration": format_duration(s)["hours_fmt"],
            }
            for p, s in sorted(durations.items(), key=lambda x: x[1], reverse=True)
        ],
    }


def _url_to_json(url, label, total_sec, total_count, entries):
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "url":         url,
        "label":       label,
        "total_files": total_count,
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "videos": [
            {
                "title":    e["title"],
                "channel":  e.get("channel", ""),
                "url":      e.get("url", ""),
                "seconds":  round(e["duration"], 2),
                "duration": format_duration(e["duration"])["hours_fmt"],
            }
            for e in sorted(entries, key=lambda x: x["duration"], reverse=True)
        ],
    }
