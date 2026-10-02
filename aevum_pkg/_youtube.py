import email.utils
import http.client
import json
import math
import os
import re
import sys
import time

from ._color import clr
from ._paths import YT_KEY_FILE, YT_VCACHE_FILE

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


# Issue 13: file now uses LF line endings (normalised from original CRLF).

YT_API_BASE = "https://www.googleapis.com/youtube/v3"


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


# What YouTube says when it refuses a request because of a limit. The two need
# different handling: the daily quota won't clear until midnight Pacific Time, so
# retrying is pointless, while a short-window rate limit clears within seconds.
_YT_QUOTA_REASONS = ('quotaExceeded', 'dailyLimitExceeded')              # HTTP 403
_YT_RATE_REASONS  = ('rateLimitExceeded', 'userRateLimitExceeded')       # HTTP 429 (or 403)

_RETRY_DELAYS    = (1, 2, 4)   # seconds to wait before each retry when YouTube gives no hint
_MAX_RETRY_AFTER = 30          # longest Retry-After we sit through; a longer one stops the scan

# Temporary trouble worth retrying: server-side errors, and timeouts or dropped
# connections (including half-received responses).
_TRANSIENT_HTTP    = (500, 502, 503, 504)
_TRANSIENT_NETWORK = (TimeoutError, ConnectionError, http.client.HTTPException)


class YouTubeLimitError(PermissionError):
    """
    YouTube refused a request because of a limit. Retrying later is safe:
    everything fetched so far was saved to the cache.

    kind='quota': the daily quota is used up (it resets at midnight Pacific Time).
    kind='rate':  YouTube asked us to slow down or wait: a rate limit that outlasted
                  the automatic retries, or a Retry-After longer than we'll sit through.

    retry_after is how long YouTube asked us to wait, in seconds, or None if it
    didn't say. _fetch_with_cache fills in
    `saved` / `total`: how many of the requested videos are now in the cache. Both
    stay None if the limit hit before then.
    """
    def __init__(self, message, kind='rate', retry_after=None):
        super().__init__(message)
        self.kind        = kind
        self.retry_after = retry_after
        self.saved       = None
        self.total       = None


_ssl_context = None


def _get_ssl_context():
    """One TLS context for the whole run. Creating it loads the system certificates (~25 ms)."""
    global _ssl_context
    if _ssl_context is None:
        import ssl
        _ssl_context = ssl.create_default_context()
    return _ssl_context


def _classify_http_error(e):
    """Return (kind, message): kind is 'quota', 'rate', 'transient', or None for any other error."""
    reason = ''
    try:
        body     = e.read().decode('utf-8', errors='replace')
        err_data = json.loads(body).get('error', {})
        msg      = err_data.get('message', str(e))
        reason   = (err_data.get('errors') or [{}])[0].get('reason', '')
    except Exception:
        msg = str(e)
    if reason in _YT_QUOTA_REASONS:
        return 'quota', msg
    if e.code == 429 or reason in _YT_RATE_REASONS:
        return 'rate', msg
    if e.code in _TRANSIENT_HTTP:
        return 'transient', msg
    return None, msg


_RETRY_AFTER_NUMBER = re.compile(r'\d+(?:\.\d+)?')


def _retry_after_seconds(value, now=None):
    """
    Parse a Retry-After header: either a number of seconds or an HTTP date.
    Returns the wait in seconds (never negative), or None if the header is
    missing or can't be understood.
    """
    if value is None:
        return None
    value = str(value).strip()
    if _RETRY_AFTER_NUMBER.fullmatch(value):
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        import datetime
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


def _limit_message(kind, msg):
    if kind == 'transient':
        return f"YouTube is temporarily unavailable: {msg}"
    return f"YouTube is limiting requests: {msg}"


def _yt_api_request(endpoint, params, api_key):
    """
    Make one YouTube Data API v3 request and return the parsed JSON.

    Aevum keeps no quota counter and no request limit of its own: YouTube is the
    source of truth for both, and says so in its responses.

      - daily quota used up (403 quotaExceeded): YouTubeLimitError(kind='quota') at
        once, since waiting won't help until the quota resets
      - rate limit (429): retried, then YouTubeLimitError(kind='rate')
      - temporary trouble (HTTP 5xx, timeouts, dropped connections): retried the same
        way, then RuntimeError
      - Retry-After: a wait of up to _MAX_RETRY_AFTER seconds is honoured exactly; a
        longer one stops at once with YouTubeLimitError(kind='rate', retry_after=...)
        instead of retrying sooner than YouTube asked. Without it, we back off
        1 s, 2 s, 4 s (no jitter: Aevum is a single-user tool).
      - anything else (bad key, bad request, ...): RuntimeError, never retried

    Every request here is a GET, so retrying is safe.

    Issue 10 fix: errors carry the API's own message, never the URL (which contains
    the API key).
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    # Copy params to avoid mutating the caller's dict
    params = {**params, 'key': api_key}
    # S-03 note: API key is in URL query string (YouTube API v3 design).
    # This means the key appears in server logs, proxy logs, and network monitoring.
    # This is a known limitation of the YouTube Data API v3 design.
    # For production use cases, consider OAuth 2.0 service accounts instead.
    url = f"{YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"

    ctx  = _get_ssl_context()
    last = len(_RETRY_DELAYS)
    for attempt in range(last + 1):
        try:
            with urllib.request.urlopen(url, timeout=15, context=ctx) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            kind, msg = _classify_http_error(e)
            if kind in ('rate', 'transient'):
                # Honour a short Retry-After exactly. A long one means YouTube wants us
                # to stop: retrying earlier than it asked would be wrong, so we stop,
                # keep what we have, and say when to come back.
                asked = _retry_after_seconds(e.headers.get('Retry-After') if e.headers else None)
                if asked is not None and asked > _MAX_RETRY_AFTER:
                    raise YouTubeLimitError(
                        _limit_message(kind, msg), kind='rate', retry_after=asked) from None
                if attempt < last:
                    time.sleep(asked if asked is not None else _RETRY_DELAYS[attempt])
                    continue
                if kind == 'rate':
                    raise YouTubeLimitError(
                        _limit_message(kind, msg), kind='rate', retry_after=asked) from None
            if kind == 'quota':
                raise YouTubeLimitError(f"YouTube API quota exceeded: {msg}", kind='quota') from None
            raise RuntimeError(f"YouTube API error {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, _TRANSIENT_NETWORK) and attempt < last:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            raise RuntimeError(f"YouTube API network error: {e.reason}") from None
        except _TRANSIENT_NETWORK as e:
            # timeout or dropped connection while reading the response
            if attempt < last:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            raise RuntimeError(f"YouTube API network error: {e}") from None


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
            # Only rewrite the cache file if a batch actually arrived.
            if persist and batches:
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

    Quota and rate limits are YouTube's to enforce: when it refuses a request,
    _yt_api_request raises YouTubeLimitError and the videos fetched so far are
    already saved (see _fetch_with_cache).

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
