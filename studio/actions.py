"""One action layer for the web UI, the REST API and the MCP server (UI = API = MCP parity).

Every write goes through preview -> submit(confirm_token): preview validates the post against the
channel's rules and returns a single-use confirm token bound to that exact payload; submit executes
only that payload. Humans in the UI are the reviewers, so their submit goes straight to Buffer.
Agents are held by their token's policy:
  review  (default) submit lands in the Approvals inbox; nothing reaches Buffer until a human approves
  draft             submit always creates a Buffer *draft*, whatever mode was asked for
  trusted           submit runs the requested mode (still capped per day, still stopped by the kill switch)
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import demo, demo_guard, drafter, preflight, radar
from .buffer_api import BufferClient, BufferError
from .config import Settings
from .store import Store, sha

MODES = {"draft", "queue", "next", "now", "schedule"}
POLICIES = ("review", "draft", "trusted")
NEEDS_MEDIA = {"instagram": "an image or video", "tiktok": "a video", "youtube": "a video", "pinterest": "an image"}
REQUIRED_METADATA = {"instagram": {"type": "post", "shouldShareToFeed": True}, "facebook": {"type": "post"},
                     "googlebusiness": {"type": "whats_new"}}
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
CONFIRM_TTL = 15 * 60
PROBE_MAX = 60 * 1024 * 1024  # media preflight downloads at most this much per file
OPTION_KEYS = ("title", "category_id", "board_id", "topic", "x_long_posts", "ig_type", "thumbnail_offset_ms")
THUMB_OFFSET_SERVICES = ("instagram", "tiktok", "pinterest")  # Buffer honours video.metadata.thumbnailOffset here only


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Actor:
    uid: str
    kind: str = "human"            # human | agent
    agent: dict | None = None      # agent_tokens row

    @property
    def label(self) -> str:
        return "you" if self.kind == "human" else f"agent:{self.agent['name']}"


def _asset(url: str, thumb_ms: int | None = None) -> dict:
    path = url.lower().split("?")[0]
    if path.endswith(VIDEO_EXT):
        v = {"url": url}
        if thumb_ms is not None:
            v["metadata"] = {"thumbnailOffset": int(thumb_ms)}  # VideoMetadataInput.thumbnailOffset, in ms
        return {"video": v}
    return {"image": {"url": url}}


def build_post_input(channel: dict, text: str, media_url: str, mode: str, due_at: str | None,
                     media_urls: list[str] | None = None, options: dict | None = None) -> dict:
    """Exact CreatePostInput sent to Buffer (shared by every surface; tested for parity)."""
    inp = {"channelId": channel["id"], "text": text, "assets": [], "needsApproval": False,
           "schedulingType": "automatic"}
    urls = [u for u in (media_urls or []) if u] or ([media_url] if media_url else [])
    options = options or {}
    thumb = options.get("thumbnail_offset_ms") if channel["service"] in THUMB_OFFSET_SERVICES else None
    if urls:
        inp["assets"] = [_asset(u, thumb) for u in urls]
    if mode == "draft" and due_at:
        # a draft that already has its time: it sits in the week as a draft and never publishes until a person schedules it
        inp.update(mode="customScheduled", dueAt=due_at, saveToDraft=True)
    elif mode == "draft":
        inp.update(mode="addToQueue", saveToDraft=True)
    elif mode == "queue":
        inp["mode"] = "addToQueue"
    elif mode == "next":
        inp["mode"] = "shareNext"
    elif mode == "now":
        inp["mode"] = "shareNow"
    elif mode == "schedule":
        inp.update(mode="customScheduled", dueAt=due_at)
    svc = channel["service"]
    meta = dict(REQUIRED_METADATA.get(svc, {}))
    if svc == "instagram" and options.get("ig_type") in ("reel", "story", "post"):
        meta["type"] = options["ig_type"]
    parts = drafter.split_thread(svc, text)
    if len(parts) > 1:
        inp["text"] = parts[0]
        meta["thread"] = [{"text": p} for p in parts]
        if inp["assets"]:
            meta["thread"][0]["assets"] = inp["assets"]
    if svc == "youtube":
        meta["title"] = (options.get("title") or text.split("\n")[0])[:100]
        if options.get("category_id"):
            meta["categoryId"] = str(options["category_id"])
    if svc == "pinterest":
        if options.get("board_id"):
            meta["boardServiceId"] = options["board_id"]
        if options.get("title"):
            meta["title"] = options["title"]
    if svc == "threads" and options.get("topic"):
        meta["topic"] = options["topic"]
    if meta:
        inp["metadata"] = {"google" if svc == "googlebusiness" else svc: meta}
    return inp


def clean_options(raw) -> dict:
    """Network options a post may carry (YouTube title/category, Pinterest board/title, Threads topic, X long posts)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in OPTION_KEYS:
        v = raw.get(k)
        if v in (None, "", False):
            continue
        if k == "x_long_posts":
            out[k] = bool(v)
        elif k == "thumbnail_offset_ms":
            try:
                out[k] = max(0, int(v))
            except (TypeError, ValueError):
                continue
        elif k == "ig_type":
            if str(v) in ("reel", "story", "post"):
                out[k] = str(v)
        else:
            out[k] = str(v).strip()[:300]
    return out


def preflight_error(results: list[dict], override: bool) -> str | None:
    """The message that blocks a send: any error, or a warning nobody overrode."""
    errs = [f"{r['network']}: {f['message']}" for r in results for f in r["findings"] if f["level"] == "error"]
    if errs:
        return "Preflight found a problem Buffer would reject: " + " ".join(errs[:3]) + \
               (" Use Fix it (autofix_post) or change the post." if any(r["fixes"] for r in results) else "")
    warns = [f"{r['network']}: {f['message']}" for r in results for f in r["findings"] if f["level"] == "warning"]
    if warns and not override:
        return "Preflight warning: " + " ".join(warns[:3]) + " Fix it, or send again with override_warnings: true to accept it."
    return None


class Studio:
    def __init__(self, s: Settings, store: Store, buffer: BufferClient):
        self.s, self.store, self.buffer = s, store, buffer
        self.house = None
        self.media_loader = None  # tests swap this for a fake; production downloads with House's safe fetch

    # ------------------------------------------------------------ preflight (Never Bounced)
    def _load_media(self, url: str) -> bytes | None:
        base = self.s.public_url.rstrip("/") + "/m/"
        if url.startswith(base) and self.house:
            m = self.house.get_media(url[len(base):].split("?")[0])
            return m["data"] if m else None
        sbase = self.s.public_url.rstrip("/") + "/sample/"
        if url.startswith(sbase):
            from . import takes
            f = (takes.SAMPLE_DIR / url[len(sbase):].split("?")[0]).resolve()
            return f.read_bytes() if f.parent == takes.SAMPLE_DIR.resolve() and f.is_file() else None
        if self.media_loader:
            return self.media_loader(url)
        return self.house._fetch_bytes(url, PROBE_MAX) if self.house else None

    def _preflight(self, ch: dict, text: str, media_urls: list[str], mode: str, options: dict | None = None,
                   probe: bool = True, creating: bool = True) -> dict:
        return preflight.check(ch, text, media_urls, mode, options, loader=self._load_media, probe_media=probe, creating=creating,
                               known=self._known_probe)

    def _known_probe(self, url: str) -> dict | None:
        """Facts the video worker already measured with ffprobe for media HuhQueue rendered (the web dyno has no ffprobe)."""
        base = self.s.public_url.rstrip("/")
        meta = None
        if url.startswith(base + "/m/") and self.house:
            rows = self.store.q("SELECT meta FROM media WHERE token=?", (url[len(base) + 3:].split("?")[0],))
            meta = json.loads(rows[0]["meta"]) if rows else None
        elif url.startswith(base + "/sample/"):
            from . import takes
            sm = takes.sample_meta() or {}
            name = url[len(base) + 8:].split("?")[0]
            meta = next((c["meta"] for c in sm.get("clips", []) if c["file"] == name), None)
        pr = (meta or {}).get("probe")
        return dict(pr) if pr and pr.get("probed") else None

    @staticmethod
    def _media_list(p: dict) -> list[str]:
        urls = [str(u).strip() for u in (p.get("media_urls") or []) if str(u).strip()][:25]
        one = (p.get("media_url") or "").strip()
        return urls or ([one] if one else [])

    def preflight(self, a: Actor, p: dict) -> dict:
        """Dry run: check one post against every chosen channel's rules. Nothing is saved or sent."""
        chans = {c["id"]: c for c in self.channels(a)}
        ids = p.get("channel_ids") or ([p["channel_id"]] if p.get("channel_id") else [])
        if not ids:
            raise ActionError("Give channel_id or channel_ids.")
        results = []
        for cid in ids[:12]:
            ch = chans.get(cid)
            if not ch:
                raise ActionError("Unknown channel, or this agent token isn't allowed to use it.", 404)
            editing = bool(p.get("editing"))  # an edit to a post already in Buffer: fields optional, media unchanged
            results.append(self._preflight(ch, p.get("text") or "", self._media_list(p), (p.get("mode") or "queue"),
                                           clean_options(p.get("options")), probe=not editing, creating=not editing))
        worst = "error" if any(r["status"] == "error" for r in results) else \
            "warning" if any(r["status"] == "warning" for r in results) else "ok"
        return {"status": worst, "ok": worst == "ok", "channels": results, "rule_pack": preflight.RULE_PACK_VERSION}

    def autofix(self, a: Actor, p: dict) -> dict:
        """Apply preflight fixes to a post for one channel and return the fixed post with a fresh preflight.
        Text fixes trim or thread the caption; image fixes crop/convert/recompress with Pillow and store the result as new
        HuhQueue media. Nothing is sent to Buffer."""
        chans = {c["id"]: c for c in self.channels(a)}
        ch = chans.get(p.get("channel_id") or "")
        if not ch:
            raise ActionError("Unknown channel, or this agent token isn't allowed to use it.", 404)
        text, urls, opts, mode = p.get("text") or "", self._media_list(p), clean_options(p.get("options")), p.get("mode") or "queue"
        before = self._preflight(ch, text, urls, mode, opts)
        want = p.get("fixes") or "all"
        ids = [f["id"] for f in before["fixes"]] if want == "all" else [str(x) for x in want]
        ids = [i for i in ids if i != "reencode"]
        has_video = any(m.get("kind") == "video" for m in before["media"])
        text2, opts2, applied = preflight.apply_text_fixes(ch, text, opts, ids, has_video)
        urls2 = list(urls)
        for fid in ids:
            if fid.startswith("keep_first:"):
                urls2 = urls2[: int(fid.split(":", 1)[1])]
                applied.append(fid)
        for fid in ids:
            if not fid.startswith("image:"):
                continue
            i = int(fid.split(":", 1)[1])
            if i >= len(urls2):
                continue
            data = self._load_media(urls2[i])
            if not data:
                continue
            try:
                out, ext = preflight.fit_image(ch["service"], data)
            except Exception as e:  # noqa: BLE001
                raise ActionError(f"Couldn't fix that image: {e}")
            if not self.house:
                raise ActionError("Image fixes need HuhQueue media storage.", 500)
            tok = self.house.put_media(a, out, f"fit-{ch['service']}.{ext}", kind="autofix", meta={"type": "photo", "from": urls2[i]})
            urls2[i] = self.house.media_url(tok)
            applied.append(fid)
        after = self._preflight(ch, text2, urls2, mode, opts2)
        self.store.audit(a.uid, a.label, "autofix_post", "read", {"channel": ch["service"], "applied": applied,
                                                                   "before": before["status"], "after": after["status"]})
        return {"channel_id": ch["id"], "service": ch["service"], "text": text2, "media_urls": urls2,
                "media_url": urls2[0] if urls2 else "", "options": opts2, "applied": applied,
                "before": {"text": text, "media_urls": urls, "status": before["status"]}, "preflight": after,
                "needs_reencode": any(f.get("fix") == "reencode" for f in after["findings"])}

    def preflight_rules(self, a: Actor, service: str | None = None) -> dict:
        rules = [r for r in preflight.RULES if not service or r["service"] in (service, "*")]
        return {"rule_pack": preflight.RULE_PACK_VERSION, "checked": preflight.CHECKED, "rules": rules,
                "unverified": [{"rule": r, "why": w} for r, w in preflight.UNVERIFIED]}

    # ------------------------------------------------------------ reads
    def me(self, a: Actor) -> dict:
        u = self.store.user(a.uid) or {}
        return {"id": u.get("id"), "email": u.get("email"), "name": u.get("name"), "organization": u.get("org_name"),
                "kill_switch": bool(u.get("kill_switch")), "actor": a.label,
                "agent_policy": a.agent["policy"] if a.agent else None}

    def channels(self, a: Actor, refresh: bool = False) -> list[dict]:
        u = self.store.user(a.uid)
        chans = self.buffer.channels(a.uid, u["org_id"], refresh=refresh)
        out = []
        for c in chans:
            if a.agent and a.agent.get("channels"):
                if c["id"] not in json.loads(a.agent["channels"]):
                    continue
            out.append({**c, "limit": drafter.limit_for(c["service"]), "needs_media": NEEDS_MEDIA.get(c["service"])})
        return out

    def queue(self, a: Actor, status: str = "scheduled") -> list[dict]:
        u = self.store.user(a.uid)
        statuses = {"scheduled": ["scheduled"], "draft": ["draft"], "sent": ["sent"], "error": ["error"]}.get(status, ["scheduled"])
        return self.buffer.posts(a.uid, u["org_id"], statuses)

    def limits(self, a: Actor) -> dict:
        return {"buffer_api": self.buffer.budget.snapshot(), "characters": drafter.LIMITS}

    def plan(self, a: Actor) -> dict:
        """How close this Buffer organization is to its plan's limits, for the warnings and the home screen."""
        u = self.store.user(a.uid)
        org = self.buffer.org_limits(a.uid, u["org_id"]) or {}
        lim = org.get("limits") or {}
        chans = self.channels(a)
        meters = []
        if lim.get("scheduledPosts"):
            used = len(self.buffer.posts(a.uid, u["org_id"], ["scheduled"], first=100))
            meters.append({"key": "scheduled_posts", "label": "Scheduled posts", "used": used, "limit": lim["scheduledPosts"]})
        if lim.get("channels"):
            meters.append({"key": "channels", "label": "Channels", "used": org.get("channelCount", len(chans)), "limit": lim["channels"]})
        for c in chans:
            w = c.get("weeklyPostingLimit") or {}
            if w.get("limit"):
                meters.append({"key": f"weekly:{c['id']}", "label": f"{c.get('displayName') or c['name']} ({c['service']}) this week",
                               "used": (w.get("scheduled") or 0) + (w.get("sent") or 0), "limit": w["limit"]})
        for m in meters:
            m["pct"] = round(100 * m["used"] / m["limit"])
        locked = [c.get("displayName") or c["name"] for c in chans if c.get("isLocked")]
        near = [m for m in meters if m["pct"] >= 80]
        return {"meters": meters, "locked_channels": locked, "near_limit": near, "upgrade_url": "https://buffer.com/pricing",
                "buffer_api": self.buffer.budget.snapshot()}

    # ------------------------------------------------------------ drafting
    def draft_from_link(self, a: Actor, url: str, channel_ids: list[str] | None = None, tone: str = "",
                        thread: bool = False) -> dict:
        chans = [c for c in self.channels(a) if not c.get("isDisconnected")]
        if channel_ids:
            chans = [c for c in chans if c["id"] in channel_ids]
        if not chans:
            raise ActionError("No connected Buffer channels selected.")
        sample = None
        if demo.is_sample_url(url) or (demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "research")):
            article, sample = dict(demo.SAMPLE_ARTICLE), demo_guard.WHY["research"].replace("the agent used", "this uses")
        else:
            try:
                article = drafter.fetch_article(url)
            except drafter.FetchError as e:
                raise ActionError(str(e))
        ai = {"base_url": self.s.ai_base_url, "api_key": self.s.ai_api_key, "model": self.s.ai_model}
        if self.s.ai_base_url and demo_guard.is_demo(a.uid):  # the free sample's AI allowance
            if demo_guard.allowed(self.store, a.uid, "ai"):
                demo_guard.spend(self.store, a.uid, "ai")
            else:
                ai = {"base_url": "", "api_key": "", "model": ""}
        drafts = drafter.draft(chans, article, ai=ai, tone=tone, thread=thread)
        self.store.audit(a.uid, a.label, "draft_from_link", "read", {"url": article["url"], "channels": len(drafts)})
        out = {"article": {k: article[k] for k in ("url", "title", "description", "image", "site")}, "drafts": drafts}
        if sample:
            out["sample"] = sample
        return out

    # ------------------------------------------------------------ news radar
    def feeds(self, a: Actor) -> list[dict]:
        return [dict(r) for r in self.store.q("SELECT id,url,created FROM feeds WHERE user_id=? ORDER BY id", (a.uid,))]

    def radar(self, a: Actor) -> dict:
        out = {"feeds": self.feeds(a), **radar.stories(self.feeds(a))}
        if demo_guard.is_demo(a.uid) and not out.get("stories"):  # the sample's feeds couldn't be read: show sample stories
            out["stories"], out["sample"] = [dict(s) for s in demo.SAMPLE_STORIES], True
        return out

    def add_feed(self, a: Actor, url: str) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can change which feeds you follow.", 403)
        url = (url or "").strip()
        if len(self.feeds(a)) >= radar.MAX_FEEDS:
            raise ActionError(f"You can follow up to {radar.MAX_FEEDS} feeds. Remove one first.")
        if any(f["url"] == url for f in self.feeds(a)):
            raise ActionError("You already follow that feed.")
        if not demo_guard.take(self.store, a.uid, "feed"):
            raise ActionError(demo_guard.WHY["feed"], 429)
        try:
            feed = radar.fetch(url)
        except drafter.FetchError as e:
            raise ActionError(str(e))
        fid = self.store.x("INSERT INTO feeds(user_id,url,created) VALUES(?,?,?)", (a.uid, url, time.time()))
        self.store.audit(a.uid, a.label, "follow_feed", "read", {"url": url})
        return {"id": fid, "url": url, "title": feed["title"], "stories": len(feed["stories"])}

    def remove_feed(self, a: Actor, feed_id: int) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can change which feeds you follow.", 403)
        self.store.x("DELETE FROM feeds WHERE id=? AND user_id=?", (feed_id, a.uid))
        return {"removed": feed_id}

    # ------------------------------------------------------------ guards
    def _guard_write(self, a: Actor, action: str) -> None:
        if a.kind != "agent":
            return
        u = self.store.user(a.uid)
        if u and u.get("kill_switch"):
            self.store.audit(a.uid, a.label, action, "blocked", {"reason": "kill switch on"}, ok=False)
            raise ActionError("Agent actions are paused: the kill switch is on in HuhQueue.", 423)
        day = time.time() - 86400
        used = self.store.q("SELECT COUNT(*) n FROM audit WHERE user_id=? AND actor=? AND action IN "
                            "('submit_post','create_idea','update_post','delete_draft') AND ok=1 AND ts>?", (a.uid, a.label, day))[0]["n"]
        if used >= int(a.agent["daily_cap"]):
            self.store.audit(a.uid, a.label, action, "blocked", {"reason": "daily cap", "cap": a.agent["daily_cap"]}, ok=False)
            raise ActionError(f"This agent token reached its daily cap of {a.agent['daily_cap']} actions.", 429)

    def _validate(self, a: Actor, p: dict) -> tuple[dict, dict, list[str], dict]:
        mode = (p.get("mode") or "draft").strip()
        if mode not in MODES:
            raise ActionError(f"mode must be one of {sorted(MODES)}")
        chans = {c["id"]: c for c in self.channels(a)}
        ch = chans.get(p.get("channel_id") or "")
        if not ch:
            raise ActionError("Unknown channel, or this agent token isn't allowed to use it.", 404)
        if ch.get("isDisconnected"):
            raise ActionError(f"{ch.get('displayName') or ch['name']} is disconnected in Buffer; reconnect it there first.")
        text = (p.get("text") or "").strip()
        if not text:
            raise ActionError("Post text is empty.")
        media_urls = [str(u).strip() for u in (p.get("media_urls") or []) if str(u).strip()][:10]
        media = (p.get("media_url") or (media_urls[0] if media_urls else "")).strip()
        for u in ([media] if media else []) + media_urls:
            if not (u.startswith("https://") or (self.s.public_url.startswith("http://") and u.startswith(self.s.public_url))):
                raise ActionError("Media must be a public https:// URL (Buffer fetches it from there).")
        warnings = []
        if ch["service"] in NEEDS_MEDIA and not media:
            if mode == "draft":
                warnings.append(f"{ch['service'].title()} needs {NEEDS_MEDIA[ch['service']]} before it can publish; saved as a draft.")
            else:
                raise ActionError(f"{ch['service'].title()} needs {NEEDS_MEDIA[ch['service']]}. Add a media URL or save as draft.")
        due = None
        if mode == "schedule":
            try:
                dt = datetime.fromisoformat((p.get("due_at") or "").replace("Z", "+00:00"))
            except ValueError:
                raise ActionError("due_at must be an ISO time, e.g. 2026-11-02T09:00:00Z")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= datetime.now(timezone.utc):
                raise ActionError("due_at is in the past.")
            due = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        elif mode == "draft" and p.get("due_at"):
            due = self._due(p["due_at"])  # a dated draft (One take places clips this way); it still never publishes
        if ch.get("isLocked") and mode != "draft":
            raise ActionError(f"{ch.get('displayName') or ch['name']} is locked in Buffer, usually because the plan has more channels "
                              "than it allows. Save as a draft, or upgrade your Buffer plan.")
        if mode != "draft":
            for st in self.buffer.daily_limits(a.uid, [ch["id"]], due):
                if st.get("limit") is None:
                    continue
                n, lim_ = st["scheduled"] + st["sent"], st["limit"]
                window = "in 24 hours" if ch["service"] == "twitter" else "that day"
                if st["isAtLimit"]:
                    raise ActionError(f"{ch.get('displayName') or ch['name']} already has {n} of {lim_} posts {window}, Buffer's daily "
                                      "limit for this channel. Pick another time, or save it as a draft.")
                if n + 1 >= lim_:
                    warnings.append(f"This is the last post Buffer allows on this channel {window} ({n + 1} of {lim_}).")
        if ch.get("isQueuePaused") and mode in ("queue", "next"):
            warnings.append("This channel's queue is paused in Buffer, so it won't go out until you resume it.")
        opts = clean_options(p.get("options"))
        override = bool(p.get("override_warnings"))
        pf = self._preflight(ch, text, media_urls or ([media] if media else []), mode, opts)
        msg = preflight_error([pf], override)
        if msg:
            raise ActionError(msg)
        clean = {"channel_id": ch["id"], "text": text, "media_url": media, "mode": mode, "due_at": due}
        if len(media_urls) > 1:
            clean["media_urls"] = media_urls
        if opts:
            clean["options"] = opts
        if override and pf["status"] == "warning":
            clean["override_warnings"] = True
        return clean, ch, warnings, pf

    # ------------------------------------------------------------ preview / submit
    def preview_post(self, a: Actor, p: dict) -> dict:
        clean, ch, warnings, pf = self._validate(a, p)
        token = "cf_" + secrets.token_urlsafe(24)
        self.store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires) VALUES(?,?,?,?,?)",
                     (sha(token), a.uid, a.agent["id"] if a.agent else None, json.dumps(clean), time.time() + CONFIRM_TTL))
        what = self._outcome(a, clean["mode"])
        self.store.audit(a.uid, a.label, "preview_post", "read", {"channel": ch["service"], "mode": clean["mode"]})
        return {"preview": {**clean, "service": ch["service"], "channel_name": ch.get("displayName") or ch["name"],
                            "length": drafter.count(ch["service"], clean["text"]), "limit": drafter.limit_for(ch["service"])},
                "warnings": warnings, "preflight": pf, "confirm_token": token, "expires_in": CONFIRM_TTL, "on_submit": what}

    def _outcome(self, a: Actor, mode: str) -> str:
        if a.kind == "human":
            return f"sends to Buffer now ({mode})"
        pol = a.agent["policy"]
        return {"review": "waits in the HuhQueue Approvals inbox for a human",
                "draft": "creates a Buffer draft (never publishes)",
                "trusted": f"sends to Buffer ({mode})"}[pol]

    def submit_post(self, a: Actor, confirm_token: str) -> dict:
        rows = self.store.q("SELECT * FROM confirms WHERE token_hash=? AND user_id=?", (sha(confirm_token or ""), a.uid))
        if not rows:
            raise ActionError("Unknown confirm_token. Call preview_post first.", 404)
        row = rows[0]
        if row["used"] or row["expires"] < time.time():
            raise ActionError("This confirm_token is used or expired. Preview again.", 410)
        if (row["agent_id"] or None) != (a.agent["id"] if a.agent else None):
            raise ActionError("This confirm_token belongs to a different caller.", 403)
        self._guard_write(a, "submit_post")
        self.store.x("UPDATE confirms SET used=1 WHERE token_hash=?", (row["token_hash"],))
        clean = json.loads(row["payload"])
        if a.kind == "agent" and a.agent["policy"] == "review":
            aid = self.store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                               (a.uid, a.agent["id"], a.agent["name"], json.dumps(clean), "pending", time.time()))
            self.store.audit(a.uid, a.label, "submit_post", "held", {"approval_id": aid, "mode": clean["mode"]})
            return {"status": "pending_approval", "approval_id": aid,
                    "message": "Waiting for a human to approve it in HuhQueue → Approvals."}
        if a.kind == "agent" and a.agent["policy"] == "draft":
            clean["mode"] = "draft"
        return self._send(a, clean, action="submit_post")

    def _send(self, a: Actor, clean: dict, action: str) -> dict:
        ch = {c["id"]: c for c in self.channels(Actor(a.uid))}.get(clean["channel_id"])
        if not ch:
            raise ActionError("That channel is no longer in your Buffer organization.", 404)
        urls = clean.get("media_urls") or ([clean["media_url"]] if clean["media_url"] else [])
        pf = self._preflight(ch, clean["text"], urls, clean["mode"], clean.get("options"))  # re-check right before createPost
        msg = preflight_error([pf], bool(clean.get("override_warnings")))
        if msg:
            self.store.audit(a.uid, a.label, action, "blocked", {"channel": ch["service"], "preflight": pf["status"]}, ok=False)
            raise ActionError(msg)
        inp = build_post_input(ch, clean["text"], clean["media_url"], clean["mode"], clean.get("due_at"), clean.get("media_urls"),
                               clean.get("options"))
        risk = "draft" if clean["mode"] == "draft" else "public"
        try:
            post = self.buffer.create_post(a.uid, inp)
        except BufferError as e:
            self.store.audit(a.uid, a.label, action, risk, {"channel": ch["service"], "mode": clean["mode"], "error": str(e)}, ok=False)
            raise
        self.store.audit(a.uid, a.label, action, risk, {"channel": ch["service"], "mode": clean["mode"], "buffer_post_id": post["id"],
                                                         "status": post.get("status")})
        return {"status": "sent_to_buffer", "post": post, "mode": clean["mode"], "service": ch["service"]}

    # ------------------------------------------------------------ edit, reschedule, delete (Planner drag-and-drop)
    @staticmethod
    def _due(value: str | None) -> str:
        try:
            dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        except ValueError:
            raise ActionError("due_at must be an ISO time, e.g. 2026-11-02T09:00:00Z")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt <= datetime.now(timezone.utc):
            raise ActionError("due_at is in the past. Pick a time from now on.")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _find_post(self, a: Actor, post_id: str) -> tuple[dict, dict]:
        u = self.store.user(a.uid)
        for status in ("scheduled", "draft", "error", "sent"):  # error: a failed post can be fixed and rescheduled
            for p in self.buffer.posts(a.uid, u["org_id"], [status], first=100):
                if p.get("id") == post_id:
                    ch = {c["id"]: c for c in self.channels(a)}.get(p.get("channelId"))
                    if not ch:
                        raise ActionError("Unknown post, or this agent token isn't allowed to use its channel.", 404)
                    return {**p, "status": p.get("status") or status}, ch
        raise ActionError("No scheduled post or draft with that id in your Buffer organization.", 404)

    def update_post(self, a: Actor, post_id: str, text: str | None = None, due_at: str | None = None,
                    override_warnings: bool = False) -> dict:
        """The one edit path for a post already in Buffer: new caption, new time, or both (editPost).
        Sent posts are history and can't move. Drafts stay drafts. Agents: 'trusted' may edit anything not sent;
        'review' and 'draft' tokens may only touch drafts, so they can never change what publishes."""
        if text is None and due_at is None:
            raise ActionError("Nothing to change: give text, due_at or both.")
        post, ch = self._find_post(a, post_id)
        if post["status"] == "sent":
            raise ActionError("That post was already sent by Buffer, so it can't be edited or moved.", 409)
        draft = post["status"] == "draft"
        if a.kind == "agent" and a.agent["policy"] != "trusted" and not draft:
            raise ActionError("This agent token may only change drafts. Ask a human to move a scheduled post.", 403)
        self._guard_write(a, "update_post")
        inp: dict = {"id": post_id}
        if text is not None:
            text = text.strip()
            if not text:
                raise ActionError("Post text is empty.")
            inp["text"] = text
        # preflight the post as it will be after editPost (assets are unchanged, so media is not re-downloaded)
        assets = post.get("assets") or []
        urls = [x.get("source") for x in assets if x.get("source")]
        kinds = ["video" if (x.get("type") or "").lower() == "video" else "image" for x in assets if x.get("source")]
        pf = preflight.check(ch, inp.get("text", post.get("text") or ""), urls, "draft" if draft else "queue",
                             loader=None, probe_media=False, creating=False, media_kinds=kinds)
        msg = preflight_error([pf], override_warnings)
        if msg:
            self.store.audit(a.uid, a.label, "update_post", "blocked", {"buffer_post_id": post_id, "preflight": pf["status"]}, ok=False)
            raise ActionError(msg)
        if due_at is not None:
            inp.update(dueAt=self._due(due_at), mode="customScheduled")
            if draft:
                inp["saveToDraft"] = True
        try:
            out = self.buffer.edit_post(a.uid, inp)
        except BufferError as e:
            self.store.audit(a.uid, a.label, "update_post", "draft" if draft else "public",
                             {"buffer_post_id": post_id, "error": str(e)}, ok=False)
            raise
        self.store.audit(a.uid, a.label, "update_post", "draft" if draft else "public",
                         {"buffer_post_id": post_id, "channel": ch["service"], "due_at": inp.get("dueAt"), "text": "text" in inp})
        return {"status": "updated", "post": out, "service": ch["service"]}

    def reschedule_post(self, a: Actor, post_id: str, due_at: str, override_warnings: bool = False) -> dict:
        return self.update_post(a, post_id, due_at=due_at, override_warnings=override_warnings)

    def delete_draft(self, a: Actor, post_id: str) -> dict:
        post, ch = self._find_post(a, post_id)
        if post["status"] != "draft":
            raise ActionError("Only drafts can be deleted here. Scheduled posts can be moved back to drafts in Buffer.", 409)
        self._guard_write(a, "delete_draft")
        self.buffer.delete_post(a.uid, post_id)
        self.store.audit(a.uid, a.label, "delete_draft", "draft", {"buffer_post_id": post_id, "channel": ch["service"]})
        return {"status": "deleted", "id": post_id}

    def reschedule_approval(self, a: Actor, approval_id: int, due_at: str) -> dict:
        """Give an agent post that is still waiting for approval a time; it is scheduled for then once approved."""
        if a.kind != "human":
            raise ActionError("Only a signed-in human can change a post that is waiting for approval.", 403)
        rows = self.store.q("SELECT * FROM approvals WHERE id=? AND user_id=?", (approval_id, a.uid))
        if not rows or rows[0]["status"] != "pending":
            raise ActionError("No pending approval with that id.", 404)
        clean = json.loads(rows[0]["payload"])
        clean.update(due_at=self._due(due_at), mode="schedule")
        self.store.x("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(clean), approval_id))
        self.store.audit(a.uid, a.label, "reschedule_approval", "read", {"approval_id": approval_id, "due_at": clean["due_at"]})
        return {"status": "pending_approval", "approval_id": approval_id, "due_at": clean["due_at"]}

    # ------------------------------------------------------------ approvals (human only)
    def approvals(self, a: Actor, status: str = "pending") -> list[dict]:
        out = []
        for r in self.store.q("SELECT * FROM approvals WHERE user_id=? AND status=? ORDER BY id DESC LIMIT 50", (a.uid, status)):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            out.append(d)
        return out

    def decide(self, a: Actor, approval_id: int, approve: bool, text: str | None = None, mode: str | None = None) -> dict:
        if a.kind != "human":
            raise ActionError("Only a signed-in human can approve or reject agent posts.", 403)
        rows = self.store.q("SELECT * FROM approvals WHERE id=? AND user_id=?", (approval_id, a.uid))
        if not rows or rows[0]["status"] != "pending":
            raise ActionError("No pending approval with that id.", 404)
        clean = json.loads(rows[0]["payload"])
        if not approve:
            self.store.x("UPDATE approvals SET status='rejected', decided=? WHERE id=?", (time.time(), approval_id))
            self.store.audit(a.uid, a.label, "reject_post", "read", {"approval_id": approval_id})
            return {"status": "rejected", "approval_id": approval_id}
        if text is not None or mode:
            clean, _, _, _ = self._validate(a, {**clean, **({"text": text} if text is not None else {}), **({"mode": mode} if mode else {})})
        res = self._send(a, clean, action="approve_post")
        self.store.x("UPDATE approvals SET status='approved', result=?, decided=? WHERE id=?",
                     (json.dumps(res), time.time(), approval_id))
        return {**res, "approval_id": approval_id}

    # ------------------------------------------------------------ ideas
    def create_idea(self, a: Actor, title: str, text: str) -> dict:
        if not (text or "").strip():
            raise ActionError("Idea text is empty.")
        self._guard_write(a, "create_idea")
        u = self.store.user(a.uid)
        idea = self.buffer.create_idea(a.uid, u["org_id"], (title or "").strip()[:200], text.strip())
        self.store.audit(a.uid, a.label, "create_idea", "private", {"idea_id": idea["id"]})
        return {"status": "saved_to_buffer_ideas", "idea": idea}

    # ------------------------------------------------------------ safety controls (human only)
    def set_kill_switch(self, a: Actor, on: bool) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can change the kill switch.", 403)
        self.store.set_kill(a.uid, on)
        self.store.audit(a.uid, a.label, "kill_switch", "safety", {"on": on})
        return {"kill_switch": on}

    def create_agent_token(self, a: Actor, name: str, policy: str = "review", daily_cap: int = 10,
                           channels: list[str] | None = None, days: int = 30) -> dict:
        if a.kind != "human":
            raise ActionError("Agents can't mint agent tokens.", 403)
        if policy not in POLICIES:
            raise ActionError(f"policy must be one of {POLICIES}")
        name = (name or "my agent").strip()[:40]
        sample = demo.is_demo(a.uid)
        if sample:  # a sample token only ever reaches the sample workspace, and dies with it
            days, daily_cap = 1, min(int(daily_cap), 10)
        token = "hq_" + secrets.token_urlsafe(32)
        tid = self.store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) "
                           "VALUES(?,?,?,?,?,?,?,?)", (a.uid, name, sha(token), policy, max(1, min(int(daily_cap), 100)),
                                                       json.dumps(channels) if channels else None, time.time(),
                                                       time.time() + max(1, min(int(days), 365)) * 86400))
        self.store.audit(a.uid, a.label, "create_agent_token", "safety", {"id": tid, "name": name, "policy": policy})
        out = {"id": tid, "name": name, "policy": policy, "token": token,
               "note": "Shown once. Paste it into your agent's MCP config."}
        if sample:
            out.update(sample=True, note="Sample token: it only works with this sample workspace (made-up channels, nothing reaches "
                                         "Buffer) and expires within a day. Shown once.")
        return out

    def agent_tokens(self, a: Actor) -> list[dict]:
        rows = self.store.q("SELECT id,name,policy,daily_cap,channels,created,expires,revoked,last_used FROM agent_tokens "
                            "WHERE user_id=? ORDER BY id DESC", (a.uid,))
        return [dict(r) | {"channels": json.loads(r["channels"]) if r["channels"] else None} for r in rows]

    def revoke_agent_token(self, a: Actor, token_id: int) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can revoke tokens.", 403)
        self.store.x("UPDATE agent_tokens SET revoked=1 WHERE id=? AND user_id=?", (token_id, a.uid))
        self.store.audit(a.uid, a.label, "revoke_agent_token", "safety", {"id": token_id})
        return {"revoked": token_id}

    def resolve_agent(self, token: str) -> Actor | None:
        rows = self.store.q("SELECT * FROM agent_tokens WHERE token_hash=? AND revoked=0 AND expires>?", (sha(token), time.time()))
        if not rows:
            return None
        row = dict(rows[0])
        self.store.x("UPDATE agent_tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
        return Actor(row["user_id"], "agent", row)

    def audit(self, a: Actor, limit: int = 100) -> list[dict]:
        return self.store.audit_rows(a.uid, limit)
