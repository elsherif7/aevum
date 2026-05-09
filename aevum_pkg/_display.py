from pathlib import Path

from ._color import clr, LINE
from ._scan  import format_duration, format_size


_DEPTH_ATTRS = ("R", "G", "B", "M", "C")

BAR_WIDTH = 28  # character width of the filled bar


def _bar(seconds, total_sec, width=BAR_WIDTH):
    """
    Return a colored ASCII bar representing seconds / total_sec.
    The bar uses block characters and shows the percentage at the end.
    Returns an empty string when total_sec is 0.
    """
    if total_sec <= 0:
        return ""
    ratio  = min(seconds / total_sec, 1.0)
    filled = round(ratio * width)
    pct    = ratio * 100
    bar    = "█" * filled + "░" * (width - filled)
    # colour: green for large shares, yellow for mid, dim for small
    if pct >= 40:
        col = clr.G
    elif pct >= 15:
        col = clr.Y
    else:
        col = clr.DIM
    return f"{col}{bar}{clr.RST}  {clr.DIM}{pct:5.1f}%{clr.RST}"


def print_bar_chart(subfolders, total_sec, direct=None):
    """
    Print a compact bar-chart section showing each top-level subfolder's
    share of the total duration.  Files sitting directly in the root folder
    are grouped as '(root files)'.
    """
    if total_sec <= 0:
        return

    # Build rows: (label, seconds)
    rows = []
    for name, sec, count, _fb, _dc, _sub, _dir in subfolders:
        if count > 0:
            rows.append((name, sec))

    # Direct root files grouped together
    if direct:
        direct_sec = sum(s for _, s in direct)
        if direct_sec > 0:
            rows.append(("(root files)", direct_sec))

    if not rows:
        return

    # Sort by duration descending for a clean waterfall look
    rows.sort(key=lambda x: x[1], reverse=True)

    # Truncate label to keep the chart tidy
    MAX_LABEL = 26

    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Duration Breakdown{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    for label, sec in rows:
        short = label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"
        dur   = format_duration(sec)["hours_fmt"]
        bar   = _bar(sec, total_sec)
        print(f"  {clr.W}{short:<{MAX_LABEL}}{clr.RST}  {bar}  {clr.DIM}{dur}{clr.RST}")
    print()


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


_DEFAULT_SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0)


def print_results(folder, total_sec, total_count, tree, durations=None, sizes=None,
                  top_n=10, show_files=False, max_depth=50, speeds=None):
    """
    Issue 26 fix: max_depth parameter added so --depth N from the CLI is
    correctly forwarded all the way into print_tree.
    speeds: extra custom speeds shown after a divider below the defaults.
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
    # ASCII bar chart — only shown when there is more than one folder to compare
    if subfolders or direct:
        print_bar_chart(subfolders, total_sec, direct)
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
    _extras = [s for s in (speeds or []) if s not in _DEFAULT_SPEEDS]
    for speed in _DEFAULT_SPEEDS:
        if speed <= 0:  # B-01: guard against division by zero
            continue
        adjusted = format_duration(total_sec / speed)
        label    = f"{speed:.6g}x"
        print(f"  {clr.W}  {label:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    if _extras:
        print(f"  {clr.DIM}  {chr(9472) * 40}{clr.RST}")
        for speed in _extras:
            if speed <= 0:  # B-01: guard against division by zero
                continue
            adjusted = format_duration(total_sec / speed)
            label    = f"{speed:.6g}x"
            print(f"  {clr.W}  {label:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()
    if durations and top_n > 0:
        print_top_files(durations, top_n)


def print_url_results(url, label, total_sec, total_count, entries, top_n=10, unavailable_count=0, speeds=None):
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
    _extras = [s for s in (speeds or []) if s not in _DEFAULT_SPEEDS]
    for speed in _DEFAULT_SPEEDS:
        if speed <= 0:  # B-01: guard against division by zero
            continue
        adjusted = format_duration(total_sec / speed)
        slabel   = f"{speed:.6g}x"
        print(f"  {clr.W}  {slabel:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    if _extras:
        print(f"  {clr.DIM}  {chr(9472) * 40}{clr.RST}")
        for speed in _extras:
            if speed <= 0:  # B-01: guard against division by zero
                continue
            adjusted = format_duration(total_sec / speed)
            slabel   = f"{speed:.6g}x"
            print(f"  {clr.W}  {slabel:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
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

    # Bar chart: top channels by total duration (only when multiple channels present)
    if entries and total_sec > 0:
        channel_secs: dict = {}
        for e in entries:
            ch = e.get("channel") or "Unknown"
            channel_secs[ch] = channel_secs.get(ch, 0.0) + e["duration"]
        if len(channel_secs) > 1:
            MAX_LABEL = 26
            print(f"  {clr.C}{LINE}{clr.RST}")
            print(f"  {clr.W}  Channel Breakdown{clr.RST}")
            print(f"  {clr.C}{LINE}{clr.RST}")
            print()
            for ch, sec in sorted(channel_secs.items(), key=lambda x: x[1], reverse=True):
                short = ch if len(ch) <= MAX_LABEL else ch[:MAX_LABEL - 1] + "…"
                dur   = format_duration(sec)["hours_fmt"]
                bar   = _bar(sec, total_sec)
                print(f"  {clr.W}{short:<{MAX_LABEL}}{clr.RST}  {bar}  {clr.DIM}{dur}{clr.RST}")
            print()


def print_stats(folder, durations, sizes):
    """
    Print deep library statistics:
    - total / average / median / shortest / longest duration
    - file format distribution
    - size distribution buckets
    - densest folder (most duration per GB)
    """
    if not durations:
        print(f"  {clr.Y}No media files found.{clr.RST}\n")
        return

    import statistics
    from collections import Counter

    secs_list  = list(durations.values())
    total_sec  = sum(secs_list)
    count      = len(secs_list)
    avg_sec    = total_sec / count
    median_sec = statistics.median(secs_list)
    min_path, min_sec = min(durations.items(), key=lambda x: x[1])
    max_path, max_sec = max(durations.items(), key=lambda x: x[1])

    # Format distribution
    ext_counter: Counter = Counter()
    for p in durations:
        ext_counter[p.suffix.lower()] += 1
    top_exts = ext_counter.most_common(8)

    # Size distribution buckets
    size_buckets = {"< 100 MB": 0, "100 MB – 1 GB": 0, "1 – 5 GB": 0, "> 5 GB": 0}
    for p, b in sizes.items():
        if b < 100 * 1024 * 1024:
            size_buckets["< 100 MB"] += 1
        elif b < 1024 * 1024 * 1024:
            size_buckets["100 MB – 1 GB"] += 1
        elif b < 5 * 1024 * 1024 * 1024:
            size_buckets["1 – 5 GB"] += 1
        else:
            size_buckets["> 5 GB"] += 1

    # Densest folder: most duration-seconds per byte
    folder_sec: dict   = {}
    folder_bytes: dict = {}
    for p, sec in durations.items():
        fn = p.parent.name
        folder_sec[fn]   = folder_sec.get(fn, 0.0) + sec
        folder_bytes[fn] = folder_bytes.get(fn, 0) + sizes.get(p, 0)
    densest = None
    best_ratio = 0.0
    for fn, fsec in folder_sec.items():
        fb = folder_bytes.get(fn, 0)
        if fb > 0:
            ratio = fsec / fb
            if ratio > best_ratio:
                best_ratio = ratio
                densest    = fn

    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Library Statistics{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}{Path(folder).name}{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{count:,}{clr.RST}")
    print(f"  {clr.W}  Total         {clr.DIM}:{clr.RST}  {clr.W}{format_duration(total_sec)['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Average       {clr.DIM}:{clr.RST}  {clr.W}{format_duration(avg_sec)['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Median        {clr.DIM}:{clr.RST}  {clr.W}{format_duration(median_sec)['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Shortest      {clr.DIM}:{clr.RST}  {clr.W}{format_duration(min_sec)['hours_fmt']}{clr.RST}  {clr.DIM}{min_path.name}{clr.RST}")
    print(f"  {clr.W}  Longest       {clr.DIM}:{clr.RST}  {clr.W}{format_duration(max_sec)['hours_fmt']}{clr.RST}  {clr.DIM}{max_path.name}{clr.RST}")
    print()

    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Format Distribution{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    for ext, n in top_exts:
        bar = _bar(n, count, width=20)
        print(f"  {clr.W}{ext or '(none)':<10}{clr.RST}  {bar}  {clr.DIM}{n:,} files{clr.RST}")
    print()

    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Size Distribution{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    for label, n in size_buckets.items():
        bar = _bar(n, count, width=20)
        print(f"  {clr.W}{label:<16}{clr.RST}  {bar}  {clr.DIM}{n:,} files{clr.RST}")
    print()

    if densest:
        density_fmt = format_duration(best_ratio * 1024 * 1024 * 1024)["hours_fmt"]
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Densest Folder{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        print()
        print(f"  {clr.W}  {densest}{clr.RST}  {clr.DIM}({density_fmt} per GB){clr.RST}")
        print()


def print_top(folder, durations, sizes, n=20, by="duration"):
    """
    Print top N files sorted by duration or size.
    by: 'duration' | 'size'
    """
    if not durations:
        print(f"\n  {clr.Y}No media files found.{clr.RST}\n")
        return

    if by == "size":
        ranked = sorted(sizes.items(), key=lambda x: x[1], reverse=True)
        ranked = [(p, s, durations.get(p, 0.0)) for p, s in ranked if p in durations]
    else:
        ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
        ranked = [(p, sizes.get(p, 0), s) for p, s in ranked]

    shown = ranked[:n]

    label = "Longest" if by == "duration" else "Largest"
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Top {n} {label} Files{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}{Path(folder).name}{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    for i, (path, fbytes, sec) in enumerate(shown, 1):
        dur = format_duration(sec)["hours_fmt"]
        sz  = format_size(fbytes)
        print(f"  {clr.DIM}{i:>3}.{clr.RST}  {clr.W}{path.name}{clr.RST}")
        print(f"        {clr.Y}{dur}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{sz}{clr.RST}  {clr.DIM}({path.parent.name}){clr.RST}")
        print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()


def print_recent(folder, durations, sizes, since_ts, limit=50):
    """
    Print files modified after since_ts, sorted newest first.
    """
    import datetime
    import os

    if not durations:
        print(f"\n  {clr.Y}No media files found.{clr.RST}\n")
        return

    # Collect files with their mtime
    entries = []
    for path, sec in durations.items():
        try:
            mtime = path.stat().st_mtime
            if mtime >= since_ts:
                entries.append((path, sec, mtime, sizes.get(path, 0)))
        except OSError:
            pass

    if not entries:
        print(f"\n  {clr.Y}No files found modified after that date.{clr.RST}\n")
        return

    # Sort newest first
    entries.sort(key=lambda x: x[2], reverse=True)
    shown    = entries[:limit]
    total_sec   = sum(e[1] for e in shown)
    total_bytes = sum(e[3] for e in shown)

    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Recent Files{clr.RST}  {clr.DIM}—{clr.RST}  "
          f"{clr.W}{Path(folder).name}{clr.RST}  "
          f"{clr.DIM}({len(entries)} files found){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()

    for path, sec, mtime, fbytes in shown:
        dt  = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        dur = format_duration(sec)["hours_fmt"]
        sz  = format_size(fbytes)
        print(f"  {clr.DIM}{dt}{clr.RST}  {clr.W}{path.name}{clr.RST}")
        print(f"             {clr.Y}{dur}{clr.RST}  {clr.DIM}|{clr.RST}  "
              f"{clr.W}{sz}{clr.RST}  {clr.DIM}({path.parent.name}){clr.RST}")
        print()

    if len(entries) > limit:
        print(f"  {clr.DIM}... and {len(entries) - limit} more (use --limit N to show more){clr.RST}\n")

    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Total{clr.RST}  {clr.DIM}:{clr.RST}  "
          f"{clr.Y}{len(shown)} files{clr.RST}  {clr.DIM}|{clr.RST}  "
          f"{clr.W}{format_duration(total_sec)['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  "
          f"{clr.W}{format_size(total_bytes)}{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()


def _fuzzy_suggest(word, candidates):
    """
    Return the closest candidate to word within edit-distance 2, or None.

    Issue 27 fix: candidate lists larger than 50 items are skipped entirely.
    The Levenshtein inner loop is O(len(word) * len(candidate)) and calling it
    hundreds of times on a large subfolder list would be noticeably slow.
    The threshold of 50 is generous for the intended use-cases (command names,
    sort fields) while protecting against large input.

    Security: Limits input lengths to prevent ReDoS attacks.
    """
    MAX_WORD_LENGTH = 50
    MAX_CANDIDATE_LENGTH = 50
    MAX_CANDIDATES = 50

    if len(word) > MAX_WORD_LENGTH:
        return None

    if len(candidates) > MAX_CANDIDATES:
        return None

    def _dist(a, b):
        a = a[:MAX_WORD_LENGTH]
        b = b[:MAX_CANDIDATE_LENGTH]

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

    filtered = [
        c for c in candidates
        if abs(len(c) - len(word)) <= 3
        and len(c) <= MAX_CANDIDATE_LENGTH
    ]

    if not filtered:
        return None

    scored = [(c, _dist(word, c)) for c in filtered]
    best_c, best_d = min(scored, key=lambda x: x[1])
    return best_c if best_d <= 2 else None
