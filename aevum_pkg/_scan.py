from __future__ import annotations

import fnmatch
import os
import re
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ._models import FolderNode, ScanTree

# How many ffprobe processes to run at once.
# ffprobe is both CPU and disk-bound; scaling beyond 2× cores gives no gain
# on spinning disks and hurts on SSDs too. Cap at 8 for safety.
MAX_WORKERS = min(8, (os.cpu_count() or 4) * 2)

video_extensions = (
    # ── Common video ─────────────────────────────────────────────────
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv',
    '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
    '.vob', '.ogv', '.divx', '.rmvb', '.asf', '.m2ts',
    # ── Less common video ────────────────────────────────────────────
    '.mts', '.m2v', '.f4v', '.f4p', '.nsv', '.roq',
    '.yuv', '.mxf', '.drc', '.gifv', '.mng', '.qt',
    '.rm', '.amv', '.svi', '.3g2', '.mpe', '.mpv',
    '.m1v', '.m2p', '.m4p', '.mpeg1', '.mpeg2',
    '.mpeg4', '.h264', '.h265', '.hevc', '.avchd',
    '.ogm', '.ogx', '.dv', '.dvr', '.dvr-ms', '.rec',
    '.wtv', '.bdmv', '.evo', '.ifo', '.mod',  # '.iso' removed — disc images too large
    '.tod', '.trp', '.tp', '.pva', '.nuv', '.fli',
    '.flc', '.flic', '.smk', '.bik', '.bik2', '.webp',
    # ── Additional video formats ─────────────────────────────────────
    '.av1',                          # AV1 raw bitstream
    '.avif',                         # AV1 Image File Format (video sequences)
    '.avs', '.avs2', '.avs3',        # AVS / AVS2 / AVS3 (Chinese standards)
    '.cavs',                         # Chinese AVS video
    '.cdg',                          # CD+G karaoke video
    '.cdxl',                         # Commodore CDXL
    '.cine',                         # Phantom Cine high-speed camera
    '.cpk',                          # Sega CRI CPK container
    # '.dat',                        # too generic — matches Windows/game data files
    '.dhav',                         # Dahua DVR video
    '.dif',                          # DV interchange format
    '.dl',                           # DL animation
    '.dpg',                          # Nintendo DS DPG video
    '.dv',                           # DV raw video
    '.dvr',                          # DVR recordings
    '.ea',                           # Electronic Arts video
    '.flh', '.flt',                  # FLIC variants
    '.gxf',                          # General eXchange Format (broadcast)
    '.h261', '.h263',                # Raw H.261 / H.263 bitstreams
    '.ifv',                          # IFV CCTV DVR
    '.imf',                          # Interoperable Master Format
    '.ipu',                          # Raw IPU video
    '.ivf',                          # IVF (VP8/VP9/AV1 raw container)
    '.ivr',                          # IVR Internet Video Recording
    '.kux',                          # KUX (YouKu)
    '.lxf',                          # VR native stream
    '.m2t',                          # MPEG-2 transport stream (alt ext)
    '.m4s',                          # MPEG-DASH segment
    '.mjpeg', '.mjpg',               # Motion JPEG
    '.mlv',                          # Magic Lantern Video
    '.mng',                          # Multiple-image Network Graphics
    '.moflex',                       # MobiClip MOFLEX
    '.mods',                         # MobiClip MODS
    '.mpl',                          # Multiplexed video
    '.msf',                          # Sony PS3 MSF
    '.mtv',                          # MTV video
    '.mv',                           # Silicon Graphics Movie
    '.mvi',                          # Motion Pixels MVI
    '.mxg',                          # MxPEG clip
    '.pmp',                          # PlayStation Portable PMP
    '.psxstr', '.str',               # Sony PlayStation STR
    '.rpl',                          # RPL / ARMovie
    '.scm',                          # Scala Multimedia
    '.seq',                          # Tiertex SEQ
    '.sfd',                          # Sega Film / CPK
    '.sol',                          # Sierra SOL
    '.swf',                          # ShockWave Flash (video content)
    '.thp',                          # Nintendo THP video
    '.ty', '.ty+',                   # TiVo TY stream
    '.vc1',                          # Raw VC-1 bitstream
    '.viv', '.vivo',                 # VivoActive video
    '.vp6', '.vp8', '.vp9',          # Raw VP6 / VP8 / VP9
    '.vqf',                          # TwinVQ video
    '.wve',                          # Psion WVE
    '.y4m',                          # YUV4MPEG2 raw video
    '.yuv',                          # Raw YUV (already above, kept for clarity)
    # ── Common audio ─────────────────────────────────────────────────
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
    # ── Additional audio formats ─────────────────────────────────────
    '.aa',                           # Audible AA audiobook
    '.aax',                          # Audible AAX (enhanced audiobook)
    '.ace',                          # tri-Ace Audio Container
    '.acm',                          # Interplay ACM audio
    '.act',                          # ACT Voice recorder
    '.adp', '.ads',                  # ADP / Sony PS2 ADS
    '.adts',                         # ADTS raw AAC
    '.afc',                          # AFC audio
    '.aix',                          # CRI AIX audio
    '.apac',                         # Raw APAC
    '.apc',                          # CRYO APC audio
    '.avr',                          # AVR (Audio Visual Research)
    '.bfstm',                        # BFSTM (Nintendo Binary Cafe Stream)
    '.binka',                        # Bink Audio
    '.bonk',                         # Bonk audio
    '.brstm',                        # BRSTM (Binary Revolution Stream)
    '.dss',                          # Digital Speech Standard
    '.dsf',                          # DSD Stream File
    '.dff',                          # DSDIFF (DSD Interchange File Format)
    '.fwse',                         # Capcom MT Framework sound
    '.g722', '.g723', '.g726',       # ITU-T G.7xx raw audio
    '.g728', '.g729',                # ITU-T G.728 / G.729
    '.hca',                          # CRI HCA audio
    '.hcom',                         # Macintosh HCOM
    '.laf',                          # Limitless Audio Format
    '.latm',                         # LOAS/LATM AAC
    '.loas',                         # LOAS AudioSyncStream
    '.mca',                          # MCA Audio Format
    '.mpc',                          # Musepack
    '.msf',                          # Sony PS3 MSF audio
    '.nsp',                          # Computerized Speech Lab NSP
    '.osq',                          # Raw OSQ lossless audio
    '.pp_bnk',                       # Pro Pinball Soundbank
    '.pvf',                          # Portable Voice Format
    '.qcp',                          # QCP (QCELP) mobile audio
    '.qoa',                          # Quite OK Audio
    '.rka',                          # RKA audio
    '.rsd',                          # RSD audio
    '.sb0', '.sb1', '.sb2',          # Sound Blaster audio banks
    '.sd2',                          # Sound Designer II
    '.shn',                          # Shorten lossless audio
    '.sln',                          # Asterisk raw signed linear
    '.tak',                          # Tom's lossless Audio Kompressor
    '.thd',                          # Dolby TrueHD (already above)
    '.tta',                          # True Audio (already above)
    '.vag',                          # Sony PS VAG audio
    '.voc',                          # Creative Voice File
    '.vpk',                          # Sony PS2 VPK audio
    '.w64',                          # Sony Wave64 (already above)
    '.wsd',                          # Wideband Single-bit Data
    '.xa',                           # Sony PS XA audio
    '.xwb',                          # Microsoft XWB (Xbox audio bank)
)

# Frozenset of the same extensions for O(1) membership testing in hot paths.
_VIDEO_EXT_SET = frozenset(video_extensions)


def check_ffprobe() -> bool:
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
                # H-05: guard against zero-size top-level atom causing infinite loop
                if size == 0:
                    break
                if name == b'moov':
                    moov_end = pos + size
                    inner = pos + hdr_size
                    while inner < moov_end:
                        f.seek(inner)
                        iname, isize, ihdr = read_atom(moov_end)
                        if iname is None or isize < ihdr:
                            break
                        # H-04: guard against zero-size inner atom causing infinite loop
                        if isize == 0:
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

    P-01 fix: use a two-pass strategy — try 2 MB first (covers most files),
    then retry with 8 MB only if the Info block was not found.  This reduces
    average memory usage from 8 MB/file to ~2 MB/file for typical MKVs.
    """
    file_size = os.path.getsize(path)

    def _try_parse(data):
        """Inner parser — returns duration float or None."""

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
                    field_start = j          # save position after header
                    if fid == 0x2AD7B1:
                        timescale_ns = int.from_bytes(data[j:j+fsize], 'big')
                    elif fid == 0x4489:
                        raw = data[j:j+fsize]
                        if fsize == 4:
                            duration = struct.unpack('>f', raw)[0]
                        elif fsize == 8:
                            duration = struct.unpack('>d', raw)[0]
                    # B-05: bounds check — malformed fsize could jump j past end
                    if j + fsize > end:
                        break
                    j = field_start + fsize  # always advance past field data
                if duration is not None:
                    return duration * timescale_ns / 1_000_000_000
                return None
            elif 0 < esize < 0x100000:
                i += esize
            else:
                i += max(1, min(esize, 65536))  # never advance by 1 byte for large elements
        return None

    try:
        # P-01: two-pass strategy — try 2 MB first (covers most MKV files),
        # then retry with 8 MB only if the Info block was not found.
        SMALL_READ = 2 * 1024 * 1024
        LARGE_READ = 8 * 1024 * 1024
        with open(path, 'rb') as f:
            data = f.read(min(SMALL_READ, file_size))
        result = _try_parse(data)
        if result is not None:
            return result
        # Info block not found in first 2 MB — retry with 8 MB
        if file_size > SMALL_READ:
            with open(path, 'rb') as f:
                data = f.read(min(LARGE_READ, file_size))
            return _try_parse(data)
        return None
    except Exception:
        return None


def get_duration(path: str | Path) -> float:
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
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, OSError) as _e:
        if isinstance(_e, subprocess.TimeoutExpired):
            print(f"  [WARN] ffprobe timed out on: {path}", file=sys.stderr)
        return 0.0


def format_size(b: int) -> str:
    """Return human-readable file size."""
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.2f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def format_duration(seconds: float) -> dict[str, str]:
    # Issue 6: clamp negatives so delta formatting never produces garbage output
    # H-07: clamp to reasonable max (100 years) to prevent integer overflow
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
    sort_by: str = "name",
    _visited_inodes: set | None = None,
) -> tuple[float, int, ScanTree, dict[Path, float], dict[Path, int]]:
    """
    Parallel scan: collector thread discovers files and submits them to the
    thread pool.  Returns (total_sec, total_count, tree_tuple, durations,
    sizes).

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
            return 0.0, 0, ScanTree([], [], 0), {}, {}

        _visited_inodes.add(root_inode)
    except OSError as e:
        print(f"  Warning: Cannot access {root}: {e}", file=sys.stderr)
        return 0.0, 0, ScanTree([], [], 0), {}, {}

    durations = {}
    sizes     = {}
    done      = 0
    total     = 0
    lock      = threading.Lock()

    # Security: Maximum recursion depth to prevent DoS
    MAX_DEPTH = 30
    root_depth = len(root.parts)

    def probe(path):
        nonlocal done
        if stop_event and stop_event.is_set():
            return path, 0.0, 0
        sec = get_duration(path)
        try:
            st        = path.stat()
            file_size = st.st_size
        except OSError:
            file_size = 0
        with lock:
            done += 1
            _snap_done  = done
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
            tree = _build_tree(root, {}, sort_by)
            return 0.0, 0, tree, {}, {}

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
        tree = _build_tree(root, {}, sort_by)
        return 0.0, 0, tree, {}, {}

    total_sec   = sum(durations.values())
    total_count = len(durations)
    tree = _build_tree(root, durations, sort_by, sizes)
    return total_sec, total_count, tree, durations, sizes


def _build_tree(root, durations, sort_by="name:asc", sizes=None) -> ScanTree:
    """O(n) tree builder.  Returns a ScanTree of FolderNode objects.

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

    folder_secs:   dict[Path, float] = {}
    folder_bytes:  dict[Path, int]   = {}
    folder_count:  dict[Path, int]   = {}
    folder_direct: dict[Path, list]  = {}

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
        child_nodes = []
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
                child_nodes.append(FolderNode(
                    name=child.name, total_sec=0.0, total_count=0,
                    total_bytes=0, direct_count=0, children=[], direct_files=[],
                ))
                continue
            child_children, child_direct = build(child)
            child_nodes.append(FolderNode(
                name=child.name,
                total_sec=secs,
                total_count=count,
                total_bytes=fbytes,
                direct_count=direct_count,
                children=child_children,
                direct_files=child_direct,
            ))
        direct = sorted(
            folder_direct.get(node, []),
            key=lambda x: (
                x[1]              if sort_field == "duration" else
                x[0].name.lower()
            ),
            reverse=sort_rev,
        )
        return child_nodes, direct

    children, direct_files = build(root)
    root_bytes = folder_bytes.get(root, 0)
    return ScanTree(children=children, direct_files=direct_files, root_bytes=root_bytes)


def _run_scan(folder, on_progress, sort_by="name"):
    """
    Run scan_parallel.
    Returns (total_sec, total_count, tree, durations, sizes).
    """
    folder     = Path(folder)
    stop_event = threading.Event()
    try:
        result = scan_parallel(folder, on_progress, stop_event, sort_by)
    except KeyboardInterrupt:
        stop_event.set()
        raise
    return result


def parse_since_arg(s: str) -> float:
    """
    Parse a --since / --until argument into a UTC timestamp (float).
    Accepts:
      7d, 30d, 1w, 2w        — relative: N days/weeks ago
      2025-01-15             — absolute date (midnight local time)
      2025-01-15T10:30       — absolute datetime
    Raises ValueError on bad input.
    """
    import re as _re
    import time as _time
    s = s.strip()
    # Relative: Nd or Nw
    m = _re.fullmatch(r'(\d+)([dDwW])', s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * 7 if unit == 'w' else n
        return _time.time() - days * 86400
    # Absolute date or datetime
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            import datetime as _dt
            dt = _dt.datetime.strptime(s, fmt)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{s}'  (try: 7d, 30d, 2w, 2025-01-15)")


def parse_duration_arg(s: str) -> float:
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
        if unit == 'h':
            total += v * 3600
        elif unit == 'm':
            total += v * 60
        elif unit == 's':
            total += v
    if found:
        return min(total, 365 * 24 * 3600)  # cap at 1 year
    raise ValueError(f"Cannot parse duration: '{s}'  (try: 30s, 5m, 1h, 1h30m, 1:30:00)")


def apply_filters(
    durations: dict[Path, float],
    sizes: dict[Path, int],
    filters: dict,
) -> tuple[dict[Path, float], dict[Path, int]]:
    """
    Filter a durations dict by the given filter dict.
    filters keys (all optional):
      min_duration  float seconds
      max_duration  float seconds
      exts          set of lowercase extensions with dot e.g. {'.mkv', '.mp4'}
      folder_pat    glob pattern matched against path.parent.name (case-insensitive)
      exclude       set of lowercase folder name patterns to skip (glob, case-insensitive)
      since         float — only include files with mtime >= this timestamp
      until         float — only include files with mtime <= this timestamp
    Returns (filtered_durations, filtered_sizes).
    """
    min_dur      = filters.get('min_duration')
    max_dur      = filters.get('max_duration')
    exts         = filters.get('exts')
    folder_pat   = filters.get('folder_pat')
    exclude_pats = filters.get('exclude')
    since_ts     = filters.get('since')
    until_ts     = filters.get('until')

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
        if exclude_pats:
            folder_name = path.parent.name.lower()
            if any(fnmatch.fnmatch(folder_name, pat) for pat in exclude_pats):
                continue
        if since_ts is not None or until_ts is not None:
            try:
                mtime = path.stat().st_mtime
                if since_ts is not None and mtime < since_ts:
                    continue
                if until_ts is not None and mtime > until_ts:
                    continue
            except OSError:
                pass
        out_dur[path]  = sec
        out_size[path] = sizes.get(path, 0)
    return out_dur, out_size


def rebuild_after_filter(
    root: str | Path,
    durations: dict[Path, float],
    sizes: dict[Path, int],
    sort_by: str = "name:asc",
) -> tuple[float, int, ScanTree, dict[Path, float], dict[Path, int]]:
    """Re-run tree builder and totals after filters have been applied."""
    if not durations:
        tree = _build_tree(root, {}, sort_by)
        return 0.0, 0, tree, durations, sizes
    total_sec   = sum(durations.values())
    total_count = len(durations)
    tree = _build_tree(root, durations, sort_by, sizes)
    return total_sec, total_count, tree, durations, sizes
