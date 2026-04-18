import subprocess
import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

VERSION = "3.2"

# Enable ANSI colors on Windows
if os.name == 'nt':
    os.system('color')
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
# 32 is a good default — fast without hammering the drive.
MAX_WORKERS = 32

# ── HELPERS ───────────────────────────────────────────────────────────

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def check_ffprobe():
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except FileNotFoundError:
        return False

def get_duration(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except:
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
        "raw":         seconds
    }

def collect_videos(path):
    """Walk the folder tree and return a flat list of all video Paths."""
    path = Path(path)
    videos = []
    for item in path.rglob('*'):
        if item.is_file() and item.suffix.lower() in video_extensions:
            videos.append(item)
    return videos

def scan_parallel(root, on_progress=None, stop_event=None):
    """
    Two-phase scan:
      1. Walk the tree to collect all video paths (fast, no ffprobe yet).
      2. Probe all files in parallel with a thread pool.
    Returns (total_seconds, total_count, tree) — same shape as before.
    """
    root = Path(root)

    # Phase 1: collect
    all_videos = collect_videos(root)
    total = len(all_videos)

    if total == 0:
        return 0.0, 0, _build_tree(root, {})

    # Phase 2: probe in parallel
    durations = {}   # path -> seconds
    done = [0]
    lock = threading.Lock()

    def probe(path):
        if stop_event and stop_event.is_set():
            return path, 0.0
        sec = get_duration(path)
        with lock:
            done[0] += 1
            if on_progress:
                on_progress(done[0], total)
        return path, sec

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe, v): v for v in all_videos}
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

    # Phase 3: build tree from results
    total_sec = sum(durations.values())
    total_count = len(durations)
    tree = _build_tree(root, durations)

    return total_sec, total_count, tree

def _build_tree(root, durations):
    """Recursively build the subfolder tree structure from the durations map."""
    root = Path(root)
    subfolders = []
    try:
        children = sorted(root.iterdir())
    except PermissionError:
        return subfolders

    for item in children:
        if item.is_dir():
            sub_secs = sum(sec for path, sec in durations.items()
                           if path.is_relative_to(item))
            sub_count = sum(1 for path in durations
                            if path.is_relative_to(item))
            sub_tree = _build_tree(item, durations)
            subfolders.append((item.name, sub_secs, sub_count, sub_tree))

    return subfolders

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
    print(f"  {DIM}Commands:{RST}  "
          f"{G}clear{RST}  "
          f"{R}exit{RST}  "
          f"{Y}Ctrl+C{RST} to quit")
    print()

def print_results(folder, total_sec, total_count, tree):
    fmt = format_duration(total_sec)
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  VIDEO LIBRARY  |  FOLDER SUMMARY{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print_tree(Path(folder).name, total_sec, total_count, tree)
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  GRAND TOTAL{RST}")
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Total Videos  {DIM}:{RST}  {Y}{total_count}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {Y}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {Y}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {Y}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  PLAYBACK SPEED{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        label = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {label:<6}        {DIM}:{RST}  {Y}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()

def print_post_scan_menu():
    print(f"  {DIM}What do you want to do?{RST}")
    print(f"  [{G}S{RST}] Scan another path    "
          f"  [{Y}C{RST}] Clear screen    "
          f"  [{R}Q{RST}] Quit")
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
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]

        folder = Path(raw)

        if not folder.exists():
            print(f"\n  {R}Path not found:{RST} {raw}\n")
            continue

        if not folder.is_dir():
            print(f"\n  {R}That is a file, not a folder.{RST}\n")
            continue

        # scan
        stop_event = threading.Event()

        def on_progress(done, total):
            pct = int((done / total) * 100)
            bar_len = 24
            filled = int(bar_len * done / total)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
                  end='', flush=True)

        try:
            total_sec, total_count, tree = scan_parallel(folder, on_progress, stop_event)
        except KeyboardInterrupt:
            stop_event.set()
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        print(f"\r  {G}Done!{RST}  {Y}{total_count}{RST} video(s) found.                                    ")
        print_results(folder, total_sec, total_count, tree)

        # post-scan menu
        print_post_scan_menu()
        try:
            choice = input(f"  {C}aevum{RST}> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if choice in ('q', 'quit', 'exit'):
            print(f"\n  {G}Goodbye!{RST}\n")
            sys.exit(0)
        elif choice in ('c', 'clear'):
            clear()
            print_banner()
        # 's' or anything else loops back to prompt

if __name__ == "__main__":
    main()
