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
        buf.write(f"AEVUM  |  Video Library Duration Scanner\n")
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
    print(f"  {C}  A E V U M{RST}  {DIM}|{RST}  {W}Video Library Duration Scanner{RST}")
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
    # Detect compare / dupes subcommands manually before argparse
    # so the main 'folder' positional doesn't conflict with subcommand names.
    argv = sys.argv[1:]
    command = None
    if argv and argv[0] in ('compare', 'dupes'):
        command = argv[0]
        argv = argv[1:]

    p = argparse.ArgumentParser(
        prog="aevum",
        description="Video library duration scanner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  aevum                             interactive mode\n"
            "  aevum D:\\Movies                   scan and print, then exit\n"
            "  aevum D:\\Movies --export csv      save results as CSV\n"
            "  aevum D:\\Movies --sort duration   sort folders by duration\n"
            "  aevum D:\\Movies --top 20          show 20 longest files\n"
            "  aevum D:\\Movies --no-color        plain text output\n"
            "  aevum compare D:\\Movies E:\\Backup compare two folders\n"
            "  aevum dupes D:\\Movies             find duplicate videos\n"
        ),
    )

    if command == "compare":
        p.add_argument("folder_a", help="first folder")
        p.add_argument("folder_b", help="second folder")
        p.add_argument("--sort",     "-s", choices=["name", "duration", "count"], default="name")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
    elif command == "dupes":
        p.add_argument("folder", help="folder to scan for duplicates")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--no-color", action="store_true")
    else:
        p.add_argument("folder",         nargs="?",  default=None,
                       help="folder to scan (omit to enter interactive mode)")
        p.add_argument("--export", "-e", choices=["txt", "csv", "json"], default=None,
                       metavar="FORMAT",
                       help="export results to a file: txt | csv | json")
        p.add_argument("--out",    "-o", default=None,
                       help="output path for --export (default: auto-named next to folder)")
        p.add_argument("--top",    "-t", type=int, default=10,
                       metavar="N",
                       help="show top N longest files (default: 10, set 0 to hide)")
        p.add_argument("--sort",   "-s", default="name:asc",
                       metavar="FIELD[:DIR]",
                       help="sort: name[:asc|desc]  duration[:asc|desc]  count[:asc|desc]  (default: name:asc)")
        p.add_argument("--files",  "-f", action="store_true",
                       help="show individual files under each folder in the tree")
        p.add_argument("--no-cache",     action="store_true",
                       help="bypass the duration cache and re-probe every file")
        p.add_argument("--no-color",     action="store_true",
                       help="strip ANSI colours from terminal output")
        p.add_argument("--version", "-v", action="version", version=f"aevum {__version__}")

    args = p.parse_args(argv)
    args.command = command
    return args


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


def main():
    args = _parse_args()

    if getattr(args, 'no_color', False):
        _disable_color()

    # ── COMPARE SUBCOMMAND ───────────────────────────────────────────
    if args.command == "compare":
        folder_a = Path(args.folder_a.strip().strip("'\""))
        folder_b = Path(args.folder_b.strip().strip("'\""))
        for f in (folder_a, folder_b):
            if not f.exists() or not f.is_dir():
                print(f"Error: not a valid folder: {f}", file=sys.stderr)
                sys.exit(1)
        if not check_ffprobe():
            print("Error: ffprobe not found on PATH.", file=sys.stderr)
            sys.exit(1)
        on_prog = _make_progress_bar()
        data_a, data_b = run_compare(folder_a, folder_b, on_prog, args.sort, not args.no_cache)
        print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(0)

    # ── DUPES SUBCOMMAND ─────────────────────────────────────────────
    if args.command == "dupes":
        folder = Path(args.folder.strip().strip("'\""))
        if not folder.exists() or not folder.is_dir():
            print(f"Error: not a valid folder: {folder}", file=sys.stderr)
            sys.exit(1)
        if not check_ffprobe():
            print("Error: ffprobe not found on PATH.", file=sys.stderr)
            sys.exit(1)
        on_prog = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        _, _, _, durations, _, hits = _run_scan(folder, on_prog, "name", not args.no_cache)
        probed = len(durations) - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{len(durations)}{RST} videos found.{cache_info}".ljust(60))
        print(f"  {DIM}Checking for duplicates...{RST}", flush=True)
        groups = find_duplicates(durations)
        print_duplicates(groups, durations)
        sys.exit(0)

    # ── HEADLESS MODE ────────────────────────────────────────────────
    if args.folder is not None:
        raw_arg = args.folder.strip().strip("'\"")

        # ── URL headless ─────────────────────────────────────────────
        if _is_url(raw_arg):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw_arg, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n")
                sys.exit(0)
            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.".ljust(60))
            print_url_results(raw_arg, label, total_sec, total_count, entries, top_n=args.top)
            sys.exit(0)

        # ── Local folder headless ─────────────────────────────────────
        folder = Path(raw_arg)

        if not check_ffprobe():
            print(f"Error: ffprobe not found on PATH. Download FFmpeg from https://ffmpeg.org/download.html",
                  file=sys.stderr)
            sys.exit(1)

        if not folder.exists():
            print(f"Error: path not found: {folder}", file=sys.stderr)
            sys.exit(1)

        if not folder.is_dir():
            print(f"Error: not a directory: {folder}", file=sys.stderr)
            sys.exit(1)

        on_progress = _make_progress_bar()
        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, args.sort, not args.no_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            sys.exit(0)

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, args.top, show_files=args.files)

        # dupe warning
        groups = find_duplicates(durations, sizes)
        print_dupe_warning(groups)

        if args.export:
            try:
                dest = export_results(folder, total_sec, total_count, tree,
                                      durations, args.export, args.out)
                print(f"  {G}Exported{RST}  {DIM}→{RST}  {W}{dest}{RST}\n")
            except Exception as e:
                print(f"  {R}Export failed:{RST} {e}\n", file=sys.stderr)
                sys.exit(1)

        sys.exit(0)

    # ── INTERACTIVE MODE ─────────────────────────────────────────────
    clear()
    print_banner()

    if not check_ffprobe():
        print(f"  {Y}ffprobe not found on PATH.{RST}  {DIM}Local folder scanning won't work.{RST}")
        print(f"  Download FFmpeg from {C}https://ffmpeg.org/download.html{RST}")
        print(f"  {DIM}(You can still scan YouTube/playlist URLs if yt-dlp is installed.){RST}")
        print()

    on_progress = _make_progress_bar()

    last_scan    = {}
    current_sort = args.sort

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

        # number aliases for the initial menu (1. scan  2. clear  3. quit)
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

        if raw.lower() in ('reset-key', 'apikey', 'api-key'):
            prompt_api_key()
            continue

        if raw.lower() == 'scan':
            print(f"\n  {DIM}Enter a folder path to scan.{RST}\n")
            continue

        # ── URL mode ─────────────────────────────────────────────────
        if _is_url(raw):
            url_prog = _make_url_progress()
            try:
                total_sec, total_count, entries, label = scan_url(raw, url_prog)
            except KeyboardInterrupt:
                print(f"\n\n  {Y}Fetch cancelled.{RST}\n")
                continue

            print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.".ljust(60))
            print_url_results(raw, label, total_sec, total_count, entries, top_n=args.top)

            last_scan = {
                "folder":      raw,
                "total_sec":   total_sec,
                "total_count": total_count,
                "tree":        None,   # no tree for URL scans
                "durations":   {e['title']: e['duration'] for e in entries},
                "sizes":       {},
                "dupe_groups": [],
                "is_url":      True,
                "entries":     entries,
                "label":       label,
            }
            print_post_scan_menu(current_sort)
            continue

        # ── Local folder mode ────────────────────────────────────────
        folder = Path(raw)

        if not folder.exists():
            print(f"\n  {R}Path not found:{RST} {raw}\n")
            continue

        if not folder.is_dir():
            print(f"\n  {R}That is a file, not a folder.{RST}\n")
            continue

        if not check_ffprobe():
            print(f"\n  {R}ffprobe not found on PATH.{RST} Download FFmpeg from https://ffmpeg.org/download.html\n")
            continue

        print(f"  {DIM}Collecting files...{RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_progress, current_sort, not args.no_cache)
        except KeyboardInterrupt:
            print(f"\n\n  {Y}Scan cancelled.{RST}\n")
            continue

        probed     = total_count - hits
        cache_info = f"  {DIM}({hits} cached, {probed} probed){RST}" if hits > 0 else ""
        print(f"\r  {G}Done!{RST}  {W}{total_count}{RST} {'video' if total_count == 1 else 'videos'} found.{cache_info}".ljust(60))
        print_results(folder, total_sec, total_count, tree, durations, sizes, args.top, show_files=getattr(args, "files", False))

        # dupe warning — result cached in last_scan so menu option 6 doesn't re-run it
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

            # number aliases
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

                # Step 1: pick field — loop until valid or back
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

                # Step 2: pick direction — loop until valid or back
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
                    direc = None  # re-prompt

                if direc is None:
                    continue

                current_sort = f"{field}:{direc}"
                _, _, new_tree, new_durations, new_sizes, _ = _run_scan(
                    last_scan["folder"], None, current_sort, True)
                last_scan["tree"]      = new_tree
                last_scan["durations"] = new_durations
                last_scan["sizes"]     = new_sizes
                show_f = getattr(args, "files", False)
                print_results(last_scan["folder"], last_scan["total_sec"],
                              last_scan["total_count"], new_tree,
                              new_durations, last_scan["sizes"], args.top, show_files=show_f)
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

                # Loop until valid format or back
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
                try:
                    dest = export_results(
                        last_scan["folder"], last_scan["total_sec"],
                        last_scan["total_count"], last_scan["tree"],
                        last_scan["durations"], fmt,
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
                # reuse groups computed at scan time — no need to re-run find_duplicates
                print_duplicates(last_scan["dupe_groups"], last_scan["durations"])
                print_post_scan_menu(current_sort)

            else:
                sug = _fuzzy_suggest(first_word, _all_cmds) if first_word else None
                if sug:
                    print(f"  {R}Unknown command.{RST}  {DIM}Did you mean{RST}  {W}{sug}{RST}{DIM}?{RST}")
                else:
                    print(f"  {R}Invalid command.{RST} Type  {G}1. scan{RST}   {B}2. sort{RST}   {M}3. export{RST}   {Y}4. clear{RST}   {R}5. quit{RST}   {C}6. duplicates{RST}")
                print_post_scan_menu(current_sort)


if __name__ == "__main__":
    main()
