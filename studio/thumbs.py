"""Story thumbnails for News (Story scan, For you) and Make a post's Discover panel.

The browser never fetches a third-party page or image: ``GET /api/thumb?url=<story>&img=<hint>`` reads the story's
og:image / twitter:image (or the feed's own image hint) server-side through netguard (no auto-redirects, every hop
re-checked against private / loopback / link-local / metadata ranges, bytes capped: SECURITY_RULES 1 and 6), shrinks it
to a 640x360 JPEG with Pillow (so the bytes we serve are always ours) and caches it. No image -> 204, and the page shows
the site's favicon monogram instead. Per-account rate limit; the free sample never fetches at all (SECURITY_RULES 3).
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

import httpx

from . import netguard

PAGE_MAX = 700_000          # the head of an article is enough to find its og:image
IMAGE_MAX = 6_000_000       # bytes, checked while streaming, before Pillow decodes anything
TTL, MISS_TTL, KEEP = 24 * 3600, 3600, 400
PER_MIN = 90  # per account per minute; the free sample gets none (it never fetches)
SIZE = (640, 360)
_OG = re.compile(r"<meta[^>]+(?:property|name)=[\"'](?:og:image(?::secure_url)?|twitter:image(?::src)?)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content=[\"']([^\"']+)[\"']", re.I)

_cache: "OrderedDict[str, tuple[float, bytes | None]]" = OrderedDict()
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


class ThumbError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _ok_url(u: str) -> bool:
    p = urlparse(u or "")
    return p.scheme in ("http", "https") and bool(p.hostname) and len(u) <= 2000


def allow(uid: str) -> bool:
    now = time.time()
    with _lock:
        h = [t for t in _hits.get(uid, []) if t > now - 60]
        if len(h) >= PER_MIN:
            _hits[uid] = h
            return False
        h.append(now)
        _hits[uid] = h
        return True


def og_image(page_url: str, http: httpx.Client | None = None) -> str:
    r = netguard.safe_get(page_url, http=http, limit=PAGE_MAX, timeout=8, headers={"User-Agent": "HuhQueue thumbnail reader"})
    if r.status_code >= 400:
        return ""
    for tag in _OG.findall(r.text[:PAGE_MAX]):
        m = _CONTENT.search(tag)
        if m:
            u = urljoin(str(r.url), m.group(1).strip().replace("&amp;", "&"))
            if _ok_url(u):
                return u
    return ""


def shrink(data: bytes) -> bytes | None:
    """Any image -> a 640x360 cover-cropped JPEG (decoded by Pillow only after the byte cap; bomb-guarded)."""
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 40_000_000
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (SIZE[0] * 2, SIZE[1] * 2))
        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), SIZE, Image.LANCZOS)
        if im.width < 32:
            return None
        b = io.BytesIO()
        im.save(b, "JPEG", quality=80, optimize=True)
        return b.getvalue()
    except Exception:  # noqa: BLE001  not an image, truncated, too large
        return None


def get(page_url: str, img_hint: str = "", http: httpx.Client | None = None) -> bytes | None:
    if not _ok_url(page_url) and not _ok_url(img_hint):
        raise ThumbError("Give a http(s) story link.")
    key = hashlib.sha256(f"{page_url}\n{img_hint}".encode()).hexdigest()
    now = time.time()
    with _lock:
        got = _cache.get(key)
        if got and got[0] > now:
            _cache.move_to_end(key)
            return got[1]
    out = None
    for cand in ([img_hint] if _ok_url(img_hint) else []) + ["og"]:
        try:
            src = og_image(page_url, http) if cand == "og" and _ok_url(page_url) else cand
            if not src or src == "og":
                continue
            r = netguard.safe_get(src, http=http, limit=IMAGE_MAX, timeout=8, headers={"User-Agent": "HuhQueue thumbnail reader"})
            if r.status_code < 400:
                out = shrink(r.content)
        except (netguard.FetchError, httpx.HTTPError, ValueError):
            out = None
        if out:
            break
    with _lock:
        _cache[key] = (now + (TTL if out else MISS_TTL), out)
        while len(_cache) > KEEP:
            _cache.popitem(last=False)
    return out
