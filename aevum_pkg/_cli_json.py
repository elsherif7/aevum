"""
JSON serialisation helpers for the Aevum CLI.

All functions that convert internal scan/dupe/compare data into
JSON-serialisable dicts live here so _cli.py stays thin.
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


def _scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits):
    """Convert a completed local scan to a JSON-serialisable dict."""
    from ._export import _tree_to_dict
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "path":        str(Path(folder).resolve()),
        "total_files": total_count,
        "total_bytes": sum(sizes.values()),
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "cache_hits":  hits,
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


def _dupes_to_json(groups, durations, sizes):
    """
    Issue 16 fix: wasted-time calculation delegates to dupes_to_json()
    in _dupes.py which uses the median-based formula — same as print_duplicates()
    — so --json output matches human output exactly.
    """
    from ._dupes import dupes_to_json as _dj
    entries      = _dj(groups, durations)
    total_wasted = sum(e["wasted_sec"] for e in entries)
    out_groups = []
    for e, group in zip(entries, groups):
        out_groups.append({
            "copies":      e["copies"],
            "seconds":     e["duration_sec"],
            "wasted_sec":  e["wasted_sec"],
            "wasted_fmt":  format_duration(e["wasted_sec"])["hours_fmt"],
            "files": [{"path": str(p), "bytes": sizes.get(p, 0)} for p in group],
        })
    return {
        "status":           "ok",
        "command":          "dupes",
        "groups_found":     len(groups),
        "total_wasted_sec": round(total_wasted, 2),
        "total_wasted_fmt": format_duration(total_wasted)["hours_fmt"],
        "groups":           out_groups,
    }


def _compare_to_json(folder_a, folder_b, data_a, data_b):
    sec_a, count_a, _ = data_a
    sec_b, count_b, _ = data_b
    delta = sec_b - sec_a
    return {
        "status":  "ok",
        "command": "compare",
        "a": {
            "path":        str(Path(folder_a).resolve()),
            "total_files": count_a,
            "total_sec":   round(sec_a, 2),
            "duration":    format_duration(sec_a)["hours_fmt"],
        },
        "b": {
            "path":        str(Path(folder_b).resolve()),
            "total_files": count_b,
            "total_sec":   round(sec_b, 2),
            "duration":    format_duration(sec_b)["hours_fmt"],
        },
        "delta": {
            "seconds":  round(delta, 2),
            "duration": format_duration(abs(delta))["hours_fmt"],
            "sign":     "+" if delta >= 0 else "-",
            "files":    count_b - count_a,
        },
    }
