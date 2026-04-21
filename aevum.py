import argparse
import csv
import hashlib
import json
import struct
import subprocess
import sys
import os
import threading
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Enable ANSI colors on Windows
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

R   = "\033[91m"
G   = "\033[92m"
Y   = "\033[93m"
B   = "\033[94m"
M   = "\033[95m"
C   = "\033[96m"
W   = "\033[97m"
DIM = "\033[2m"
RST = "\033[0m"

video_extensions = (
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv',
    '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
    '.vob', '.ogv', '.divx', '.rmvb', '.asf', '.m2ts'
)

# How many ffprobe processes to run at once.
# Capped at 32 — beyond that, disk I/O becomes the bottleneck on HDDs/network shares.
MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "cache"

# ── CACHE ─────────────────────────────────────────────────────────────

def _cache_key(root):
    """Stable filename for the cache of a given root folder."""
    h = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"

def load_cache(root):
    """
    Load the cache for this root folder.
    Returns a dict mapping absolute path string -> {mtime, size, duration}.
    Returns {} if no cache exists or it is unreadable.
    """
    path = _cache_key(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {entry["path"]: entry for entry in data}
    except Exception:
        return {}

def save_cache(root, durations):
    """
    Persist durations to the cache file for this root folder.
    durations: dict mapping Path -> seconds (float)
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for p, sec in durations.items():
            try:
                st = p.stat()
                entries.append({
                    "path":     str(p.resolve()),
                    "mtime":    st.st_mtime,
                    "size":     st.st_size,
                    "duration": sec,
                })
            except OSError:
                pass
        _cache_key(root).write_text(
            json.dumps(entries, indent=None, separators=(',', ':')),
            encoding="utf-8"
        )
    except Exception:
        pass  # cache write failure is never fatal

# ── HELPERS ───────────────────────────────────────────────────────────

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def check_ffprobe():
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except FileNotFoundError:
        return False

def _read_mp4_duration(path):
    """Seek through MP4 atoms without reading full file into memory."""
    try:
        file_size = os.path.getsize(path)
        with open(path, 'rb') as f:
            def read_atom(limit_end):
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None, None, 0
                size = struct.unpack('>I', hdr[:4])[0]
                name = hdr[4:8]
                if size == 1:  # 64-bit size
                    ext = f.read(8)
                    if len(ext) < 8:
                        return None, None, 0
                    size = struct.unpack('>Q', ext)[0]
                    header_size = 16
                else:
                    header_size = 8
                if size == 0:
                    size = limit_end - (f.tell() - header_size)
                return name, size, header_size

            pos = 0
            while pos < file_size:
                f.seek(pos)
                name, size, hdr_size = read_atom(file_size)
                if name is None or size < hdr_size:
                    break
                if name == b'moov':
                    # enter moov, search for mvhd
                    moov_end = pos + size
                    inner = pos + hdr_size
                    while inner < moov_end:
                        f.seek(inner)
                        iname, isize, ihdr = read_atom(moov_end)
                        if iname is None or isize < ihdr:
                            break
                        if iname == b'mvhd':
                            box = f.read(min(isize - ihdr, 40))
                            if not box:
                                break
                            version = box[0]
                            if version == 1:
                                ts = struct.unpack_from('>I', box, 20)[0]
                                dur = struct.unpack_from('>Q', box, 24)[0]
                            else:
                                ts = struct.unpack_from('>I', box, 12)[0]
                                dur = struct.unpack_from('>I', box, 16)[0]
                            return dur / ts if ts else 0.0
                        inner += isize
                    break
                pos += size
    except Exception:
        pass
    return None

def _read_mkv_duration(path):
    """Read duration from MKV/WEBM by scanning EBML for the Segment/Info block."""
    try:
        with open(path, 'rb') as f:
            data = f.read(min(2 * 1024 * 1024, os.path.getsize(path)))

        def read_vint(buf, pos):
            if pos >= len(buf):
                return 0, pos + 1
            b = buf[pos]
            if b == 0:
                return 0, len(buf)  # invalid/reserved vint — signal parse failure
            width = 1
            mask = 0x80
            while not (b & mask) and width <= 8:
                width += 1
                mask >>= 1
            val = b & (mask - 1)
            for k in range(1, width):
                if pos + k >= len(buf):
                    break
                val = (val << 8) | buf[pos + k]
            return val, pos + width

        def read_id(buf, pos):
            if pos >= len(buf):
                return 0, pos + 1
            b = buf[pos]
            width = 1
            mask = 0x80
            while not (b & mask) and width <= 4:
                width += 1
                mask >>= 1
            val = int.from_bytes(buf[pos:pos+width], 'big')
            return val, pos + width

        timescale_ns = 1_000_000
        i = 0
        while i < len(data) - 4:
            eid, i = read_id(data, i)
            esize, i = read_vint(data, i)
            if eid == 0x1549A966:  # Info
                end = i + esize
                j = i
                duration = None
                while j < end - 4:
                    fid, j = read_id(data, j)
                    fsize, j = read_vint(data, j)
                    if fid == 0x2AD7B1:
                        timescale_ns = int.from_bytes(data[j:j+fsize], 'big')
                    elif fid == 0x4489:
                        raw = data[j:j+fsize]
                        duration = struct.unpack('>f', raw)[0] if fsize == 4 else struct.unpack('>d', raw)[0]
                    j += fsize
                if duration is not None:
                    return duration * timescale_ns / 1_000_000_000
                return None
            elif 0 < esize < 0x100000:
                i += esize
            else:
                i += 1
    except Exception:
        pass
    return None

def get_duration(path):
    """Try fast native parse first; fall back to ffprobe if needed."""
    ext = Path(path).suffix.lower()
    result = None
    if ext in ('.mp4', '.mov', '.m4v', '.3gp'):
        result = _read_mp4_duration(path)
    elif ext in ('.mkv', '.webm'):
        result = _read_mkv_duration(path)
    if result is not None and result > 0:
        return result
    # fallback to ffprobe for unsupported/failed formats
    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, text=True, timeout=15
        )
        val = proc.stdout.strip()
        return float(val) if val and val != 'N/A' else 0.0
    except Exception:
        return 0.0

def format_duration(seconds):
    days    = int(seconds // 86400)
    hours   = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    return {
        "days_fmt":    f"{days}d {hours:02}h {minutes:02}m {secs:02}s",
        "hours_fmt":   f"{int(seconds // 3600):02}h {minutes:02}m {secs:02}s",
        "minutes_fmt": f"{int(seconds // 60)}m {secs:02}s",
    }

def scan_parallel(root, on_progress=None, stop_event=None, sort_by="name", cache=None):
    """Pipelined scan: files are submitted to probe pool as they are discovered."""
    root      = Path(root)
    durations = {}
    done      = 0
    total     = 0
    hits      = 0   # files served from cache
    lock      = threading.Lock()
    cache     = cache or {}

    def probe(path):
        nonlocal done, hits
        if stop_event and stop_event.is_set():
            return path, 0.0

        # Check cache: match on both mtime and size to detect re-encoded files
        key = str(path.resolve())
        if key in cache:
            try:
                st = path.stat()
                entry = cache[key]
                if st.st_mtime == entry["mtime"] and st.st_size == entry["size"]:
                    with lock:
                        done += 1
                        hits += 1
                        if on_progress and total > 0:
                            on_progress(done, total)
                    return path, entry["duration"]
            except OSError:
                pass

        sec = get_duration(path)
        with lock:
            done += 1
            if on_progress and total > 0:
                on_progress(done, total)
        return path, sec

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}

        def collect_and_submit():
            nonlocal total
            stack = [str(root)]
            while stack:
                if stop_event and stop_event.is_set():
                    break
                current = stack.pop()
                try:
                    with os.scandir(current) as it:
                        for entry in it:
                            if stop_event and stop_event.is_set():
                                return
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                if Path(entry.name).suffix.lower() in video_extensions:
                                    p = Path(entry.path)
                                    with lock:
                                        total += 1
                                    f = pool.submit(probe, p)
                                    futures[f] = p
                except PermissionError:
                    pass

        collector = threading.Thread(target=collect_and_submit, daemon=True)
        collector.start()
        collector.join()

        try:
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    break
                path, sec = future.result()
                durations[path] = sec
        except KeyboardInterrupt:
            if stop_event:
                stop_event.set()
            raise

    if not durations:
        return 0.0, 0, _build_tree(root, {}, sort_by), {}, 0

    total_sec   = sum(durations.values())
    total_count = len(durations)
    tree        = _build_tree(root, durations, sort_by)

    return total_sec, total_count, tree, durations, hits

def _build_tree(root, durations, sort_by="name"):
    """
    O(n) tree builder — aggregate folder stats in a single pass over durations,
    then recursively assemble the tree structure from the pre-built dict.
    sort_by: 'name' | 'duration' | 'count'
    """
    root = Path(root)

    # Single pass: bucket every file's duration into its parent folder and all ancestors
    folder_secs  = {}   # folder path -> total seconds
    folder_count = {}   # folder path -> video count

    for path, sec in durations.items():
        parent = path.parent
        while parent != parent.parent:  # stops at filesystem root (/ or C:\)
            folder_secs[parent]  = folder_secs.get(parent, 0.0) + sec
            folder_count[parent] = folder_count.get(parent, 0) + 1
            if parent == root:
                break
            parent = parent.parent

    def build(node):
        subfolders = []
        try:
            children = list(p for p in node.iterdir() if p.is_dir())
        except PermissionError:
            return subfolders

        if sort_by == "duration":
            children.sort(key=lambda p: folder_secs.get(p, 0.0), reverse=True)
        elif sort_by == "count":
            children.sort(key=lambda p: folder_count.get(p, 0), reverse=True)
        else:  # name (default)
            children.sort()

        for child in children:
            secs  = folder_secs.get(child, 0.0)
            count = folder_count.get(child, 0)
            subfolders.append((child.name, secs, count, build(child)))
        return subfolders

    return build(root)

depth_colors = [R, G, B, M, C, W]

def print_tree(name, seconds, count, subfolders, depth=0, number="", max_depth=50):
    if depth > max_depth:
        return
    PAD    = "    "
    indent = PAD * depth
    fmt    = format_duration(seconds)
    col    = depth_colors[depth % len(depth_colors)]
    label  = f"{number}.  {name}" if number else name

    if count == 0:
        print(f"{indent}{col}{label}{RST}")
        print(f"{indent}    {DIM}+--  (empty){RST}")
    else:
        print(f"{indent}{col}{label}{RST}")
        print(f"{indent}    {DIM}+--{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{count} videos{RST}")

    if subfolders:
        print()
    for i, (sub_name, sub_sec, sub_count, sub_sub) in enumerate(subfolders, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        print_tree(sub_name, sub_sec, sub_count, sub_sub, depth + 1, sub_number)
    if subfolders:
        print()

def print_top_files(durations, n=10):
    """Print the N longest individual video files."""
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Top {n} Longest Files{RST}")
    print(f"  {C}{LINE}{RST}")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt = format_duration(sec)
        name = path.name
        parent = path.parent.name
        print(f"  {DIM}{i:>2}.{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{name}{RST}  {DIM}({parent}){RST}")
    print()

def _tree_to_dict(name, seconds, count, subfolders):
    """Recursively convert a tree tuple into a JSON-serialisable dict."""
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "children":  [_tree_to_dict(n, s, c, sub) for n, s, c, sub in subfolders],
    }

def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None):
    """
    Export scan results to a file.
    fmt: 'txt' | 'csv' | 'json'
    out_path: explicit Path to write to, or None to auto-generate next to the scan folder.
    Returns the Path that was written.
    """
    folder   = Path(folder)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"aevum_{folder.name}_{stamp}.{fmt}"
    dest     = Path(out_path) if out_path else folder.parent / filename

    if fmt == "json":
        root_name = folder.name
        payload = {
            "scanned":     str(folder),
            "timestamp":   datetime.now().isoformat(),
            "total_count": total_count,
            "total_sec":   round(total_sec, 2),
            "totals":      format_duration(total_sec),
            "tree":        _tree_to_dict(root_name, total_sec, total_count, tree),
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
        import io
        buf = io.StringIO()
        # Strip ANSI by temporarily redirecting — we rebuild the text cleanly
        fd = format_duration(total_sec)
        buf.write(f"AEVUM  |  Video Library Duration Scanner\n")
        buf.write(f"Scanned : {folder}\n")
        buf.write(f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        buf.write("=" * 64 + "\n\n")

        def write_tree(name, seconds, count, subfolders, depth=0, number=""):
            indent = "    " * depth
            label  = f"{number}.  {name}" if number else name
            fd_    = format_duration(seconds)
            if count == 0:
                buf.write(f"{indent}{label}\n")
                buf.write(f"{indent}    +--  (empty)\n")
            else:
                buf.write(f"{indent}{label}\n")
                buf.write(f"{indent}    +--  {fd_['hours_fmt']}  |  {count} videos\n")
            if subfolders:
                buf.write("\n")
            for i, (sn, ss, sc, ssub) in enumerate(subfolders, start=1):
                sub_number = f"{number}.{i}" if number else str(i)
                write_tree(sn, ss, sc, ssub, depth + 1, sub_number)
            if subfolders:
                buf.write("\n")

        write_tree(folder.name, total_sec, total_count, tree)
        buf.write("=" * 64 + "\n")
        buf.write("GRAND TOTAL\n")
        buf.write("=" * 64 + "\n")
        buf.write(f"Total videos  :  {total_count}\n")
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

LINE = "=" * 64

def print_banner():
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  A E V U M{RST}  {DIM}|{RST}  Video Library Duration Scanner")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {DIM}Type a folder path and press Enter to scan.{RST}")
    print()

def print_results(folder, total_sec, total_count, tree, durations=None, top_n=10):
    fmt = format_duration(total_sec)
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Video Library  |  Folder Summary{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print_tree(Path(folder).name, total_sec, total_count, tree)
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Grand Total{RST}")
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Total videos  {DIM}:{RST}  {Y}{total_count}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {Y}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {Y}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {Y}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Playback Speed{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        label = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {label:<6}        {DIM}:{RST}  {Y}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()
    if durations and top_n > 0:
        print_top_files(durations, top_n)

def print_post_scan_menu(current_sort="name"):
    sort_label = f"{DIM}(sorted by {current_sort}){RST}"
    print(f"  {DIM}What do you want to do?{RST}  {sort_label}")
    print(f"  {G}scan{RST}   {Y}clear{RST}   {M}export{RST}   {B}sort{RST}   {R}quit{RST}")
    print()

# ── MAIN ──────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        prog="aevum",
        description="Video library duration scanner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  aevum                          interactive mode\n"
            "  aevum D:\\Movies                scan and print, then exit\n"
            "  aevum D:\\Movies --export csv   scan and save a CSV next to the folder\n"
            "  aevum D:\\Movies --export json --out report.json\n"
            "  aevum D:\\Movies --top 20       show 20 longest files\n"
            "  aevum D:\\Movies --top 0        hide top-files section\n"
            "  aevum D:\\Movies --sort duration  sort folders by total duration\n"
            "  aevum D:\\Movies --sort count     sort folders by video count\n"
            "  aevum D:\\Movies --no-color     plain text output (good for piping)\n"
        ),
    )
    p.add_argument("folder",         nargs="?",  default=None,
                   help="folder to scan (omit to enter interactive mode)")
    p.add_argument("--export", "-e", choices=["txt", "csv", "json"], default=None,
                   metavar="FORMAT",
                   help="export results to a file: txt | csv | json")
    p.add_argument("--out",    "-o", default=None,
                   help="explicit output file path for --export (default: auto-named next to scanned folder)")
    p.add_argument("--top",    "-t", type=int, default=10,
                   metavar="N",
                   help="show top N longest files (default: 10, set 0 to hide)")
    p.add_argument("--sort",   "-s", choices=["name", "duration", "count"], default="name",
                   help="sort folders by: name (default) | duration | count")
    p.add_argument("--no-cache",     action="store_true",
                   help="bypass the duration cache and re-probe every file")
    p.add_argument("--no-color",     action="store_true",
                   help="strip ANSI colours from terminal output")
    p.add_argument("--version", "-v", action="version", version="aevum")
    return p.parse_args()


def _disable_color():
    """Replace all colour constants with empty strings for plain output."""
    global R, G, Y, B, M, C, W, DIM, RST
    R = G = Y = B = M = C = W = DIM = RST = ""


def _run_scan(folder, on_progress, sort_by="name", use_cache=True):
    """
    Run scan_parallel with optional cache.
    Returns (total_sec, total_count, tree, durations, hits).
    """
    folder     = Path(folder)
    cache      = load_cache(folder) if use_cache else {}
    stop_event = threading.Event()
    try:
        result = scan_parallel(folder, on_progress, stop_event, sort_by, cache)
    except KeyboardInterrupt:
        stop_event.set()
        raise
    total_sec, total_count, tree, durations, hits = result
    if use_cache and durations:
        save_cache(folder, durations)
    return total_sec, total_count, tree, durations, hits


def main():
    args = _parse_args()

    if args.no_color:
        _disable_color()

    # ── HEADLESS MODE ────────────────────────────────────────────────
    if args.folder is not None:
        folder = Path(args.folder.strip().strip("'\""))

        if not check_ffprobe():
            print(f"Error: ffprobe not found on PATH. Download FFmpeg from https://ffmpeg.org/download.html",
                  file=sys.stderr)
            sys.exit(1)

        if not folder.exists():
            print(f"Error: path not found: {folder}", file=sys.stderr)
            sys.exit(1)

        if not folder.is_dir():
            print(f"Error: not a directory: {folder}", file=sys.stderr)
            sys.exit(1)

        def on_progress(done, total):
            pct = int((done / total) * 100)
            bar_len = 24
            filled  = int(bar_len * done / total)
            bar     = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
                  end='', flush=True)

        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, hits = _run_scan(
                folder, on_progress, args.sort, not args.no_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            sys.exit(0)

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {Y}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, args.top)

        if args.export:
            try:
                dest = export_results(folder, total_sec, total_count, tree,
                                      durations, args.export, args.out)
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(1)

        sys.exit(0)

    # ── INTERACTIVE MODE ─────────────────────────────────────────────
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {R}ffprobe not found on PATH!{RST}")
        print(f"  Download FFmpeg from {C}https://ffmpeg.org/download.html{RST}")
        print()
        input("  Press Enter to exit...")
        sys.exit(1)

    def on_progress(done, total):
        pct    = int((done / total) * 100)
        bar_len = 24
        filled  = int(bar_len * done / total)
        bar     = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
              end='', flush=True)

    # Remember last scan for export in post-scan menu
    last_scan   = {}
    current_sort = args.sort  # inherits --sort flag if given, otherwise 'name'

    while True:
        try:
            raw = input(f"  {C}aevum{RST}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if not raw:
            continue

        # strip surrounding quotes (Windows drag-and-drop adds these)
        raw = raw.strip().strip("'\"")

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if raw.lower() in ('clear', 'c'):
            clear()
            print_banner()
            continue

        folder = Path(raw)

        if not folder.exists():
            print(f"\n  {R}Path not found:{RST} {raw}\n")
            continue

        if not folder.is_dir():
            print(f"\n  {R}That is a file, not a folder.{RST}\n")
            continue

        # scan
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, hits = _run_scan(
                folder, on_progress, current_sort, not args.no_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {Y}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, args.top)

        # stash for potential export
        last_scan = {
            "folder":      folder,
            "total_sec":   total_sec,
            "total_count": total_count,
            "tree":        tree,
            "durations":   durations,
        }

        # post-scan menu
        print_post_scan_menu(current_sort)
        while True:
            try:
                choice = input(f"  {C}aevum{RST}> ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n  {G}Goodbye!{RST}\n")
                sys.exit(0)

            if choice in ('quit', 'exit', 'q'):
                print(f"\n  {G}Goodbye!{RST}\n")
                sys.exit(0)

            elif choice == 'clear':
                clear()
                print_banner()
                break

            elif choice == 'scan':
                break

            elif choice in ('sort', 'sort name', 'sort duration', 'sort count'):
                parts = choice.split()
                mode  = parts[1] if len(parts) == 2 else None

                if mode is None:
                    print(f"  {DIM}Sort by?{RST}  {W}name{RST}   {W}duration{RST}   {W}count{RST}")
                    try:
                        mode = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        continue

                if mode not in ('name', 'duration', 'count'):
                    print(f"  {R}Unknown sort.{RST} Choose {W}name{RST}, {W}duration{RST}, or {W}count{RST}.")
                    continue

                current_sort = mode
                print(f"\n  {G}Sort set to:{RST} {W}{current_sort}{RST}  {DIM}(applies on next scan){RST}\n")
                print_post_scan_menu(current_sort)

            elif choice in ('export', 'export txt', 'export csv', 'export json'):
                parts = choice.split()
                fmt   = parts[1] if len(parts) == 2 else None

                if fmt is None:
                    print(f"  {DIM}Format?{RST}  {W}txt{RST}   {W}csv{RST}   {W}json{RST}")
                    try:
                        fmt = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        continue

                if fmt not in ('txt', 'csv', 'json'):
                    print(f"  {R}Unknown format.{RST} Choose {W}txt{RST}, {W}csv{RST}, or {W}json{RST}.")
                    continue

                try:
                    dest = export_results(
                        last_scan["folder"], last_scan["total_sec"],
                        last_scan["total_count"], last_scan["tree"],
                        last_scan["durations"], fmt,
                    )
                    print(f"\n  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
                except Exception as e:
                    print(f"\n  {R}Export failed:{RST} {e}\n")

            else:
                print(f"  {R}Invalid command.{RST} Type {G}scan{RST}, {Y}clear{RST}, {M}export{RST}, {B}sort{RST}, or {R}quit{RST}.")


if __name__ == "__main__":
    main()
