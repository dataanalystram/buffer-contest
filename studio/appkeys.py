"""App-level research keys: the keys belong to HuhQueue, not to each person (judges never sign in to Product Hunt or X).

Product Hunt
    PH_CLIENT_ID + PH_CLIENT_SECRET (Render env) -> an app token from Product Hunt's OAuth client_credentials grant
    (POST https://api.producthunt.com/v2/oauth/token), cached in memory until it expires. Today's top launches are read
    with it ONCE for everyone and cached globally (PH_CACHE_TTL, 20 min); live fetches are rate-limited globally
    (PH_MIN_INTERVAL). Without env keys, or when the API errors, the public feed https://www.producthunt.com/feed is read
    instead (same cache). A person's own developer token (Settings -> Integrations) is an optional override.
    Product Hunt's API is free for NON-COMMERCIAL use by default: fine for the contest; ask Product Hunt before a
    commercial launch (docs/SOURCES_AGENT.md).

X
    No key needed: studio/xreader.py reads a linked public post for free (oEmbed, the syndication endpoint react-tweet
    uses, FxTwitter), cached and rate-limited globally. X_BEARER_TOKEN (Render env) is only an OPTIONAL override for
    media when the free layers found none; a person's own bearer token is likewise optional.

Key values never leave this module: nothing here logs them, returns them to a client, or puts them in an error message.
Every call goes through netguard (pinned host, no redirects, bytes capped). SECURITY_RULES 1-2.
"""
from __future__ import annotations

import os
import threading
import time

import httpx

from . import netguard

UA = {"User-Agent": "Mozilla/5.0 (HuhQueue; +https://huhqueue-studio.onrender.com)"}
PH_TOKEN_URL = "https://api.producthunt.com/v2/oauth/token"
PH_GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
PH_FEED = "https://www.producthunt.com/feed"
PH_CACHE_TTL = int(os.environ.get("HQ_PH_CACHE_TTL", "1200"))        # 20 min: one shared read for everyone
PH_MIN_INTERVAL = int(os.environ.get("HQ_PH_MIN_INTERVAL", "120"))   # at most one live Product Hunt fetch per 2 min
X_CACHE_TTL = 3600
X_WINDOW, X_PER_WINDOW = 900, int(os.environ.get("HQ_X_APP_PER_15MIN", "30"))  # shared X lookups per 15 min
X_CACHE_MAX = 500

_lock = threading.Lock()
_ph_token: dict = {}            # {"token", "exp"}
_ph_cache: dict = {}            # {"items", "at", "mode"}
_ph_last_try = [0.0]
_x_cache: dict[str, tuple[float, list]] = {}
_x_hits: list[float] = []


def reset() -> None:
    """Tests: forget every cached token and result."""
    with _lock:
        _ph_token.clear(); _ph_cache.clear(); _ph_last_try[0] = 0.0; _x_cache.clear(); _x_hits.clear()


# ================================================================================================ Product Hunt
def ph_configured() -> bool:
    return bool(os.environ.get("PH_CLIENT_ID") and os.environ.get("PH_CLIENT_SECRET"))


def ph_app_token(http=None) -> str:
    """The app's client_credentials token, cached until shortly before it expires. '' when not configured or refused."""
    if not ph_configured():
        return ""
    now = time.time()
    if _ph_token.get("token") and _ph_token.get("exp", 0) > now + 60:
        return _ph_token["token"]
    try:
        r = netguard.safe_post(PH_TOKEN_URL, http=http, json={"client_id": os.environ["PH_CLIENT_ID"], "client_secret": os.environ["PH_CLIENT_SECRET"],
                                                             "grant_type": "client_credentials"},
                               headers={**UA, "Accept": "application/json"}, timeout=15, allowed_hosts={"api.producthunt.com"}, limit=100_000)
    except (netguard.FetchError, httpx.HTTPError):
        return ""
    if r.status_code != 200:
        print(f"producthunt: app token refused (HTTP {r.status_code})", flush=True)  # status only, never a key
        return ""
    try:
        j = r.json()
    except ValueError:
        return ""
    tok = str(j.get("access_token") or "")
    if not tok:
        return ""
    ttl = j.get("expires_in")
    _ph_token.update({"token": tok, "exp": now + (float(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else 7 * 86400)})
    return tok


def _ph_api(token: str, http, query: str) -> list[dict]:
    from .sources_agent import SourceError, parse_ph
    r = netguard.safe_post(PH_GRAPHQL, http=http, json={"query": query}, headers={**UA, "Authorization": f"Bearer {token}", "Accept": "application/json"},
                           timeout=15, allowed_hosts={"api.producthunt.com"}, limit=2_000_000)
    if r.status_code in (401, 403):
        _ph_token.clear()  # a revoked/expired app token: fetch a new one next time
    if r.status_code != 200:
        raise SourceError(f"Product Hunt answered HTTP {r.status_code}.")
    try:
        return parse_ph(r.json())
    except ValueError:
        raise SourceError("Product Hunt didn't return JSON.")


def _ph_feed(http) -> list[dict]:
    from . import radar
    from .sources_agent import _item
    r = netguard.safe_get(PH_FEED, http=http, headers=UA, limit=radar.MAX_BYTES, timeout=12, https_only=True)
    if r.status_code != 200:
        raise netguard.FetchError(f"Product Hunt's feed answered HTTP {r.status_code}.")
    feed = radar.parse(r.content, PH_FEED)
    return [_item("ph", "Product Hunt", s["title"], s["url"], s.get("summary", ""), s.get("published") or 0.0, image=s.get("image", ""))
            for s in feed["stories"] if str(s.get("url", "")).startswith("https://")][:20]


def ph_shared(http, query: str) -> tuple[list[dict], str]:
    """Today's launches for everyone: (items, mode) with mode 'api' (app token) or 'feed' (public RSS). Cached globally."""
    from .sources_agent import SourceError
    now = time.time()
    with _lock:
        if _ph_cache.get("items") is not None and now - _ph_cache["at"] < PH_CACHE_TTL:
            return list(_ph_cache["items"]), _ph_cache["mode"]
        if now - _ph_last_try[0] < PH_MIN_INTERVAL:  # global rate limit: serve the last result (even stale) or nothing
            if _ph_cache.get("items") is not None:
                return list(_ph_cache["items"]), _ph_cache["mode"]
            raise SourceError("Product Hunt was just checked; try again in a couple of minutes.")
        _ph_last_try[0] = now
        items, mode = None, ""
        tok = ph_app_token(http)
        if tok:
            try:
                items, mode = _ph_api(tok, http, query), "api"
            except (SourceError, netguard.FetchError, httpx.HTTPError):
                items = None
        if items is None:
            try:
                items, mode = _ph_feed(http), "feed"
            except (netguard.FetchError, httpx.HTTPError, ValueError, KeyError):
                if _ph_cache.get("items") is not None:
                    return list(_ph_cache["items"]), _ph_cache["mode"]
                raise SourceError("Product Hunt couldn't be read right now.")
        _ph_cache.update({"items": items, "at": now, "mode": mode})
        return list(items), mode


# ================================================================================================ X
def x_app_bearer() -> str:
    return os.environ.get("X_BEARER_TOKEN", "").strip()


def x_media_shared(status_id: str, http=None) -> list:
    """A post's media through the app's bearer: cached per post for an hour, globally rate-limited."""
    from . import reader
    now = time.time()
    with _lock:
        hit = _x_cache.get(status_id)
        if hit and now - hit[0] < X_CACHE_TTL:
            return list(hit[1])
        recent = [t for t in _x_hits if t > now - X_WINDOW]
        _x_hits[:] = recent
        if len(recent) >= X_PER_WINDOW:
            raise reader.ReadError("HuhQueue's shared X lookups are busy right now; upload the clip, or try again in a few minutes.")
        _x_hits.append(now)
    try:
        got = reader.x_media(status_id, x_app_bearer(), http)
    except reader.ReadError:
        raise reader.ReadError("HuhQueue's X lookup didn't work for this post; upload the clip, or try again later.")
    with _lock:
        if len(_x_cache) >= X_CACHE_MAX:
            _x_cache.pop(next(iter(_x_cache)))
        _x_cache[status_id] = (now, got)
    return list(got)


# ================================================================================================ status (no values)
def status() -> dict:
    return {"ph": {"app": ph_configured(), "mode": _ph_cache.get("mode") or ("api" if ph_configured() else "feed")},
            "x": {"app": bool(x_app_bearer()), "free": True}}
