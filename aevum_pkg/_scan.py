import fnmatch
import os
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ._cache import load_cache, save_cache

# How many ffprobe processes to run at once.
# ffprobe is both CPU and disk-bound; scaling beyond 2× cores gives no gain
# on spinning disks and hurts on SSDs too. Cap at 8 for safety.
MAX_WORKERS = min(8, (os.cpu_count() or 4) * 2)

video_extensions = (
    # Common video
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv',
    '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
    '.vob', '.ogv', '.divx', '.rmvb', '.asf', '.m2ts',
    # Less common video
    '.mts', '.m2v', '.f4v', '.f4p', '.nsv', '.roq',
    '.yuv', '.mxf', '.drc', '.gifv', '.mng', '.qt',
    '.rm', '.amv', '.svi', '.3g2', '.mpe', '.mpv',
    '.m1v', '.m2p', '.m4p', '.mpeg1', '.mpeg2',
    '.mpeg4', '.h264', '.h265', '.hevc', '.avchd',
    '.ogm', '.ogx', '.dv', '.dvr', '.dvr-ms', '.rec',
    '.wtv', '.bdmv', '.iso', '.evo', '.ifo', '.mod',
    '.tod', '.trp', '.tp', '.pva', '.nuv', '.fli',
    '.flc', '.flic', '.smk', '.bik', '.bik2', '.webp',
    # Audio
    '.mp3', '.aac', '.flac', '.wav', '.ogg', '.wma',
    '.m4a', '.opus', '.aiff', '.aif', '.aifc', '.ape',
    '.wv', '.tta', '.mka', '.mpa', '.mp2', '.ac3',
    '.eac3', '.dts', '.dtshd', '.truehd', '.thd',
    '.pcm', '.caf', '.ra', '.ram', '.oga', '.spx',
    '.amr', '.awb', '.gsm', '.au', '.snd', '.vox',
    '.8svx', '.iff', '.svx', '.f32', '.f64', '.s8',
    '.s16', '.s24', '.s32', '.u8', '.u16', '.u24',
    '.u32', '.w64', '.rf64', '.bwf', '.mid', '.midi',
    '.kar', '.xmf', '.mxmf', '.rtttl', '.rtx', '.ota',
    '.imy', '.mp1',
)

# Frozenset of the same extensions for O(1) membership testing in hot paths.
_VIDEO_EXT_SET = frozenset(video_extensions)


def check_ffprobe():
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except (FileNotFoundError, OSError):
        return False


def _read_mp4_duration(path):
    """Seek through MP4 atoms without reading full file into memory."""
    try:
        file_size = os.path.getsize(path)
        with open(path, 'rb') as f:
            def read_atom(limit_end):
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None, None, 0
                size = struct.unpack('>I', hdr[:4])[0]
                name = hdr[4:8]
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return None, None, 0
                    size = struct.unpack('>Q', ext)[0]
                    header_size = 16
                else:
                    header_size = 8
                if size == 0:
                    size = limit_end - (f.tell() - header_size)
                return name, size, header_size

            pos = 0
            while pos < file_size:
                f.seek(pos)
                name, size, hdr_size = read_atom(file_size)
                if name is None or size < hdr_size:
                    break
                if name == b'moov':
                    moov_end = pos + size
                    inner = pos + hdr_size
                    while inner < moov_end:
                        f.seek(inner)
                        iname, isize, ihdr = read_atom(moov_end)
                        if iname is None or isize < ihdr:
                            break
                        if iname == b'mvhd':
                            box = f.read(min(isize - ihdr, 40))
                            if not box:
                                break
                            version = box[0]
                            min_size = 32 if version == 1 else 20
                            if len(box) < min_size:
                                break
                            if version == 1:
                                ts  = struct.unpack_from('>I', box, 16)[0]
                                dur = struct.unpack_from('>Q', box, 20)[0]
                            else:
                                ts  = struct.unpack_from('>I', box, 12)[0]
                                dur = struct.unpack_from('>I', box, 16)[0]
                            return dur / ts if ts else 0.0
                        inner += isize
                    break
                pos += size
    except Exception:
        pass
    return None


def _read_mkv_duration(path):
    """Read duration from MKV/WEBM by scanning EBML for the Segment/Info block.

    Issue 3 fix: increased read size from 2 MB to 8 MB so that Info blocks
    placed after large Tracks/SeekHead structures are still found without
    falling back to ffprobe.
    """
    try:
        with open(path, 'rb') as f:
            data = f.read(min(8 * 1024 * 1024, os.path.getsize(path)))

        def read_vint(buf, pos):
            if pos >= len(buf):
                return 0, pos + 1
            b = buf[pos]
            if b == 0:
                return 0, len(buf)
            width = 1
            mask  = 0x80
            while not (b & mask) and width <= 8:
                width += 1
                mask >>= 1
            val = b & (mask - 1)
            for k in range(1, width):
                if pos + k >= len(buf):
                    break
                val = (val << 8) | buf[pos + k]
            return val, pos + width

        def read_id(buf, pos):
            if pos >= len(buf):
                return 0, pos + 1
            b = buf[pos]
            width = 1
            mask  = 0x80
            while not (b & mask) and width <= 4:
                width += 1
                mask >>= 1
            val = int.from_bytes(buf[pos:pos+width], 'big')
            return val, pos + width

        timescale_ns = 1_000_000
        i = 0
        while i + 4 <= len(data):
            eid, i   = read_id(data, i)
            esize, i = read_vint(data, i)
            if eid == 0x1549A966:  # Info
                end      = i + esize
                j        = i
                duration = None
                while j < end - 4:
                    fid, j   = read_id(data, j)
                    fsize, j = read_vint(data, j)
                    if fid == 0x2AD7B1:
                        timescale_ns = int.from_bytes(data[j:j+fsize], 'big')
                    elif fid == 0x4489:
                        raw = data[j:j+fsize]
                        if fsize == 4:
                            duration = struct.unpack('>f', raw)[0]
                        elif fsize == 8:
                            duration = struct.unpack('>d', raw)[0]
                        # else: unknown size — skip, fall through to ffprobe
                    if fsize == 0:
                        continue  # zero-length field is valid; don't abort the block
                    j += fsize
                if duration is not None:
                    return duration * timescale_ns / 1_000_000_000
                return None
            elif 0 < esize < 0x100000:
                i += esize
            else:
                i += 1
    except Exception:
        pass
    return None


def get_duration(path):
    """
    Try fast native parse first; fall back to ffprobe if needed.
    
    Security: Uses subprocess with list form (never shell=True) to prevent
    command injection attacks. Path is converted to string safely.
    """
    ext    = Path(path).suffix.lower()
    result = None
    if ext in ('.mp4', '.mov', '.m4v', '.3gp', '.3g2', '.m4a', '.m4p', '.m4b', '.mp4v', '.f4v', '.f4a'):
        result = _read_mp4_duration(path)
    elif ext in ('.mkv', '.webm', '.mka', '.mk3d'):
        result = _read_mkv_duration(path)
    if result is not None and result > 0:
        return result
    
    # Security: ALWAYS use list form with shell=False to prevent command injection
    # str(path) safely converts Path to string without shell interpretation
    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False  # Explicitly set to False (default, but explicit is safer)
        )
        val = proc.stdout.strip()
        return float(val) if val and val != 'N/A' else 0.0
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, OSError):
        return 0.0


def format_size(b):
    """Return human-readable file size."""
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.2f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def format_duration(seconds):
    # Issue 6: clamp negatives so delta formatting never produces garbage output
    seconds = max(0.0, float(seconds))
    days    = int(seconds // 86400)
    hours   = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    return {
        "days_fmt":    f"{days}d {hours:02}h {minutes:02}m {secs:02}s",
        "hours_fmt":   f"{int(seconds // 3600):02}h {minutes:02}m {secs:02}s",
        "minutes_fmt": f"{int(seconds // 60)}m {secs:02}s",
    }


def scan_parallel(root, on_progress=None, stop_event=None, sort_by="name", cache=None, _visited_inodes=None):
    """
    Parallel scan: collector thread discovers files and submits them to the
    thread pool.  Returns (total_sec, total_count, tree_tuple, durations,
    sizes, hits).

    Security: Detects symlink loops and limits recursion depth to prevent DoS.

    Issue 2 fix: `total` is read inside the lock inside probe() so the
    progress callback never sees a torn value.

    Issue 7 fix: collector thread is fully joined before as_completed() is
    called, so no futures submitted near the end are silently dropped.

    Issue 4 fix: files returning 0.0 duration are excluded so the reported
    file count matches real, readable media files.
    """
    if _visited_inodes is None:
        _visited_inodes = set()

    root = Path(root).resolve()

    try:
        root_stat  = root.stat()
        root_inode = (root_stat.st_dev, root_stat.st_ino)

        if root_inode in _visited_inodes:
            print(f"  Warning: Symlink loop detected: {root}", file=sys.stderr)
            return 0.0, 0, ([], [], 0), {}, {}, 0

        _visited_inodes.add(root_inode)
    except OSError as e:
        print(f"  Warning: Cannot access {root}: {e}", file=sys.stderr)
        return 0.0, 0, ([], [], 0), {}, {}, 0

    durations = {}
    sizes     = {}
    done      = 0
    total     = 0
    hits      = 0
    lock      = threading.Lock()
    if cache is None:
        cache = {}
    
    # Security: Maximum recursion depth to prevent DoS
    MAX_DEPTH = 30
    root_depth = len(root.parts)

    def probe(path):
        nonlocal done, hits
        if stop_event and stop_event.is_set():
            return path, 0.0, 0
        key = str(path.resolve())
        if os.name == "nt":
            key = key.lower()  # Issue 22: normalise on Windows
        if key in cache:
            try:
                st    = path.stat()
                entry = cache[key]
                if st.st_mtime == entry["mtime"] and st.st_size == entry["size"]:
                    with lock:
                        done += 1
                        hits += 1
                        _snap_done  = done   # read both under lock
                        _snap_total = total
                    if on_progress and _snap_total > 0:
                        on_progress(_snap_done, _snap_total)
                    return path, entry["duration"], int(entry.get("size", 0))
            except OSError:
                pass
        sec = get_duration(path)
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        with lock:
            done += 1
            _snap_done  = done   # read both under lock
            _snap_total = total
        if on_progress and _snap_total > 0:
            on_progress(_snap_done, _snap_total)
        return path, sec, file_size

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}

        def collect_and_submit():
            nonlocal total
            # Security: Track (path, depth) to prevent excessive recursion
            stack = [(str(root), root_depth)]
            visited_dirs = set()
            
            while stack:
                if stop_event and stop_event.is_set():
                    break
                
                current, depth = stack.pop()
                
                # Security: Limit recursion depth
                if depth - root_depth > MAX_DEPTH:
                    continue
                
                # Security: Detect directory loops via inode
                try:
                    current_stat = Path(current).stat()
                    current_inode = (current_stat.st_dev, current_stat.st_ino)
                    
                    if current_inode in visited_dirs:
                        continue  # Skip already visited directory
                    
                    visited_dirs.add(current_inode)
                except OSError:
                    continue  # Skip inaccessible directories
                
                try:
                    with os.scandir(current) as it:
                        for entry in it:
                            if stop_event and stop_event.is_set():
                                return
                            
                            # Security: Skip symlinks or resolve and check for loops
                            try:
                                if entry.is_symlink():
                                    # Resolve symlink and check if it's already visited
                                    resolved = Path(entry.path).resolve(strict=True)
                                    resolved_stat = resolved.stat()
                                    resolved_inode = (resolved_stat.st_dev, resolved_stat.st_ino)
                                    
                                    if resolved_inode in _visited_inodes or resolved_inode in visited_dirs:
                                        continue  # Skip symlink loop
                                    
                                    if entry.is_dir(follow_symlinks=True):
                                        stack.append((entry.path, depth + 1))
                                    elif entry.is_file(follow_symlinks=True):
                                        if os.path.splitext(entry.name)[1].lower() in _VIDEO_EXT_SET:
                                            p = Path(entry.path)
                                            with lock:
                                                total += 1
                                            futures[pool.submit(probe, p)] = p
                                else:
                                    # Not a symlink, process normally
                                    if entry.is_dir(follow_symlinks=False):
                                        stack.append((entry.path, depth + 1))
                                    elif entry.is_file(follow_symlinks=False):
                                        if os.path.splitext(entry.name)[1].lower() in _VIDEO_EXT_SET:
                                            p = Path(entry.path)
                                            with lock:
                                                total += 1
                                            futures[pool.submit(probe, p)] = p
                            except (OSError, RuntimeError):
                                # Skip broken symlinks or inaccessible entries
                                continue
                except PermissionError:
                    pass

        collector = threading.Thread(target=collect_and_submit, daemon=True)
        collector.start()

        # Issue 7: join collector BEFORE consuming futures so every future
        # that was submitted is visible to as_completed().
        collector.join()

        if stop_event and stop_event.is_set():
            subfolders, direct, root_bytes = _build_tree(root, {}, sort_by)
            return 0.0, 0, (subfolders, direct, root_bytes), {}, {}, 0

        try:
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    break
                path, sec, file_size = future.result()
                # Issue 4: skip files whose duration could not be determined
                if sec > 0.0:
                    durations[path] = sec
                    sizes[path]     = file_size
        except KeyboardInterrupt:
            if stop_event:
                stop_event.set()
            raise

    if not durations:
        subfolders, direct, root_bytes = _build_tree(root, {}, sort_by)
        return 0.0, 0, (subfolders, direct, root_bytes), {}, {}, 0

    total_sec   = sum(durations.values())
    total_count = len(durations)
    subfolders, direct, root_bytes = _build_tree(root, durations, sort_by, sizes)
    return total_sec, total_count, (subfolders, direct, root_bytes), durations, sizes, hits


def _build_tree(root, durations, sort_by="name:asc", sizes=None):
    """O(n) tree builder.

    Issue 5 fix: ancestor walk is capped at MAX_DEPTH to prevent an
    infinite loop on symlink cycles.
    """
    MAX_DEPTH = 200
    if ':' not in sort_by:
        defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
        sort_by  = sort_by + ':' + defaults.get(sort_by, 'asc')
    sort_field, sort_dir = sort_by.split(':', 1)
    sort_rev = (sort_dir == 'desc')
    root     = Path(root)
    sizes    = sizes or {}

    folder_secs   = {}
    folder_bytes  = {}
    folder_count  = {}
    folder_direct = {}

    for path, sec in durations.items():
        file_bytes = sizes.get(path, 0)
        folder_direct.setdefault(path.parent, []).append((path, sec))
        ancestor = path.parent
        depth    = 0
        while depth < MAX_DEPTH:
            folder_secs[ancestor]  = folder_secs.get(ancestor, 0.0) + sec
            folder_bytes[ancestor] = folder_bytes.get(ancestor, 0) + file_bytes
            folder_count[ancestor] = folder_count.get(ancestor, 0) + 1
            if ancestor == root:
                break
            next_ancestor = ancestor.parent
            if next_ancestor == ancestor:
                break
            ancestor = next_ancestor
            depth   += 1

    known_folders: set = set(folder_secs.keys()) | set(folder_direct.keys())
    children_of: dict = {}
    for folder in known_folders:
        if folder == root:
            continue
        parent = folder.parent
        if parent in known_folders or parent == root:
            children_of.setdefault(parent, set()).add(folder)

    def build(node):
        subfolders  = []
        child_paths = sorted(
            children_of.get(node, set()),
            key=lambda p: (
                folder_secs.get(p, 0.0)   if sort_field == "duration" else
                folder_count.get(p, 0)    if sort_field == "count"    else
                p.name.lower()
            ),
            reverse=sort_rev,
        )
        for child in child_paths:
            secs         = folder_secs.get(child, 0.0)
            count        = folder_count.get(child, 0)
            fbytes       = folder_bytes.get(child, 0)
            direct_files = folder_direct.get(child, [])
            direct_count = len(direct_files)
            if count == 0:
                subfolders.append((child.name, 0.0, 0, 0, 0, [], []))
                continue
            child_subs, child_direct = build(child)
            subfolders.append((child.name, secs, count, fbytes, direct_count, child_subs, child_direct))
        direct = sorted(
            folder_direct.get(node, []),
            key=lambda x: (
                x[1]              if sort_field == "duration" else
                x[0].name.lower()
            ),
            reverse=sort_rev,
        )
        return subfolders, direct

    subfolders, direct = build(root)
    root_bytes = folder_bytes.get(root, 0)
    return subfolders, direct, root_bytes


def _run_scan(folder, on_progress, sort_by="name", use_cache=True):
    """
    Run scan_parallel with optional cache.
    Returns (total_sec, total_count, tree, durations, sizes, hits).
    """
    folder     = Path(folder)
    cache      = load_cache(folder) if use_cache else {}
    stop_event = threading.Event()
    try:
        result = scan_parallel(folder, on_progress, stop_event, sort_by, cache)
    except KeyboardInterrupt:
        stop_event.set()
        raise
    total_sec, total_count, tree, durations, sizes, hits = result
    if durations and hits < len(durations):
        save_cache(folder, durations)
    return result


def parse_duration_arg(s):
    """
    Parse a human duration string into seconds (float).
    Accepts: 30s, 5m, 1h, 1h30m, 90m, 1:30:00, 5400, 1.5h
    Raises ValueError if the string cannot be parsed.
    Note: colon format treats first group as hours e.g. 1:30 = 1h30m = 5400s.
    """
    s = s.strip().lower()
    try:
        return min(float(s), 365 * 24 * 3600)
    except ValueError:
        pass
    m = re.fullmatch(r'(\d+):(\d{1,2})(?::(\d{1,2}))?', s)
    if m:
        h, mn, sc = m.groups()
        return min(int(h) * 3600 + int(mn) * 60 + int(sc or 0), 365 * 24 * 3600)
    total = 0.0
    found = False
    for value, unit in re.findall(r'(\d+(?:\.\d+)?)([hms])', s):
        found = True
        v = float(value)
        if unit == 'h':   total += v * 3600
        elif unit == 'm': total += v * 60
        elif unit == 's': total += v
    if found:
        return min(total, 365 * 24 * 3600)  # cap at 1 year
    raise ValueError(f"Cannot parse duration: '{s}'  (try: 30s, 5m, 1h, 1h30m, 1:30:00)")


def apply_filters(durations, sizes, filters):
    """
    Filter a durations dict by the given filter dict.
    filters keys (all optional):
      min_duration  float seconds
      max_duration  float seconds
      exts          set of lowercase extensions with dot e.g. {'.mkv', '.mp4'}
      folder_pat    glob pattern matched against path.parent.name (case-insensitive)
    Returns (filtered_durations, filtered_sizes).
    """
    min_dur    = filters.get('min_duration')
    max_dur    = filters.get('max_duration')
    exts       = filters.get('exts')
    folder_pat = filters.get('folder_pat')

    out_dur  = {}
    out_size = {}
    for path, sec in durations.items():
        if min_dur is not None and sec < min_dur:
            continue
        if max_dur is not None and sec > max_dur:
            continue
        if exts is not None and path.suffix.lower() not in exts:
            continue
        if folder_pat is not None:
            if not fnmatch.fnmatch(path.parent.name.lower(), folder_pat.lower()):
                continue
        out_dur[path]  = sec
        out_size[path] = sizes.get(path, 0)
    return out_dur, out_size


def rebuild_after_filter(root, durations, sizes, sort_by="name:asc"):
    """Re-run tree builder and totals after filters have been applied."""
    if not durations:
        subfolders, direct, root_bytes = _build_tree(root, {}, sort_by)
        return 0.0, 0, (subfolders, direct, root_bytes), durations, sizes
    total_sec   = sum(durations.values())
    total_count = len(durations)
    subfolders, direct, root_bytes = _build_tree(root, durations, sort_by, sizes)
    return total_sec, total_count, (subfolders, direct, root_bytes), durations, sizes
