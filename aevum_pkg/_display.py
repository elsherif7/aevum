from pathlib import Path

from ._color import clr, LINE
from ._scan  import format_duration, format_size


_DEPTH_ATTRS = ("R", "G", "B", "M", "C")


def _dc(depth: int) -> str:
    """Return the ANSI code for the given tree depth."""
    return getattr(clr, _DEPTH_ATTRS[depth % len(_DEPTH_ATTRS)])


def print_tree(name, seconds, count, subfolders, direct=None, depth=0, number="",
               max_depth=50, show_files=False, direct_count=None, fbytes=0):
    """
    Recursively print the folder tree.

    Issue 26 fix: max_depth is now threaded through every recursive call so
    that --depth N actually limits the displayed tree depth.  Previously
    max_depth was always 50 regardless of the CLI flag because _cli.py never
    forwarded it.  Callers (print_results) pass max_depth through, and _cli.py
    now forwards args.depth there.
    """
    if depth > max_depth:
        return
    PAD    = "    "
    indent = PAD * depth
    fmt    = format_duration(seconds)
    col    = _dc(depth)
    label  = f"{number}.  {name}" if number else name

    if count == 0:
        print(f"{indent}{col}{label}{clr.RST}")
        print(f"{indent}    {clr.DIM}+--  (empty){clr.RST}")
    else:
        print(f"{indent}{col}{label}{clr.RST}")
        size_label = f"  {clr.DIM}|{clr.RST}  {clr.W}{format_size(fbytes)}{clr.RST}" if fbytes else ""
        print(f"{indent}    {clr.DIM}+--{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{count} {'video' if count == 1 else 'videos'}{clr.RST}{size_label}")

    print()

    if direct and subfolders:
        direct_sec   = sum(sec for _, sec in direct)
        direct_count = len(direct)
        dir_fmt      = format_duration(direct_sec)
        child_col    = _dc(depth + 1)
        virt_num     = f"{number}.0" if number else "0"
        print(f"{indent}    {child_col}{virt_num}.  (no folder){clr.RST}")
        dir_bytes = 0
        for p, _ in direct:
            try:
                dir_bytes += p.stat().st_size
            except OSError:
                pass
        dir_size_label = f"  {clr.DIM}|{clr.RST}  {clr.W}{format_size(dir_bytes)}{clr.RST}" if dir_bytes else ""
        print(f"{indent}        {clr.DIM}+--{clr.RST}  {clr.W}{dir_fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{direct_count} {'video' if direct_count == 1 else 'videos'}{clr.RST}{dir_size_label}")
        if show_files:
            print()
            for path, sec in direct:
                fd = format_duration(sec)
                print(f"{indent}        {clr.DIM}|  {fd['hours_fmt']}  {path.name}{clr.RST}")
        print()
    elif direct and show_files:
        for path, sec in direct:
            fd = format_duration(sec)
            print(f"{indent}    {clr.DIM}|  {fd['hours_fmt']}  {path.name}{clr.RST}")
        print()

    for i, (sub_name, sub_sec, sub_count, sub_fbytes, sub_direct_count, sub_sub, sub_direct) in enumerate(subfolders, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        # Issue 26: pass max_depth through every recursive call
        print_tree(
            sub_name, sub_sec, sub_count, sub_sub, sub_direct,
            depth + 1, sub_number,
            max_depth=max_depth,
            show_files=show_files,
            direct_count=sub_direct_count,
            fbytes=sub_fbytes,
        )
    if subfolders:
        print()


def print_top_files(durations, n=10):
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Top {n} Longest Files{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt    = format_duration(sec)
        name   = path.name
        parent = path.parent.name
        print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}({parent}){clr.RST}")
    print()


def print_results(folder, total_sec, total_count, tree, durations=None, sizes=None,
                  top_n=10, show_files=False, max_depth=50):
    """
    Issue 26 fix: max_depth parameter added so --depth N from the CLI is
    correctly forwarded all the way into print_tree.
    """
    fmt        = format_duration(total_sec)
    sizes      = sizes or {}
    subfolders, direct, root_bytes = tree
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    _folder_p     = Path(folder)
    _folder_label = _folder_p.name or _folder_p.drive or str(_folder_p)
    print(f"  {clr.W}  {_folder_label}{clr.RST}  {clr.DIM}({folder}){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print_tree(
        Path(folder).name, total_sec, total_count, subfolders, direct,
        show_files=show_files, fbytes=root_bytes, max_depth=max_depth,
    )
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Grand Total{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    total_bytes = sum(sizes.values())
    print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{total_count}{clr.RST}")
    print(f"  {clr.W}  Total size    {clr.DIM}:{clr.RST}  {clr.W}{format_size(total_bytes)}{clr.RST}")
    print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt['days_fmt']}{clr.RST}")
    print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Minutes       {clr.DIM}:{clr.RST}  {clr.W}{fmt['minutes_fmt']}{clr.RST}")
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Playback Speed{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        label    = f"{speed:.2f}".rstrip("0").rstrip(".") + "x"
        print(f"  {clr.W}  {label:<6}        {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()
    if durations and top_n > 0:
        print_top_files(durations, top_n)


def print_url_results(url, label, total_sec, total_count, entries, top_n=10, unavailable_count=0):
    fmt = format_duration(total_sec)
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  {label}{clr.RST}  {clr.DIM}({url}){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.W}  Total videos  {clr.DIM}:{clr.RST}  {clr.W}{total_count}{clr.RST}")
    if unavailable_count > 0:
        print(f"  {clr.Y}  Unavailable   {clr.DIM}:{clr.RST}  {clr.Y}{unavailable_count}{clr.RST}  {clr.DIM}(private, deleted, or region-blocked){clr.RST}")
    print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt['days_fmt']}{clr.RST}")
    print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Minutes       {clr.DIM}:{clr.RST}  {clr.W}{fmt['minutes_fmt']}{clr.RST}")
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Playback Speed{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        slabel   = f"{speed:.2f}".rstrip("0").rstrip(".") + "x"
        print(f"  {clr.W}  {slabel:<6}        {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()
    if entries and top_n > 0:
        ranked = sorted(entries, key=lambda e: e["duration"], reverse=True)[:top_n]
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Top {top_n} Longest Videos{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for i, e in enumerate(ranked, start=1):
            dur_fmt = format_duration(e["duration"])
            print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{dur_fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{e['title'][:60]}{clr.RST}")
        print()


def print_banner():
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.C}  A E V U M{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}Media Library Scanner{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.W}Type a folder path or YouTube URL (video/playlist/channel) and press Enter.{clr.RST}")
    print()
    print(f"  {clr.G}1. scan{clr.RST}   {clr.M}2. clear{clr.RST}   {clr.R}3. quit{clr.RST}")
    print()


def print_post_scan_menu(current_sort="name:asc"):
    print(f"  {clr.W}What do you want to do?{clr.RST}")
    print(
        f"  {clr.G}1. scan{clr.RST}   {clr.B}2. sort{clr.RST}   "
        f"{clr.M}3. export{clr.RST}   {clr.Y}4. clear{clr.RST}   "
        f"{clr.R}5. quit{clr.RST}   {clr.C}6. duplicates{clr.RST}   {clr.W}7. files{clr.RST}"
    )
    print()


def _fuzzy_suggest(word, candidates):
    """
    Return the closest candidate to word within edit-distance 2, or None.

    Issue 27 fix: candidate lists larger than 50 items are skipped entirely.
    The Levenshtein inner loop is O(len(word) * len(candidate)) and calling it
    hundreds of times on a large subfolder list would be noticeably slow.
    The threshold of 50 is generous for the intended use-cases (command names,
    sort fields) while protecting against large input.
    """
    if len(candidates) > 50:
        return None

    def _dist(a, b):
        if a == b:
            return 0
        la, lb = len(a), len(b)
        if abs(la - lb) > 3:
            return 99
        prev = list(range(lb + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(
                    prev[j] + (0 if ca == cb else 1),
                    curr[j] + 1,
                    prev[j + 1] + 1,
                ))
            prev = curr
        return prev[lb]

    scored   = [(c, _dist(word, c)) for c in candidates]
    best_c, best_d = min(scored, key=lambda x: x[1])
    return best_c if best_d <= 2 else None
