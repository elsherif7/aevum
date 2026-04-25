from pathlib import Path

from ._color import R, G, Y, C, W, DIM, RST, LINE
from ._scan  import format_duration, _run_scan


def run_compare(folder_a, folder_b, on_progress, sort_by, use_cache):
    print(f"  {DIM}Scanning {Path(folder_a).name}...{RST}", end='', flush=True)
    sec_a, count_a, tree_a, dur_a, _, _ = _run_scan(folder_a, on_progress, sort_by, use_cache)
    print(f"\r  {G}Done{RST}  {DIM}→{RST}  {W}{Path(folder_a).name}{RST}  {DIM}|{RST}  {Y}{count_a} files  {format_duration(sec_a)['hours_fmt']}{RST}".ljust(70))

    print(f"  {DIM}Scanning {Path(folder_b).name}...{RST}", end='', flush=True)
    sec_b, count_b, tree_b, dur_b, _, _ = _run_scan(folder_b, on_progress, sort_by, use_cache)
    print(f"\r  {G}Done{RST}  {DIM}→{RST}  {W}{Path(folder_b).name}{RST}  {DIM}|{RST}  {Y}{count_b} files  {format_duration(sec_b)['hours_fmt']}{RST}".ljust(70))

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

    subs_a   = {p.parent.name for p in dur_a}
    subs_b   = {p.parent.name for p in dur_b}
    only_a   = sorted(subs_a - subs_b)
    only_b   = sorted(subs_b - subs_a)
    in_both  = sorted(subs_a & subs_b)

    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Folder Comparison{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}  {name_a:<30}{RST}  {Y}{format_duration(sec_a)['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{count_a} files{RST}")
    print(f"  {W}  {name_b:<30}{RST}  {Y}{format_duration(sec_b)['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{count_b} files{RST}")
    print()
    delta_col = G if delta_sec >= 0 else R
    print(f"  {W}  Delta{'':<25}{RST}  {delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{RST}  {DIM}|{RST}  {delta_col}{delta_csign}{delta_count} files{RST}")
    print()

    if only_a:
        print(f"  {C}{LINE}{RST}")
        print(f"  {Y}  Only in {name_a}{RST}")
        print(f"  {C}{LINE}{RST}")
        for s in only_a:
            print(f"    {DIM}→{RST}  {s}")
        print()

    if only_b:
        print(f"  {C}{LINE}{RST}")
        print(f"  {Y}  Only in {name_b}{RST}")
        print(f"  {C}{LINE}{RST}")
        for s in only_b:
            print(f"    {DIM}→{RST}  {s}")
        print()

    if in_both:
        print(f"  {C}{LINE}{RST}")
        print(f"  {G}  In both{RST}")
        print(f"  {C}{LINE}{RST}")
        for s in in_both:
            print(f"    {DIM}→{RST}  {s}")
        print()
