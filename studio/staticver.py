"""Versioned static URLs (owner, 2 Nov 2026: a new landing section shipped, but a browser that kept yesterday's CSS
showed the old strip).

``page(name)`` returns an HTML file from studio/static with every same-origin ``/static/<file>.css|.js`` reference
rewritten to ``/static/<file>?v=<first 10 hex of the file's SHA-256>``. A deploy that changes a file changes its URL, so
no browser, proxy or CDN can serve the old copy. A request that carries the current ``v`` gets a year-long immutable
cache; anything else (no ``v``, or an old one) is answered with ``no-cache`` as before, so it always revalidates.

Hashes are cached per file and re-read when the file's mtime changes (tests edit files in place)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"
_REF = re.compile(r'''((?:href|src)=")/static/([A-Za-z0-9_./-]+\.(?:css|js))(")''')
_hash: dict[str, tuple[float, str]] = {}
_pages: dict[str, tuple[tuple, str]] = {}


def version(rel: str) -> str:
    """The content version of studio/static/<rel>, or '' when there is no such file."""
    p = (STATIC / rel)
    try:
        m = p.stat().st_mtime
    except OSError:
        return ""
    hit = _hash.get(rel)
    if hit and hit[0] == m:
        return hit[1]
    v = hashlib.sha256(p.read_bytes()).hexdigest()[:10]
    _hash[rel] = (m, v)
    return v


def rewrite(html: str) -> str:
    def sub(m: re.Match) -> str:
        v = version(m.group(2))
        return f"{m.group(1)}/static/{m.group(2)}?v={v}{m.group(3)}" if v else m.group(0)
    return _REF.sub(sub, html)


def page(name: str) -> str:
    p = STATIC / name
    mtime = p.stat().st_mtime
    hit = _pages.get(name)
    if hit and hit[0][0] == mtime:
        # the page itself didn't change; its assets may have (a CSS edit without an HTML edit), so re-check them
        stamp = tuple(version(r[1]) for r in hit[0][1])
        if stamp == hit[0][2]:
            return hit[1]
    html = p.read_text()
    refs = tuple(_REF.findall(html))
    out = rewrite(html)
    _pages[name] = ((mtime, refs, tuple(version(r[1]) for r in refs)), out)
    return out


def cache_header(rel: str, v: str) -> str | None:
    """The Cache-Control for /static/<rel>?v=<v>: immutable when v is the file's current version, else None."""
    return IMMUTABLE if v and v == version(rel) else None
