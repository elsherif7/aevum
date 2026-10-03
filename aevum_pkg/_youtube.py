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

_YT_KEY_PATTERN = re.compile(r'^AIza[0-9A-Za-z\-_]{35}$')


def _write_private_file(path, text: str) -> None:
    """
    Write text so other users can never read it, not even briefly: the temp file is
    created owner-only (mkstemp uses 0600) and then renamed into place. Mode bits don't
    apply on Windows, where the profile folder's ACL protects the file.
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
    """Save the key to a local file. Returns True on success."""
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
    """Return the saved key, or "" if there is none."""
    try:
        return YT_KEY_FILE.read_text(encoding='utf-8').strip()
    except Exception:
        return ""


YT_API_BASE = "https://www.googleapis.com/youtube/v3"


# Video details are cached by ID. A duration never changes once a video is uploaded.

_MAX_CACHED_DURATION = 10 * 365 * 24 * 3600   # 10 years


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valid_cache_entry(e) -> bool:
    """
    The cache is a plain file on disk, so entries are checked before use: a wrong
    type or NaN would otherwise crash the report much later.
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
    """Return the cache, or {} if it is missing, unreadable or too large."""
    MAX_YT_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB
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
    """Write the cache atomically. Failures are ignored, since the cache is only an optimisation."""
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
    now = int(time.time())
    for vid_id, entry in new_entries_by_id.items():
        cache[vid_id] = {**entry, "cached_at": now}
    if save:
        _save_yt_video_cache(cache)


# A video the API doesn't return (private, deleted, region-blocked) is remembered as a
# small stub, so reruns don't spend quota asking again. The stub expires because
# availability can change.
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


_YT_DOMAINS = (
    'youtube.com', 'youtu.be', 'm.youtube.com',
    'music.youtube.com', 'kids.youtube.com', 'gaming.youtube.com',
)

_HTTP_SCHEMES = ('http://', 'https://')


def _is_url(s):
    if s.lower().startswith(_HTTP_SCHEMES):
        return True
    # bare domains such as "www.youtube.com/..." or "music.youtube.com/..."
    host = re.split(r'[/?#]', s, maxsplit=1)[0].lower().rsplit(':', 1)[0]
    return host.startswith('www.') or host in _YT_DOMAINS


def _normalise_url(url):
    if not url.lower().startswith(_HTTP_SCHEMES):
        return 'https://' + url
    return url


def _parse_iso8601_duration(d):
    # YouTube uses P<days>DT<h>H<m>M<s>S, with the day part only on videos of 24 h or more
    m = re.match(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', d or '')
    if not m:
        return 0.0
    dd, h, mi, s = m.groups()
    result = float(dd or 0) * 86400 + float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)
    return max(0.0, min(result, 365 * 86400))  # capped at one year


# The daily quota won't clear until midnight Pacific Time, so retrying is pointless.
# A rate limit clears within seconds, so it is worth retrying.
_YT_QUOTA_REASONS = ('quotaExceeded', 'dailyLimitExceeded')              # HTTP 403
_YT_RATE_REASONS  = ('rateLimitExceeded', 'userRateLimitExceeded')       # HTTP 429 (or 403)

_RETRY_DELAYS    = (1, 2, 4)   # seconds before each retry when YouTube gives no hint
_MAX_RETRY_AFTER = 30          # a longer Retry-After stops the scan instead of waiting

# server errors, timeouts and dropped connections (including half-received responses)
_TRANSIENT_HTTP    = (500, 502, 503, 504)
_TRANSIENT_NETWORK = (TimeoutError, ConnectionError, http.client.HTTPException)


class YouTubeLimitError(PermissionError):
    """
    YouTube refused a request because of a limit. Everything fetched so far is
    already in the cache, so running the command again later is safe.

    kind='quota': the daily quota is used up.
    kind='rate':  a rate limit that outlasted the retries, or a Retry-After longer
                  than we will wait.

    retry_after is the wait YouTube asked for in seconds, or None. _fetch_with_cache
    fills in saved and total (videos now in the cache, and videos requested).
    """
    def __init__(self, message, kind='rate', retry_after=None):
        super().__init__(message)
        self.kind        = kind
        self.retry_after = retry_after
        self.saved       = None
        self.total       = None


_ssl_context = None


def _get_ssl_context():
    """One TLS context per run, because creating it loads the system certificates (~25 ms)."""
    global _ssl_context
    if _ssl_context is None:
        import ssl
        _ssl_context = ssl.create_default_context()
    return _ssl_context


def _classify_http_error(e):
    """Return (kind, message). kind is 'quota', 'rate', 'transient', or None for any other error."""
    reason = ''
    try:
        body     = e.read().decode('utf-8', errors='replace')
        err_data = json.loads(body).get('error', {})
        msg      = err_data.get('message', str(e))
        reason   = (err_data.get('errors') or [{}])[0].get('reason', '')
    except Exception:
        msg = str(e)
    finally:
        e.close()   # release the connection; Python 3.14+ warns if it is left open
    if reason in _YT_QUOTA_REASONS:
        return 'quota', msg
    if e.code == 429 or reason in _YT_RATE_REASONS:
        return 'rate', msg
    if e.code in _TRANSIENT_HTTP:
        return 'transient', msg
    return None, msg


_RETRY_AFTER_NUMBER = re.compile(r'\d+(?:\.\d+)?')


def _retry_after_seconds(value, now=None):
    """Parse a Retry-After header (seconds or an HTTP date). Returns seconds, or None if unusable."""
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
        when = when.replace(tzinfo=datetime.UTC)
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


def _limit_message(kind, msg):
    if kind == 'transient':
        return f"YouTube is temporarily unavailable: {msg}"
    return f"YouTube is limiting requests: {msg}"


def _yt_api_request(endpoint, params, api_key):
    """
    Make one YouTube Data API v3 request and return the parsed JSON.

    Aevum keeps no quota counter or request limit of its own. YouTube's responses decide:

      - daily quota used up: YouTubeLimitError(kind='quota') at once
      - rate limit (429): retried, then YouTubeLimitError(kind='rate')
      - 5xx, timeouts, dropped connections: retried the same way, then RuntimeError
      - Retry-After up to _MAX_RETRY_AFTER seconds is honoured exactly. A longer one
        raises YouTubeLimitError(kind='rate', retry_after=...) instead of retrying early.
        Without it we back off 1 s, 2 s, 4 s.
      - anything else (bad key, bad request): RuntimeError, never retried

    Every request is a GET, so retrying is safe. Errors carry the API's own message and
    never the URL, because the URL contains the key.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    params = {**params, 'key': api_key}
    # the API only accepts the key in the query string
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
    """Parse a YouTube URL into (kind, id), or (None, None) if it isn't a supported link."""
    from urllib.parse import parse_qs, urlparse
    try:
        p      = urlparse(url)
        host   = (p.hostname or '').removeprefix('www.')
    except ValueError:
        return None, None
    qs         = parse_qs(p.query)
    path_parts = [x for x in p.path.split('/') if x]

    if host not in _YT_DOMAINS:
        return None, None
    # a video link that also carries list=... (copied from inside a playlist) means that
    # one video. Only a /playlist link scans the whole playlist.
    if host == 'youtu.be' and path_parts:
        return 'video', path_parts[0]
    if 'v' in qs:
        return 'video', qs['v'][0]
    if 'list' in qs:
        return 'playlist', qs['list'][0]
    if len(path_parts) == 2 and path_parts[0] in ('shorts', 'live', 'embed'):
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
    from urllib.parse import parse_qs, urlparse
    return 'list' in parse_qs(urlparse(_normalise_url(url)).query)


def _yt_get_channel_uploads_playlist(channel_ref, api_key, kind='channel_handle'):
    """
    Return (uploads_playlist_id, channel_title), or (None, None) if the channel doesn't exist.

    /channel/UC... is looked up by id and @handle by forHandle. A bare name (from
    /c/Name or /user/Name) tries forHandle, then forUsername. Errors, including quota
    and rate limits, propagate: they are not "channel not found".
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
    # 2000 pages (100,000 videos) is a cap so a bad nextPageToken can't loop forever
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
        # the total isn't known until the last page
        if on_progress:
            on_progress(len(ids), max(len(ids), 1))
        if not page_token:
            break
    return ids


def _yt_fetch_video_details(video_ids, api_key, on_progress=None, progress_offset=0, total=0,
                            on_batch=None):
    """
    Fetch video details in batches of 50. Returns (entries, unavailable_ids), where
    unavailable_ids are IDs the API didn't return (private, deleted or region-blocked).

    on_batch(batch_entries, batch_unavailable_ids) is called after every batch, so the
    caller keeps its results even if a later batch fails.
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
    Return the details for video_ids, asking the API only for IDs that are neither
    cached nor recently found unavailable.

    The cache is saved when the fetch ends, however it ends (finished, limit, error or
    Ctrl-C), and every 10 batches in between, so a failure part-way keeps the batches
    already fetched. persist=False keeps everything in memory.

    A YouTubeLimitError is re-raised with .saved and .total filled in.

    Returns (entries, cache_hits, unavailable_ids). cache_hits counts usable cached
    entries only; unavailable_ids includes remembered unavailable videos.
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
            # only rewrite the file if a batch actually arrived
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


def scan_url(url, on_progress=None, use_cache=True):
    """
    Fetch durations for a YouTube URL via the Data API v3.

    Returns (total_sec, total_count, entries, label, cache_hits, unavailable_count).
    """
    # validate first, so a bad link is rejected before asking for a key
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

    if kind == 'video':
        entries, cache_hits, unavail = _fetch_with_cache(
            [vid_id], api_key, cache, on_progress, persist=use_cache)
        label             = entries[0]['title'] if entries else vid_id
        unavailable_count = len(unavail)

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
