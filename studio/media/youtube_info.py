"""A YouTube link -> the real video's details, and the maker's own website (owner bug, 3 Nov 2026).

"https://youtu.be/MxAa8g8qFz4 can you get this video with huhbyte ai brand kit" read the watch page as a plain article:
the title came back as "- YouTube", so the facts were made up from the prompt. A YouTube link is read here instead:

* the person's **YouTube Data API key** (Settings > Integrations, ``user_secret(..., "yt_key")``) or the operator's
  ``YOUTUBE_API_KEY``: ``videos.list`` (snippet, contentDetails) gives title, channel, description, thumbnails, date and
  length. The Data API never hands out the video file itself; the file comes from the fallbacks in video_first.
* without a key, YouTube's public **oEmbed** endpoint: title, channel, channel URL, thumbnail (no description).

``find_site`` then finds the maker's own website (links in the description first, then the channel's name as a domain:
"Screen Studio" -> screen.studio), so the cascade can fetch the official copy of the video from there when YouTube
itself can't be downloaded. Every request goes through netguard; the key never appears in an error or a log.
"""
from __future__ import annotations

import html as htmlmod
import re
from urllib.parse import quote, urlparse

from .. import netguard
from . import find

API = "https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails&id={id}&key={key}"
OEMBED = "https://www.youtube.com/oembed?format=json&url={u}"
SOCIAL = ("youtube.com", "youtu.be", "x.com", "twitter.com", "instagram.com", "tiktok.com", "linkedin.com", "facebook.com",
          "fb.com", "discord.gg", "discord.com", "t.me", "threads.net", "bsky.app", "reddit.com", "github.com", "patreon.com",
          "bit.ly", "goo.gl", "linktr.ee", "apple.com", "apps.apple.com", "play.google.com", "medium.com", "substack.com")
TLDS = ("com", "ai", "io", "app", "so", "dev", "co")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def is_youtube_video(url: str) -> bool:
    return find.platform_of(url or "") == "youtube" and bool(find.youtube_id(url or ""))


def _dur(iso: str) -> int | None:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else None


def video_info(url: str, http=None, api_key: str = "") -> dict | None:
    """{id, url, title, channel, channel_url, description, thumbnail, published, duration, via, links, note} or None."""
    vid = find.youtube_id(url or "")
    if not vid:
        return None
    out = {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": "", "channel": "", "channel_url": "",
           "description": "", "thumbnail": "", "published": "", "duration": None, "via": "", "links": [], "note": ""}
    if api_key:
        try:
            r = netguard.safe_get(API.format(id=quote(vid), key=quote(api_key)), http=http, https_only=True, timeout=12,
                                  limit=600_000, headers={"Accept": "application/json"})
            items = r.json().get("items") or [] if r.status_code == 200 else []
            if items:
                sn, cd = items[0].get("snippet") or {}, items[0].get("contentDetails") or {}
                th = sn.get("thumbnails") or {}
                best = next((th[k]["url"] for k in ("maxres", "standard", "high", "medium", "default") if (th.get(k) or {}).get("url")), "")
                out.update(title=sn.get("title") or "", channel=sn.get("channelTitle") or "", description=(sn.get("description") or "")[:5000],
                           thumbnail=best, published=sn.get("publishedAt") or "", duration=_dur(cd.get("duration") or ""),
                           channel_url=f"https://www.youtube.com/channel/{sn['channelId']}" if sn.get("channelId") else "",
                           via="youtube_api")
            else:
                out["note"] = f"The YouTube Data API answered {r.status_code}" if r.status_code != 200 else "The YouTube Data API has no such video"
        except Exception as e:  # noqa: BLE001 - never the URL (it carries the key)
            out["note"] = f"The YouTube Data API didn't answer ({e.__class__.__name__})"
    if not out["title"]:
        try:
            r = netguard.safe_get(OEMBED.format(u=quote(out["url"], safe="")), http=http, https_only=True, timeout=12, limit=200_000,
                                  headers={"Accept": "application/json"})
            if r.status_code == 200:
                j = r.json()
                out.update(title=j.get("title") or "", channel=j.get("author_name") or "", channel_url=j.get("author_url") or "",
                           thumbnail=j.get("thumbnail_url") or out["thumbnail"], via="youtube_oembed")
            else:
                out["note"] = (out["note"] + "; " if out["note"] else "") + f"YouTube oEmbed answered {r.status_code}"
        except Exception as e:  # noqa: BLE001
            out["note"] = (out["note"] + "; " if out["note"] else "") + f"YouTube oEmbed didn't answer ({e.__class__.__name__})"
    if not out["title"]:
        return None
    out["title"] = htmlmod.unescape(out["title"]).strip()
    if out["thumbnail"].endswith("hqdefault.jpg"):  # oEmbed gives 480x360; the max-res frame is there for most videos
        out["thumbnail_hd"] = out["thumbnail"].replace("hqdefault.jpg", "maxresdefault.jpg")
    out["links"] = site_links(out["description"])
    return out


def site_links(text: str) -> list[str]:
    """Links in a description that could be the maker's own site (social profiles, shorteners and stores left out)."""
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:!?")
        h = find.host_of(u)
        if not h or any(h == s or h.endswith("." + s) for s in SOCIAL):
            continue
        base = f"https://{urlparse(u).netloc}/"
        if base not in out:
            out.append(base)
    return out[:4]


def name_domains(name: str) -> list[str]:
    """'Screen Studio' -> screen.studio, screenstudio.com, ... (the channel's name as its own domain)."""
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w]
    if not words or len("".join(words)) < 3:
        return []
    out = []
    if len(words) == 2:
        out.append(f"https://{words[0]}.{words[1]}/")
    joined = "".join(words)
    out += [f"https://{joined}.{t}/" for t in TLDS]
    if len(words) > 1:
        out.append(f"https://{words[0]}.com/")
    return list(dict.fromkeys(out))


def _matches(name: str, site: str, page: dict) -> bool:
    n = find._norm(name)
    if len(n) < 3:
        return False
    host = find._norm(find.host_of(site).split(":")[0])
    return n in host or n in find._norm(page.get("title", "") + " " + page.get("site_name", ""))


def find_site(name: str, links: list[str], http=None, tries: int = 5) -> tuple[str, str]:
    """(website, how) for the channel: a description link that answers, else the name as a domain whose page carries
    the name. ("", note) when neither worked."""
    for u in list(links or [])[:3]:
        try:
            got, ctx = find.from_page(u, http)
            if not ctx.get("note"):
                return u, "linked in the video's description"
        except Exception:  # noqa: BLE001
            continue
    for u in name_domains(name)[:tries]:
        try:
            got, ctx = find.from_page(u, http)
            if not ctx.get("note") and _matches(name, u, ctx):
                return u, f"{find.host_of(u)} carries the channel's name"
        except Exception:  # noqa: BLE001
            continue
    return "", "the maker's website wasn't found"
