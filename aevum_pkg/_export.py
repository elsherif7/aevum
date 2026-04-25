import csv
import io
import json
from datetime import datetime
from pathlib import Path

from ._scan import format_duration


def _tree_to_dict(name, seconds, count, subfolders, direct=None):
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "direct":    [{"file": p.name, "seconds": round(s, 2)} for p, s in (direct or [])],
        "children":  [_tree_to_dict(n, s, c, sub, d) for n, s, c, _fb, _dc, sub, d in subfolders],
    }


def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None):
    """
    Export scan results to a file.
    fmt: 'txt' | 'csv' | 'json'
    out_path: explicit Path or None to auto-generate next to the scan folder.
    Returns the Path that was written.
    """
    folder   = Path(folder)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"aevum_{folder.name}_{stamp}.{fmt}"

    if out_path:
        dest = Path(out_path)
    else:
        preferred = folder.parent / filename
        try:
            preferred.parent.stat()
            preferred.touch()
            preferred.unlink()
            dest = preferred
        except OSError:
            desktop = Path.home() / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            dest = desktop / filename

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
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    elif fmt == "csv":
        ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "filename", "folder", "seconds", "duration"])
            for path, sec in ranked:
                writer.writerow([
                    str(path),
                    path.name,
                    path.parent.name,
                    round(sec, 2),
                    format_duration(sec)["hours_fmt"],
                ])

    elif fmt == "txt":
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
        dest.write_text(buf.getvalue(), encoding="utf-8")

    return dest
