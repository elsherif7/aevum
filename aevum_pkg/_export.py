import csv
import io
import json
from datetime import datetime
from pathlib import Path

from ._scan import format_duration, format_size


def _tree_to_dict(name, seconds, count, subfolders, direct=None):
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "direct":    [{"file": p.name, "seconds": round(s, 2)} for p, s in (direct or [])],
        "children":  [_tree_to_dict(n, s, c, sub, d) for n, s, c, _fb, _dc, sub, d in subfolders],
    }


def _resolve_dest(folder, fmt, out_path):
    """
    Resolve the destination path for an export.

    Issue 29 fix: removed the touch()+unlink() write-permission test that
    created a TOCTOU race.  Instead we just return the preferred path and let
    the actual write() call raise if something is wrong; callers catch OSError
    and fall back to the Desktop.
    """
    folder   = Path(folder)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"aevum_{folder.name}_{stamp}.{fmt}"

    if out_path:
        return Path(out_path)

    preferred = folder.parent / filename
    # Only use the sibling path if the parent directory actually exists.
    if preferred.parent.is_dir():
        return preferred

    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop / filename


def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None):
    """
    Export scan results to a file.
    fmt: 'txt' | 'csv' | 'json'
    out_path: explicit Path or None to auto-generate next to the scan folder.
    Returns the Path that was written.

    Issue 29 fix: the old code did touch()+unlink() to probe writeability,
    creating a TOCTOU race.  Now we attempt the real write directly, and fall
    back to the Desktop only if that raises OSError.
    """
    dest    = _resolve_dest(folder, fmt, out_path)
    content = _build_content(folder, total_sec, total_count, tree, durations, fmt)

    try:
        _write_content(dest, content, fmt)
    except OSError:
        # Fall back to Desktop on permission or path errors.
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest     = desktop / f"aevum_{Path(folder).name}_{stamp}.{fmt}"
        _write_content(dest, content, fmt)

    return dest


def _write_content(dest, content, fmt):
    """Write pre-built content to dest. Raises OSError on failure."""
    if fmt == "csv":
        # content is a list of rows for csv
        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in content:
                writer.writerow(row)
    else:
        dest.write_text(content, encoding="utf-8")


def _build_content(folder, total_sec, total_count, tree, durations, fmt):
    """Build export content string (or row list for CSV) without touching disk."""
    folder = Path(folder)

    if fmt == "json":
        root_name  = folder.name
        subfolders, direct, _root_bytes = tree
        payload = {
            "scanned":     str(folder),
            "timestamp":   datetime.now().isoformat(),
            "total_count": total_count,
            "total_sec":   round(total_sec, 2),
            "totals":      format_duration(total_sec),
            "tree":        _tree_to_dict(root_name, total_sec, total_count, subfolders, direct),
            "files":       {str(p): round(s, 2) for p, s in
                            sorted(durations.items(), key=lambda x: x[1], reverse=True)},
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    if fmt == "csv":
        ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
        rows   = [["path", "filename", "folder", "seconds", "duration"]]
        for path, sec in ranked:
            rows.append([
                str(path),
                path.name,
                path.parent.name,
                round(sec, 2),
                format_duration(sec)["hours_fmt"],
            ])
        return rows  # returned as list; _write_content handles csv.writer

    # fmt == "txt"
    buf = io.StringIO()
    fd  = format_duration(total_sec)
    buf.write("AEVUM  |  Media Library Scanner\n")
    buf.write(f"Scanned : {folder}\n")
    buf.write(f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    buf.write("=" * 64 + "\n\n")

    def write_tree(name, seconds, count, subfolders, direct=None, depth=0, number=""):
        indent = "    " * depth
        label  = f"{number}.  {name}" if number else name
        fd_    = format_duration(seconds)
        if count == 0:
            buf.write(f"{indent}{label}\n")
            buf.write(f"{indent}    +--  (empty)\n")
        else:
            buf.write(f"{indent}{label}\n")
            buf.write(f"{indent}    +--  {fd_['hours_fmt']}  |  {count} files\n")
        for path, sec in (direct or []):
            buf.write(f"{indent}    |  {format_duration(sec)['hours_fmt']}  {path.name}\n")
        if subfolders:
            buf.write("\n")
        for i, (sn, ss, sc, _fb, _dc, ssub, sd) in enumerate(subfolders, start=1):
            sub_number = f"{number}.{i}" if number else str(i)
            write_tree(sn, ss, sc, ssub, sd, depth + 1, sub_number)
        if subfolders:
            buf.write("\n")

    subfolders, direct, _root_bytes = tree
    write_tree(folder.name, total_sec, total_count, subfolders, direct)
    buf.write("=" * 64 + "\n")
    buf.write("GRAND TOTAL\n")
    buf.write("=" * 64 + "\n")
    buf.write(f"Total files   :  {total_count}\n")
    buf.write(f"Days          :  {fd['days_fmt']}\n")
    buf.write(f"Hours         :  {fd['hours_fmt']}\n")
    buf.write(f"Minutes       :  {fd['minutes_fmt']}\n")
    buf.write("=" * 64 + "\n\n")
    buf.write("TOP 10 LONGEST FILES\n")
    buf.write("=" * 64 + "\n")
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:10]
    for i, (path, sec) in enumerate(ranked, start=1):
        buf.write(f"  {i:>2}.  {format_duration(sec)['hours_fmt']}  |  {path.name}  ({path.parent.name})\n")
    return buf.getvalue()


def export_url_results(url, label, total_sec, total_count, entries, fmt, out_path=None):
    """
    Export YouTube scan results to a file.

    Issue 30 fix: previously all URL exports were written as plain text
    regardless of the requested format.  Now txt, csv, and json are all
    properly implemented to match the behaviour of export_results() for
    local folder scans.
    """
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe     = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    filename = f"aevum_yt_{safe}_{stamp}.{fmt}"

    if out_path:
        dest = Path(out_path)
    else:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        dest = desktop / filename

    content = _build_url_content(url, label, total_sec, total_count, entries, fmt)

    try:
        _write_content(dest, content, fmt)
    except OSError:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        dest    = desktop / filename
        _write_content(dest, content, fmt)

    return dest


def _build_url_content(url, label, total_sec, total_count, entries, fmt):
    """Build export content for a YouTube scan."""
    if fmt == "json":
        payload = {
            "url":         url,
            "label":       label,
            "timestamp":   datetime.now().isoformat(),
            "total_count": total_count,
            "total_sec":   round(total_sec, 2),
            "totals":      format_duration(total_sec),
            "videos": [
                {
                    "title":        e.get("title", ""),
                    "duration_sec": round(e.get("duration", 0.0), 2),
                    "duration_fmt": format_duration(e.get("duration", 0.0))["hours_fmt"],
                    "url":          e.get("url", ""),
                    "channel":      e.get("channel", ""),
                }
                for e in sorted(entries, key=lambda x: x.get("duration", 0.0), reverse=True)
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    if fmt == "csv":
        ranked = sorted(entries, key=lambda x: x.get("duration", 0.0), reverse=True)
        rows   = [["title", "channel", "seconds", "duration", "url"]]
        for e in ranked:
            rows.append([
                e.get("title", ""),
                e.get("channel", ""),
                round(e.get("duration", 0.0), 2),
                format_duration(e.get("duration", 0.0))["hours_fmt"],
                e.get("url", ""),
            ])
        return rows

    # fmt == "txt"
    buf = io.StringIO()
    fd  = format_duration(total_sec)
    buf.write(f"AEVUM  |  {label}\n")
    buf.write(f"URL   : {url}\n")
    buf.write(f"Date  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    buf.write("=" * 64 + "\n\n")
    buf.write(f"Total videos  :  {total_count}\n")
    buf.write(f"Days          :  {fd['days_fmt']}\n")
    buf.write(f"Hours         :  {fd['hours_fmt']}\n")
    buf.write(f"Minutes       :  {fd['minutes_fmt']}\n")
    buf.write("=" * 64 + "\n\n")
    buf.write("TOP 10 LONGEST VIDEOS\n")
    buf.write("=" * 64 + "\n")
    ranked = sorted(entries, key=lambda x: x.get("duration", 0.0), reverse=True)[:10]
    for i, e in enumerate(ranked, start=1):
        dur = format_duration(e.get("duration", 0.0))["hours_fmt"]
        buf.write(f"  {i:>2}.  {dur}  |  {e.get('title', '')[:60]}\n")
    return buf.getvalue()
