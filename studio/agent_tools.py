"""The agent's tools. One implementation each, used by the in-app brain (agent_brain), REST and MCP alike, so a user's own
ChatGPT/Claude can drive exactly the same steps (docs/AGENT_PIPELINE.md).

Every tool works on one run's state (agent_runs.result) and returns a small JSON summary. Writes go through the kill
switch and the token's daily cap (SECURITY_RULES 4); network reads go through netguard (SECURITY_RULES 1).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from urllib.parse import urlparse

from . import brand, demo_guard, drafter, factcheck, hbstyle, intake, llm, netguard, reader, render, sources, vision
from .actions import ActionError

VIDEO_ONLY = {"tiktok", "youtube"}
TEXT_FIRST = {"twitter", "threads", "bluesky", "mastodon"}
VIDEO_WAIT = int(os.environ.get("HQ_VIDEO_WAIT", "300"))

S = {"type": "string"}
IDS = {"type": "array", "items": {"type": "string"}, "description": "Channel ids; omit for every channel in the run"}
SPECS = [
    {"name": "read_url", "description": "Read a link the person gave: an article (direct read, Firecrawl fallback) or an X post (public oEmbed; media only with the person's X API key). The link is THE source; never replace it with a search.",
     "input_schema": {"type": "object", "required": ["url"], "properties": {"url": S}}},
    {"name": "read_upload", "description": "Read the person's OWN uploaded photo or video (context.upload). A video is transcribed (studio.media "
                                        "transcribe) so the post is about what is said in it; with no link, the upload (its transcript and the person's "
                                        "own description) is the source. Call it first whenever there is an upload. It is their media: render_reel uses "
                                        "it with rights_confirmed.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "x_oembed", "description": "Read an X/Twitter post through the public oEmbed endpoint: author, handle, text, date. No scraping, no login.",
     "input_schema": {"type": "object", "required": ["url"], "properties": {"url": S}}},
    {"name": "search_news", "description": "Find recent stories for a topic (Bing News, Hacker News, the person's feeds). Use only when the person gave no link.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {"query": S}}},
    {"name": "extract_facts", "description": "Pull the headline, 3 facts, why it matters and a label from the source, and cross-check money figures against other outlets. A conflict holds the post and asks the person.",
     "input_schema": {"type": "object", "properties": {"headline": {"type": "string", "description": "Optional headline you wrote (max 12 words, only facts from the source)"},
                                                       "facts": {"type": "array", "items": S}, "why": S}}},
    {"name": "render_cover", "description": "Render the cover card (4:5) in the brand for text-first channels.", "input_schema": {"type": "object", "properties": {"channel_ids": IDS}}},
    {"name": "render_carousel", "description": "Render a carousel (4:5): cover, the post card or facts, end card last.", "input_schema": {"type": "object", "properties": {"channel_ids": IDS}}},
    {"name": "render_reel", "description": "Render a 9:16 Reel: the headline on every frame, the source clip if there is one, the end card last. seconds sets a target length. "
                                       "style edits the real video with the media pipeline (studio.media): opus = auto-picked best moment with big word captions "
                                       "(short punchy explainers), news = HuhByte layout with the full video in a panel and a facts card, minimal = calm "
                                       "tutorial look, hype = fast one-word captions. It uses the clip from the link or the video find_media found. "
                                       "Someone else's video needs rights_confirmed: if the result says needs_confirmation, finish and ask the person the question.",
     "input_schema": {"type": "object", "properties": {"channel_ids": IDS, "seconds": {"type": "integer"}, "captions": {"type": "boolean"},
                                                       "ratio": {"type": "string", "enum": ["9:16", "4:5", "1:1"],
                                                                 "description": "9:16 for Instagram/YouTube/TikTok; 4:5 (or 1:1) for LinkedIn/Facebook feeds"},
                                                       "style": {"type": "string", "enum": ["opus", "news", "minimal", "hype"]},
                                                       "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]},
                                                       "rights_confirmed": {"type": "boolean", "description": "The person confirmed they may repost this video (with credit)"}}}},
    {"name": "add_end_card", "description": "Set the end card line for every future video (a Style Ledger rule), e.g. 'Follow @huhbyte'.",
     "input_schema": {"type": "object", "required": ["text"], "properties": {"text": S}}},
    {"name": "edit_brand", "description": "Change the brand kit from plain words: 'make the logo smaller', 'use #FF5A1F', 'switch font to Poppins'. Records a ledger rule.",
     "input_schema": {"type": "object", "required": ["instruction"], "properties": {"instruction": S}}},
    {"name": "edit_channel_media", "description": "Re-render ONLY one channel's media: seconds (Reel length), format (reel|carousel|photo), captions on/off.",
     "input_schema": {"type": "object", "required": ["channel"], "properties": {"channel": {"type": "string", "description": "channel id or network name, e.g. youtube"},
                                                                              "seconds": {"type": "integer"}, "format": S, "captions": {"type": "boolean"}}}},
    {"name": "write_captions", "description": "Write one caption per channel in the brand voice and the ledger's text rules. tone: formal|punchy|casual|explainer. hook: a new headline.",
     "input_schema": {"type": "object", "properties": {"channel_ids": IDS, "tone": S, "hook": S}}},
    {"name": "save_buffer_drafts", "description": "Save the ready packages as Buffer DRAFTS (never published). Idempotent per run and channel. Refused while a fact conflict is open.",
     "input_schema": {"type": "object", "properties": {"channel_ids": IDS}}},
    {"name": "remember_rule", "description": "Turn a standing instruction into a Style Ledger rule that applies to every future post ('end every video with Follow @x', 'never say game-changer').",
     "input_schema": {"type": "object", "required": ["correction"], "properties": {"correction": S}}},
    {"name": "find_media", "description": "Find the REAL launch / demo video for the story on any platform (X, YouTube, Vimeo, Loom, YC launch pages, HN, GitHub, the company's own site). "
                                      "Returns the best video, the best one the worker can fetch (with its credit line) and images. Call before render_reel with a style.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "A link or the story; omit to use the person's link"}}}},
    {"name": "generate_images", "description": "No usable video or photo: make n images for the story (the configured image model, else on-brand cards), labelled as AI-generated. A styled render_reel then uses them.",
     "input_schema": {"type": "object", "properties": {"prompt": S, "n": {"type": "integer"}}}},
    {"name": "finish", "description": "Stop: everything the person asked for is rendered, captioned and checked. message: one or two sentences for the person.",
     "input_schema": {"type": "object", "required": ["message"], "properties": {"message": S}}},
]
CHECK_SPEC = {"name": "check", "description": "Check every channel (brand rules, clipped text, about the source, fact conflicts) and build the packages. Call after write_captions.",
              "input_schema": {"type": "object", "properties": {}}}
LABELS = {"read_upload": "Read your upload", "read_url": "Read your link", "x_oembed": "Read the X post", "search_news": "Search the news", "extract_facts": "Pull out the facts",
          "render_cover": "Render the cover", "render_carousel": "Render the carousel", "render_reel": "Render the Reel",
          "add_end_card": "Set the end card", "edit_brand": "Update your brand kit", "edit_channel_media": "Re-render one channel",
          "write_captions": "Write captions", "save_buffer_drafts": "Save Buffer drafts", "remember_rule": "Remember a rule",
          "find_media": "Find media", "generate_images": "Make images", "check": "Check everything", "finish": "Done"}
WRITES = {"generate_images", "render_cover", "render_carousel", "render_reel", "add_end_card", "edit_brand", "edit_channel_media", "write_captions",
          "save_buffer_drafts", "remember_rule"}


# Tone drives the caption prompt (QA: "short punchy explainer" came out as plain explainers). Several tones can combine.
TONE_GUIDE = {
    "punchy": "PUNCHY: the first line is a scroll-stopping hook (the most surprising fact, figure or a sharp question, max 10 words). "
              "Short sentences, most under 12 words. Active verbs and energy. No filler or throat-clearing ('In a recent announcement...'). "
              "One idea per line, line breaks between ideas.",
    "explainer": "EXPLAINER: say what happened, then why it matters, in plain words a friend would get. No jargon.",
    "formal": "FORMAL: measured, professional, complete sentences, no slang or emoji.",
    "casual": "CASUAL: friendly and conversational, like telling a friend.",
    "short": "SHORT: well under the limit (LinkedIn under 600 characters, everywhere else under 300).",
}
TONE_WORDS = {"punchy": r"punchy|snappy|sharp|hype|hook|energ", "explainer": r"explain|simple|eli5|beginner",
              "formal": r"formal|professional|corporate", "casual": r"casual|friendly|\bfun\b", "short": r"\bshort\b|\bbrief\b|concise|tight"}
CHANNEL_STYLE = {
    "linkedin": "hook line, blank line, 2-3 short paragraphs (1-2 sentences each), end with a takeaway or question, then the link; at most 3 hashtags.",
    "facebook": "conversational hook line, 2-4 short lines, then the link.",
    "instagram": "the hook is the first line (it shows before 'more'), short lines with breaks, end with 'Link in bio' instead of the URL, 3-5 hashtags.",
    "youtube": "description only: the video title is set separately to the headline, so never repeat the headline; open with a hook sentence, "
               "2-3 short lines, then the link.",
    "tiktok": "hook line, 1-2 short lines, 3-5 hashtags.",
    "twitter": "one punchy hook line plus the link, under 280 characters.",
    "threads": "hook line, one short follow-up line, then the link.",
    "bluesky": "hook line plus the link, under 300 characters.",
    "mastodon": "hook line, one short line, the link, 1-2 hashtags.",
}


_URLISH = re.compile(r"https?://\S+|\b(?:www\.)?[\w-]+\.(?:com|ai|io|co|org|net|dev|app|be)/\S*|\b(?:x|twitter)\.com\b|/status/\d+", re.I)
_ORDERS = re.compile(r"\b(?:search (?:it|for|online)|research|look (?:it )?up|post (?:it |this )?on|all (?:my |the )?channels|you can|"
                     r"can you|please|make (?:a|me|it)|have a|reel post|instagram reel|insta(?:gram)? post|do that|online)\b", re.I)


def safe_headline(headline: str, prompt: str = "", fallbacks: tuple | list = ()) -> str:
    """A headline is written from the story, never copied from the person's request. Owner bug, 1 Nov 2026: the prompt
    "YOU CAN DO THAT ONLINE SEARCH IT IS HTTPS://X.COM/... RESEARCH AND HAVE A INSTGRAM..." was burned into a Reel.
    Refuses (falls through to the next candidate) anything with a URL, request wording, or a long run copied from the
    prompt."""
    pw = re.findall(r"[a-z0-9$€£₹.%]+", (prompt or "").lower())
    for cand in [headline, *fallbacks]:
        h = _URLISH.sub(" ", str(cand or ""))
        h = re.sub(r"\s+", " ", h).strip(" -–—:;,.\"'“”")
        if len(re.findall(r"\w{2,}", h)) < 2 or _ORDERS.search(h):
            continue
        hw = re.findall(r"[a-z0-9$€£₹.%]+", h.lower())
        if pw and len(hw) >= 8 and " ".join(hw[:8]) in " ".join(pw):
            continue  # a long verbatim run of the prompt: their words, not a headline
        return h[:120]
    return ""


def tone_keys(*texts: str) -> list[str]:
    """Every tone named in the tone argument / the person's prompt, in a stable order ("short punchy explainer" -> all three)."""
    t = " ".join(x for x in texts if x).lower()
    return [k for k, pat in TONE_WORDS.items() if re.search(pat, t)]


def tone_instructions(*texts: str) -> str:
    return " ".join(TONE_GUIDE[k] for k in tone_keys(*texts))


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9$.]+", (s or "").lower())) - {"the", "a", "an", "and", "of", "to", "has", "have", "is"}


def _same(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.75


def dedupe_youtube(text: str, title: str) -> str:
    """YouTube gets its title as a separate field, so the description never opens with the headline or says it twice."""
    lines = (text or "").strip().split("\n")
    if len(lines) > 1 and lines[0].strip() and (_same(lines[0], title) or _same(lines[0], next((x for x in lines[1:] if x.strip()), ""))):
        rest = "\n".join(lines[1:]).strip()
        if rest:
            return rest
    return (text or "").strip()


def tidy(text: str, story: dict | None = None) -> str:
    """Copy pass: plain hyphens/spaces the fonts and every network render, money keeps its currency sign."""
    t = (text or "").translate(str.maketrans({"\u2011": "-", "\u2010": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " "}))
    for f in (story or {}).get("facts") or []:
        for m in re.finditer(r"([$€£₹])(\d[\d.,]*\s?(?:[kKmMbB]n?\b|million|billion)?)", f):
            bare = m.group(2).strip()
            t = re.sub(rf"(?<![$€£₹\d.]){re.escape(bare)}(?![\d])", m.group(1) + bare, t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


class Tools:
    def __init__(self, house, actor, rid: int):
        self.h, self.a, self.rid = house, actor, rid
        self.studio, self.store = house.studio, house.store
        self.demo = demo_guard.is_demo(actor.uid)

    @property
    def s(self):
        return self.h.s

    # ---------------------------------------------------------------- state
    def state(self) -> dict:
        return self.h.run(self.a, self.rid)

    def save(self, run: dict, status: str | None = None) -> None:
        self.h._save(self.rid, run["steps"], run["result"], status)

    def guard(self, tool: str) -> None:
        """Kill switch for every brain action (even when the person started it) + an agent token's daily cap."""
        u = self.store.user(self.a.uid) or {}
        if u.get("kill_switch"):
            raise ActionError("Agent actions are paused: the kill switch is on in HuhQueue.", 423)
        if tool in WRITES:
            self.studio._guard_write(self.a, f"agent_{tool}")

    def channels(self, run: dict, ids=None) -> list[dict]:
        return [c for c in run["input"]["channels"] if not ids or c["id"] in ids or c["service"] in ids]

    def run_tool(self, name: str, args: dict) -> dict:
        if name not in {s["name"] for s in SPECS} | {"check"}:
            raise ActionError(f"Unknown tool {name}")
        self.guard(name)
        fn = getattr(self, "t_" + name)
        names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
        return fn(**{k: v for k, v in (args or {}).items() if k in names})

    def model_json(self, purpose: str, system: str, user: str, max_tokens: int = 900):
        """A fast model step (Claude Haiku on the operator key, budgeted; or the person's own key) returning JSON, or None
        so the caller uses the free model / rule writer. Never for the free sample. An answer cut by max_tokens is never
        used as if it were complete: it is retried once with twice the room, then dropped (self.truncated says so)."""
        self.truncated = False
        if self.demo:
            return None
        from . import brain_llm, budget
        for eng in brain_llm.engines(self.store, self.a.uid, self.s, self.h.http):
            if eng["source"] not in ("huhqueue claude", "your key"):
                continue
            room = max_tokens
            for _ in range(2):
                try:
                    out = brain_llm.call(eng, self.store, self.a.uid, self.a.label, system=system + " Reply with JSON only.",
                                         messages=[{"role": "user", "content": user}], max_tokens=room, fast=True, purpose=purpose,
                                         http=self.h.http, timeout=60)
                except (budget.BudgetExceeded, llm.LLMError):
                    break
                if out.get("stop_reason") == "max_tokens":
                    self.truncated, room = True, room * 2
                    continue
                self.truncated = False
                try:
                    return llm.parse_json(out["text"])
                except llm.LLMError:
                    break
        return None

    def thread(self, run: dict, role: str, text: str) -> None:
        run["result"].setdefault("thread", []).append({"role": role, "text": text[:2000], "at": time.time()})

    # ---------------------------------------------------------------- read
    def _x_bearer(self) -> str:
        try:
            rows = self.store.q("SELECT secret FROM source_settings WHERE user_id=?", (self.a.uid,))
        except Exception:
            return ""
        sec = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        return sec.get("x_bearer", "")

    def t_read_url(self, url: str) -> dict:
        run = self.state()
        res = run["result"]
        try:
            url = netguard.check_url(intake.clean_url(url))
        except netguard.FetchError as e:
            raise ActionError(f"That link isn't allowed: {e}")
        try:
            art = reader.read_url(url, self.h.http, "" if self.demo else self._x_bearer())
        except reader.ReadError as e:
            raise ActionError(str(e))
        res["source"] = {k: art.get(k) for k in ("url", "title", "description", "image", "site", "via", "post", "media", "media_note")}
        res["source"]["paragraphs"] = (art.get("paragraphs") or [])[:10]
        self.save(run)
        out = {"site": art["site"], "title": art["title"], "via": art.get("via"), "words": len(" ".join(art.get("paragraphs") or []).split())}
        if art.get("post"):
            out.update(author=art["post"]["author_name"], handle=art["post"]["handle"], text=art["post"]["text"][:400],
                       media=[m["type"] for m in art.get("media") or []], media_note=art.get("media_note", ""))
        return out

    def t_read_upload(self) -> dict:
        """The person's own photo/video as an input: transcribe a video, and with no link make it the source."""
        run = self.state()
        res = run["result"]
        up = res.get("upload")
        if not up:
            raise ActionError("There's no upload in this post.")
        if up["type"] == "video" and not up.get("transcript_tried"):
            text, note = self.h.transcribe(self.a, self.rid, up["token"])
            run = self.state()
            res = run["result"]
            up = res["upload"]
            up.update(transcript_tried=True, transcript=(text or "")[:8000], transcript_note=note)
        up["read"] = True
        it = run["input"]["intake"]
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", up.get("transcript") or "") if len(x.split()) >= 3][:12]
        desc = (run["input"].get("brief") or "").strip() or it.get("topic") or ""
        if not res.get("source") and not it.get("source_url") and not (it.get("research") and it.get("topic")) and (sents or desc):
            # no link and nothing to research: the upload itself is the story (its words, and the person's own line about it)
            what = "video" if up["type"] == "video" else "photo"
            res["source"] = {"url": "", "title": (sents[0] if sents else desc)[:140], "description": desc[:300], "image": "",
                             "site": f"Your {what}", "via": "upload", "post": None, "media": [], "media_note": "",
                             "paragraphs": ([desc] if desc else []) + sents}
        self.save(run)
        return {"type": up["type"], "name": up.get("name", ""), "transcript": (up.get("transcript") or "")[:600],
                "transcript_words": len((up.get("transcript") or "").split()), "note": up.get("transcript_note", ""),
                "is_source": bool((res.get("source") or {}).get("via") == "upload")}

    def t_x_oembed(self, url: str) -> dict:
        if not reader.x_status(url):
            raise ActionError("That isn't a link to an X post.")
        return self.t_read_url(url)

    def t_search_news(self, query: str) -> dict:
        run = self.state()
        leads = sources.search(query, self.studio.feeds(self.a), http=self.h.http)
        run["result"]["leads"] = leads[:6]
        self.save(run)
        if not leads:
            raise ActionError(f"No recent stories found for “{query}”. Try a broader topic or paste a link.")
        return {"leads": [{"title": l["title"], "url": l["url"], "source": l.get("source")} for l in leads[:6]]}

    def t_find_media(self, query: str = "") -> dict:
        """studio.media.tools.media_find (docs/MEDIA_PIPELINE.md): the source cascade, every fetch through netguard."""
        try:
            from .media import tools as media_tools
        except Exception:  # noqa: BLE001
            return {"found": [], "note": "The media finder isn't installed on this server; I used the source's own media."}
        run = self.state()
        res = run["result"]
        q = query or run["input"]["intake"].get("source_url") or (res.get("story") or {}).get("headline") or ""
        if not q:
            raise ActionError("Read a source first (read_url or search_news), or give find_media a link.")
        try:
            got = media_tools.media_find(q, http=self.h.http)
        except Exception as e:  # noqa: BLE001
            return {"found": [], "note": f"Media finder failed ({e.__class__.__name__})."}
        res["found_media"] = got
        self.save(run)
        bv, bd = got.get("best_video"), got.get("best_downloadable")
        return {"best_video": bv, "best_downloadable": bd, "images": got.get("images", [])[:3], "note": got.get("note", ""),
                "needs_rights_confirmation": bool(bd and not bd.get("official"))}

    def t_generate_images(self, prompt: str = "", n: int = 3) -> dict:
        from .media import imagegen
        import tempfile
        run = self.state()
        st = self._story(run)
        kit = self._ctx()[0]
        from .media.worker import brand_from_kit
        with tempfile.TemporaryDirectory(prefix="hq_gen_") as d:
            imgs = imagegen.generate_images(prompt or st["headline"], d, n=max(1, min(int(n or 3), 4)), ratio="9:16",
                                            headline=st["headline"], brand=brand_from_kit(kit), http=self.h.http)
            out = []
            for im in imgs:
                with open(im["path"], "rb") as fh:
                    tok = self.h.put_media(self.a, fh.read(), "generated.jpg", kind="render", meta={"type": "photo", "credit": im["credit"]})
                out.append({"token": tok, "url": self.h.media_url(tok), "type": "photo", "credit": im["credit"], "provider": im["provider"]})
        run = self.state()
        run["result"]["generated_images"] = out
        self.save(run)
        return {"images": [m["url"] for m in out], "providers": sorted({m["provider"] for m in out}), "credit": out[0]["credit"] if out else ""}

    # ---------------------------------------------------------------- facts
    def t_extract_facts(self, headline: str = "", facts: list | None = None, why: str = "") -> dict:
        run = self.state()
        res = run["result"]
        src = res.get("source")
        if not src:
            raise ActionError("Read a source first (read_url or search_news).")
        article = {"url": src["url"], "title": src["title"], "description": src.get("description", ""), "image": src.get("image", ""),
                   "site": src["site"], "paragraphs": src.get("paragraphs") or []}
        post = src.get("post")
        if headline and facts:
            story = {"headline": str(headline)[:120], "facts": [str(f)[:160] for f in facts][:4], "why": str(why or "")[:200], "label": "News",
                     "source": src["site"], "url": src["url"], "image": src.get("image", ""), "engine": "agent brain"}
        else:
            text = " ".join(article["paragraphs"])[:3000]
            d = self.model_json("facts", "You are a careful news editor. Use only facts in the source; never invent or change numbers.",
                                "Return JSON {headline (max 12 words, plain, no hype, keep figures exactly as written), facts: [3 short facts], "
                                "why_it_matters (one sentence), label (1-2 words)}. The headline comes from the story, never from the "
                                "person's request; no URLs.\nSOURCE: " + json.dumps(
                                    {"title": article["title"], "description": article["description"], "text": text,
                                     "post_by": (post or {}).get("author_name"),
                                     "video_transcript": ((res.get("upload") or {}).get("transcript") or "")[:2500]}))
            facts_ = [str(f)[:160] for f in ((d or {}).get("facts") or []) if str(f).strip()][:4]
            if d and d.get("headline") and facts_:
                story = {"headline": str(d["headline"])[:120], "facts": facts_, "why": str(d.get("why_it_matters") or "")[:200],
                         "label": str(d.get("label") or "News")[:16], "source": src["site"], "url": src["url"], "image": src.get("image", ""),
                         "engine": "claude"}
            else:
                story = self.h._structure(self.a, llm.resolve(self.store, self.a.uid, self.s), article)
        story["url"] = src["url"]  # the person's link stays the source of record
        if post:
            story["source"] = f"X @{post['handle']}"
            if story.get("label") in (None, "", "News"):
                story["label"] = "Launch"
            if re.search(r"\bon x\b|\(@\w+\)", story["headline"], re.I):
                story["headline"] = re.split(r"(?<=[.!?])\s+", post["text"])[0][:110]
        prompt = (run.get("input") or {}).get("prompt", "")
        story["headline"] = tidy(safe_headline(story.get("headline", ""), prompt,
                                               [article["title"], *(story.get("facts") or []), (post or {}).get("text", "")])
                                 or (f"New from {story['source']}" if story.get("source") else "What's new"), story)
        story["why"] = tidy(story.get("why", ""), story)
        story["facts"] = [tidy(f, story) for f in story["facts"]]
        res["story"] = story
        # the person's source stays primary: its own words (the post text too) are what every other outlet is compared with
        primary = {"source": story["source"], "url": src["url"],
                   "text": " ".join([story["headline"], *story["facts"], article["description"], (post or {}).get("text", "")])}
        others = self._corroborating(article, story) if not self.demo else []  # cross-source figure check: never guess
        res["other_outlets"] = [{"source": o["source"], "url": o["url"]} for o in others]
        done = {r.get("ours") for r in res.get("resolved") or []}
        res["conflicts"] = [c for c in factcheck.conflicts(primary, others) if c["ours"] not in done]
        if res["conflicts"]:
            res["needs_answer"] = res["conflicts"][0]["question"]
            self.thread(run, "agent", res["needs_answer"])
        self.save(run)
        return {"headline": story["headline"], "facts": story["facts"], "why": story.get("why", ""),
                "also_reported_by": [o["source"] for o in others], "conflicts": res["conflicts"]}

    def _corroborating(self, article: dict, story: dict) -> list[dict]:
        """Other outlets on the same story, each read for its figures. Searched by the story's headline first (an X post's
        own title is the founder's first sentence, which rarely names the company), then by the source title. Runs both
        searches whenever the story states a figure, so "$1.3M" vs "$13 million" is surfaced, never silently picked."""
        figures = factcheck.amounts(" ".join([story.get("headline", ""), *story.get("facts", [])]))
        titles = [story.get("headline", ""), article.get("title", "")] if figures else [story.get("headline") or article.get("title", "")]
        leads, seen = [], set()
        for t in dict.fromkeys(x for x in titles if x):
            try:
                found = self.h._corroborate({**article, "title": t})
            except Exception:
                found = []
            for c in found:
                if c.get("url") and c["url"] not in seen:
                    seen.add(c["url"])
                    leads.append(c)
            if len(leads) >= 3:
                break
        others = []
        for c in leads[:3]:
            txt = c.get("title", "") + " " + c.get("description", "")
            try:
                oa = reader.read_article(sources.resolve_google_link(c["url"], self.h.http), self.h.http)
                txt += " " + (oa.get("description") or "") + " " + " ".join(oa.get("paragraphs") or [])[:2000]
            except Exception:
                pass  # the headline alone still carries the figure most of the time
            others.append({"source": c.get("source") or urlparse(c["url"]).hostname, "url": c["url"], "text": txt})
        return others

    # ---------------------------------------------------------------- rendering
    def _ctx(self):
        kit, rules = self.h.kit(self.a), self.h.rules(self.a)
        return kit, rules, brand.visual_params(kit, rules), self.h._logo_bytes(kit)

    def _story(self, run) -> dict:
        st = run["result"].get("story")
        if not st:
            raise ActionError("Pull out the facts first (extract_facts).")
        return st

    def _hero(self, run) -> tuple[bytes | None, str]:
        res = run["result"]
        story = res.get("story") or {}
        up = res.get("upload") or {}
        if up.get("type") == "photo":  # their own photo is the picture on the cover, always (never judged or swapped)
            m = self.h.get_media(up["token"])
            if m:
                return m["data"], "hero"
        url = story.get("image") or (res.get("source") or {}).get("image") or ""
        if not url:
            return None, "none"
        data = self.h._fetch_bytes(url, 8 * 1024 * 1024)
        if not data:
            return None, "none"
        cache = res.setdefault("hero_judgment", {})
        if url not in cache:
            j = vision.judge_candidate(data, story.get("headline", "") + ". " + " ".join(story.get("facts") or []), self.demo, self.h.http)
            cache[url] = {**j, "use": vision.decide_hero(j)}
            self.save(run)
        use = cache[url]["use"]
        return (data if use != "skip" else None), use

    def _keep(self, data: bytes, name: str, meta: dict, role: str = "") -> dict:
        tok = self.h.put_media(self.a, data, name, kind="render", meta=meta)
        out = {"token": tok, "url": self.h.media_url(tok), "type": meta["type"], "name": name}
        if role:
            out["role"] = role
        return out

    def _set_media(self, run, ids: list[str], media: list[dict], fmt: str, extra: dict | None = None) -> None:
        res = run["result"]
        cm = res.setdefault("channel_media", {})
        for cid in ids:
            cm[cid] = {"format": fmt, "media": media, **(extra or {})}
        if media:
            try:  # every finished channel render is kept in the person's Library (studio/library.py)
                self.h.library.register_generated(self.a, media, fmt, self.rid)
            except Exception:  # noqa: BLE001  the Library never breaks a run
                pass
        seen, allm = set(), []
        for v in cm.values():
            for m in v["media"]:
                if m["url"] not in seen:
                    seen.add(m["url"])
                    allm.append(m)
        res["media"] = allm

    @staticmethod
    def _hi(headline: str) -> list[str]:
        return re.findall(r"[$€£₹]\S+|\d[\d.,]*%?", headline)

    def _slides_45(self, run, carousel: bool) -> list[dict]:
        kit, rules, p, logo = self._ctx()
        st = self._story(run)
        post = (run["result"].get("source") or {}).get("post")
        img, use = self._hero(run)
        total = (3 if post else 2) + (1 if p.get("end_card") else 0)
        b, m = render.news_card(st["headline"], kit, dict(p), image=img if use == "hero" else None, inset=img if use == "inset" else None,
                                logo=logo, ratio="4:5", label=st.get("label", ""), credit=st["source"], n=(1, total) if carousel else None,
                                hi=self._hi(st["headline"]))
        out = [self._keep(b, "cover.jpg", m)]
        if not carousel:
            return out
        k = 2
        if post:
            b, m = render.post_slide(post, kit, dict(p), logo=logo, ratio="4:5", n=(k, total))
            out.append(self._keep(b, "post.jpg", m))
            k += 1
        bullets = (st["facts"] + ([st["why"]] if st.get("why") else []))[:4]
        b, m = render.fact_slide("Why it matters" if st.get("why") else "What happened", bullets, kit, dict(p), logo=logo, ratio="4:5", n=(k, total))
        out.append(self._keep(b, "facts.jpg", m))
        if p.get("end_card"):
            b, m = render.end_card(kit, dict(p), logo=logo, ratio="4:5", n=(total, total))  # N/N like every other slide
            out.append(self._keep(b, "follow.jpg", m))
        return out

    def _clipped(self, media: list[dict]) -> list:
        bad = []
        for m in media:
            meta = self.h.media_meta(self.a, m["token"]) if m.get("token") else None
            if meta and hbstyle.overflow(meta, (meta.get("width") or 1080, meta.get("height") or 1350)):
                bad.append(m["url"])
        return bad

    def t_render_cover(self, channel_ids: list | None = None) -> dict:
        run = self.state()
        ids = [c["id"] for c in self.channels(run, channel_ids)]
        media = self._slides_45(run, carousel=False)
        self._set_media(run, ids, media, "photo")
        self.save(run)
        return {"channels": ids, "format": "photo", "media": [m["url"] for m in media], "clipped": self._clipped(media)}

    def t_render_carousel(self, channel_ids: list | None = None) -> dict:
        run = self.state()
        ids = [c["id"] for c in self.channels(run, channel_ids) if c["service"] not in VIDEO_ONLY]
        media = self._slides_45(run, carousel=True)
        self._set_media(run, ids, media, "carousel")
        self.save(run)
        return {"channels": ids, "format": "carousel", "media": [m["url"] for m in media], "clipped": self._clipped(media)}

    def t_render_reel(self, channel_ids: list | None = None, seconds: int | None = None, captions: bool | None = None,
                      style: str | None = None, music: str | None = None, rights_confirmed: bool | None = None,
                      ratio: str | None = None) -> dict:
        run = self.state()
        ids = [c["id"] for c in self.channels(run, channel_ids)]
        ratio = ratio if ratio in ("9:16", "4:5", "1:1") else "9:16"
        if any(c["service"] in VIDEO_ONLY for c in self.channels(run, ids)):
            ratio = "9:16"  # YouTube Shorts / TikTok are vertical only
        if style:
            media, note, ask = self._media_reel(run, style, seconds=seconds, music=music, rights_confirmed=bool(rights_confirmed), ratio=ratio)
            run = self.state()
            fmt = "reel" if media and media[0]["type"] == "video" else "carousel"
            self._set_media(run, ids, media, fmt, {"seconds": seconds, "captions": True, "style": style, "note": note, "ratio": ratio})
            self.save(run)
            out = {"channels": ids, "format": fmt, "media": [m["url"] for m in media], "note": note, "style": style,
                   "clipped": self._clipped([m for m in media if m["type"] == "photo"])}
            if ask:
                out.update(needs_confirmation=True, question=ask)
            return out
        media, note = self._reel(run, seconds=seconds, captions=bool(captions), ratio=ratio)
        run = self.state()
        fmt = "reel" if media and media[0]["type"] == "video" else "carousel"
        self._set_media(run, ids, media, fmt, {"seconds": seconds, "captions": bool(captions), "note": note, "ratio": ratio})
        self.save(run)
        return {"channels": ids, "format": fmt, "media": [m["url"] for m in media], "note": note,
                "clipped": self._clipped([m for m in media if m["type"] == "photo"])}

    def _reel_frames(self, run) -> list[dict]:
        """9:16 frames for a slideshow Reel; the story headline is on every one (the end card is added last)."""
        kit, rules, p, logo = self._ctx()
        st = self._story(run)
        post = (run["result"].get("source") or {}).get("post")
        img, use = self._hero(run)
        b, m = render.news_card(st["headline"], kit, dict(p), image=img if use == "hero" else None, inset=img if use == "inset" else None,
                                logo=logo, ratio="9:16", label=st.get("label", ""), credit=st["source"], hi=self._hi(st["headline"]))
        frames = [self._keep(b, "r_cover.jpg", m, "slide")]
        if post:
            b, m = render.post_slide(post, kit, dict(p), logo=logo, ratio="9:16", headline=st["headline"])
            frames.append(self._keep(b, "r_post.jpg", m, "slide"))
        bullets = (st["facts"] + ([st["why"]] if st.get("why") else []))[:4]
        b, m = render.fact_slide("Why it matters" if st.get("why") else "What happened", bullets, kit, dict(p), logo=logo, ratio="9:16",
                                 headline=st["headline"])
        frames.append(self._keep(b, "r_facts.jpg", m, "slide"))
        return frames

    def _reel(self, run, seconds=None, captions=False, ratio: str = "9:16") -> tuple[list[dict], str]:
        """Render (or have the worker render) the Reel and wait for it. Never substitutes a sample clip: when no video
        can be made, the channel gets the slides and the note says why."""
        kit, rules, p, logo = self._ctx()
        st = self._story(run)
        res = run["result"]
        src = res.get("source") or {}
        clip_tok = res.get("clip_token") or ""
        if not clip_tok:
            vid = next((m for m in src.get("media") or [] if m["type"] == "video"), None)
            if vid:
                data = self.h._fetch_bytes(vid["url"], reader.MAX_MEDIA)
                if data:
                    clip_tok = self.h.put_media(self.a, data, "source.mp4", kind="upload", meta={"type": "video"})
                    res["clip_token"] = clip_tok
                    self.save(run)
        frames = [] if clip_tok else self._reel_frames(run)
        note_extra = " Word-by-word captions need a transcript: use One take for talking clips." if captions and clip_tok else ""
        spec = {"kind": "brand_video" if clip_tok else "slideshow", "kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "params": p,
                "ratio": ratio if clip_tok else "9:16", "hook": st["headline"], "logo": kit.get("logo") or "", "source": clip_tok,
                "slides": [m["token"] for m in frames], "total": seconds}
        return self._video_job(run, spec, frames, note_extra)

    def _video_job(self, run, spec: dict, frames: list[dict], note_extra: str = "") -> tuple[list[dict], str]:
        """Queue one video render (local FFmpeg off the hosted app, else the GitHub render worker) and wait for it."""
        mode = self.h.video_mode()  # never "local" on the hosted app (512 MB): Reels go to the GitHub render worker there
        if self.demo:
            mode = "local" if self.h.local_video_ok() and demo_guard.take(self.store, self.a.uid, "video") else "none"
        own = self._own_clip(run, spec)
        if mode == "none":
            if own:
                return own, "Your video as uploaded: there's no video renderer here, so it isn't re-cut with your brand. Reply “redo the reel” to try again."
            return frames or self._slides_45(run, carousel=True), "No video renderer here, so this channel gets the slides instead."
        jid = self.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                           (self.a.uid, self.rid, json.dumps(spec), "queued", time.time(), time.time()))
        if mode == "local":
            try:
                data, meta = self.h.render_job_local(spec)
                self.h.finish_video(jid, data, meta, run_inline=False)
                return [self.h._job_media(jid)], "Rendered on this server." + note_extra
            except Exception as e:
                self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (str(e)[:500], time.time(), jid))
                if own:
                    return own, f"The branded render failed ({str(e)[:120]}), so this is your video as uploaded."
                return frames or self._slides_45(run, carousel=True), f"The video render failed ({str(e)[:120]}); slides instead."
        ok, why = self.h._dispatch_github(jid)
        if not ok:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, time.time(), jid))
            if own:
                return own, f"The render worker didn't start ({why}), so this is your video as uploaded."
            return frames or self._slides_45(run, carousel=True), f"The render worker didn't start ({why}); slides instead."
        t0 = time.time()
        while time.time() - t0 < VIDEO_WAIT:  # the brain waits here; 'updated' stays fresh so the watchdog knows it's alive
            time.sleep(0.05 if self.h.sync else 3)
            row = self.store.q("SELECT status, error FROM render_jobs WHERE id=?", (jid,))[0]
            self.store.x("UPDATE agent_runs SET updated=? WHERE id=?", (time.time(), self.rid))
            if row["status"] == "done":
                return [self.h._job_media(jid)], "Rendered by the video worker." + note_extra
            if row["status"] == "failed":
                if own:
                    return own, f"The branded render failed ({(row['error'] or '')[:120]}), so this is your video as uploaded."
                return frames or self._slides_45(run, carousel=True), f"The video render failed ({(row['error'] or '')[:120]}); slides instead."
        self.store.x("UPDATE render_jobs SET status='failed', error='timed out', updated=? WHERE id=?", (time.time(), jid))
        if own:
            return own, "The video worker took too long, so this is your video as uploaded. Reply “redo the reel” to try again."
        return frames or self._slides_45(run, carousel=True), "The video worker took too long; slides instead. Reply “redo the reel” to try again."

    def _own_clip(self, run, spec: dict) -> list[dict]:
        """The person's own uploaded video, as uploaded: the fallback when a branded render can't be made (never slides
        in place of a video they gave us)."""
        res = run["result"]
        tok = res.get("clip_token") or ""
        if not (res.get("clip_owned") and tok and spec.get("source") == tok):
            return []
        return [{"token": tok, "url": self.h.media_url(tok), "type": "video", "name": (res.get("upload") or {}).get("name") or "your video",
                 "role": "original"}]

    def _media_reel(self, run, style: str, seconds=None, music=None, rights_confirmed=False, ratio: str = "9:16") -> tuple[list[dict], str, str]:
        """A styled Reel from the real video via studio.media (worker kind ``media_render``). Returns (media, note, question):
        a third-party video without the person's OK is not rendered; the question asks for it (docs/MEDIA_PIPELINE.md, Risks)."""
        from .media import styles as media_styles
        sid = media_styles.resolve(style)
        if sid not in media_styles.STYLE_PRESETS:
            raise ActionError(f"Unknown style {style}. Pick opus, news, minimal or hype.")
        kit = self._ctx()[0]
        st = self._story(run)
        res = run["result"]
        src = res.get("source") or {}
        clip_tok = res.get("clip_token") or ""
        if not clip_tok:
            vid = next((m for m in src.get("media") or [] if m["type"] == "video"), None)
            if vid:
                data = self.h._fetch_bytes(vid["url"], reader.MAX_MEDIA)
                if data:
                    clip_tok = self.h.put_media(self.a, data, "source.mp4", kind="upload", meta={"type": "video"})
                    res["clip_token"] = clip_tok
                    self.save(run)
        found = res.get("found_media") if isinstance(res.get("found_media"), dict) else {}
        cand = None if clip_tok else found.get("best_downloadable")
        gen = res.get("generated_images") or []
        if clip_tok and res.get("clip_owned"):
            rights_confirmed = True  # the person's own upload: nothing to ask
        if not clip_tok and not cand and not gen:
            media, note = self._reel(run, seconds=seconds, ratio=ratio)
            return media, note + " No video to edit, so the styled edit wasn't used (find_media or generate_images first).", ""
        handle = ((src.get("post") or {}).get("handle") or "").lstrip("@")
        credit = (f"Video: @{handle} on X" if clip_tok and handle else
                  (cand or {}).get("credit") or (f"Video: {st.get('source')}" if clip_tok and st.get("source") else "")) if (clip_tok or cand) else gen[0]["credit"]
        official = bool(cand and cand.get("official"))
        if (clip_tok or cand) and not (official or rights_confirmed):
            ask = (f"This Reel uses someone else's video ({credit or 'no credit found'}). I'll credit it on screen and in the caption. "
                   "Do you have the right to repost it (or is it fair commentary)?")
            res["rights_question"] = ask
            self.save(run)
            frames = self._reel_frames(run)
            return frames or self._slides_45(run, carousel=True), "Slides for now: the video needs your OK first. " + ask, ask
        res.pop("rights_question", None)
        spec = {"kind": "media_render", "style": sid, "ratio": ratio, "hook": st["headline"], "facts": list(st.get("facts") or [])[:4],
                "credit": credit, "names": [n for n in (st.get("company"), (cand or {}).get("company")) if n], "music": music or "auto",
                "official": official, "rights_confirmed": bool(rights_confirmed or official),
                "kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "logo": kit.get("logo") or "", "source": clip_tok, "candidate": cand,
                "slides": [] if (clip_tok or cand) else [m["token"] for m in gen], "image_prompt": st["headline"]}
        if seconds and sid not in ("opus", "hype"):
            spec["clip"] = [0, float(seconds)]
        frames = [] if (clip_tok or cand) else gen
        media, note = self._video_job(run, spec, frames, f" Style: {sid}.")
        return media, note, ""

    # ---------------------------------------------------------------- edits
    def t_add_end_card(self, text: str) -> dict:
        out = self.h.remember(self.a, f"End every video with {text}")
        return {"rules": [r["rule"] for r in out.get("added") or []]}

    def t_remember_rule(self, correction: str) -> dict:
        out = self.h.remember(self.a, correction)
        return {"rules": [r["rule"] for r in out.get("added") or []], "message": out.get("message", "")}

    def t_edit_brand(self, instruction: str) -> dict:
        t = (instruction or "").strip()
        patch, changed = {}, []
        hexes = brand.HEX_RE.findall(t)
        if hexes:
            h = "#" + hexes[0].upper().lstrip("#")
            key = "primary" if re.search(r"background|ground|primary", t, re.I) else "accent"
            patch["colors"] = {key: h}
            changed.append(f"{'accent' if key == 'accent' else 'ground'} colour {h}")
        fm = re.search(r"font (?:to |as |= ?)?([a-z]+)|([a-z]+) font", t, re.I)
        if fm:
            name = (fm.group(1) or fm.group(2)).lower()
            if name in brand.FONTS:
                patch["font"] = name
                changed.append(f"headline font {name.title()}")
            elif name not in ("the", "a", "headline", "body", "switch", "change", "my", "brand"):
                return {"changed": [], "message": f"I don't have {name.title()} yet. Fonts I can use: {', '.join(f.title() for f in brand.FONTS)}."}
        if patch:
            self.h.save_kit(self.a, patch)
        learned = self.h.remember(self.a, t) if re.search(r"logo|end ?card|case|overlay|credit|words|#[0-9a-f]{6}|font", t, re.I) else {"added": []}
        changed += [r["rule"] for r in learned.get("added") or [] if r["rule"] not in changed]
        return {"changed": changed or ["nothing I could apply"],
                "kit": {k: v for k, v in self.h.kit(self.a).items() if k in ("colors", "font", "name", "handle")}}

    def t_edit_channel_media(self, channel: str, seconds: int | None = None, format: str | None = None, captions: bool | None = None) -> dict:
        run = self.state()
        ch = next((c for c in run["input"]["channels"] if c["id"] == channel or c["service"] == str(channel).lower()), None)
        if not ch:
            raise ActionError(f"No {channel} channel in this post.")
        cur = (run["result"].get("channel_media") or {}).get(ch["id"], {})
        fmt = (format or ("reel" if seconds or captions else cur.get("format")) or "carousel").lower()
        if ch["service"] in VIDEO_ONLY:
            fmt = "reel"
        if fmt in ("reel", "video"):
            r = self.t_render_reel([ch["id"]], seconds=seconds or cur.get("seconds"),
                                   captions=captions if captions is not None else cur.get("captions"))
        elif fmt == "carousel":
            r = self.t_render_carousel([ch["id"]])
        else:
            r = self.t_render_cover([ch["id"]])
        return {"channel": ch["service"], **r}

    # ---------------------------------------------------------------- captions
    def t_write_captions(self, channel_ids: list | None = None, tone: str = "", hook: str = "") -> dict:
        run = self.state()
        res = run["result"]
        kit, rules = self.h.kit(self.a), self.h.rules(self.a)
        st = self._story(run)
        src = res.get("source") or {}
        chans = self.channels(run, channel_ids)
        tones = res.setdefault("tones", {})
        for c in chans:
            if tone:
                tones[c["id"]] = tone
        if hook:
            st["headline"] = tidy(safe_headline(hook, (run.get("input") or {}).get("prompt", ""), [st["headline"]]) or st["headline"], st)
        article = {"url": src.get("url") or st["url"], "title": st["headline"], "description": st.get("why") or src.get("description", ""),
                   "site": src.get("site") or st["source"], "image": "", "paragraphs": st["facts"] + (src.get("paragraphs") or [])[:3]}
        eng = llm.resolve(self.store, self.a.uid, self.s)
        ai = None
        if eng and eng["style"] == "openai":
            eng = self.h._ai_reserve(self.a, eng)
            ai = {"base_url": eng["base_url"], "api_key": eng["api_key"], "model": eng["model"]} if eng else None
        prompt = (run.get("input") or {}).get("prompt", "")
        default_tone = ((run.get("input") or {}).get("intake") or {}).get("tone", "")
        groups: dict[str, list] = {}
        for c in chans:
            groups.setdefault(tones.get(c["id"], "") or default_tone, []).append(c)
        drafts = []
        limits = {c["service"]: drafter.limit_for(c["service"]) for c in chans}

        def guide(c):  # the channel's own tone wins (a reply "LinkedIn more formal"); else the prompt's own words
            own = tones.get(c["id"], "")
            return tone_instructions(own) if own else tone_instructions(default_tone, prompt)
        d = self.model_json("captions", "You write social captions for a brand. Use only the facts given; keep every figure exactly as written; "
                            "no hype words like game-changer or revolutionary; one caption per network, each under its limit. Follow each "
                            "network's tone instructions and conventions exactly: the tone decides the hook, sentence length and energy.",
                            json.dumps({"brand_voice": kit.get("voice", ""), "rules": brand.rules_prompt(rules), "story": {"headline": st["headline"],
                                        "facts": st["facts"], "why": st.get("why", ""), "link": article["url"]},
                                        "networks": {c["service"]: {"limit": limits[c["service"]], "tone": tones.get(c["id"], "") or default_tone,
                                                                    "tone_instructions": guide(c),
                                                                    "conventions": CHANNEL_STYLE.get(c["service"], "hook line, then the link.")}
                                                     for c in chans},
                                        "format": "Return JSON {network: caption}. Every caption opens with a hook line, never a plain "
                                                  "restatement of the headline. Brand rules win over conventions."}),
                            max_tokens=2400)
        truncated = self.truncated
        if isinstance(d, dict) and all(isinstance(d.get(c["service"]), str) and d.get(c["service"]).strip() for c in chans):
            drafts = [{"channel_id": c["id"], "service": c["service"], "text": d[c["service"]], "engine": "claude"} for c in chans]
            groups = {}
        for tn, cs in groups.items():
            voice = "; ".join(x for x in [kit.get("voice", ""), tone_instructions(tn, "" if tones.get(cs[0]["id"]) else prompt),
                                          brand.rules_prompt(rules)] if x)
            drafts += drafter.draft(cs, article, ai=ai, http=self.h.http, tone=voice)
        if ai and groups:
            self.h._ai_audit(self.a, eng, "captions")
        caps = res.setdefault("captions", {})
        for d in drafts:
            raw = brand.apply_text_rules(tidy(d["text"], st), rules)
            if d["service"] == "youtube":
                raw = dedupe_youtube(raw, st["headline"])
            text = drafter.fit(d["service"], raw)
            caps[d["channel_id"]] = {"text": text, "engine": d["engine"], "tone": tones.get(d["channel_id"], "") or default_tone,
                                     "trimmed": text != raw}
        res["story"] = st
        self.save(run)
        # the full saved text: a 160-character preview made the brain report a "cut off" caption that was complete
        return {"captions": {d["service"]: caps[d["channel_id"]]["text"] for d in drafts},
                "complete": {d["service"]: not caps[d["channel_id"]]["trimmed"] for d in drafts},
                "fallback_writer": bool(groups), "model_cut_off_retried": bool(truncated), "tone": tone or default_tone}

    # ---------------------------------------------------------------- check + packages
    def t_check(self) -> dict:
        """Brand check + clipping + 'is this about THE source' + fact conflicts, then one package per channel."""
        run = self.state()
        res = run["result"]
        kit, rules = self.h.kit(self.a), self.h.rules(self.a)
        st = self._story(run)
        packages, problems = [], []
        old = {p["channel_id"]: p for p in res.get("packages") or []}
        for c in run["input"]["channels"]:
            cm = (res.get("channel_media") or {}).get(c["id"])
            cap = (res.get("captions") or {}).get(c["id"])
            if not cm or not cap:
                problems.append(f"{c['service']}: {'no media' if not cm else 'no caption'}")
                continue
            media = cm["media"]
            main = next((m for m in media if m["type"] == "video"), None) or (media[0] if media else None)
            meta = self.h.media_meta(self.a, main["token"]) if main and main.get("token") else None
            chk = brand.brand_check(cap["text"], meta, rules, kit)
            clipped = self._clipped(media)
            if clipped:
                problems.append(f"{c['service']}: text clipped on {len(clipped)} image(s)")
            prev = old.get(c["id"], {})
            if prev.get("sent"):
                status = prev["status"]
            else:
                status = "hold" if res.get("conflicts") else "ready"
            pk = {"channel_id": c["id"], "service": c["service"], "channel_name": c["name"], "text": cap["text"],
                  "length": drafter.count(c["service"], cap["text"]), "limit": drafter.limit_for(c["service"]),
                  "media_urls": self._urls_for(c["service"], media), "format": cm["format"], "note": cm.get("note", ""),
                  "ratio": cm.get("ratio") or ("9:16" if cm["format"] == "reel" else "4:5"),
                  "check": chk, "clipped": clipped, "status": status,
                  "idempotency_key": hashlib.sha256(f"{self.rid}:{c['id']}".encode()).hexdigest()[:16]}
            if c["service"] == "youtube":
                pk["options"] = {"title": st["headline"][:100], "category_id": "28"}
            if prev.get("sent"):
                pk["sent"] = prev["sent"]
            pk["media"] = self._media_info(media, pk["media_urls"], pk["ratio"])
            packages.append(pk)
        res["packages"] = packages
        src_url = ((res.get("source") or {}).get("url") or "").split("?")[0].rstrip("/")
        want = (run["input"].get("source_url") or "").split("?")[0].rstrip("/")
        about = not want or (src_url.lower().replace("twitter.com", "x.com") == want.lower().replace("twitter.com", "x.com"))
        if not about:
            problems.append("the story isn't from your link")
        res["checks"] = {"about_source": about, "problems": problems, "conflicts": len(res.get("conflicts") or [])}
        self.save(run)
        return {**res["checks"], "packages": len(packages)}

    def _media_info(self, media: list[dict], urls: list[str], ratio: str) -> list[dict]:
        """What each channel card shows: its own files with type, size, aspect and length (a playable preview, not a tile)."""
        out = []
        for m in media:
            if m["url"] not in urls:
                continue
            meta = (self.h.media_meta(self.a, m["token"]) if m.get("token") else None) or {}
            w, h = meta.get("width"), meta.get("height")
            r = ratio
            if w and h:
                q = w / h
                r = min((("9:16", 9 / 16), ("4:5", 0.8), ("1:1", 1.0), ("16:9", 16 / 9)), key=lambda x: abs(x[1] - q))[0]
            out.append({"url": m["url"], "type": m["type"], "role": m.get("role", ""), "width": w, "height": h,
                        "duration": meta.get("duration"), "ratio": r})
        return out

    @staticmethod
    def _urls_for(svc: str, media: list[dict]) -> list[str]:
        vids = [m["url"] for m in media if m["type"] == "video"]
        imgs = [m["url"] for m in media if m["type"] == "photo" and m.get("role") != "slide"]
        if vids:
            return vids[:1]
        if svc in VIDEO_ONLY:
            return []
        if svc in ("instagram", "linkedin", "facebook", "threads"):
            return imgs[:10]
        if svc in TEXT_FIRST:
            return imgs[:4]
        return imgs[:1]

    def t_save_buffer_drafts(self, channel_ids: list | None = None) -> dict:
        run = self.state()
        res = run["result"]
        if res.get("conflicts"):
            raise ActionError("A fact needs your answer before anything goes to Buffer: " + res["conflicts"][0]["question"], 409)
        out = []
        for pk in res.get("packages") or []:
            if channel_ids and pk["channel_id"] not in channel_ids and pk["service"] not in channel_ids:
                continue
            if pk.get("sent") or pk.get("status") != "ready":  # idempotent: one draft per run + channel
                out.append({"service": pk["service"], "status": "already saved" if pk.get("sent") else pk.get("status")})
                continue
            if not pk["media_urls"] and pk["service"] in VIDEO_ONLY:
                out.append({"service": pk["service"], "status": "skipped: needs a video"})
                continue
            prev = self.studio.preview_post(self.a, {"channel_id": pk["channel_id"], "text": pk["text"], "media_urls": pk["media_urls"],
                                                     "mode": "draft", "options": pk.get("options") or {}})
            sub = self.studio.submit_post(self.a, prev["confirm_token"])
            pk["status"] = sub["status"]
            pk["sent"] = {"mode": "draft", "buffer_post_id": (sub.get("post") or {}).get("id"), "approval_id": sub.get("approval_id"),
                          "key": pk["idempotency_key"], "at": time.time()}
            out.append({"service": pk["service"], "status": sub["status"], "buffer_post_id": pk["sent"]["buffer_post_id"]})
            self.save(run)  # saved after every draft, so a retry never creates the same draft twice
        self.save(run)
        return {"drafts": out}

    def t_finish(self, message: str) -> dict:
        return {"message": (message or "")[:600]}
