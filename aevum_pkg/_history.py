"""
Scan history and diff for Aevum.

Every time a folder is scanned, a snapshot is saved to the history store.
`aevum history <path>` lists past scans.
`aevum diff <path>` compares the latest scan to the previous one.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from ._color import clr, LINE
from ._scan  import format_duration, format_size
from ._paths import APPDATA

HISTORY_DIR = APPDATA / "history"


def _history_key(folder: Path) -> str:
    """Stable filename key for a folder path."""
    import hashlib
    norm = str(folder.resolve()).lower() if os.name == "nt" else str(folder.resolve())
    return hashlib.blake2b(norm.encode(), digest_size=8).hexdigest()


def _history_file(folder: Path) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{_history_key(folder)}.json"


def load_history(folder: Path) -> list:
    """Return list of snapshot dicts, oldest first. Empty list on error."""
    f = _history_file(folder)
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_snapshot(folder: Path, total_sec: float, total_count: int,
                  total_bytes: int, durations: dict):
    """
    Append a snapshot to the history for this folder.
    Keeps the last 50 snapshots to cap disk usage.
    Writes atomically.
    """
    history = load_history(folder)
    snapshot = {
        "ts":          int(time.time()),
        "total_sec":   round(total_sec, 2),
        "total_count": total_count,
        "total_bytes": total_bytes,
        # Store just the filenames + durations (not full paths) to keep size small
        "files": {
            Path(p).name: round(s, 2)
            for p, s in durations.items()
        },
    }
    history.append(snapshot)
    history = history[-50:]   # keep last 50

    f = _history_file(folder)
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=HISTORY_DIR, suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(history, separators=(",", ":")))
        os.replace(tmp_path, f)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def print_history(folder: Path):
    """Print a table of past scans for this folder."""
    history = load_history(folder)
    if not history:
        print(f"\n  {clr.Y}No scan history for{clr.RST}  {clr.W}{folder.name}{clr.RST}\n")
        print(f"  Run  {clr.W}aevum scan {folder}{clr.RST}  to start recording history.\n")
        return

    import datetime
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Scan History{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}{folder.name}{clr.RST}  "
          f"{clr.DIM}({len(history)} snapshots){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    for i, snap in enumerate(reversed(history), 1):
        dt  = datetime.datetime.fromtimestamp(snap["ts"]).strftime("%Y-%m-%d %H:%M")
        dur = format_duration(snap["total_sec"])["hours_fmt"]
        sz  = format_size(snap["total_bytes"])
        cnt = snap["total_count"]
        age = "#latest" if i == 1 else f"#{i}"
        print(f"  {clr.DIM}{age:>8}{clr.RST}  {clr.W}{dt}{clr.RST}  "
              f"{clr.Y}{cnt:>5} files{clr.RST}  {clr.DIM}|{clr.RST}  "
              f"{clr.W}{dur}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{sz}{clr.RST}")
    print()


def print_diff(folder: Path):
    """Compare the two most recent snapshots and show what changed."""
    history = load_history(folder)
    if len(history) < 2:
        print(f"\n  {clr.Y}Need at least 2 scans to diff.{clr.RST}  "
              f"Only {len(history)} snapshot(s) found for {clr.W}{folder.name}{clr.RST}.\n")
        return

    import datetime
    prev = history[-2]
    curr = history[-1]

    prev_dt = datetime.datetime.fromtimestamp(prev["ts"]).strftime("%Y-%m-%d %H:%M")
    curr_dt = datetime.datetime.fromtimestamp(curr["ts"]).strftime("%Y-%m-%d %H:%M")

    prev_files = set(prev.get("files", {}).keys())
    curr_files = set(curr.get("files", {}).keys())

    added   = sorted(curr_files - prev_files)
    removed = sorted(prev_files - curr_files)

    delta_sec   = curr["total_sec"]   - prev["total_sec"]
    delta_count = curr["total_count"] - prev["total_count"]
    delta_bytes = curr["total_bytes"] - prev["total_bytes"]
    delta_sign  = "+" if delta_sec >= 0 else ""
    delta_col   = clr.G if delta_sec >= 0 else clr.R

    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Diff{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}{folder.name}{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.DIM}  From:{clr.RST}  {clr.W}{prev_dt}{clr.RST}  "
          f"{clr.DIM}({prev['total_count']} files, "
          f"{format_duration(prev['total_sec'])['hours_fmt']}){clr.RST}")
    print(f"  {clr.DIM}  To:  {clr.RST}  {clr.W}{curr_dt}{clr.RST}  "
          f"{clr.DIM}({curr['total_count']} files, "
          f"{format_duration(curr['total_sec'])['hours_fmt']}){clr.RST}")
    print()

    csign = "+" if delta_count >= 0 else ""
    bsign = "+" if delta_bytes >= 0 else ""
    print(f"  {clr.W}  Duration  {clr.DIM}:{clr.RST}  "
          f"{delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Files     {clr.DIM}:{clr.RST}  "
          f"{delta_col}{csign}{delta_count}{clr.RST}")
    print(f"  {clr.W}  Size      {clr.DIM}:{clr.RST}  "
          f"{delta_col}{bsign}{format_size(abs(delta_bytes))}{clr.RST}")
    print()

    if added:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.G}  Added ({len(added)}){clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for name in added[:30]:
            dur = format_duration(curr["files"].get(name, 0))["hours_fmt"]
            print(f"    {clr.G}+{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}{dur}{clr.RST}")
        if len(added) > 30:
            print(f"    {clr.DIM}... and {len(added) - 30} more{clr.RST}")
        print()

    if removed:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.R}  Removed ({len(removed)}){clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for name in removed[:30]:
            dur = format_duration(prev["files"].get(name, 0))["hours_fmt"]
            print(f"    {clr.R}-{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}{dur}{clr.RST}")
        if len(removed) > 30:
            print(f"    {clr.DIM}... and {len(removed) - 30} more{clr.RST}")
        print()

    if not added and not removed:
        print(f"  {clr.G}  No file changes between these two scans.{clr.RST}\n")


def history_to_json(folder: Path) -> dict:
    history = load_history(folder)
    return {
        "status":    "ok",
        "command":   "history",
        "path":      str(folder.resolve()),
        "snapshots": history,
    }


def diff_to_json(folder: Path) -> dict:
    history = load_history(folder)
    if len(history) < 2:
        return {"status": "error", "error": "Need at least 2 snapshots to diff"}
    prev, curr = history[-2], history[-1]
    prev_files = set(prev.get("files", {}).keys())
    curr_files = set(curr.get("files", {}).keys())
    return {
        "status":       "ok",
        "command":      "diff",
        "path":         str(folder.resolve()),
        "from_ts":      prev["ts"],
        "to_ts":        curr["ts"],
        "delta_sec":    round(curr["total_sec"] - prev["total_sec"], 2),
        "delta_count":  curr["total_count"] - prev["total_count"],
        "delta_bytes":  curr["total_bytes"] - prev["total_bytes"],
        "added":        sorted(curr_files - prev_files),
        "removed":      sorted(prev_files - curr_files),
    }
