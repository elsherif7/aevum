from __future__ import annotations

import codecs
import os
import struct
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ._models import FolderNode, ScanTree
from ._text import warn

# ffprobe is CPU and disk bound, so more than 2x the core count doesn't help.
MAX_WORKERS = min(8, (os.cpu_count() or 4) * 2)

# A cold disk or a network share can be slow to answer for a single file.
PROBE_TIMEOUT = 30

# Folders more than this many levels below the scan root are not scanned.
MAX_DEPTH = 100

# '.iso' (disc images, too large) and '.dat' (too generic) are deliberately left out.
video_extensions = (
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
    '.vob', '.ogv', '.divx', '.rmvb', '.asf', '.m2ts', '.mts', '.m2v', '.f4v', '.f4a', '.f4p', '.nsv', '.roq',
    '.yuv', '.mxf', '.drc', '.gifv', '.qt', '.rm', '.amv', '.svi', '.3g2', '.mpe', '.mpv', '.m1v',
    '.m2p', '.m4p', '.mpeg1', '.mpeg2', '.mpeg4', '.h264', '.h265', '.hevc', '.avchd', '.ogm', '.ogx',
    '.dv', '.dvr', '.dvr-ms', '.rec', '.wtv', '.bdmv', '.evo', '.ifo', '.mod', '.tod', '.trp', '.tp',
    '.pva', '.nuv', '.fli', '.flc', '.flic', '.smk', '.bik', '.bik2', '.av1', '.avs', '.avs2', '.avs3',
    '.cavs', '.cdg', '.cdxl', '.cine', '.cpk', '.dhav', '.dif', '.dl', '.dpg', '.ea', '.flh', '.flt',
    '.gxf', '.h261', '.h263', '.ifv', '.imf', '.ipu', '.ivf', '.ivr', '.kux', '.lxf', '.m2t', '.m4s',
    '.mjpeg', '.mjpg', '.mlv', '.moflex', '.mods', '.mpl', '.mtv', '.mv', '.mvi', '.mxg', '.pmp',
    '.psxstr', '.rpl', '.scm', '.seq', '.sfd', '.swf', '.thp', '.ty', '.ty+', '.vc1', '.viv', '.vivo',
    '.vp6', '.vp8', '.vp9', '.vqf', '.wve', '.y4m', '.mp3', '.aac', '.flac', '.wav', '.ogg', '.wma',
    '.m4a', '.m4b', '.opus', '.aiff', '.aif', '.aifc', '.ape', '.wv', '.tta', '.mka', '.mk3d', '.mpa', '.mp2', '.ac3',
    '.eac3', '.dts', '.dtshd', '.truehd', '.thd', '.pcm', '.caf', '.ra', '.ram', '.oga', '.spx', '.amr',
    '.awb', '.gsm', '.au', '.snd', '.vox', '.8svx', '.iff', '.svx', '.f32', '.f64', '.s8', '.s16',
    '.s24', '.s32', '.u8', '.u16', '.u24', '.u32', '.w64', '.rf64', '.bwf', '.mid', '.midi', '.kar',
    '.xmf', '.mxmf', '.rtttl', '.rtx', '.ota', '.imy', '.mp1', '.aa', '.aax', '.ace', '.acm', '.act',
    '.adp', '.ads', '.adts', '.afc', '.aix', '.apac', '.apc', '.avr', '.bfstm', '.binka', '.bonk',
    '.brstm', '.dss', '.dsf', '.dff', '.fwse', '.g722', '.g723', '.g726', '.g728', '.g729', '.hca',
    '.hcom', '.laf', '.latm', '.loas', '.mca', '.mpc', '.msf', '.nsp', '.osq', '.pp_bnk', '.pvf',
    '.qcp', '.qoa', '.rka', '.rsd', '.sb0', '.sb1', '.sb2', '.sd2', '.shn', '.sln', '.tak', '.vag',
    '.voc', '.vpk', '.wsd', '.xa', '.xwb',
)

_VIDEO_EXT_SET = frozenset(video_extensions)

_MP4_EXTENSIONS = frozenset(('.mp4', '.mov', '.m4v', '.3gp', '.3g2', '.m4a', '.m4p', '.m4b', '.f4v', '.f4a'))
_MKV_EXTENSIONS = frozenset(('.mkv', '.webm', '.mka', '.mk3d'))

# Also used for text (TypeScript, MOD/SCM source), so these are sniffed before probing.
_TEXT_AMBIGUOUS_EXTENSIONS = frozenset(('.ts', '.mod', '.scm'))


def _looks_like_text(path) -> bool:
    try:
        with open(path, 'rb') as f:
            head = f.read(4096)
        if b'\0' in head:
            return False
        # final=False so a character cut off at the 4 KiB boundary isn't an error
        codecs.getincrementaldecoder('utf-8')().decode(head)
        return True
    except (OSError, UnicodeDecodeError):
        return False


def check_ffprobe() -> bool:
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except (FileNotFoundError, OSError):
        return False


def _read_mp4_duration(path):
    """Read the duration from the moov/mvhd atom, seeking instead of loading the file."""
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
                            # after version+flags, v1 has two 8-byte timestamps and v0 two 4-byte ones
                            if version == 1:
                                ts  = struct.unpack_from('>I', box, 20)[0]
                                dur = struct.unpack_from('>Q', box, 24)[0]
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
    """
    Read the duration from the EBML Info block. Reads 2 MB first and retries with
    8 MB if Info isn't found, which covers Info placed after a large Tracks or SeekHead.
    """
    def _try_parse(data):

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
        MAX_ITERATIONS = 100_000
        iterations = 0
        while i + 4 <= len(data):
            iterations += 1
            if iterations > MAX_ITERATIONS:
                return None
            eid, i   = read_id(data, i)
            esize, i = read_vint(data, i)
            if eid == 0x1549A966:  # Info
                end      = i + esize
                j        = i
                duration = None
                while j < end - 4:
                    fid, j    = read_id(data, j)
                    fsize, j  = read_vint(data, j)
                    field_start = j
                    if fid == 0x2AD7B1:
                        timescale_ns = int.from_bytes(data[j:j+fsize], 'big')
                    elif fid == 0x4489:
                        raw = data[j:j+fsize]
                        if fsize == 4:
                            duration = struct.unpack('>f', raw)[0]
                        elif fsize == 8:
                            duration = struct.unpack('>d', raw)[0]
                    if j + fsize > end:  # a bad field size would run past Info
                        break
                    j = field_start + fsize
                if duration is not None:
                    return duration * timescale_ns / 1_000_000_000
                return None
            elif eid == 0x18538067:  # Segment: step into it, its children follow the header
                continue
            elif eid == 0x1F43B675:  # Cluster: the media data starts here, so Info is not coming
                return None
            else:
                i += esize
        return None

    try:
        file_size = os.path.getsize(path)
        SMALL_READ = 2 * 1024 * 1024
        LARGE_READ = 8 * 1024 * 1024
        with open(path, 'rb') as f:
            data = f.read(min(SMALL_READ, file_size))
        result = _try_parse(data)
        if result is not None:
            return result
        if file_size > SMALL_READ:
            with open(path, 'rb') as f:
                data = f.read(min(LARGE_READ, file_size))
            return _try_parse(data)
        return None
    except Exception:
        return None


def _probe_duration(path: str | Path) -> tuple[float, bool]:
    """Return (seconds, timed_out). Seconds is 0.0 when the duration can't be read."""
    ext    = Path(path).suffix.lower()
    result = None
    if ext in _MP4_EXTENSIONS:
        result = _read_mp4_duration(path)
    elif ext in _MKV_EXTENSIONS:
        result = _read_mkv_duration(path)
    if result is not None and result > 0:
        return result, False

    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            shell=False
        )
        val = proc.stdout.strip()
        return (float(val) if val and val != 'N/A' else 0.0), False
    except subprocess.TimeoutExpired:
        warn(f"ffprobe timed out on: {path}")
        return 0.0, True
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0.0, False


def get_duration(path: str | Path) -> float:
    """Native parse for MP4 and MKV, ffprobe for everything else."""
    return _probe_duration(path)[0]


def format_size(b: int) -> str:
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.2f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def format_duration(seconds: float) -> dict[str, str]:
    # clamp to 0..100 years so a bad value can't produce garbage output
    seconds = max(0.0, min(float(seconds), 100 * 365 * 86400))
    days    = int(seconds // 86400)
    hours   = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    return {
        "days_fmt":    f"{days}d {hours:02}h {minutes:02}m {secs:02}s",
        "hours_fmt":   f"{int(seconds // 3600):02}h {minutes:02}m {secs:02}s",
        "minutes_fmt": f"{int(seconds // 60)}m {secs:02}s",
    }


def scan_parallel(
    root: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    stop_event: threading.Event | None = None,
    _visited_inodes: set | None = None,
    stats: dict[str, int] | None = None,
) -> tuple[float, int, ScanTree, dict[Path, float], dict[Path, int]]:
    """
    Scan root for media files. Returns (total_sec, total_count, tree, durations, sizes).

    A collector thread walks the folders and submits files to a thread pool.
    Files whose duration can't be read are left out of the totals and counted. A file reachable by more
    than one path (a hardlink, or a symlink to a file) is counted once: the real
    file wins over a symlink, then the alphabetically first path. If stats is
    given, it is filled with duplicates_skipped, unreadable_files, timed_out,
    unreadable_dirs and skipped_deep.
    """
    if _visited_inodes is None:
        _visited_inodes = set()
    if stop_event is None:
        stop_event = threading.Event()

    root = Path(root).resolve()

    try:
        root_stat  = root.stat()
        root_inode = (root_stat.st_dev, root_stat.st_ino)

        if root_inode in _visited_inodes:
            warn(f"Symlink loop detected: {root}")
            return 0.0, 0, ScanTree([], [], 0), {}, {}

        _visited_inodes.add(root_inode)
    except OSError as e:
        warn(f"Cannot access {root}: {e}")
        if stats is not None:
            stats["unreadable_dirs"] = 1
        return 0.0, 0, ScanTree([], [], 0), {}, {}

    durations = {}
    sizes     = {}
    done      = 0
    total     = 0
    lock      = threading.Lock()

    root_depth = len(root.parts)
    unreadable_dirs = 0
    skipped_deep    = 0

    def probe(path, is_link):
        nonlocal done
        if stop_event and stop_event.is_set():
            return path, 0.0, 0, None, is_link, False
        try:
            sec, timed_out = _probe_duration(path)
        except Exception:
            sec, timed_out = 0.0, False
        file_id = None
        try:
            st        = path.stat()
            file_size = st.st_size
            if st.st_ino:   # some filesystems report 0, so files can't be told apart
                file_id = (st.st_dev, st.st_ino)
        except OSError:
            file_size = 0
        with lock:
            done += 1
            _snap_done  = done
            _snap_total = total
        if on_progress and _snap_total > 0:
            on_progress(_snap_done, _snap_total)
        return path, sec, file_size, file_id, is_link, timed_out

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}

        def submit(entry, is_link):
            nonlocal total
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in _VIDEO_EXT_SET:
                return
            if ext in _TEXT_AMBIGUOUS_EXTENSIONS and _looks_like_text(entry.path):
                return
            p = Path(entry.path)
            with lock:
                total += 1
            futures[pool.submit(probe, p, is_link)] = p

        def collect_and_submit():
            nonlocal unreadable_dirs, skipped_deep
            stack = [(str(root), root_depth)]
            visited_dirs = set()

            while stack:
                if stop_event and stop_event.is_set():
                    break

                current, depth = stack.pop()

                if depth - root_depth > MAX_DEPTH:
                    skipped_deep += 1
                    continue

                # skip folders already seen, which also stops symlink loops
                try:
                    current_stat = Path(current).stat()
                    current_inode = (current_stat.st_dev, current_stat.st_ino)

                    if current_inode in visited_dirs:
                        continue

                    visited_dirs.add(current_inode)
                except OSError:
                    unreadable_dirs += 1
                    continue

                try:
                    with os.scandir(current) as it:
                        for entry in it:
                            if stop_event and stop_event.is_set():
                                return

                            try:
                                if entry.is_symlink():
                                    resolved = Path(entry.path).resolve(strict=True)
                                    resolved_stat = resolved.stat()
                                    resolved_inode = (resolved_stat.st_dev, resolved_stat.st_ino)

                                    if resolved_inode in _visited_inodes or resolved_inode in visited_dirs:
                                        continue

                                    if entry.is_dir(follow_symlinks=True):
                                        stack.append((entry.path, depth + 1))
                                    elif entry.is_file(follow_symlinks=True):
                                        submit(entry, True)
                                else:
                                    if entry.is_dir(follow_symlinks=False):
                                        stack.append((entry.path, depth + 1))
                                    elif entry.is_file(follow_symlinks=False):
                                        submit(entry, False)
                            except (OSError, RuntimeError):
                                # broken symlink or unreadable entry
                                continue
                except OSError:
                    unreadable_dirs += 1

        collector = threading.Thread(target=collect_and_submit, daemon=True)
        collector.start()

        # join before reading the futures so as_completed() sees every submitted one
        try:
            collector.join()
        except KeyboardInterrupt:
            # stop the workers and drop queued files, so leaving the `with` block
            # doesn't wait for every remaining ffprobe call
            stop_event.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise

        if stop_event and stop_event.is_set():
            tree = _build_tree(root, {})
            return 0.0, 0, tree, {}, {}

        found = []   # (path, sec, size, file_id, is_link, timed_out)
        try:
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    break
                found.append(future.result())
        except KeyboardInterrupt:
            stop_event.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise

    # keep one path per underlying file: real file first, then name order
    best: dict[tuple, tuple] = {}
    dropped = 0
    ordered = sorted(found, key=lambda r: (r[4], str(r[0])))
    for item in ordered:
        path, sec, file_size, file_id, _, _ = item
        if sec <= 0.0:
            continue
        if file_id is not None:
            if file_id in best:
                dropped += 1
                continue
            best[file_id] = item
        durations[path] = sec
        sizes[path]     = file_size

    unreadable_files = 0
    timed_out_files  = 0
    bad_ids: set[tuple] = set()
    for path, sec, _, file_id, _, timed_out in ordered:
        if sec > 0.0:
            continue
        if file_id is not None:
            if file_id in best or file_id in bad_ids:
                continue
            bad_ids.add(file_id)
        unreadable_files += 1
        timed_out_files  += timed_out

    if stats is not None:
        stats["duplicates_skipped"] = dropped
        stats["unreadable_files"]   = unreadable_files
        stats["timed_out"]          = timed_out_files
        stats["unreadable_dirs"]    = unreadable_dirs
        stats["skipped_deep"]       = skipped_deep

    if not durations:
        tree = _build_tree(root, {})
        return 0.0, 0, tree, {}, {}

    total_sec   = sum(durations.values())
    total_count = len(durations)
    tree = _build_tree(root, durations, sizes)
    return total_sec, total_count, tree, durations, sizes


def _build_tree(root, durations, sizes=None) -> ScanTree:
    """
    Build the folder tree in O(n), with children and files sorted by name.
    The ancestor walk is capped at MAX_DEPTH so a symlink cycle can't loop forever.
    """
    root      = Path(root)
    sizes     = sizes or {}

    folder_secs:   dict[Path, float] = {}
    folder_bytes:  dict[Path, int]   = {}
    folder_count:  dict[Path, int]   = {}
    folder_direct: dict[Path, list]  = {}

    for path, sec in durations.items():
        file_bytes = sizes.get(path, 0)
        folder_direct.setdefault(path.parent, []).append((path, sec))
        ancestor = path.parent
        depth    = 0
        while depth <= MAX_DEPTH:
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
        child_nodes = []
        child_paths = sorted(
            children_of.get(node, set()),
            key=lambda p: p.name.lower(),
        )
        for child in child_paths:
            secs         = folder_secs.get(child, 0.0)
            count        = folder_count.get(child, 0)
            fbytes       = folder_bytes.get(child, 0)
            child_children, child_direct = build(child)
            child_nodes.append(FolderNode(
                name=child.name,
                total_sec=secs,
                total_count=count,
                total_bytes=fbytes,
                children=child_children,
                direct_files=child_direct,
            ))
        direct = sorted(
            folder_direct.get(node, []),
            key=lambda x: x[0].name.lower(),
        )
        return child_nodes, direct

    children, direct_files = build(root)
    root_bytes = folder_bytes.get(root, 0)
    return ScanTree(children=children, direct_files=direct_files, root_bytes=root_bytes)


def _run_scan(folder, on_progress, stats=None):
    folder     = Path(folder)
    stop_event = threading.Event()
    try:
        result = scan_parallel(folder, on_progress, stop_event, stats=stats)
    except KeyboardInterrupt:
        stop_event.set()
        raise
    return result
