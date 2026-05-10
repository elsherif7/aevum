"""
Folder comparison logic for Aevum.

Q-01 fix: extracted from _cli.py where it was inlined with a comment
'# ── Compare (inlined from _compare.py) ───'.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ._color import LINE, clr
from ._scan import _run_scan, format_duration

# Type alias for the data returned per folder by run_compare.
_FolderData = tuple[float, int, dict]


def run_compare(
    folder_a: str | Path,
    folder_b: str | Path,
    on_progress: Callable[[int, int], None] | None,
    sort_by: str,
    use_cache: bool,
    quiet: bool = False,
) -> tuple[_FolderData, _FolderData]:
    """
    Scan both folders and return their summary data.

    Returns:
        ((sec_a, count_a, dur_a), (sec_b, count_b, dur_b))
    """
    if not quiet:
        print(f"  {clr.DIM}Scanning {Path(folder_a).name}...{clr.RST}", end="", flush=True)
    sec_a, count_a, tree_a, dur_a, _, _ = _run_scan(folder_a, on_progress, sort_by, use_cache)
    if not quiet:
        print(
            f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  "
            f"{clr.W}{Path(folder_a).name}{clr.RST}  {clr.DIM}|{clr.RST}  "
            f"{clr.Y}{count_a} files  {format_duration(sec_a)['hours_fmt']}{clr.RST}".ljust(70)
        )
        print(f"  {clr.DIM}Scanning {Path(folder_b).name}...{clr.RST}", end="", flush=True)
    sec_b, count_b, tree_b, dur_b, _, _ = _run_scan(folder_b, on_progress, sort_by, use_cache)
    if not quiet:
        print(
            f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  "
            f"{clr.W}{Path(folder_b).name}{clr.RST}  {clr.DIM}|{clr.RST}  "
            f"{clr.Y}{count_b} files  {format_duration(sec_b)['hours_fmt']}{clr.RST}".ljust(70)
        )
    return (sec_a, count_a, dur_a), (sec_b, count_b, dur_b)


def print_comparison(
    folder_a: str | Path,
    folder_b: str | Path,
    data_a: _FolderData,
    data_b: _FolderData,
) -> None:
    """Print a side-by-side comparison of two folder scans."""
    sec_a, count_a, dur_a = data_a
    sec_b, count_b, dur_b = data_b
    name_a = Path(folder_a).name
    name_b = Path(folder_b).name
    root_a = Path(folder_a).resolve()
    root_b = Path(folder_b).resolve()
    delta_sec   = sec_b   - sec_a
    delta_count = count_b - count_a
    delta_sign  = "+" if delta_sec   >= 0 else ""
    delta_csign = "+" if delta_count >= 0 else ""

    def _rel(p, root):
        """
        Return the path of p relative to root as a string.
        Uses the full relative path (e.g. 'Action/Superhero') so that two
        subfolders with the same leaf name (e.g. 'Action/Extras' and
        'Comedy/Extras') are correctly treated as distinct entries.
        Falls back to p.parent.name if the path is not under root.
        """
        try:
            parts = Path(p).resolve().relative_to(root).parts
            # Drop the filename — we want the containing folder path.
            return str(Path(*parts[:-1])) if len(parts) > 1 else ""
        except ValueError:
            return Path(p).parent.name

    subs_a  = {_rel(p, root_a) for p in dur_a if _rel(p, root_a)}
    subs_b  = {_rel(p, root_b) for p in dur_b if _rel(p, root_b)}
    only_a  = sorted(subs_a - subs_b)
    only_b  = sorted(subs_b - subs_a)
    in_both = sorted(subs_a & subs_b)

    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.C}  Folder Comparison{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.W}  {name_a:<30}{clr.RST}  {clr.Y}{format_duration(sec_a)['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_a} files{clr.RST}")
    print(f"  {clr.W}  {name_b:<30}{clr.RST}  {clr.Y}{format_duration(sec_b)['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_b} files{clr.RST}")
    print()
    delta_col = clr.G if delta_sec >= 0 else clr.R
    # B-02 fix: use proper f-string format expression (not escaped braces)
    print(
        f"  {clr.W}  {'Delta':<25}{clr.RST}  "
        f"{delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{clr.RST}  "
        f"{clr.DIM}|{clr.RST}  {delta_col}{delta_csign}{delta_count} files{clr.RST}"
    )
    print()

    if only_a:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.Y}  Only in {name_a}{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in only_a:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
    if only_b:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.Y}  Only in {name_b}{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in only_b:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
    if in_both:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.G}  In both{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in in_both:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
