import json
import os
import time
from pathlib import Path

from ._color import clr

YT_API_KEY_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "yt_api_key.txt"
YT_API_BASE     = "https://www.googleapis.com/youtube/v3"

# ---------------------------------------------------------------------------
# YouTube API quota tracker
# ---------------------------------------------------------------------------
# Tracks daily API usage to estimate remaining quota (10,000 units/day).
# Resets automatically at midnight Pacific Time (Google's quota reset time).
#
# File: %LOCALAPPDATA%\Aevum\yt_quota_tracker.json
# Format: { "date": "YYYY-MM-DD", "units_used": 123 }
# ---------------------------------------------------------------------------

YT_QUOTA_TRACKER_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "yt_quota_tracker.json"
YT_QUOTA_DAILY_LIMIT  = 10000

# API costs (in quota units):
# - videos endpoint: 1 unit
# - playlistItems endpoint: 1 unit
# - playlists endpoint: 1 unit
# - channels endpoint: 1 unit

# ---------------------------------------------------------------------------
# YouTube video cache
# ---------------------------------------------------------------------------
# Stores individual video details keyed by video ID — cached forever since
# a video's duration never changes once uploaded.
# Playlist/channel rescans only fetch IDs from the API, diff against this
# cache, and only call the videos endpoint for IDs not already stored.
#
# File: %LOCALAPPDATA%\Aevum\yt_video_cache.json
# Format: { "video_id": { "title", "duration", "channel", "url", "cached_at" }, ... }
# ---------------------------------------------------------------------------

YT_VIDEO_CACHE_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "yt_video_cache.json"


def _load_yt_video_cache():
    """Load the per-video cache. Returns {} on any error."""
    try:
        return json.loads(YT_VIDEO_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_yt_video_cache(cache):
    """Persist the per-video cache. Failures are silently ignored."""
    try:
        YT_VIDEO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_VIDEO_CACHE_FILE.write_text(
            json.dumps(cache, indent=None, separators=(',', ':')),
            encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------------------

def _get_current_date_pt():
    """Return current date in Pacific Time (where YouTube quota resets)."""
    import datetime
    # Approximate PT offset (PST -8, PDT -7). This is rough but good enough.
    utc_now = datetime.datetime.utcnow()
    pt_now  = utc_now - datetime.timedelta(hours=8)
    return pt_now.strftime("%Y-%m-%d")


def _load_quota_tracker():
    """Load quota tracker. Returns (date, units_used)."""
    try:
        data = json.loads(YT_QUOTA_TRACKER_FILE.read_text(encoding="utf-8"))
        return data.get("date", ""), data.get("units_used", 0)
    except Exception:
        return "", 0


def _save_quota_tracker(date, units_used):
    """Persist quota tracker. Failures are silently ignored."""
    try:
        YT_QUOTA_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_QUOTA_TRACKER_FILE.write_text(
            json.dumps({"date": date, "units_used": units_used}, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass


def _add_quota_usage(units):
    """Add units to today's quota usage. Auto-resets if it's a new day."""
    current_date = _get_current_date_pt()
    tracked_date, units_used = _load_quota_tracker()
    
    # Reset if it's a new day
    if tracked_date != current_date:
        units_used = 0
    
    units_used += units
    _save_quota_tracker(current_date, units_used)
    return units_used


def get_quota_status():
    """
    Return (units_used, units_remaining, percent_used).
    This is an estimate based on Aevum's tracked usage only.
    """
    current_date = _get_current_date_pt()
    tracked_date, units_used = _load_quota_tracker()
    
    # Reset if it's a new day
    if tracked_date != current_date:
        units_used = 0
    
    units_remaining = max(0, YT_QUOTA_DAILY_LIMIT - units_used)
    percent_used = min(100, (units_used / YT_QUOTA_DAILY_LIMIT) * 100)
    
    return units_used, units_remaining, percent_used


def _merge_into_cache(cache, new_entries_by_id):
    """
    Write new_entries_by_id into cache and persist.
    new_entries_by_id: dict of video_id -> entry dict (title, duration, channel, url).
    """
    now = int(time.time())
    for vid_id, entry in new_entries_by_id.items():
        cache[vid_id] = {**entry, "cached_at": now}
    _save_yt_video_cache(cache)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_url(s):
    return s.startswith(('http://', 'https://')) or s.startswith('www.')


def _parse_iso8601_duration(d):
    import re
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', d or '')
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)


def _yt_api_request(endpoint, params, api_key):
    import urllib.request, urllib.parse
    params['key'] = api_key
    url = f"{YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        result = json.loads(r.read().decode('utf-8'))
    
    # Track quota usage (all endpoints cost 1 unit)
    _add_quota_usage(1)
    
    return result


def load_api_key():
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
    save_api_key(key)
    print(f"  {clr.G}Key saved to {YT_API_KEY_FILE}{clr.RST}")
    print()
    return key


def _parse_yt_url(url):
    from urllib.parse import urlparse, parse_qs
    p          = urlparse(url)
    qs         = parse_qs(p.query)
    path_parts = [x for x in p.path.split('/') if x]
    netloc     = p.netloc.replace('www.', '')

    if netloc not in ('youtube.com', 'youtu.be', 'm.youtube.com'):
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
        if on_progress:
            on_progress(len(ids), 0)
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    return ids


def _yt_fetch_video_details(video_ids, api_key, on_progress=None, progress_offset=0, total=0):
    """
    Fetch video details from the API.
    Returns (entries, unavailable_ids).
    
    unavailable_ids: video IDs that were requested but not returned by the API
                     (private, deleted, or region-blocked videos)
    """
    entries = []
    unavailable_ids = []
    done    = progress_offset
    
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        try:
            data = _yt_api_request('videos', {'part': 'snippet,contentDetails', 'id': ','.join(batch)}, api_key)
        except Exception as e:
            raise RuntimeError(f"videos API error: {e}")
        
        # Track which IDs were actually returned
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
        
        # Find IDs that were requested but not returned
        missing = set(batch) - returned_ids
        unavailable_ids.extend(missing)
        
        # Count missing videos for progress tracking
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
    Returns (entries, cache_hits, unavailable_ids).
    """
    cached_ids = [vid for vid in video_ids if vid in cache]
    new_ids    = [vid for vid in video_ids if vid not in cache]
    cache_hits = len(cached_ids)
    total      = len(video_ids)
    unavailable_ids = []

    if new_ids:
        new_entries, unavailable_ids = _yt_fetch_video_details(new_ids, api_key, on_progress, cache_hits, total)
        _merge_into_cache(cache, {e['id']: e for e in new_entries})
        cache = _load_yt_video_cache()

    # For all-cached case, fire progress bar per cached video
    if not new_ids and on_progress and total > 0:
        for i in range(1, total + 1):
            on_progress(i, total)

    # Build final ordered entry list from cache
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

    Caching strategy:
      - Single video  : cached forever by video ID — duration never changes
      - Playlist      : fetches ID list fresh every time, diffs against
                        per-video cache, only calls API for new IDs
      - Channel       : same as playlist via uploads playlist

    Pass use_cache=False (or --no-cache flag) to bypass and re-fetch everything.

    Returns (total_sec, total_count, entries, label, cache_hits, unavailable_count).
    """
    api_key = load_api_key()
    if not api_key:
        api_key = prompt_api_key()
        if not api_key:
            return 0, 0, [], 'cancelled', 0, 0

    kind, vid_id = _parse_yt_url(url)
    if kind is None:
        raise ValueError(f"Could not parse YouTube URL: {url}")

    cache   = _load_yt_video_cache() if use_cache else {}
    label   = url
    entries = []
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
            entries    = fetched
            label      = entries[0]['title'] if entries else vid_id
            cache_hits = 0
            unavailable_count = len(unavailable_ids)

    # ── Playlist ──────────────────────────────────────────────────────
    elif kind == 'playlist':
        try:
            pl_data  = _yt_api_request('playlists', {'part': 'snippet', 'id': vid_id}, api_key)
            pl_items = pl_data.get('items', [])
            label    = pl_items[0]['snippet']['title'] if pl_items else vid_id
        except Exception:
            label = vid_id

        ids = _yt_fetch_playlist_video_ids(vid_id, api_key, None)
        entries, cache_hits, unavailable_ids = _fetch_with_cache(ids, api_key, cache, on_progress)
        unavailable_count = len(unavailable_ids)

    # ── Channel ───────────────────────────────────────────────────────
    elif kind in ('channel_id', 'channel_handle'):
        uploads_pl, channel_title = _yt_get_channel_uploads_playlist(vid_id, api_key)
        if not uploads_pl:
            raise ValueError(f"Could not find channel: {vid_id}")
        label = channel_title or vid_id

        ids = _yt_fetch_playlist_video_ids(uploads_pl, api_key, None)
        entries, cache_hits, unavailable_ids = _fetch_with_cache(ids, api_key, cache, on_progress)
        unavailable_count = len(unavailable_ids)

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
        size  = YT_VIDEO_CACHE_FILE.stat().st_size if YT_VIDEO_CACHE_FILE.exists() else 0
        return count, size
    except Exception:
        return 0, 0


def yt_cache_clear():
    """Delete the YouTube video cache file."""
    try:
        if YT_VIDEO_CACHE_FILE.exists():
            YT_VIDEO_CACHE_FILE.unlink()
            return True
    except Exception:
        pass
    return False
