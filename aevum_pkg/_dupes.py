import hashlib

from ._color import clr, LINE
from ._scan  import format_duration, format_size


def _file_fingerprint(path, size, chunk=65536):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
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

    Two files are considered duplicates when they share the same byte-size
    *and* have identical SHA-1 fingerprints computed from the first and last
    64 KiB of their content.

    Returns a list of groups; each group is a list of Paths with 2+ copies.
    """
    sizes = sizes or {}

    # Group paths by file size first — only paths that share a size are
    # candidates for a more expensive hash comparison.
    by_size: dict[int, list] = {}
    for path in durations:
        sz = sizes.get(path)
        if sz is None:
            try:
                sz = path.stat().st_size
            except OSError:
                continue
        if sz > 0:                     # zero-byte files are never "duplicates"
            by_size.setdefault(sz, []).append(path)

    groups = []
    for sz, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: dict[str, list] = {}
        for path in paths:
            fp = _file_fingerprint(path, sz)
            if fp:
                by_hash.setdefault(fp, []).append(path)
        for members in by_hash.values():
            if len(members) >= 2:
                groups.append(members)
    return groups


def print_duplicates(groups, durations):
    if not groups:
        print(f"  {clr.G}No duplicates found.{clr.RST}\n")
        return

    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.R}  Duplicate Groups Found: {len(groups)}{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()

    total_wasted_sec = 0.0
    for i, group in enumerate(groups, start=1):
        # Use the median duration of the group rather than blindly taking
        # group[0], so the "wasted time" estimate is more accurate when
        # copies have slightly different durations (e.g. transcoded files
        # that happen to share the same partial hash).
        group_secs = [durations.get(p, 0.0) for p in group]
        group_secs.sort()
        median_sec = group_secs[len(group_secs) // 2]
        wasted     = sum(group_secs) - median_sec   # every copy except the "keeper"
        total_wasted_sec += wasted

        fmt = format_duration(median_sec)
        print(
            f"  {clr.Y}Group {i}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{fmt['hours_fmt']}{clr.RST}"
            f"  {clr.DIM}|{clr.RST}  {clr.R}{len(group)} copies{clr.RST}"
            f"  {clr.DIM}(wasted: {format_duration(wasted)['hours_fmt']}){clr.RST}"
        )
        for path in group:
            print(f"      {clr.DIM}\u2192{clr.RST}  {clr.Y}{path.name}{clr.RST}")
            print(f"         {clr.DIM}{path}{clr.RST}")
        print()

    wasted_fmt = format_duration(total_wasted_sec)
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.W}  Total wasted time  {clr.DIM}:{clr.RST}  {clr.R}{wasted_fmt['hours_fmt']}{clr.RST}  {clr.DIM}({wasted_fmt['days_fmt']}){clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()


def print_dupe_warning(groups, folder=None):
    if not groups:
        return
    total     = sum(len(g) - 1 for g in groups)
    grp_word  = "group"  if len(groups) == 1 else "groups"
    file_word = "file"   if total == 1        else "files"
    cmd = f"aevum dupes {folder}" if folder else "aevum dupes <path>"
    print(
        f"  {clr.Y}\u26a0  {len(groups)} duplicate {grp_word} found ({total} redundant {file_word}){clr.RST}\n"
        f"  {clr.DIM}To see details, run:{clr.RST}  {clr.W}{cmd}{clr.RST}\n"
    )
