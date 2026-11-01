"""The free X reader: one public post a person linked, read without an X API key (shared research layer).

The cascade, each layer behind netguard (pinned host, no redirects, bytes capped), results cached globally for 20 minutes
per post and live lookups rate-limited globally:

  1. oEmbed       publish.x.com/oembed (X's official, documented embed): the author and the post's words.
  2. syndication  cdn.syndication.twimg.com/tweet-result?id=…&token=… (the public endpoint X's own embeds use, as read by
                  Vercel's MIT-licensed react-tweet; the token is computed exactly like react-tweet's getToken):
                  created_at, photos, video variants + poster, quoted post.
  3. FxTwitter    api.fxtwitter.com/status/<id> (the MIT-licensed FixTweet/FxTwitter public API): full text of long
                  posts and media URLs when the syndication answer has none.
  4. yt-dlp       the render worker downloads a post's video with yt-dlp's Twitter extractor only (guest token;
                  studio/media/download.py), using the post URL kept on each video as ``source_page``.

An optional X API bearer (the person's own key, or X_BEARER_TOKEN on the server) still overrides layers 2-3 for media.

Layers 2-3 are unofficial public endpoints: they can change or break without notice, and X's Terms restrict scraping.
HuhQueue only reads the single public post a person links, on demand, and never crawls, searches or bulk-collects X.
Nothing here logs a key or a post body; only a layer's HTTP status is ever printed (by the smoke script).
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from urllib.parse import urlencode

import httpx

from . import netguard

UA = {"User-Agent": "Mozilla/5.0 (HuhQueue link reader; +https://huhqueue-studio.onrender.com)"}
OEMBED = "https://publish.x.com/oembed"
SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
FXTWITTER = "https://api.fxtwitter.com/status/"
CACHE_TTL = int(os.environ.get("HQ_X_CACHE_TTL", "1200"))          # 20 min per post, shared by everyone
WINDOW, PER_WINDOW = 900, int(os.environ.get("HQ_X_LOOKUPS_PER_15MIN", "60"))
CACHE_MAX = 500
MAX_VIDEO_BITRATE = 2_200_000
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
_hits: list[float] = []


class XReadError(ValueError):
    pass


def reset() -> None:
    with _lock:
        _cache.clear(); _hits.clear()


# ================================================================================================ react-tweet's token
def _js_radix(value: float, radix: int = 36) -> str:
    """JavaScript's Number.prototype.toString(radix) for a positive double (a port of V8's DoubleToRadixCString)."""
    integer = math.floor(value)
    fraction = value - integer
    delta = max(0.5 * (math.nextafter(value, math.inf) - value), math.nextafter(0.0, 1.0))
    frac: list[int] = []
    if fraction >= delta:
        while True:
            fraction *= radix
            delta *= radix
            d = int(fraction)
            frac.append(d)
            fraction -= d
            if fraction > 0.5 or (fraction == 0.5 and (d & 1)):
                if fraction + delta > 1:  # round up, carrying into the integer part when needed
                    i = len(frac) - 1
                    while True:
                        if i < 0:
                            integer += 1
                            break
                        if frac[i] + 1 < radix:
                            frac[i] += 1
                            frac = frac[:i + 1]
                            break
                        frac = frac[:i]
                        i -= 1
                    break
            if not fraction >= delta:
                break
    n, ip = int(integer), ""
    while True:
        ip = _B36[n % radix] + ip
        n //= radix
        if n == 0:
            break
    return ip + ("." + "".join(_B36[d] for d in frac) if frac else "")


def syndication_token(status_id: str) -> str:
    """react-tweet: ((Number(id) / 1e15) * Math.PI).toString(6 ** 2).replace(/(0+|\\.)/g, '')"""
    return re.sub(r"(0+|\.)", "", _js_radix((int(status_id) / 1e15) * math.pi))


# ================================================================================================ the layers
def _get(url: str, http, host: str) -> httpx.Response:
    try:
        return netguard.safe_get(url, http=http, headers={**UA, "Accept": "application/json"}, limit=1_500_000, timeout=12,
                                 https_only=True, max_hops=0)
    except (netguard.FetchError, httpx.HTTPError) as e:
        raise XReadError(f"{host} didn't answer ({e.__class__.__name__}).")


def _json(r: httpx.Response, host: str) -> dict:
    if r.status_code != 200:
        raise XReadError(f"{host} answered HTTP {r.status_code}.")
    try:
        j = r.json()
    except ValueError:
        raise XReadError(f"{host} didn't return JSON.")
    if not isinstance(j, dict):
        raise XReadError(f"{host} returned an unexpected answer.")
    return j


def _pick_mp4(variants: list[dict]) -> str:
    """The highest bit rate at or under ~2.2 Mbps (a minute-long clip stays well under the download cap)."""
    mp4 = [(v.get("bitrate") or v.get("bit_rate") or _res_rate(v.get("url") or v.get("src") or ""), v.get("url") or v.get("src"))
           for v in variants or [] if (v.get("content_type") or v.get("type") or v.get("container") or "").endswith("mp4")
           and (v.get("url") or v.get("src"))]
    if not mp4:
        return ""
    mp4.sort(key=lambda x: -x[0])
    return next((u for b, u in mp4 if b <= MAX_VIDEO_BITRATE), mp4[-1][1])


def _res_rate(url: str) -> int:
    """No bitrate given (react-tweet's video.variants): rank by the WxH in the URL."""
    m = re.search(r"/(\d{2,4})x(\d{2,4})/", url)
    return int(m.group(1)) * int(m.group(2)) * 2 if m else 0  # ~2 bits per pixel: 640x360 ≈ 0.46 Mbps, 1280x720 ≈ 1.8


def layer_oembed(handle: str, sid: str, http=None) -> dict:
    from . import reader
    url = f"https://x.com/{handle}/status/{sid}"
    r = _get(OEMBED + "?" + urlencode({"url": url, "omit_script": "1", "dnt": "true"}), http, "publish.x.com")
    j = _json(r, "publish.x.com")
    return {"layer": "oembed", **reader.parse_oembed(j)}


def parse_syndication(j: dict, post_url: str = "") -> dict:
    if not j.get("text") and not j.get("id_str"):
        raise XReadError("The syndication answer had no post (deleted, private or age-restricted).")
    u = j.get("user") or {}
    photos = [p.get("url") for p in j.get("photos") or [] if str(p.get("url", "")).startswith("https://")]
    videos = []
    for m in j.get("mediaDetails") or []:
        if m.get("type") in ("video", "animated_gif"):
            pick = _pick_mp4((m.get("video_info") or {}).get("variants") or [])
            if pick:
                videos.append({"url": pick, "poster": m.get("media_url_https", ""),
                               "duration": ((m.get("video_info") or {}).get("duration_millis") or 0) / 1000})
        elif m.get("type") == "photo" and m.get("media_url_https") and m["media_url_https"] not in photos:
            photos.append(m["media_url_https"])
    if not videos and j.get("video"):
        v = j["video"]
        pick = _pick_mp4(v.get("variants") or [])
        if pick:
            videos.append({"url": pick, "poster": v.get("poster", ""), "duration": (v.get("durationMs") or 0) / 1000})
    q = j.get("quoted_tweet") or None
    quoted = {"handle": (q.get("user") or {}).get("screen_name", ""), "text": q.get("text", ""),
              "url": f"https://x.com/{(q.get('user') or {}).get('screen_name', 'i')}/status/{q.get('id_str', '')}"} if q else None
    return {"layer": "syndication", "text": j.get("text", ""), "author_name": u.get("name", ""), "handle": u.get("screen_name", ""),
            "date": j.get("created_at", ""), "photos": photos, "videos": videos, "quoted": quoted,
            "long": bool(j.get("note_tweet")), "avatar": u.get("profile_image_url_https", "")}


def layer_syndication(sid: str, http=None) -> dict:
    r = _get(SYNDICATION + "?" + urlencode({"id": sid, "lang": "en", "token": syndication_token(sid)}), http, "cdn.syndication.twimg.com")
    return parse_syndication(_json(r, "cdn.syndication.twimg.com"))


def parse_fxtwitter(j: dict) -> dict:
    t = j.get("tweet") or {}
    if not t:
        raise XReadError(f"FxTwitter had no post (code {j.get('code')}).")
    media = t.get("media") or {}
    photos = [p.get("url") for p in media.get("photos") or [] if str(p.get("url", "")).startswith("https://")]
    videos = []
    for v in media.get("videos") or []:
        url = _pick_mp4(v.get("formats") or []) or (v.get("url") if str(v.get("url", "")).startswith("https://") else "")
        if url:
            videos.append({"url": url, "poster": v.get("thumbnail_url", ""), "duration": float(v.get("duration") or 0)})
    a = t.get("author") or {}
    q = t.get("quote") or None
    quoted = {"handle": (q.get("author") or {}).get("screen_name", ""), "text": q.get("text", ""), "url": q.get("url", "")} if q else None
    return {"layer": "fxtwitter", "text": t.get("text", ""), "author_name": a.get("name", ""), "handle": a.get("screen_name", ""),
            "date": t.get("created_at", ""), "photos": photos, "videos": videos, "quoted": quoted, "avatar": a.get("avatar_url", "")}


def layer_fxtwitter(sid: str, http=None) -> dict:
    r = _get(FXTWITTER + sid, http, "api.fxtwitter.com")
    return parse_fxtwitter(_json(r, "api.fxtwitter.com"))


# ================================================================================================ the cascade
def read(handle: str, sid: str, http=None) -> dict:
    """One post: {text, author_name, handle, date, photos, videos, quoted, has_media, layers{name: ok|error}}.
    Cached globally per post; live lookups rate-limited. Raises XReadError only when no layer could read it."""
    now = time.time()
    with _lock:
        hit = _cache.get(sid)
        if hit and now - hit[0] < CACHE_TTL:
            return dict(hit[1])
        _hits[:] = [t for t in _hits if t > now - WINDOW]
        if len(_hits) >= PER_WINDOW:
            raise XReadError("HuhQueue has read a lot of X posts in the last few minutes; try again shortly, or upload the clip.")
        _hits.append(now)
    layers, got = {}, {}
    for name, fn in (("oembed", lambda: layer_oembed(handle, sid, http)), ("syndication", lambda: layer_syndication(sid, http))):
        try:
            got[name] = fn()
            layers[name] = "ok"
        except (XReadError, KeyError, TypeError, ValueError) as e:
            layers[name] = str(e)[:120]
    syn = got.get("syndication") or {}
    want_fx = not syn or (syn.get("long")) or (not syn.get("videos") and not syn.get("photos") and (got.get("oembed") or {}).get("has_media"))
    if want_fx:
        try:
            got["fxtwitter"] = layer_fxtwitter(sid, http)
            layers["fxtwitter"] = "ok"
        except (XReadError, KeyError, TypeError, ValueError) as e:
            layers["fxtwitter"] = str(e)[:120]
    if not got:
        raise XReadError("X didn't share this post (it may be deleted, private or age-restricted).")
    oe, fx = got.get("oembed") or {}, got.get("fxtwitter") or {}
    texts = [t for t in (fx.get("text"), syn.get("text"), oe.get("text")) if t]
    first = lambda k: next((d.get(k) for d in (syn, fx, oe) if d.get(k)), "")  # noqa: E731
    media_src = syn if (syn.get("videos") or syn.get("photos")) else fx
    out = {"text": max(texts, key=len) if texts else "", "author_name": first("author_name"), "handle": first("handle") or handle,
           "date": first("date"), "photos": list(media_src.get("photos") or []), "videos": list(media_src.get("videos") or []),
           "quoted": syn.get("quoted") or fx.get("quoted"), "avatar": first("avatar"),
           "has_media": bool(oe.get("has_media") or media_src.get("photos") or media_src.get("videos")),
           "media_via": media_src.get("layer", "") if (media_src.get("photos") or media_src.get("videos")) else "", "layers": layers}
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[sid] = (now, out)
    return dict(out)
