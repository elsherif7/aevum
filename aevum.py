import struct
import subprocess
import sys
import os
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "3.2"

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
                return 0, pos + 8
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

def scan_parallel(root, on_progress=None, stop_event=None):
    """Pipelined scan: files are submitted to probe pool as they are discovered."""
    root = Path(root)
    durations = {}
    done = [0]
    total = [0]
    lock = threading.Lock()

    def probe(path):
        if stop_event and stop_event.is_set():
            return path, 0.0
        sec = get_duration(path)
        with lock:
            done[0] += 1
            if on_progress and total[0] > 0:
                on_progress(done[0], total[0])
        return path, sec

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}

        def collect_and_submit():
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
                                        total[0] += 1
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
        return 0.0, 0, _build_tree(root, {})

    total_sec = sum(durations.values())
    total_count = len(durations)
    tree = _build_tree(root, durations)

    return total_sec, total_count, tree

def _build_tree(root, durations):
    """
    O(n) tree builder — aggregate folder stats in a single pass over durations,
    then recursively assemble the tree structure from the pre-built dict.
    """
    root = Path(root)

    # Single pass: bucket every file's duration into its parent folder and all ancestors
    folder_secs  = {}   # folder path -> total seconds
    folder_count = {}   # folder path -> video count

    for path, sec in durations.items():
        parent = path.parent
        while True:
            folder_secs[parent]  = folder_secs.get(parent, 0.0) + sec
            folder_count[parent] = folder_count.get(parent, 0) + 1
            if parent == root:
                break
            parent = parent.parent

    def build(node):
        subfolders = []
        try:
            children = sorted(p for p in node.iterdir() if p.is_dir())
        except PermissionError:
            return subfolders
        for child in children:
            secs  = folder_secs.get(child, 0.0)
            count = folder_count.get(child, 0)
            subfolders.append((child.name, secs, count, build(child)))
        return subfolders

    return build(root)

depth_colors = [R, G, B, M, C, W]

def print_tree(name, seconds, count, subfolders, depth=0, number=""):
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

LINE = "=" * 64

def print_banner():
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  A E V U M{RST}  {DIM}|{RST}  Video Library Duration Scanner")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {DIM}Type a folder path and press Enter to scan.{RST}")
    print()

def print_results(folder, total_sec, total_count, tree):
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

def print_post_scan_menu():
    print(f"  {DIM}What do you want to do?{RST}")
    print(f"  {G}scan{RST}   {Y}clear{RST}   {R}quit{RST}")
    print()

# ── MAIN ──────────────────────────────────────────────────────────────

def main():
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {R}ffprobe not found on PATH!{RST}")
        print(f"  Download FFmpeg from {C}https://ffmpeg.org/download.html{RST}")
        print()
        input("  Press Enter to exit...")
        sys.exit(1)

    def on_progress(done, total):
        pct = int((done / total) * 100)
        bar_len = 24
        filled = int(bar_len * done / total)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
              end='', flush=True)

    while True:
        try:
            raw = input(f"  {C}aevum{RST}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if not raw:
            continue

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if raw.lower() in ('clear', 'c'):
            clear()
            print_banner()
            continue

        # strip surrounding quotes (Windows drag-and-drop adds these)
        raw = raw.strip().strip("'\"")

        folder = Path(raw)

        if not folder.exists():
            print(f"\n  {R}Path not found:{RST} {raw}\n")
            continue

        if not folder.is_dir():
            print(f"\n  {R}That is a file, not a folder.{RST}\n")
            continue

        # scan
        stop_event = threading.Event()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)

        try:
            total_sec, total_count, tree = scan_parallel(folder, on_progress, stop_event)
        except KeyboardInterrupt:
            stop_event.set()
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        print(f"\r  {G}Done!{RST}  {Y}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.                                    ")
        print_results(folder, total_sec, total_count, tree)

        # post-scan menu
        print_post_scan_menu()
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
            else:
                print(f"  {R}Invalid command.{RST} Type {G}scan{RST}, {Y}clear{RST}, or {R}quit{RST}.")

if __name__ == "__main__":
    main()
