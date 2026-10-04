from __future__ import annotations

from pathlib import Path

from ._color import LINE, clr
from ._models import FolderNode, ScanTree
from ._scan import MAX_DEPTH, format_duration, format_size
from ._text import _safe

_DEPTH_ATTRS = ("R", "G", "B", "M", "C")

BAR_WIDTH = 28
MAX_LABEL = 26
_DEFAULT_SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0)


def _banner(title: str, suffix: str = "") -> None:
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  {title}{clr.RST}{suffix}")
    print(f"  {clr.C}{LINE}{clr.RST}")


def _print_share_rows(rows: list[tuple[str, float]], total_sec: float) -> None:
    """One bar per (label, seconds) row; labels must already be sanitised."""
    for label, sec in rows:
        short = label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"
        dur   = format_duration(sec)["hours_fmt"]
        bar   = _bar(sec, total_sec)
        print(f"  {clr.W}{short:<{MAX_LABEL}}{clr.RST}  {bar}  {clr.DIM}{dur}{clr.RST}")


def _print_duration_totals(fmt: dict[str, str]) -> None:
    print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt['days_fmt']}{clr.RST}")
    print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}")
    print(f"  {clr.W}  Minutes       {clr.DIM}:{clr.RST}  {clr.W}{fmt['minutes_fmt']}{clr.RST}")


def _print_playback_speeds(total_sec: float) -> None:
    _banner("Playback Speed")
    for speed in _DEFAULT_SPEEDS:
        adjusted = format_duration(total_sec / speed)
        label    = f"{speed:.6g}x"
        print(f"  {clr.W}  {label:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adjusted['hours_fmt']}{clr.RST}  {clr.DIM}({adjusted['days_fmt']}){clr.RST}")
    print()


def _bar(seconds: float, total_sec: float, width: int = BAR_WIDTH) -> str:
    if total_sec <= 0:
        return ""
    ratio  = min(seconds / total_sec, 1.0)
    filled = round(ratio * width)
    pct    = ratio * 100
    bar    = "█" * filled + "░" * (width - filled)
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
    """Bar chart of each top-level subfolder's share of the total, with files directly in the root as '(root files)'. Skipped when there is only one row."""
    if total_sec <= 0:
        return

    rows = []
    for node in children:
        if node.total_count > 0:
            rows.append((node.name, node.total_sec))

    if direct_files:
        direct_sec = sum(s for _, s in direct_files)
        if direct_sec > 0:
            rows.append(("(root files)", direct_sec))

    if len(rows) < 2:
        return

    rows.sort(key=lambda x: x[1], reverse=True)

    _banner("Duration Breakdown")
    print()
    _print_share_rows([(_safe(label), sec) for label, sec in rows], total_sec)
    print()


def _dc(depth: int) -> str:
    return getattr(clr, _DEPTH_ATTRS[depth % len(_DEPTH_ATTRS)])


def _print_stats_line(prefix: str, seconds: float, count: int, nbytes: int) -> None:
    hours      = format_duration(seconds)["hours_fmt"]
    noun       = "media file" if count == 1 else "media files"
    size_label = f"  {clr.DIM}|{clr.RST}  {clr.W}{format_size(nbytes)}{clr.RST}" if nbytes else ""
    print(f"{prefix}{clr.DIM}+--{clr.RST}  {clr.W}{hours}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{count} {noun}{clr.RST}{size_label}")


def print_tree(
    name: str,
    seconds: float,
    count: int,
    children: list[FolderNode],
    direct_files: list[tuple[Path, float]] | None = None,
    depth: int = 0,
    number: str = "",
    max_depth: int = MAX_DEPTH,
    fbytes: int = 0,
    direct_bytes: int = 0,
) -> None:
    if depth > max_depth:
        return
    PAD    = "    "
    indent = PAD * depth
    col    = _dc(depth)
    name   = _safe(name)
    label  = f"{number}.  {name}" if number else name

    if count == 0:
        print(f"{indent}{col}{label}{clr.RST}")
        print(f"{indent}    {clr.DIM}+--  (empty){clr.RST}")
    else:
        print(f"{indent}{col}{label}{clr.RST}")
        _print_stats_line(f"{indent}    ", seconds, count, fbytes)

    print()

    if direct_files and children:
        direct_sec   = sum(sec for _, sec in direct_files)
        child_col    = _dc(depth + 1)
        virt_num     = f"{number}.0" if number else "0"
        print(f"{indent}    {child_col}{virt_num}.  (no folder){clr.RST}")
        _print_stats_line(f"{indent}        ", direct_sec, len(direct_files), direct_bytes)
        print()

    for i, node in enumerate(children, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        print_tree(
            node.name, node.total_sec, node.total_count,
            node.children, node.direct_files,
            depth + 1, sub_number,
            max_depth=max_depth,
            fbytes=node.total_bytes,
            direct_bytes=node.direct_bytes,
        )
    if children:
        print()


def print_top_files(durations: dict[Path, float], n: int = 10) -> None:
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    _banner(f"Top {n} Longest Files")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt    = format_duration(sec)
        name   = _safe(path.name)
        parent = _safe(path.parent.name)
        print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}({parent}){clr.RST}")
    print()


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
    _folder_p     = Path(folder).resolve()
    _folder_label = _safe(_folder_p.name or _folder_p.drive or str(_folder_p))
    _banner(_folder_label, f"  {clr.DIM}({_safe(str(folder), 500)}){clr.RST}")
    print()
    print_tree(
        _folder_label, total_sec, total_count,
        tree.children, tree.direct_files,
        fbytes=tree.root_bytes,
        direct_bytes=tree.direct_bytes,
    )
    if tree.children or tree.direct_files:
        print_bar_chart(tree.children, total_sec, tree.direct_files)
    _banner("Grand Total")
    total_bytes = sum(sizes.values())
    print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{total_count}{clr.RST}")
    print(f"  {clr.W}  Total size    {clr.DIM}:{clr.RST}  {clr.W}{format_size(total_bytes)}{clr.RST}")
    _print_duration_totals(fmt)
    print()
    _print_playback_speeds(total_sec)
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
    _banner(_safe(label), f"  {clr.DIM}({_safe(url, 500)}){clr.RST}")
    print()
    print(f"  {clr.W}  Total videos  {clr.DIM}:{clr.RST}  {clr.W}{total_count}{clr.RST}")
    if unavailable_count > 0:
        print(f"  {clr.Y}  Unavailable   {clr.DIM}:{clr.RST}  {clr.Y}{unavailable_count}{clr.RST}  {clr.DIM}(private, deleted, or region-blocked){clr.RST}")
    _print_duration_totals(fmt)
    print()
    _print_playback_speeds(total_sec)
    if entries:
        ranked = sorted(entries, key=lambda e: e["duration"], reverse=True)[:10]
        _banner("Top 10 Longest Videos")
        for i, e in enumerate(ranked, start=1):
            dur_fmt = format_duration(e["duration"])
            print(f"  {clr.DIM}{i:>2}.{clr.RST}  {clr.W}{dur_fmt['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{_safe(e['title'])[:60]}{clr.RST}")
        print()

    if entries and total_sec > 0:
        channel_secs: dict = {}
        for e in entries:
            ch = _safe(e.get("channel") or "Unknown")
            channel_secs[ch] = channel_secs.get(ch, 0.0) + e["duration"]
        if len(channel_secs) > 1:
            _banner("Channel Breakdown")
            print()
            _print_share_rows(sorted(channel_secs.items(), key=lambda x: x[1], reverse=True), total_sec)
            print()


def _fuzzy_suggest(word: str, candidates: list[str]) -> str | None:
    """
    Return the closest candidate, or None. A name of 4 characters or fewer may differ
    by one edit, longer names by two.

    Lists of more than 50 candidates are skipped because the Levenshtein
    loop would be noticeably slow on a big folder listing.
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
    return best_c if best_d <= (1 if len(word) <= 4 else 2) else None
