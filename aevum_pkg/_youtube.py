import json
import os
import sys
import time
from pathlib import Path

from ._color import clr
from ._paths import YT_KEY_FILE, YT_QUOTA_FILE, YT_VCACHE_FILE
from ._apikey import save_api_key, load_api_key, delete_api_key, get_storage_method
# ── Rate limiting (inlined from _ratelimit.py) ───────────────────────
import time as _time_mod
from collections import deque as _deque
from threading import Lock as _Lock

class _RateLimiter:
    """Token bucket rate limiter — 100 req/hr to stay well under YouTube quota."""
    def __init__(self, max_calls: int, time_window: int):
        self.max_calls   = max_calls
        self.time_window = time_window
        self.calls       = _deque()
        self.lock        = _Lock()

    def allow_request(self) -> bool:
        with self.lock:
            now = _time_mod.time()
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            return False

    def wait_time(self) -> float:
        with self.lock:
            if not self.calls:
                return 0.0
            return max(0.0, (self.calls[0] + self.time_window) - _time_mod.time())

    def reset(self):
        with self.lock:
            self.calls.clear()

youtube_limiter = _RateLimiter(max_calls=100, time_window=3600)

# Issue 13: file now uses LF line endings (normalised from original CRLF).

YT_API_BASE          = "https://www.googleapis.com/youtube/v3"
YT_QUOTA_DAILY_LIMIT = 10000

# API costs in quota units.  Not all endpoints cost 1 unit — search.list
# costs 100.  Pass the correct cost to _yt_api_request() (Issue 9).
YT_QUOTA_COST = {
    "videos":         1,
    "playlistItems":  1,
    "playlists":      1,
    "channels":       1,
    "search":       100,   # expensive — listed here for future use
}

# ---------------------------------------------------------------------------
# YouTube video cache
# ---------------------------------------------------------------------------
# Stores individual video details keyed by video ID — cached forever since
# a video's duration never changes once uploaded.
#
# File path comes from _paths.py (Issue 23/32).
# ---------------------------------------------------------------------------


def _load_yt_video_cache():
    """Load the per-video cache. Returns {} on any error."""
    try:
        return json.loads(YT_VCACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_yt_video_cache(cache):
    """Persist the per-video cache atomically. Failures are silently ignored."""
    try:
        import tempfile
        YT_VCACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=YT_VCACHE_FILE.parent,
            prefix=".tmp_ytcache_",
            suffix=".json",
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                f.write(json.dumps(cache, indent=None, separators=(',', ':')))
            os.replace(tmp_path, YT_VCACHE_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------------------

def _get_current_date_pt():
    """Return current date string in Pacific Time (where YouTube quota resets).

    Issue 8 fix: use zoneinfo (stdlib >=3.9) for correct DST handling instead
    of a fixed -8 offset that was wrong half the year.  Falls back gracefully
    on Python 3.8 where zoneinfo is not yet in the stdlib.
    """
    import datetime
    try:
        import zoneinfo
        pt_now = datetime.datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    except Exception:
        # Python 3.8 fallback: approximate PDT/PST with -7 (slightly better
        # than always -8, since most of the year the US is on DST).
        utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        pt_now  = utc_now - datetime.timedelta(hours=7)
    return pt_now.strftime("%Y-%m-%d")


def _load_quota_tracker():
    """Load quota tracker. Returns (date_str, units_used)."""
    try:
        data = json.loads(YT_QUOTA_FILE.read_text(encoding="utf-8"))
        return data.get("date", ""), data.get("units_used", 0)
    except Exception:
        return "", 0


def _save_quota_tracker(date, units_used):
    """Persist quota tracker. Failures are silently ignored."""
    try:
        YT_QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_QUOTA_FILE.write_text(
            json.dumps({"date": date, "units_used": units_used}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _add_quota_usage(units):
    """Add units to today's usage total. Auto-resets on a new day."""
    current_date = _get_current_date_pt()
    tracked_date, units_used = _load_quota_tracker()
    if tracked_date != current_date:
        units_used = 0
    units_used += units
    _save_quota_tracker(current_date, units_used)
    return units_used


def get_quota_status():
    """
    Return (units_used, units_remaining, percent_used).
    Estimate based on Aevum's tracked usage only.
    """
    current_date = _get_current_date_pt()
    tracked_date, units_used = _load_quota_tracker()
    if tracked_date != current_date:
        units_used = 0
    units_remaining = max(0, YT_QUOTA_DAILY_LIMIT - units_used)
    percent_used    = min(100.0, (units_used / YT_QUOTA_DAILY_LIMIT) * 100)
    return units_used, units_remaining, percent_used


def _merge_into_cache(cache, new_entries_by_id):
    """
    Write new_entries_by_id into cache and persist.
    new_entries_by_id: dict of video_id -> entry dict.

    Issue 11 fix: no longer reloads cache from disk after writing — the
    in-memory dict already contains the new entries after this call.
    """
    now = int(time.time())
    for vid_id, entry in new_entries_by_id.items():
        cache[vid_id] = {**entry, "cached_at": now}
    _save_yt_video_cache(cache)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_url(s):
    if s.startswith(('http://', 'https://')):
        return True
    if s.startswith('www.'):
        return True  # _parse_yt_url will prepend scheme if needed
    return False


def _normalise_url(url):
    """Ensure URL has a scheme so urlparse works correctly."""
    if url.startswith('www.'):
        return 'https://' + url
    return url


def _parse_iso8601_duration(d):
    import re
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', d or '')
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)


def _yt_api_request(endpoint, params, api_key, quota_cost=None):
    """
    Make one YouTube Data API v3 request and track quota usage.

    Issue 9 fix: quota_cost defaults to the known cost for the endpoint
    (from YT_QUOTA_COST) rather than always blindly charging 1 unit.

    Issue 10 fix: HTTPError is caught and re-raised with a human-readable
    message that includes the API error description (e.g. "quota exceeded").
    """
    import urllib.request
    import urllib.parse
    import urllib.error

    if quota_cost is None:
        quota_cost = YT_QUOTA_COST.get(endpoint, 1)

    # Copy params to avoid mutating the caller's dict
    params = {**params, 'key': api_key}
    url = f"{YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            result = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # Issue 10: extract the API error message from the JSON body
        try:
            body     = e.read().decode('utf-8', errors='replace')
            err_data = json.loads(body)
            msg      = err_data.get('error', {}).get('message', str(e))
        except Exception:
            msg = str(e)
        raise RuntimeError(f"YouTube API error {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"YouTube API network error: {e.reason}")

    _add_quota_usage(quota_cost)
    return result


def prompt_api_key():
    """
    Prompt user for YouTube API key and save it securely.
    
    Security: Now uses secure storage (_apikey.py) instead of plaintext file.
    """
    print()
    print(f"  {clr.Y}YouTube API key required.{clr.RST}")
    print(f"  {clr.DIM}Get a free key in ~2 minutes:{clr.RST}")
    print(f"  {clr.C}1.{clr.RST} Go to {clr.W}https://console.cloud.google.com/{clr.RST}")
    print(f"  {clr.C}2.{clr.RST} Create a project → Enable {clr.W}YouTube Data API v3{clr.RST}")
    print(f"  {clr.C}3.{clr.RST} Credentials → Create API Key → copy it here")
    print()
    try:
        key = input(f"  {clr.C}Paste API key{clr.RST}> ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not key:
        return None
    
    if save_api_key(key):
        storage = get_storage_method()
        storage_name = {
            "keyring": "system keyring (encrypted)",
            "encrypted_file": "encrypted file",
            "plaintext_file": "file",
        }.get(storage, "storage")
        print(f"  {clr.G}Key saved to {storage_name}{clr.RST}")
    else:
        print(f"  {clr.R}Failed to save API key{clr.RST}")
        return None
    
    print()
    return key


def _parse_yt_url(url):
    """
    Parse a YouTube URL into (kind, id).

    Issue 12 fix: music.youtube.com URLs are now accepted.
    """
    from urllib.parse import urlparse, parse_qs
    p          = urlparse(url)
    qs         = parse_qs(p.query)
    path_parts = [x for x in p.path.split('/') if x]
    netloc     = p.netloc.removeprefix('www.')

    # Issue 12: added music.youtube.com
    if netloc not in ('youtube.com', 'youtu.be', 'm.youtube.com', 'music.youtube.com'):
        return None, None
    if 'list' in qs:
        return 'playlist', qs['list'][0]
    if netloc == 'youtu.be' and path_parts:
        return 'video', path_parts[0]
    if 'v' in qs:
        return 'video', qs['v'][0]
    if len(path_parts) == 2 and path_parts[0] == 'shorts':
        return 'video', path_parts[1]
    if path_parts:
        if path_parts[0].startswith('@'):
            return 'channel_handle', path_parts[0]
        if path_parts[0] in ('c', 'user') and len(path_parts) >= 2:
            return 'channel_handle', path_parts[1]
        if path_parts[0] == 'channel' and len(path_parts) >= 2:
            return 'channel_id', path_parts[1]
    return None, None


def _yt_get_channel_uploads_playlist(channel_id_or_handle, api_key):
    for param_key, param_val in [('forHandle', channel_id_or_handle), ('id', channel_id_or_handle)]:
        try:
            data  = _yt_api_request('channels', {'part': 'contentDetails,snippet', param_key: param_val}, api_key)
            items = data.get('items', [])
            if items:
                uploads = items[0]['contentDetails']['relatedPlaylists']['uploads']
                title   = items[0]['snippet']['title']
                return uploads, title
        except Exception:
            continue
    return None, None


def _yt_fetch_playlist_video_ids(playlist_id, api_key, on_progress=None):
    ids        = []
    page_token = None
    while True:
        params = {'part': 'contentDetails', 'playlistId': playlist_id, 'maxResults': 50}
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
        page_token = data.get('nextPageToken')
        # Use a spinner-style callback (total unknown until pagination ends)
        if on_progress:
            on_progress(len(ids), max(len(ids), 1))
        if not page_token:
            break
    return ids


def _yt_fetch_video_details(video_ids, api_key, on_progress=None, progress_offset=0, total=0):
    """
    Fetch video details from the API in batches of 50.
    Returns (entries, unavailable_ids).

    unavailable_ids: IDs requested but not returned by the API
                     (private, deleted, or region-blocked).
    """
    entries         = []
    unavailable_ids = []
    done            = progress_offset

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        try:
            data = _yt_api_request('videos', {'part': 'snippet,contentDetails', 'id': ','.join(batch)}, api_key)
        except Exception as e:
            raise RuntimeError(f"videos API error: {e}")

        returned_ids = set()
        for item in data.get('items', []):
            title    = item['snippet']['title']
            channel  = item['snippet'].get('channelTitle', '')
            duration = _parse_iso8601_duration(item['contentDetails']['duration'])
            vid_url  = f"https://youtu.be/{item['id']}"
            entries.append({
                'id':       item['id'],
                'title':    title,
                'duration': duration,
                'url':      vid_url,
                'channel':  channel,
            })
            returned_ids.add(item['id'])
            done += 1
            if on_progress and total > 0:
                on_progress(done, total)

        missing = set(batch) - returned_ids
        unavailable_ids.extend(missing)
        done += len(missing)
        if on_progress and total > 0 and missing:
            on_progress(done, total)

    return entries, unavailable_ids


def _fetch_with_cache(video_ids, api_key, cache, on_progress=None):
    """
    For a list of video IDs:
      - Return cached entries immediately for IDs already in cache
      - Only call the API for IDs not in cache
      - Merge new results into cache and persist

    Issue 11 fix: removed redundant _load_yt_video_cache() after
    _merge_into_cache() — the dict is already up-to-date in memory.

    Returns (entries, cache_hits, unavailable_ids).
    """
    cached_ids      = [vid for vid in video_ids if vid in cache]
    new_ids         = [vid for vid in video_ids if vid not in cache]
    cache_hits      = len(cached_ids)
    total           = len(video_ids)
    unavailable_ids = []

    if new_ids:
        new_entries, unavailable_ids = _yt_fetch_video_details(
            new_ids, api_key, on_progress, cache_hits, total)
        _merge_into_cache(cache, {e['id']: e for e in new_entries})
        # Issue 11: cache dict is already updated in-place by _merge_into_cache
        # — no need to reload from disk here.

    if not new_ids and on_progress and total > 0:
        on_progress(total, total)

    entries = []
    for vid in video_ids:
        if vid in cache:
            e = cache[vid]
            entries.append({
                'title':    e.get('title', vid),
                'duration': e.get('duration', 0.0),
                'url':      e.get('url', f"https://youtu.be/{vid}"),
                'channel':  e.get('channel', ''),
            })
    return entries, cache_hits, unavailable_ids


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_url(url, on_progress=None, use_cache=True):
    """
    Fetch durations for a YouTube URL via the Data API v3.
    
    Security: Implements rate limiting and quota checking to prevent abuse.

    Returns (total_sec, total_count, entries, label, cache_hits, unavailable_count).
    """
    api_key = load_api_key()
    if not api_key:
        api_key = prompt_api_key()
        if not api_key:
            return 0, 0, [], 'cancelled', 0, 0
    
    # Check quota before making requests — only swallow non-PermissionError failures
    try:
        used, remaining, pct = get_quota_status()
    except Exception:
        pass
    else:
        if remaining < 100:
            raise PermissionError(
                f"Daily quota nearly exhausted ({used:,}/10,000 units used). "
                f"Remaining: {remaining:,} units. Try again tomorrow."
            )
    
    # Security: Apply rate limiting
    if not youtube_limiter.allow_request():
        wait = youtube_limiter.wait_time()
        print(
            f"  {clr.Y}[RATE LIMIT]{clr.RST} "
            f"Please wait {int(wait)}s before next request.",
            file=sys.stderr
        )
        time.sleep(wait)
        
        # Try again after waiting
        if not youtube_limiter.allow_request():
            raise PermissionError(
                "Rate limit exceeded. Please try again later."
            )

    kind, vid_id = _parse_yt_url(_normalise_url(url))
    if kind is None:
        raise ValueError(f"Could not parse YouTube URL: {url}")

    cache             = _load_yt_video_cache() if use_cache else {}
    label             = url
    entries           = []
    unavailable_count = 0

    # ── Single video ──────────────────────────────────────────────────
    if kind == 'video':
        if use_cache and vid_id in cache:
            e = cache[vid_id]
            entries = [{
                'title':    e.get('title', vid_id),
                'duration': e.get('duration', 0.0),
                'url':      e.get('url', f"https://youtu.be/{vid_id}"),
                'channel':  e.get('channel', ''),
            }]
            label      = entries[0]['title']
            cache_hits = 1
            if on_progress:
                on_progress(1, 1)
        else:
            fetched, unavailable_ids = _yt_fetch_video_details([vid_id], api_key, on_progress, 0, 1)
            if fetched:
                _merge_into_cache(cache, {vid_id: fetched[0]})
            entries           = fetched
            label             = entries[0]['title'] if entries else vid_id
            cache_hits        = 0
            unavailable_count = len(unavailable_ids)

    # ── Playlist ──────────────────────────────────────────────────────
    elif kind == 'playlist':
        try:
            pl_data  = _yt_api_request('playlists', {'part': 'snippet', 'id': vid_id}, api_key)
            pl_items = pl_data.get('items', [])
            label    = pl_items[0]['snippet']['title'] if pl_items else vid_id
        except Exception:
            label = vid_id

        ids                         = _yt_fetch_playlist_video_ids(vid_id, api_key, None)
        entries, cache_hits, unavail = _fetch_with_cache(ids, api_key, cache, on_progress)
        unavailable_count           = len(unavail)

    # ── Channel ───────────────────────────────────────────────────────
    elif kind in ('channel_id', 'channel_handle'):
        uploads_pl, channel_title = _yt_get_channel_uploads_playlist(vid_id, api_key)
        if not uploads_pl:
            raise ValueError(f"Could not find channel: {vid_id}")
        label = channel_title or vid_id

        ids                         = _yt_fetch_playlist_video_ids(uploads_pl, api_key, None)
        entries, cache_hits, unavail = _fetch_with_cache(ids, api_key, cache, on_progress)
        unavailable_count           = len(unavail)

    total_sec   = sum(e['duration'] for e in entries)
    total_count = len(entries)
    return total_sec, total_count, entries, label, cache_hits, unavailable_count


def _make_url_progress():
    def on_progress(done, _total):
        print(f"\r  {clr.C}Collecting video IDs...{clr.RST}  {clr.C}{done} found{clr.RST}", end='', flush=True)
    return on_progress


# ---------------------------------------------------------------------------
# Cache management helpers (called by cmd_cache in _config.py)
# ---------------------------------------------------------------------------

def yt_cache_stats():
    """Return (count, total_bytes) for the YouTube video cache."""
    try:
        data  = _load_yt_video_cache()
        count = len(data)
        size  = YT_VCACHE_FILE.stat().st_size if YT_VCACHE_FILE.exists() else 0
        return count, size
    except Exception:
        return 0, 0


def yt_cache_clear():
    """Delete the YouTube video cache file."""
    try:
        if YT_VCACHE_FILE.exists():
            YT_VCACHE_FILE.unlink()
            return True
    except Exception:
        pass
    return False
