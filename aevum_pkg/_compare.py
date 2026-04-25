from pathlib import Path

from ._color import clr, LINE
from ._scan  import format_duration, _run_scan


def run_compare(folder_a, folder_b, on_progress, sort_by, use_cache):
    print(f"  {clr.DIM}Scanning {Path(folder_a).name}...{clr.RST}", end="", flush=True)
    sec_a, count_a, tree_a, dur_a, _, _ = _run_scan(folder_a, on_progress, sort_by, use_cache)
    print(f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{Path(folder_a).name}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_a} files  {format_duration(sec_a)['hours_fmt']}{clr.RST}".ljust(70))

    print(f"  {clr.DIM}Scanning {Path(folder_b).name}...{clr.RST}", end="", flush=True)
    sec_b, count_b, tree_b, dur_b, _, _ = _run_scan(folder_b, on_progress, sort_by, use_cache)
    print(f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{Path(folder_b).name}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_b} files  {format_duration(sec_b)['hours_fmt']}{clr.RST}".ljust(70))

    return (sec_a, count_a, dur_a), (sec_b, count_b, dur_b)


def print_comparison(folder_a, folder_b, data_a, data_b):
    sec_a, count_a, dur_a = data_a
    sec_b, count_b, dur_b = data_b
    name_a = Path(folder_a).name
    name_b = Path(folder_b).name

    delta_sec   = sec_b   - sec_a
    delta_count = count_b - count_a
    delta_sign  = "+" if delta_sec   >= 0 else ""
    delta_csign = "+" if delta_count >= 0 else ""

    subs_a  = {p.parent.name for p in dur_a}
    subs_b  = {p.parent.name for p in dur_b}
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
    print(f"  {clr.W}  Delta{'':<25}{clr.RST}  {delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {delta_col}{delta_csign}{delta_count} files{clr.RST}")
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
