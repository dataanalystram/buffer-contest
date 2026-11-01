"""Weekly recap with videos: "make a 7 days what happened in AI recap with videos".

    stories = recap.gather_week(http=client)                    # the week's top stories (news search, deduped, ranked)
    plan = recap.plan(stories, find=tools.media_find)           # each story's official video/image + credit + rights
    out = recap.compose(plan, fetched, out_dir="recap/")        # worker: carousel video slides + one Reel

The HuhByte weekly-recap format (owner's rules, 22 Oct 2026): a cover that asks "DO YOU REALIZE WHAT JUST HAPPENED IN AI
IN THE LAST 7 DAYS?", one branded segment per story with its credit line on screen, and the Follow card only once, at
the very end. Story slides never get their own end card or Follow pill. Slides stay at or under 60 s.

Gathering and composition live here; the look is behind ``RecapLayout``. The HuhByte weekly-recap template design is
being ported separately (hq-themes); when a layout is registered under ``RECAP_LAYOUTS`` (``register_layout``) it is
used, otherwise the minimal internal layout below renders the same structure.

Rights: a video or image from the company's own site/channel (``official``) is fine with a credit; anything else needs
the person's confirmation (``plan()["needs_confirmation"]`` lists those stories with one question), and stories the
person declines fall back to an on-brand title card.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

COVER_TEXT = "DO YOU REALIZE WHAT JUST HAPPENED IN AI IN THE LAST 7 DAYS?"
COVER_ACCENT = ["LAST", "7", "DAYS?"]
DEFAULT_QUERIES = ("artificial intelligence", "AI model launch", "OpenAI", "Anthropic Claude", "Google Gemini AI",
                   "Nvidia AI")
MAX_STORIES = 7
SLIDE_MAX = 60.0       # carousel video slides: never over 60 s
REEL_STORY_SECS = 7.0  # per story in the single Reel
CARD_SECS = 2.5
RECAP_RX = re.compile(r"\b(?:recap|round-?up|week in ai|what happened in ai|this week in ai|last 7 days|past week|weekly)\b", re.I)
WITH_VIDEO_RX = re.compile(r"\bwith (?:the |their )?(?:videos?|clips?|launch videos?|media)\b|\bvideo recap\b", re.I)


def wants_recap(prompt: str) -> dict:
    """Pure. {"recap": bool, "videos": bool, "days": int, "n": int|None}."""
    p = prompt or ""
    days = 7
    m = re.search(r"\b(\d{1,2})\s*[- ]?days?\b", p, re.I)
    if m:
        days = max(1, min(14, int(m.group(1))))
    n = None
    m = re.search(r"\btop\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:stories|things|headlines)\b", p, re.I)
    if m:
        n = max(3, min(MAX_STORIES, int(m.group(1) or m.group(2))))
    return {"recap": bool(RECAP_RX.search(p)), "videos": bool(WITH_VIDEO_RX.search(p)) or "video" in p.lower(), "days": days, "n": n}


# ====================================================================== gather (light: runs on the web app)
def _tokens(t: str) -> set[str]:
    from ..sources_agent import tokens
    return tokens(t)


def _norm_title(t: str) -> str:
    return re.sub(r"[’']s\b", "", t or "")


def same_story(a: dict, b: dict) -> bool:
    """Two headlines about one event: at least half the salient words of the LONGER headline shared (measured against the
    shorter one, "Anthropic ... Claude Code" would merge every Anthropic story of the week)."""
    ta, tb = _tokens(_norm_title(a["title"])), _tokens(_norm_title(b["title"]))
    if not ta or not tb:
        return False
    shared = ta & tb
    if len(shared) < 2:
        return False
    return len(shared) / max(len(ta), len(tb)) >= 0.5 or shared == min(ta, tb, key=len)  # "Gemini 4 Carbon" inside a longer one


AI_RX = re.compile(r"\b(ai|a\.i\.|model|models|llm|gpt|gemini|claude|openai|anthropic|deepmind|nvidia|agents?|chatbot|copilot|"
                   r"machine learning|neural|robot\w*|grok|llama|mistral|perplexity)\b", re.I)


def rank(leads: list[dict], days: int = 7, now: float | None = None, n: int = 5) -> list[dict]:
    """Leads -> one entry per story, ranked by how many outlets carried it, Hacker News points and freshness.
    Only AI stories from the window; the most-covered link of each cluster is the story's link."""
    now = now or time.time()
    lo = now - days * 86400
    leads = [x for x in leads if x.get("title") and AI_RX.search(x["title"]) and (not x.get("published") or x["published"] >= lo)]
    groups: list[list[dict]] = []
    for x in sorted(leads, key=lambda y: -(y.get("points") or 0)):
        for g in groups:
            if same_story(x, g[0]):  # against the group's first headline only: no chaining into a different story
                g.append(x)
                break
        else:
            groups.append([x])
    out = []
    for g in groups:
        outlets = {(urlparse(y["url"]).hostname or y.get("source") or "").removeprefix("www.") for y in g}
        pts = max((y.get("points") or 0) for y in g)
        pub = max((y.get("published") or 0) for y in g)
        fresh = max(0.0, 1 - (now - pub) / (days * 86400)) if pub else 0.3
        # the publisher's own link beats a news.google.com redirect; then the fullest summary
        lead = max(g, key=lambda y: ("news.google.com" not in y["url"], len(y.get("summary") or ""), -len(y["title"])))
        score = 3 * len(outlets) + min(4, pts / 100) + 2 * fresh
        out.append({"title": lead["title"], "url": lead["url"], "source": lead.get("source") or "", "published": pub,
                    "outlets": sorted(o for o in outlets if o), "score": round(score, 2), "summary": lead.get("summary") or "",
                    "image": next((y.get("image") for y in g if y.get("image")), "")})
    out.sort(key=lambda s: -s["score"])
    picked: list[dict] = []
    per: dict[str, int] = {}
    for st in out:  # a recap covers different things: one entry per event, at most two stories per company
        who = company(st["title"])
        if any(same_story(st, p) for p in picked) or (who and per.get(who, 0) >= MAX_PER_COMPANY):
            continue
        picked.append(st)
        if who:
            per[who] = per.get(who, 0) + 1
    return picked[:n]


COMPANIES = [("anthropic", r"anthropic|claude"), ("openai", r"openai|chatgpt|gpt-?\d|sora"), ("google", r"google|gemini|deepmind"),
             ("nvidia", r"nvidia"), ("meta", r"\bmeta\b|llama"), ("microsoft", r"microsoft|copilot"), ("xai", r"\bxai\b|grok"),
             ("deepseek", r"deepseek"), ("apple", r"\bapple\b"), ("amazon", r"amazon|\baws\b"), ("mistral", r"mistral")]
MAX_PER_COMPANY = 2


def company(title: str) -> str:
    t = (title or "").lower()
    for name, rx in COMPANIES:
        if re.search(rx, t):
            return name
    return ""


def gather_week(http=None, queries=DEFAULT_QUERIES, days: int = 7, n: int = 5, feeds: list[dict] | None = None,
                search=None) -> list[dict]:
    """The week's top AI stories from the free news sources (Bing News, Google News, Hacker News, followed feeds)."""
    from .. import sources
    leads: list[dict] = []
    for q in queries:
        if search:
            leads += search(q)
            continue
        leads += sources.bing_news(q, http, 15) + sources.google_news(q, http, 15) + sources.hacker_news(q, http, 10)
    if feeds:
        leads += sources.followed(feeds, http, 30)
    top = rank(leads, days=days, n=n)
    for st in top:  # a Google News redirect -> the publisher's page, so media search reads the real article
        if "news.google.com" in st["url"]:
            try:
                st["url"] = sources.resolve_google_link(st["url"], http)
            except Exception:  # noqa: BLE001
                pass
    return top


# ====================================================================== plan (light): media + rights per story
def headline(title: str, max_words: int = 12) -> str:
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", title or "").strip()  # " - The Verge" tails
    w = t.split()
    return t if len(w) <= max_words else " ".join(w[:max_words]).rstrip(",;:") + "…"


def dek(summary: str, title: str, max_chars: int = 170) -> str:
    """The one-line summary under a story on the 4:5 slide: the feed's own summary, tags stripped, cut on a word.
    Empty when it only repeats the headline (Google News summaries often do)."""
    t = re.sub(r"<[^>]+>|&nbsp;", " ", summary or "")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    a, b = _tokens(t), _tokens(title or "")
    if a and b and len(a & b) / len(a) >= 0.7:
        return ""
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return t


def source_line(seg: dict) -> str:
    """'The Verge + 2 more': who reported the story (the pill at the bottom of a 4:5 slide)."""
    outlets = [o for o in seg.get("outlets") or [] if o] or ([seg["source"]] if seg.get("source") else [])
    if not outlets:
        return ""
    return outlets[0] + (f" + {len(outlets) - 1} more" if len(outlets) > 1 else "")


def x_media(post_url: str, http=None, read=None) -> dict | None:
    """An X post's own video (or photo) through the free X reader cascade (studio/xreader.py: oEmbed -> react-tweet
    syndication -> FxTwitter), as a direct MP4/JPG the worker fetches through netguard. The post URL stays as
    ``source_page`` so the worker can fall back to yt-dlp's Twitter extractor. None when the post has no media."""
    from ..reader import X_STATUS
    m = X_STATUS.match(post_url or "")
    if not m:
        return None
    if read is None:
        from ..xreader import XReadError, read as _read

        def read(h, sid, http=None):  # noqa: E306
            try:
                return _read(h, sid, http)
            except XReadError:
                return None
    got = read(m.group(1), m.group(2), http=http) or {}
    who = got.get("handle") or m.group(1)
    if got.get("videos"):
        v = got["videos"][0]
        return {"kind": "video", "url": v["url"], "official": False, "credit": f"Video: @{who} on X",
                "candidate": {"url": v["url"], "kind": "video", "platform": "", "source_page": post_url, "poster": v.get("poster", ""),
                              "duration": v.get("duration")}}
    if got.get("photos"):
        return {"kind": "image", "url": got["photos"][0], "official": False, "credit": f"Image: @{who} on X",
                "candidate": {"url": got["photos"][0], "kind": "image", "source_page": post_url}}
    return None


def plan(stories: list[dict], find=None, video: bool = True, http=None, x_read=None) -> dict:
    """Each story -> {headline, url, media: {kind, url, official, credit, candidate} | None}. Official media needs no
    question; other people's media is listed in ``needs_confirmation`` with one question for the person."""
    segs, ask = [], []
    for i, st in enumerate(stories):
        media = None
        if find and video:
            try:
                got = find(st["url"])
            except Exception:  # noqa: BLE001 - a story without media gets a title card
                got = {}
            c = (got or {}).get("best_downloadable") or (got or {}).get("best_video") or None
            if c and re.search(r"//(?:www\.|mobile\.)?(?:x|twitter)\.com/", c.get("url") or ""):
                # an X post: its media through the free X reader; a post without video is not a video
                xm = x_media(c["url"], http=http, read=x_read)
                if xm:
                    media = xm
                c = None
            if c:
                media = {"kind": "video", "url": c["url"], "official": bool(c.get("official")), "credit": c.get("credit") or "",
                         "candidate": c}
            elif not media:
                im = next(iter((got or {}).get("images") or []), None)
                if im:
                    media = {"kind": "image", "url": im["url"], "official": bool(im.get("official")), "credit": im.get("credit") or "",
                             "candidate": im}
        if not media and video and "x.com/" in (st.get("url") or ""):
            media = x_media(st["url"], http=http, read=x_read)
        if not media and st.get("image"):
            media = {"kind": "image", "url": st["image"], "official": False, "credit": f"Image: {st.get('source') or 'the publisher'}",
                     "candidate": {"url": st["image"], "kind": "image"}}
        if media and not media["official"] and _first_party(st, media):
            media["official"] = True
        seg = {"i": i + 1, "headline": headline(st["title"]), "url": st["url"], "source": st.get("source") or "",
               "outlets": st.get("outlets") or [], "summary": dek(st.get("summary") or "", st["title"]), "media": media}
        if media and not media["official"]:
            ask.append({"i": i + 1, "headline": seg["headline"], "credit": media["credit"]})
        segs.append(seg)
    out = {"cover": COVER_TEXT, "segments": segs, "follow_only_at_end": True}
    if ask:
        out["needs_confirmation"] = ask
        out["question"] = ("These stories use media that isn't from the company itself: " +
                           "; ".join(f"#{a['i']} {a['headline']} ({a['credit']})" for a in ask) +
                           ". I'll credit each on screen and in the caption. OK to use them, or should I use title cards instead?")
    return out


def _first_party(st: dict, media: dict) -> bool:
    """The story IS the company's own post (Google's blog about Gemini) and the media came from that page or the
    company's own domain: official, so no rights question (still credited)."""
    who = company(st.get("title") or "")
    host = (urlparse(st.get("url") or "").hostname or "").lower()
    if not who or who not in host:
        return False
    c = media.get("candidate") or {}
    mhost = (urlparse(media.get("url") or "").hostname or "").lower()
    own_cdn = {"google": ("googleapis.com", "gstatic.com", "googleusercontent.com", "youtube.com", "ytimg.com")}.get(who, ())
    return (c.get("source_page") or "").startswith(st.get("url") or "\0") or who in mhost or mhost.endswith(own_cdn)


def apply_rights(p: dict, confirmed: bool | list[int]) -> dict:
    """The person's answer: True (all), False (none: title cards), or the story numbers they OK'd."""
    ok = set(confirmed) if isinstance(confirmed, list) else None
    for s in p["segments"]:
        m = s.get("media")
        if m and not m["official"]:
            allowed = confirmed is True or (ok is not None and s["i"] in ok)
            m["rights_confirmed"] = bool(allowed)
            if not allowed:
                s["media"] = None
    p.pop("needs_confirmation", None)
    p.pop("question", None)
    return p


# ====================================================================== layout interface (hq-themes registers its own)
class RecapLayout:
    """What a recap look must provide. Every method writes a PNG of exactly (W, H) and returns layout boxes."""
    name = "minimal"

    def cover(self, path: str, W: int, H: int, brand: dict, text: str, still) -> dict:
        from .compose import cover_card
        return cover_card(path, W, H, brand, text, still, label="WEEKLY RECAP", accent_words=COVER_ACCENT)

    def story_overlay(self, path: str, W: int, H: int, brand: dict, seg: dict, n: int) -> dict:
        """Transparent overlay over the story's media: counter, headline on top, credit under the media panel.
        Returns {"panel": (x0, y0, x1, y1), ...} where the media goes."""
        from PIL import Image, ImageDraw

        from .compose import _credit_chip, _pill, _text_block, safe_area
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        l, t, r, b = safe_area(W, H)
        boxes = {"counter": _pill(d, f"{seg['i']} / {n}", (l + r) // 2, t, brand, size=28)}
        y = boxes["counter"][3] + int(H * 0.02)
        hb, _ = _text_block(d, seg["headline"], brand["head_font"], (l, y, r, y + int(H * (0.16 if H / W > 1.6 else 0.18))),
                            max_size=84, min_size=30, max_lines=3, upper=True, valign="top")
        boxes["headline"] = hb
        top = hb[3] + int(H * 0.025)
        bottom = b - 70
        pw = r - l if H / W > 1.6 else W - 2 * l
        ph = min(bottom - top, int(pw * 9 / 16) if H / W <= 1.6 else int(pw * 16 / 9))
        x0 = (W - pw) // 2
        boxes["panel"] = (x0, top, x0 + pw, top + ph)
        feed = H / W <= 1.6
        credit = (seg.get("media") or {}).get("credit") or \
            (f"Source: {seg['source']}" if seg.get("source") and not (feed and source_line(seg)) else "")  # 4:5: the pill says it
        if credit:
            boxes["credit"] = _credit_chip(d, credit, l, top + ph + 14, r - l)
        if feed:  # 4:5 feed slide: the space under the media holds the story's summary and who reported it
            self._feed_footer(d, W, H, brand, seg, (boxes.get("credit") or boxes["panel"])[3], boxes)
        im.save(path)
        return boxes

    def _feed_footer(self, d, W: int, H: int, brand: dict, seg: dict, y_top: int, boxes: dict) -> None:
        from .compose import _outline_pill, _text_block, safe_area
        l, _, r, b = safe_area(W, H)
        src = source_line(seg)
        pill_h = int(H * 0.05)
        bottom = b - (pill_h + int(H * 0.02) if src else 0)
        y = y_top + int(H * 0.025)
        if seg.get("summary") and bottom - y >= 60:
            boxes["summary"], _ = _text_block(d, seg["summary"], brand.get("body_font") or "Poppins-Medium.ttf", (l, y, r, bottom),
                                              max_size=40, min_size=22, max_lines=3, fill=brand.get("text", "#FFFFFF"))
        if src:
            py = b - pill_h if seg.get("summary") else (y + b) // 2 - pill_h  # no summary: the pill sits mid-space
            boxes["source"] = _outline_pill(d, "Reported by " + src, (l + r) // 2, py, brand, height=pill_h, maxw=r - l)
        if not seg.get("summary"):  # nothing to read under the media: a quiet swipe cue keeps the slide from looking unfinished
            boxes["swipe"], _ = _text_block(d, "SWIPE FOR THE NEXT STORY", "Inter-SemiBold.ttf", (l, b - int(H * 0.045), r, b),
                                            max_size=26, min_size=16, max_lines=1, fill=brand.get("muted", "#A0A0A8"))

    def plate(self, path: str, w: int, h: int, brand: dict, seg: dict) -> dict:
        """A story with no usable video or image: a quiet brand panel naming who reported it."""
        from PIL import Image, ImageDraw

        from .compose import _rgb, _text_block
        bg, ac = _rgb(brand["bg"]), _rgb(brand["accent"])
        im = Image.new("RGB", (w, h), bg)
        d = ImageDraw.Draw(im)
        for y in range(h):  # a soft accent glow from the bottom
            k = max(0.0, (y - h * 0.35) / (h * 0.65)) * 0.22
            d.line((0, y, w, y), fill=tuple(int(bg[i] * (1 - k) + ac[i] * k) for i in range(3)))
        d.rounded_rectangle((2, 2, w - 3, h - 3), 28, outline=(60, 60, 68), width=2)
        outlets = [o for o in seg.get("outlets") or [] if o] or ([seg["source"]] if seg.get("source") else [])
        who = outlets[0] if outlets else "the news"
        more = f" + {len(outlets) - 1} more" if len(outlets) > 1 else ""
        m = int(w * 0.08)
        boxes = {}
        boxes["label"], _ = _text_block(d, "REPORTED BY", "Inter-SemiBold.ttf", (m, int(h * 0.30), w - m, int(h * 0.38)),
                                        max_size=34, min_size=16, max_lines=1, fill=brand.get("muted", "#A0A0A8"))
        boxes["who"], _ = _text_block(d, who + more, brand["head_font"], (m, int(h * 0.40), w - m, int(h * 0.62)), max_size=96,
                                      min_size=28, max_lines=2, fill=brand["accent"])
        im.save(path)
        return boxes

    def follow(self, path: str, W: int, H: int, brand: dict) -> dict:
        from .compose import end_card
        return end_card(path, W, H, brand)


RECAP_LAYOUTS: dict[str, RecapLayout] = {"minimal": RecapLayout()}


def register_layout(name: str, layout: RecapLayout) -> None:
    RECAP_LAYOUTS[name] = layout


def layout_for(name: str | None = None) -> RecapLayout:
    if name and name in RECAP_LAYOUTS:
        return RECAP_LAYOUTS[name]
    return RECAP_LAYOUTS.get("huhbyte_weekly_recap") or RECAP_LAYOUTS["minimal"]


# ====================================================================== compose (worker)
SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350)}
FPS = 30


def _venc() -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def _aenc() -> list[str]:
    return ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]


def _card(png: str, secs: float, out: str) -> str:
    from . import ff
    ff.run(["-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{secs:.2f}", "-i", png, "-f", "lavfi", "-t",
            f"{secs:.2f}", "-i", "anullsrc=r=44100:cl=stereo", "-vf", "format=yuv420p,setsar=1", *_venc(), *_aenc(),
            "-shortest", out], timeout=300)
    return out


def _segment(seg: dict, media_path: str | None, secs: float, W: int, H: int, brand: dict, lay: RecapLayout, n: int,
             wd: Path, tag: str) -> tuple[str, dict]:
    """One story: its media in the layout's panel (video from the start, up to ``secs``; an image with a slow push-in;
    nothing -> the panel shows a brand-coloured title plate), the overlay on top, the source's own audio (silence if
    none). Returns (mp4 path, boxes)."""
    from . import ff
    ov = str(wd / f"ov_{tag}.png")
    boxes = lay.story_overlay(ov, W, H, brand, seg, n)
    x0, y0, x1, y1 = boxes["panel"]
    pw, ph = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    bg = brand["bg"].lstrip("#")
    out = str(wd / f"seg_{tag}.mp4")
    kind = (seg.get("media") or {}).get("kind") if media_path else None
    dur = secs
    if kind == "video":
        pr = ff.probe(media_path)
        dur = max(2.0, min(secs, pr["duration"] or secs))
        args = ["-t", f"{dur:.3f}", "-i", media_path]
        src = (f"[0:v]setpts=PTS-STARTPTS,fps={FPS},scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
               f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=0x{bg}[pv]")
        audio = ["a"] if pr.get("has_audio") else []
    elif kind == "image":
        args = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", media_path]
        fr = int(dur * FPS)
        src = (f"[0:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},setsar=1,scale={pw * 112 // 100 // 2 * 2}:-2,"
               f"zoompan=z='min(1+0.0010*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s={pw}x{ph}:fps={FPS}[pv]")
        audio = []
    else:  # no usable media: the layout's plate (who reported it), held still
        plate = str(wd / f"plate_{tag}.png")
        boxes["plate"] = lay.plate(plate, pw, ph, brand, seg)
        args = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", plate]
        src = "[0:v]format=yuv420p,setsar=1[pv]"
        audio = []
    fc = (f"{src};color=c=0x{bg}:s={W}x{H}:r={FPS}:d={dur:.3f}[bgc];[bgc][pv]overlay={x0}:{y0}:shortest=1[b];"
          f"[b][1:v]overlay=0:0,format=yuv420p,setsar=1[v]")
    if audio:
        fc += f";[0:a]aresample=44100,aformat=channel_layouts=stereo,apad,atrim=0:{dur:.3f},afade=t=out:st={max(0, dur - 0.4):.2f}:d=0.4[a]"
    else:
        args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    ff.run(_cmd(args, out, ov, dur, fc, audio), timeout=900)
    return out, boxes


def _cmd(args: list[str], out: str, ov: str, dur: float, fc: str, audio: list[str]) -> list[str]:
    """ffmpeg command for a segment. Input order: 0 = media (or colour), 1 = overlay, [2 = silence]."""
    media, silence = args, []
    if not audio:  # the silence input was appended to args last; keep it after the overlay so it is input 2
        media, silence = args[:-6], args[-6:]
    amap = ["-map", "[a]"] if audio else ["-map", "2:a"]
    return ["-y", "-v", "error", *media, "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", ov, *silence,
            "-filter_complex", fc, "-map", "[v]", *amap, "-t", f"{dur:.3f}", *_venc(), *_aenc(), out]


def _concat(parts: list[str], out: str) -> str:
    from . import ff
    lst = Path(out).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts))
    ff.run(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", out],
           timeout=600)
    return out


def usable(path: str | None, kind: str | None) -> bool:
    """A fetched file is what it claims to be (an error page saved as .png is not): images open in Pillow and are at
    least 200 px; videos probe with a duration."""
    if not path or not Path(path).is_file() or Path(path).stat().st_size < 1024:
        return False
    try:
        if kind == "image":
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                return min(im.size) >= 200
        from . import ff
        pr = ff.probe(path)
        return bool(pr.get("width")) and (pr.get("duration") or 0) > 0.5
    except Exception:  # noqa: BLE001
        return False


def compose(p: dict, media: dict[int, str | None], out_dir: str, brand: dict | None = None, layout: str | None = None,
            reel: bool = True, slides: bool = True, reel_secs: float = REEL_STORY_SECS, slide_max: float = SLIDE_MAX) -> dict:
    """Render the recap. ``media`` maps story number -> local file (video or image) or None. Returns
    {"slides": [{i, path, duration, ratio}], "reel": {path, duration} | None, "cover", "follow", "boxes"}.

    Carousel (4:5): slide 0 the cover, one video slide per story (its media up to 60 s, no end card, no Follow pill),
    last slide the Follow card. Reel (9:16): cover 2.5 s -> each story up to ``reel_secs`` -> Follow card, once."""
    from PIL import Image

    from . import ff
    from .compose import DEFAULT_BRAND
    brand = {**DEFAULT_BRAND, **(brand or {})}
    lay = layout_for(layout)
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    wd = Path(tempfile.mkdtemp(prefix="hq_recap_"))
    segs = p["segments"]
    n = len(segs)
    from ..headline import render_hook
    for s in segs:  # the headline guard at render time (owner P0): no URL or request wording is ever burned in
        s["headline"] = render_hook(s.get("headline") or "") or (f"Reported by {source_line(s)}" if source_line(s) else f"Story {s['i']}")
    media = {i: (m if usable(m, (next((s for s in segs if s["i"] == i), {}).get("media") or {}).get("kind")) else None)
             for i, m in media.items()}
    for s in segs:  # media that didn't arrive (or isn't real media) -> the story's title card, and no stale credit
        if not media.get(s["i"]) and s.get("media"):
            s["media"] = None
    result: dict = {"slides": [], "reel": None, "layout": lay.name, "boxes": {}}
    try:
        still = None
        for s in segs:
            mp = media.get(s["i"])
            if mp and (s.get("media") or {}).get("kind") == "video":
                f = wd / "still.png"
                ff.run(["-y", "-v", "error", "-ss", "1", "-i", mp, "-frames:v", "1", "-update", "1", str(f)], timeout=60)
                still = Image.open(f)
                break
            if mp and (s.get("media") or {}).get("kind") == "image":
                still = Image.open(mp)
                break
        for ratio, on in (("4:5", slides), ("9:16", reel)):
            if not on:
                continue
            W, H = SIZES[ratio]
            tag = ratio.replace(":", "x")
            cov = str(od / f"cover_{tag}.png")
            result["boxes"][f"cover_{tag}"] = lay.cover(cov, W, H, brand, p.get("cover") or COVER_TEXT, still)
            fol = str(od / f"follow_{tag}.png")
            result["boxes"][f"follow_{tag}"] = lay.follow(fol, W, H, brand)
            if ratio == "4:5":
                result["cover"], result["follow"] = cov, fol
                for s in segs:
                    path, bx = _segment(s, media.get(s["i"]), slide_max if (s.get("media") or {}).get("kind") == "video" else 6.0,
                                        W, H, brand, lay, n, wd, f"s{s['i']}_{tag}")
                    dst = od / f"slide_{s['i']:02d}.mp4"
                    shutil.move(path, dst)
                    result["slides"].append({"i": s["i"], "path": str(dst), "duration": round(ff.probe(str(dst))["duration"], 2),
                                             "ratio": ratio, "credit": (s.get("media") or {}).get("credit") or "", "boxes": bx})
            else:
                parts = [_card(cov, CARD_SECS, str(wd / f"c_{tag}.mp4"))]
                for s in segs:
                    path, bx = _segment(s, media.get(s["i"]), reel_secs, W, H, brand, lay, n, wd, f"r{s['i']}_{tag}")
                    parts.append(path)
                    result["boxes"][f"reel_{s['i']}"] = bx
                parts.append(_card(fol, 3.0, str(wd / f"f_{tag}.mp4")))
                rp = _concat(parts, str(od / "recap_reel_9x16.mp4"))
                result["reel"] = {"path": rp, "duration": round(ff.probe(rp)["duration"], 2), "segments": len(parts)}
        return result
    finally:
        shutil.rmtree(wd, ignore_errors=True)
