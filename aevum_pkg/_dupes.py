import hashlib

from ._color import R, G, Y, C, W, DIM, RST, LINE
from ._scan  import format_duration, format_size


def _file_fingerprint(path, size, chunk=65536):
    h = hashlib.sha1()
    try:
        with open(path, 'rb') as f:
            h.update(f.read(chunk))
            if size > chunk * 2:
                f.seek(-chunk, 2)
                h.update(f.read(chunk))
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(durations, sizes=None):
    """
    Find duplicate files by size + partial hash.
    Returns a list of groups (each group is a list of Paths with 2+ copies).
    """
    sizes = sizes or {}
    by_size = {}
    for path in durations:
        sz = sizes.get(path)
        if sz is None:
            try:
                sz = path.stat().st_size
            except OSError:
                continue
        by_size.setdefault(sz, []).append(path)

    groups = []
    for sz, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash = {}
        for path in paths:
            fp = _file_fingerprint(path, sz)
            if fp:
                by_hash.setdefault(fp, []).append(path)
        for fp, members in by_hash.items():
            if len(members) >= 2:
                groups.append(members)
    return groups


def print_duplicates(groups, durations):
    if not groups:
        print(f"  {G}No duplicates found.{RST}\n")
        return

    total_wasted_sec = 0.0
    print(f"  {C}{LINE}{RST}")
    print(f"  {R}  Duplicate Groups Found: {len(groups)}{RST}")
    print(f"  {C}{LINE}{RST}")
    print()

    for i, group in enumerate(groups, start=1):
        sec    = durations.get(group[0], 0.0)
        wasted = sec * (len(group) - 1)
        total_wasted_sec += wasted
        fmt    = format_duration(sec)
        print(f"  {Y}Group {i}{RST}  {DIM}|{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {R}{len(group)} copies{RST}  {DIM}(wasted: {format_duration(wasted)['hours_fmt']}){RST}")
        for path in group:
            print(f"      {DIM}→{RST}  {Y}{path.name}{RST}")
            print(f"         {DIM}{path}{RST}")
        print()

    wasted_fmt = format_duration(total_wasted_sec)
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Total wasted time  {DIM}:{RST}  {R}{wasted_fmt['hours_fmt']}{RST}  {DIM}({wasted_fmt['days_fmt']}){RST}")
    print(f"  {C}{LINE}{RST}")
    print()


def print_dupe_warning(groups):
    if not groups:
        return
    total     = sum(len(g) - 1 for g in groups)
    grp_word  = "group"  if len(groups) == 1 else "groups"
    file_word = "file"   if total == 1        else "files"
    print(f"  {Y}⚠  {len(groups)} duplicate {grp_word} found ({total} redundant {file_word}){RST}  "
          f"{DIM}— press 6 or type 'duplicates' for details{RST}\n")
