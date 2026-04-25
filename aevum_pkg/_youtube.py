import json
import os
from pathlib import Path

from ._color import clr

YT_API_KEY_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "yt_api_key.txt"
YT_API_BASE     = "https://www.googleapis.com/youtube/v3"


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
        return json.loads(r.read().decode('utf-8'))


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


def _yt_fetch_video_details(video_ids, api_key):
    entries = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        try:
            data = _yt_api_request('videos', {'part': 'snippet,contentDetails', 'id': ','.join(batch)}, api_key)
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
    Fetch durations for a YouTube URL via the Data API v3.
    Returns (total_sec, total_count, entries, label).
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
        try:
            pl_data  = _yt_api_request('playlists', {'part': 'snippet', 'id': vid_id}, api_key)
            pl_items = pl_data.get('items', [])
            label    = pl_items[0]['snippet']['title'] if pl_items else vid_id
        except Exception:
            label = vid_id
        ids = _yt_fetch_playlist_video_ids(vid_id, api_key, on_progress)
        if on_progress:
            print(f"\r  {clr.C}Fetching video details...{clr.RST}  {clr.C}{len(ids)} videos{clr.RST}  {clr.DIM}(this may take a moment){clr.RST}".ljust(70), flush=True)
        entries = _yt_fetch_video_details(ids, api_key)

    elif kind in ('channel_id', 'channel_handle'):
        uploads_pl, channel_title = _yt_get_channel_uploads_playlist(vid_id, api_key)
        if not uploads_pl:
            raise ValueError(f"Could not find channel: {vid_id}")
        label = channel_title or vid_id
        ids   = _yt_fetch_playlist_video_ids(uploads_pl, api_key, on_progress)
        if on_progress:
            print(f"\r  {clr.C}Fetching video details...{clr.RST}  {clr.C}{len(ids)} videos{clr.RST}  {clr.DIM}(this may take a moment){clr.RST}".ljust(70), flush=True)
        entries = _yt_fetch_video_details(ids, api_key)

    total_sec   = sum(e['duration'] for e in entries)
    total_count = len(entries)
    return total_sec, total_count, entries, label


def _make_url_progress():
    def on_progress(done, _total):
        print(f"\r  {clr.C}Collecting video IDs...{clr.RST}  {clr.C}{done} found{clr.RST}", end='', flush=True)
    return on_progress
