import json
import math
import os
import re
import sys
import time

# ── Rate limiting (inlined from _ratelimit.py) ───────────────────────
import time as _time_mod
from collections import deque as _deque
from threading import Lock as _Lock

from ._color import clr
from ._paths import YT_KEY_FILE, YT_QUOTA_FILE, YT_VCACHE_FILE

# ── API key storage (inlined from _apikey.py) ────────────────────────
# Simplest practical option: the key is saved once to a single local file
# with restrictive permissions (0o600, owner read/write only) so it
# doesn't need to be re-entered on every run.
# H-01: compile once at module level.
_YT_KEY_PATTERN = re.compile(r'^AIza[0-9A-Za-z\-_]{35}$')


def _write_private_file(path, text: str) -> None:
    """
    Write `text` to `path` so that it is never readable by other users, not even
    for an instant. The temp file is created owner-only (mkstemp uses mode 0600)
    and then renamed into place, instead of writing with default permissions and
    tightening them afterwards.

    On Windows the mode bits don't apply; the file is protected by the ACL it
    inherits from the user's profile folder (%LOCALAPPDATA%).
    """
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".tmp_{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_api_key(api_key: str) -> bool:
    """
    Store the API key in a local file (owner-only on Linux/macOS).
    Returns True if saved successfully, False otherwise.
    """
    # S-02: validate API key format (YouTube keys start with AIza).
    if not api_key or not _YT_KEY_PATTERN.match(api_key):
        print("  Error: Invalid API key format (expected AIza...)", file=sys.stderr)
        return False

    try:
        _write_private_file(YT_KEY_FILE, api_key)
        return True
    except Exception as e:
        print(f"  Error: Could not save API key: {e}", file=sys.stderr)
        return False


def load_api_key() -> str:
    """Load the API key from local storage. Returns "" if not found."""
    try:
        return YT_KEY_FILE.read_text(encoding='utf-8').strip()
    except Exception:
        return ""


class _RateLimiter:
    """
    Token bucket rate limiter — 100 req/hr to stay well under YouTube quota.

    S-09 fix: state is persisted to disk so the limit is enforced across
    multiple process invocations (e.g. shell loops).  The backing file is
    a simple JSON list of UTC timestamps.  Entries older than time_window
    are pruned on every load/save.
    """
    def __init__(self, max_calls: int, time_window: int):
        self.max_calls   = max_calls
        self.time_window = time_window
        self.calls: _deque[float] = _deque()
        self.lock        = _Lock()
        self._state_file = None   # set lazily after _paths is importable

    def _state_path(self):
        if self._state_file is None:
            from ._paths import APPDATA
            self._state_file = APPDATA / "yt_ratelimit.json"
        return self._state_file

    def _load(self):
        """Load persisted timestamps into self.calls (pruning stale ones)."""
        try:
            import json as _json
            raw = _json.loads(self._state_path().read_text(encoding="utf-8"))
            now = _time_mod.time()
            self.calls = _deque(
                t for t in raw if isinstance(t, (int, float)) and t >= now - self.time_window
            )
        except Exception:
            self.calls = _deque()

    def _save(self):
        """Persist current timestamps atomically."""
        try:
            import json as _json
            import os as _os
            import tempfile
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
            try:
                with _os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    f.write(_json.dumps(list(self.calls)))
                _os.replace(tmp_path, p)
            except Exception:
                try:
                    _os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception:
            pass

    def allow_request(self) -> bool:
        with self.lock:
            self._load()
            now = _time_mod.time()
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                self._save()
                return True
            return False

    def wait_time(self) -> float:
        with self.lock:
            self._load()
            if not self.calls:
                return 0.0
            return max(0.0, (self.calls[0] + self.time_window) - _time_mod.time())

    def reset(self):
        with self.lock:
            self.calls.clear()
            self._save()

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


_MAX_CACHED_DURATION = 10 * 365 * 24 * 3600   # 10 years; no real video is longer


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valid_cache_entry(e) -> bool:
    """
    Keep only cache entries that are safe to use. The cache is a plain file on
    disk, so anything in it is untrusted: wrong types or NaN would otherwise
    crash the report (or the "unavailable" age check) much later.
    """
    if not isinstance(e, dict):
        return False
    if not _is_number(e.get('cached_at', 0)):
        return False
    if e.get('unavailable'):
        return True
    return (isinstance(e.get('title'), str)
            and _is_number(e.get('duration'))
            and 0 <= e['duration'] <= _MAX_CACHED_DURATION)


def _load_yt_video_cache():
    """Load the per-video cache. Returns {} on any error or if file is too large."""
    MAX_YT_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB hard limit
    try:
        if YT_VCACHE_FILE.exists() and YT_VCACHE_FILE.stat().st_size > MAX_YT_CACHE_SIZE:
            print("  [WARN] YouTube cache too large, ignoring.", file=sys.stderr)
            return {}
        raw = json.loads(YT_VCACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {vid: e for vid, e in raw.items() if _valid_cache_entry(e)}


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

def _nth_sunday(year: int, month: int, n: int):
    import datetime
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _pacific_date_without_tzdata(utc_now) -> str:
    """
    Pacific date for a naive UTC datetime, using the US daylight-saving rule in
    force since 2007: DST starts 2:00 local on the 2nd Sunday of March (10:00 UTC)
    and ends 2:00 local on the 1st Sunday of November (09:00 UTC).

    Used when the system has no time zone database. That's the case on Windows
    unless the optional `tzdata` package is installed.
    """
    import datetime
    start = datetime.datetime.combine(_nth_sunday(utc_now.year, 3, 2), datetime.time(10, 0))
    end   = datetime.datetime.combine(_nth_sunday(utc_now.year, 11, 1), datetime.time(9, 0))
    offset = -7 if start <= utc_now < end else -8
    return (utc_now + datetime.timedelta(hours=offset)).strftime("%Y-%m-%d")


def _get_current_date_pt():
    """Return current date string in Pacific Time (where YouTube quota resets)."""
    import datetime
    try:
        import zoneinfo
        pt_now = datetime.datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
        return pt_now.strftime("%Y-%m-%d")
    except Exception:
        # No zoneinfo or no time zone database (e.g. Windows without tzdata).
        utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return _pacific_date_without_tzdata(utc_now)


def _load_quota_tracker():
    """Load quota tracker. Returns (date_str, units_used).

    H-09: validate units_used is a non-negative integer to prevent
    a corrupted file from bypassing the quota guard.
    """
    try:
        data = json.loads(YT_QUOTA_FILE.read_text(encoding="utf-8"))
        date = data.get("date", "")
        raw  = data.get("units_used", 0)
        # Clamp to valid range — never trust disk data blindly
        units_used = max(0, min(int(raw), YT_QUOTA_DAILY_LIMIT))
        return date, units_used
    except Exception:
        return "", 0


def _save_quota_tracker(date, units_used):
    """Persist quota tracker atomically. Failures are silently ignored.

    S-04 fix: use temp-file + rename (atomic) instead of write_text which
    could corrupt the tracker on a mid-write crash and silently reset the
    quota counter to 0.
    """
    try:
        import tempfile
        YT_QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=YT_QUOTA_FILE.parent,
            prefix=".tmp_quota_",
            suffix=".json",
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                f.write(json.dumps({"date": date, "units_used": units_used}, indent=2))
            os.replace(tmp_path, YT_QUOTA_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
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


def _merge_into_cache(cache, new_entries_by_id, save=True):
    """
    Write new_entries_by_id into cache and (by default) persist.
    new_entries_by_id: dict of video_id -> entry dict.

    Issue 11 fix: no longer reloads cache from disk after writing — the
    in-memory dict already contains the new entries after this call.
    """
    now = int(time.time())
    for vid_id, entry in new_entries_by_id.items():
        cache[vid_id] = {**entry, "cached_at": now}
    if save:
        _save_yt_video_cache(cache)


# A video the API didn't return (private, deleted, region-blocked) is remembered as
# a small "unavailable" stub so reruns don't spend quota asking again. Unlike a
# duration, availability can change, so a stub expires and is re-checked.
UNAVAILABLE_TTL = 7 * 24 * 3600


def _cache_state(cache, vid_id, now=None):
    """Return 'hit' (usable entry), 'unavailable' (fresh stub) or 'miss'."""
    e = cache.get(vid_id)
    if not isinstance(e, dict):
        return 'miss'
    if not e.get('unavailable'):
        return 'hit'
    now = time.time() if now is None else now
    return 'unavailable' if now - e.get('cached_at', 0) < UNAVAILABLE_TTL else 'miss'


def _mark_unavailable(cache, video_ids):
    now = int(time.time())
    for vid in video_ids:
        cache[vid] = {'id': vid, 'unavailable': True, 'cached_at': now}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YT_DOMAINS = (
    'youtube.com', 'youtu.be', 'm.youtube.com',
    'music.youtube.com', 'kids.youtube.com', 'gaming.youtube.com',
)

def _is_url(s):
    if s.startswith(('http://', 'https://')):
        return True
    # bare domain shortcuts e.g. "www.youtube.com/..." or "music.youtube.com/..."
    for domain in _YT_DOMAINS:
        if s.startswith(domain) or s.startswith('www.' + domain):
            return True
    if s.startswith('www.'):
        return True
    return False


def _normalise_url(url):
    """Ensure URL has a scheme so urlparse works correctly."""
    if not url.startswith(('http://', 'https://')):
        return 'https://' + url
    return url


def _parse_iso8601_duration(d):
    # YouTube uses P<days>DT<h>H<m>M<s>S, with the day part only on videos of 24 h or more
    m = re.match(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', d or '')
    if not m:
        return 0.0
    dd, h, mi, s = m.groups()
    # H-08: clamp to reasonable max and ensure non-negative
    result = float(dd or 0) * 86400 + float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)
    return max(0.0, min(result, 365 * 86400))  # cap at 1 year


# Error reasons YouTube uses when a quota or rate limit is hit (HTTP 403/429).
_YT_LIMIT_REASONS = (
    'quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded', 'userRateLimitExceeded',
)


class YouTubeLimitError(PermissionError):
    """
    A request limit was hit: Aevum's own hourly limit (kind='rate') or YouTube's
    daily quota (kind='quota'). Retrying later is safe: everything fetched so far
    was saved to the cache.

    _fetch_with_cache fills in `saved` / `total`: how many of the requested
    videos are now in the cache. Both stay None if the limit hit before then.
    """
    def __init__(self, message, kind='rate', retry_after=None):
        super().__init__(message)
        self.kind        = kind
        self.retry_after = retry_after
        self.saved       = None
        self.total       = None


def _yt_api_request(endpoint, params, api_key, quota_cost=None):
    """
    Make one YouTube Data API v3 request and track quota usage.

    Issue 9 fix: quota_cost defaults to the known cost for the endpoint
    (from YT_QUOTA_COST) rather than always blindly charging 1 unit.

    Issue 10 fix: HTTPError is caught and re-raised with a human-readable
    message that includes the API error description (e.g. "quota exceeded").

    B-07 fix: rate limiter check moved here so it fires per API call, not
    once per scan_url invocation.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    if quota_cost is None:
        quota_cost = YT_QUOTA_COST.get(endpoint, 1)

    # B-07: apply rate limiting per API call
    if not youtube_limiter.allow_request():
        wait = youtube_limiter.wait_time()
        raise YouTubeLimitError(
            f"Hourly request limit reached ({youtube_limiter.max_calls} requests per hour).",
            kind='rate', retry_after=wait,
        )

    # Copy params to avoid mutating the caller's dict
    params = {**params, 'key': api_key}
    # S-03 note: API key is in URL query string (YouTube API v3 design).
    # This means the key appears in server logs, proxy logs, and network monitoring.
    # This is a known limitation of the YouTube Data API v3 design.
    # For production use cases, consider OAuth 2.0 service accounts instead.
    url = f"{YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"

    try:
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=15, context=_ctx) as r:
            result = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # Issue 10: extract the API error message from the JSON body
        # Never include the URL (which contains the API key) in error messages
        reason = ''
        try:
            body     = e.read().decode('utf-8', errors='replace')
            err_data = json.loads(body).get('error', {})
            msg      = err_data.get('message', str(e))
            reason   = (err_data.get('errors') or [{}])[0].get('reason', '')
        except Exception:
            msg = str(e)
        if e.code == 429 or reason in _YT_LIMIT_REASONS:
            raise YouTubeLimitError(f"YouTube API quota exceeded: {msg}", kind='quota') from None
        raise RuntimeError(f"YouTube API error {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"YouTube API network error: {e.reason}") from None

    _add_quota_usage(quota_cost)
    return result


class ApiKeyCancelled(Exception):
    """The user gave no API key (empty input, Ctrl-C, closed stdin, or a key that could not be saved)."""


def prompt_api_key():
    """
    Prompt user for YouTube API key and save it to local storage.
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
        print(f"  {clr.G}Key saved{clr.RST}")
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
    from urllib.parse import parse_qs, urlparse
    p          = urlparse(url)
    qs         = parse_qs(p.query)
    path_parts = [x for x in p.path.split('/') if x]
    netloc     = p.netloc.removeprefix('www.')

    # Issue 12: added music.youtube.com; also support kids and gaming subdomains
    if netloc not in _YT_DOMAINS:
        return None, None
    # A link to a video that also carries list=... (copied from inside a playlist)
    # means that one video. Only a /playlist link scans the whole playlist.
    if netloc == 'youtu.be' and path_parts:
        return 'video', path_parts[0]
    if 'v' in qs:
        return 'video', qs['v'][0]
    if 'list' in qs:
        return 'playlist', qs['list'][0]
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


def _has_playlist_param(url):
    """True if the URL carries a list=... parameter."""
    from urllib.parse import parse_qs, urlparse
    return 'list' in parse_qs(urlparse(_normalise_url(url)).query)


def _yt_get_channel_uploads_playlist(channel_ref, api_key, kind='channel_handle'):
    """
    Return (uploads_playlist_id, channel_title), or (None, None) if the channel
    doesn't exist. One API call in the common case.

    /channel/UC... is looked up by id and @handle by forHandle. A bare name
    (from /c/Name or /user/Name) tries forHandle, then forUsername. Errors,
    including quota and rate limits, propagate: they are not "channel not found".
    """
    if kind == 'channel_id':
        lookups = [('id', channel_ref)]
    elif channel_ref.startswith('@'):
        lookups = [('forHandle', channel_ref)]
    else:
        lookups = [('forHandle', channel_ref), ('forUsername', channel_ref)]

    for param_key, param_val in lookups:
        data  = _yt_api_request('channels', {'part': 'contentDetails,snippet', param_key: param_val}, api_key)
        items = data.get('items', [])
        if items:
            uploads = items[0]['contentDetails']['relatedPlaylists']['uploads']
            title   = items[0]['snippet']['title']
            return uploads, title
    return None, None


def _yt_fetch_playlist_video_ids(playlist_id, api_key, on_progress=None):
    ids        = []
    page_token = None
    # H-06: cap pagination at 2000 pages (100,000 videos) to prevent infinite
    # loops from malformed/adversarial nextPageToken responses.
    MAX_PAGES  = 2000
    page_count = 0
    while page_count < MAX_PAGES:
        params = {'part': 'contentDetails', 'playlistId': playlist_id, 'maxResults': 50}
        if page_token:
            params['pageToken'] = page_token
        try:
            data = _yt_api_request('playlistItems', params, api_key)
        except YouTubeLimitError:
            raise
        except Exception as e:
            raise RuntimeError(f"playlistItems API error: {e}")
        for item in data.get('items', []):
            vid = item.get('contentDetails', {}).get('videoId')
            if vid:
                ids.append(vid)
        page_token = data.get('nextPageToken')
        page_count += 1
        # Use a spinner-style callback (total unknown until pagination ends)
        if on_progress:
            on_progress(len(ids), max(len(ids), 1))
        if not page_token:
            break
    return ids


def _yt_fetch_video_details(video_ids, api_key, on_progress=None, progress_offset=0, total=0,
                            on_batch=None):
    """
    Fetch video details from the API in batches of 50.
    Returns (entries, unavailable_ids).

    unavailable_ids: IDs requested but not returned by the API
                     (private, deleted, or region-blocked).

    on_batch(batch_entries, batch_unavailable_ids), if given, is called after
    every batch, so the caller can keep results even if a later batch fails.
    """
    entries         = []
    unavailable_ids = []
    done            = progress_offset

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        try:
            data = _yt_api_request('videos', {'part': 'snippet,contentDetails', 'id': ','.join(batch)}, api_key)
        except YouTubeLimitError:
            raise
        except Exception as e:
            raise RuntimeError(f"videos API error: {e}")

        batch_entries = []
        returned_ids  = set()
        for item in data.get('items', []):
            title    = item['snippet']['title']
            channel  = item['snippet'].get('channelTitle', '')
            duration = _parse_iso8601_duration(item['contentDetails']['duration'])
            vid_url  = f"https://youtu.be/{item['id']}"
            batch_entries.append({
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

        entries.extend(batch_entries)
        missing = [vid for vid in dict.fromkeys(batch) if vid not in returned_ids]
        unavailable_ids.extend(missing)
        done += len(missing)
        if on_progress and total > 0 and missing:
            on_progress(done, total)
        if on_batch:
            on_batch(batch_entries, missing)

    return entries, unavailable_ids


def _fetch_with_cache(video_ids, api_key, cache, on_progress=None, persist=True):
    """
    For a list of video IDs:
      - Return cached entries immediately for IDs already in cache
      - Skip IDs recently found to be unavailable (private/deleted/blocked)
      - Only call the API for the rest, and keep each batch as it arrives

    Results are saved to disk when the fetch ends, however it ends (finished,
    rate/quota limit, error or Ctrl-C), and every 10 batches in between, so a
    failure part-way never throws away the batches already fetched.

    If a YouTube limit stops the fetch, the YouTubeLimitError is re-raised with
    .saved / .total filled in. persist=False keeps everything in memory only.

    Returns (entries, cache_hits, unavailable_ids). cache_hits counts usable
    cached entries only; unavailable_ids includes remembered unavailable videos.
    """
    now             = time.time()
    states          = {vid: _cache_state(cache, vid, now) for vid in video_ids}
    cache_hits      = sum(1 for vid in video_ids if states[vid] == 'hit')
    unavailable_ids = [vid for vid in dict.fromkeys(video_ids) if states[vid] == 'unavailable']
    new_ids         = [vid for vid in video_ids if states[vid] == 'miss']
    total           = len(video_ids)
    batches         = 0

    def on_batch(batch_entries, batch_unavailable):
        nonlocal batches
        _merge_into_cache(cache, {e['id']: e for e in batch_entries}, save=False)
        _mark_unavailable(cache, batch_unavailable)
        batches += 1
        if persist and batches % 10 == 0:
            _save_yt_video_cache(cache)

    if new_ids:
        try:
            _, new_unavailable = _yt_fetch_video_details(
                new_ids, api_key, on_progress,
                cache_hits + len(unavailable_ids), total, on_batch=on_batch)
            unavailable_ids += new_unavailable
        except YouTubeLimitError as e:
            e.total = total
            e.saved = sum(1 for vid in video_ids if _cache_state(cache, vid) != 'miss')
            raise
        finally:
            if persist:
                _save_yt_video_cache(cache)

    if not new_ids and on_progress and total > 0:
        on_progress(total, total)

    entries = []
    for vid in video_ids:
        if _cache_state(cache, vid) == 'hit':
            cached = cache[vid]
            entries.append({
                'title':    cached.get('title', vid),
                'duration': cached.get('duration', 0.0),
                'url':      cached.get('url', f"https://youtu.be/{vid}"),
                'channel':  cached.get('channel', ''),
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
    # Validate the URL first, so a bad link is rejected before asking for a key.
    kind, vid_id = _parse_yt_url(_normalise_url(url))
    if kind is None:
        raise ValueError(f"Could not parse YouTube URL: {url}")

    api_key = load_api_key()
    if not api_key:
        api_key = prompt_api_key()
        if not api_key:
            raise ApiKeyCancelled("No API key provided.")

    # Check quota before making requests
    try:
        used, remaining, _ = get_quota_status()
        if remaining < 100:
            raise YouTubeLimitError(
                f"Daily quota nearly exhausted ({used:,}/10,000 units used). "
                f"Remaining: {remaining:,} units.",
                kind='quota',
            )
    except PermissionError:
        raise
    except Exception as _quota_err:
        # Quota check failed (e.g. corrupted tracker file) — log and continue
        print(
            f"  {clr.Y}[WARN]{clr.RST}  Could not read quota tracker: {_quota_err}",
            file=sys.stderr,
        )

    # B-07: rate limiting is now enforced per API call inside _yt_api_request

    cache             = _load_yt_video_cache() if use_cache else {}
    label             = url
    entries           = []
    unavailable_count = 0

    # ── Single video ──────────────────────────────────────────────────
    if kind == 'video':
        entries, cache_hits, unavail = _fetch_with_cache(
            [vid_id], api_key, cache, on_progress, persist=use_cache)
        label             = entries[0]['title'] if entries else vid_id
        unavailable_count = len(unavail)

    # ── Playlist ──────────────────────────────────────────────────────
    elif kind == 'playlist':
        try:
            pl_data  = _yt_api_request('playlists', {'part': 'snippet', 'id': vid_id}, api_key)
            pl_items = pl_data.get('items', [])
            label    = pl_items[0]['snippet']['title'] if pl_items else vid_id
        except YouTubeLimitError:
            raise
        except Exception:
            label = vid_id

        ids                         = _yt_fetch_playlist_video_ids(vid_id, api_key, None)
        entries, cache_hits, unavail = _fetch_with_cache(ids, api_key, cache, on_progress, persist=use_cache)
        unavailable_count           = len(unavail)

    # ── Channel ───────────────────────────────────────────────────────
    elif kind in ('channel_id', 'channel_handle'):
        uploads_pl, channel_title = _yt_get_channel_uploads_playlist(vid_id, api_key, kind)
        if not uploads_pl:
            raise ValueError(f"Could not find channel: {vid_id}")
        label = channel_title or vid_id

        ids                         = _yt_fetch_playlist_video_ids(uploads_pl, api_key, None)
        entries, cache_hits, unavail = _fetch_with_cache(ids, api_key, cache, on_progress, persist=use_cache)
        unavailable_count           = len(unavail)

    total_sec   = sum(e['duration'] for e in entries)
    total_count = len(entries)
    return total_sec, total_count, entries, label, cache_hits, unavailable_count
