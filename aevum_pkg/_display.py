from __future__ import annotations

import re as _re
from pathlib import Path

from ._color import LINE, clr
from ._models import FolderNode, ScanTree
from ._scan import format_duration, format_size

# Complete escape sequences: CSI (ESC [ ... final byte, including private forms such
# as ESC[?25l), OSC (ESC ] ... BEL or ESC \), charset switches (ESC ( 0), and the
# two-character ones such as ESC c (full terminal reset).
_ANSI_ESCAPE = _re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[ -/]+[0-~]|[0-~])')
# C0 controls, DEL and C1 controls. U+009B is "CSI" on terminals that honour 8-bit controls.
_CTRL_CHARS  = _re.compile(r'[\x00-\x1f\x7f-\x9f]')
# Bidi embeddings/overrides/isolates, which can make "gpj.exe" display as "exe.jpg".
_BIDI_CHARS  = _re.compile('[\u202a-\u202e\u2066-\u2069]')


def _safe(name: object, maxlen: int = 200) -> str:
    """
    Make an untrusted string (folder or file name, video title, channel name, a
    path typed or pasted by the user) safe to print: no terminal escape sequences,
    control characters or bidi overrides, and no characters that can't be encoded
    (the stray bytes of an undecodable file name become '?').
    """
    text = str(name)
    text = _ANSI_ESCAPE.sub('', text)
    text = _CTRL_CHARS.sub('', text)
    text = _BIDI_CHARS.sub('', text)
    text = text.encode('utf-8', 'replace').decode('utf-8')
    return text[:maxlen]


_DEPTH_ATTRS = ("R", "G", "B", "M", "C")

BAR_WIDTH = 28  # character width of the filled bar


def _bar(seconds: float, total_sec: float, width: int = BAR_WIDTH) -> str:
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


def print_bar_chart(
    children: list[FolderNode],
    total_sec: float,
    direct_files: list[tuple[Path, float]] | None = None,
) -> None:
    """
    Print a compact bar-chart section showing each top-level subfolder's
    share of the total duration.  Files sitting directly in the root folder
    are grouped as '(root files)'.
    """
    if total_sec <= 0:
        return

    # Build rows: (label, seconds)
    rows = []
    for node in children:
        if node.total_count > 0:
            rows.append((node.name, node.total_sec))

    # Direct root files grouped together
    if direct_files:
        direct_sec = sum(s for _, s in direct_files)
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
        label = _safe(label)
        short = label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"
        dur   = format_duration(sec)["hours_fmt"]
        bar   = _bar(sec, total_sec)
        print(f"  {clr.W}{short:<{MAX_LABEL}}{clr.RST}  {bar}  {clr.DIM}{dur}{clr.RST}")
    print()


def _dc(depth: int) -> str:
    """Return the ANSI code for the given tree depth."""
    return getattr(clr, _DEPTH_ATTRS[depth % len(_DEPTH_ATTRS)])


def print_tree(
    name: str,
    seconds: float,
    count: int,
    children: list[FolderNode],
    direct_files: list[tuple[Path, float]] | None = None,
    depth: int = 0,
    number: str = "",
    max_depth: int = 50,
    fbytes: int = 0,
) -> None:
    """
    Recursively print the folder tree.

    max_depth is passed down every recursive call, so a very deep folder
    structure can't cause unbounded recursion.
    """
    if depth > max_depth:
        return
    PAD    = "    "
    indent = PAD * depth
    fmt    = format_duration(seconds)
    col    = _dc(depth)
    name   = _safe(name)
    label  = f"{number}.  {name}" if number else name

    if count == 0:
        print(f"{indent}{col}{label}{clr.RST}")
        print(f"{indent}    {clr.DIM}+--  (empty){clr.RST}")
    else:
        print(f"{indent}{col}{label}{clr.RST}")
        size_label = f"  {clr.DIM}|{clr.RST}  {clr.W}{format_size(fbytes)}{clr.RST}" if fbytes else ""
        print(f"{indent}    {clr.DIM}+--{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{count} {'video' if count == 1 else 'videos'}{clr.RST}{size_label}")

    print()

    if direct_files and children:
        direct_sec   = sum(sec for _, sec in direct_files)
        direct_count = len(direct_files)
        dir_fmt      = format_duration(direct_sec)
        child_col    = _dc(depth + 1)
        virt_num     = f"{number}.0" if number else "0"
        print(f"{indent}    {child_col}{virt_num}.  (no folder){clr.RST}")
        dir_bytes = 0
        for p, _ in direct_files:
            try:
                dir_bytes += p.stat().st_size
            except OSError:
                pass
        dir_size_label = f"  {clr.DIM}|{clr.RST}  {clr.W}{format_size(dir_bytes)}{clr.RST}" if dir_bytes else ""
        print(f"{indent}        {clr.DIM}+--{clr.RST}  {clr.W}{dir_fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{direct_count} {'video' if direct_count == 1 else 'videos'}{clr.RST}{dir_size_label}")
        print()

    for i, node in enumerate(children, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        print_tree(
            node.name, node.total_sec, node.total_count,
            node.children, node.direct_files,
            depth + 1, sub_number,
            max_depth=max_depth,
            fbytes=node.total_bytes,
        )
    if children:
        print()


def print_top_files(durations: dict[Path, float], n: int = 10) -> None:
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Top {n} Longest Files{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt    = format_duration(sec)
        name   = _safe(path.name)
        parent = _safe(path.parent.name)
        print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}({parent}){clr.RST}")
    print()


_DEFAULT_SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0)


def print_results(
    folder: str | Path,
    total_sec: float,
    total_count: int,
    tree: ScanTree,
    durations: dict[Path, float] | None = None,
    sizes: dict[Path, int] | None = None,
) -> None:
    fmt        = format_duration(total_sec)
    sizes      = sizes or {}
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    _folder_p     = Path(folder).resolve()
    _folder_label = _safe(_folder_p.name or _folder_p.drive or str(_folder_p))
    print(f"  {clr.W}  {_folder_label}{clr.RST}  {clr.DIM}({_safe(str(folder), 500)}){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print_tree(
        _folder_label, total_sec, total_count,
        tree.children, tree.direct_files,
        fbytes=tree.root_bytes,
    )
    # ASCII bar chart — only shown when there is more than one folder to compare
    if tree.children or tree.direct_files:
        print_bar_chart(tree.children, total_sec, tree.direct_files)
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
    for speed in _DEFAULT_SPEEDS:
        adjusted = format_duration(total_sec / speed)
        label    = f"{speed:.6g}x"
        print(f"  {clr.W}  {label:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()
    if durations:
        print_top_files(durations, 10)


def print_url_results(
    url: str,
    label: str,
    total_sec: float,
    total_count: int,
    entries: list[dict],
    unavailable_count: int = 0,
) -> None:
    fmt = format_duration(total_sec)
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  {_safe(label)}{clr.RST}  {clr.DIM}({_safe(url, 500)}){clr.RST}")
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
    for speed in _DEFAULT_SPEEDS:
        adjusted = format_duration(total_sec / speed)
        slabel   = f"{speed:.6g}x"
        print(f"  {clr.W}  {slabel:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()
    if entries:
        ranked = sorted(entries, key=lambda e: e["duration"], reverse=True)[:10]
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Top 10 Longest Videos{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for i, e in enumerate(ranked, start=1):
            dur_fmt = format_duration(e["duration"])
            print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{dur_fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{_safe(e['title'])[:60]}{clr.RST}")
        print()

    # Bar chart: top channels by total duration (only when multiple channels present)
    if entries and total_sec > 0:
        channel_secs: dict = {}
        for e in entries:
            ch = _safe(e.get("channel") or "Unknown")
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


def _fuzzy_suggest(word: str, candidates: list[str]) -> str | None:
    """
    Return the closest candidate to word within edit-distance 2, or None.

    Candidate lists larger than 50 items are skipped entirely. The
    Levenshtein inner loop is O(len(word) * len(candidate)), so running it
    on a large folder listing would be noticeably slow.

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
