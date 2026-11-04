"""Video links in One take and over MCP: the rights attestation, the consent record, the caps and the licence hint.

Owner ask, 4 Nov 2026: pasting a YouTube link in One take, or asking an AI app about a YouTube video, ended with "please
upload", while Make a post's agent fetched a YouTube video and rendered a Reel the same evening (run 61, job 66 on the
InstaBox worker). Both already share ONE fetch path: the video worker (InstaBox first, GitHub Actions as its fallback)
runs ``studio.media.download.download_video`` (yt-dlp with only that platform's extractor, mp4 at or under 1080p, no
cookies, vetted by mediasafe). What sent people to "upload" instead:

* an age-restricted video ("Sign in to confirm your age", job 65) was read as a bot block, so the message blamed the
  server and tried the Invidious route (whose instance answered 500);
* a platform download was capped at 300 MB (``HQ_MEDIA_MAX_BYTES``), not One take's 4 GB, and takes at 30 min;
* the One take form and the MCP tool both said "a direct link to the file, not a social media page".

Rights: before any link is fetched, a signed-in person confirms ``ATTESTATION`` (a checkbox in One take, or the approval
item an AI app's request puts in their Inbox). Each confirmation is a row in ``link_consents`` (who, the link, its hash,
when, which version of the text, the licence hint). An agent token can never write one: over MCP the request waits in
Inbox → Approvals and only ``Studio.decide`` (a human) approves it (studio/consent.py, the same gate as paid APIs).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from urllib.parse import urlparse

from .actions import ActionError, Actor

ATTESTATION_VERSION = "2026-11-04.1"
ATTESTATION = ("I own this video or have permission to use it (e.g. my own content, a licence, or Creative Commons); "
               "I'm responsible for rights.")
MAX_LINK_SECS = int(os.environ.get("HQ_MAX_LINK_SECS", str(3 * 3600)))  # 3 h: longer sources are refused before download
SOURCE_RETENTION_DAYS = 7  # a fetched source kept in the bucket is deleted after this (and when its take is purged)

SCHEMA = """
CREATE TABLE IF NOT EXISTS link_consents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, url TEXT, url_hash TEXT, attestation_version TEXT,
  attestation TEXT, licence TEXT, via TEXT, agent_id INTEGER, approval_id INTEGER, take_id INTEGER, created REAL);
CREATE INDEX IF NOT EXISTS link_consents_user ON link_consents(user_id, url_hash);
"""


def ensure(store) -> None:
    """The migration: SQLite as written; Postgres with SERIAL ids, DOUBLE PRECISION times and row-level security on."""
    if getattr(store, "_link_consents_ready", False):
        return
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
        store.x(stmt)
    if store.pg:
        store.x("ALTER TABLE link_consents ENABLE ROW LEVEL SECURITY")
    store._link_consents_ready = True


def url_hash(url: str) -> str:
    """One hash per video: youtu.be / watch / shorts links to the same video match (find.canonical)."""
    from .media.find import canonical
    return hashlib.sha256(canonical(str(url or "").strip()).encode()).hexdigest()


def record(store, a: Actor, url: str, *, licence: str = "", via: str = "app", approval_id: int | None = None,
           agent_id: int | None = None) -> int:
    """Write the consent record. Only for a signed-in human (the app's checkbox, or Studio.decide on an approval item)."""
    if getattr(a, "kind", "") != "human":
        raise ActionError("Only the signed-in person can confirm the rights to a video. An AI app asks them in the Inbox.", 403)
    ensure(store)
    return store.x("INSERT INTO link_consents(user_id,url,url_hash,attestation_version,attestation,licence,via,agent_id,"
                   "approval_id,take_id,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (a.uid, str(url)[:2000], url_hash(url), ATTESTATION_VERSION, ATTESTATION, (licence or "")[:120], via,
                    agent_id, approval_id, None, time.time()))


def attach(store, uid: str, consent_id: int, take_id: int) -> None:
    ensure(store)
    store.x("UPDATE link_consents SET take_id=? WHERE id=? AND user_id=?", (take_id, consent_id, uid))


def for_user(store, uid: str, limit: int = 50) -> list[dict]:
    ensure(store)
    return [dict(r) for r in store.q("SELECT id,url,attestation_version,licence,via,take_id,created FROM link_consents "
                                     "WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, int(limit)))]


def human_attest(store, a: Actor, url: str, body: dict, licence: str = "") -> int:
    """The One take form (or REST): the person ticked the attestation. 403 without it; 409 when the text they saw is old."""
    if body.get("rights_attested") is not True:
        raise ActionError("Confirm the rights first: tick “" + ATTESTATION + "” (rights_attested: true). Nothing was fetched.", 403)
    ver = body.get("attestation_version")
    if ver not in (None, "", ATTESTATION_VERSION):
        raise ActionError("The rights statement changed since this page loaded. Reload, read it and tick it again.", 409)
    return record(store, a, url, licence=licence, via="app")


def licence_hint(url: str, http=None) -> str:
    """A licence the platform states, read cheaply before the fetch: YouTube's Creative Commons flag through the Data API
    (only when the operator set YOUTUBE_API_KEY). '' = unknown; the worker then reports what yt-dlp saw."""
    from .media.find import platform_of, youtube_id
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if platform_of(url) != "youtube" or not key:
        return ""
    vid = youtube_id(url) or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        return ""
    from . import netguard
    try:
        r = netguard.safe_get(f"https://www.googleapis.com/youtube/v3/videos?part=status&id={vid}&key={key}", http=http,
                              https_only=True, timeout=10, limit=200_000)
        items = ((r.json() or {}).get("items") or []) if r.status_code == 200 else []
    except Exception:  # noqa: BLE001 - a hint never blocks the take
        return ""
    lic = ((items[0].get("status") or {}).get("license") if items else "") or ""
    return {"creativeCommon": "Creative Commons (CC BY)", "youtube": "Standard YouTube licence"}.get(lic, "")


def licence_label(raw: str) -> str:
    """yt-dlp's ``license`` field as a short hint ('' when the platform says nothing)."""
    return " ".join(str(raw or "").split())[:120]


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
