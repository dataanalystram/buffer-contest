"""Try-it workspace: a sample Buffer account that needs no sign-in.

Demo users (ids start with "demo_") get an in-memory stand-in for Buffer, so visitors and judges can draft, approve and
"send" without a Buffer account. Nothing reaches Buffer. Everything else (drafting, validation, approvals, agent rules,
audit log) runs the real code paths.
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from .store import Store, sha

PREFIX = "demo_"
TTL = 86400  # demo workspaces are deleted a day after they were created

CHANNELS = [
    {"id": "demo_x", "name": "brightdesk", "displayName": "Bright Desk", "service": "twitter"},
    {"id": "demo_li", "name": "bright-desk", "displayName": "Bright Desk", "service": "linkedin"},
    {"id": "demo_ig", "name": "brightdesk.news", "displayName": "brightdesk.news", "service": "instagram"},
    {"id": "demo_bsky", "name": "brightdesk.bsky.social", "displayName": "Bright Desk", "service": "bluesky"},
    {"id": "demo_th", "name": "brightdesk", "displayName": "Bright Desk", "service": "threads"},
]
IMG = "https://picsum.photos/seed/{}/1200/800"
SEED_POSTS = [  # channel, hours from now (negative = already sent), text, image seed
    ("demo_li", 20, "Small teams ship faster when the review step is a habit, not a meeting. Here is how we run ours.", "desk"),
    ("demo_x", 26, "Three tools we stopped paying for this quarter, and what replaced them. https://example.com/stack", ""),
    ("demo_ig", 30, "Behind the scenes: how one link becomes five posts before lunch. Link in bio.", "studio"),
    ("demo_bsky", 44, "Reading list for the weekend: open-source schedulers, agent safety, and why limits matter.", ""),
    ("demo_x", -5, "Agents can draft. Humans approve. That's the whole policy.", "approve"),
]
SEED_FEEDS = ["https://www.theverge.com/rss/index.xml", "https://hnrss.org/frontpage"]
AGENT_POST = ("demo_x", "Claude", "AI agents can now draft your posts, but should they publish them? "
              "Our take: agents write, humans approve. Here's the setup we use. https://example.com/agents-approve")


def is_demo(uid: str | None) -> bool:
    return bool(uid) and uid.startswith(PREFIX)


def create_user(store: Store) -> str:
    """A fresh sample workspace with an agent post already waiting for approval."""
    cutoff = time.time() - TTL
    for r in store.q("SELECT id FROM users WHERE id LIKE ? AND created<?", (PREFIX + "%", cutoff)):
        store.delete_user(r["id"])
    uid = PREFIX + secrets.token_hex(8)
    store.upsert_user(uid, "", "Demo workspace", "demo_org", "Bright Desk (sample)",
                      {"access_token": "", "refresh_token": None, "scope": "", "expires_at": time.time() + TTL, "demo": True})
    now = time.time()
    aid = store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) "
                  "VALUES(?,?,?,?,?,?,?,?)", (uid, AGENT_POST[1], sha(secrets.token_urlsafe(32)), "review", 10, None, now, now + TTL))
    payload = {"channel_id": AGENT_POST[0], "text": AGENT_POST[2], "media_url": "", "mode": "queue", "due_at": None}
    store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
            (uid, aid, AGENT_POST[1], json.dumps(payload), "pending", now - 600))
    store.audit(uid, f"agent:{AGENT_POST[1]}", "submit_post", "held", {"channel": "twitter", "mode": "queue"})
    for url in SEED_FEEDS:
        store.x("INSERT INTO feeds(user_id,url,created) VALUES(?,?,?)", (uid, url, now))
    return uid


def _image(url: str) -> dict:
    return {"type": "image", "mimeType": "image/jpeg", "source": url, "thumbnail": url}


class DemoBuffer:
    """Same read/write surface Studio uses on BufferClient, kept in memory per demo user."""

    def __init__(self):
        self._posts: dict[str, list[dict]] = {}

    def _seed(self, uid: str) -> list[dict]:
        if uid not in self._posts:
            now = datetime.now(timezone.utc)
            self._posts[uid] = [{"id": f"dp{i}", "text": t, "status": "sent" if h < 0 else "scheduled", "channelId": ch,
                                 "dueAt": (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                 "assets": [_image(IMG.format(img))] if img else [], "externalLink": None}
                                for i, (ch, h, t, img) in enumerate(SEED_POSTS)]
        return self._posts[uid]

    def channels(self, uid: str, org_id: str = "", refresh: bool = False) -> list[dict]:
        return [dict(c, avatar="", isDisconnected=False, isQueuePaused=False) for c in CHANNELS]

    def posts(self, uid: str, org_id: str, statuses: list[str], first: int = 30) -> list[dict]:
        rows = [p for p in self._seed(uid) if p["status"] in statuses]
        return sorted(rows, key=lambda p: p.get("dueAt") or "")[:first]

    def create_post(self, uid: str, inp: dict) -> dict:
        posts = self._seed(uid)
        status = "draft" if inp.get("saveToDraft") else ("sent" if inp.get("mode") == "shareNow" else "scheduled")
        due = inp.get("dueAt")
        if not due and status == "scheduled":
            due = (datetime.now(timezone.utc) + timedelta(hours=2 + len(posts))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        assets = [_image(x["image"]["url"]) if "image" in x else {"type": "video", "mimeType": "video/mp4", "source": x["video"]["url"],
                  "thumbnail": ""} for x in inp.get("assets") or []]
        post = {"id": f"dp{len(posts)}", "text": inp["text"], "status": status, "dueAt": due, "channelId": inp["channelId"],
                "assets": assets, "externalLink": None}
        posts.append(post)
        return post

    def create_idea(self, uid: str, org_id: str, title: str, text: str) -> dict:
        return {"id": "demo_idea", "content": {"title": title, "text": text}}

    def invalidate(self, uid: str) -> None:
        self._posts.pop(uid, None)


class BufferRouter:
    """Sends demo users to DemoBuffer and everyone else to the real Buffer client."""

    def __init__(self, real, demo: DemoBuffer):
        self.real, self.demo = real, demo

    def __getattr__(self, name):
        return getattr(self.real, name)

    def _pick(self, uid):
        return self.demo if is_demo(uid) else self.real

    def channels(self, uid, *a, **k):
        return self._pick(uid).channels(uid, *a, **k)

    def posts(self, uid, *a, **k):
        return self._pick(uid).posts(uid, *a, **k)

    def create_post(self, uid, *a, **k):
        return self._pick(uid).create_post(uid, *a, **k)

    def create_idea(self, uid, *a, **k):
        return self._pick(uid).create_idea(uid, *a, **k)
