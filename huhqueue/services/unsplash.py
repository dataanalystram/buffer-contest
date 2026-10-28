"""Unsplash photo picker: search free photos and copy one into the media library with its credit.

Uses the official API with your own free access key (Settings > App keys > Unsplash, or UNSPLASH_ACCESS_KEY). Following
Unsplash's API guidelines, importing a photo calls its `download_location` (counts the download) and the photographer
credit ("Photo by NAME on Unsplash") is kept as the media title and offered for the caption."""
from __future__ import annotations

import httpx

from ..models import Media
from . import media as media_svc

API = "https://api.unsplash.com"
UTM = "utm_source=huhqueue&utm_medium=referral"


class UnsplashError(media_svc.MediaError):
    pass


def _key(core) -> str:
    key = (core.settings.unsplash_access_key or "").strip()
    if not key:
        raise UnsplashError("Add your free Unsplash access key in Settings › App keys first (unsplash.com/developers)")
    return key


def _get(core, path: str, **params) -> dict:
    try:
        r = core.http.get(f"{API}{path}", params=params, headers={"Authorization": f"Client-ID {_key(core)}",
                                                                  "Accept-Version": "v1"}, timeout=20)
    except httpx.HTTPError as e:
        raise UnsplashError(f"Unsplash did not answer: {e.__class__.__name__}") from e
    if r.status_code == 401:
        raise UnsplashError("Unsplash rejected the access key - check it in Settings › App keys")
    if r.status_code == 403:
        raise UnsplashError("Unsplash rate limit reached (50 requests an hour for demo apps). Try again later")
    if r.status_code >= 400:
        raise UnsplashError(f"Unsplash error {r.status_code}")
    return r.json()


def _photo(p: dict) -> dict:
    u = p.get("user") or {}
    name = u.get("name") or u.get("username") or "Unknown"
    profile = ((u.get("links") or {}).get("html") or "https://unsplash.com") + "?" + UTM
    return {"id": p.get("id"), "width": p.get("width"), "height": p.get("height"), "color": p.get("color"),
            "alt": p.get("alt_description") or p.get("description") or "", "thumb": (p.get("urls") or {}).get("small"),
            "photographer": name, "photographer_url": profile,
            "unsplash_url": ((p.get("links") or {}).get("html") or "https://unsplash.com") + "?" + UTM,
            "credit": f"Photo by {name} on Unsplash"}


def search(core, q: str, page: int = 1, per_page: int = 24, orientation: str = "") -> dict:
    q = (q or "").strip()
    if not q:
        raise UnsplashError("Type something to search for")
    params = {"query": q[:100], "page": max(1, int(page or 1)), "per_page": max(1, min(int(per_page or 24), 30)),
              "content_filter": "high"}
    if orientation in ("landscape", "portrait", "squarish"):
        params["orientation"] = orientation
    d = _get(core, "/search/photos", **params)
    return {"results": [_photo(p) for p in d.get("results") or []], "total": d.get("total", 0),
            "total_pages": d.get("total_pages", 0), "page": params["page"]}


def import_photo(core, photo_id: str, width: int = 2160) -> dict:
    photo_id = (photo_id or "").strip()
    if not photo_id or "/" in photo_id:
        raise UnsplashError("id is required")
    p = _get(core, f"/photos/{photo_id}")
    info = _photo(p)
    loc = (p.get("links") or {}).get("download_location")
    if loc:   # required by the API guidelines: tells Unsplash the photo was used
        try:
            core.http.get(loc, headers={"Authorization": f"Client-ID {_key(core)}"}, timeout=20)
        except httpx.HTTPError:
            pass
    raw = (p.get("urls") or {}).get("raw") or (p.get("urls") or {}).get("full")
    if not raw:
        raise UnsplashError("Unsplash returned no image URL")
    url = raw + ("&" if "?" in raw else "?") + f"w={max(320, min(int(width or 2160), 4096))}&fm=jpg&q=85"
    m = media_svc.download(core, url, f"unsplash-{photo_id}.jpg")
    with core.db.session() as s:
        row = s.get(Media, m.id)
        row.alt_text = (info["alt"] or "")[:1000]
        row.title = info["credit"][:200]
        row.tags = sorted(set((row.tags or []) + ["unsplash"]))
        s.flush()
        s.expunge(row)
    return {"media": row, "credit": info["credit"], "photographer_url": info["photographer_url"], "unsplash_url": info["unsplash_url"]}
