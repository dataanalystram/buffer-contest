"""Brand studio: the brand kit's front and end cards, reusable post templates per channel, the two example brands, and
recurring template slots on the Planner (UI = REST = MCP).

The brand kit itself (name, handle, colours, logo, voice) stays in House; this adds what templates need on top of it:
a body font, the headline case, the FRONT card (an uploaded image or a style) and the END card style or image, default
hashtags, a "not affiliated" label for concept examples, and which channels the brand posts to.

Security: renders run in-process with Pillow (no network). The sample workspace never fetches anything and never renders
video live (it shows the pre-rendered example Reels); its renders count against the sample's daily "run" cap. Agent
writes pass the kill switch and the token's daily cap (Studio._guard_write). Slots only ever create Buffer DRAFTS.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import brand, brand_examples, demo_guard, templates

SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_studio (user_id TEXT PRIMARY KEY, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS template_slots (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, template TEXT, channels TEXT,
  weekday TEXT, at TEXT, tz TEXT, active INTEGER DEFAULT 1, last_fire REAL, next_fire REAL, last_result TEXT, created REAL);
"""
EXTRA_KEYS = ("head_font", "body_font", "headline_case", "secondary", "front_card", "end_style", "end_image", "hashtags", "label",
              "example", "channels")
MAX_SLOTS = 12
PREVIEWS_PER_MIN = 40  # live preview redraws per workspace per minute (CPU guard; SECURITY_RULES 3)
_previews: dict[str, list[float]] = {}


class StudioError(ValueError):
    pass


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        store.x(stmt)
    if store.pg:
        for t in ("brand_studio", "template_slots"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


class BrandStudio:
    def __init__(self, house):
        self.house, self.studio, self.store = house, house.studio, house.store
        ensure(self.store)

    # ================================================================ kit
    def extras(self, a) -> dict:
        rows = self.store.q("SELECT data FROM brand_studio WHERE user_id=?", (a.uid,))
        return json.loads(rows[0]["data"]) if rows else {}

    def kit(self, a) -> dict:
        """The House kit plus the template extras, in the shape templates.Brand reads."""
        k = dict(self.house.kit(a))
        ex = self.extras(a)
        k["colors"] = dict(k.get("colors") or {})
        if ex.get("secondary"):
            k["colors"]["secondary"] = ex["secondary"]
        k["font"] = ex.get("head_font") or k.get("font") or "anton"
        k["body_font"] = ex.get("body_font") or ("poppins" if k["font"] == "anton" else k["font"])
        k["headline_case"] = ex.get("headline_case") or ("upper" if k["font"] == "anton" else "title")
        fc = dict(ex.get("front_card") or {})
        k["front_card"] = {"style": fc.get("style") or "glow", "kicker": fc.get("kicker") or "", "image": fc.get("image") or ""}
        ec = dict(k.get("end_card") or {})
        ec["style"] = ex.get("end_style") or k["front_card"]["style"]
        ec["image"] = ex.get("end_image") or ""
        k["end_card"] = ec
        k["hashtags"] = ex.get("hashtags") or []
        k["label"] = ex.get("label") or ""
        k["example"] = ex.get("example") or ""
        k["channels"] = ex.get("channels") or []
        k["front_url"] = self.house.media_url(k["front_card"]["image"]) if k["front_card"]["image"] else ""
        k["end_url"] = self.house.media_url(ec["image"]) if ec["image"] else ""
        k["first_run"] = {"logo": bool(k.get("logo")), "style": bool(ex.get("front_card")), "examples_seen": bool(ex.get("seen_examples")),
                          "buffer": not demo_guard.is_demo(a.uid)}
        return k

    def save(self, a, patch: dict) -> dict:
        """Save base fields through House (name, handle, colours, logo, voice, end card text) and the template extras here."""
        self.studio._guard_write(a, "save_brand_studio")
        base = {k: patch[k] for k in ("name", "handle", "website", "logo", "voice") if k in patch}
        if isinstance(patch.get("colors"), dict):
            base["colors"] = {k: v for k, v in patch["colors"].items() if k in ("primary", "accent", "text")}
        if isinstance(patch.get("end_card"), dict):
            base["end_card"] = {k: v for k, v in patch["end_card"].items() if k in ("on", "line", "cta")}
        if patch.get("font") in brand.FONTS:
            base["font"] = patch["font"]
        for tok_key in ("logo",):
            if base.get(tok_key) and not self.house.media_meta(a, base[tok_key]):
                raise StudioError("Unknown logo media token. Upload the logo first.")
        if base:
            self.house.save_kit(a, base)
        ex = self.extras(a)
        if patch.get("font") in templates.HEAD_FONTS:
            ex["head_font"] = patch["font"]
        if patch.get("body_font") in templates.BODY_FONTS:
            ex["body_font"] = patch["body_font"]
        if patch.get("headline_case") in ("upper", "title"):
            ex["headline_case"] = patch["headline_case"]
        sec = ((patch.get("colors") or {}).get("secondary") or "").strip()
        if sec and brand.HEX_RE.fullmatch(sec):
            ex["secondary"] = "#" + sec.lstrip("#").upper()
        if isinstance(patch.get("front_card"), dict):
            fc = dict(ex.get("front_card") or {})
            f = patch["front_card"]
            if f.get("style") in templates.STYLES:
                fc["style"] = f["style"]
            if "kicker" in f:
                fc["kicker"] = str(f.get("kicker") or "")[:24]
            if "image" in f:
                fc["image"] = self._token(a, f.get("image"))
            ex["front_card"] = fc
        if isinstance(patch.get("end_card"), dict):
            e = patch["end_card"]
            if e.get("style") in templates.STYLES:
                ex["end_style"] = e["style"]
            if "image" in e:
                ex["end_image"] = self._token(a, e.get("image"))
        if isinstance(patch.get("hashtags"), list):
            ex["hashtags"] = ["#" + re.sub(r"[^\w]", "", str(t))[:40] for t in patch["hashtags"][:8] if re.sub(r"[^\w]", "", str(t))]
        if "label" in patch:
            ex["label"] = str(patch.get("label") or "")[:60]
        if isinstance(patch.get("channels"), list):
            ex["channels"] = [c for c in patch["channels"] if c in templates.CHANNELS][:9]
        if patch.get("seen_examples"):
            ex["seen_examples"] = True
        self._put(a, ex)
        self.store.audit(a.uid, a.label, "save_brand_studio", "read", {"fields": sorted(patch)[:20]})
        return self.kit(a)

    def _token(self, a, tok) -> str:
        tok = str(tok or "")
        if tok and not self.house.media_meta(a, tok):
            raise StudioError("Unknown media token. Upload the card image first.")
        return tok

    def _put(self, a, ex: dict) -> None:
        self.store.x("INSERT INTO brand_studio(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,"
                     " updated=excluded.updated", (a.uid, json.dumps(ex), time.time()))

    def brand_obj(self, a, kit: dict | None = None) -> templates.Brand:
        k = kit or self.kit(a)
        logo = self.house._logo_bytes(k)
        front = self._bytes(k["front_card"].get("image"))
        end = self._bytes(k["end_card"].get("image"))
        return templates.Brand(k, logo, front, end)

    def _bytes(self, tok: str | None) -> bytes | None:
        if not tok:
            return None
        m = self.house.get_media(tok)
        return m["data"] if m else None

    # ================================================================ examples
    def examples(self, a=None) -> dict:
        return {"examples": brand_examples.listing(), "styles": templates.STYLES, "active": (self.extras(a).get("example") if a else "")}

    def apply_example(self, a, example_id: str) -> dict:
        ex = brand_examples.EXAMPLES.get(example_id)
        if not ex:
            raise StudioError(f"No example brand {example_id!r}. Pick one of {', '.join(brand_examples.EXAMPLES)}.", )
        self.studio._guard_write(a, "apply_brand_example")
        k = ex["kit"]
        logo_tok = self.house.put_media(a, brand_examples.logo_bytes(example_id), f"{example_id}-logo.png", kind="logo")
        self.save(a, {"name": k["name"], "handle": k["handle"], "website": k["website"], "voice": k["voice"], "logo": logo_tok,
                      "colors": k["colors"], "font": k["font"], "body_font": k["body_font"], "headline_case": k["headline_case"],
                      "front_card": {**k["front_card"], "image": ""}, "end_card": {**k["end_card"], "image": ""},
                      "hashtags": k["hashtags"], "label": k["label"], "channels": ex["channels"]})
        cur = self.extras(a)
        cur["example"] = example_id
        self._put(a, cur)
        return {"kit": self.kit(a), "example": brand_examples.listing()[list(brand_examples.EXAMPLES).index(example_id)]}

    # ================================================================ templates
    def list_templates(self) -> dict:
        return {"templates": [{"id": k, **v} for k, v in templates.TEMPLATES.items()], "channels": templates.CHANNELS,
                "styles": templates.STYLES}

    def preview(self, a, body: dict) -> dict:
        """Live preview while editing: the template in the kit as it is now, with any unsaved changes in `kit` laid over it.
        Small JPEGs as data: URLs, nothing stored."""
        tpl = body.get("template") or "weekly_recap"
        if tpl not in templates.TEMPLATES:
            raise StudioError("Unknown template.")
        if demo_guard.is_demo(a.uid) and not demo_guard.allowed(self.store, a.uid, "run"):
            raise StudioError(demo_guard.WHY["run"])
        now = time.time()
        hits = [t for t in _previews.get(a.uid, []) if now - t < 60]
        if len(hits) >= PREVIEWS_PER_MIN:
            raise StudioError("Too many preview redraws this minute. Pause for a moment.")
        _previews[a.uid] = hits + [now]
        kit = self.kit(a)
        over = body.get("kit") or {}
        if isinstance(over.get("colors"), dict):
            kit["colors"] = {**kit["colors"], **{k: v for k, v in over["colors"].items() if brand.HEX_RE.fullmatch(str(v or ""))}}
        for k in ("font", "body_font", "headline_case", "handle", "name", "label"):
            if over.get(k) is not None:
                kit[k] = str(over[k])[:60]
        if isinstance(over.get("front_card"), dict) and over["front_card"].get("style") in templates.STYLES:
            kit["front_card"] = {**kit["front_card"], "style": over["front_card"]["style"]}
        if isinstance(over.get("end_card"), dict):
            kit["end_card"] = {**kit["end_card"], **{k: str(v)[:60] for k, v in over["end_card"].items() if k in ("style", "cta", "line")}}
        ratio = body.get("ratio") or templates.CHANNELS.get(body.get("service") or "instagram", templates.CHANNELS["instagram"])["ratio"]
        b = self.brand_obj(a, kit)
        data = body.get("data") if isinstance(body.get("data"), dict) else None
        ims = templates.slides(tpl, data, b, ratio)
        w = max(120, min(int(body.get("width") or 360), 540))
        out = []
        for im in ims[: int(body.get("max") or 10)]:
            t = im.copy()
            t.thumbnail((w, w * 2))
            out.append("data:image/jpeg;base64," + base64.b64encode(templates.jpeg(t, 82)).decode())
        return {"template": tpl, "ratio": ratio, "slides": out, "count": len(ims)}

    def recap_data(self, a, template: str, now: datetime | None = None) -> dict:
        """The week's sent and scheduled posts -> recap stories (weekly: the last 7 days; mid-week: Monday to Wednesday)."""
        now = now or datetime.now(timezone.utc)
        if template == "weekly_recap":
            start, end = now - timedelta(days=7), now + timedelta(hours=12)
        else:
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            start, end = monday, monday + timedelta(days=3)
        svc = {c["id"]: c["service"] for c in self.studio.channels(a)}
        posts = []
        for status in ("sent", "scheduled"):
            for p in self.studio.queue(a, status):
                posts.append({**p, "status": status, "service": svc.get(p.get("channelId"), "")})
        stories = templates.stories_from_posts(posts, start, end, 7 if template == "weekly_recap" else 3)
        return {"stories": stories, "from_posts": len(posts), "window": [start.isoformat(), end.isoformat()]}

    def render(self, a, body: dict) -> dict:
        """Render a template for each chosen channel (frame and slide count per channel) and store it as HuhQueue media.
        `data` gives the content; recaps without data are built from the week's posts. video: true adds an MP4 (where
        FFmpeg runs; the free sample shows the pre-rendered example Reels instead)."""
        tpl = body.get("template") or ""
        if tpl not in templates.TEMPLATES:
            raise StudioError(f"Unknown template. Pick one of {', '.join(templates.TEMPLATES)}.")
        self.studio._guard_write(a, "render_template")
        demo = demo_guard.is_demo(a.uid)
        if demo and not demo_guard.take(self.store, a.uid, "run"):
            raise StudioError(demo_guard.WHY["run"])
        kit = self.kit(a)
        services = [s for s in (body.get("services") or kit.get("channels") or ["instagram"]) if s in templates.CHANNELS][:6]
        data = body.get("data") if isinstance(body.get("data"), dict) else None
        src = "your data"
        if data is None and tpl in ("weekly_recap", "midweek_recap"):
            rd = self.recap_data(a, tpl)
            if rd["stories"]:
                data, src = {"stories": rd["stories"]}, f"{len(rd['stories'])} stories from this week's sent and scheduled posts"
            else:
                src = "sample stories (no posts in this window yet)"
        elif data is None:
            src = "sample content"
        b = self.brand_obj(a, kit)
        out = {"template": tpl, "content": src, "channels": [], "video": None}
        ratios: dict[str, list[str]] = {}
        for svc in services:
            spec = templates.CHANNELS[svc]
            if spec.get("video_only"):
                out["channels"].append({"service": svc, "label": spec["label"], "ratio": spec["video_ratio"], "slides": [],
                                        "note": "Video-only network: use the Reel."})
                continue
            if spec["ratio"] not in ratios:
                ims = templates.slides(tpl, data, b, spec["ratio"], max_slides=spec["max_slides"] or 10)
                ratios[spec["ratio"]] = [self.house.media_url(self.house.put_media(a, templates.jpeg(im), f"{tpl}-{i + 1}.jpg", kind="template",
                                                                                   meta={"type": "photo", "template": tpl}))
                                         for i, im in enumerate(ims)]
            urls = ratios[spec["ratio"]][: (spec["max_slides"] or 10)]
            if len(urls) < len(ratios[spec["ratio"]]):
                urls = urls[:-1] + [ratios[spec["ratio"]][-1]]  # always keep the end card last
            out["channels"].append({"service": svc, "label": spec["label"], "ratio": spec["ratio"], "slides": urls})
        want_video = bool(body.get("video")) and templates.TEMPLATES[tpl]["video"]
        if want_video:
            mode = "none" if os.environ.get("HQ_TEMPLATE_VIDEO", "1") == "0" else self.house.video_mode()
            if not demo and mode == "local":
                mp4 = templates.video(tpl, data, b, "9:16")
                tok = self.house.put_media(a, mp4, f"{tpl}.mp4", kind="template", meta={"type": "video", "template": tpl})
                out["video"] = {"url": self.house.media_url(tok), "bytes": len(mp4), "example": False, "rendered_by": "this server"}
            elif not demo and mode == "github":
                # the hosted app (512 MB) never runs FFmpeg: the person's own Reel renders on the GitHub worker. Never a canned clip.
                out["video"], out["video_job"] = self._template_video_on_worker(a, tpl, data, kit)
            else:
                out["video"] = self._example_reel(kit, tpl)
        self.store.audit(a.uid, a.label, "render_template", "read", {"template": tpl, "services": services, "video": want_video})
        return out

    @staticmethod
    def _example_reel(kit: dict, tpl: str) -> dict:
        """The committed example Reel, always labelled: it shows the template, it is not made from the person's content."""
        ex = kit.get("example") if kit.get("example") in brand_examples.EXAMPLES else "huhbyte"
        name = (brand_examples.EXAMPLES.get(ex) or {}).get("title") or ex
        return {"url": f"/static/examples/{ex}/reel-{tpl if tpl in ('weekly_recap', 'quote') else 'weekly_recap'}.mp4",
                "example": True, "label": "Example",
                "note": f"EXAMPLE REEL, not your content: a pre-rendered sample from the {name} example brand, to show the template. "
                        "Your own Reel renders on a video worker (FFmpeg); the sample workspace never starts one."}

    def _template_video_on_worker(self, a, tpl: str, data: dict | None, kit: dict) -> tuple[dict | None, dict]:
        """Queue the template's Reel on the GitHub render worker and wait a little for it. Returns (video, job): video is
        the person's own rendered Reel, or None while it renders (job says how to fetch it); never an example clip."""
        spec = {"kind": "template_video", "template": tpl, "data": data, "ratio": "9:16",
                "kit": {k: v for k, v in kit.items() if k != "_ledger"}, "logo": kit.get("logo") or "",
                "front": (kit.get("front_card") or {}).get("image") or "", "end": (kit.get("end_card") or {}).get("image") or ""}
        now = time.time()
        cap = int(os.environ.get("HQ_TEMPLATE_VIDEO_DAILY", "6"))  # each one is a GitHub Actions run (SECURITY_RULES 3, 4)
        recent = [r for r in self.store.q("SELECT spec FROM render_jobs WHERE user_id=? AND created>?", (a.uid, now - 86400))
                  if '"template_video"' in (r["spec"] or "")]
        if len(recent) >= cap:
            return None, {"status": "capped", "note": f"Template Reels are limited to {cap} a day; the slides above are ready."}
        jid = self.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                           (a.uid, None, json.dumps(spec), "queued", now, now))
        ok, why = self.house._dispatch_github(jid)
        if not ok:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, time.time(), jid))
            return None, {"id": jid, "status": "failed", "note": f"The video worker didn't start ({why}); the slides above are ready."}
        wait = float(os.environ.get("HQ_TEMPLATE_VIDEO_WAIT", "240"))
        t0 = time.time()
        while time.time() - t0 < wait:
            row = self.store.q("SELECT status, error FROM render_jobs WHERE id=?", (jid,))[0]
            if row["status"] == "done":
                return {**self.house._job_media(jid), "example": False, "rendered_by": "video worker"}, {"id": jid, "status": "done"}
            if row["status"] == "failed":
                return None, {"id": jid, "status": "failed", "note": f"The video render failed ({(row['error'] or '')[:120]})."}
            time.sleep(0.05 if self.house.sync else 3)
        return None, {"id": jid, "status": "queued", "note": "Your Reel is still rendering on the video worker; "
                      f"GET /api/templates/video/{jid} returns it when it's done."}

    def video_job(self, a, jid: int) -> dict:
        """A template Reel the worker rendered for this person (status, and the URL when done)."""
        rows = self.store.q("SELECT user_id, status, error, spec FROM render_jobs WHERE id=?", (jid,))
        if not rows or rows[0]["user_id"] != a.uid or json.loads(rows[0]["spec"]).get("kind") != "template_video":
            raise StudioError("No such template video.")
        r = rows[0]
        out = {"id": jid, "status": r["status"]}
        if r["status"] == "done":
            out["video"] = {**self.house._job_media(jid), "example": False, "rendered_by": "video worker"}
        elif r["status"] == "failed":
            out["error"] = (r["error"] or "")[:200]
        return out

    def render_cover(self, a, story: dict, ratio: str, kit: dict | None = None) -> str:
        """The Sources agent's per-channel cover: the breaking template in the brand, stored, returned as a public URL."""
        b = self.brand_obj(a, kit)
        img = None
        if story.get("image") and not demo_guard.is_demo(a.uid):
            img = self.house._fetch_bytes(story["image"], 4_000_000)  # netguard: every hop checked
        im = templates.breaking_card(b, ratio, story["headline"], story.get("source", ""), "Today", img)
        return self.house.media_url(self.house.put_media(a, templates.jpeg(im), "cover.jpg", kind="template",
                                                         meta={"type": "photo", "template": "breaking"}))

    # ================================================================ recurring slots
    def slots(self, a) -> list[dict]:
        out = []
        for r in self.store.q("SELECT * FROM template_slots WHERE user_id=? ORDER BY id", (a.uid,)):
            d = dict(r)
            d["channels"] = json.loads(d["channels"] or "[]")
            d["last_result"] = json.loads(d["last_result"] or "null")
            d["name"] = templates.TEMPLATES.get(d["template"], {}).get("name", d["template"])
            d["next_fire_iso"] = datetime.fromtimestamp(d["next_fire"], timezone.utc).isoformat() if d.get("next_fire") else None
            out.append(d)
        return out

    def add_slot(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "add_template_slot")
        tpl = body.get("template") or "weekly_recap"
        if tpl not in templates.TEMPLATES:
            raise StudioError("Unknown template.")
        dflt = templates.TEMPLATES[tpl].get("slot") or {"weekday": "mon", "time": "20:00"}
        wd = (body.get("weekday") or dflt["weekday"]).lower()[:3]
        at = body.get("time") or dflt["time"]
        if wd not in templates.DAYS or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            raise StudioError("weekday is mon..sun and time is HH:MM (24 h).")
        tz = (body.get("tz") or "Europe/Dublin")[:64]
        chans = {c["id"]: c for c in self.studio.channels(a) if not c.get("isDisconnected")}
        ids = [i for i in (body.get("channel_ids") or list(chans)) if i in chans][:8]
        if not ids:
            raise StudioError("Pick at least one connected channel.")
        if len(self.slots(a)) >= MAX_SLOTS:
            raise StudioError(f"Up to {MAX_SLOTS} recurring slots.")
        nf = templates.next_fire(wd, at, tz, datetime.now(timezone.utc)).timestamp()
        sid = self.store.x("INSERT INTO template_slots(user_id,template,channels,weekday,at,tz,active,next_fire,created) VALUES(?,?,?,?,?,?,1,?,?)",
                           (a.uid, tpl, json.dumps(ids), wd, at, tz, nf, time.time()))
        self.store.audit(a.uid, a.label, "add_template_slot", "read", {"slot": sid, "template": tpl, "weekday": wd, "time": at})
        return next(s for s in self.slots(a) if s["id"] == sid)

    def delete_slot(self, a, sid: int) -> dict:
        self.studio._guard_write(a, "delete_template_slot")
        self.store.x("DELETE FROM template_slots WHERE id=? AND user_id=?", (int(sid), a.uid))
        self.store.audit(a.uid, a.label, "delete_template_slot", "read", {"slot": sid})
        return {"deleted": int(sid)}

    def run_slot(self, a, sid: int, now: datetime | None = None) -> dict:
        """Render the slot's template now (recaps from the week's posts) and put one Buffer DRAFT per channel at the slot's
        next time. A person reviews it in Buffer (or in Approvals for an agent) before it is scheduled."""
        rows = self.store.q("SELECT * FROM template_slots WHERE id=? AND user_id=?", (int(sid), a.uid))
        if not rows:
            raise StudioError("No such slot.")
        s = dict(rows[0])
        now = now or datetime.now(timezone.utc)
        due = datetime.fromtimestamp(s["next_fire"], timezone.utc) if s.get("next_fire") and s["next_fire"] > now.timestamp() + 120 \
            else templates.next_fire(s["weekday"], s["at"], s["tz"], now)
        chans = {c["id"]: c for c in self.studio.channels(a)}
        ids = [i for i in json.loads(s["channels"] or "[]") if i in chans]
        services = sorted({chans[i]["service"] for i in ids if chans[i]["service"] in templates.CHANNELS})
        r = self.render(a, {"template": s["template"], "services": services})
        by_svc = {c["service"]: c for c in r["channels"]}
        kit = self.kit(a)
        results = []
        for cid in ids:
            svc = chans[cid]["service"]
            slides = (by_svc.get(svc) or {}).get("slides") or []
            text = self._slot_caption(s["template"], kit, svc)
            p = {"channel_id": cid, "text": text, "mode": "draft", "due_at": due.isoformat(), "override_warnings": True}
            if slides:
                p["media_urls"] = slides
            try:
                pv = self.studio.preview_post(a, p)
                res = self.studio.submit_post(a, pv["confirm_token"])
                results.append({"channel_id": cid, "service": svc, "status": res.get("status"), "post_id": (res.get("post") or {}).get("id")})
            except Exception as e:  # one channel failing never stops the rest
                results.append({"channel_id": cid, "service": svc, "status": "failed", "error": str(e)[:200]})
        nf = templates.next_fire(s["weekday"], s["at"], s["tz"], due + timedelta(minutes=1)).timestamp()
        self.store.x("UPDATE template_slots SET last_fire=?, next_fire=?, last_result=? WHERE id=?",
                     (time.time(), nf, json.dumps({"due": due.isoformat(), "results": results, "content": r["content"]}), s["id"]))
        self.store.audit(a.uid, a.label, "run_template_slot", "draft", {"slot": s["id"], "drafts": len(results)})
        return {"slot": s["id"], "due": due.isoformat(), "content": r["content"], "drafts": results}

    def _slot_caption(self, tpl: str, kit: dict, svc: str) -> str:
        tags = " ".join((kit.get("hashtags") or [])[: {"twitter": 1, "bluesky": 0, "linkedin": 3, "instagram": 5}.get(svc, 2)])
        name = templates.TEMPLATES[tpl]["name"]
        lead = {"weekly_recap": "The week in AI, in one swipe.", "midweek_recap": "Halfway through the week: the three things to know.",
                "launch": "New launch, explained.", "tutorial": "Free, and it takes five minutes.", "quote": "Worth hearing.",
                "breaking": "Breaking."}[tpl]
        return f"{name}\n\n{lead}" + (f"\n\n{tags}" if tags else "")

    def due_slots(self, now: float | None = None) -> list[tuple[str, int]]:
        now = now or time.time()
        # never the sample, and never an account whose kill switch is on: the ticker acts without a person (SECURITY_RULES 4)
        return [(r["user_id"], r["id"]) for r in self.store.q(
                    "SELECT s.user_id, s.id FROM template_slots s JOIN users u ON u.id = s.user_id "
                    "WHERE s.active=1 AND s.next_fire<=? AND COALESCE(u.kill_switch, 0) = 0", (now,))
                if not demo_guard.is_demo(r["user_id"])]


def start_ticker(bs: BrandStudio, every: int = 600) -> threading.Thread | None:
    """Fire due slots for real accounts (never the sample) every `every` seconds. Opt-in: HQ_SLOT_TICK=1."""
    if os.environ.get("HQ_SLOT_TICK") != "1":
        return None
    from .actions import Actor

    def loop():
        while True:
            time.sleep(every)
            for uid, sid in bs.due_slots():
                try:
                    bs.run_slot(Actor(uid), sid)
                except Exception as e:  # keep ticking
                    print(f"slot {sid}: {e.__class__.__name__}: {e}", flush=True)
    t = threading.Thread(target=loop, daemon=True, name="template-slots")
    t.start()
    return t
