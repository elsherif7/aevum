import io
import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path

from ._models import ScanTree
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

def validate_export_path(out_path: str, scan_folder) -> Path:
    """Validate export destination — blocks system dirs, checks extension."""
    import os as _os
    resolved = Path(out_path).resolve()
    if not resolved.parent.exists():
        raise ValueError(f"Output directory does not exist: {resolved.parent}")
    system_roots = []
    if _os.name == "nt":
        win_dir = Path(_os.environ.get("SystemRoot", r"C:\Windows"))
        system_roots = [win_dir, Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]
    else:
        raw_roots = [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
                     Path("/lib"), Path("/lib64"), Path("/boot"), Path("/sys"), Path("/proc")]
        # On macOS, /etc /tmp /var are symlinks to /private/etc etc.
        # Resolve each root so the comparison works after Path.resolve().
        system_roots = []
        for r in raw_roots:
            system_roots.append(r)
            try:
                resolved_root = r.resolve()
                if resolved_root != r:
                    system_roots.append(resolved_root)
            except OSError:
                pass
    for sysroot in system_roots:
        if _is_relative_to(resolved, sysroot):
            raise PermissionError(f"Cannot write to system directory: {resolved}")
    allowed_extensions = {".txt", ".csv", ".json", ".html"}
    if resolved.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Invalid extension {resolved.suffix}. Allowed: {', '.join(sorted(allowed_extensions))}"
        )
    return resolved


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


def _tree_to_dict(name, seconds, count, children: list, direct_files=None):
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "direct":    [{"file": p.name, "seconds": round(s, 2)} for p, s in (direct_files or [])],
        "children":  [_tree_to_dict(n.name, n.total_sec, n.total_count, n.children, n.direct_files)
                      for n in children],
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
            print("  Falling back to safe location...", file=__import__('sys').stderr)
            # Fall through to auto-generate safe path

    # Auto-generate safe path
    if folder.parent.is_dir():
        preferred = folder.parent / filename
        if preferred.parent.is_dir():
            return preferred

    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop / filename


def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None, sizes=None):
    """
    Export scan results to a file with race-condition-safe atomic writes.
    fmt: 'txt' | 'csv' | 'json' | 'html'
    """
    dest = _resolve_dest(folder, fmt, out_path)
    if fmt == "html":
        content = _build_html(folder, total_sec, total_count, tree, durations, sizes or {})
    else:
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
    H-11: removed the Windows-specific dest.unlink() before os.replace() which
    created a TOCTOU window. os.replace() on modern Windows (Vista+) is atomic.
    """
    mode    = ('w', {'newline': '', 'encoding': 'utf-8'}) if fmt == "csv" else ('w', {'encoding': 'utf-8'})
    temp_fd, temp_path = tempfile.mkstemp(
        dir=dest.parent,
        prefix=f".{dest.stem}_",
        suffix=".tmp"
    )
    try:
        with os.fdopen(temp_fd, mode[0], **mode[1]) as f:
            if fmt == "csv":
                import csv as _csv
                writer = _csv.writer(f)
                for row in content:
                    writer.writerow(row)
            else:
                f.write(content)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, dest)   # atomic on all supported platforms
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _build_html(folder, total_sec, total_count, tree: ScanTree, durations, sizes):
    """Build a self-contained HTML report with collapsible tree and sortable table."""
    from html import escape
    folder   = Path(folder)
    stamp    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fd       = format_duration(total_sec)
    total_bytes = sum(sizes.values()) if sizes else 0

    # Build tree rows recursively using FolderNode objects
    def tree_rows(name, seconds, count, children: list, direct_files, depth=0):
        rows = []
        indent = "&nbsp;" * (depth * 4)
        fd_ = format_duration(seconds)
        rows.append(
            f'<tr><td>{indent}<b>{escape(name)}</b></td>'
            f'<td>{escape(fd_["hours_fmt"])}</td>'
            f'<td>{count}</td></tr>'
        )
        for node in children:
            rows.extend(tree_rows(node.name, node.total_sec, node.total_count,
                                  node.children, node.direct_files, depth + 1))
        return rows

    tree_html = "\n".join(tree_rows(folder.name, total_sec, total_count,
                                    tree.children, tree.direct_files))

    # Top files table
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
    file_rows = []
    for i, (path, sec) in enumerate(ranked, 1):
        fd_ = format_duration(sec)
        sz  = format_size(sizes.get(path, 0))
        file_rows.append(
            f'<tr><td>{i}</td><td>{escape(path.name)}</td>'
            f'<td>{escape(path.parent.name)}</td>'
            f'<td data-sec="{sec:.0f}">{escape(fd_["hours_fmt"])}</td>'
            f'<td>{escape(sz)}</td></tr>'
        )
    files_html = "\n".join(file_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<title>Aevum — {escape(folder.name)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; margin: 0; padding: 24px; }}
  h1 {{ color: #4fc3f7; margin-bottom: 4px; }}
  .meta {{ color: #888; font-size: 0.9em; margin-bottom: 24px; }}
  .card {{ background: #1a1a1a; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
  .card h2 {{ color: #4fc3f7; margin-top: 0; font-size: 1em; text-transform: uppercase; letter-spacing: 1px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
  .stat {{ background: #222; border-radius: 6px; padding: 12px; }}
  .stat-label {{ color: #888; font-size: 0.8em; }}
  .stat-value {{ color: #fff; font-size: 1.2em; font-weight: bold; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th {{ background: #222; color: #4fc3f7; padding: 8px 12px; text-align: left; cursor: pointer; user-select: none; }}
  th:hover {{ background: #2a2a2a; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #222; }}
  tr:hover td {{ background: #1e1e1e; }}
  .search {{ width: 100%; padding: 8px 12px; background: #222; border: 1px solid #333;
             border-radius: 6px; color: #e0e0e0; font-size: 0.9em; margin-bottom: 12px; box-sizing: border-box; }}
  .search:focus {{ outline: none; border-color: #4fc3f7; }}
</style>
</head>
<body>
<h1>{escape(folder.name)}</h1>
<div class="meta">Scanned: {escape(str(folder))} &nbsp;·&nbsp; {stamp} &nbsp;·&nbsp; Generated by Aevum</div>

<div class="card">
  <h2>Summary</h2>
  <div class="stats-grid">
    <div class="stat"><div class="stat-label">Total Files</div><div class="stat-value">{total_count:,}</div></div>
    <div class="stat"><div class="stat-label">Total Duration</div><div class="stat-value">{escape(fd["hours_fmt"])}</div></div>
    <div class="stat"><div class="stat-label">Days</div><div class="stat-value">{escape(fd["days_fmt"])}</div></div>
    <div class="stat"><div class="stat-label">Total Size</div><div class="stat-value">{escape(format_size(total_bytes))}</div></div>
  </div>
</div>

<div class="card">
  <h2>Folder Tree</h2>
  <table id="tree-table">
    <thead><tr><th onclick="sortTable('tree-table',0)">Folder</th><th onclick="sortTable('tree-table',1)">Duration</th><th onclick="sortTable('tree-table',2)">Files</th></tr></thead>
    <tbody>{tree_html}</tbody>
  </table>
</div>

<div class="card">
  <h2>All Files</h2>
  <input class="search" type="text" id="file-search" placeholder="Search files..." oninput="filterTable()">
  <table id="file-table">
    <thead><tr><th>#</th><th onclick="sortTable('file-table',1)">File</th><th onclick="sortTable('file-table',2)">Folder</th><th onclick="sortTable('file-table',3)">Duration</th><th onclick="sortTable('file-table',4)">Size</th></tr></thead>
    <tbody id="file-tbody">{files_html}</tbody>
  </table>
</div>

<script>
function sortTable(id, col) {{
  const t = document.getElementById(id);
  const rows = Array.from(t.tBodies[0].rows);
  const asc = t.dataset.sortCol == col && t.dataset.sortDir == 'asc';
  rows.sort((a, b) => {{
    const av = a.cells[col]?.dataset.sec ? parseFloat(a.cells[col].dataset.sec)
             : a.cells[col]?.innerText.trim() || '';
    const bv = b.cells[col]?.dataset.sec ? parseFloat(b.cells[col].dataset.sec)
             : b.cells[col]?.innerText.trim() || '';
    if (typeof av === 'number') return asc ? bv - av : av - bv;
    return asc ? bv.localeCompare(av) : av.localeCompare(bv);
  }});
  rows.forEach(r => t.tBodies[0].appendChild(r));
  t.dataset.sortCol = col; t.dataset.sortDir = asc ? 'desc' : 'asc';
}}
function filterTable() {{
  const q = document.getElementById('file-search').value.toLowerCase();
  document.querySelectorAll('#file-tbody tr').forEach(r => {{
    r.style.display = r.innerText.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""


def _build_content(folder, total_sec, total_count, tree: ScanTree, durations, fmt, sizes=None):
    """Build export content string (or row list for CSV) without touching disk."""
    folder = Path(folder)

    if fmt == "html":
        return _build_html(folder, total_sec, total_count, tree, durations, sizes or {})

    if fmt == "json":
        root_name  = folder.name
        payload = {
            "scanned":     str(folder),
            "timestamp":   datetime.now().isoformat(),
            "total_count": total_count,
            "total_sec":   round(total_sec, 2),
            "totals":      format_duration(total_sec),
            "tree":        _tree_to_dict(root_name, total_sec, total_count,
                                         tree.children, tree.direct_files),
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
                round(sec, 2),
                sanitize_csv_field(format_duration(sec)["hours_fmt"]),
            ])
        return rows

    # fmt == "txt"
    buf = io.StringIO()
    fd  = format_duration(total_sec)
    buf.write("AEVUM  |  Media Library Scanner\n")
    buf.write(f"Scanned : {folder}\n")
    buf.write(f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    buf.write("=" * 64 + "\n\n")

    def write_tree(name, seconds, count, children: list, direct_files=None, depth=0, number=""):
        indent = "    " * depth
        label  = f"{number}.  {name}" if number else name
        fd_    = format_duration(seconds)
        if count == 0:
            buf.write(f"{indent}{label}\n")
            buf.write(f"{indent}    +--  (empty)\n")
        else:
            buf.write(f"{indent}{label}\n")
            buf.write(f"{indent}    +--  {fd_['hours_fmt']}  |  {count} files\n")
        for path, sec in (direct_files or []):
            buf.write(f"{indent}    |  {format_duration(sec)['hours_fmt']}  {path.name}\n")
        if children:
            buf.write("\n")
        for i, node in enumerate(children, start=1):
            sub_number = f"{number}.{i}" if number else str(i)
            write_tree(node.name, node.total_sec, node.total_count,
                       node.children, node.direct_files, depth + 1, sub_number)
        if children:
            buf.write("\n")

    write_tree(folder.name, total_sec, total_count, tree.children, tree.direct_files)
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

    S-06 fix: validate output path to prevent arbitrary file writes.
    """
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe     = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    random_suffix = secrets.token_hex(4)
    filename = f"aevum_yt_{safe}_{stamp}_{random_suffix}.{fmt}"

    if out_path:
        # S-06: validate output path
        try:
            dest = validate_export_path(out_path, Path.home())
        except (PermissionError, ValueError) as e:
            print(f"  Warning: {e}", file=__import__('sys').stderr)
            print("  Falling back to safe location...", file=__import__('sys').stderr)
            desktop = Path.home() / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            dest = desktop / filename
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
