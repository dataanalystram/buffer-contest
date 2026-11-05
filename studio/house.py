"""HuhQueue: the agent, the brand kit, the Style Ledger, media and the calendar, on top of the Studio actions.

One agent, eight visible steps: find -> verify -> structure -> format -> render -> caption -> brand check -> ready.
Every step is stored with what it did, so the web app shows a timeline and an MCP client gets the same record. A
correction at any point becomes a ledger rule, and the run re-renders, re-captions and re-checks against it.
Scheduling still goes through Studio.preview_post/submit_post, so agents keep the approval policy.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import brand, demo, demo_guard, demo_view, drafter, llm, netguard, render, sources, untrusted
from .actions import ActionError, Actor, Studio

STEPS = [("find", "Find the story"), ("verify", "Verify on the source"), ("structure", "Structure the facts"),
         ("format", "Pick the format"), ("render", "Render on-brand media"), ("caption", "Write captions"),
         ("check", "Brand check"), ("ready", "Ready to schedule")]
MEDIA_STEPS = [("upload", "Read your media"), ("format", "Pick the format"), ("render", "Render on-brand media"),
               ("caption", "Write captions"), ("check", "Brand check"), ("ready", "Ready to schedule")]
MAX_UPLOAD = 40 * 1024 * 1024
MAX_IMAGE_FETCH = 8 * 1024 * 1024
VIDEO_ONLY = {"tiktok", "youtube"}

STALE_RUN = int(os.environ.get("HQ_RUN_STALE", "900"))  # a run silent this long (seconds) is reported as stopped
# a worker job (InstaBox or GitHub Actions) with no heartbeat for this many minutes is failed (House.sweep_stale_jobs)
JOB_STALE_MIN = float(os.environ.get("HQ_JOB_HEARTBEAT_MIN", "10"))
JOB_STALE_WHY = "The video worker stopped responding (no heartbeat for {n} minutes), so this job was stopped. Try again."
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="house-agent")


def _req(inp: dict | None) -> str:
    """The person's whole request on a run (agent runs keep it as prompt; pre-agent runs as query/url/brief)."""
    inp = inp or {}
    return " ".join(str(inp.get(k) or "") for k in ("prompt", "query", "url", "brief") if inp.get(k)).strip()


def _now() -> float:
    return time.time()



def _run_thumb(res: dict, inp: dict) -> str:
    """A still for a run's Recent / Library card (owner, 1 Nov 2026: video-only runs showed a frame with burned captions or
    a blank intro, and a One take showed nothing): any photo the run made (a package, a channel's slides, a clip cover),
    then the upload's own keyframe. "" when the run only has video; thumb_src then names a page to read a picture from."""
    for p in res.get("packages") or []:
        for m in p.get("media") or []:
            if m.get("type") == "photo" and m.get("url"):
                return m["url"]
    for v in (res.get("channel_media") or {}).values():
        for m in (v or {}).get("media") or []:
            if m.get("type") == "photo" and m.get("url"):
                return m["url"]
    for c in res.get("clips") or []:
        if c.get("cover_url"):
            return c["cover_url"]
    up = res.get("upload") or {}
    if up.get("type") == "photo" and up.get("url"):
        return up["url"]
    return ""


def _run_thumb_src(res: dict, inp: dict) -> dict | None:
    """The story behind a video-only run, for GET /api/thumb (its og:image, read server-side through netguard and
    re-encoded): {url, img}. None for a person's own upload (their video frame is the right picture)."""
    src = res.get("source") or {}
    if src.get("via") == "upload":
        return None
    url = src.get("url") or inp.get("platform_url") or inp.get("url") or inp.get("source_url") or ""
    poster = next((m.get("poster") for m in src.get("media") or [] if m.get("poster")), "")
    img = src.get("image") or poster or ""
    if not (isinstance(url, str) and url.startswith("https://")):
        return None
    return {"url": url, "img": img if isinstance(img, str) and img.startswith("https://") else ""}

_FETCH_WHY = threading.local()  # why the last House._fetch_bytes on this thread returned None
IMPORT_WHY = {  # media import failures, said plainly (owner bug 2 Nov 2026: every failure read the same)
    "bad_url": "Couldn't download that file: give a full https:// link to the photo or video.",
    "not_found": "Couldn't download that file: the link answered 404 (not found). Files in a private repo or drive need "
                 "a public link, or upload the file instead.",
    "forbidden": "Couldn't download that file: the link needs a sign-in (HTTP 401/403). Use a public link, or upload the file instead.",
    "too_large": "Couldn't download that file: it is over 40 MB (or empty). Upload a shorter clip instead.",
    "private": "Couldn't download that file: the link points to a private address, so it can't be read.",
    "unreachable": "Couldn't download that file: the link didn't answer. Check it opens in a browser, or upload the file instead.",
}



def start_media_check(house: "House", every: float | None = None, first_delay: float = 20) -> threading.Thread | None:
    """Check for missing media files shortly after startup, then every ``every`` seconds (HQ_MEDIA_CHECK_S, default 6 h;
    0 turns it off). Only reads; the result is house.media_health."""
    every = float(os.environ.get("HQ_MEDIA_CHECK_S", "21600")) if every is None else every
    if every <= 0:
        return None

    def loop():
        time.sleep(first_delay)
        while True:
            try:
                house.media_audit()
            except Exception as e:  # noqa: BLE001 - keep checking
                print(f"media check: {e.__class__.__name__}: {e}", flush=True)
            try:  # Library items deleted more than TRASH_DAYS ago are removed for good (library.Library.purge_trash)
                n = house.library.purge_trash()
                if n:
                    print(f"library trash: removed {n} item(s) for good", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"library trash: {e.__class__.__name__}: {e}", flush=True)
            try:  # One take's Bin (takes and clips) after TRASH_DAYS, and bucket uploads nobody finished or used
                n = house.threads.purge_expired() + house.takes.purge_expired() + house.take_uploads.purge_stale()
                if n:
                    print(f"take bin: removed {n} item(s) for good", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"take bin: {e.__class__.__name__}: {e}", flush=True)
            time.sleep(every)
    t = threading.Thread(target=loop, daemon=True, name="media-check")
    t.start()
    return t


_HTTP_WHY = {400: "Bad Request", 401: "needs a sign-in", 403: "Forbidden (private or needs a sign-in)", 404: "Not Found",
             410: "Gone", 429: "Too Many Requests", 500: "a server error", 502: "a server error", 503: "unavailable"}


def plain_worker_error(msg: str) -> str:
    """The worker reports raw Python errors ("HTTPStatusError: Client error '404 Not Found' for url ..."). Say what happened
    and what to do instead (live test 2 Nov 2026: a bad One take link showed the raw httpx message)."""
    import re
    m = (msg or "").strip()
    if not m:
        return ""
    h = re.match(r"HTTPStatusError: (?:Client|Server) error '(\d{3})[^']*' for url '([^']+)'", m)
    if h:
        code, url = int(h.group(1)), h.group(2)
        host = urlparse(url).hostname or "the link"
        return (f"Couldn't download the video: {host} answered {code} {_HTTP_WHY.get(code, '')}".rstrip() + ". "
                "Use a direct link to the video file (it usually ends in .mp4, .mov or .webm), or upload the file instead.")
    if m.startswith(("ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException", "RemoteProtocolError")):
        return "Couldn't reach the video link from the video worker. Check the link opens in a private window, or upload the file."
    return m


class House:
    def __init__(self, studio: Studio, http: httpx.Client | None = None):
        self.studio, self.store, self.s = studio, studio.store, studio.s
        self.http = http
        self.sync = bool(self.s.extra.get("sync_agent"))
        self.worker_mode = (os.environ.get("RENDER_WORKER") or self.s.extra.get("render_worker") or "").strip()
        from .takes import Takes
        self.takes = Takes(self)
        from .agent_brain import Brain
        self.brain = Brain(self)
        from .library import Library
        self.library = Library(self)
        from .uploads import Uploads
        self.uploads = Uploads(self)
        from .take_upload import TakeUploads
        self.take_uploads = TakeUploads(self)  # One take's long videos: multipart straight to the bucket (R2), off unless configured  # create_upload / upload_media_chunk (any MCP client, no public host)
        from .thread_bin import ThreadBin
        self.threads = ThreadBin(self)  # thread ids, list/get threads, delete/restore any thread, never its media (studio/thread_bin.py)
        from .ledger import Ledger
        self.ledger = Ledger(self)  # Style Ledger v2: groups, scope, learned suggestions, evidence, review links
        from .send import Send
        self.send = Send(self)  # Send a file page: a short code + QR, any device drops a file into the Library
        from .share_import import Importer
        self.share_import = Importer(self)  # import_from_link: Drive / Dropbox / OneDrive / iCloud share links

    # ================================================================ brand kit
    def kit(self, a: Actor) -> dict:
        rows = self.store.q("SELECT kit FROM brand_kits WHERE user_id=?", (a.uid,))
        k = json.loads(rows[0]["kit"]) if rows else {}
        out = {**brand.DEFAULT_KIT, **k}
        out["colors"] = {**brand.DEFAULT_KIT["colors"], **(k.get("colors") or {})}
        out["end_card"] = {**brand.DEFAULT_KIT["end_card"], **(k.get("end_card") or {})}
        if not (out.get("name") or "").strip() or out["name"] == "My brand":  # unset: use the workspace's name, else none
            org = ((self.store.user(a.uid) or {}).get("org_name") or "").strip()
            org = re.sub(r"\s*\((sample|demo)\)$", "", org, flags=re.I)
            out["name"] = "" if org.lower() in ("", "my organization", "my organisation", "personal", "default") else org[:60]
        out["logo_url"] = self.media_url(out["logo"]) if out.get("logo") else ""
        out["is_set"] = bool(rows)
        return out

    def save_kit(self, a: Actor, patch: dict) -> dict:
        self.studio._guard_write(a, "save_brand_kit")  # SECURITY_RULES 4: kill switch + daily cap for agents
        from .userlocks import held
        with held(a.uid):  # B2: read-modify-write of the kit row
            return self._save_kit_locked(a, patch)

    def _save_kit_locked(self, a: Actor, patch: dict) -> dict:
        cur = self.kit(a)
        allowed = {"name", "handle", "website", "font", "logo", "voice"}
        for k in allowed & set(patch):
            cur[k] = str(patch[k] or "")[:300]
        if isinstance(patch.get("colors"), dict):
            for k in ("primary", "accent", "text"):
                v = str(patch["colors"].get(k) or "")
                if brand.HEX_RE.fullmatch(v.strip()):
                    cur["colors"][k] = "#" + v.strip().lstrip("#").upper()
        if isinstance(patch.get("end_card"), dict):
            for k in ("on", "line", "cta"):
                if k in patch["end_card"]:
                    cur["end_card"][k] = patch["end_card"][k] if k == "on" else str(patch["end_card"][k] or "")[:80]
        if cur["font"] not in brand.FONTS:
            cur["font"] = "inter"
        cur["handle"] = cur["handle"].lstrip("@")
        cur["updated"] = _now()
        data = {k: cur[k] for k in brand.DEFAULT_KIT}
        self.store.x("INSERT INTO brand_kits(user_id,kit,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET kit=excluded.kit,"
                     " updated=excluded.updated", (a.uid, json.dumps(data), _now()))
        self.store.audit(a.uid, a.label, "save_brand_kit", "read", {"fields": sorted(set(patch) & (allowed | {"colors", "end_card"}))})
        return self.kit(a)

    def kit_from_website(self, a: Actor, url: str) -> dict:
        self.studio._guard_write(a, "brand_from_website")
        if demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "import"):
            kit = self.save_kit(a, dict(demo.SAMPLE_KIT))
            return {"kit": kit, "found": {"logo": False, "palette": [], "theme_color": "", "handle": demo.SAMPLE_KIT["handle"]},
                    "sample": True, "note": demo_guard.why("import")}
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        p = urlparse(url)
        if not p.hostname or not drafter._public(p.hostname):
            raise ActionError("Paste your brand's public website address.")
        try:  # every redirect hop is checked again (SECURITY_RULES 1)
            r = netguard.safe_get(url, http=self.http, headers=sources.UA, limit=3_000_000)
        except netguard.FetchError as e:
            raise ActionError(str(e))
        except httpx.HTTPError as e:
            raise ActionError(f"Couldn't open {p.hostname} ({e.__class__.__name__}).")
        info = brand.kit_from_html(str(r.url), r.text)
        logo_tok, palette = "", []
        for cand in info["logo_candidates"]:
            data = self._fetch_bytes(cand, limit=2_000_000)
            if not data:
                continue
            try:
                palette = brand.palette_from_image(data)
                logo_tok = self.put_media(a, data, "logo" + (".png" if not cand.lower().endswith((".jpg", ".jpeg")) else ".jpg"),
                                          kind="logo")
                break
            except Exception:
                continue
        colors = brand.pick_colors(info["theme_color"], palette)
        patch = {"name": info["name"], "website": info["website"], "colors": colors}
        if info["handle"]:
            patch["handle"] = info["handle"]
        if logo_tok:
            patch["logo"] = logo_tok
        if info["description"] and not self.kit(a).get("voice"):
            patch["voice"] = info["description"][:200]
        kit = self.save_kit(a, patch)
        return {"kit": kit, "found": {"logo": bool(logo_tok), "palette": palette, "theme_color": info["theme_color"],
                                      "handle": info["handle"]}}

    def preview_kit(self, a: Actor) -> dict:
        """A sample cover and end card with the current kit and ledger, so brand changes are visible at once."""
        kit, rules = self.kit(a), self.rules(a)
        p = brand.visual_params(kit, rules)
        logo = self._logo_bytes(kit)
        b1, m1 = render.news_card("Your next post, rendered in your brand style", kit, p, logo=logo, ratio="4:5", label="Preview",
                                  credit="example.com")
        b2, _ = render.end_card(kit, p, logo=logo, ratio="4:5")
        from .library import record_owner_deletes
        with self.store.tx():  # the old previews go for good; noted as the owner's own delete in the same transaction
            old = [r["token"] for r in self.store.q("SELECT token FROM media WHERE user_id=? AND meta LIKE ?", (a.uid, '%"preview"%'))]
            self.store.x("DELETE FROM media WHERE user_id=? AND meta LIKE ?", (a.uid, '%"preview"%'))
            record_owner_deletes(self.store, [(t, a.uid) for t in old])
        t1 = self.put_media(a, b1, "preview.jpg", kind="preview", meta=m1)
        t2 = self.put_media(a, b2, "preview_end.jpg", kind="preview", meta={"type": "photo"})
        return {"cover": self.media_url(t1), "end": self.media_url(t2), "check": brand.brand_check("", m1, rules, kit)}

    # ================================================================ ledger
    def rules(self, a: Actor, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM style_rules WHERE user_id=?" + ("" if include_inactive else " AND active=1") + " ORDER BY id"
        out = []
        for r in self.store.q(sql, (a.uid,)):
            d = dict(r)
            d["check"] = json.loads(d.pop("spec"))
            d["active"] = bool(d["active"])
            for k in ("scope", "examples", "evidence"):  # Style Ledger v2 columns (JSON lists; NULL on older rows)
                try:
                    d[k] = json.loads(d.get(k) or "[]")
                except ValueError:
                    d[k] = []
            d["origin"] = d.get("origin") or "taught"
            out.append(d)
        return out

    def remember(self, a: Actor, correction: str, rules: list[dict] | None = None, scope=None, origin: str | None = None) -> dict:
        """A correction in plain words -> ledger rules. Rule writer first; an AI model only when it matched nothing."""
        self.studio._guard_write(a, "remember_rule")
        correction = (correction or "").strip()[:500]
        if not correction and not rules:
            raise ActionError("Say what to change, e.g. “logo smaller, top left” or “never say game-changer”.")
        found = brand.clean_rules(rules, correction) if rules else brand.parse_correction(correction)
        engine_used = "rule writer"
        if not found:
            eng = self._ai_reserve(a, llm.resolve(self.store, a.uid, self.s))
            if eng:
                try:
                    data = llm.chat_json(eng, "You turn a social media editor's correction into brand rules. " + brand.RULE_SCHEMA_HINT,
                                         correction, http=self.http, max_tokens=400)
                    self._ai_audit(a, eng, "rules")
                    found = brand.clean_rules(data.get("rules") if isinstance(data, dict) else data, correction)
                    engine_used = eng["source"]
                except llm.LLMError:
                    found = []
        if not found:
            return {"added": [], "message": "I couldn't turn that into a rule. Try “logo top left”, “max 2 hashtags”, "
                                            "“never say X”, “no end card”, “use #FF5A1F as the accent”.", "ledger": self.rules(a)}
        added = []
        existing = self.rules(a)
        from .ledger import norm_scope
        sc = norm_scope(scope)
        for r in found:
            # a newer rule of the same single-valued type replaces the old one (for the same channels)
            if r["check"]["type"] not in ("banned_words", "require_text", "prefer_words"):
                for old in existing:
                    if old["check"]["type"] == r["check"]["type"] and (old.get("scope") or []) == (sc if r["kind"] == "text" else []):
                        self.store.x("UPDATE style_rules SET active=0 WHERE id=?", (old["id"],))
            rid = self.store.x("INSERT INTO style_rules(user_id,kind,spec,rule,source,active,created) VALUES(?,?,?,?,?,1,?)",
                               (a.uid, r["kind"], json.dumps(r["check"]), r["rule"], r["source"], _now()))
            added.append({**r, "id": rid})
        self.ledger.stamp(a, added, scope, origin)
        self.store.audit(a.uid, a.label, "remember_rule", "read", {"rules": [r["rule"] for r in added], "engine": engine_used})
        return {"added": added, "ledger": self.rules(a), "engine": engine_used}

    def set_rule(self, a: Actor, rule_id: int, active: bool) -> dict:
        if a.kind != "human":  # SECURITY_RULES 4: switching rules on or off is a human decision
            self.studio.kill_switch_check(a, "set_rule")
            raise ActionError("Only a human can switch ledger rules on or off; agents can add them.", 403)
        self.store.x("UPDATE style_rules SET active=? WHERE id=? AND user_id=?", (1 if active else 0, rule_id, a.uid))
        return {"id": rule_id, "active": active}

    def delete_rule(self, a: Actor, rule_id: int) -> dict:
        if a.kind != "human":
            raise ActionError("Only a human can delete ledger rules; agents can add them.", 403)
        self.store.x("DELETE FROM style_rules WHERE id=? AND user_id=?", (rule_id, a.uid))
        return {"deleted": rule_id}

    def check(self, a: Actor, text: str, media_token: str = "", service: str = "") -> dict:
        meta = self.media_meta(a, media_token) if media_token else None
        res = brand.brand_check(text, meta, brand.for_channel(self.rules(a), service) if service else self.rules(a), self.kit(a))
        if res["failed"]:
            for f in res["failed"]:
                if f["rule_id"]:
                    self.store.x("UPDATE style_rules SET hits=hits+1 WHERE id=?", (f["rule_id"],))
        return res

    # ================================================================ media
    def put_media(self, a: Actor, data: bytes, name: str, kind: str = "upload", meta: dict | None = None) -> str:
        ext = (name.rsplit(".", 1)[-1] if "." in name else "bin").lower()[:5]
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
                "mp4": "video/mp4", "mov": "video/quicktime", "m4v": "video/mp4", "webm": "video/webm"}.get(ext, "application/octet-stream")
        tok = secrets.token_urlsafe(18) + "." + ext
        self.store.x("INSERT INTO media(token,user_id,name,mime,data,size,meta,created) VALUES(?,?,?,?,?,?,?,?)",
                     (tok, a.uid, name[:80], mime, data, len(data), json.dumps({"kind": kind, **(meta or {})}), _now()))
        return tok

    def get_media(self, token: str) -> dict | None:
        from .library import TRASH_TOKEN
        if not token or token.startswith(TRASH_TOKEN):  # /m/trash~<token> must not serve a binned file (owner-only preview)
            return None
        rows = self.store.q("SELECT token,user_id,mime,data,size,meta FROM media WHERE token=?", (token,))
        if not rows:
            return self.library.blob(token)  # an uploaded Library file works as a media token everywhere
        d = dict(rows[0])
        d["data"] = bytes(d["data"])
        return d

    def media_head(self, token: str) -> dict | None:
        """Type and length of a media token without reading its bytes (HEAD /m/<token>)."""
        from .library import TRASH_TOKEN
        if not token or token.startswith(TRASH_TOKEN):
            return None
        rows = self.store.q("SELECT mime, size FROM media WHERE token=?", (token,))
        if rows and rows[0]["size"]:
            return {"mime": rows[0]["mime"], "size": int(rows[0]["size"])}
        m = self.get_media(token)
        return {"mime": m["mime"], "size": len(m["data"])} if m else None

    def media_meta(self, a: Actor, token: str) -> dict | None:
        rows = self.store.q("SELECT meta FROM media WHERE token=? AND user_id=?", (token, a.uid))
        return json.loads(rows[0]["meta"]) if rows else self.library.meta(a, token)

    def media_url(self, token: str) -> str:
        return f"{self.s.public_url.rstrip('/')}/m/{token}" if token else ""

    def upload(self, a: Actor, name: str, data_b64: str, source: str = "upload") -> dict:
        self.studio._guard_write(a, "upload_media")
        b64 = data_b64.split(",", 1)[-1]
        demo_user = demo_guard.is_demo(a.uid)
        cap = demo_guard.DEMO_FILE_MAX if demo_user else MAX_UPLOAD
        if len(b64) * 3 // 4 > cap + 3:  # the size, from the base64 length, before anything is decoded (SECURITY_RULES 6)
            raise ActionError(demo_guard.why("upload_kb") if demo_user else "Files up to 40 MB.", 413)
        if demo_user and not demo_guard.reserve(self.store, a.uid, "upload_kb", max(1, len(b64) * 3 // 4 // 1024)):
            raise ActionError(demo_guard.why("upload_kb"), 429)
        try:
            data = base64.b64decode(b64, validate=False)
        except Exception:
            raise ActionError("Couldn't read that file.")
        if not data or len(data) > cap:
            raise ActionError("Files up to 40 MB.")
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
        if ext not in ("jpg", "jpeg", "png", "webp", "mp4", "mov", "m4v", "webm"):
            raise ActionError("Upload a JPG, PNG, WebP photo or an MP4/MOV video.")
        kind = "video" if ext in ("mp4", "mov", "m4v", "webm") else "photo"
        if not demo_user:  # a real account's uploads are kept in its Library (studio/library.py), type sniffed from the bytes
            item = self.library.save_bytes(a, data, name, "take" if source == "take" else "upload")
            kind = "video" if item["kind"] == "video" else "photo"
            self.store.audit(a.uid, a.label, "upload_media", "read", {"type": kind, "bytes": len(data)})
            return {"token": item["media_token"], "url": self.media_url(item["media_token"]), "type": kind, "bytes": len(data),
                    "library_id": item["id"], **({"deduped": True} if item.get("deduped") else {})}
        tok = self.put_media(a, data, name, kind="upload", meta={"type": kind})
        self.store.audit(a.uid, a.label, "upload_media", "read", {"type": kind, "bytes": len(data)})
        return {"token": tok, "url": self.media_url(tok), "type": kind, "bytes": len(data)}

    def import_url(self, a: Actor, url: str) -> dict:
        """For agents: fetch a photo or video the user owns from a public URL into HuhQueue."""
        self.studio._guard_write(a, "import_media")
        ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
        _FETCH_WHY.v = "unreachable"
        data = self._fetch_bytes(url, MAX_UPLOAD)
        why = _FETCH_WHY.v or "unreachable"
        if not data:
            raise ActionError(IMPORT_WHY.get(why, IMPORT_WHY["unreachable"]) if not why.startswith("http_")
                              else f"Couldn't download that file: the link answered HTTP {why[5:]}.")
        from .library import not_media, sniff
        if not sniff(data[:32]):  # say what actually came back (owner bug 2 Nov 2026: a sign-in page read "Upload a JPG, PNG…")
            word = "video" if ext in ("mp4", "mov", "m4v", "webm") else "photo or video"
            why = not_media(data[:64])
            if why.startswith("That file is a web page"):
                raise ActionError(f"That link returned a web page (probably a sign-in page), not a {word}. "
                                  "Use create_upload instead (it uploads the file itself), or create_send_code if your "
                                  "sandbox can't reach it; a Google Drive, Dropbox, OneDrive or iCloud share link works "
                                  "with import_from_link.", 415)
            if why.startswith("That file is JSON"):
                raise ActionError(f"That link returned JSON text (probably an error), not a {word}. Use create_upload instead.", 415)
            raise ActionError(f"That link didn't return a JPG, PNG, WebP or GIF image or an MP4, MOV or WebM video. "
                              "Use create_upload to upload the file itself.", 415)
        if ext not in ("jpg", "jpeg", "png", "webp", "mp4", "mov", "m4v", "webm"):
            ext = "mp4" if data[4:8] == b"ftyp" else ("png" if data[:4] == b"\x89PNG" else "jpg")
        return self.upload(a, "import." + ext, base64.b64encode(data).decode())

    # ================================================================ missing media
    # Owner bug, 2 Nov 2026: run 20's ready Instagram carousel showed a broken thumbnail (/m/<token>.jpg answered 404)
    # because its slides had been deleted from the Library (a forced delete). A package now says which of its files are
    # gone, the Planner thumbnail falls back to the first slide that still exists, a package with missing files can't be
    # sent to Buffer (it would only fail there), and a check at startup and every few hours counts what is missing.
    def media_token_of(self, url: str) -> str:
        """The media token of one of this server's own /m/<token> URLs, else ''."""
        u = str(url or "")
        base = self.s.public_url.rstrip("/") + "/m/"
        if base != "/m/" and u.startswith(base):
            return u[len(base):].split("?", 1)[0].split("#", 1)[0]
        return u[3:].split("?", 1)[0] if u.startswith("/m/") else ""

    def media_removed(self, tokens) -> tuple[set[str], set[str]]:
        """Of these (absent) tokens: (the ones in the person's Library Bin, the ones they deleted for good)."""
        from .library import TRASH_TOKEN
        toks = sorted({t for t in tokens if t})
        binned: set[str] = set()
        deleted: set[str] = set()
        for i in range(0, len(toks), 200):
            part = toks[i:i + 200]
            marks = ",".join("?" for _ in part)
            tr = tuple(TRASH_TOKEN + t for t in part)
            binned |= {r["token"].removeprefix(TRASH_TOKEN) for r in self.store.q(f"SELECT token FROM media WHERE token IN ({marks})", tr)}
            binned |= {r["token"].removeprefix(TRASH_TOKEN) for r in self.store.q(
                f"SELECT token FROM library_items WHERE storage='trash' AND token IN ({marks})", tr)}
            deleted |= {r["token"] for r in self.store.q(f"SELECT token FROM library_purged WHERE token IN ({marks})", tuple(part))}
        return binned, deleted - binned

    def media_present(self, tokens) -> set[str]:
        """Which of these tokens still have a file (a media row or a chunked Library upload)."""
        toks = sorted({t for t in tokens if t})
        have: set[str] = set()
        for i in range(0, len(toks), 200):
            part = toks[i:i + 200]
            marks = ",".join("?" for _ in part)
            have |= {r["token"] for r in self.store.q(f"SELECT token FROM media WHERE token IN ({marks})", tuple(part))}
            have |= {r["token"] for r in self.store.q(f"SELECT token FROM library_items WHERE storage='chunks' AND token IN ({marks})",
                                                       tuple(part))}
        return have

    @staticmethod
    def _pkg_urls(pk: dict) -> list[str]:
        urls = [str((m or {}).get("url") or "") for m in (pk.get("media") or []) if isinstance(m, dict)]
        urls += [str(u) for u in (pk.get("media_urls") or []) if u]
        return list(dict.fromkeys(u for u in urls if u))

    def media_state(self, packages: list, have: set[str] | None = None) -> dict[int, dict]:
        """For each package (by index): {state: ok | partial | missing, missing: [urls], thumb: the first file that still
        exists}. Only this server's own /m/ files are checked; outside URLs count as present."""
        toks = {self.media_token_of(u) for pk in packages if isinstance(pk, dict) for u in self._pkg_urls(pk)}
        toks.discard("")
        have = self.media_present(toks) if have is None else have
        out = {}
        for i, pk in enumerate(packages):
            if not isinstance(pk, dict):
                continue
            urls = self._pkg_urls(pk)
            gone = [u for u in urls if self.media_token_of(u) and self.media_token_of(u) not in have]
            alive = [u for u in urls if u not in gone]
            out[i] = {"state": "ok" if not gone else ("missing" if not alive else "partial"), "missing": gone,
                      "thumb": alive[0] if alive else ""}
        return out

    def annotate_media(self, res: dict) -> dict:
        """Mark each package of a run result with media_state / media_missing (never saved: always read fresh)."""
        pks = (res or {}).get("packages") or []
        if not pks:
            return res
        try:
            st = self.media_state(pks)
        except Exception:  # noqa: BLE001 - a check that can't run never hides the run
            return res
        for i, info in st.items():
            pks[i]["media_state"] = info["state"]
            pks[i]["media_missing"] = info["missing"]
        return res

    MISSING_MEDIA_NOTE = ("This post's media was deleted from your Library, so it can't be sent. "
                          "Make the post again (or upload the files) to get new media.")

    def media_audit(self, limit: int = 300) -> dict:
        """Every recent ready run's package files, checked against the store. Kept in self.media_health for /healthz
        and GET /api/media/health; nothing is changed or deleted."""
        rows = self.store.q("SELECT id,user_id,result FROM agent_runs WHERE status='ready' ORDER BY id DESC LIMIT ?", (limit,))
        runs, toks = [], set()
        for r in rows:
            try:
                pks = (json.loads(r["result"] or "{}") or {}).get("packages") or []
            except ValueError:
                continue
            runs.append((r["id"], r["user_id"], pks))
            toks |= {self.media_token_of(u) for pk in pks if isinstance(pk, dict) for u in self._pkg_urls(pk)}
        toks.discard("")
        have = self.media_present(toks)
        binned, deleted = self.media_removed(toks - have)
        bad = []
        n_bin = n_del = 0
        for rid, uid, pks in runs:
            st = self.media_state(pks, have)
            gone_t = [self.media_token_of(u) for v in st.values() for u in v["missing"]]
            b_, d_ = sum(t in binned for t in gone_t), sum(t in deleted for t in gone_t)
            n_bin, n_del = n_bin + b_, n_del + d_
            if gone_t:
                row = {"run_id": rid, "user_id": uid, "missing": len(gone_t) - b_ - d_,
                       "state": "missing" if any(v["state"] == "missing" for v in st.values()) else "partial"}
                row.update({k: v for k, v in (("in_bin", b_), ("deleted", d_)) if v})
                bad.append(row)
        gone = {self.media_token_of(u) for _, _, pks in runs for st in [self.media_state(pks, have)] for v in st.values() for u in v["missing"]}
        gone -= {""}
        # files the person moved to the Library Bin or deleted themselves are expected, not missing (owner, 3 Nov 2026)
        self.media_health = {"checked_at": _now(), "runs_checked": len(runs), "files_checked": len(toks),
                             "files_missing": sum(b["missing"] for b in bad), "unique_files_missing": len(gone - binned - deleted),
                             "files_in_bin": n_bin, "files_deleted_by_owner": n_del, "runs": bad}
        unexplained = [b for b in bad if b["missing"]]
        if unexplained:
            print(f"media check: {len(unexplained)} ready run(s) reference missing files: "
                  + ", ".join(f"#{b['run_id']} ({b['missing']})" for b in unexplained[:10]), flush=True)
        return self.media_health

    def media_health_for(self, a: Actor) -> dict:
        h = getattr(self, "media_health", None) or self.media_audit()
        return {"checked_at": h["checked_at"], "runs": [{k: v for k, v in b.items() if k != "user_id"}
                                                        for b in h["runs"] if b["user_id"] == a.uid]}

    # ================================================================ AI settings
    def ai_settings(self, a: Actor) -> dict:
        return llm.public_view(self.store, a.uid, self.s)

    def save_ai(self, a: Actor, body: dict) -> dict:
        """The older one-key form (POST /api/ai): saves into the per-provider keys (byo_keys), or removes them all."""
        from . import byo_keys
        if a.kind != "human":
            raise ActionError("Only a human can change AI settings.", 403)
        if body.get("remove"):
            self.store.x("DELETE FROM ai_settings WHERE user_id=?", (a.uid,))
            self.store.audit(a.uid, a.label, "delete_ai_key", "safety", {"provider": "all"})
            return self.ai_settings(a)
        prov = body.get("provider") or "custom"
        if prov not in llm.PRESETS:
            raise ActionError("Unknown provider.")
        if not (body.get("api_key") or "").strip() or (prov == "custom" and not (body.get("base_url") or "").startswith("https://")):
            raise ActionError("Add an API key (and an https:// base URL for a custom endpoint).")
        try:
            byo_keys.save(self.store, a.uid, prov, body.get("api_key") or "", body.get("model") or "", body.get("base_url") or "",
                          http=self.http)
        except byo_keys.KeyError_ as e:
            raise ActionError(str(e), e.status)
        self.store.audit(a.uid, a.label, "save_ai_key", "safety", {"provider": prov, "model": body.get("model") or llm.PRESETS[prov]["model"]})
        return self.ai_settings(a)

    def _ai_reserve(self, a: Actor, eng: dict | None) -> dict | None:
        """The engine for ONE AI call, or None (built-in writer). The free sample's AI allowance is reserved here, before
        the call, in one atomic step, so parallel calls can't run past the cap (SECURITY_RULES 3)."""
        if eng and eng["source"] == "free starter" and not demo_guard.reserve(self.store, a.uid, "ai"):
            return None
        return eng

    def _ai_audit(self, a: Actor, eng: dict, what: str) -> None:
        self.store.audit(a.uid, a.label, "ai_call", "read", {"engine": "free" if eng["source"] == "free starter" else "own", "for": what,
                                                             "provider": eng.get("provider"), "model": eng.get("model")})

    # ================================================================ agent runs
    def start_run(self, a: Actor, body: dict) -> dict:
        """Every Make a post run goes through the agent brain (plan -> tools -> check, with a reply thread): a prompt,
        a prompt with a link, or a prompt with the person's own photo/video (an input tool, read_upload). The older
        fixed pipeline below only finishes runs that were started before this change (owner bug, 1 Nov 2026: uploads
        still ran it and burned the prompt into the video)."""
        body = dict(body or {})
        if not body.get("prompt"):
            body["prompt"] = " ".join(str(body.get(k) or "").strip() for k in ("query", "brief") if body.get(k)).strip()
        if body.get("brief") and body["brief"] not in body["prompt"]:
            body["prompt"] = (body["prompt"] + " " + str(body["brief"])).strip()
        return self.brain.start(a, body)

    def _legacy_start_run(self, a: Actor, body: dict) -> dict:
        self.studio._guard_write(a, "agent_run")
        if demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "run"):
            raise ActionError(demo_guard.why("run"), 429)
        kind = "media" if body.get("media_token") else "news"
        if kind == "news" and not ((body.get("query") or "").strip() or (body.get("url") or "").strip()):
            raise ActionError("Give the agent a topic to research or a link to start from.")
        if kind == "media" and not self.media_meta(a, body["media_token"]):
            raise ActionError("Unknown media token. Upload the file first.", 404)
        chans = [c for c in self.studio.channels(a) if not c.get("isDisconnected")]
        ids = body.get("channel_ids") or [c["id"] for c in chans]
        chans = [c for c in chans if c["id"] in ids]
        if not chans:
            raise ActionError("Pick at least one connected channel.")
        steps = [{"key": k, "label": l, "status": "pending", "detail": "", "data": None} for k, l in (MEDIA_STEPS if kind == "media" else STEPS)]
        inp = {k: body.get(k) for k in ("query", "url", "media_token", "brief", "format", "ratio") if body.get(k)}
        inp["channels"] = [{"id": c["id"], "service": c["service"], "name": c.get("displayName") or c["name"]} for c in chans]
        rid = self.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                           (a.uid, kind, json.dumps(inp), "running", json.dumps(steps), json.dumps({}), _now(), _now()))
        self.store.audit(a.uid, a.label, "agent_run", "read", {"run": rid, "kind": kind})
        self._spawn_run(rid, self._execute, a, rid)
        return self.run(a, rid)

    def _spawn_run(self, rid: int, fn, *args):
        """Run part of a Make a post run in the background with its activity attribution: every AI call it makes is
        logged as the "HuhQueue agent" with this run's id, so the Inbox can show one row per run, not one per call."""
        from . import activity

        def attributed(*xs):
            tok = activity.begin(**{**(activity._ctx.get() or {}), "internal_app": "huhqueue-agent", "run_id": rid})
            try:
                fn(*xs)
            finally:
                activity.end(tok)
        self._spawn(attributed, *args)

    def _spawn(self, fn, *args):
        if self.sync:
            fn(*args)
        else:
            demo_view.submit(_pool, self._safe, fn, *args)  # carries the demo's no-outbound guard into the thread

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()

    def run(self, a: Actor, rid: int) -> dict:
        self._sweep_quietly()
        rows = self.store.q("SELECT * FROM agent_runs WHERE id=? AND user_id=?", (rid, a.uid))
        if not rows:
            raise ActionError("No such agent run.", 404)
        d = dict(rows[0])
        for k in ("input", "steps", "result"):
            d[k] = json.loads(d[k] or "null")
        if d["status"] in ("running", "rendering") and d["updated"] < _now() - STALE_RUN and not self._busy(rid):
            # the worker died, the server restarted mid-run or a callback never came: say so instead of spinning forever
            why = "The agent stopped before finishing (the server restarted or a step timed out). Reply or press Retry to continue."
            for s in d["steps"]:
                if s["status"] in ("running", "pending"):
                    s["status"], s["detail"] = "failed", why
                    break
            (d["result"] or {}).setdefault("thread", []).append({"role": "agent", "text": why, "at": _now()})
            d["status"] = "failed"
            self._save(rid, d["steps"], d["result"], "failed")
        if isinstance(d.get("result"), dict):
            self.annotate_media(d["result"])
        from . import mcp_runs  # what an MCP client (Claude, ChatGPT...) did on this run: its steps and its brain line
        return mcp_runs.merge(self.store, d)

    def _sweep_quietly(self) -> None:
        takes_ = getattr(self, "takes", None)
        if takes_ is not None and takes_.holds_take_lock():  # never nested in a take's lock (it takes other takes' locks)
            return
        try:
            self.sweep_stale_jobs()
        except Exception:  # noqa: BLE001  the watchdog never breaks a page
            traceback.print_exc()

    def _busy(self, rid: int) -> bool:
        from . import agent_brain
        return rid in agent_brain._running

    def runs(self, a: Actor, limit: int = 20) -> list[dict]:
        from .chat_context import titles as _chat_titles  # names given in the Make a post sidebar win
        out, named = [], _chat_titles(self.store, a.uid)
        from .thread_bin import deleted_ids as _deleted_threads
        gone = _deleted_threads(self.store, a.uid)  # threads deleted from the sidebar (the conversation only; media stays)
        # archived runs (cleanup_agent_drafts tidied them away) are hidden from the threads; deleted threads are left out
        # before the LIMIT, so they never use up the sidebar's slots
        for r in self.store.q("SELECT id,kind,input,status,result,created,updated FROM agent_runs WHERE user_id=? AND status NOT IN ('archived','binned') "
                              "AND id NOT IN (SELECT run_id FROM thread_deleted WHERE user_id=?) "
                              "ORDER BY created DESC, id DESC LIMIT ?", (a.uid, a.uid, limit)):
            d = dict(r)
            if d["id"] in gone:
                continue
            inp, res = json.loads(d.pop("input")), json.loads(d.pop("result") or "{}")
            from .headline import title as _title  # never the raw request with its URL (owner P0)
            d["title"] = _title((res.get("story") or {}).get("headline") or "", _req(inp), "Your media" if d["kind"] == "media" else "Your post")
            d["legacy"] = d["kind"] in ("media", "news")
            if d["kind"] == "take":
                # (the take's clip cover used to be overwritten by the generic line below: the blank 5th Recent card)
                d["title"] = "One take: " + (inp.get("name") or "your video")
                d["cover"] = next((c.get("cover_url") for c in res.get("clips") or [] if c.get("cover_url")), "") or \
                    (self.media_url(inp["media_token"]) if inp.get("media_token") else "")
            else:
                d["cover"] = (res.get("media") or [{}])[0].get("url", "") if res.get("media") else ""
            d["packages"] = len(res.get("packages") or [])
            # Make a post v2 "Recent content": type, slides / length badge and a status pill per run
            pk = res.get("packages") or []
            first = next((m for p in pk for m in (p.get("media") or [])), None) or (res.get("media") or [{}])[0] or {}
            fmts = [p.get("format") for p in pk if p.get("format")]
            d["format"] = "reel" if "reel" in fmts or first.get("type") == "video" else ("carousel" if "carousel" in fmts else
                                                                                         ("image" if fmts and fmts[0] == "photo" else (fmts[0] if fmts else "")))
            d["slides"] = max((len(p.get("media") or []) for p in pk if p.get("format") == "carousel"), default=0)
            d["duration"] = first.get("duration") or (res.get("upload") or {}).get("duration") or None
            d["thumb"], d["thumb_src"] = _run_thumb(res, inp), _run_thumb_src(res, inp)
            if named.get(d["id"]):
                d["auto_title"], d["title"] = d["title"], named[d["id"]]
            d["summarized"] = (res.get("thread_summary") or {}).get("summarized", 0)
            sent = [p.get("sent") or {} for p in pk if p.get("sent")]
            d["state"] = ("running" if d["status"] in ("running", "rendering") else "failed" if d["status"] == "failed" else
                          "scheduled" if any(x.get("mode") in ("queue", "schedule") for x in sent) else
                          "draft" if sent else "ready" if pk else d["status"])
            out.append(d)
        try:  # the public thread id (thr_...) the sidebar's delete and MCP's get_thread use
            from .thread_bin import public_ids
            ids = public_ids(self.store, a.uid, [d["id"] for d in out])
            for d in out:
                d["thread_id"] = ids[d["id"]]
        except Exception:  # noqa: BLE001 - never hide the threads over their ids
            traceback.print_exc()
        return out

    def _save(self, rid: int, steps: list, result: dict, status: str | None = None) -> None:
        from . import mcp_runs
        steps, result = mcp_runs.strip(steps, result)  # MCP steps live in mcp_calls, merged on read (never stored twice)
        if isinstance(result, dict) and result.get("packages"):  # media_state is read fresh on every view, never stored
            result = {**result, "packages": [{k: v for k, v in p.items() if k not in ("media_state", "media_missing")}
                                             if isinstance(p, dict) else p for p in result["packages"]]}
        if status:
            self.store.x("UPDATE agent_runs SET steps=?, result=?, status=?, updated=? WHERE id=?",
                         (json.dumps(steps), json.dumps(result, default=str), status, _now(), rid))
        else:
            self.store.x("UPDATE agent_runs SET steps=?, result=?, updated=? WHERE id=?",
                         (json.dumps(steps), json.dumps(result, default=str), _now(), rid))

    def _step(self, steps, key, status, detail="", data=None):
        for s in steps:
            if s["key"] == key:
                s["status"], s["detail"] = status, detail[:400]
                if data is not None:
                    s["data"] = data
                s["at"] = _now()

    def _execute(self, a: Actor, rid: int) -> None:
        run = self.run(a, rid)
        steps, inp, res = run["steps"], run["input"], run["result"] or {}
        try:
            if run["kind"] == "news":
                self._news(a, rid, steps, inp, res)
            else:
                self._media(a, rid, steps, inp, res)
        except ActionError as e:
            cur = next((s for s in steps if s["status"] == "running"), None)
            if cur:
                self._step(steps, cur["key"], "failed", str(e))
            self._save(rid, steps, res, "failed")
        except Exception as e:  # never leave a run spinning
            traceback.print_exc()
            cur = next((s for s in steps if s["status"] == "running"), None)
            if cur:
                self._step(steps, cur["key"], "failed", f"Unexpected error: {e.__class__.__name__}")
            self._save(rid, steps, res, "failed")

    # ---------------------------------------------------------------- news pipeline
    def _news(self, a, rid, steps, inp, res):
        eng = llm.resolve(self.store, a.uid, self.s)
        res["engine"] = eng["source"] if eng else "built-in writer"
        # the free sample: live research while the daily allowance lasts, then the bundled sample story
        if demo.is_sample_url(inp.get("url")) or not demo_guard.take(self.store, a.uid, "research"):
            art = dict(demo.SAMPLE_ARTICLE)
            res["sample"] = demo_guard.why("research")
            res["leads"] = [{"title": art["title"], "url": art["url"], "source": art["site"], "via": "sample", "published": 0}]
            self._step(steps, "find", "done", "Sample story. " + demo_guard.why("research"), {"leads": res["leads"]})
            res["article"] = {k: art[k] for k in ("url", "title", "description", "image", "site")}
            res["verification"] = {"url": art["url"], "site": art["site"], "title": art["title"], "image": "", "other_outlets": [],
                                   "skipped": [], "sample": True}
            self._step(steps, "verify", "done", "Sample story: made up for the demo, so there is nothing to verify", res["verification"])
            self._step(steps, "structure", "running", "Pulling the facts out")
            self._save(rid, steps, res)
            story = self._structure(a, eng, art)
            res["story"] = story
            self._step(steps, "structure", "done", story["headline"], story)
            return self._after_story(a, rid, steps, inp, res, eng)
        # 1 find
        self._step(steps, "find", "running", "Searching Bing News, Hacker News, the source library and your feeds")
        self._save(rid, steps, res)
        if inp.get("url"):
            leads = [{"title": "", "url": inp["url"].strip(), "source": urlparse(inp["url"]).hostname or "", "via": "your_link",
                      "published": 0}]
        else:
            leads = sources.search(inp["query"], self.studio.feeds(a), http=self.http, store=self.store, uid=a.uid)
        if not leads:
            raise ActionError(f"No recent stories found for “{inp.get('query')}”. Try a broader topic or paste a link.")
        res["leads"] = leads[:6]
        self._step(steps, "find", "done", f"{len(leads)} leads; best: {leads[0]['title'] or leads[0]['url']}"[:200], {"leads": leads[:6]})
        # 2 verify: open the page itself; skip leads that don't open
        self._step(steps, "verify", "running", "Opening the source pages")
        self._save(rid, steps, res)
        article, lead, tried = None, None, []
        for ld in leads[:5]:
            url = sources.resolve_google_link(ld["url"], self.http)
            try:
                art = drafter.fetch_article(url, self.http)
            except drafter.FetchError as e:
                tried.append({"url": url, "error": str(e)})
                continue
            if len(" ".join(art.get("paragraphs", []))) < 120 and not art.get("description"):
                tried.append({"url": url, "error": "page has no readable text"})
                continue
            article, lead = art, ld
            break
        if not article and inp.get("url"):
            raise ActionError(f"Your link didn't open for the agent ({tried[0]['error'] if tried else 'no text'}). Some sites block "
                              "bots; try the same story from another outlet, or search by topic.")
        if not article:
            raise ActionError("None of the top stories opened (paywalls or blocked pages). Paste a link you can read.")
        corroborate = self._corroborate(article)
        verified = {"url": article["url"], "site": article["site"], "title": article["title"], "image": article.get("image", ""),
                    "other_outlets": [{"title": c["title"], "source": c["source"], "url": c["url"]} for c in corroborate],
                    "skipped": tried}
        res["article"] = {k: article[k] for k in ("url", "title", "description", "image", "site")}
        res["verification"] = verified
        conf = "also reported by " + ", ".join(c["source"] for c in corroborate) if corroborate else "single source: check before posting"
        self._step(steps, "verify", "done", f"Read {article['site']}; {conf}"[:300], verified)
        # 3 structure
        self._step(steps, "structure", "running", "Pulling the facts out")
        self._save(rid, steps, res)
        story = self._structure(a, eng, article)
        res["story"] = story
        self._step(steps, "structure", "done", story["headline"], story)
        self._after_story(a, rid, steps, inp, res, eng)

    def _corroborate(self, article: dict) -> list[dict]:
        """Other outlets with the same story: a different site whose headline shares most of the key words."""
        stop = {"the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "are", "its", "new", "how", "why", "what"}
        # figures are left out of the match: "$1.3M" and "$13 million" stories must still find each other
        key = {w for w in re.findall(r"[a-z0-9]+", article["title"].lower()) if len(w) > 2 and w not in stop and not re.search(r"\d", w)}
        if len(key) < 2:
            return []
        q = " ".join(sorted(key, key=len, reverse=True)[:6])
        host = (urlparse(article["url"]).hostname or "").removeprefix("www.")
        out, seen = [], {host}
        for l in sources.bing_news(q, self.http, 10) + sources.google_news(q, self.http, 8):
            words = {w for w in re.findall(r"[a-z0-9]+", l["title"].lower()) if len(w) > 2 and w not in stop}
            src = (urlparse(l["url"]).hostname or "").lower().removeprefix("www.") if "news.google.com" not in l["url"] else (l.get("source") or "").lower()
            label = (l.get("source") or src).lower().replace(" ", "")
            if src in seen or label in seen or any(label and label in x.replace(".", "") for x in seen) or not words:
                continue
            if len(key & words) / len(key) >= 0.4:
                seen.update({src, label})
                out.append(l)
            if len(out) == 3:
                break
        return out

    def _structure(self, a, eng, article) -> dict:
        text = " ".join(article.get("paragraphs", []))[:3000]
        eng = self._ai_reserve(a, eng)
        if eng:
            try:
                d = llm.chat_json(eng, "You are a careful news editor. Use only facts in the article; never invent numbers.",
                                  "Return JSON {headline (max 12 words, plain, no hype), facts: [3 short facts from the text], "
                                  "why_it_matters (one sentence), label (1-2 words like 'AI news' or 'Launch')}.\nARTICLE: "
                                  + untrusted.wrap(json.dumps({"title": article["title"], "description": article.get("description", ""),
                                                               "text": text}), "article"),
                                  http=self.http, max_tokens=600)
                self._ai_audit(a, eng, "structure")
                facts = [str(f)[:160] for f in (d.get("facts") or []) if str(f).strip()][:4]
                if d.get("headline") and facts:
                    return {"headline": str(d["headline"])[:120], "facts": facts, "why": str(d.get("why_it_matters") or "")[:200],
                            "label": str(d.get("label") or "News")[:16], "source": article["site"], "url": article["url"],
                            "image": article.get("image", ""), "engine": eng["source"]}
            except llm.LLMError:
                pass
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (article.get("description", "") + " " + text)) if 40 < len(s.strip()) < 220]
        facts, seen = [], set()
        for s in sents:
            k = s[:40].lower()
            if k not in seen:
                seen.add(k)
                facts.append(s)
            if len(facts) == 3:
                break
        return {"headline": re.sub(r"^GitHub - [^:]+:\s*", "", article["title"])[:120], "facts": facts or [article.get("description", "")[:160] or article["title"]],
                "why": "", "label": "News", "source": article["site"], "url": article["url"], "image": article.get("image", ""),
                "engine": "built-in writer"}

    def _after_story(self, a, rid, steps, inp, res, eng):
        # 4 format
        self._step(steps, "format", "running", "")
        fmt = self._pick_format(inp, res)
        res["format"] = fmt
        self._step(steps, "format", "done", fmt["why"], fmt)
        self._save(rid, steps, res)
        # 5 render, 6 caption, 7 check, 8 ready
        self._render_caption_check(a, rid, steps, inp, res, eng)

    def _pick_format(self, inp, res) -> dict:
        services = {c["service"] for c in inp["channels"]}
        want = (inp.get("format") or "auto").lower()
        is_video = (res.get("source_media") or {}).get("type") == "video"
        if want in ("reel", "carousel", "photo"):
            f = want
            why = f"You asked for a {want}."
        elif is_video or services & VIDEO_ONLY:
            f, why = "reel", "A video channel is selected" + (" and you gave a video" if is_video else "") + ", so a 9:16 Reel."
        elif "instagram" in services or "linkedin" in services:
            f, why = "carousel", "Instagram/LinkedIn reward swipeable carousels: cover, facts, follow card (4:5)."
        else:
            f, why = "photo", "One 4:5 card for text-first networks."
        if res.get("source_media", {}).get("type") == "photo" and want == "auto":
            f, why = "photo", "Your photo, edited to the brand kit (4:5)."
        if f == "reel" and not is_video and res.get("story") is None:
            f, why = "photo", "No story or video to build a Reel from."
        ratio = inp.get("ratio") or ("9:16" if f == "reel" else "4:5")
        return {"format": f, "ratio": ratio, "why": why}

    def _render_caption_check(self, a, rid, steps, inp, res, eng, revision: str = ""):
        kit, rules = self.kit(a), self.rules(a)
        p = brand.visual_params(kit, rules)
        logo = self._logo_bytes(kit)
        fmt = res["format"]
        story = res.get("story")
        self._step(steps, "render", "running", "Rendering with your brand kit and ledger")
        self._save(rid, steps, res)
        media = []
        src = res.get("source_media") or {}
        if src.get("type") == "photo":
            data = self.get_media(src["token"])["data"]
            b, m = render.brand_photo(data, kit, p, logo=logo, ratio=fmt["ratio"], caption=inp.get("brief", "")[:120])
            media.append(self._keep(a, b, "photo.jpg", m))
        elif story:
            img = self._fetch_bytes(story.get("image") or "", MAX_IMAGE_FETCH) if story.get("image") else None
            if fmt["format"] == "photo":
                b, m = render.news_card(story["headline"], kit, p, image=img, logo=logo, ratio=fmt["ratio"], label=story.get("label", ""),
                                        credit=story["source"])
                media.append(self._keep(a, b, "card.jpg", m))
            else:
                total = 2 + (1 if p.get("end_card") else 0)
                ratio = "4:5" if fmt["format"] == "carousel" else "9:16"
                b, m = render.news_card(story["headline"], kit, p, image=img, logo=logo, ratio=ratio, label=story.get("label", ""),
                                        credit=story["source"], n=(1, total) if fmt["format"] == "carousel" else None)
                cover = self._keep(a, b, "cover.jpg", m)
                b2, m2 = render.fact_slide(story.get("why") and "Why it matters" or "What happened",
                                           (story["facts"] + ([story["why"]] if story.get("why") else []))[:4], kit, p, logo=logo,
                                           ratio=ratio, n=(2, total) if fmt["format"] == "carousel" else None)
                facts = self._keep(a, b2, "facts.jpg", m2)
                if fmt["format"] == "carousel":
                    media += [cover, facts]
                    if p.get("end_card"):
                        b3, m3 = render.end_card(kit, p, logo=logo, ratio=ratio, n=(total, total))
                        media.append(self._keep(a, b3, "follow.jpg", m3))
                else:
                    media.append({**cover, "role": "slide"})
                    media.append({**facts, "role": "slide"})
        res["media"] = media
        if fmt["format"] == "reel":
            job = self._queue_video(a, rid, kit, p, res, inp)
            res["render_job"] = job
            if job["status"] != "done":
                self._step(steps, "render", "running", job["detail"])
                self._save(rid, steps, res, "rendering")
                if job["status"] == "queued":
                    return  # the worker calls back into finish_video()
            media = res["media"]
        self._step(steps, "render", "done", self._render_note(res, p, revision), {"media": res["media"], "params": brand.dumps(
            {k: p[k] for k in ("logo_position", "logo_scale", "headline_case", "font", "end_card")})})
        self._caption_check(a, rid, steps, inp, res, eng)

    def _render_note(self, res, p, revision):
        kinds = [m["type"] for m in res["media"]]
        what = f"{kinds.count('video')} video" if "video" in kinds else f"{len(kinds)} image" + ("s" if len(kinds) > 1 else "")
        rules = len(p.get("applied_rules") or [])
        return (f"Re-rendered after “{revision}”: " if revision else "") + f"{what}, {rules} ledger rule" + ("" if rules == 1 else "s") + " applied"

    def _caption_check(self, a, rid, steps, inp, res, eng):
        kit, rules = self.kit(a), self.rules(a)
        self._step(steps, "caption", "running", "Writing one caption per channel")
        self._save(rid, steps, res)
        story = res.get("story")
        article = res.get("article") or {"url": "", "title": inp.get("brief") or kit["name"], "description": inp.get("brief", ""),
                                         "site": kit["name"], "image": "", "paragraphs": [inp.get("brief", "")]}
        if story:
            article = {**article, "description": (story.get("why") or article.get("description", "")),
                       "paragraphs": story["facts"] + article.get("paragraphs", [])[:3]}
        chans = inp["channels"]
        tone = "; ".join(x for x in [kit.get("voice", ""), brand.rules_prompt([r for r in rules if not r.get("scope")])] if x)
        if eng and eng["style"] == "openai":
            eng = self._ai_reserve(a, eng)
        ai = {"base_url": eng["base_url"], "api_key": eng["api_key"], "model": eng["model"]} if eng and eng["style"] == "openai" else None
        drafts = drafter.draft(chans, article, ai=ai, http=self.http, tone=tone)
        if ai:
            self._ai_audit(a, eng, "captions")
        self._step(steps, "caption", "done", f"{len(drafts)} captions ({drafts[0]['engine'] if drafts else '-'})")
        # 7 check
        self._step(steps, "check", "running", "Checking every caption and render against the ledger")
        self._save(rid, steps, res)
        main = next((m for m in res["media"] if m["type"] == "video"), None) or (res["media"][0] if res["media"] else None)
        packages, fails = [], 0
        trace: list = []
        from .ledger import evidence as _evidence
        ev = _evidence(res.get("story"), res.get("article") or res.get("source"))
        for d in drafts:
            svc = d["service"]
            scoped = brand.for_channel(rules, svc)
            text = drafter.fit(svc, brand.apply_text_rules(d["text"], scoped, trace))
            meta = self.media_meta(a, main["token"]) if main and main.get("token") else None  # the sample clip has no token
            chk = brand.brand_check(text, meta, scoped, kit)
            fails += len(chk["failed"])
            urls = self._media_for(svc, res)
            packages.append({"channel_id": d["channel_id"], "service": svc, "channel_name": d["channel_name"], "text": text,
                             "length": drafter.count(svc, text), "limit": drafter.limit_for(svc), "media_urls": urls,
                             "check": chk, "status": "ready", "applied_rules": self.ledger.applied(rules, svc), "evidence": ev})
        self.ledger.record_examples(a.uid, trace)
        res["packages"] = packages
        self._step(steps, "check", "done" if not fails else "warn",
                   "Every package passes the ledger" if not fails else f"{fails} rule issue(s): see each package", {"failed": fails})
        self._step(steps, "ready", "done", "Review, then schedule or send to your Buffer queue")
        self._save(rid, steps, res, "ready")

    def _media_for(self, svc: str, res: dict) -> list[str]:
        vids = [m["url"] for m in res["media"] if m["type"] == "video"]
        imgs = [m["url"] for m in res["media"] if m["type"] == "photo" and m.get("role") != "slide"]
        if svc in VIDEO_ONLY:
            return vids[:1]
        if vids:
            return vids[:1]
        if svc in ("instagram", "linkedin", "facebook", "threads"):
            return imgs[:10]
        if svc in ("twitter", "bluesky", "mastodon"):
            return imgs[:4] if svc != "bluesky" else imgs[:4]
        return imgs[:1]

    def _keep(self, a, data: bytes, name: str, meta: dict) -> dict:
        tok = self.put_media(a, data, name, kind="render", meta=meta)
        return {"token": tok, "url": self.media_url(tok), "type": meta["type"], "name": name}

    # ---------------------------------------------------------------- media pipeline (a person's own photo/video)
    def _media(self, a, rid, steps, inp, res):
        eng = llm.resolve(self.store, a.uid, self.s)
        res["engine"] = eng["source"] if eng else "built-in writer"
        self._step(steps, "upload", "running", "")
        meta = self.media_meta(a, inp["media_token"]) or {}
        res["source_media"] = {"token": inp["media_token"], "type": meta.get("type", "photo"), "url": self.media_url(inp["media_token"])}
        self._step(steps, "upload", "done", f"Your {res['source_media']['type']}", res["source_media"])
        self._after_story(a, rid, steps, inp, res, eng)

    # ---------------------------------------------------------------- video jobs
    def _queue_video(self, a, rid, kit, p, res, inp) -> dict:
        src = res.get("source_media") or {}
        from .headline import safe_headline, spec_hook
        spec = {"kind": "brand_video" if src.get("type") == "video" else "slideshow", "kit": {k: kit[k] for k in brand.DEFAULT_KIT},
                "params": p, "ratio": "9:16", "hook": safe_headline((res.get("story") or {}).get("headline", ""), _req(inp)),
                "logo": kit.get("logo") or "", "source": src.get("token", ""),
                "slides": [m["token"] for m in res["media"] if m.get("role") == "slide"]}
        spec_hook(spec, _req(inp))
        jid = self.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                           (a.uid, rid, json.dumps(spec), "queued", _now(), _now()))
        mode = self.video_mode()
        # the free sample never starts the GitHub worker (SECURITY_RULES 3): it renders on this server under its daily cap,
        # or shows the bundled sample clip. The cap is checked before any render work starts.
        if demo_guard.is_demo(a.uid) and ((mode == "github" and not self._worker_allowed(a.uid))
                                          or (mode == "local" and not demo_guard.take(self.store, a.uid, "video"))):
            from . import takes
            clip = self.s.public_url.rstrip("/") + "/sample/" + (takes.sample_meta() or {}).get("clips", [{"file": "clip1.mp4"}])[0]["file"]
            self.store.x("UPDATE render_jobs SET status='done', error=?, updated=? WHERE id=?", ("sample: demo video cap", _now(), jid))
            res["media"] = [m for m in res["media"] if m.get("role") != "slide"] + [{"url": clip, "type": "video", "sample": True}]
            res["sample"] = demo_guard.why("video")
            return {"id": jid, "status": "done", "detail": demo_guard.why("video")}
        if mode == "local":
            try:
                data, meta = self.render_job_local(spec)
                self.finish_video(jid, data, meta, run_inline=False)
                res["media"] = [m for m in res["media"] if m.get("role") != "slide"] + [self._job_media(jid)]
                return {"id": jid, "status": "done", "detail": "Rendered here"}
            except Exception as e:
                self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (str(e)[:500], _now(), jid))
                raise ActionError(f"Video render failed: {str(e)[:200]}")
        if mode == "github":
            ok, why = self._dispatch_github(jid)
            if ok:
                return {"id": jid, "status": "queued", "detail": "Rendering the video on the free render worker (about a minute)"}
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, _now(), jid))
        # no worker: fall back to the slides as a carousel, and say so
        res["format"] = {**res["format"], "format": "carousel", "why": res["format"]["why"] + " The video worker isn't available, so "
                         "I made the carousel instead."}
        for m in res["media"]:
            m.pop("role", None)
        return {"id": jid, "status": "done", "detail": "No video worker: carousel instead"}

    def transcribe(self, a: Actor, rid: int, token: str) -> tuple[str, str]:
        """(transcript, note) for the person's own uploaded video, with studio.media transcribe (faster-whisper): in this
        process where video may run, else on the free video worker (a 'transcribe' job). Never on the free sample."""
        if demo_guard.is_demo(a.uid):
            return "", "Transcripts need the video worker, which the free sample doesn't run."
        mode = self.video_mode()
        if mode == "local":
            import tempfile
            m = self.get_media(token)
            if not m:
                return "", "The upload is gone."
            try:
                from .media import transcribe as tr
                with tempfile.TemporaryDirectory(prefix="hq_tr_") as d:
                    path = os.path.join(d, "in.mp4")
                    with open(path, "wb") as fh:
                        fh.write(m["data"])
                    out = tr.transcribe(path)
                return out.get("text", ""), "Transcribed on this server."
            except Exception as e:  # noqa: BLE001  faster-whisper missing, silent clip, ...
                return "", f"No transcript ({str(e)[:120]})."
        if mode != "github":
            return "", "No transcriber here (the video worker isn't set up)."
        jid = self.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                           (a.uid, rid, json.dumps({"kind": "transcribe", "source": token, "model": os.environ.get("HQ_WHISPER_MODEL", "tiny")}),
                            "queued", _now(), _now()))
        ok, why = self._dispatch_github(jid)
        if not ok:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, _now(), jid))
            return "", f"The transcriber didn't start ({why})."
        t0, wait = _now(), int(os.environ.get("HQ_TRANSCRIBE_WAIT", "240"))
        while _now() - t0 < wait:
            time.sleep(0.05 if self.sync else 3)
            row = self.store.q("SELECT status, error, result FROM render_jobs WHERE id=?", (jid,))[0]
            self.store.x("UPDATE agent_runs SET updated=? WHERE id=?", (_now(), rid))
            if row["status"] == "done":
                return (json.loads(row["result"] or "{}").get("text") or ""), "Transcribed by the video worker."
            if row["status"] == "failed":
                return "", f"No transcript ({(row['error'] or '')[:120]})."
        self.store.x("UPDATE render_jobs SET status='failed', error='timed out', updated=? WHERE id=?", (_now(), jid))
        return "", "The transcriber took too long; I went on without it."

    @staticmethod
    def local_video_ok() -> bool:
        """May this process run FFmpeg itself? Never on the hosted app: Render's free instance has 512 MB and a Reel render
        peaked at 1.5 GB, so video goes to the GitHub render worker there (RENDER is set by Render; HQ_NO_LOCAL_VIDEO=1
        does the same anywhere)."""
        if os.environ.get("HQ_NO_LOCAL_VIDEO") == "1" or os.environ.get("RENDER"):
            return False
        return render.have_ffmpeg()

    def video_mode(self) -> str:
        """Where video renders: local (FFmpeg in this process), github (the free render worker) or none."""
        m = self.worker_mode
        if m == "local" and not self.local_video_ok():
            m = ""
        if m:
            return m
        return "local" if self.local_video_ok() else ("github" if os.environ.get("GH_DISPATCH_TOKEN") else "none")

    def render_job_local(self, spec: dict) -> tuple[bytes, dict]:
        from .headline import spec_hook
        spec = spec_hook(dict(spec))  # render time: the same guard as the GitHub worker
        logo = self.get_media(spec["logo"])["data"] if spec.get("logo") and self.get_media(spec["logo"]) else None
        if spec["kind"] == "template_video":
            from . import templates
            def _b(t):
                m = self.get_media(t) if t else None
                return m["data"] if m else None
            b = templates.Brand(spec["kit"], logo, _b(spec.get("front")), _b(spec.get("end")))
            return templates.video(spec["template"], spec.get("data"), b, spec.get("ratio", "9:16")), {"type": "video", "template": spec["template"]}
        if spec["kind"] == "media_render":  # studio.media styled edit (docs/MEDIA_PIPELINE.md); off the hosted app only
            from .media.worker import render_job
            base = self.s.public_url.rstrip("/") + "/m/"
            urls = {"source": base + spec["source"] if spec.get("source") else "", "slides": [base + t for t in spec.get("slides", [])],
                    "cover_image": base + spec["cover_image"] if spec.get("cover_image") else ""}

            def _get(u):
                m = self.get_media(u[len(base):]) if u.startswith(base) else None
                return m["data"] if m else None
            return render_job(spec, _get, urls=urls, logo=logo, http=self.http)
        if spec["kind"] == "cover_swap":
            from .media.worker import cover_swap
            return cover_swap(self.get_media(spec["source"])["data"], self.get_media(spec["cover"])["data"], spec.get("secs") or 0.6,
                              spec.get("replace"))
        if spec["kind"] == "brand_video":
            return render.brand_video(self.get_media(spec["source"])["data"], spec["kit"], spec["params"], logo=logo,
                                      hook=spec.get("hook", ""), ratio=spec.get("ratio", "9:16"))
        slides = [self.get_media(t)["data"] for t in spec["slides"]]
        return render.slideshow_reel(slides, spec["kit"], spec["params"], logo=logo, ratio=spec.get("ratio", "9:16"), total=spec.get("total"))

    def _worker_allowed(self, uid: str) -> bool:
        """May this account start the GitHub render worker? Never for the free sample (SECURITY_RULES 3)."""
        return not demo_guard.is_demo(uid)

    def _dispatch_github(self, jid: int, workflow: str = "render.yml", inputs: dict | None = None, ttl: int = 1800) -> tuple[bool, str]:
        """Start the free render worker for one job. No token travels in the dispatch (its inputs show on the run page):
        the run claims this job once with its GitHub OIDC identity (worker_auth) inside the claim window (30 minutes;
        longer for One take jobs) and gets a token that only works for this job, in memory only. SECURITY_RULES 2."""
        owner = self.store.q("SELECT user_id FROM render_jobs WHERE id=?", (jid,))
        if not owner or not self._worker_allowed(owner[0]["user_id"]):  # belt and braces: sample jobs never reach the worker
            return False, "sample workspace: no worker runs"
        from . import instabox_worker
        if instabox_worker.enabled():  # opt-in (HQ_RENDER_BACKEND=instabox): a warm InstaBox VM, docs/INSTABOX_WORKER.md
            ok, why = instabox_worker.dispatch(self, jid, workflow, inputs, ttl)
            if ok or not os.environ.get("GH_DISPATCH_TOKEN"):
                return ok, why
            # InstaBox failed or timed out: fall back to the GitHub Actions run. open_claim below replaces the job token
            # the box was given, so a box job that did start late can no longer read or write this job.
            print(f"render: InstaBox dispatch failed for job {jid} ({why}); falling back to GitHub Actions", flush=True)
            gh_ok, gh_why = self._dispatch_github_actions(jid, workflow, inputs, ttl)
            return gh_ok, f"{why}; fallback: {gh_why}"
        return self._dispatch_github_actions(jid, workflow, inputs, ttl)

    @staticmethod
    def backend_label(why: str) -> str:
        """Which worker took the job, from _dispatch_github's answer: InstaBox, or GitHub Actions (also as InstaBox's
        fallback). Replies name the real backend (owner bug 2 Nov 2026: an InstaBox render was called the GitHub worker)."""
        w = str(why or "")
        if "fallback:" in w:
            return "the GitHub Actions video worker (InstaBox fallback)"
        if w.startswith("InstaBox"):
            return "the InstaBox video worker"
        return "the GitHub Actions video worker"

    def _dispatch_github_actions(self, jid: int, workflow: str = "render.yml", inputs: dict | None = None, ttl: int = 1800) -> tuple[bool, str]:
        """The GitHub Actions render run (the default backend, and the fallback when InstaBox fails)."""
        repo = os.environ.get("GH_REPO", "dataanalystram/buffer-contest")
        token = os.environ.get("GH_DISPATCH_TOKEN", "")
        self.open_claim(jid, ttl, workflow)
        try:
            r = (self.http or httpx.Client(timeout=15)).post(
                f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"ref": os.environ.get("GH_REF", "main"),
                      "inputs": {"job_id": str(jid), "app": self.s.public_url, **(inputs or {})}})
            return (r.status_code == 204, f"GitHub answered {r.status_code}")
        except httpx.HTTPError as e:
            return False, e.__class__.__name__

    # ---- the worker's job token: claimed once, read once, each clip once, dead after the result (SECURITY_RULES 2)
    def _token_state(self, jid: int) -> tuple[str, dict, dict] | None:
        rows = self.store.q("SELECT spec, status FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            return None
        raw = rows[0]["spec"]
        spec = json.loads(raw)
        return raw, spec, {"status": rows[0]["status"]}

    def _cas(self, jid: int, old_raw: str, spec: dict) -> bool:
        """Write the job spec only if nobody changed it since we read it (one claim, one read, even under races)."""
        return bool(self.store.q("UPDATE render_jobs SET spec=? WHERE id=? AND spec=? RETURNING id", (json.dumps(spec), jid, old_raw)))

    def open_claim(self, jid: int, ttl: int = 1800, workflow: str = "render.yml") -> None:
        st = self._token_state(jid)
        if not st:
            return
        _, spec, _ = st
        spec["_job_token"] = {"claim_until": _now() + ttl, "ttl": ttl, "workflow": workflow}
        self.store.x("UPDATE render_jobs SET spec=? WHERE id=?", (json.dumps(spec), jid))

    def claim_job(self, jid: int, oidc_jwt: str, jwks: dict | None = None) -> tuple[int, dict]:
        """The worker trades its GitHub OIDC token for this job's token, once. (status, body)."""
        from . import worker_auth
        st = self._token_state(jid)
        wf = ((st[1].get("_job_token") or {}).get("workflow") if st else None) or "render.yml"
        cfg = worker_auth.settings()
        try:
            worker_auth.verify(oidc_jwt, audience=self.s.public_url, repo=cfg["repo"], workflows=(wf,), ref=cfg["ref"],
                               jwks=jwks, http=None)
        except worker_auth.OIDCError as e:
            return 401, {"error": f"not the worker: {e}"}
        except httpx.HTTPError:
            return 503, {"error": "couldn't read GitHub's signing keys"}
        if not st:
            return 404, {"error": "no such job"}
        raw, spec, meta = st
        t = spec.get("_job_token") or {}
        if meta["status"] != "queued" or t.get("claimed") or t.get("claim_until", 0) < _now():
            return 409, {"error": "this job can't be claimed (already claimed, finished or expired)"}
        jt = "jt_" + secrets.token_urlsafe(24)
        from .store import sha
        spec["_job_token"] = {**t, "claimed": True, "hash": sha(jt), "expires": _now() + int(t.get("ttl") or 1800), "read": False, "clips": []}
        if not self._cas(jid, raw, spec):
            return 409, {"error": "this job was claimed just now"}
        return 200, {"job_token": jt}

    def issue_job_token(self, jid: int, ttl: int = 1800) -> str:
        """A token for one job (what a claim hands out; tests and the local worker call it directly)."""
        from .store import sha
        jt = "jt_" + secrets.token_urlsafe(24)
        rows = self.store.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))
        spec = json.loads(rows[0]["spec"])
        spec["_job_token"] = {"claimed": True, "hash": sha(jt), "expires": _now() + ttl, "read": False, "clips": []}
        self.store.x("UPDATE render_jobs SET spec=? WHERE id=?", (json.dumps(spec), jid))
        return jt

    def job_token_ok(self, jid: int, jt: str, use: str = "write", clip_id: str = "") -> bool:
        """use: 'read' (the job spec, once), 'clip' (each of this job's clips, once) or 'write' (the result; the token
        dies with it because the job leaves 'queued'). Every use is recorded atomically, so a copied token can't replay."""
        import hmac
        from .store import sha
        st = self._token_state(jid)
        if not st or not jt:
            return False
        raw, spec, meta = st
        t = spec.get("_job_token") or {}
        if meta["status"] != "queued" or not t.get("hash") or t.get("expires", 0) <= _now() or not hmac.compare_digest(t["hash"], sha(jt)):
            return False
        if use == "read":
            if t.get("read"):
                return False
            t["read"] = True
        elif use == "clip":
            ids = {str(c.get("id")) for c in spec.get("clips") or []} | \
                {str(v) for c in spec.get("clips") or [] for v in c.get("variant_ids") or []}  # other ratios of a clip
            if clip_id not in ids or clip_id in (t.get("clips") or []):
                return False
            t["clips"] = list(t.get("clips") or []) + [clip_id]
        else:
            return True  # 'write' (and 'beat'): the result write moves the job out of 'queued'; burn_job_token makes sure
        spec["_job_token"] = t
        return self._cas(jid, raw, spec)

    # ---- worker liveness (2 Nov 2026): heartbeat, failure log tail, and the watchdog that fails silent jobs
    def job_heartbeat(self, jid: int) -> bool:
        """The worker is alive: bump the job's clock (the watchdog reads `updated`). Only while the job is open."""
        return bool(self.store.q("UPDATE render_jobs SET updated=? WHERE id=? AND status='queued' RETURNING id", (_now(), jid)))

    def keep_job_log(self, jid: int, tail: str) -> None:
        """The worker's log tail on a failure: kept on the job (result.log_tail, server side only) and in the app log."""
        tail = re.sub(r"jt_[A-Za-z0-9_\-]{8,}", "jt_[redacted]", str(tail or ""))[-4000:]
        if not tail:
            return
        rows = self.store.q("SELECT result FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            return
        try:
            res = json.loads(rows[0]["result"] or "{}")
        except ValueError:
            res = {}
        if not isinstance(res, dict):
            res = {"value": res}
        res["log_tail"] = tail
        self.store.x("UPDATE render_jobs SET result=? WHERE id=?", (json.dumps(res), jid))
        print(f"render: job {jid} failed on the worker; log tail:\n{tail[-1500:]}", flush=True)

    _swept = 0.0

    def sweep_stale_jobs(self, now: float | None = None, force: bool = False) -> list[int]:
        """Fail every worker job (one that was handed a job token) that has been silent for JOB_STALE_MIN minutes: the
        worker was killed, the box paused or the runner never started, so no failure callback will ever come. Runs from
        the app's watchdog thread and, throttled to once a minute, when a run or take is read. Returns the failed ids."""
        t = _now() if now is None else now
        if not force and t - self._swept < 60:
            return []
        self._swept = t
        cutoff = t - JOB_STALE_MIN * 60
        rows = self.store.q("SELECT id FROM render_jobs WHERE status='queued' AND updated < ? AND spec LIKE ?",
                            (cutoff, '%"_job_token"%'))
        why = JOB_STALE_WHY.format(n=int(JOB_STALE_MIN) if JOB_STALE_MIN == int(JOB_STALE_MIN) else JOB_STALE_MIN)
        failed = []
        for r in rows:
            jid = r["id"]
            # claim the job atomically, so a heartbeat or result landing right now wins and the job is left alone
            if not self.store.q("UPDATE render_jobs SET updated=? WHERE id=? AND status='queued' AND updated < ? RETURNING id",
                                (t, jid, cutoff)):
                continue
            try:
                self.finish_video(jid, None, None, why)  # the same path as a worker-reported failure: the run/take shows it
            except Exception:  # noqa: BLE001  an orphaned job (its run is gone) still gets closed
                traceback.print_exc()
            self.store.x("UPDATE render_jobs SET status='failed', error=? WHERE id=? AND status='queued'", (why, jid))
            self.burn_job_token(jid)
            print(f"render: watchdog failed job {jid} (no heartbeat for {JOB_STALE_MIN:g} min)", flush=True)
            failed.append(jid)
        return failed

    def burn_job_token(self, jid: int) -> None:
        st = self._token_state(jid)
        if not st:
            return
        raw, spec, _ = st
        if (spec.get("_job_token") or {}).get("hash"):
            spec["_job_token"] = {**spec["_job_token"], "hash": "", "burned": _now()}
            self._cas(jid, raw, spec)

    def job_spec(self, jid: int) -> dict | None:
        rows = self.store.q("SELECT * FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            return None
        spec = json.loads(rows[0]["spec"])
        if str(spec.get("kind", "")).startswith("take_"):
            return self.takes.job_payload(jid)
        spec.pop("_job_token", None)
        from .headline import spec_hook
        spec_hook(spec, self._run_prompt(rows[0]["run_id"]))  # jobs queued before the guard existed are cleaned here too
        if spec.get("candidate"):  # a found platform video: the operator's YouTube fallback flag travels with the job
            from .media import yt_fallback
            spec["yt_fallback"] = yt_fallback.config()
        base = self.s.public_url.rstrip("/") + "/m/"
        for k in ("logo", "source", "front", "end", "cover_image", "cover"):  # a media token or nothing, never a flag
            if k in spec and not isinstance(spec[k], str):
                spec[k] = ""
        return {"id": jid, "status": rows[0]["status"], "spec": spec,
                "urls": {"logo": base + spec["logo"] if spec.get("logo") else "", "source": base + spec["source"] if spec.get("source") else "",
                         "slides": [base + t for t in spec.get("slides", [])],
                         "front": base + spec["front"] if spec.get("front") else "", "end": base + spec["end"] if spec.get("end") else "",
                         "cover_image": base + spec["cover_image"] if spec.get("cover_image") else "",
                         "cover": base + spec["cover"] if spec.get("cover") else ""}}

    # ---- the weekly recap (job kind media_recap): cover + one video slide per story + Follow card (4:5) and one Reel (9:16)
    RECAP_KINDS = {"cover": ("photo", "png"), "follow": ("photo", "png"), "reel": ("video", "mp4")}

    def job_kind(self, jid: int) -> str:
        rows = self.store.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))
        return json.loads(rows[0]["spec"]).get("kind", "") if rows else ""

    def on_recap_file(self, jid: int, clip_id: str, data: bytes, meta: dict | None = None) -> dict:
        """One recap file from the worker (token-checked per file by the route). Checked to be what its id says (a PNG
        for the cards, an MP4 for slides and the Reel) before it is stored."""
        typ, ext = self.RECAP_KINDS.get(clip_id, ("video", "mp4"))
        is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
        is_mp4 = len(data) > 12 and data[4:8] == b"ftyp"
        if (ext == "png" and not is_png) or (ext == "mp4" and not is_mp4):
            raise ActionError(f"{clip_id}: not a {ext.upper()} file.", 415)
        rows = self.store.q("SELECT user_id, result FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            raise ActionError("No such job.", 404)
        m = {k: v for k, v in (meta or {}).items() if k in ("width", "height", "duration", "ratio", "credit") and
             isinstance(v, (int, float, str))}
        m["ratio"] = "9:16" if clip_id == "reel" else "4:5"
        tok = self.put_media(Actor(rows[0]["user_id"]), data, f"recap_{clip_id}.{ext}", kind="render", meta={"type": typ, **m})
        res = json.loads(rows[0]["result"] or "{}")
        res.setdefault("files", {})[clip_id] = tok
        self.store.x("UPDATE render_jobs SET result=?, updated=? WHERE id=?", (json.dumps(res), _now(), jid))
        return {"ok": True, "token": tok}

    def recap_media(self, jid: int) -> dict:
        """The finished recap as agent media: {"carousel": [cover, s1..sN, follow], "reel": [reel]}."""
        rows = self.store.q("SELECT spec, result FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            return {}
        files = json.loads(rows[0]["result"] or "{}").get("files") or {}
        order = [c["id"] for c in json.loads(rows[0]["spec"]).get("clips") or [] if c["id"] != "reel"]

        def item(cid):
            typ, ext = self.RECAP_KINDS.get(cid, ("video", "mp4"))
            return {"token": files[cid], "url": self.media_url(files[cid]), "type": typ, "name": f"recap_{cid}.{ext}"}
        car = [item(c) for c in order if c in files]
        return {"carousel": car if len(car) == len(order) else [], "reel": [item("reel")] if "reel" in files else []}

    def render_recap_local(self, jid: int, spec: dict) -> None:
        """Off the hosted app only (video_mode local): render the recap in this process, store it like the worker would."""
        import shutil
        import tempfile

        from .media import tools as media_tools
        from .media.worker import brand_from_kit
        tmp = tempfile.mkdtemp(prefix="hq_recap_job_")
        try:
            logo_path = None
            lm = self.get_media(spec["logo"]) if spec.get("logo") else None
            if lm:
                logo_path = os.path.join(tmp, "logo.png")
                with open(logo_path, "wb") as fh:
                    fh.write(lm["data"])
            out = media_tools.media_recap(spec["plan"], True, brand_from_kit(spec.get("kit"), logo_path), run_dir=tmp, http=self.http)
            for cid, path, meta in media_tools.recap_files(out):
                with open(path, "rb") as fh:
                    self.on_recap_file(jid, cid, fh.read(), meta)
            self.finish_video(jid, None, None, run_inline=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _job_media(self, jid: int) -> dict:
        tok = self.store.q("SELECT media_token FROM render_jobs WHERE id=?", (jid,))[0]["media_token"]
        return {"token": tok, "url": self.media_url(tok), "type": "video", "name": "reel.mp4"}

    def finish_video(self, jid: int, data: bytes | None, meta: dict | None, error: str = "", run_inline: bool = True,
                     body: dict | None = None) -> dict:
        error = plain_worker_error(error)
        rows = self.store.q("SELECT * FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            raise ActionError("No such job.", 404)
        job = dict(rows[0])
        kind = json.loads(job["spec"]).get("kind", "")
        if kind == "transcribe":
            tr = (body or {}).get("transcript") if isinstance((body or {}).get("transcript"), dict) else None
            ok = bool(tr and not error)
            self.store.x("UPDATE render_jobs SET status=?, error=?, result=?, updated=? WHERE id=?",
                         ("done" if ok else "failed", (error or ("" if ok else "no transcript"))[:500],
                          json.dumps({"text": str(tr.get("text") or "")[:20000], "language": str(tr.get("language") or "")[:8]}) if ok else None,
                          _now(), jid))
            return {"ok": ok}
        if kind == "media_recap":  # the files already arrived one by one (on_recap_file); this closes the job
            files = json.loads(job.get("result") or "{}").get("files") or {}
            spec = json.loads(job["spec"])
            want = {c["id"] for c in spec.get("clips") or []}
            ok = not error and want <= set(files)
            self.store.x("UPDATE render_jobs SET status=?, error=?, updated=? WHERE id=?",
                         ("done" if ok else "failed", (error or ("" if ok else f"missing {', '.join(sorted(want - set(files)))}"))[:500],
                          _now(), jid))
            return {"ok": ok}
        if kind == "image_matte":  # the ML cut-out's PNG (studio/image_edit.py): run media, never a Library item
            from . import image_edit
            return image_edit.finish_matte(self, job, data, meta, error)
        if kind == "take_analyze":
            return self.takes.on_analysis(jid, (body or {}).get("analysis"), error)
        if kind == "take_render":
            return self.takes.on_render_done(jid, error)
        a = Actor(job["user_id"])
        if error or not data:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (error[:500] or "no output", _now(), jid))
            if run_inline and job["run_id"] and self._run_kind(job["run_id"]) != "agent":
                run = self.run(a, job["run_id"])
                self._step(run["steps"], "render", "failed", f"Video render failed: {error[:200]}")
                self._save(job["run_id"], run["steps"], run["result"], "failed")
            return {"ok": False}
        meta = dict(meta or {"type": "video"})
        if meta.get("covers_b64"):
            meta["covers"] = self._store_covers(a, meta.pop("covers_b64"))
        meta.pop("covers_b64", None)
        src_tok = json.loads(job["spec"]).get("source")
        if isinstance(src_tok, str) and src_tok and kind in ("media_render", "cover_swap", ""):
            # the video this render was made from: re-editing a finished reel starts again from it (mcp edit_video), so the
            # cover, title card, logo and end card are never stacked a second time (owner bug 3 Nov 2026, Click reel)
            meta.setdefault("source_token", src_tok)
        tok = self.put_media(a, data, "reel.mp4", kind="render", meta=meta)
        self.store.x("UPDATE render_jobs SET status='done', media_token=?, updated=? WHERE id=?", (tok, _now(), jid))
        jspec = json.loads(job["spec"])
        if jspec.get("for_run"):  # an AI app's render for its post: it becomes that run's output (one run, one thread)
            try:
                from . import mcp_client_brain
                mcp_client_brain.record_output(self, a, int(jspec["for_run"]), int(jspec.get("for_output") or 1), "reel",
                                               [{"token": tok, "type": "video", "url": self.media_url(tok)}], job_id=jid)
            except Exception:  # noqa: BLE001 - bookkeeping never fails the render
                pass
        if run_inline and job["run_id"] and self._run_kind(job["run_id"]) != "agent":  # the agent brain waits on its own job
            self._spawn_run(job["run_id"], self._resume_after_video, a, job["run_id"], jid)
        return {"ok": True, "token": tok}

    def _store_covers(self, a: Actor, covers: list) -> list[dict]:
        """The worker's cover variants (base64 JPEGs per size) -> media tokens: [{variant, layout, score, frame_t, source,
        files: {ratio: token}}]. Each file is checked to be a JPEG and capped (SECURITY_RULES 6)."""
        out = []
        for c in (covers or [])[:4]:
            if not isinstance(c, dict):
                continue
            files = {}
            for r, b64 in (c.get("files") or {}).items():
                if r not in ("9:16", "4:5", "16:9") or not isinstance(b64, str) or len(b64) > 3_000_000:
                    continue
                try:
                    raw = base64.b64decode(b64, validate=True)
                except (ValueError, TypeError):
                    continue
                if raw[:3] != b"\xff\xd8\xff":
                    continue
                files[r] = self.put_media(a, raw, f"cover-{c.get('variant', 0)}-{r.replace(':', 'x')}.jpg", kind="render",
                                          meta={"type": "photo", "role": "cover", "ratio": r})
            if files:
                out.append({"variant": int(c.get("variant") or 0), "layout": str(c.get("layout") or "")[:12],
                            "score": float(c.get("score") or 0), "frame_t": c.get("frame_t"), "source": str(c.get("source") or "")[:8],
                            "files": files})
        return out

    def _run_prompt(self, rid) -> str:
        rows = self.store.q("SELECT input FROM agent_runs WHERE id=?", (rid,)) if rid else []
        try:
            return _req(json.loads(rows[0]["input"] or "{}")) if rows else ""
        except ValueError:
            return ""

    def _run_kind(self, rid: int) -> str:
        rows = self.store.q("SELECT kind FROM agent_runs WHERE id=?", (rid,))
        return rows[0]["kind"] if rows else ""

    def _resume_after_video(self, a, rid, jid):
        try:
            self._resume_inner(a, rid, jid)
        except Exception as e:  # the "hangs at Write captions" bug: an error here used to leave the run 'rendering' forever
            traceback.print_exc()
            run = self.run(a, rid)
            cur = next((s for s in run["steps"] if s["status"] == "running"), None)
            if cur:
                self._step(run["steps"], cur["key"], "failed", f"{e.__class__.__name__}: {str(e)[:200]}")
            self._save(rid, run["steps"], run["result"], "failed")

    def _resume_inner(self, a, rid, jid):
        run = self.run(a, rid)
        steps, res, inp = run["steps"], run["result"], run["input"]
        res["media"] = [m for m in res["media"] if m.get("role") != "slide"] + [self._job_media(jid)]
        p = brand.visual_params(self.kit(a), self.rules(a))
        self._step(steps, "render", "done", self._render_note(res, p, res.get("last_revision", "")), {"media": res["media"]})
        self._caption_check(a, rid, steps, inp, res, llm.resolve(self.store, a.uid, self.s))

    # ---------------------------------------------------------------- revise: correction -> rule -> re-render
    def redo_with_agent(self, a: Actor, rid: int, message: str = "") -> dict:
        """A run from the pre-agent pipeline (kind media/news) is never re-run by that pipeline: a reply or a redo starts
        a fresh agent run from the same request, file and channels (owner P0: the old pipeline burned the request into
        the video and wrote captions about the request). The old run points at the new one."""
        run = self.run(a, rid)
        inp = run["input"] or {}
        prompt = _req(inp)
        msg = (message or "").strip()
        if msg and not re.fullmatch(r"(?i)\s*(?:redo|re-?do|again|redo (?:it )?with the agent)\s*", msg):
            prompt = (prompt + "\n" + msg).strip()
        body = {"prompt": prompt[:4000], "channel_ids": [c["id"] for c in inp.get("channels") or []],
                "format": inp.get("format") or "auto"}
        if inp.get("media_token"):
            body["media_token"] = inp["media_token"]
        new = self.start_run(a, body)
        res = run["result"] or {}
        res["superseded_by"] = new["id"]
        self._save(rid, run["steps"], res)
        return new

    def revise(self, a: Actor, rid: int, correction: str) -> dict:
        self.studio._guard_write(a, "revise")
        if self._run_kind(rid) in ("media", "news"):  # never re-run the pre-agent pipeline (owner P0): the agent remakes it
            return self.brain.reply(a, rid, correction)
        if demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "run"):
            raise ActionError(demo_guard.why("run"), 429)
        run = self.run(a, rid)
        if run["status"] in ("running", "rendering"):
            raise ActionError("The agent is still working on this run. Try again in a moment.", 409)
        if not (run["result"] or {}).get("format"):
            raise ActionError("This run didn't get far enough to revise. Start a new one.")
        learned = self.remember(a, correction)
        steps, res, inp = run["steps"], run["result"], run["input"]
        before = [m["url"] for m in res.get("media", [])]
        res.setdefault("revisions", []).append({"correction": correction, "rules": [r["rule"] for r in learned["added"]],
                                                "before": before, "at": _now()})
        res["last_revision"] = correction
        for k in ("render", "caption", "check", "ready"):
            self._step(steps, k, "pending", "")
        self._save(rid, steps, res, "running")
        eng = llm.resolve(self.store, a.uid, self.s)

        def go():
            try:
                self._render_caption_check(a, rid, steps, inp, res, eng, revision=correction)
                r2 = self.run(a, rid)
                r2["result"]["revisions"][-1]["after"] = [m["url"] for m in r2["result"].get("media", [])]
                self._save(rid, r2["steps"], r2["result"])
            except ActionError as e:
                self._step(steps, "render", "failed", str(e))
                self._save(rid, steps, res, "failed")
        self._spawn_run(rid, go)
        out = self.run(a, rid)
        out["learned"] = learned["added"]
        out["message"] = learned.get("message", "")
        return out

    def update_package(self, a: Actor, rid: int, channel_id: str, text: str) -> dict:
        run = self.run(a, rid)
        res = run["result"]
        for pk in res.get("packages", []):
            if pk["channel_id"] == channel_id:
                main = next((m for m in res["media"] if m["type"] == "video"), None) or (res["media"][0] if res["media"] else None)
                before = pk.get("text") or ""
                pk["text"] = text
                pk["length"] = drafter.count(pk["service"], text)
                if channel_id in (res.get("captions") or {}):  # agent runs: the edit survives the next check
                    res["captions"][channel_id]["text"] = text
                rules = self.rules(a)
                pk["check"] = brand.brand_check(text, self.media_meta(a, main["token"]) if main else None,
                                                brand.for_channel(rules, pk["service"]), self.kit(a))
                pk["applied_rules"] = self.ledger.applied(rules, pk["service"])
                from .ledger import evidence as _evidence
                pk["evidence"] = _evidence(res.get("story"), res.get("source") or res.get("article"))
                self._save(rid, run["steps"], res)
                # Style Ledger v2: a person's edit is a signal; the same kind of edit in enough posts becomes a suggestion
                new = self.ledger.record_edit(a, rid, channel_id, pk["service"], before, text) if a.kind == "human" else []
                return {**pk, "suggestions": new}
        raise ActionError("No package for that channel in this run.", 404)

    def schedule_package(self, a: Actor, rid: int, channel_id: str, mode: str = "queue", due_at: str | None = None,
                         override_warnings: bool = False) -> dict:
        """The hand-off: a ready package goes through the same preview -> confirm -> submit path as every post, so an
        agent token's policy (approval inbox, drafts only) still holds."""
        run = self.run(a, rid)
        pk = next((p for p in (run["result"] or {}).get("packages", []) if p["channel_id"] == channel_id), None)
        if not pk:
            raise ActionError("No package for that channel in this run.", 404)
        if pk.get("status") in ("sent_to_buffer", "pending_approval"):
            raise ActionError("That package was already sent.", 409)
        if pk.get("media_missing"):
            raise ActionError(self.MISSING_MEDIA_NOTE, 409)
        if mode == "draft" and (pk.get("buffer_draft") or {}).get("buffer_post_id"):
            return self.update_package_draft(a, rid, channel_id)  # its Buffer draft takes the new media (never a 2nd draft)
        prev = self.studio.preview_post(a, {"channel_id": channel_id, "text": pk["text"], "media_urls": pk.get("media_urls") or [],
                                            "mode": mode, "due_at": due_at, "options": pk.get("options") or {},
                                            "override_warnings": override_warnings})
        out = self.studio.submit_post(a, prev["confirm_token"])
        self.mark_package(a, rid, channel_id, out["status"], {"mode": mode, "due_at": prev["preview"].get("due_at"),
                                                               "buffer_post_id": (out.get("post") or {}).get("id"),
                                                               "approval_id": out.get("approval_id")})
        return {**out, "warnings": prev["warnings"]}

    def update_package_draft(self, a: Actor, rid: int, channel_id: str) -> dict:
        """"Changed since saved to Buffer · Update draft": the package's Buffer DRAFT gets the package's current media and
        caption (editPost). When that draft is gone from Buffer (deleted there), a new draft is saved instead. A post that
        was scheduled meanwhile is never touched (replace_draft refuses anything but a draft)."""
        run = self.run(a, rid)
        pk = next((p for p in (run["result"] or {}).get("packages", []) if p["channel_id"] == channel_id), None)
        if not pk:
            raise ActionError("No package for that channel in this run.", 404)
        pid = (pk.get("buffer_draft") or {}).get("buffer_post_id")
        if not pid:
            raise ActionError("This post has no Buffer draft to update. Use Save draft to save one.", 409)
        if pk.get("media_missing"):
            raise ActionError(self.MISSING_MEDIA_NOTE, 409)
        body = {"channel_id": channel_id, "text": pk["text"], "media_urls": pk.get("media_urls") or [], "options": pk.get("options") or {}}
        try:
            out = self.studio.replace_draft(a, pid, body)
        except ActionError as e:
            if e.status != 404:
                raise
            for p in run["result"].get("packages", []):  # the draft was deleted in Buffer: a fresh draft, the same way as Save draft
                if p["channel_id"] == channel_id:
                    p.pop("buffer_draft", None)
            self._save(rid, run["steps"], run["result"])
            out = self.schedule_package(a, rid, channel_id, "draft")
            return {**out, "draft": "recreated"}
        run = self.run(a, rid)
        for p in run["result"].get("packages", []):
            if p["channel_id"] == channel_id:
                p["status"] = "sent_to_buffer"
                p["sent"] = {"mode": "draft", "buffer_post_id": pid, "key": p.get("idempotency_key"), "at": time.time(), "updated": True}
                p.pop("changed_since_sent", None)
                p.pop("buffer_draft", None)
        self._save(rid, run["steps"], run["result"])
        return {"status": "sent_to_buffer", "draft": "updated", "post": out.get("post"), "warnings": out.get("warnings") or []}

    def mark_package(self, a: Actor, rid: int, channel_id: str, status: str, info: dict) -> None:
        run = self.run(a, rid)
        for pk in run["result"].get("packages", []):
            if pk["channel_id"] == channel_id:
                pk["status"] = status
                pk["sent"] = info
        self._save(rid, run["steps"], run["result"])

    # ================================================================ calendar / Planner
    @staticmethod
    def _post_type(assets: list, text: str = "") -> str:
        if not assets:
            return "text"
        kinds = [str((x or {}).get("type") or (x or {}).get("mimeType") or "") for x in assets]
        if any("video" in k for k in kinds):
            return "video"
        return "carousel" if len(assets) > 1 else "image"

    def calendar(self, a: Actor) -> dict:
        """Everything the Planner shows: Buffer posts (scheduled, sent, drafts with or without a date), agent posts waiting
        for approval, and agent packages ready to post, plus each channel's Buffer posting slots. One shape for every item:
        ref (what to act on), state (scheduled | sent | approval | ready | draft), when (ISO UTC or ''), draggable."""
        items = []
        u = self.store.user(a.uid)
        chans = {c["id"]: c for c in self.studio.channels(a)}
        try:  # one Buffer request for all four statuses, not four
            self.studio.buffer.prefetch_posts(a.uid, u["org_id"], ("scheduled", "sent", "draft", "error"))
        except Exception:  # noqa: BLE001
            pass
        for status in ("scheduled", "sent", "draft", "error"):
            try:
                posts = self.studio.buffer.posts(a.uid, u["org_id"], [status], first=60)
            except Exception:
                posts = []
            for p in posts:
                ch = chans.get(p.get("channelId"), {})
                assets = p.get("assets") or []
                first = (assets[0] or {}) if assets else {}
                thumb = first.get("thumbnail") or ("" if "video" in str(first.get("type", "")) else first.get("source", "")) or ""
                items.append({"kind": "buffer", "status": status, "state": "failed" if status == "error" else status, "ref": f"buffer:{p.get('id')}",
                              "error": ((p.get("error") or {}).get("message") or "") if status == "error" else "",
                              "when": p.get("dueAt") or p.get("sentAt") or "", "text": (p.get("text") or "")[:280],
                              "service": ch.get("service", ""), "channel": ch.get("displayName") or ch.get("name") or "",
                              "channel_id": p.get("channelId", ""), "id": p.get("id"), "thumb": thumb,
                              "type": self._post_type(assets), "draggable": status != "sent",
                              "link": p.get("externalLink") or ""})
        for ap in self.studio.approvals(a, "pending", "post") if a.kind == "human" else []:
            pl = ap["payload"]
            ch = chans.get(pl.get("channel_id"), {})
            media = [m for m in ([pl.get("media_url")] + (pl.get("media_urls") or [])) if m]
            items.append({"kind": "approval", "status": "needs approval", "state": "approval", "ref": f"approval:{ap['id']}",
                          "when": pl.get("due_at") or "", "text": pl["text"][:280], "service": ch.get("service", ""),
                          "channel": ch.get("displayName") or ch.get("name", ""), "channel_id": pl.get("channel_id", ""),
                          "id": ap["id"], "agent": ap["agent_name"], "thumb": media[0] if media else "",
                          "type": "video" if media and media[0].lower().split("?")[0].endswith((".mp4", ".mov", ".webm")) else
                          ("carousel" if len(set(media)) > 1 else "image" if media else "text"), "draggable": True})
        for r in self.store.q("SELECT id,result,updated FROM agent_runs WHERE user_id=? AND status='ready' ORDER BY id DESC LIMIT 20", (a.uid,)):
            try:
                res = json.loads(r["result"] or "{}")
            except ValueError:
                continue
            try:
                mstate = self.media_state(res.get("packages") or [])
            except Exception:  # noqa: BLE001
                mstate = {}
            for pi, pk in enumerate(res.get("packages") or []):
                # Packages come from three makers (the agent, Make a post, an MCP client's create_package) and not all of
                # them carry every field: one client-made package without channel_name made /api/calendar a 500, so the
                # Planner showed nothing at all (owner, 2 Nov 2026). Read every field defensively; skip what can't be shown.
                if not isinstance(pk, dict) or pk.get("status") != "ready" or not pk.get("channel_id"):
                    continue
                media = [str(m) for m in pk.get("media_urls") or [] if m]
                vid = any(m.lower().split("?")[0].endswith((".mp4", ".mov", ".webm")) for m in media)
                ms = mstate.get(pi) or {"state": "ok", "missing": [], "thumb": media[0] if media else ""}
                ch = chans.get(pk["channel_id"], {})
                items.append({"kind": "ready", "status": "ready to post", "state": "ready",
                              "ref": f"ready:{r['id']}:{pk['channel_id']}", "when": "", "text": str(pk.get("text") or "")[:280],
                              "service": pk.get("service") or ch.get("service", ""),
                              "channel": pk.get("channel_name") or pk.get("channel") or ch.get("displayName") or ch.get("name") or "",
                              "channel_id": pk["channel_id"], "run_id": r["id"], "id": f"{r['id']}:{pk['channel_id']}",
                              # the first slide that still exists: a deleted first slide never shows as a broken image
                              "thumb": "" if vid else ms["thumb"],
                              "type": "reel" if vid else ("carousel" if len(media) > 1 else "image" if media else "text"),
                              "media_state": ms["state"], "media_missing": len(ms["missing"]),
                              "error": self.MISSING_MEDIA_NOTE if ms["state"] == "missing" else "",
                              "pass": bool((pk.get("check") or {}).get("pass", True)) and ms["state"] == "ok", "draggable": True})
        try:
            slots = [{"channel_id": c["id"], "timezone": c.get("timezone") or "Europe/London",
                      "schedule": c.get("postingSchedule") or []}
                     for c in self.studio.buffer.posting_schedules(a.uid, u["org_id"]) if c.get("id") in chans]
        except Exception:
            slots = []
        return {"items": items, "slots": slots, "now": datetime.now(timezone.utc).isoformat()}

    # ================================================================ helpers
    def _own_file(self, url: str, limit: int) -> tuple[bytes | None, str] | None:
        """A URL on this server's own PUBLIC_URL host (/m/<token>, /sample/<file>, /static/<file>), read straight from the
        store / package instead of over the network. On Render a request from the service to its own public hostname
        never arrives (no hairpin), so every self-hosted import failed with "Couldn't download that file" (owner bug,
        2 Nov 2026). None when the URL is not ours. Same files the public routes serve, same path checks."""
        p = urlparse(url)
        own = urlparse(self.s.public_url or "")
        if not own.hostname or (p.hostname or "").lower().rstrip(".") != own.hostname.lower() or (p.port or None) != (own.port or None):
            return None
        path = p.path or "/"
        data = None
        if path.startswith("/m/"):
            m = self.get_media(path[3:])
            data = m["data"] if m else None
        elif path.startswith("/sample/"):
            from .takes import SAMPLE_DIR
            f = (SAMPLE_DIR / path[8:]).resolve()
            if f.parent == SAMPLE_DIR.resolve() and f.is_file() and f.suffix in (".mp4", ".jpg"):
                data = f.read_bytes() if f.stat().st_size <= limit else b""
        elif path.startswith("/static/"):
            root = (Path(__file__).parent / "static").resolve()
            f = (root / path[8:]).resolve()
            if root in f.parents and f.is_file() and f.parent.name != "src":
                data = f.read_bytes() if f.stat().st_size <= limit else b""
        if data is None:
            return None, "not_found"
        if not data or len(data) > limit:
            return None, "too_large"
        return data, ""

    def _fetch_bytes_why(self, url: str, limit: int, https_only: bool = False) -> tuple[bytes | None, str]:
        """(bytes, "") or (None, why) for a public URL, every redirect hop checked. why: bad_url, not_found (HTTP 404/410),
        forbidden (401/403: private or signed-in only), http_<code>, too_large, private (netguard refused the host),
        unreachable (DNS/connect/timeout)."""
        if not url or not url.startswith(("http://", "https://")):
            return None, "bad_url"
        own = House._own_file(self, url, limit) if getattr(self, "s", None) is not None else None
        if own is not None:
            return own
        host = urlparse(url).hostname or "?"
        try:
            with netguard.safe_stream(url, http=self.http, headers=sources.UA, https_only=https_only) as r:
                if r.status_code >= 400:
                    why = {404: "not_found", 410: "not_found", 401: "forbidden", 403: "forbidden"}.get(r.status_code, f"http_{r.status_code}")
                    print(f"fetch: {host} answered HTTP {r.status_code}", flush=True)  # host only: a path can carry a token
                    return None, why
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        return None, "too_large"
                return bytes(buf), ""
        except netguard.FetchError as e:
            print(f"fetch: {host} refused: {e}", flush=True)
            if "must start with" in str(e):  # https_only: a redirect hop to http:// (or a non-http scheme)
                return None, "bad_url"
            return None, "private" if "private" in str(e) else "unreachable"
        except httpx.HTTPError as e:
            print(f"fetch: {host} failed: {e.__class__.__name__}", flush=True)
            return None, "unreachable"

    def _fetch_bytes(self, url: str, limit: int) -> bytes | None:
        """Bytes of a public URL (logo, story image, media import, preflight probe), every redirect hop checked. Why it
        failed is left in _FETCH_WHY.v for this thread (import_url says it plainly)."""
        data, _FETCH_WHY.v = House._fetch_bytes_why(self, url, limit)
        return data

    def _logo_bytes(self, kit: dict) -> bytes | None:
        if not kit.get("logo"):
            return None
        m = self.get_media(kit["logo"])
        return m["data"] if m else None
