import csv
import io
import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path

from ._scan import format_duration, format_size
# ── Path validation (inlined from _security.py) ──────────────────────
def _is_relative_to(path, root):
    try:
        return path.is_relative_to(root)
    except AttributeError:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

def validate_export_path(out_path: str, scan_folder) -> "Path":
    """Validate export destination — blocks system dirs, checks extension."""
    import os as _os
    out_path = Path(out_path).resolve()
    if not out_path.parent.exists():
        raise ValueError(f"Output directory does not exist: {out_path.parent}")
    system_roots = []
    if _os.name == "nt":
        win_dir = Path(_os.environ.get("SystemRoot", r"C:\Windows"))
        system_roots = [win_dir, Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]
    else:
        system_roots = [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
                        Path("/lib"), Path("/lib64"), Path("/boot"), Path("/sys"), Path("/proc")]
    for sysroot in system_roots:
        if _is_relative_to(out_path, sysroot):
            raise PermissionError(f"Cannot write to system directory: {out_path}")
    allowed_extensions = {".txt", ".csv", ".json"}
    if out_path.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Invalid extension {out_path.suffix}. Allowed: {', '.join(sorted(allowed_extensions))}"
        )
    return out_path


def sanitize_csv_field(value: str) -> str:
    """
    Sanitize CSV field to prevent formula injection attacks.
    
    Security: Escapes formula characters that could execute in Excel/Google Sheets.
    Formulas starting with =, +, -, @, |, or tab could execute commands.
    """
    if not value:
        return value
    
    # Characters that start formulas in spreadsheets
    dangerous_chars = {'=', '+', '-', '@', '\t', '\r', '\n', '|'}
    
    # If starts with dangerous char, prefix with single quote
    if value[0] in dangerous_chars:
        value = "'" + value
    
    # Remove any embedded nulls
    value = value.replace('\x00', '')
    
    # Remove control characters except common whitespace
    value = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', value)
    
    return value


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
    Resolve the destination path for an export with security validation.
    
    Security: Validates output path to prevent path traversal and arbitrary
    file writes. Uses unpredictable names to prevent symlink attacks.
    """
    folder = Path(folder).resolve()
    
    # Add random suffix for unpredictability (prevents symlink timing attacks)
    random_suffix = secrets.token_hex(4)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"aevum_{folder.name}_{stamp}_{random_suffix}.{fmt}"
    
    if out_path:
        # Security: validate output path
        try:
            validated_path = validate_export_path(out_path, folder)
            return validated_path
        except (PermissionError, ValueError) as e:
            print(f"  Warning: {e}", file=__import__('sys').stderr)
            print(f"  Falling back to safe location...", file=__import__('sys').stderr)
            # Fall through to auto-generate safe path
    
    # Auto-generate safe path
    if folder.parent.is_dir():
        preferred = folder.parent / filename
        if preferred.parent.is_dir():
            return preferred
    
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop / filename


def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None):
    """
    Export scan results to a file with race-condition-safe atomic writes.
    
    fmt: 'txt' | 'csv' | 'json'
    out_path: explicit Path or None to auto-generate next to the scan folder.
    Returns the Path that was written.
    
    Security: Uses atomic writes via temp file + rename to prevent TOCTOU races.
    """
    dest = _resolve_dest(folder, fmt, out_path)
    content = _build_content(folder, total_sec, total_count, tree, durations, fmt)
    
    try:
        _write_content_atomic(dest, content, fmt)
    except OSError:
        # Fall back to Desktop on permission or path errors
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        random_suffix = secrets.token_hex(4)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = desktop / f"aevum_{Path(folder).name}_{stamp}_{random_suffix}.{fmt}"
        _write_content_atomic(dest, content, fmt)
    
    return dest


def _write_content_atomic(dest, content, fmt):
    """
    Write content atomically using temp file + rename.
    
    Security: Prevents TOCTOU races and sets restrictive permissions (user-only).
    """
    if fmt == "csv":
        # For CSV, write atomically via temp file
        temp_fd, temp_path = tempfile.mkstemp(
            dir=dest.parent,
            prefix=f".{dest.name}_",
            suffix=".tmp"
        )
        
        try:
            with os.fdopen(temp_fd, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for row in content:
                    writer.writerow(row)
            
            # Set restrictive permissions before moving
            os.chmod(temp_path, 0o600)
            
            # Atomic rename
            if os.name == 'nt' and dest.exists():
                dest.unlink()
            os.replace(temp_path, dest)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    else:
        # For text/JSON, write atomically via temp file
        temp_fd, temp_path = tempfile.mkstemp(
            dir=dest.parent,
            prefix=f".{dest.name}_",
            suffix=".tmp"
        )
        
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Set restrictive permissions
            os.chmod(temp_path, 0o600)
            
            # Atomic rename
            if os.name == 'nt' and dest.exists():
                dest.unlink()
            os.replace(temp_path, dest)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise


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
                sanitize_csv_field(str(path)),
                sanitize_csv_field(path.name),
                sanitize_csv_field(path.parent.name),
                round(sec, 2),  # Numbers are safe
                sanitize_csv_field(format_duration(sec)["hours_fmt"]),
            ])
        return rows  # returned as list; _write_content_atomic handles csv.writer

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
        _write_content_atomic(dest, content, fmt)
    except OSError:
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        dest    = desktop / filename
        _write_content_atomic(dest, content, fmt)

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
                sanitize_csv_field(e.get("title", "")),
                sanitize_csv_field(e.get("channel", "")),
                round(e.get("duration", 0.0), 2),  # Numbers are safe
                sanitize_csv_field(format_duration(e.get("duration", 0.0))["hours_fmt"]),
                sanitize_csv_field(e.get("url", "")),
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
