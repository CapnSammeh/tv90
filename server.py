#!/usr/bin/env python3
"""tv90 - 90's TV Simulator with TorBox + TVDB backend."""

import http.server
import json
import os
import urllib.parse
import urllib.request
import random
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "channels.json"
APIKEY_PATH = BASE_DIR / "apikey.json"
SCHEDULE_PATH = BASE_DIR / "schedule.json"
TORBOX_API = "https://api.torbox.app/v1/api"
TVDB_API = "https://api4.thetvdb.com/v4"

DEFAULT_EPISODE_DURATION = 22 * 60  # 22 minutes in seconds

# ── In-memory TVDB token cache ──
_tvdb_token = None
_tvdb_token_expiry = 0


def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def find_torbox_match(torrents, show_name, season, episode):
    """Find a TorBox torrent matching a show name + optional season/episode."""
    if not torrents or not show_name:
        return None

    show_lower = show_name.lower().strip()

    # Step 1: Filter candidates by show name
    candidates = []
    for t in torrents:
        t_name = (t.get("name") or "").lower()
        if show_lower in t_name:
            candidates.append(t)

    if not candidates:
        return None

    # Step 2: Try to match exact season + episode
    if season and episode:
        patterns = [
            f"s{season:02d}e{episode:02d}",     # s23e10
            f"s{season}e{episode}",              # s23e10 (no zero-pad)
            f"{season}x{episode:02d}",           # 23x10
            f"{season}x{episode}",               # 23x10 (no zero-pad)
        ]
        for pattern in patterns:
            for t in candidates:
                if pattern in (t.get("name") or "").lower():
                    return t

    # Step 3: Try to match just the season
    if season:
        season_patterns = [
            f"s{season:02d}",                    # s23
            f"season {season}",                  # season 23
            f"season{season:02d}",               # season23
        ]
        for pattern in season_patterns:
            for t in candidates:
                if pattern in (t.get("name") or "").lower():
                    return t

    # Step 4: Fall back to any matching show torrent (prefer largest = likely full episode)
    candidates.sort(key=lambda t: t.get("size", 0), reverse=True)
    return candidates[0]


def search_external(query, jackett_url=None, jackett_key=None):
    """Search for torrents externally. Returns list of {title, magnet, size, seeders}."""
    results = []

    # ── Jackett ──
    if jackett_url and jackett_key:
        try:
            url = f"{jackett_url.rstrip('/')}/api/v2.0/indexers/all/results"
            params = urllib.parse.urlencode({"Query": query, "Tracker": "", "Category": "5000"})
            req = urllib.request.Request(f"{url}?{params}")
            req.add_header("X-Api-Key", jackett_key)
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                for r in data.get("Results", [])[:20]:
                    magnet = r.get("MagnetUri") or r.get("Link")
                    if magnet:
                        results.append({
                            "title": r.get("Title", ""),
                            "magnet": magnet,
                            "size": r.get("Size", 0),
                            "seeders": r.get("Seeders", 0),
                            "source": "jackett",
                        })
        except Exception:
            pass

    # ── Public fallback #1: TPB via apibay ──
    if not results:
        for alt_query in [query, query.split(" ")[0]]:
            try:
                encoded = urllib.parse.quote(alt_query)
                url = f"https://apibay.org/q.php?q={encoded}&cat="
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                time.sleep(1)  # avoid rate limiting
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                    for r in (data or [])[:15]:
                        if r.get("name") and r.get("info_hash") and r.get("name") != "No results returned":
                            magnet = f"magnet:?xt=urn:btih:{r['info_hash']}&dn={urllib.parse.quote(r['name'])}"
                            results.append({
                                "title": r["name"],
                                "magnet": magnet,
                                "size": int(r.get("size", 0)),
                                "seeders": int(r.get("seeders", 0)),
                                "source": "public",
                            })
                if results:
                    break
            except Exception:
                pass

    # ── Public fallback #2: solidtorrents ──
    if not results:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://solidtorrents.to/api/v1/search?q={encoded}&skip=0&take=10&sort=seeders"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            time.sleep(1)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                for r in (data.get("results") or [])[:15]:
                    ih = r.get("infohash")
                    title = r.get("title", "")
                    if ih:
                        magnet = f"magnet:?xt=urn:btih:{ih}&dn={urllib.parse.quote(title)}"
                        results.append({
                            "title": title,
                            "magnet": magnet,
                            "size": r.get("size", 0),
                            "seeders": r.get("seeders", 0),
                            "source": "public",
                        })
        except Exception:
            pass

    return sorted(results, key=lambda r: r.get("seeders", 0), reverse=True)


def torbox_post(endpoint, form_data):
    """Make a POST request to TorBox API (multipart/form-data)."""
    api_key = get_torbox_key()
    url = f"{TORBOX_API}{endpoint}"

    boundary = "----tv90boundary"
    body = b""
    for key, value in form_data.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += str(value).encode() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"success": False, "error": str(e.code), "detail": body}
    except Exception as e:
        return {"success": False, "error": "CONNECTION_ERROR", "detail": str(e)}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_api_keys():
    return load_json(APIKEY_PATH)


def get_torbox_key():
    keys = get_api_keys()
    return keys.get("torbox_api_key", "")


def get_tvdb_key():
    keys = get_api_keys()
    return keys.get("tvdb_api_key", "")


def load_config():
    return load_json(CONFIG_PATH)


def save_config(config):
    save_json(CONFIG_PATH, config)


def torbox_request(endpoint, params=None):
    api_key = get_torbox_key()
    url = f"{TORBOX_API}{endpoint}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    req.add_header("Accept-Language", "en-US,en;q=0.9")
    req.add_header("Referer", "https://torbox.app/")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"success": False, "error": str(e.code), "detail": body}
    except Exception as e:
        return {"success": False, "error": "CONNECTION_ERROR", "detail": str(e)}


def tvdb_login():
    """Get a TVDB auth token, caching it for up to 30 days."""
    global _tvdb_token, _tvdb_token_expiry

    if _tvdb_token and time.time() < _tvdb_token_expiry:
        return _tvdb_token

    api_key = get_tvdb_key()
    if not api_key:
        return None

    body = json.dumps({"apikey": api_key}).encode()
    req = urllib.request.Request(
        f"{TVDB_API}/login",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("data", {}).get("token")
            if token:
                _tvdb_token = token
                _tvdb_token_expiry = time.time() + (30 * 24 * 3600)  # 30 days
                return token
    except Exception:
        pass
    return None


def tvdb_request(endpoint, params=None):
    token = tvdb_login()
    if not token:
        return {"success": False, "detail": "No TVDB API key configured or login failed"}

    url = f"{TVDB_API}{endpoint}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"success": False, "error": str(e.code), "detail": body}
    except Exception as e:
        return {"success": False, "error": "CONNECTION_ERROR", "detail": str(e)}


# ═══════════════════════════════════════════
#  TV Schedule System
# ═══════════════════════════════════════════

def load_schedule():
    return load_json(SCHEDULE_PATH)


def save_schedule(sched):
    save_json(SCHEDULE_PATH, sched)


def ensure_schedule(channel_idx, channels):
    """Ensure a channel has a schedule. Generate one if missing or depleted."""
    sched = load_schedule()
    key = str(channel_idx)
    now = time.time()

    if key not in sched or not sched[key].get("episodes"):
        return generate_schedule(channel_idx, channels, sched)

    # Check if we're running low on episodes (< 3 remaining in the future)
    episodes = sched[key]["episodes"]
    future_eps = [e for e in episodes if e["ends_at"] > now]
    if len(future_eps) < 3:
        return generate_schedule(channel_idx, channels, sched)

    return sched


def generate_schedule(channel_idx, channels, existing_sched=None):
    """Generate a TV schedule for a channel: random episodes from ALL shows."""
    if existing_sched is None:
        existing_sched = load_schedule()

    try:
        ch = channels[int(channel_idx)]
    except (IndexError, ValueError):
        return existing_sched

    shows = ch.get("shows", [])
    # Backward compat: if channel has old single tvdb_id, wrap it
    if not shows and ch.get("tvdb_id"):
        shows = [{"tvdb_id": ch["tvdb_id"], "name": ch.get("name", "")}]

    if not shows:
        return existing_sched

    # Fetch episodes from ALL shows
    all_episodes = []
    for show in shows:
        tvdb_id = show.get("tvdb_id")
        show_name = show.get("name", "")
        if not tvdb_id:
            continue
        eps_data = tvdb_request(f"/series/{tvdb_id}/episodes/default", {"page": 0})
        if eps_data.get("data"):
            for ep in eps_data["data"].get("episodes", []):
                ep["_show_name"] = show_name
                ep["_show_tvdb_id"] = tvdb_id
                all_episodes.append(ep)

    if not all_episodes:
        return existing_sched

    # Filter by season if configured
    season_filter = ch.get("season_filter")
    if season_filter is not None:
        all_episodes = [e for e in all_episodes if e.get("seasonNumber") == season_filter]
        if not all_episodes:
            return existing_sched

    key = str(channel_idx)
    old_eps = (existing_sched.get(key, {}).get("episodes") or [])
    episode_duration = existing_sched.get(key, {}).get("episode_duration", DEFAULT_EPISODE_DURATION)

    # Always start from now — old episodes are only used to prevent repeats
    start_time = time.time()

    # Shuffle all episodes from all shows together (avoid recent repeats)
    recent_names = {e.get("name") for e in old_eps[-20:]}
    fresh_eps = [e for e in all_episodes if e.get("name") not in recent_names]
    if len(fresh_eps) < 5:
        fresh_eps = all_episodes

    random.shuffle(fresh_eps)
    schedule_eps = fresh_eps[:50]

    new_episodes = []
    t = start_time
    for ep in schedule_eps:
        new_episodes.append({
            "name": ep.get("name") or f"Episode {ep.get('number')}",
            "season": ep.get("seasonNumber", 0),
            "episode_number": ep.get("number", 0),
            "overview": ep.get("overview", ""),
            "show_name": ep.get("_show_name", ""),
            "show_tvdb_id": ep.get("_show_tvdb_id"),
            "starts_at": t,
            "ends_at": t + episode_duration,
            "duration": episode_duration,
        })
        t += episode_duration

    existing_sched[key] = {
        "channel_name": ch.get("name", ""),
        "channel_number": ch.get("number", 0),
        "episodes": new_episodes,
        "episode_duration": episode_duration,
        "generated_at": time.time(),
    }

    save_schedule(existing_sched)

    # Auto pre-cache in background
    try:
        config = load_config()
        channels_list = config.get("channels", [])
        import threading
        t = threading.Thread(target=precache_episodes, args=(channel_idx, channels_list, 3), daemon=True)
        t.start()
    except Exception:
        pass

    return existing_sched


def get_current_episode(channel_idx):
    """Get the currently-airing episode for a channel, with seek offset."""
    sched = load_schedule()
    key = str(channel_idx)
    channel_sched = sched.get(key, {})
    episodes = channel_sched.get("episodes", [])
    now = time.time()

    for ep in episodes:
        if ep["starts_at"] <= now < ep["ends_at"]:
            seek_to = int(now - ep["starts_at"])
            return {
                "episode": ep,
                "seek_to": seek_to,
                "is_live": True,
            }

    # No current episode — find the next one
    for ep in episodes:
        if ep["starts_at"] > now:
            return {
                "episode": ep,
                "seek_to": 0,
                "is_live": False,
                "starts_in": int(ep["starts_at"] - now),
            }

    return None


def get_upcoming(channel_idx, count=5):
    """Get the next N upcoming episodes for a channel."""
    sched = load_schedule()
    key = str(channel_idx)
    episodes = sched.get(key, {}).get("episodes", [])
    now = time.time()

    upcoming = [e for e in episodes if e["starts_at"] > now]
    return upcoming[:count]


def precache_episodes(channel_idx, channels, count=5):
    """Pre-cache upcoming episodes: find torrents and inject into TorBox ahead of time."""
    sched = load_schedule()
    key = str(channel_idx)
    episodes = sched.get(key, {}).get("episodes", [])
    if not episodes:
        return {"success": False, "detail": "No schedule"}

    try:
        ch = channels[int(channel_idx)]
    except (IndexError, ValueError):
        return {"success": False, "detail": "Invalid channel"}

    now = time.time()
    keys = get_api_keys()
    jackett_url = keys.get("jackett_url", "")
    jackett_key = keys.get("jackett_api_key", "")

    # Get TorBox library once
    tb_data = torbox_request("/torrents/mylist", {"limit": 500})
    all_torrents = []
    if tb_data.get("success"):
        raw = tb_data.get("data", [])
        all_torrents = raw if isinstance(raw, list) else raw.get("torrents", [])

    cached = 0
    total = 0
    changed = False

    for ep in episodes:
        # Skip past episodes and already-cached ones
        if ep["ends_at"] < now:
            continue
        if ep.get("torrent_id"):
            cached += 1
            continue

        total += 1
        if total > count:
            break

        # Try TorBox library first
        ep_show = ep.get("show_name") or ch.get("name", "")
        match = find_torbox_match(all_torrents, ep_show, ep["season"], ep["episode_number"])
        if match:
            ep["torrent_id"] = match["id"]
            ep["precached"] = True
            cached += 1
            changed = True
            continue

        # External search + inject
        search_queries = [
            f"{ep_show} S{ep['season']:02d}E{ep['episode_number']:02d}",
            f"{ep_show} {ep['season']}x{ep['episode_number']:02d}",
            f"{ep_show}",
        ]
        for q in search_queries:
            results = search_external(q, jackett_url, jackett_key)
            if not results:
                continue
            for best in results[:3]:
                # Try cached-only first
                inject = torbox_post("/torrents/createtorrent", {
                    "magnet": best["magnet"],
                    "name": best["title"],
                    "add_only_if_cached": True,
                    "as_queued": False,
                })
                tid = None
                if inject.get("success") and inject.get("data"):
                    tid = inject["data"].get("id") or inject["data"].get("torrent_id")
                if tid:
                    ep["torrent_id"] = tid
                    ep["precached"] = True
                    cached += 1
                    changed = True
                    break

                # Not cached — full download (will be ready by air time)
                inject = torbox_post("/torrents/createtorrent", {
                    "magnet": best["magnet"],
                    "name": best["title"],
                    "add_only_if_cached": False,
                    "as_queued": False,
                })
                if inject.get("success") and inject.get("data"):
                    tid = inject["data"].get("id") or inject["data"].get("torrent_id")
                if tid:
                    ep["torrent_id"] = tid
                    ep["precached"] = True
                    cached += 1
                    changed = True
                    break
            if ep.get("torrent_id"):
                break

    if changed:
        save_schedule(sched)

    return {
        "success": True,
        "cached": cached,
        "attempted": total,
        "detail": f"Pre-cached {cached} episodes",
    }


class TV90Handler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write(f"[tv90] {args[0]}\n")

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    # ─── GET ───

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        # ── API: channels (public, no key exposed) ──
        if path == "/api/channels":
            config = load_config()
            keys = get_api_keys()
            self._send_json({
                "success": True,
                "channels": config.get("channels", []),
                "has_torbox_key": bool(keys.get("torbox_api_key")),
                "has_tvdb_key": bool(keys.get("tvdb_api_key")),
            })
            return

        # ── API: admin config ──
        if path == "/api/admin/config":
            config = load_config()
            keys = get_api_keys()
            self._send_json({
                "torbox_api_key": keys.get("torbox_api_key", ""),
                "tvdb_api_key": keys.get("tvdb_api_key", ""),
                "jackett_url": keys.get("jackett_url", ""),
                "jackett_api_key": keys.get("jackett_api_key", ""),
                "channels": config.get("channels", []),
            })
            return

        # ── API: play ──
        if path == "/api/play":
            torrent_id = params.get("torrent_id")
            file_id = params.get("file_id", "0")

            if not torrent_id:
                self._send_json({"success": False, "detail": "Missing torrent_id"}, 400)
                return

            tid = int(torrent_id)
            fid = int(file_id)

            stream = torbox_request(
                "/stream/createstream",
                {"id": tid, "file_id": fid, "type": "torrent"}
            )

            # If file is not a video, find the first video file and retry
            if not stream.get("success") and "not a video" in str(stream.get("detail", "")).lower():
                # Look up torrent to find video files
                info = torbox_request("/torrents/mylist", {"id": tid})
                files = []
                if info.get("success") and info.get("data"):
                    d = info["data"]
                    files = (d if isinstance(d, list) else [d])
                    if files:
                        files = files[0].get("files", [])

                video_fid = None
                for f in files:
                    mt = (f.get("mimetype") or "").lower()
                    if mt.startswith("video/"):
                        video_fid = f.get("id", 0)
                        break

                if video_fid is not None and video_fid != fid:
                    stream = torbox_request(
                        "/stream/createstream",
                        {"id": tid, "file_id": video_fid, "type": "torrent"}
                    )

            if not stream.get("success"):
                self._send_json(stream, 500)
                return

            sd = stream.get("data", {})
            hls_url = sd.get("hls_url")
            file_token = sd.get("file_token") or sd.get("token") or sd.get("user_token")
            presigned_token = sd.get("presigned_token")

            # If TorBox already gave us an HLS URL, use it directly
            if hls_url:
                self._send_json({"success": True, "data": {"stream_url": hls_url}})
                return

            if not file_token or not presigned_token:
                self._send_json({"success": False, "detail": "Missing tokens in stream response", "data": sd}, 500)
                return

            stream_info = torbox_request(
                "/stream/getstreamdata",
                {"token": file_token, "presigned_token": presigned_token}
            )
            self._send_json(stream_info)
            return

        # ── API: random episode ──
        if path == "/api/random-episode":
            channel_idx = params.get("channel_idx")
            if channel_idx is None:
                self._send_json({"success": False, "detail": "Missing channel_idx"}, 400)
                return

            config = load_config()
            channels = config.get("channels", [])
            try:
                ch = channels[int(channel_idx)]
            except (IndexError, ValueError):
                self._send_json({"success": False, "detail": "Invalid channel"}, 400)
                return

            tvdb_id = ch.get("tvdb_id")
            # For multi-show channels, find the right show
            if not tvdb_id:
                shows = ch.get("shows", [])
                if shows:
                    req_show = params.get("show_name")
                    if req_show:
                        match = next((s for s in shows if s.get("name") == req_show), None)
                        tvdb_id = match["tvdb_id"] if match else shows[0]["tvdb_id"]
                    else:
                        tvdb_id = shows[0]["tvdb_id"]
            if not tvdb_id:
                self._send_json({"success": False, "detail": "No TVDB ID for this channel"}, 400)
                return

            show_name = params.get("show_name") or ch.get("name", "")

            # Fetch episodes from TVDB
            eps_data = tvdb_request(f"/series/{tvdb_id}/episodes/default", {"page": 0})
            if not eps_data.get("data"):
                self._send_json({"success": False, "detail": "Failed to fetch episodes from TVDB"}, 500)
                return

            episodes = eps_data["data"].get("episodes", [])
            if not episodes:
                self._send_json({"success": False, "detail": "No episodes found on TVDB"}, 400)
                return

            # Filter by season if configured
            season_filter = ch.get("season_filter")
            if season_filter is not None:
                episodes = [e for e in episodes if e.get("seasonNumber") == season_filter]
                if not episodes:
                    self._send_json({"success": False, "detail": f"No episodes in season {season_filter}"}, 400)
                    return

            # Pick episode: use specific season/episode if provided, otherwise random
            req_season = params.get("season")
            req_episode = params.get("episode_number")

            if req_season is not None and req_episode is not None:
                # Find the specific episode requested
                ep = None
                for e in episodes:
                    if (e.get("seasonNumber") == int(req_season) and
                        e.get("number") == int(req_episode)):
                        ep = e
                        break
                if not ep:
                    self._send_json({
                        "success": False,
                        "detail": f"Episode S{req_season}E{req_episode} not found",
                    }, 400)
                    return
            else:
                ep = random.choice(episodes)
            ep_name = ep.get("name") or f"Episode {ep.get('number')}"
            season_num = ep.get("seasonNumber", 0)
            ep_num = ep.get("number", 0)

            # Search TorBox for matching content
            torrents_data = torbox_request("/torrents/mylist", {"limit": 500})
            all_torrents = []
            if torrents_data.get("success"):
                raw = torrents_data.get("data", [])
                all_torrents = raw if isinstance(raw, list) else raw.get("torrents", [])

            match = find_torbox_match(all_torrents, show_name, season_num, ep_num)

            # If not in library, try external search + inject
            if not match:
                keys = get_api_keys()
                jackett_url = keys.get("jackett_url", "")
                jackett_key = keys.get("jackett_api_key", "")

                # Try multiple query formats, from most specific to broadest
                search_queries = [
                    f"{show_name} S{season_num:02d}E{ep_num:02d}",
                    f"{show_name} {season_num}x{ep_num:02d}",
                    f"{show_name} Season {season_num}",
                    show_name,
                ]

                search_errors = []
                for search_q in search_queries:
                    results = search_external(search_q, jackett_url, jackett_key)
                    if results:
                        # Try each result until one injects successfully
                        for best in results[:5]:
                            # Try cached-only first for instant play
                            inject = torbox_post("/torrents/createtorrent", {
                                "magnet": best["magnet"],
                                "name": best["title"],
                                "add_only_if_cached": True,
                                "as_queued": False,
                            })
                            tid = None
                            if inject.get("success") and inject.get("data"):
                                tid = inject["data"].get("id") or inject["data"].get("torrent_id")
                            if tid:
                                match = {"id": tid, "name": best["title"]}
                                break

                            # Not cached — try full download
                            inject = torbox_post("/torrents/createtorrent", {
                                "magnet": best["magnet"],
                                "name": best["title"],
                                "add_only_if_cached": False,
                                "as_queued": False,
                            })
                            if inject.get("success") and inject.get("data"):
                                tid = inject["data"].get("id") or inject["data"].get("torrent_id")
                            if tid:
                                match = {"id": tid, "name": best["title"]}
                                break

                            search_errors.append(inject.get("detail", "injection failed"))
                        if match:
                            break
                    else:
                        search_errors.append(f"no results for '{search_q}'")
                    if match:
                        break

                if not match and search_errors:
                    err_detail = "; ".join(search_errors[:3])
                else:
                    err_detail = f"No TorBox content found for '{show_name}'"
            else:
                err_detail = f"No TorBox content found for '{show_name}'"

            if not match:
                self._send_json({
                    "success": False,
                    "detail": err_detail,
                    "episode": {"name": ep_name, "season": season_num, "episode_number": ep_num},
                }, 400)
                return

            self._send_json({
                "success": True,
                "episode": {
                    "name": ep_name,
                    "season": season_num,
                    "episode_number": ep_num,
                    "overview": ep.get("overview", ""),
                },
                "torrent_id": match["id"],
                "file_id": 0,
                "channel_name": ch.get("name", ""),
                "channel_number": ch.get("number", 0),
                "matched_torrent_name": match.get("name", ""),
            })
            return

        # ── API: Schedule ──
        if path == "/api/schedule":
            config = load_config()
            channels = config.get("channels", [])
            sched = load_schedule()
            channel_idx = params.get("channel_idx")

            if channel_idx is not None:
                # Return schedule for one channel + current/upcoming
                ensure_schedule(int(channel_idx), channels)
                sched = load_schedule()
                key = str(channel_idx)
                ch_sched = sched.get(key, {})
                current = get_current_episode(int(channel_idx))
                upcoming = get_upcoming(int(channel_idx), 10)
                self._send_json({
                    "success": True,
                    "channel": ch_sched,
                    "current": current,
                    "upcoming": upcoming,
                })
            else:
                # Return all schedules summary
                result = {}
                for i, ch in enumerate(channels):
                    if ch.get("shows") or ch.get("tvdb_id"):
                        ensure_schedule(i, channels)
                sched = load_schedule()
                for key, val in sched.items():
                    current = get_current_episode(int(key))
                    result[key] = {
                        "channel_name": val.get("channel_name", ""),
                        "channel_number": val.get("channel_number", 0),
                        "current": current,
                        "episode_count": len(val.get("episodes", [])),
                    }
                self._send_json({"success": True, "channels": result})
            return

        if path == "/api/schedule/generate":
            channel_idx = params.get("channel_idx")
            if channel_idx is None:
                self._send_json({"success": False, "detail": "Missing channel_idx"}, 400)
                return
            config = load_config()
            channels = config.get("channels", [])
            sched = generate_schedule(int(channel_idx), channels)
            key = str(channel_idx)
            self._send_json({
                "success": True,
                "detail": f"Generated {len(sched.get(key,{}).get('episodes',[]))} episodes",
                "channel": sched.get(key, {}),
            })
            return

        if path == "/api/schedule/precache":
            channel_idx = params.get("channel_idx")
            if channel_idx is None:
                self._send_json({"success": False, "detail": "Missing channel_idx"}, 400)
                return
            config = load_config()
            channels = config.get("channels", [])
            count = int(params.get("count", 10))
            # Run in background thread to not block response
            import threading
            def _run():
                precache_episodes(int(channel_idx), channels, count=count)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            self._send_json({"success": True, "detail": f"Pre-caching {count} episodes in background..."})
            return

        # ── API: TV Guide (all channels at once) ──
        if path == "/api/guide":
            config = load_config()
            channels = config.get("channels", [])
            result = []
            for i, ch in enumerate(channels):
                if not (ch.get("shows") or ch.get("tvdb_id")):
                    continue
                ensure_schedule(i, channels)
                sched = load_schedule()
                key = str(i)
                ch_sched = sched.get(key, {})
                current = get_current_episode(i)
                upcoming = get_upcoming(i, 5)
                result.append({
                    "channel_idx": i,
                    "channel_name": ch.get("name", ""),
                    "channel_number": ch.get("number", 0),
                    "channel_logo": ch.get("logo", ""),
                    "current": current,
                    "upcoming": upcoming,
                })
            self._send_json({"success": True, "channels": result, "server_time": time.time()})
            return

        # ── API: TorBox torrents ──
        if path == "/api/torrents":
            data = torbox_request("/torrents/mylist", {
                "bypass_cache": params.get("bypass_cache", "false"),
                "offset": params.get("offset", "0"),
                "limit": params.get("limit", "200")
            })
            self._send_json(data)
            return

        # ── API: TVDB search ──
        if path == "/api/tvdb/search":
            query = params.get("query", "")
            if not query:
                self._send_json({"success": False, "detail": "Missing query"}, 400)
                return
            data = tvdb_request("/search", {"query": query, "type": "series"})
            self._send_json(data)
            return

        # ── API: TVDB series info ──
        if path.startswith("/api/tvdb/series/"):
            series_id = path.split("/")[-1]
            if not series_id.isdigit():
                self._send_json({"success": False, "detail": "Invalid series ID"}, 400)
                return
            data = tvdb_request(f"/series/{series_id}")
            self._send_json(data)
            return

        # ── API: TVDB episodes ──
        if path.startswith("/api/tvdb/episodes/"):
            series_id = path.split("/")[-1]
            if not series_id.isdigit():
                self._send_json({"success": False, "detail": "Invalid series ID"}, 400)
                return
            data = tvdb_request(f"/series/{series_id}/episodes/default", {"page": 0})
            self._send_json(data)
            return

        # ── Serve pages ──
        if path == "/" or path == "":
            html = (BASE_DIR / "index.html").read_text() if (BASE_DIR / "index.html").exists() else "Not found"
            self._send_html(html)
            return

        if path == "/admin":
            html = (BASE_DIR / "admin.html").read_text() if (BASE_DIR / "admin.html").exists() else "Not found"
            self._send_html(html)
            return

        # Fall back to file serving
        super().do_GET()

    # ─── POST ───

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"success": False, "detail": "Invalid JSON"}, 400)
            return

        # ── Save full config (channels) ──
        if path == "/api/admin/save":
            channels = data.get("channels", [])
            config = load_config()
            config["channels"] = channels
            save_config(config)
            self._send_json({"success": True, "detail": "Config saved"})
            return

        # ── Save API keys ──
        if path == "/api/admin/save-keys":
            keys = get_api_keys()
            for field in ["torbox_api_key", "tvdb_api_key", "jackett_url", "jackett_api_key"]:
                if field in data:
                    keys[field] = data[field]
            if "tvdb_api_key" in data:
                global _tvdb_token
                _tvdb_token = None
            save_json(APIKEY_PATH, keys)
            self._send_json({"success": True, "detail": "API keys saved"})
            return

        self._send_json({"success": False, "detail": "Not found"}, 404)

    # ─── OPTIONS (CORS) ───

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    server = http.server.HTTPServer(("0.0.0.0", port), TV90Handler)
    print(f"📺 tv90 running at http://localhost:{port}")
    print(f"   Admin:  http://localhost:{port}/admin")
    print(f"   Config: {CONFIG_PATH}")
    print(f"   Keys:   {APIKEY_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n📴 tv90 signing off...")
        server.shutdown()


if __name__ == "__main__":
    main()
