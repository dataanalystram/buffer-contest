"""B-roll for One take: short stock cutaways from Pexels (free API key, PEXELS_API_KEY on the web server).

The web server only searches (a few KB of JSON) and stores the chosen video's URL and credit in the clip draft. The video
worker downloads the file at render time, from a Pexels host only and under a size cap, and lays it over the picture while
the speaker's own audio plays on. No key set: nothing is added and the studio says so plainly."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from .. import netguard

API = "https://api.pexels.com/videos/search"
HOSTS = ("videos.pexels.com", "player.vimeo.com")   # Pexels serves its files from videos.pexels.com (vimeo CDN as fallback)
MAX_BYTES = 45_000_000
MIN_LEN, MAX_LEN, MAX_ITEMS, LEAD_IN = 1.5, 5.0, 3, 1.0   # seconds on the clip timeline; no cutaway in the first second


class BrollError(Exception):
    pass


def key() -> str:
    return (os.environ.get("PEXELS_API_KEY") or "").strip()


def available() -> bool:
    return bool(key())


def host_ok(url: str) -> bool:
    u = urlparse(str(url or ""))
    return u.scheme == "https" and (u.hostname or "") in HOSTS and not u.port and not u.username


def pick_file(files: list[dict]) -> dict | None:
    """The smallest mp4 at least 720 px wide (a full-frame cutaway on a 1080-wide clip), else the widest."""
    mp4 = [f for f in files or [] if isinstance(f, dict) and str(f.get("file_type") or "") == "video/mp4" and f.get("link")
           and host_ok(f["link"])]
    big = sorted((f for f in mp4 if int(f.get("width") or 0) >= 720), key=lambda f: int(f.get("width") or 0))
    return big[0] if big else (max(mp4, key=lambda f: int(f.get("width") or 0)) if mp4 else None)


def search(query: str, *, n: int = 4, http=None) -> list[dict]:
    """Portrait Pexels clips for the query: [{url, thumb, credit, page, seconds, w, h, query}]. Raises BrollError."""
    k = key()
    if not k:
        raise BrollError("B-roll needs a free Pexels key (PEXELS_API_KEY) on the server, so none was added.")
    q = " ".join(str(query or "").split())[:60]
    if not q:
        raise BrollError("Say what the B-roll should show, e.g. \"b-roll of a busy kitchen\".")
    try:
        r = netguard.safe_get(f"{API}?query={q.replace(' ', '+')}&orientation=portrait&size=medium&per_page={max(1, min(n, 8))}",
                              http=http, headers={"Authorization": k}, limit=2_000_000, timeout=15, https_only=True)
    except netguard.FetchError as e:
        raise BrollError(f"Pexels couldn't be reached ({str(e)[:80]}).") from None
    if r.status_code in (401, 403):
        raise BrollError("The Pexels key was refused: check PEXELS_API_KEY.")
    if r.status_code != 200:
        raise BrollError(f"Pexels answered {r.status_code}; try again in a minute.")
    try:
        vids = r.json().get("videos") or []
    except ValueError:
        raise BrollError("Pexels sent something unreadable.") from None
    out = []
    for v in vids:
        f = pick_file(v.get("video_files") or [])
        if not f:
            continue
        out.append({"url": f["link"], "thumb": str(v.get("image") or "")[:300], "page": str(v.get("url") or "")[:300],
                    "credit": f"Video by {str((v.get('user') or {}).get('name') or 'a Pexels creator')[:40]} on Pexels",
                    "seconds": float(v.get("duration") or 0), "w": int(f.get("width") or 0), "h": int(f.get("height") or 0),
                    "query": q})
    if not out:
        raise BrollError(f"No Pexels footage for \"{q}\": try a more common word.")
    return out


def clean(raw, dur: float) -> list[dict]:
    """The draft's broll list checked: [{s, e, url, thumb, credit, query}] with s, e on the clip timeline (seconds).
    A cutaway sits inside the clip (not in its first second), runs 1.5-5 s, never overlaps another; URL on a Pexels host."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise ValueError(f"broll is a list of at most {MAX_ITEMS} cutaways.")
    out = []
    for b in raw:
        if not isinstance(b, dict):
            raise ValueError("Each cutaway is {s, e, url, query, credit}.")
        try:
            s, e = round(float(b.get("s")), 2), round(float(b.get("e")), 2)
        except (TypeError, ValueError):
            raise ValueError("A cutaway needs s and e (seconds on the clip).") from None
        if s < LEAD_IN or e > dur - 0.3 or not MIN_LEN - 0.01 <= e - s <= MAX_LEN + 0.01:
            raise ValueError(f"A cutaway runs {MIN_LEN}-{MAX_LEN} s, starts after {LEAD_IN} s and ends inside the clip.")
        if not host_ok(b.get("url")):
            raise ValueError("B-roll can only come from Pexels.")
        out.append({"s": s, "e": e, "url": str(b["url"])[:500], "thumb": str(b.get("thumb") or "")[:300],
                    "credit": str(b.get("credit") or "Video on Pexels")[:80], "query": " ".join(str(b.get("query") or "").split())[:60]})
    out.sort(key=lambda x: x["s"])
    for a, b in zip(out, out[1:]):
        if b["s"] < a["e"] + 0.1:
            raise ValueError("Cutaways can't overlap.")
    return out


def fetch(url: str, dest: Path, *, http=None) -> Path:
    """Download one Pexels file (host allowlist, redirects checked, size cap). Raises BrollError."""
    if not host_ok(url):
        raise BrollError("B-roll can only come from Pexels.")
    try:
        with netguard.safe_stream(url, http=http, https_only=True, timeout=30) as r:
            if r.status_code != 200:
                raise BrollError(f"The footage answered {r.status_code}.")
            if not host_ok(str(r.url)):
                raise BrollError("The footage moved somewhere that isn't Pexels.")
            n = 0
            with open(dest, "wb") as fh:
                for chunk in netguard.capped_body(r, MAX_BYTES):
                    n += len(chunk)
                    fh.write(chunk)
    except netguard.FetchError as e:
        raise BrollError(f"The footage couldn't be fetched ({str(e)[:80]}).") from None
    return dest


def slots(ws: list[dict], d_start: float, d_end: float, taken: list[tuple[float, float]] = (), want: float = 3.0) -> tuple[float, float] | None:
    """Where a cutaway fits: on the clip timeline, a `want`-second window (kept on word edges) in the middle of the
    clip, clear of other cutaways. None if the clip is too short."""
    dur = d_end - d_start
    if dur < LEAD_IN + MIN_LEN + 1.5:
        return None
    want = max(MIN_LEN, min(want, MAX_LEN, dur - LEAD_IN - 1.0))
    lo, hi = LEAD_IN + 0.4, dur - 0.4 - want
    c = (dur - want) / 2
    best = None
    for s in [c + k * 0.5 * sign for k in range(0, 40) for sign in (1, -1)]:
        if not lo <= s <= hi:
            continue
        if any(s < b + 0.2 and s + want > a - 0.2 for a, b in taken):
            continue
        best = round(s, 2)
        break
    return (best, round(best + want, 2)) if best is not None else None
