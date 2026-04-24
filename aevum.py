import argparse
import csv
import hashlib
import json
import struct
import subprocess
import sys
import os
import threading
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

__version__ = "1.0.0"

# Enable ANSI colors on Windows
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _handle = kernel32.GetStdHandle(-11)
    if _handle and _handle != -1:
        kernel32.SetConsoleMode(_handle, 7)

R   = "\033[91m"
G   = "\033[92m"
Y   = "\033[93m"
B   = "\033[94m"
M   = "\033[95m"
C   = "\033[96m"
W   = "\033[97m"
DIM = "\033[2m"
RST = "\033[0m"

LINE = "=" * 64

video_extensions = (
    # Common video
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv',
    '.wmv', '.m4v', '.mpg', '.mpeg', '.3gp', '.ts',
    '.vob', '.ogv', '.divx', '.rmvb', '.asf', '.m2ts',
    # Less common video
    '.mts', '.m2v', '.f4v', '.f4p', '.nsv', '.roq',
    '.yuv', '.mxf', '.drc', '.gifv', '.mng', '.qt',
    '.rm', '.amv', '.svi', '.3g2', '.mpe', '.mpv',
    '.m1v', '.m2p', '.m4p', '.mp2', '.mpeg1', '.mpeg2',
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
    '.imy', '.mp1', '.m3u', '.pls', '.xspf',
)

# How many ffprobe processes to run at once.
# Capped at 32 — beyond that, disk I/O becomes the bottleneck on HDDs/network shares.
MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "cache"

# ── CACHE ─────────────────────────────────────────────────────────────

def _cache_key(root):
    """Stable filename for the cache of a given root folder."""
    h = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"

def load_cache(root):
    """
    Load the cache for this root folder.
    Returns a dict mapping absolute path string -> {mtime, size, duration}.
    Returns {} if no cache exists or it is unreadable.
    """
    path = _cache_key(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {entry["path"]: entry for entry in data}
    except Exception:
        return {}

def save_cache(root, durations):
    """
    Persist durations to the cache file for this root folder.
    durations: dict mapping Path -> seconds (float)
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for p, sec in durations.items():
            try:
                st = p.stat()
                entries.append({
                    "path":     str(p.resolve()),
                    "mtime":    st.st_mtime,
                    "size":     st.st_size,
                    "duration": sec,
                })
            except OSError:
                pass
        _cache_key(root).write_text(
            json.dumps(entries, indent=None, separators=(',', ':')),
            encoding="utf-8"
        )
    except Exception:
        pass  # cache write failure is never fatal

# ── HELPERS ───────────────────────────────────────────────────────────

def clear():
    print('\033[2J\033[H', end='', flush=True)

def check_ffprobe():
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True)
        return True
    except FileNotFoundError:
        return False

# ── YOUTUBE API SUPPORT ───────────────────────────────────────────────

YT_API_KEY_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "yt_api_key.txt"
YT_API_BASE     = "https://www.googleapis.com/youtube/v3"

def _is_url(s):
    return s.startswith(('http://', 'https://')) or s.startswith('www.')

def _parse_iso8601_duration(d):
    """Parse ISO 8601 duration string like PT1H2M3S → seconds (float)."""
    import re
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', d or '')
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)

def _yt_api_request(endpoint, params, api_key):
    """Make a single YouTube Data API v3 GET request. Returns parsed JSON or raises."""
    import urllib.request, urllib.parse
    params['key'] = api_key
    url = f"{YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

def load_api_key():
    """Load the saved API key, or return None."""
    try:
        key = YT_API_KEY_FILE.read_text(encoding='utf-8').strip()
        return key if key else None
    except Exception:
        return None

def save_api_key(key):
    try:
        YT_API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_API_KEY_FILE.write_text(key.strip(), encoding='utf-8')
    except Exception:
        pass

def prompt_api_key():
    """Interactively ask the user for their YouTube API key and save it."""
    print()
    print(f"  {Y}YouTube API key required.{RST}")
    print(f"  {DIM}Get a free key in ~2 minutes:{RST}")
    print(f"  {C}1.{RST} Go to {W}https://console.cloud.google.com/{RST}")
    print(f"  {C}2.{RST} Create a project → Enable {W}YouTube Data API v3{RST}")
    print(f"  {C}3.{RST} Credentials → Create API Key → copy it here")
    print()
    try:
        key = input(f"  {C}Paste API key{RST}> ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not key:
        return None
    save_api_key(key)
    print(f"  {G}Key saved to {YT_API_KEY_FILE}{RST}")
    print()
    return key

def _parse_yt_url(url):
    """
    Parse a YouTube URL and return (kind, id) where kind is:
    'video', 'playlist', 'channel_id', 'channel_handle'
    """
    from urllib.parse import urlparse, parse_qs
    p  = urlparse(url)
    qs = parse_qs(p.query)
    path_parts = [x for x in p.path.split('/') if x]
    netloc = p.netloc.replace('www.', '')

    if netloc not in ('youtube.com', 'youtu.be', 'm.youtube.com'):
        return None, None

    # Playlist (takes priority over video if both present)
    if 'list' in qs:
        return 'playlist', qs['list'][0]

    # youtu.be/<id>
    if netloc == 'youtu.be' and path_parts:
        return 'video', path_parts[0]

    # watch?v=
    if 'v' in qs:
        return 'video', qs['v'][0]

    # /shorts/<id>
    if len(path_parts) == 2 and path_parts[0] == 'shorts':
        return 'video', path_parts[1]

    # /@handle  /c/name  /user/name  /channel/<id>
    if path_parts:
        if path_parts[0].startswith('@'):
            return 'channel_handle', path_parts[0]
        if path_parts[0] in ('c', 'user') and len(path_parts) >= 2:
            return 'channel_handle', path_parts[1]
        if path_parts[0] == 'channel' and len(path_parts) >= 2:
            return 'channel_id', path_parts[1]

    return None, None

def _yt_get_channel_uploads_playlist(channel_id_or_handle, api_key):
    """Resolve a channel handle/id to its uploads playlist id."""
    # Try by handle (forHandle param) first, then by id
    for param_key, param_val in [('forHandle', channel_id_or_handle), ('id', channel_id_or_handle)]:
        try:
            data = _yt_api_request('channels', {
                'part': 'contentDetails,snippet',
                param_key: param_val,
            }, api_key)
            items = data.get('items', [])
            if items:
                uploads = items[0]['contentDetails']['relatedPlaylists']['uploads']
                title   = items[0]['snippet']['title']
                return uploads, title
        except Exception:
            continue
    return None, None

def _yt_fetch_playlist_video_ids(playlist_id, api_key, on_progress=None):
    """Page through a playlist and collect all video IDs. Returns list of ids."""
    ids = []
    page_token = None
    while True:
        params = {
            'part':       'contentDetails',
            'playlistId': playlist_id,
            'maxResults': 50,
        }
        if page_token:
            params['pageToken'] = page_token
        try:
            data = _yt_api_request('playlistItems', params, api_key)
        except Exception as e:
            raise RuntimeError(f"playlistItems API error: {e}")
        for item in data.get('items', []):
            vid = item.get('contentDetails', {}).get('videoId')
            if vid:
                ids.append(vid)
        if on_progress:
            on_progress(len(ids), 0)
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    return ids

def _yt_fetch_video_details(video_ids, api_key):
    """
    Batch-fetch title + duration for a list of video IDs.
    Processes up to 50 per API call. Returns list of dicts.
    """
    entries = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        try:
            data = _yt_api_request('videos', {
                'part': 'snippet,contentDetails',
                'id':   ','.join(batch),
            }, api_key)
        except Exception as e:
            raise RuntimeError(f"videos API error: {e}")
        for item in data.get('items', []):
            title    = item['snippet']['title']
            channel  = item['snippet'].get('channelTitle', '')
            duration = _parse_iso8601_duration(item['contentDetails']['duration'])
            vid_url  = f"https://youtu.be/{item['id']}"
            entries.append({'title': title, 'duration': duration, 'url': vid_url, 'channel': channel})
    return entries

def scan_url(url, on_progress=None):
    """
    Fetch video durations for a YouTube URL via the Data API v3.
    Returns (total_sec, total_count, entries, label).
    Prompts for API key on first use (saved to disk for reuse).
    """
    api_key = load_api_key()
    if not api_key:
        api_key = prompt_api_key()
        if not api_key:
            return 0, 0, [], 'cancelled'

    kind, vid_id = _parse_yt_url(url)
    if kind is None:
        raise ValueError(f"Could not parse YouTube URL: {url}")

    label   = url
    entries = []

    if kind == 'video':
        entries = _yt_fetch_video_details([vid_id], api_key)
        label   = entries[0]['title'] if entries else vid_id

    elif kind == 'playlist':
        # Get playlist title
        try:
            pl_data = _yt_api_request('playlists', {'part': 'snippet', 'id': vid_id}, api_key)
            pl_items = pl_data.get('items', [])
            label = pl_items[0]['snippet']['title'] if pl_items else vid_id
        except Exception:
            label = vid_id
        ids = _yt_fetch_playlist_video_ids(vid_id, api_key, on_progress)
        if on_progress:
            print(f"\r  {C}Fetching video details...{RST}  {Y}{len(ids)} videos{RST}  {DIM}(this may take a moment){RST}".ljust(70), flush=True)
        entries = _yt_fetch_video_details(ids, api_key)

    elif kind in ('channel_id', 'channel_handle'):
        uploads_pl, channel_title = _yt_get_channel_uploads_playlist(vid_id, api_key)
        if not uploads_pl:
            raise ValueError(f"Could not find channel: {vid_id}")
        label = channel_title or vid_id
        ids = _yt_fetch_playlist_video_ids(uploads_pl, api_key, on_progress)
        if on_progress:
            print(f"\r  {C}Fetching video details...{RST}  {Y}{len(ids)} videos{RST}  {DIM}(this may take a moment){RST}".ljust(70), flush=True)
        entries = _yt_fetch_video_details(ids, api_key)

    total_sec   = sum(e['duration'] for e in entries)
    total_count = len(entries)
    return total_sec, total_count, entries, label


def print_url_results(url, label, total_sec, total_count, entries, top_n=10):
    fmt = format_duration(total_sec)
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  {label}{RST}")
    print(f"  {DIM}  {url[:70]}{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}  Total videos  {DIM}:{RST}  {W}{total_count}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {W}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {W}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {W}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Playback Speed{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        slabel = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {slabel:<6}        {DIM}:{RST}  {W}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()
    if entries and top_n > 0:
        ranked = sorted(entries, key=lambda e: e['duration'], reverse=True)[:top_n]
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Top {top_n} Longest Videos{RST}")
        print(f"  {C}{LINE}{RST}")
        for i, e in enumerate(ranked, start=1):
            dur_fmt = format_duration(e['duration'])
            print(f"  {DIM}{i:>2}.{RST}  {W}{dur_fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{e['title'][:60]}{RST}")
        print()


def _make_url_progress():
    """Progress callback for playlist ID collection."""
    def on_progress(done, _total):
        print(f"\r  {C}Collecting video IDs...{RST}  {Y}{done} found{RST}",
              end='', flush=True)
    return on_progress

# ── END YOUTUBE API SUPPORT ───────────────────────────────────────────

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
                if size == 1:  # 64-bit size
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
                    # enter moov, search for mvhd
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
                            # version 1: timescale at offset 20 (4 bytes), duration at 24 (8 bytes) — need 32
                            # version 0: timescale at offset 12 (4 bytes), duration at 16 (4 bytes) — need 20
                            min_size = 32 if version == 1 else 20
                            if len(box) < min_size:
                                break
                            if version == 1:
                                ts = struct.unpack_from('>I', box, 20)[0]
                                dur = struct.unpack_from('>Q', box, 24)[0]
                            else:
                                ts = struct.unpack_from('>I', box, 12)[0]
                                dur = struct.unpack_from('>I', box, 16)[0]
                            return dur / ts if ts else 0.0
                        inner += isize
                    break
                pos += size
    except Exception:
        pass
    return None

def _read_mkv_duration(path):
    """Read duration from MKV/WEBM by scanning EBML for the Segment/Info block."""
    try:
        with open(path, 'rb') as f:
            data = f.read(min(2 * 1024 * 1024, os.path.getsize(path)))

        def read_vint(buf, pos):
            if pos >= len(buf):
                return 0, pos + 1
            b = buf[pos]
            if b == 0:
                return 0, len(buf)  # invalid/reserved vint — signal parse failure
            width = 1
            mask = 0x80
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
            mask = 0x80
            while not (b & mask) and width <= 4:
                width += 1
                mask >>= 1
            val = int.from_bytes(buf[pos:pos+width], 'big')
            return val, pos + width

        timescale_ns = 1_000_000
        i = 0
        while i < len(data) - 4:
            eid, i = read_id(data, i)
            esize, i = read_vint(data, i)
            if eid == 0x1549A966:  # Info
                end = i + esize
                j = i
                duration = None
                while j < end - 4:
                    fid, j = read_id(data, j)
                    fsize, j = read_vint(data, j)
                    if fid == 0x2AD7B1:
                        timescale_ns = int.from_bytes(data[j:j+fsize], 'big')
                    elif fid == 0x4489:
                        raw = data[j:j+fsize]
                        duration = struct.unpack('>f', raw)[0] if fsize == 4 else struct.unpack('>d', raw)[0]
                    if fsize == 0:
                        break
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
    """Try fast native parse first; fall back to ffprobe if needed."""
    ext = Path(path).suffix.lower()
    result = None
    if ext in ('.mp4', '.mov', '.m4v', '.3gp', '.3g2', '.m4a', '.m4p', '.m4b', '.mp4v', '.f4v', '.f4a'):
        result = _read_mp4_duration(path)
    elif ext in ('.mkv', '.webm', '.mka', '.mk3d'):
        result = _read_mkv_duration(path)
    if result is not None and result > 0:
        return result
    # fallback to ffprobe for unsupported/failed formats
    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, text=True, timeout=15
        )
        val = proc.stdout.strip()
        return float(val) if val and val != 'N/A' else 0.0
    except Exception:
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
    days    = int(seconds // 86400)
    hours   = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    return {
        "days_fmt":    f"{days}d {hours:02}h {minutes:02}m {secs:02}s",
        "hours_fmt":   f"{int(seconds // 3600):02}h {minutes:02}m {secs:02}s",
        "minutes_fmt": f"{int(seconds // 60)}m {secs:02}s",
    }

def scan_parallel(root, on_progress=None, stop_event=None, sort_by="name", cache=None):
    """
    Parallel scan: collector thread discovers files and submits them to the thread
    pool. Results are drained after collection completes (not true streaming), so
    memory usage is O(n_files) in futures. Sufficient for libraries up to ~100k files.
    """
    root      = Path(root)
    durations = {}
    sizes     = {}
    done      = 0
    total     = 0
    hits      = 0   # files served from cache
    lock      = threading.Lock()
    cache     = cache or {}

    def probe(path):
        nonlocal done, hits
        if stop_event and stop_event.is_set():
            return path, 0.0, 0

        # Check cache: match on both mtime and size to detect re-encoded files
        key = str(path.resolve())
        if key in cache:
            try:
                st = path.stat()
                entry = cache[key]
                if st.st_mtime == entry["mtime"] and st.st_size == entry["size"]:
                    with lock:
                        done += 1
                        hits += 1
                        if on_progress and total > 0:
                            on_progress(done, total)
                    # return cached size — avoids a second stat() in as_completed
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
            if on_progress and total > 0:
                on_progress(done, total)
        return path, sec, file_size

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}

        def collect_and_submit():
            nonlocal total
            stack = [str(root)]
            while stack:
                if stop_event and stop_event.is_set():
                    break
                current = stack.pop()
                try:
                    with os.scandir(current) as it:
                        for entry in it:
                            if stop_event and stop_event.is_set():
                                return
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                if Path(entry.name).suffix.lower() in video_extensions:
                                    p = Path(entry.path)
                                    with lock:
                                        total += 1
                                    f = pool.submit(probe, p)
                                    futures[f] = p
                except PermissionError:
                    pass

        collector = threading.Thread(target=collect_and_submit, daemon=True)
        collector.start()
        # Join with periodic timeout so a hung network scandir doesn't block forever.
        # stop_event lets an external Ctrl+C propagate cleanly.
        while collector.is_alive():
            collector.join(timeout=1.0)
            if stop_event and stop_event.is_set():
                break

        try:
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    break
                path, sec, file_size = future.result()
                durations[path] = sec
                sizes[path] = file_size
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
    """
    O(n) tree builder — aggregate folder stats in a single pass over durations,
    then recursively assemble the tree structure from the pre-built dict.
    sort_by: 'name:asc' | 'name:desc' | 'duration:asc' | 'duration:desc' | 'count:asc' | 'count:desc'
    Also accepts bare 'name'|'duration'|'count' (defaults to asc for name, desc for others).
    """
    # Normalise sort_by to "field:dir"
    if ':' not in sort_by:
        defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
        sort_by = sort_by + ':' + defaults.get(sort_by, 'asc')
    sort_field, sort_dir = sort_by.split(':', 1)
    sort_rev = (sort_dir == 'desc')
    root = Path(root)

    sizes         = sizes or {}
    folder_secs   = {}  # folder path -> total seconds (recursive)
    folder_bytes  = {}  # folder path -> total bytes (recursive)
    folder_count  = {}  # folder path -> total video count (recursive)
    folder_direct = {}  # folder path -> list of (Path, sec) sitting directly inside

    for path, sec in durations.items():
        file_bytes = sizes.get(path, 0)
        # record this file as a direct child of its parent
        parent = path.parent
        folder_direct.setdefault(parent, []).append((path, sec))

        # bubble totals up to all ancestors including root.
        # Loop is path-based, not identity-based, so it correctly handles
        # drive roots (e.g. D:\) where parent.parent == parent on Windows.
        ancestor = path.parent
        while True:
            folder_secs[ancestor]  = folder_secs.get(ancestor, 0.0) + sec
            folder_bytes[ancestor] = folder_bytes.get(ancestor, 0) + file_bytes
            folder_count[ancestor] = folder_count.get(ancestor, 0) + 1
            if ancestor == root:
                break
            next_ancestor = ancestor.parent
            if next_ancestor == ancestor:
                # reached filesystem root without hitting scan root — stop
                break
            ancestor = next_ancestor

    def build(node):
        subfolders = []
        try:
            children = list(p for p in node.iterdir() if p.is_dir())
        except PermissionError:
            return subfolders, []

        if sort_field == "duration":
            children.sort(key=lambda p: folder_secs.get(p, 0.0), reverse=sort_rev)
        elif sort_field == "count":
            children.sort(key=lambda p: folder_count.get(p, 0), reverse=sort_rev)
        else:
            children.sort(reverse=sort_rev)

        for child in children:
            secs         = folder_secs.get(child, 0.0)
            count        = folder_count.get(child, 0)
            fbytes       = folder_bytes.get(child, 0)
            direct_files = folder_direct.get(child, [])
            direct_count = len(direct_files)
            if count == 0:
                # show the folder but do not recurse — no children displayed
                subfolders.append((child.name, 0.0, 0, 0, 0, [], []))
                continue
            child_subs, child_direct = build(child)
            subfolders.append((child.name, secs, count, fbytes, direct_count, child_subs, child_direct))

        # direct files sitting immediately inside this node
        direct = sorted(folder_direct.get(node, []), key=lambda x: x[1], reverse=True)
        return subfolders, direct

    subfolders, direct = build(root)
    root_bytes = folder_bytes.get(root, 0)
    return subfolders, direct, root_bytes

depth_colors = [R, G, B, M, C]

def print_tree(name, seconds, count, subfolders, direct=None, depth=0, number="", max_depth=50, show_files=False, direct_count=None, fbytes=0):
    if depth > max_depth:
        return
    PAD    = "    "
    indent = PAD * depth
    fmt    = format_duration(seconds)
    col    = depth_colors[depth % len(depth_colors)]
    label  = f"{number}.  {name}" if number else name

    if count == 0:
        print(f"{indent}{col}{label}{RST}")
        print(f"{indent}    {DIM}+--  (empty){RST}")
    else:
        print(f"{indent}{col}{label}{RST}")
        size_label = f"  {DIM}|{RST}  {W}{format_size(fbytes)}{RST}" if fbytes else ""
        print(f"{indent}    {DIM}+--{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{count} {'video' if count == 1 else 'videos'}{RST}{size_label}")

    print()
    # Show (no folder) virtual entry ONLY when there are also real subfolders
    if direct and subfolders:
        direct_sec   = sum(sec for _, sec in direct)
        direct_count = len(direct)
        dir_fmt      = format_duration(direct_sec)
        child_col    = depth_colors[(depth + 1) % len(depth_colors)]
        virt_num     = f"{number}.0" if number else "0"
        print(f"{indent}    {child_col}{virt_num}.  (no folder){RST}")
        dir_bytes = 0
        for p, _ in direct:
            try: dir_bytes += p.stat().st_size
            except OSError: pass
        dir_size_label = f"  {DIM}|{RST}  {W}{format_size(dir_bytes)}{RST}" if dir_bytes else ""
        print(f"{indent}        {DIM}+--{RST}  {W}{dir_fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{direct_count} {'video' if direct_count == 1 else 'videos'}{RST}{dir_size_label}")
        if show_files:
            print()
            for path, sec in direct:
                fd = format_duration(sec)
                print(f"{indent}        {DIM}|  {fd['hours_fmt']}  {path.name}{RST}")
        print()

    for i, (sub_name, sub_sec, sub_count, sub_fbytes, sub_direct_count, sub_sub, sub_direct) in enumerate(subfolders, start=1):
        sub_number = f"{number}.{i}" if number else str(i)
        print_tree(sub_name, sub_sec, sub_count, sub_sub, sub_direct, depth + 1, sub_number,
                   show_files=show_files, direct_count=sub_direct_count, fbytes=sub_fbytes)
    if subfolders:
        print()

def print_top_files(durations, n=10):
    """Print the N longest individual video files."""
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:n]
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Top {n} Longest Files{RST}")
    print(f"  {C}{LINE}{RST}")
    for i, (path, sec) in enumerate(ranked, start=1):
        fmt = format_duration(sec)
        name = path.name
        parent = path.parent.name
        print(f"  {DIM}{i:>2}.{RST}  {W}{fmt['hours_fmt']}{RST}  {DIM}|{RST}  {W}{name}{RST}  {DIM}({parent}){RST}")
    print()

def _tree_to_dict(name, seconds, count, subfolders, direct=None):
    """Recursively convert a tree tuple into a JSON-serialisable dict."""
    return {
        "name":      name,
        "seconds":   round(seconds, 2),
        "count":     count,
        "hours_fmt": format_duration(seconds)["hours_fmt"],
        "direct":    [{"file": p.name, "seconds": round(s, 2)} for p, s in (direct or [])],
        "children":  [_tree_to_dict(n, s, c, sub, d) for n, s, c, _fb, _dc, sub, d in subfolders],
    }

def export_results(folder, total_sec, total_count, tree, durations, fmt, out_path=None):
    """
    Export scan results to a file.
    fmt: 'txt' | 'csv' | 'json'
    out_path: explicit Path to write to, or None to auto-generate next to the scan folder.
    Returns the Path that was written.
    """
    folder   = Path(folder)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"aevum_{folder.name}_{stamp}.{fmt}"
    if out_path:
        dest = Path(out_path)
    else:
        # Prefer writing next to the scanned folder; fall back to Desktop
        # if the parent directory is read-only (e.g. external drive, network share).
        preferred = folder.parent / filename
        try:
            preferred.parent.stat()  # quick existence check
            preferred.touch()        # will raise if not writable
            preferred.unlink()
            dest = preferred
        except OSError:
            desktop = Path.home() / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            dest = desktop / filename

    if fmt == "json":
        root_name = folder.name
        subfolders, direct, _root_bytes = tree
        payload = {
            "scanned":     str(folder),
            "timestamp":   datetime.now().isoformat(),
            "total_count": total_count,
            "total_sec":   round(total_sec, 2),
            "totals":      format_duration(total_sec),
            "tree":        _tree_to_dict(root_name, total_sec, total_count, subfolders, direct),
            "files":       {str(p): round(s, 2) for p, s in
                            sorted(durations.items(), key=lambda x: x[1], reverse=True)},
        }
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    elif fmt == "csv":
        ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)
        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "filename", "folder", "seconds", "duration"])
            for path, sec in ranked:
                writer.writerow([
                    str(path),
                    path.name,
                    path.parent.name,
                    round(sec, 2),
                    format_duration(sec)["hours_fmt"],
                ])

    elif fmt == "txt":
        import io
        buf = io.StringIO()
        # Strip ANSI by temporarily redirecting — we rebuild the text cleanly
        fd = format_duration(total_sec)
        buf.write(f"AEVUM  |  Media Library Scanner\n")
        buf.write(f"Scanned : {folder}\n")
        buf.write(f"Date    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        buf.write("=" * 64 + "\n\n")

        def write_tree(name, seconds, count, subfolders, direct=None, depth=0, number=""):
            indent = "    " * depth
            label  = f"{number}.  {name}" if number else name
            fd_    = format_duration(seconds)
            if count == 0:
                buf.write(f"{indent}{label}\n")
                buf.write(f"{indent}    +--  (empty)\n")
            else:
                buf.write(f"{indent}{label}\n")
                buf.write(f"{indent}    +--  {fd_['hours_fmt']}  |  {count} videos\n")
            for path, sec in (direct or []):
                buf.write(f"{indent}    |  {format_duration(sec)['hours_fmt']}  {path.name}\n")
            if subfolders:
                buf.write("\n")
            for i, (sn, ss, sc, _fb, _dc, ssub, sd) in enumerate(subfolders, start=1):
                sub_number = f"{number}.{i}" if number else str(i)
                write_tree(sn, ss, sc, ssub, sd, depth + 1, sub_number)
            if subfolders:
                buf.write("\n")

        subfolders, direct, _root_bytes = tree
        write_tree(folder.name, total_sec, total_count, subfolders, direct)
        buf.write("=" * 64 + "\n")
        buf.write("GRAND TOTAL\n")
        buf.write("=" * 64 + "\n")
        buf.write(f"Total videos  :  {total_count}\n")
        buf.write(f"Days          :  {fd['days_fmt']}\n")
        buf.write(f"Hours         :  {fd['hours_fmt']}\n")
        buf.write(f"Minutes       :  {fd['minutes_fmt']}\n")
        buf.write("=" * 64 + "\n\n")

        buf.write("TOP 10 LONGEST FILES\n")
        buf.write("=" * 64 + "\n")
        ranked = sorted(durations.items(), key=lambda x: x[1], reverse=True)[:10]
        for i, (path, sec) in enumerate(ranked, start=1):
            buf.write(f"  {i:>2}.  {format_duration(sec)['hours_fmt']}  |  {path.name}  ({path.parent.name})\n")

        dest.write_text(buf.getvalue(), encoding="utf-8")

    return dest

# ── DUPLICATE DETECTION ───────────────────────────────────────────────

def _file_fingerprint(path, size, chunk=65536):
    """
    Fast partial hash: read first + last 64KB of the file.
    size is passed in from the caller (already known) to avoid a second stat().
    Files with different sizes are never equal, so we only hash
    candidates that share a size — making this very rarely called
    on unique files.
    """
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
    Find duplicate video files by size + partial hash.
    sizes: optional dict mapping Path -> bytes (from scan); avoids extra stat() calls.
    Returns a list of groups, where each group is a list of Paths
    that are identical. Only groups with 2+ files are returned.
    """
    sizes = sizes or {}

    # Step 1: group by size — use known sizes dict, fall back to stat() only if missing
    by_size = {}
    for path in durations:
        if path in sizes:
            sz = sizes[path]
        else:
            try:
                sz = path.stat().st_size
            except OSError:
                continue
        by_size.setdefault(sz, []).append(path)

    # Step 2: for size groups with 2+ files, hash and group
    groups = []
    for sz, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash = {}
        for path in paths:
            fp = _file_fingerprint(path, sz)  # pass known size — no extra stat()
            if fp:
                by_hash.setdefault(fp, []).append(path)
        for fp, members in by_hash.items():
            if len(members) >= 2:
                groups.append(members)

    return groups

def print_duplicates(groups, durations):
    """Print duplicate groups with wasted space info."""
    if not groups:
        print(f"  {G}No duplicates found.{RST}\n")
        return

    total_wasted_sec = 0.0
    print(f"  {C}{LINE}{RST}")
    print(f"  {R}  Duplicate Groups Found: {len(groups)}{RST}")
    print(f"  {C}{LINE}{RST}")
    print()

    for i, group in enumerate(groups, start=1):
        # wasted = duration of all copies minus one original
        sec = durations.get(group[0], 0.0)
        wasted = sec * (len(group) - 1)
        total_wasted_sec += wasted
        fmt = format_duration(sec)
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
    """Short inline warning shown at the bottom of a normal scan."""
    if not groups:
        return
    total = sum(len(g) - 1 for g in groups)
    grp_word  = "group" if len(groups) == 1 else "groups"
    file_word = "file"  if total == 1       else "files"
    print(f"  {Y}⚠  {len(groups)} duplicate {grp_word} found ({total} redundant {file_word}){RST}  "
          f"{DIM}— press 6 or type 'duplicates' for details{RST}\n")

# ── FOLDER COMPARISON ─────────────────────────────────────────────────

def run_compare(folder_a, folder_b, on_progress, sort_by, use_cache):
    """Scan both folders and return comparison data."""
    print(f"  {DIM}Scanning {Path(folder_a).name}...{RST}", end='', flush=True)
    sec_a, count_a, tree_a, dur_a, _, _ = _run_scan(folder_a, on_progress, sort_by, use_cache)
    print(f"\r  {G}Done{RST}  {DIM}→{RST}  {W}{Path(folder_a).name}{RST}  {DIM}|{RST}  {Y}{count_a} videos  {format_duration(sec_a)['hours_fmt']}{RST}".ljust(70))

    print(f"  {DIM}Scanning {Path(folder_b).name}...{RST}", end='', flush=True)
    sec_b, count_b, tree_b, dur_b, _, _ = _run_scan(folder_b, on_progress, sort_by, use_cache)
    print(f"\r  {G}Done{RST}  {DIM}→{RST}  {W}{Path(folder_b).name}{RST}  {DIM}|{RST}  {Y}{count_b} videos  {format_duration(sec_b)['hours_fmt']}{RST}".ljust(70))

    return (sec_a, count_a, dur_a), (sec_b, count_b, dur_b)

def print_comparison(folder_a, folder_b, data_a, data_b):
    """Print side-by-side comparison of two scanned folders."""
    sec_a, count_a, dur_a = data_a
    sec_b, count_b, dur_b = data_b
    name_a = Path(folder_a).name
    name_b = Path(folder_b).name

    delta_sec   = sec_b   - sec_a
    delta_count = count_b - count_a
    delta_sign  = "+" if delta_sec >= 0 else ""
    delta_csign = "+" if delta_count >= 0 else ""

    # subfolder names in each
    subs_a = {p.parent.name for p in dur_a}
    subs_b = {p.parent.name for p in dur_b}
    only_a = sorted(subs_a - subs_b)
    only_b = sorted(subs_b - subs_a)
    in_both = sorted(subs_a & subs_b)

    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Folder Comparison{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}  {name_a:<30}{RST}  {Y}{format_duration(sec_a)['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{count_a} videos{RST}")
    print(f"  {W}  {name_b:<30}{RST}  {Y}{format_duration(sec_b)['hours_fmt']}{RST}  {DIM}|{RST}  {Y}{count_b} videos{RST}")
    print()
    delta_col = G if delta_sec >= 0 else R
    print(f"  {W}  Delta{'':<25}{RST}  {delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{RST}  {DIM}|{RST}  {delta_col}{delta_csign}{delta_count} videos{RST}")
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

def print_banner(post_scan=False, current_sort="name:asc"):
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  A E V U M{RST}  {DIM}|{RST}  {W}Media Library Scanner{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print(f"  {W}Type a folder path or YouTube URL (video/playlist/channel) and press Enter.{RST}")
    print()
    if post_scan:
        print_post_scan_menu(current_sort)
    else:
        print(f"  {G}1. scan{RST}   {M}2. clear{RST}   {R}3. quit{RST}")
        print()

def print_results(folder, total_sec, total_count, tree, durations=None, sizes=None, top_n=10, show_files=False):
    fmt = format_duration(total_sec)
    sizes = sizes or {}
    subfolders, direct, root_bytes = tree
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Video Library  |  Folder Summary{RST}")
    print(f"  {C}{LINE}{RST}")
    print()
    print_tree(Path(folder).name, total_sec, total_count, subfolders, direct, show_files=show_files, fbytes=root_bytes)
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Grand Total{RST}")
    print(f"  {C}{LINE}{RST}")
    total_bytes = sum(sizes.values())
    print(f"  {W}  Total videos  {DIM}:{RST}  {W}{total_count}{RST}")
    print(f"  {W}  Total size    {DIM}:{RST}  {W}{format_size(total_bytes)}{RST}")
    print(f"  {W}  Days          {DIM}:{RST}  {W}{fmt['days_fmt']}{RST}")
    print(f"  {W}  Hours         {DIM}:{RST}  {W}{fmt['hours_fmt']}{RST}")
    print(f"  {W}  Minutes       {DIM}:{RST}  {W}{fmt['minutes_fmt']}{RST}")
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {W}  Playback Speed{RST}")
    print(f"  {C}{LINE}{RST}")
    for speed in (1.0, 1.25, 1.5, 1.75, 2.0):
        adjusted = format_duration(total_sec / speed)
        label = f"{speed:.2f}".rstrip('0').rstrip('.') + "x"
        print(f"  {W}  {label:<6}        {DIM}:{RST}  {W}{adjusted['hours_fmt']}{RST}  {DIM}({adjusted['days_fmt']}){RST}")
    print()
    if durations and top_n > 0:
        print_top_files(durations, top_n)

def _sort_label(current_sort):
    """Convert 'name:asc' -> 'name ascending' etc."""
    _dir_words = {'asc': 'ascending', 'desc': 'descending'}
    if ':' in current_sort:
        field, d = current_sort.split(':', 1)
        return f"{field} {_dir_words.get(d, d)}"
    return current_sort

def print_post_scan_menu(current_sort="name:asc"):
    print(f"  {W}What do you want to do?{RST}")
    print(f"  {G}1. scan{RST}   {B}2. sort{RST}   {M}3. export{RST}   {Y}4. clear{RST}   {R}5. quit{RST}   {C}6. duplicates{RST}")
    print()


def _fuzzy_suggest(word, candidates):
    """Return the closest candidate if edit distance <= 2, else None."""
    def _dist(a, b):
        if a == b: return 0
        la, lb = len(a), len(b)
        if abs(la - lb) > 3: return 99
        prev = list(range(lb + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(prev[j] + (0 if ca == cb else 1),
                                curr[j] + 1, prev[j + 1] + 1))
            prev = curr
        return prev[lb]
    scored = [(c, _dist(word, c)) for c in candidates]
    best_c, best_d = min(scored, key=lambda x: x[1])
    return best_c if best_d <= 2 else None

# ── CONFIG ────────────────────────────────────────────────────────────

CONFIG_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "config.json"

CONFIG_DEFAULTS = {
    "sort":         "name:asc",
    "top":          10,
    "no_color":     False,
    "cache_enabled": True,
    "export_dir":   "",
}

def load_config():
    """Load config from disk, merging with defaults for missing keys."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**CONFIG_DEFAULTS, **data}
    except Exception:
        return dict(CONFIG_DEFAULTS)

def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  {R}Config write failed:{RST} {e}", file=sys.stderr)

def _config_key_valid(key):
    return key in CONFIG_DEFAULTS

# ── MAIN ──────────────────────────────────────────────────────────────

def _make_progress_bar():
    """Return a progress callback that renders a progress bar to stdout."""
    def on_progress(done, total):
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "█" * filled + "░" * (24 - filled)
        print(f"\r  {C}Scanning...{RST}  {bar}  {Y}{done}/{total}{RST}  {DIM}({pct}%){RST}",
              end='', flush=True)
    return on_progress


def _parse_args():
    argv = sys.argv[1:]

    # ── top-level --version / --help before subcommand dispatch ──────
    # argparse subparsers don't propagate -V cleanly, so handle it here.
    if not argv or argv[0] in ('-h', '--help'):
        _print_global_help()
        sys.exit(0)

    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(0)

    # ── subcommand dispatch ───────────────────────────────────────────
    subcommand = argv[0]

    SUBCOMMANDS = ('scan', 'compare', 'dupes', 'export', 'cache', 'config', 'doctor', 'version', 'shell')

    # Bare path / URL shorthand → treat as implicit 'scan'
    if subcommand not in SUBCOMMANDS:
        argv = ['scan'] + argv
        subcommand = 'scan'

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub, argv):
    """Parse argv for the given subcommand. Returns a Namespace with .command set."""
    import types

    def _make(ns_dict):
        ns = types.SimpleNamespace(**ns_dict, command=sub)
        return ns

    # ── scan ─────────────────────────────────────────────────────────
    if sub == 'scan':
        p = argparse.ArgumentParser(
            prog="aevum scan",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a local folder or YouTube URL and report total duration.",
            epilog=(
                "Examples:\n"
                "  aevum scan D:\\Movies\n"
                "  aevum scan D:\\Movies --sort duration --top 20\n"
                "  aevum scan D:\\Movies --files --out report.csv\n"
                "  aevum scan https://youtube.com/@mkbhd\n"
                "  aevum scan https://youtube.com/playlist?list=PLxxx --top 5\n"
            ),
            add_help=True,
        )
        p.add_argument("target", nargs="?", default=None,
                       metavar="PATH|URL",
                       help="local folder path or YouTube URL (video/playlist/channel)")
        p.add_argument("-s", "--sort", default=None,
                       metavar="FIELD[:DIR]",
                       help="sort tree: name|duration|count  with optional :asc|:desc  (default: name:asc)")
        p.add_argument("-t", "--top", type=int, default=None,
                       metavar="N",
                       help="show top N longest files (default: 10, 0 = hide)")
        p.add_argument("-f", "--files", action="store_true",
                       help="show individual filenames under each folder in the tree")
        p.add_argument("-o", "--out", default=None,
                       metavar="FILE",
                       help="write results to FILE (format inferred from extension: .txt .csv .json)")
        p.add_argument("--format", dest="fmt", choices=["txt", "csv", "json"], default=None,
                       help="explicit export format when --out is used")
        p.add_argument("--depth", type=int, default=None,
                       metavar="N",
                       help="limit tree display to N levels deep")
        p.add_argument("--no-cache", action="store_true",
                       help="bypass cache; re-probe every file")
        p.add_argument("--no-color", action="store_true",
                       help="disable ANSI color output")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── compare ──────────────────────────────────────────────────────
    if sub == 'compare':
        p = argparse.ArgumentParser(
            prog="aevum compare",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Compare the duration totals of two local video libraries.",
            epilog=(
                "Examples:\n"
                "  aevum compare D:\\Movies E:\\Movies-Backup\n"
                "  aevum compare D:\\Movies E:\\Backup --sort duration\n"
            ),
        )
        p.add_argument("folder_a", help="first folder")
        p.add_argument("folder_b", help="second folder")
        p.add_argument("-s", "--sort", default=None,
                       metavar="FIELD[:DIR]",
                       help="sort: name|duration|count  (default: name:asc)")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── dupes ─────────────────────────────────────────────────────────
    if sub == 'dupes':
        p = argparse.ArgumentParser(
            prog="aevum dupes",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Find duplicate video files (by size + partial hash) in a folder.",
            epilog=(
                "Examples:\n"
                "  aevum dupes D:\\Movies\n"
                "  aevum dupes D:\\Movies -o dupes.txt\n"
            ),
        )
        p.add_argument("folder", help="folder to scan for duplicates")
        p.add_argument("-o", "--out", default=None,
                       metavar="FILE",
                       help="write duplicate report to FILE")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── export ────────────────────────────────────────────────────────
    if sub == 'export':
        p = argparse.ArgumentParser(
            prog="aevum export",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder or URL and write results directly to a file.",
            epilog=(
                "Examples:\n"
                "  aevum export D:\\Movies csv\n"
                "  aevum export D:\\Movies json -o D:\\Reports\\library.json\n"
                "  aevum export https://youtube.com/@mkbhd txt\n"
            ),
        )
        p.add_argument("target", metavar="PATH|URL",
                       help="local folder path or YouTube URL")
        p.add_argument("format", choices=["txt", "csv", "json"],
                       help="output format: txt | csv | json")
        p.add_argument("-o", "--out", default=None,
                       metavar="FILE",
                       help="output path (default: auto-named next to the folder)")
        p.add_argument("-s", "--sort", default=None,
                       metavar="FIELD[:DIR]",
                       help="sort: name|duration|count  (default: name:asc)")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── cache ─────────────────────────────────────────────────────────
    if sub == 'cache':
        p = argparse.ArgumentParser(
            prog="aevum cache",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage the duration cache.",
            epilog=(
                "Subcommands:\n"
                "  list               List all cache files\n"
                "  clear              Delete all cache files\n"
                "  clear <path>       Delete cache for a specific folder\n"
                "  path               Print the cache directory path\n"
            ),
        )
        p.add_argument("action", nargs="?", default="list",
                       choices=["list", "clear", "path"],
                       help="action to perform (default: list)")
        p.add_argument("folder", nargs="?", default=None,
                       help="folder path (used with 'clear' to target a specific folder)")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── config ────────────────────────────────────────────────────────
    if sub == 'config':
        p = argparse.ArgumentParser(
            prog="aevum config",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Read and write persistent configuration.",
            epilog=(
                "Keys:  sort  top  no_color  cache_enabled  export_dir  yt_api_key\n\n"
                "Examples:\n"
                "  aevum config list\n"
                "  aevum config get sort\n"
                "  aevum config set sort duration:desc\n"
                "  aevum config set top 20\n"
                "  aevum config set yt_api_key AIzaSy...\n"
                "  aevum config reset\n"
            ),
        )
        p.add_argument("action", choices=["get", "set", "list", "reset"],
                       help="action to perform")
        p.add_argument("key",   nargs="?", default=None,
                       help="config key (required for get/set)")
        p.add_argument("value", nargs="?", default=None,
                       help="new value (required for set)")
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── doctor ────────────────────────────────────────────────────────
    if sub == 'doctor':
        p = argparse.ArgumentParser(
            prog="aevum doctor",
            description="Check environment: ffprobe, API key, cache, Python version.",
        )
        p.add_argument("--no-color", action="store_true")
        args = p.parse_args(argv)
        args.command = sub
        return args

    # ── version ───────────────────────────────────────────────────────
    if sub == 'version':
        print(f"aevum {__version__}")
        sys.exit(0)

    # ── shell (explicit REPL entry) ───────────────────────────────────
    if sub == 'shell':
        ns = types.SimpleNamespace(command='shell', no_color=False, sort=None, top=None)
        for a in argv:
            if a == '--no-color':
                ns.no_color = True
        return ns

    _print_global_help()
    sys.exit(1)


def _print_global_help():
    print(f"""
  {C}aevum {__version__}{RST}  {DIM}—{RST}  {W}Media Library Scanner{RST}

  {W}USAGE{RST}
    aevum [command] [options]
    aevum                           Open interactive shell
    aevum <path|url>                Quick scan (shorthand for 'aevum scan')

  {W}COMMANDS{RST}
    {G}scan{RST}      <path|url>            Scan a folder or YouTube URL
    {G}compare{RST}   <path> <path>         Compare two libraries side-by-side
    {G}dupes{RST}     <path>                Find duplicate-duration files
    {G}export{RST}    <path|url> <format>   Scan and write results to a file
    {G}cache{RST}                           Manage the duration cache
    {G}config{RST}                          Read/write configuration
    {G}doctor{RST}                          Check environment (ffprobe, API key, cache)
    {G}version{RST}                         Print version and exit

  {W}GLOBAL OPTIONS{RST}
    --no-color                      Disable ANSI color output
    -h, --help                      Show this help
    -V, --version                   Show version

  {W}EXAMPLES{RST}
    aevum scan D:\\Movies
    aevum scan D:\\Movies --sort duration --top 20
    aevum scan https://youtube.com/@mkbhd
    aevum export D:\\Movies json -o library.json
    aevum dupes D:\\Movies
    aevum compare D:\\Movies E:\\Backup
    aevum config set sort duration:desc
    aevum doctor

  {DIM}Run 'aevum <command> --help' for command-specific options.{RST}
""")


def _disable_color():
    """Replace all colour constants with empty strings for plain output."""
    global R, G, Y, B, M, C, W, DIM, RST
    R = G = Y = B = M = C = W = DIM = RST = ""


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
    if use_cache and durations:
        save_cache(folder, durations)
    return total_sec, total_count, tree, durations, sizes, hits


def _require_ffprobe(context=""):
    """Print a clear error and exit 2 if ffprobe is missing."""
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        print(f"\n  {R}[ERROR]{RST} ffprobe not found on PATH{ctx}.")
        print(f"  {DIM}ffprobe is required for local folder scanning.{RST}")
        print(f"  Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}")
        print(f"  Then re-run:    {W}aevum doctor{RST}\n")
        sys.exit(2)


def _resolve_sort(args, cfg):
    """Return the effective sort string, preferring CLI flag > config > default."""
    raw = getattr(args, 'sort', None) or cfg.get('sort') or 'name:asc'
    if ':' not in raw:
        defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
        raw = raw + ':' + defaults.get(raw, 'asc')
    return raw


def _resolve_top(args, cfg):
    v = getattr(args, 'top', None)
    if v is not None:
        return v
    return cfg.get('top', 10)


def _resolve_out_format(out_path, explicit_fmt):
    """Infer format from file extension if not explicitly given."""
    if explicit_fmt:
        return explicit_fmt
    if out_path:
        ext = Path(out_path).suffix.lower().lstrip('.')
        if ext in ('txt', 'csv', 'json'):
            return ext
    return None


def main():
    args = _parse_args()
    cfg  = load_config()

    # Apply no_color from CLI flag or config
    if getattr(args, 'no_color', False) or cfg.get('no_color'):
        _disable_color()

    cmd = args.command

    # ── VERSION ──────────────────────────────────────────────────────
    if cmd == 'version':
        print(f"aevum {__version__}")
        sys.exit(0)

    # ── DOCTOR ───────────────────────────────────────────────────────
    if cmd == 'doctor':
        _cmd_doctor(cfg)
        sys.exit(0)

    # ── CONFIG ───────────────────────────────────────────────────────
    if cmd == 'config':
        _cmd_config(args, cfg)
        sys.exit(0)

    # ── CACHE ────────────────────────────────────────────────────────
    if cmd == 'cache':
        _cmd_cache(args)
        sys.exit(0)

    # ── COMPARE ──────────────────────────────────────────────────────
    if cmd == 'compare':
        folder_a = Path(args.folder_a.strip().strip("'\""))
        folder_b = Path(args.folder_b.strip().strip("'\""))
        for f in (folder_a, folder_b):
            if not f.exists() or not f.is_dir():
                print(f"\n  {R}[ERROR]{RST} Not a valid folder: {f}\n", file=sys.stderr)
                sys.exit(1)
        _require_ffprobe("compare")
        sort = _resolve_sort(args, cfg)
        on_prog = _make_progress_bar()
        data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort, not args.no_cache)
        print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(0)

    # ── DUPES ─────────────────────────────────────────────────────────
    if cmd == 'dupes':
        folder = Path(args.folder.strip().strip("'\""))
        if not folder.exists() or not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(1)
        _require_ffprobe("dupes")
        on_prog = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", use_cache)
        probed = len(durations) - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{len(durations)}{RST} videos found.{cache_info}".ljust(60))
        print(f"  {DIM}Checking for duplicates...{RST}", flush=True)
        groups = find_duplicates(durations, sizes)
        print_duplicates(groups, durations)
        if args.out:
            # Export the dupe report as plain text
            try:
                import io
                buf = io.StringIO()
                if not groups:
                    buf.write("No duplicates found.\n")
                else:
                    for i, group in enumerate(groups, 1):
                        sec = durations.get(group[0], 0.0)
                        buf.write(f"Group {i}  |  {format_duration(sec)['hours_fmt']}  |  {len(group)} copies\n")
                        for p in group:
                            buf.write(f"  -> {p}\n")
                        buf.write("\n")
                Path(args.out).write_text(buf.getvalue(), encoding="utf-8")
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{args.out}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(4)
        sys.exit(0)

    # ── EXPORT (dedicated command) ────────────────────────────────────
    if cmd == 'export':
        raw = args.target.strip().strip("'\"")
        sort = _resolve_sort(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path = args.out or None
        fmt = args.format

        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n")
                sys.exit(0)
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
            if not fmt:
                print(f"  {R}[ERROR]{RST} --format required for URL export (txt|csv|json).", file=sys.stderr)
                sys.exit(1)
            # For URLs we build a synthetic folder-less export
            durations_titled = {type('P', (), {'name': e['title'], 'parent': type('Q', (), {'name': label})()})(): e['duration'] for e in entries}
            # Simple txt fallback for URL
            try:
                import io
                buf = io.StringIO()
                buf.write(f"AEVUM  |  {label}\n{'=' * 64}\n")
                buf.write(f"Total videos : {total_count}\n")
                buf.write(f"Duration     : {format_duration(total_sec)['hours_fmt']}\n\n")
                for e in sorted(entries, key=lambda x: x['duration'], reverse=True):
                    buf.write(f"  {format_duration(e['duration'])['hours_fmt']}  |  {e['title']}\n")
                dest = Path(out_path) if out_path else Path(f"aevum_url_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}")
                dest.write_text(buf.getvalue(), encoding="utf-8")
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(4)
            sys.exit(0)

        folder = Path(raw)
        if not folder.exists() or not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(1)
        _require_ffprobe("export")
        if not fmt:
            fmt = _resolve_out_format(out_path, None)
        if not fmt:
            print(f"  {R}[ERROR]{RST} Specify a format: aevum export <path> txt|csv|json\n", file=sys.stderr)
            sys.exit(1)
        on_prog = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(folder, on_prog, sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            sys.exit(0)
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} videos found.".ljust(60))
        try:
            dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
            print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
        except Exception as e:
            print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
            sys.exit(4)
        sys.exit(0)

    # ── SCAN (headless) ───────────────────────────────────────────────
    if cmd == 'scan' and getattr(args, 'target', None) is not None:
        raw = args.target.strip().strip("'\"")
        sort = _resolve_sort(args, cfg)
        top  = _resolve_top(args, cfg)
        use_cache = not args.no_cache and cfg.get('cache_enabled', True)
        out_path = getattr(args, 'out', None)
        fmt = _resolve_out_format(out_path, getattr(args, 'fmt', None))

        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n")
                sys.exit(0)
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.".ljust(60))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=top)
            sys.exit(0)

        folder = Path(raw)
        if not folder.exists():
            print(f"\n  {R}[ERROR]{RST} Path not found: {folder}", file=sys.stderr)
            sug = _fuzzy_suggest(folder.name, [p.name for p in folder.parent.iterdir() if p.is_dir()] if folder.parent.exists() else [])
            if sug:
                print(f"  {DIM}Did you mean:{RST}  {W}{folder.parent / sug}{RST}", file=sys.stderr)
            print()
            sys.exit(1)
        if not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} That is a file, not a folder: {folder}\n", file=sys.stderr)
            sys.exit(1)
        _require_ffprobe("scan")

        on_progress = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(folder, on_progress, sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            sys.exit(0)

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                      show_files=getattr(args, 'files', False))

        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups)

        if fmt and out_path:
            try:
                dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(4)

        sys.exit(0)

    # ── INTERACTIVE / SHELL MODE ──────────────────────────────────────
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {Y}ffprobe not found on PATH.{RST}  {DIM}Local folder scanning won't work.{RST}")
        print(f"  Download FFmpeg from {C}https://ffmpeg.org/download.html{RST}")
        print()

    on_progress  = _make_progress_bar()
    last_scan    = {}
    current_sort = cfg.get('sort', 'name:asc')
    default_top  = cfg.get('top', 10)
    use_cache    = cfg.get('cache_enabled', True)

    while True:
        try:
            raw = input(f"  {C}aevum{RST}> ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if not raw:
            continue

        raw = raw.strip().strip("'\"")

        if not raw:
            continue

        _init_map = {'1': 'scan', '2': 'clear', '3': 'quit'}
        if raw in _init_map:
            raw = _init_map[raw]

        if raw.lower() in ('exit', 'quit', 'q'):
            print(f"\n  {G}Goodbye!{RST}\n")
            sys.exit(0)

        if raw.lower() in ('clear', 'c'):
            clear()
            print_banner()
            continue

        # config set yt_api_key  (replaces old reset-key / api-key)
        if raw.lower() in ('reset-key', 'apikey', 'api-key') or raw.lower().startswith('config set yt_api_key'):
            prompt_api_key()
            continue

        if raw.lower().startswith('config '):
            parts = raw.split()
            _repl_config(parts[1:], cfg)
            continue

        if raw.lower() == 'scan':
            print(f"\n  {DIM}Enter a folder path or YouTube URL to scan.{RST}\n")
            continue

        # ── URL ──────────────────────────────────────────────────────
        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n")
                continue

            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.".ljust(60))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=default_top)

            last_scan = {
                "folder":      raw,
                "total_sec":   total_sec,
                "total_count": total_count,
                "tree":        None,
                "durations":   {e['title']: e['duration'] for e in entries},
                "sizes":       {},
                "dupe_groups": [],
                "is_url":      True,
                "entries":     entries,
                "label":       label,
            }
            print_post_scan_menu(current_sort)
            continue

        # ── Local folder ──────────────────────────────────────────────
        folder = Path(raw)

        if not folder.exists():
            print(f"\n  {R}[ERROR]{RST} Path not found: {raw}\n")
            continue
        if not folder.is_dir():
            print(f"\n  {R}[ERROR]{RST} That is a file, not a folder.\n")
            continue
        if not check_ffprobe():
            print(f"\n  {R}[ERROR]{RST} ffprobe not found. Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}\n")
            continue

        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, current_sort, use_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, default_top,
                      show_files=False)

        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups)

        last_scan = {
            "folder":      folder,
            "total_sec":   total_sec,
            "total_count": total_count,
            "tree":        tree,
            "durations":   durations,
            "sizes":       sizes,
            "dupe_groups": groups,
            "is_url":      False,
        }

        print_post_scan_menu(current_sort)
        while True:
            try:
                choice = input(f"  {C}aevum{RST}> ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n  {G}Goodbye!{RST}\n")
                sys.exit(0)

            _menu_map = {'1': 'scan', '2': 'sort', '3': 'export', '4': 'clear', '5': 'quit', '6': 'duplicates'}
            if choice in _menu_map:
                choice = _menu_map[choice]

            _all_cmds = ['scan', 'clear', 'export', 'sort', 'quit', 'exit', 'q', 'duplicates', 'dupes']
            first_word = choice.split()[0] if choice else ''

            if choice in ('quit', 'exit', 'q'):
                print(f"\n  {G}Goodbye!{RST}\n")
                sys.exit(0)

            elif choice == 'clear':
                clear()
                print_banner()
                break

            elif choice == 'scan':
                break

            elif choice.split()[0] == 'sort' or choice == 'sort':
                if last_scan.get("is_url"):
                    print(f"  {Y}Sort is not available for URL scans.{RST}\n")
                    print_post_scan_menu(current_sort)
                    continue
                parts = choice.split()
                field = parts[1] if len(parts) >= 2 else None
                direc = parts[2] if len(parts) >= 3 else None
                _field_opts = ('name', 'duration', 'count')
                _field_map  = {'1': 'name', '2': 'duration', '3': 'count'}

                while field not in _field_opts:
                    sug  = _fuzzy_suggest(field, list(_field_opts) + list(_field_map.keys())) if field else None
                    hint = f"  {DIM}Did you mean {W}{_field_map.get(sug, sug)}{RST}{DIM}?{RST}" if sug else ""
                    if field is not None:
                        print(f"  {R}Unknown.{RST}{hint}")
                    print(f"  {DIM}Sort by?{RST}  {G}1. name{RST}   {B}2. duration{RST}   {M}3. count{RST}   {DIM}0. back{RST}")
                    try:
                        field = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        field = 'back'
                    if field in _field_map:
                        field = _field_map[field]
                    if field in ('back', '0', ''):
                        print_post_scan_menu(current_sort)
                        field = None
                        break
                if field is None:
                    continue

                _dir_aliases = {
                    'asc': 'asc', 'ascending': 'asc', 'a': 'asc', '1': 'asc',
                    'desc': 'desc', 'descending': 'desc', 'd': 'desc', '2': 'desc',
                }
                if field == 'name':
                    dir_hint_str = f"{G}1. ascending{RST} (a→z)   {B}2. descending{RST} (z→a)"
                    dir_def = 'asc'
                else:
                    dir_hint_str = f"{G}1. descending{RST} (high→low)   {B}2. ascending{RST} (low→high)"
                    dir_def = 'desc'

                while True:
                    if direc is None:
                        print(f"  {DIM}Direction?{RST}  {dir_hint_str}   {DIM}0. back{RST}  {DIM}[Enter = default]{RST}")
                        try:
                            direc = input(f"  {C}aevum{RST}> ").strip().lower()
                        except (KeyboardInterrupt, EOFError):
                            print()
                            direc = 'back'
                    if direc in ('back', '0'):
                        print_post_scan_menu(current_sort)
                        direc = None
                        break
                    if direc == '':
                        direc = dir_def
                    resolved = _dir_aliases.get(direc)
                    if resolved:
                        direc = resolved
                        break
                    sug  = _fuzzy_suggest(direc, list(_dir_aliases.keys()))
                    hint = f"  {DIM}Did you mean {W}{sug}{RST}{DIM}?{RST}" if sug else ""
                    print(f"  {R}Unknown direction.{RST}{hint}")
                    direc = None

                if direc is None:
                    continue

                current_sort = f"{field}:{direc}"
                _, _, new_tree, new_durations, new_sizes, _ = _run_scan(
                    last_scan["folder"], None, current_sort, True)
                last_scan["tree"]      = new_tree
                last_scan["durations"] = new_durations
                last_scan["sizes"]     = new_sizes
                print_results(last_scan["folder"], last_scan["total_sec"],
                              last_scan["total_count"], new_tree,
                              new_durations, last_scan["sizes"], default_top, show_files=False)
                print_post_scan_menu(current_sort)

            elif choice.split()[0] == 'export' or choice == 'export':
                if last_scan.get("is_url"):
                    print(f"  {Y}Export is not available for URL scans yet.{RST}\n")
                    print_post_scan_menu(current_sort)
                    continue
                parts   = choice.split()
                fmt     = parts[1] if len(parts) >= 2 else None
                _fmt_opts = ('txt', 'csv', 'json')
                _fmt_map  = {'1': 'txt', '2': 'csv', '3': 'json'}

                while fmt not in _fmt_opts:
                    sug  = _fuzzy_suggest(fmt, list(_fmt_opts) + list(_fmt_map.keys())) if fmt else None
                    hint = f"  {DIM}Did you mean {W}{_fmt_map.get(sug, sug)}{RST}{DIM}?{RST}" if sug else ""
                    if fmt is not None:
                        print(f"  {R}Unknown format.{RST}{hint}")
                    print(f"  {DIM}Export as?{RST}  {G}1. txt{RST}   {B}2. csv{RST}   {M}3. json{RST}   {DIM}0. back{RST}")
                    try:
                        fmt = input(f"  {C}aevum{RST}> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        fmt = 'back'
                    if fmt in _fmt_map:
                        fmt = _fmt_map[fmt]
                    if fmt in ('back', '0', ''):
                        print_post_scan_menu(current_sort)
                        fmt = None
                        break

                if fmt is None:
                    continue
                out_dir = cfg.get('export_dir') or None
                out_path_default = (Path(out_dir) / f"aevum_{Path(last_scan['folder']).name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}") if out_dir else None
                try:
                    dest = export_results(
                        last_scan["folder"], last_scan["total_sec"],
                        last_scan["total_count"], last_scan["tree"],
                        last_scan["durations"], fmt, out_path_default,
                    )
                    print(f"\n  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
                except Exception as e:
                    print(f"\n  {R}Export failed:{RST} {e}\n")

            elif choice in ('duplicates', 'dupes'):
                if last_scan.get("is_url"):
                    print(f"  {Y}Duplicate detection is not available for URL scans.{RST}\n")
                    print_post_scan_menu(current_sort)
                    continue
                if not last_scan:
                    print(f"  {R}No scan yet.{RST} Run a scan first.\n")
                    print_post_scan_menu(current_sort)
                    continue
                print_duplicates(last_scan["dupe_groups"], last_scan["durations"])
                print_post_scan_menu(current_sort)

            else:
                sug = _fuzzy_suggest(first_word, _all_cmds) if first_word else None
                if sug:
                    print(f"  {R}Unknown command.{RST}  {DIM}Did you mean{RST}  {W}{sug}{RST}{DIM}?{RST}")
                else:
                    print(f"  {R}Invalid command.{RST} Type  {G}1. scan{RST}   {B}2. sort{RST}   {M}3. export{RST}   {Y}4. clear{RST}   {R}5. quit{RST}   {C}6. duplicates{RST}")
                print_post_scan_menu(current_sort)


# ── COMMAND IMPLEMENTATIONS ───────────────────────────────────────────

def _cmd_doctor(cfg):
    """aevum doctor — environment check."""
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Aevum Doctor{RST}  {DIM}|{RST}  {W}Environment Check{RST}")
    print(f"  {C}{LINE}{RST}")
    print()

    # Python
    pv = sys.version.split()[0]
    print(f"  {G}[OK]{RST}   Python {pv}")

    # ffprobe
    try:
        r = subprocess.run(['ffprobe', '-version'], capture_output=True, text=True)
        fv = r.stdout.splitlines()[0] if r.stdout else "unknown"
        print(f"  {G}[OK]{RST}   {fv}")
    except FileNotFoundError:
        print(f"  {R}[FAIL]{RST}  ffprobe not found on PATH")
        print(f"         Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}")

    # YouTube API key
    api_key = load_api_key()
    if api_key:
        masked = api_key[:6] + '...' + api_key[-4:] if len(api_key) > 10 else '***'
        print(f"  {G}[OK]{RST}   YouTube API key set  {DIM}({masked}){RST}")
    else:
        print(f"  {Y}[WARN]{RST}  YouTube API key not set")
        print(f"         Set it with: {W}aevum config set yt_api_key <key>{RST}")

    # Cache
    try:
        files = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        total_bytes = sum(f.stat().st_size for f in files)
        print(f"  {G}[OK]{RST}   Cache: {len(files)} entries, {format_size(total_bytes)} at {CACHE_DIR}")
    except Exception:
        print(f"  {Y}[WARN]{RST}  Could not read cache directory: {CACHE_DIR}")

    # Config file
    if CONFIG_FILE.exists():
        print(f"  {G}[OK]{RST}   Config: {CONFIG_FILE}")
    else:
        print(f"  {DIM}[INFO]{RST}  No config file (using defaults). {DIM}{CONFIG_FILE}{RST}")

    print()


def _cmd_cache(args):
    """aevum cache [list|clear|path] [folder]"""
    action = args.action or 'list'

    if action == 'path':
        print(f"  {W}{CACHE_DIR}{RST}")
        return

    if action == 'list':
        if not CACHE_DIR.exists() or not list(CACHE_DIR.glob("*.json")):
            print(f"  {DIM}Cache is empty.{RST}  {W}{CACHE_DIR}{RST}")
            return
        files = sorted(CACHE_DIR.glob("*.json"))
        print()
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Cache  {DIM}|{RST}  {CACHE_DIR}{RST}")
        print(f"  {C}{LINE}{RST}")
        print()
        total = 0
        for f in files:
            sz = f.stat().st_size
            total += sz
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                folder_path = data[0]['path'].rsplit('\\', 1)[0] if data else '?'
                count = len(data)
            except Exception:
                folder_path = '?'
                count = 0
            print(f"  {DIM}{f.name[:16]}{RST}  {W}{folder_path}{RST}  {DIM}({count} files, {format_size(sz)}){RST}")
        print()
        print(f"  {DIM}Total: {len(files)} cache files, {format_size(total)}{RST}")
        print()
        return

    if action == 'clear':
        target_folder = getattr(args, 'folder', None)
        if target_folder:
            key = _cache_key(target_folder)
            if key.exists():
                key.unlink()
                print(f"  {G}[OK]{RST}  Cleared cache for {target_folder}")
            else:
                print(f"  {DIM}[SKIP]{RST}  No cache found for {target_folder}")
        else:
            if not CACHE_DIR.exists():
                print(f"  {DIM}Cache is already empty.{RST}")
                return
            files = list(CACHE_DIR.glob("*.json"))
            for f in files:
                f.unlink()
            print(f"  {G}[OK]{RST}  Cleared {len(files)} cache files from {CACHE_DIR}")


def _cmd_config(args, cfg):
    """aevum config get|set|list|reset [key] [value]"""
    action = args.action

    # yt_api_key is stored separately — bridge it into config commands
    YT_KEY = 'yt_api_key'

    if action == 'list':
        print()
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Configuration{RST}  {DIM}|{RST}  {CONFIG_FILE}")
        print(f"  {C}{LINE}{RST}")
        print()
        for k, v in cfg.items():
            print(f"  {G}{k:<18}{RST}  {W}{v}{RST}")
        api_key = load_api_key()
        masked = (api_key[:6] + '...' + api_key[-4:]) if api_key and len(api_key) > 10 else (api_key or '(not set)')
        print(f"  {G}{YT_KEY:<18}{RST}  {W}{masked}{RST}")
        print()
        return

    if action == 'reset':
        save_config(dict(CONFIG_DEFAULTS))
        print(f"  {G}[OK]{RST}  Configuration reset to defaults.")
        return

    key = args.key
    if not key:
        print(f"  {R}[ERROR]{RST} Key required. Run 'aevum config list' to see all keys.", file=sys.stderr)
        sys.exit(1)

    if action == 'get':
        if key == YT_KEY:
            api_key = load_api_key()
            print(api_key or '(not set)')
        elif _config_key_valid(key):
            print(cfg.get(key, CONFIG_DEFAULTS.get(key)))
        else:
            print(f"  {R}[ERROR]{RST} Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        return

    if action == 'set':
        value = args.value
        if key == YT_KEY:
            if not value:
                # Interactive prompt
                value = prompt_api_key()
                return
            save_api_key(value)
            print(f"  {G}[OK]{RST}  yt_api_key saved.")
            return
        if not _config_key_valid(key):
            print(f"  {R}[ERROR]{RST} Unknown key: {key}. Run 'aevum config list' to see all keys.", file=sys.stderr)
            sys.exit(1)
        if value is None:
            print(f"  {R}[ERROR]{RST} Value required: aevum config set {key} <value>", file=sys.stderr)
            sys.exit(1)
        # Coerce types
        default = CONFIG_DEFAULTS[key]
        try:
            if isinstance(default, bool):
                coerced = value.lower() in ('1', 'true', 'yes')
            elif isinstance(default, int):
                coerced = int(value)
            else:
                coerced = value
        except (ValueError, AttributeError):
            print(f"  {R}[ERROR]{RST} Invalid value for {key}: {value}", file=sys.stderr)
            sys.exit(1)
        cfg[key] = coerced
        save_config(cfg)
        print(f"  {G}[OK]{RST}  {key} = {coerced}")


def _repl_config(parts, cfg):
    """Handle 'config ...' typed inside the REPL."""
    import types
    if not parts:
        print(f"  {DIM}Usage: config get <key> | config set <key> <value> | config list | config reset{RST}\n")
        return
    ns = types.SimpleNamespace(
        action=parts[0] if parts else 'list',
        key=parts[1] if len(parts) > 1 else None,
        value=parts[2] if len(parts) > 2 else None,
        no_color=False,
    )
    _cmd_config(ns, cfg)


if __name__ == "__main__":
    main()
