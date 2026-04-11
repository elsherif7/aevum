import subprocess
import sys
import os
from pathlib import Path

VERSION = "3.1"

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

def scan_folder(path, on_file=None):
    path = Path(path)
    folder_seconds = 0.0
    video_count = 0
    subfolders = []
    for item in sorted(path.iterdir()):
        if item.is_dir():
            sub_sec, sub_count, sub_info = scan_folder(item, on_file)
            folder_seconds += sub_sec
            video_count += sub_count
            subfolders.append((item.name, sub_sec, sub_count, sub_info))
        elif item.is_file() and item.suffix.lower() in video_extensions:
            if on_file:
                on_file(item)
            folder_seconds += get_duration(item)
            video_count += 1
    return folder_seconds, video_count, subfolders

depth_colors = [R, G, B, M, C, W]

def print_tree(name, seconds, count, subfolders, depth=0, number=""):
    PAD    = "    "
    indent = PAD * depth
    fmt    = format_duration(seconds)
    col    = depth_colors[depth % len(depth_colors)]
    icons  = ["[]", "--", "  ", "  ", "  ", "  "]
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
    print(f"  {C}{LINE}{RST}")
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

        # strip surrounding quotes
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
        found = [0]
        def on_file(item):
            found[0] += 1
            print(f"\r  {C}Scanning...{RST}  {Y}{found[0]}{RST} video(s) found", end='', flush=True)

        try:
            total_sec, total_count, tree = scan_folder(folder, on_file)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        print(f"\r  {G}Done!{RST}  {Y}{total_count}{RST} video(s) found.              ")
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
        # 's' or anything else just loops back to prompt

if __name__ == "__main__":
    main()
