from pathlib import Path

from ._color import R, G, Y, B, M, C, W, DIM, RST, LINE
from ._scan  import format_duration, format_size


depth_colors = [R, G, B, M, C]


def print_tree(name, seconds, count, subfolders, direct=None, depth=0, number="",
               max_depth=50, show_files=False, direct_count=None, fbytes=0):
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
        size_label = f"  {DIM}|{RST}  {W}{format_size(fbytes)}{RST}" if fbytes else ""
        print(f"{indent}    {DIM}+--{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{count} {'video' if count == 1 else 'videos'}{RST}{size_label}")

    print()

    if direct and subfolders:
        direct_sec   = sum(sec for _, sec in direct)
        direct_count = len(direct)
        dir_fmt      = format_duration(direct_sec)
        child_col    = depth_colors[(depth + 1) % len(depth_colors)]
        virt_num     = f"{number}.0" if number else "0"
        print(f"{indent}    {child_col}{virt_num}.  (no folder){RST}")
        dir_bytes = 0
        for p, _ in direct:
            try:
                dir_bytes += p.stat().st_size
            except OSError:
                pass
        dir_size_label = f"  {DIM}|{RST}  {W}{format_size(dir_bytes)}{RST}" if dir_bytes else ""
        print(f"{indent}        {DIM}+--{RST}  {W}{dir_fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{direct_count} {'video' if direct_count == 1 else 'videos'}{RST}{dir_size_label}")
        if show_files:
            print()
            for path, sec in direct:
                fd = format_duration(sec)
                print(f"{indent}        {DIM}|  {fd['hours_fmt']}  {path.name}{RST}")
        print()

    for i, (sub_name, sub_sec, sub_count, sub_fbytes, sub_direct_count, sub_sub, sub_direct) in enumerate(subfolders, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        print_tree(sub_name, sub_sec, sub_count, sub_sub, sub_direct, depth + 1, sub_number,
                   show_files=show_files, direct_count=sub_direct_count, fbytes=sub_fbytes)
    if subfolders:
        print()


def print_top_files(durations, n=10):
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Top {n} Longest Files{RST}")
    print(f"  {C}{LINE}{RST}")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt    = format_duration(sec)
        name   = path.name
        parent = path.parent.name
        print(f"  {DIM}{i:>2}.{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{name}{RST}  {DIM}({parent}){RST}")
    print()


def print_results(folder, total_sec, total_count, tree, durations=None, sizes=None, top_n=10, show_files=False):
    fmt   = format_duration(total_sec)
    sizes = sizes or {}
    subfolders, direct, root_bytes = tree
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Media Library  |  Folder Summary{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print_tree(Path(folder).name, total_sec, total_count, subfolders, direct, show_files=show_files, fbytes=root_bytes)
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Grand Total{RST}")
    print(f"  {C}{LINE}{RST}")
    total_bytes = sum(sizes.values())
    print(f"  {W}  Total files   {DIM}:{RST}  {W}{total_count}{RST}")
    print(f"  {W}  Total size    {DIM}:{RST}  {W}{format_size(total_bytes)}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {W}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {W}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {W}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Playback Speed{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        label    = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {label:<6}        {DIM}:{RST}  {W}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()
    if durations and top_n > 0:
        print_top_files(durations, top_n)


def print_url_results(url, label, total_sec, total_count, entries, top_n=10):
    fmt = format_duration(total_sec)
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  {label}{RST}")
    print(f"  {DIM}  {url[:70]}{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}  Total videos  {DIM}:{RST}  {W}{total_count}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {W}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {W}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {W}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Playback Speed{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        slabel   = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {slabel:<6}        {DIM}:{RST}  {W}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()
    if entries and top_n > 0:
        ranked = sorted(entries, key=lambda e: e['duration'], reverse=True)[:top_n]
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Top {top_n} Longest Videos{RST}")
        print(f"  {C}{LINE}{RST}")
        for i, e in enumerate(ranked, start=1):
            dur_fmt = format_duration(e['duration'])
            print(f"  {DIM}{i:>2}.{RST}  {W}{dur_fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{e['title'][:60]}{RST}")
        print()


def print_banner():
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  A E V U M{RST}  {DIM}|{RST}  {W}Media Library Scanner{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}Type a folder path or YouTube URL (video/playlist/channel) and press Enter.{RST}")
    print()
    print(f"  {G}1. scan{RST}   {M}2. clear{RST}   {R}3. quit{RST}")
    print()


def print_post_scan_menu(current_sort="name:asc"):
    print(f"  {W}What do you want to do?{RST}")
    print(f"  {G}1. scan{RST}   {B}2. sort{RST}   {M}3. export{RST}   {Y}4. clear{RST}   {R}5. quit{RST}   {C}6. duplicates{RST}")
    print()


def _fuzzy_suggest(word, candidates):
    def _dist(a, b):
        if a == b: return 0
        la, lb = len(a), len(b)
        if abs(la - lb) > 3: return 99
        prev = list(range(lb + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(prev[j] + (0 if ca == cb else 1),
                                curr[j] + 1, prev[j + 1] + 1))
            prev = curr
        return prev[lb]
    scored = [(c, _dist(word, c)) for c in candidates]
    best_c, best_d = min(scored, key=lambda x: x[1])
    return best_c if best_d <= 2 else None
