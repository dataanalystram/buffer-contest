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
from typing import Any

from . import demo, demo_guard, drafter, mediareach, preflight, radar
from .buffer_api import BufferClient, BufferError
from .config import Settings
from .store import Store, sha

MODES = {"draft", "queue", "next", "now", "schedule"}
POLICIES = ("review", "draft", "trusted")
POLICY_LABELS = {"review": "publish needs approval", "draft": "drafts only", "trusted": "can schedule"}


def _past(value) -> ActionError:
    """due_at in the past: a structured error with the time now (UTC), never a post dated before it."""
    from .mcp_flow import StepError
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return StepError(f"due_at {str(value or '')[:40]} is in the past (now is {now} UTC). Pick a time from now on; nothing was "
                     "sent.", 400, code="due_at_past", now_utc=now)
NEEDS_MEDIA = {"instagram": "an image or video", "tiktok": "a video", "youtube": "a video", "pinterest": "an image"}
REQUIRED_METADATA = {"instagram": {"type": "post", "shouldShareToFeed": True}, "facebook": {"type": "post"},
                     "googlebusiness": {"type": "whats_new"}}
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
CONFIRM_TTL = 15 * 60
PROBE_MAX = 60 * 1024 * 1024  # media preflight downloads at most this much per file
OPTION_KEYS = ("title", "category_id", "made_for_kids", "board_id", "topic", "x_long_posts", "ig_type", "thumbnail_offset_ms", "ig_audio", "fb_type",
               "first_comment", "alt_text")
FIRST_COMMENT_SERVICES = ("instagram", "facebook", "linkedin")  # metadata.<svc>.firstComment in Buffer's PostInputMetaData
AUDIO_TYPES = ("music", "originalSound")  # Buffer's InstagramAudioType
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
        alts = options.get("alt_text") or []
        for asset, alt in zip(inp["assets"], alts):  # ImageMetadataInput.altText: screen readers on every network
            if alt and "image" in asset:
                asset["image"]["metadata"] = {"altText": alt}
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
    has_video = any("video" in x for x in inp["assets"])
    if svc == "instagram" and options.get("ig_type") in ("reel", "story", "post"):
        meta["type"] = options["ig_type"]
    elif svc == "instagram" and has_video:
        meta["type"] = "reel"  # Instagram publishes every feed video as a Reel; say so instead of leaving Buffer to convert "post"
    if svc == "facebook" and options.get("fb_type") in ("reel", "story", "post"):
        meta["type"] = options["fb_type"] if (has_video or options["fb_type"] != "reel") else "post"
    fc = str(options.get("first_comment") or "").strip()
    if fc and svc in FIRST_COMMENT_SERVICES:  # Buffer adds the first comment right after it publishes
        meta["firstComment"] = fc[:2200]
    au = options.get("ig_audio") if svc == "instagram" else None
    if au and any("video" in a for a in inp["assets"]):
        # Instagram music on a Reel: metadata.instagram.audio (InstagramAudioSelectionInput, experimental and not in
        # Buffer's introspection). Buffer only auto-publishes it for channels connected through a Facebook Page.
        meta["type"] = "reel"
        meta["audio"] = audio_input(au)
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
        # COPPA: always explicit; "not for kids" unless the person said made_for_kids: true (youtube.madeForKids)
        meta["madeForKids"] = options.get("made_for_kids") is True
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


def clean_audio(raw) -> dict | None:
    """A Reel's music choice as HuhQueue keeps it in a post package (options.ig_audio). None when it isn't usable.
    music_volume / original_volume are 0-100 (Buffer's audioVolume / videoVolume). start_ms is kept with the package
    but Buffer's API has no start-time field yet, so it is not sent (Instagram starts the track at its default)."""
    if not isinstance(raw, dict):
        return None
    aid = str(raw.get("audio_id") or raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    typ = str(raw.get("type") or "music")
    try:
        dur = int(raw.get("duration_ms") if raw.get("duration_ms") is not None else raw.get("duration"))
    except (TypeError, ValueError):
        return None
    if not aid or not aid.isdigit() or len(aid) > 40 or not title or typ not in AUDIO_TYPES or dur <= 0:
        return None

    def vol(k, d):
        try:
            return max(0, min(100, int(raw.get(k, d))))
        except (TypeError, ValueError):
            return d
    out = {"audio_id": aid, "title": title[:200], "type": typ, "duration_ms": dur,
           "music_volume": vol("music_volume", 100), "original_volume": vol("original_volume", 100)}
    for k in ("display_artist", "creator_username", "cover_url"):
        v = str(raw.get(k) or raw.get({"display_artist": "displayArtist", "creator_username": "creatorUsername",
                                       "cover_url": "coverArtworkUrl"}[k]) or "").strip()
        if v:
            out[k] = v[:500] if k == "cover_url" else v[:200]
    try:
        st = int(raw.get("start_ms") or 0)
    except (TypeError, ValueError):
        st = 0
    if st > 0:  # only a start someone chose is kept (the UI has no start: Instagram picks the part)
        out["start_ms"] = min(st, max(0, dur - 1000))
    return out


def audio_input(au: dict) -> dict:
    """The exact InstagramAudioSelectionInput Buffer takes (probed against api.buffer.com, 2 Nov 2026)."""
    i = {"audioId": au["audio_id"], "title": au["title"], "duration": int(au["duration_ms"]), "type": au["type"],
         "audioVolume": int(au.get("music_volume", 100)), "videoVolume": int(au.get("original_volume", 100))}
    if au.get("display_artist"):
        i["displayArtist"] = au["display_artist"]
    if au.get("creator_username"):
        i["creatorUsername"] = au["creator_username"]
    return i


def clean_options(raw) -> dict:
    """Network options a post may carry (YouTube title/category, Pinterest board/title, Threads topic, X long posts)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in OPTION_KEYS:
        v = raw.get(k)
        if v in (None, "", False):
            continue
        if k in ("x_long_posts", "made_for_kids"):
            out[k] = v is True
        elif k == "thumbnail_offset_ms":
            try:
                out[k] = max(0, int(v))
            except (TypeError, ValueError):
                continue
        elif k in ("ig_type", "fb_type"):
            if str(v) in ("reel", "story", "post"):
                out[k] = str(v)
        elif k == "alt_text":
            vals = v if isinstance(v, list) else [v]
            alts = [str(x or "").strip()[:1000] for x in vals[:10]]
            if any(alts):
                out[k] = alts
        elif k == "first_comment":
            out[k] = str(v).strip()[:2200]
        elif k == "ig_audio":
            au = clean_audio(v)
            if au:
                out[k] = au
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
        self.media_reach = None   # urls -> problems; tests swap this. Production reads each link the way Buffer does
        self._reach_http = None

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

    def _media_path(self, url: str):
        """The local copy of our own /m/ file (studio/mediacache.py), so preflight probes it from disk; None otherwise
        (another host, a sample, the cache off): preflight then loads the bytes as before."""
        base = self.s.public_url.rstrip("/") + "/m/"
        if not self.house or not url.startswith(base):
            return None
        getp = getattr(self.house, "media_path", None)
        return getp(url[len(base):].split("?")[0]) if getp else None

    def _reach(self, urls: list[str]) -> list[str]:
        """Can Buffer download every media link? Read each one from the outside, HEAD then a byte range, like Buffer's
        fetcher (studio/mediareach.py). Test apps that fake media downloads skip the network unless they set media_reach."""
        if self.media_reach:
            return self.media_reach(urls)
        if self.media_loader:
            return []
        if self._reach_http is None:
            import httpx
            self._reach_http = httpx.Client(timeout=mediareach.TIMEOUT)
        return mediareach.check(urls, self._reach_http)

    def _preflight(self, ch: dict, text: str, media_urls: list[str], mode: str, options: dict | None = None,
                   probe: bool = True, creating: bool = True) -> dict:
        return preflight.check(ch, text, media_urls, mode, options, loader=self._load_media, probe_media=probe, creating=creating,
                               known=self._known_probe, path_loader=self._media_path)

    def _fix_audio(self, a: Actor, ch: dict, text: str, urls: list[str], mode: str, options: dict | None,
                   pf: dict) -> tuple[list[str], dict, list[str]]:
        """Instagram's 128 kbps audio warning is fixed here, never handed to the person or the agent to override: each video
        whose finding is fix "audio:<i>" gets its sound re-encoded on the video worker (picture copied, house.fix_audio),
        then preflight runs again. Returns (urls, preflight, notes); unchanged when nothing needed it or the fix couldn't run
        (the warning then stays, with the reason in notes)."""
        idx = [i for i in preflight.audio_fix_ids([pf]) if i < len(urls)]
        if not idx or not self.house:
            return urls, pf, []
        new, notes = list(urls), []
        for i in idx:
            got, why = self.house.fix_audio(a, urls[i], None if urls[i].startswith(self.s.public_url.rstrip("/") + "/m/")
                                            else self._load_media(urls[i]))
            if got:
                new[i] = got
            else:
                notes.append(f"Couldn't fix the sound of media {i + 1} yet ({why}).")
        if new == urls:
            return urls, pf, notes
        pf2 = self._preflight(ch, text, new, mode, options)
        self.store.audit(a.uid, a.label, "fix_audio", "read", {"channel": ch["service"], "media": idx, "after": pf2["status"]})
        return new, pf2, notes + [f"Re-encoded the sound of {len(idx)} video(s) to {preflight.AUDIO_FIX_KBPS} kbps for "
                                  "Instagram (the picture is unchanged)."]

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
        self._guard_write(a, "autofix_post")  # SECURITY_RULES 4
        chans = {c["id"]: c for c in self.channels(a)}
        ch = chans.get(p.get("channel_id") or "")
        if not ch:
            raise ActionError("Unknown channel, or this agent token isn't allowed to use it.", 404)
        text, urls, opts, mode = p.get("text") or "", self._media_list(p), clean_options(p.get("options")), p.get("mode") or "queue"
        if p.get("youtube") not in (None, {}):  # youtube: {title, categoryId, madeForKids} (6 Nov 2026: preflight blocked YouTube)
            if not isinstance(p["youtube"], dict):
                raise ActionError("youtube is {title, categoryId, madeForKids}.")
            opts = {**opts, **preflight.youtube_options(p["youtube"])}
        before = self._preflight(ch, text, urls, mode, opts)
        want = p.get("fixes") or "all"
        ids = [f["id"] for f in before["fixes"]] if want == "all" else [str(x) for x in want]
        ids = [i for i in ids if i != "reencode"]
        if "audio" in ids:  # fixes=['audio']: every video whose sound is over Instagram's spec
            ids = [i for i in ids if i != "audio"] + [f["id"] for f in before["fixes"] if f["id"].startswith("audio:")]
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
        aud, fix_notes = [fid for fid in ids if fid.startswith("audio:")], []
        after = self._preflight(ch, text2, urls2, mode, opts2)
        if aud:  # the sound only, on the video worker (picture copied)
            want_idx = {int(f[6:]) for f in aud if f[6:].isdigit()}
            keep_pf = {**after, "findings": [f for f in after["findings"] if str(f.get("fix") or "") in {f"audio:{i}" for i in want_idx}]}
            urls3, after, fix_notes = self._fix_audio(a, ch, text2, urls2, mode, opts2, keep_pf)
            if urls3 != urls2:
                urls2 = urls3
                applied += [f"audio:{i}" for i in sorted(want_idx)]
            else:
                after = self._preflight(ch, text2, urls2, mode, opts2)
        self.store.audit(a.uid, a.label, "autofix_post", "read", {"channel": ch["service"], "applied": applied,
                                                                   "before": before["status"], "after": after["status"]})
        return {"channel_id": ch["id"], "service": ch["service"], "text": text2, "media_urls": urls2,
                "media_url": urls2[0] if urls2 else "", "options": opts2, "applied": applied,
                "before": {"text": text, "media_urls": urls, "status": before["status"]}, "preflight": after,
                "needs_reencode": any(f.get("fix") == "reencode" for f in after["findings"]), "notes": fix_notes}

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
        self._guard_write(a, "draft_from_link")
        chans = [c for c in self.channels(a) if not c.get("isDisconnected")]
        if channel_ids:
            chans = [c for c in chans if c["id"] in channel_ids]
        if not chans:
            raise ActionError("No connected Buffer channels selected.")
        sample = None
        if demo.is_sample_url(url) or (demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "research")):
            article, sample = dict(demo.SAMPLE_ARTICLE), demo_guard.why("research").replace("the agent used", "this uses")
        else:
            try:
                article = drafter.fetch_article(url)
            except drafter.FetchError as e:
                raise ActionError(str(e))
        ai = {"base_url": self.s.ai_base_url, "api_key": self.s.ai_api_key, "model": self.s.ai_model}
        from . import opsctl
        if opsctl.paused("ai", self.store):  # the operator's AI kill switch: the rule drafter, no call on our key
            ai = {"base_url": "", "api_key": "", "model": ""}
        if ai["base_url"] and demo_guard.is_demo(a.uid):  # the free sample's AI allowance (not spent while AI is paused)
            if not demo_guard.reserve(self.store, a.uid, "ai"):
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
        """The For you feed: the newest stories from the feeds you follow, filtered and ranked by the interests you
        accepted in setup (onboarding content types, kept per account), the same way on every refresh."""
        from . import onboarding, starter_feeds
        if a.kind == "human":  # a new account's first open: follow the starter feeds so News is never empty
            starter_feeds.sync(self.store, a.uid, first_run_only=True)
        feeds = self.feeds(a)
        out = {"feeds": feeds, **radar.stories(feeds, limit=80)}
        if out["stories"] and out["errors"]:  # a starter feed that's down is not the person's problem; their own feeds' errors stay
            mine = set(starter_feeds.mine(self.store, a.uid))
            out["errors"] = [e for e in out["errors"] if e["feed"] not in mine]
        if demo_guard.is_demo(a.uid) and not out.get("stories"):  # the sample's feeds couldn't be read: show sample stories
            out["stories"], out["sample"] = [dict(s) for s in demo.SAMPLE_STORIES], True
        via = {f["id"]: starter_feeds.TYPES_OF.get(f["url"], ()) for f in feeds}
        out.update(onboarding.rank_stories(out["stories"], onboarding.interests(self.store, a.uid), via=via))
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
            raise ActionError(demo_guard.why("feed"), 429)
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
        from . import starter_feeds
        for r in self.store.q("SELECT url FROM feeds WHERE id=? AND user_id=?", (feed_id, a.uid)):
            starter_feeds.forget(self.store, a.uid, r["url"])  # a starter feed they unfollowed is never added back
        self.store.x("DELETE FROM feeds WHERE id=? AND user_id=?", (feed_id, a.uid))
        return {"removed": feed_id}

    # ------------------------------------------------------------ guards
    # Every agent action that does something (SECURITY_RULES 4). The kill switch stops all of them; each one counts
    # against the token's daily cap (the audit action names they are recorded under).
    COUNTED = ("submit_post", "create_idea", "update_post", "delete_draft", "agent_run", "clips_from_video", "adjust_clip",
               "remember_rule", "revise", "draft_from_link", "autofix_post", "place_clips_in_week", "save_brand_kit",
               "upload_media", "import_media", "brand_from_website", "follow_feed",
               "fetch_image", "cutout_product", "edit_image",
               "set_clip_layout")  # One take per-scene layouts (studio/take_layout.py): each re-renders a clip; image tools (studio/image_edit.py): compose_product_card is an edit_image

    def kill_switch_check(self, a: Actor, action: str) -> None:
        """The kill switch alone (any agent request that changes something, counted or not)."""
        if a.kind != "agent":
            return
        u = self.store.user(a.uid)
        if u and u.get("kill_switch"):
            self.store.audit(a.uid, a.label, action, "blocked", {"reason": "kill switch on"}, ok=False)
            raise ActionError("Agent actions are paused: the kill switch is on in HuhQueue.", 423)

    # The token's daily cap counts and blocks Buffer writes only (owner, 6 Nov 2026: 20 renders used up a cap of 10 before
    # one draft was saved). Renders, previews, image edits, runs and rules are free of it; the kill switch still stops all.
    BUFFER_WRITES = ("submit_post", "update_post", "replace_draft", "delete_draft", "create_idea")
    CAP_DEFAULT, CAP_MAX, CAP_SAMPLE = 50, 500, 10

    def _guard_write(self, a: Actor, action: str) -> None:
        """Kill switch (every agent action) + the token's daily cap (Buffer writes only), before an agent action runs."""
        if a.kind != "agent":
            return
        self.kill_switch_check(a, action)
        if action not in self.BUFFER_WRITES:
            return
        day = time.time() - 86400
        marks = ",".join("?" * len(self.BUFFER_WRITES))
        row = self.store.q(f"SELECT COUNT(*) n, MIN(ts) t FROM audit WHERE user_id=? AND actor=? AND action IN ({marks}) "
                           "AND ok=1 AND ts>?", (a.uid, a.label, *self.BUFFER_WRITES, day))[0]
        cap = int(a.agent["daily_cap"])
        if int(row["n"] or 0) >= cap:
            reset = float(row["t"] or time.time()) + 86400  # rolling 24 h: the oldest counted write drops out then
            at = time.strftime("%H:%M UTC on %d %b", time.gmtime(reset))
            self.store.audit(a.uid, a.label, action, "blocked", {"reason": "daily cap", "cap": cap, "resets": int(reset)}, ok=False)
            raise ActionError(f"This agent token reached its daily cap: {cap} Buffer posts per day at most. Only Buffer writes count "
                              "(posts, drafts, draft edits and deletes, ideas); renders, previews and image edits don't. "
                              f"The next one frees up at {at} (24 hours after the oldest counted write).", 429)

    def token_policy(self, a: Actor) -> dict | None:
        """What this agent token may do, said up front (preview_post, describe_tool, initialize, policy errors)."""
        if a.kind != "agent" or not a.agent:
            return None
        pol = a.agent.get("policy") or "review"
        return {"policy": pol, "label": POLICY_LABELS.get(pol, pol), "allowed_modes": sorted(MODES) if pol != "draft" else ["draft"],
                "needs_approval": pol == "review", "change_at": self.s.public_url.rstrip("/") + "/?tab=settings#tokens",
                "how_to_change": "The owner changes it in HuhQueue → Settings → Apps with access (token policy)."}

    def _policy_gate(self, a: Actor, mode: str) -> None:
        """A drafts-only token never asks for a schedule / queue / now (owner report 6 Nov 2026: schedule_package and
        submit_post(mode=schedule) on a drafts-only token silently made Buffer drafts with a dueAt). Before any Buffer call."""
        if a.kind != "agent" or not a.agent or a.agent.get("policy") != "draft" or mode == "draft":
            return
        from .mcp_flow import StepError
        pol = self.token_policy(a)
        self.store.audit(a.uid, a.label, "policy_refused", "blocked", {"mode": mode, "policy": "draft"}, ok=False)
        raise StepError(f"This connection is drafts-only, so mode '{mode}' isn't allowed and nothing was sent to Buffer. Save it "
                        f"with mode='draft', or ask the owner to allow scheduling in Agents › token policy: {pol['change_at']}",
                        403, code="policy_drafts_only", token_policy=pol, allowed_modes=["draft"],
                        next_steps=["the same call with mode='draft'", f"or the owner allows scheduling: {pol['change_at']}"])

    def _validate(self, a: Actor, p: dict) -> tuple[dict, dict, list[str], dict]:
        mode = (p.get("mode") or "draft").strip()
        if mode not in MODES:
            raise ActionError(f"mode must be one of {sorted(MODES)}")
        self._policy_gate(a, mode)
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
                raise _past(p.get("due_at"))
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
        raw_audio = (p.get("options") or {}).get("ig_audio") if isinstance(p.get("options"), dict) else None
        if raw_audio and not opts.get("ig_audio"):
            raise ActionError("That music choice is incomplete: pick the track again (it needs its id, title, type and length).")
        if opts.get("ig_audio"):
            if ch["service"] != "instagram":
                raise ActionError("Music can only be added to Instagram Reels.")
            if not any(u.lower().split("?")[0].endswith(VIDEO_EXT) for u in ([media] if media else []) + media_urls):
                raise ActionError("Music goes on a Reel: this Instagram post has no video, so remove the music or add a video.")
        override = bool(p.get("override_warnings"))
        urls0 = media_urls or ([media] if media else [])
        pf = self._preflight(ch, text, urls0, mode, opts)
        urls1, pf, notes = self._fix_audio(a, ch, text, urls0, mode, opts, pf)
        warnings += notes
        if urls1 != urls0:
            if media_urls:
                media_urls = urls1
            media = urls1[0] if urls1 else media
        msg = preflight_error([pf], override)
        if msg:
            raise ActionError(" ".join([msg, *notes]))
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
                "warnings": warnings, "preflight": pf, "confirm_token": token, "expires_in": CONFIRM_TTL, "on_submit": what,
                **({"token_policy": self.token_policy(a)} if a.kind == "agent" else {})}

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
        if not (a.kind == "agent" and a.agent["policy"] == "review"):  # a held post waits for approval: holding it still works
            self._publishing_open(a)                                   # before the confirm token is used up
        self.store.x("UPDATE confirms SET used=1 WHERE token_hash=?", (row["token_hash"],))
        clean = json.loads(row["payload"])
        self._policy_gate(a, clean.get("mode") or "draft")
        if a.kind == "agent" and a.agent["policy"] == "review":
            aid = self.store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                               (a.uid, a.agent["id"], a.agent["name"], json.dumps(clean), "pending", time.time()))
            self.store.audit(a.uid, a.label, "submit_post", "held", {"approval_id": aid, "mode": clean["mode"]})
            return {"status": "pending_approval", "approval_id": aid,
                    "message": "Waiting for a human to approve it in HuhQueue → Approvals."}
        if a.kind == "agent" and a.agent["policy"] == "draft":
            clean["mode"] = "draft"
        return self._send(a, clean, action="submit_post")

    def _publishing_open(self, a: Actor) -> None:
        """The operator's publishing kill switch (studio/opsctl.py), before any send work. The sample workspace never
        reaches Buffer, so it is not stopped; BufferClient.create_post / edit_post keep the same guard as a backstop."""
        if demo.is_demo(a.uid):
            return
        from . import opsctl
        if opsctl.paused("publishing", self.store):
            raise BufferError(opsctl.message("publishing"), 503)

    def _send(self, a: Actor, clean: dict, action: str) -> dict:
        self._publishing_open(a)
        if clean.get("due_at"):  # a confirm made earlier can be past its time now: never a dueAt in the past (6 Nov 2026)
            self._due(clean["due_at"])
        ch = {c["id"]: c for c in self.channels(Actor(a.uid))}.get(clean["channel_id"])
        if not ch:
            raise ActionError("That channel is no longer in your Buffer organization.", 404)
        urls = clean.get("media_urls") or ([clean["media_url"]] if clean["media_url"] else [])
        pf = self._preflight(ch, clean["text"], urls, clean["mode"], clean.get("options"))  # re-check right before createPost
        urls2, pf, _notes = self._fix_audio(a, ch, clean["text"], urls, clean["mode"], clean.get("options"), pf)
        if urls2 != urls:  # a post that skipped preview (an approval, a placed clip): its sound is fixed here
            urls = urls2
            clean = {**clean, "media_url": urls2[0]}
            if clean.get("media_urls"):
                clean["media_urls"] = urls2
        msg = preflight_error([pf], bool(clean.get("override_warnings")))
        if msg:
            self.store.audit(a.uid, a.label, action, "blocked", {"channel": ch["service"], "preflight": pf["status"]}, ok=False)
            raise ActionError(" ".join([msg, *_notes]))
        risk = "draft" if clean["mode"] == "draft" else "public"
        if urls and not demo_guard.is_demo(a.uid):  # Buffer downloads each link while it creates the post: check them first
            problems = self._reach(urls)
            if problems:
                msg = mediareach.message(problems)
                self.store.audit(a.uid, a.label, action, risk, {"channel": ch["service"], "mode": clean["mode"],
                                                                 "error": msg[:600], "stage": "media check"}, ok=False)
                raise ActionError(msg, 422)
        inp = build_post_input(ch, clean["text"], clean["media_url"], clean["mode"], clean.get("due_at"), clean.get("media_urls"),
                               clean.get("options"))
        try:
            post = self.buffer.create_post(a.uid, inp)
        except BufferError as e:
            self.store.audit(a.uid, a.label, action, risk, {"channel": ch["service"], "mode": clean["mode"], "error": str(e)}, ok=False)
            if "could not be read from its url" in str(e).lower():
                raise BufferError(f"{e} HuhQueue read these links like Buffer does just before sending and they worked, so Buffer "
                                  "may have timed out: try again in a minute. If it keeps failing, upload the file to the Library "
                                  "and use it from there.", e.status) from e
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
            raise _past(value)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _find_post(self, a: Actor, post_id: str) -> tuple[dict, dict]:
        u = self.store.user(a.uid)
        try:
            self.buffer.prefetch_posts(a.uid, u["org_id"], ("scheduled", "draft", "error", "sent"))
        except Exception:  # noqa: BLE001  (the per-status reads below surface any real error)
            pass
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

    def replace_draft(self, a: Actor, post_id: str, p: dict) -> dict:
        """Put new media (and caption) on a Buffer DRAFT a Make a post package made, after the post was re-rendered or its
        template changed (editPost: id, text, assets, metadata, saveToDraft). Only a draft: a scheduled or sent post is
        never changed here, so a re-render can never touch what publishes. Returns {status: updated, post}."""
        post, ch = self._find_post(a, post_id)
        if post["status"] != "draft":
            raise ActionError("That post is no longer a draft in Buffer (it was scheduled or sent), so it isn't changed here.", 409)
        if p.get("channel_id") and p["channel_id"] != ch["id"]:
            raise ActionError("That Buffer draft is on another channel.", 409)
        self._guard_write(a, "replace_draft")
        clean, _ch, warnings, _pf = self._validate(a, {**p, "channel_id": ch["id"], "mode": "draft", "due_at": None})
        urls = clean.get("media_urls") or ([clean["media_url"]] if clean["media_url"] else [])
        if urls and not demo_guard.is_demo(a.uid):
            problems = self._reach(urls)
            if problems:
                raise ActionError(mediareach.message(problems), 422)
        full = build_post_input(ch, clean["text"], clean["media_url"], "draft", None, clean.get("media_urls"), clean.get("options"))
        inp = {"id": post_id, "text": full["text"], "assets": full["assets"], "saveToDraft": True}
        if full.get("metadata"):
            inp["metadata"] = full["metadata"]
        try:
            out = self.buffer.edit_post(a.uid, inp)
        except BufferError as e:
            self.store.audit(a.uid, a.label, "replace_draft", "draft", {"buffer_post_id": post_id, "error": str(e)}, ok=False)
            raise
        self.store.audit(a.uid, a.label, "replace_draft", "draft", {"buffer_post_id": post_id, "channel": ch["service"],
                                                                    "assets": len(inp["assets"])})
        return {"status": "updated", "post": out, "service": ch["service"], "warnings": warnings}

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
        from . import consent
        if consent.row_kind(rows[0]) != "post":
            raise ActionError("That approval is a request to use a paid API or fetch a video link, not a post: it has no time.", 409)
        clean = json.loads(rows[0]["payload"])
        clean.update(due_at=self._due(due_at), mode="schedule")
        self.store.x("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(clean), approval_id))
        self.store.audit(a.uid, a.label, "reschedule_approval", "read", {"approval_id": approval_id, "due_at": clean["due_at"]})
        return {"status": "pending_approval", "approval_id": approval_id, "due_at": clean["due_at"]}

    # ------------------------------------------------------------ approvals (human only)
    def approvals(self, a: Actor, status: str = "pending", kind: str = "all") -> list[dict]:
        """Approval items: agent posts (kind post) and, since 4 Nov 2026, requests to use a paid API (kind paid_api,
        studio/consent.py). Internal readers that expect a post payload pass kind='post'."""
        out = []
        # consent.py writes payload with json.dumps defaults; the Python check below confirms (link_rights: video links)
        likes = {"post": ('%"kind": "paid_api"%', '%"kind": "link_rights"%'), "paid_api": ('%"kind": "paid_api"%',),
                 "link_rights": ('%"kind": "link_rights"%',)}.get(kind, ())
        cond = (" AND payload NOT LIKE ? AND payload NOT LIKE ?" if kind == "post" else " AND payload LIKE ?") if likes else ""
        from . import consent
        for r in self.store.q(f"SELECT * FROM approvals WHERE user_id=? AND status=?{cond} ORDER BY id DESC LIMIT 50",
                              (a.uid, status, *likes)):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            d["kind"] = consent.row_kind(d)
            if kind in ("post", "paid_api", "link_rights") and d["kind"] != kind:
                continue
            out.append(d)
        return out

    def decide(self, a: Actor, approval_id: int, approve: bool, text: str | None = None, mode: str | None = None,
               rights_attested: bool = False, attestation_version: object = None) -> dict:
        if a.kind != "human":
            raise ActionError("Only a signed-in human can approve or reject agent posts.", 403)
        rows = self.store.q("SELECT * FROM approvals WHERE id=? AND user_id=?", (approval_id, a.uid))
        if not rows or rows[0]["status"] != "pending":
            raise ActionError("No pending approval with that id.", 404)
        from . import consent
        if consent.row_kind(rows[0]) == consent.LINK_KIND:  # an AI app asking to fetch a video link: the person attests the rights
            return consent.decide_link(self.store, a, dict(rows[0]), approve, rights_attested, attestation_version)
        if consent.is_paid_row(rows[0]):  # an AI app asking to spend money or credits: approving lets that one call run
            return consent.decide(self.store, a, dict(rows[0]), approve)
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

    def create_agent_token(self, a: Actor, name: str, policy: str = "review", daily_cap: int = 50,
                           channels: list[str] | None = None, days: int = 30) -> dict:
        if a.kind != "human":
            raise ActionError("Agents can't mint agent tokens.", 403)
        if policy not in POLICIES:
            raise ActionError(f"policy must be one of {POLICIES}")
        name = (name or "my agent").strip()[:40]
        sample = demo.is_demo(a.uid)
        if sample:  # a sample token only ever reaches the sample workspace, and dies with it
            days, daily_cap = 1, min(int(daily_cap), self.CAP_SAMPLE)
        token = "hq_" + secrets.token_urlsafe(32)
        tid = self.store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) "
                           "VALUES(?,?,?,?,?,?,?,?)", (a.uid, name, sha(token), policy, max(1, min(int(daily_cap), self.CAP_MAX)),
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
        rows = self.store.q("SELECT id,name,policy,daily_cap,channels,created,expires,revoked,last_used,allow_paid_apis FROM agent_tokens "
                            "WHERE user_id=? ORDER BY id DESC", (a.uid,))
        return [dict(r) | {"channels": json.loads(r["channels"]) if r["channels"] else None, "allow_paid_apis": bool(r["allow_paid_apis"])}
                for r in rows]

    def set_agent_policy(self, a: Actor, token_id: int, policy: Any, confirm: Any = None) -> dict:
        """The token policy switch in Settings → Apps with access: drafts only / publish needs approval / can schedule.
        Humans only (an agent never widens its own policy), an explicit confirm: true, and an audit entry with from/to."""
        if a.kind != "human":
            raise ActionError("Only the owner can change an app's policy, in HuhQueue → Settings → Apps with access.", 403)
        if policy not in POLICIES:
            raise ActionError(f"policy must be one of {', '.join(POLICIES)}.")
        if confirm is not True:
            raise ActionError("Confirm the change (confirm: true).", 400)
        if policy == "trusted" and demo.is_demo(a.uid):
            raise ActionError("The sample workspace never schedules.", 403)
        rows = self.store.q("SELECT id,policy FROM agent_tokens WHERE id=? AND user_id=? AND revoked=0", (token_id, a.uid))
        if not rows:
            raise ActionError("No such token.", 404)
        was = rows[0]["policy"]
        if was != policy:
            self.store.x("UPDATE agent_tokens SET policy=? WHERE id=? AND user_id=?", (policy, token_id, a.uid))
            self.store.audit(a.uid, a.label, "set_agent_policy", "safety", {"id": token_id, "from": was, "to": policy})
        return {"id": token_id, "policy": policy, "label": POLICY_LABELS[policy], "was": was, "changed": was != policy}

    def set_agent_paid(self, a: Actor, token_id: int, on: bool) -> dict:
        """The person's "Allow paid APIs" switch for one app (studio/consent.py). Off by default; humans only, so an agent
        can never let itself spend money or credits (SECURITY_RULES 4). The sample workspace keeps it off."""
        if a.kind != "human":
            raise ActionError("Only a human can let an app use paid APIs.", 403)
        if not isinstance(on, bool):
            raise ActionError("allow_paid_apis is true or false.")
        if on and demo.is_demo(a.uid):
            raise ActionError("The sample workspace never uses paid APIs.", 403)
        rows = self.store.q("SELECT id,allow_paid_apis FROM agent_tokens WHERE id=? AND user_id=? AND revoked=0", (token_id, a.uid))
        if not rows:
            raise ActionError("No such token.", 404)
        self.store.x("UPDATE agent_tokens SET allow_paid_apis=? WHERE id=? AND user_id=?", (1 if on else 0, token_id, a.uid))
        self.store.audit(a.uid, a.label, "set_agent_paid_apis", "safety", {"id": token_id, "from": bool(rows[0]["allow_paid_apis"]), "to": on})
        return {"id": token_id, "allow_paid_apis": on}

    def set_agent_cap(self, a: Actor, token_id: int, daily_cap: int) -> dict:
        """Change a token's daily cap (1-500 Buffer posts a day) without minting a new one. Humans only: an agent can never raise
        its own limit (SECURITY_RULES 4). Sample-workspace tokens stay at 10."""
        if a.kind != "human":
            raise ActionError("Only a human can change a token's daily limit.", 403)
        try:
            cap = int(daily_cap)
        except (TypeError, ValueError):
            raise ActionError("daily_cap must be a whole number from 1 to 500.")
        if not 1 <= cap <= self.CAP_MAX:
            raise ActionError("daily_cap must be a whole number from 1 to 500.")
        if demo.is_demo(a.uid):
            cap = min(cap, self.CAP_SAMPLE)
        rows = self.store.q("SELECT id,daily_cap FROM agent_tokens WHERE id=? AND user_id=? AND revoked=0", (token_id, a.uid))
        if not rows:
            raise ActionError("No such token.", 404)
        self.store.x("UPDATE agent_tokens SET daily_cap=? WHERE id=? AND user_id=?", (cap, token_id, a.uid))
        self.store.audit(a.uid, a.label, "set_agent_cap", "safety", {"id": token_id, "from": rows[0]["daily_cap"], "to": cap})
        return {"id": token_id, "daily_cap": cap}

    def revoke_agent_token(self, a: Actor, token_id: int) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can revoke tokens.", 403)
        self.store.x("UPDATE agent_tokens SET revoked=1 WHERE id=? AND user_id=?", (token_id, a.uid))
        self.store.audit(a.uid, a.label, "revoke_agent_token", "safety", {"id": token_id})
        return {"revoked": token_id}

    def resolve_agent(self, token: str) -> Actor | None:
        # a blocked account's tokens stop working at once and start again when it is unblocked (studio/admin.py)
        rows = self.store.q("SELECT t.* FROM agent_tokens t LEFT JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.revoked=0 "
                            "AND t.expires>? AND COALESCE(u.blocked,0)=0", (sha(token), time.time()))
        if not rows:
            return None
        row = dict(rows[0])
        self.store.x("UPDATE agent_tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
        self.store.touch(row["user_id"])
        from . import activity
        activity.note(agent={k: row.get(k) for k in ("id", "name", "client_id", "client_info", "app")})  # every write is attributable
        return Actor(row["user_id"], "agent", row)

    def note_client(self, a: Actor, client_info: str) -> None:
        """Remember which MCP client (its clientInfo name/version) is using this token, and which app that is."""
        if a.kind != "agent" or not client_info:
            return
        from . import activity
        info = activity.clean_text(client_info, 120)
        app = a.agent.get("app") or activity.classify_app(a.agent.get("name", ""), info)
        if info != a.agent.get("client_info") or app != a.agent.get("app"):
            self.store.x("UPDATE agent_tokens SET client_info=?, app=? WHERE id=?", (info, app, a.agent["id"]))
            a.agent.update(client_info=info, app=app)
        activity.note(client_info=info, agent={k: a.agent.get(k) for k in ("id", "name", "client_id", "client_info", "app")})

    def activity(self, a: Actor, f: dict) -> dict:
        """The activity log page (UI, REST and MCP get_activity). Agents get every category but never sign-in IPs."""
        from . import activity
        try:
            out = activity.page(self.store, a.uid, f, show_ip=a.kind == "human")
            if (f.get("tab") or "all") in ("agent", "all"):
                out["agents"] = activity.agents(self.store, a.uid, f)
            if (f.get("tab") or "all") in ("system", "all"):
                out["buffer"] = activity.buffer_usage(self.store, a.uid, self.buffer.budget.snapshot())
        except ValueError as e:
            raise ActionError(str(e))
        return out

    def audit(self, a: Actor, limit: int = 100) -> list[dict]:
        return self.store.audit_rows(a.uid, limit)
