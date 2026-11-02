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
    ("demo_li", -28, "What we learned shipping a scheduler in two weeks: cut scope early, test the boring paths.", "ship"),
]
# what the Inbox shows in the sample: one post Buffer failed to publish, one scheduled post preflight would block
SEED_FAILED = [("demo_ig", -2, "Five open models you can run on a laptop, ranked by speed.", "laptop",
                "Instagram rejected the media: the image's aspect ratio is outside 4:5 to 1.91:1.")]
SEED_LONG = ("demo_x", 52, "Sample: our full weekly roundup, written too long for X on purpose so preflight flags it. "
             "This week: open models got faster, agents learned to ask before they post, two schedulers shipped approval "
             "steps, and we finally wrote down the checklist we use every morning before anything goes out.", "")
SEED_DRAFTS = [  # channel, hours from now (None = no date yet), text, image seed
    ("demo_th", None, "Draft: a short thread on why every agent needs a kill switch.", ""),
    ("demo_ig", None, "Draft: carousel idea, five open models you can run on a laptop.", "laptop"),
    ("demo_li", 50, "Draft: our posting checklist, the one we actually use every morning.", ""),
]
# Buffer queue slots per channel, in the channel's timezone (what the Planner shows as "Posting times")
SLOTS = {"demo_x": ["09:00", "13:00", "18:30"], "demo_li": ["08:30", "12:00"], "demo_ig": ["11:00", "19:00"],
         "demo_bsky": ["10:00", "16:00"], "demo_th": ["12:30"]}
SEED_FEEDS = ["https://www.theverge.com/rss/index.xml", "https://hnrss.org/frontpage"]
AGENT_POST = ("demo_x", "Claude", "AI agents can now draft your posts, but should they publish them? "
              "Our take: agents write, humans approve. Here's the setup we use. https://example.com/agents-approve")


# What the agent researches in the sample once live research is used up (or for a link it may not open). Made up.
SAMPLE_ARTICLE = {
    "url": "https://example.com/sample/open-models-get-faster", "site": "Sample News", "image": "",
    "title": "Open models now run twice as fast on an ordinary laptop",
    "description": "A new release of an open-source inference library doubles local speed on the same hardware, with no change to accuracy.",
    "paragraphs": ["Sample story for the HuhQueue demo. The library's maintainers say the new release roughly doubles the speed of "
                   "open models on a standard laptop, with the same answers on their published benchmarks.",
                   "Teams that run small models locally can upgrade without changing their code, and the project says memory use "
                   "drops by about a third on long prompts.",
                   "The release is free and open source, and the maintainers expect hosted providers to adopt it within weeks."]}
SAMPLE_KIT = {"name": "Bright Desk", "website": "https://example.com", "handle": "@brightdesk", "voice": "Plain, warm, a little dry. Short sentences.",
              "colors": {"ground": "#101418", "accent": "#FF5A1F", "text": "#F4F1EA"}}
SAMPLE_STORIES = [  # News radar when the sample's feeds can't be read (offline, or a feed is down)
    {"title": "Sample: open models now run twice as fast on an ordinary laptop", "url": SAMPLE_ARTICLE["url"], "source": "Sample News",
     "summary": SAMPLE_ARTICLE["description"], "image": "", "published": 0},
    {"title": "Sample: agents that ask before they post are winning trust", "url": "https://example.com/sample/agents-ask-first",
     "source": "Sample News", "summary": "Teams report fewer mistakes when an agent drafts and a person approves.", "image": "", "published": 0},
    {"title": "Sample: the boring checklist behind every good post", "url": "https://example.com/sample/checklist",
     "source": "Sample News", "summary": "Length, links, alt text and a time that suits the audience.", "image": "", "published": 0},
]


# Sample Activity rows so every tab of the log has something to show. IPs are from the documentation range (203.0.113.0/24)
# and user agents are labelled as samples; nothing here came from a real person or client.
SAMPLE_UA = "Sample browser (Chrome on macOS)"
SAMPLE_ACTIVITY = [  # (minutes ago, actor, action, category, ok, detail, extra columns)
    (2880, "you", "connect_buffer", "signin", True, {"scopes": "sample"}, {"surface": "ui", "ip": "203.0.113.24", "ua": SAMPLE_UA}),
    (1500, "system", "signin_failed", "signin", False, {"via": "agent token", "reason": "revoked or expired", "token_label": "old laptop script"},
     {"surface": "rest", "ip": "203.0.113.77", "ua": "Sample script (python-httpx)"}),
    (1440, "you", "oauth_consent", "signin", True, {"client": "Claude", "redirect_host": "claude.ai"},
     {"surface": "ui", "ip": "203.0.113.24", "ua": SAMPLE_UA, "app": "claude"}),
    (720, "you", "device_signed_in", "signin", True, {}, {"surface": "ui", "ip": "203.0.113.51", "ua": "Sample browser (Safari on iPhone)"}),
    (240, "agent:Claude", "create_idea", "agent", True, {"idea_id": "sample1"},
     {"surface": "mcp", "app": "claude", "token_label": "Claude", "provider": "anthropic", "model": "claude-sonnet-4-5",
      "tokens_in": 1840, "tokens_out": 260, "cost_usd": 0.00942}),
    (200, "agent:ChatGPT", "get_posts", "agent", True, {"status": "scheduled"},
     {"surface": "mcp", "app": "chatgpt", "token_label": "ChatGPT", "provider": "openai", "model": "gpt-4.1-mini",
      "tokens_in": 950, "tokens_out": 120, "cost_usd": 0.000572}),
    (150, "agent:Cursor", "preflight", "agent", True, {"channel": "linkedin"}, {"surface": "mcp", "app": "cursor", "token_label": "my editor"}),
    (90, "you", "ai_call", "agent", True, {"task": "draft captions"},
     {"surface": "ui", "app": "huhqueue", "provider": "groq", "model": "llama-3.3-70b-versatile", "tokens_in": 1200, "tokens_out": 340,
      "cost_usd": 0.000977}),
    # one sample Make a post run: five model calls the Inbox folds into "HuhQueue agent made a post · 5 AI calls"
    *[(m, "you", "ai_call", "agent", True, {"for": f, "run": "sample-run"},
       {"surface": "ui", "app": "huhqueue-agent", "provider": "anthropic", "model": mdl, "tokens_in": ti, "tokens_out": to, "cost_usd": c})
      for m, f, mdl, ti, to, c in ((24, "research the story", "claude-sonnet-4-5", 6200, 540, 0.0267),
                                   (23, "check other outlets", "claude-haiku-4-5", 3100, 220, 0.0042),
                                   (22, "plan the post", "claude-sonnet-4-5", 4800, 610, 0.0236),
                                   (21, "write captions per channel", "claude-sonnet-4-5", 5400, 980, 0.0309),
                                   (20, "check against the Style Ledger", "claude-haiku-4-5", 2600, 180, 0.0035))],
    (60, "system", "buffer_token_refreshed", "system", True, {}, {"surface": "system"}),
    (45, "system", "buffer_api", "system", True, {"op": "query channels", "status": 200}, {"surface": "system"}),
    (44, "system", "buffer_api", "system", True, {"op": "query posts", "status": 200}, {"surface": "system"}),
    (30, "system", "buffer_api", "system", True, {"op": "mutation createIdea", "status": 200}, {"surface": "system"}),
    (12, "system", "buffer_api", "system", False, {"op": "mutation createPost", "status": 429}, {"surface": "system"}),
    (11, "system", "buffer_api", "system", True, {"op": "mutation createPost", "status": 200}, {"surface": "system"}),
]
ACTIVITY_COLS = ("surface", "app", "token_label", "provider", "model", "tokens_in", "tokens_out", "cost_usd", "ip", "ua")


def seed_activity(store: Store, uid: str, now: float) -> None:
    for mins, actor, action, cat, ok, detail, extra in SAMPLE_ACTIVITY:
        store.x(f"INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts,category,{','.join(ACTIVITY_COLS)}) "
                f"VALUES(?,?,?,?,?,?,?,?{',?' * len(ACTIVITY_COLS)})",
                (uid, actor, action, "sample", json.dumps({**detail, "sample": True}), 1 if ok else 0, now - mins * 60, cat,
                 *(extra.get(c, "" if c in ("surface", "app", "token_label", "provider", "model", "ip", "ua") else None)
                   for c in ACTIVITY_COLS)))


def is_sample_url(url: str | None) -> bool:
    return (url or "").strip().startswith("https://example.com/sample/")


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
    seed_activity(store, uid, now)
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
            n = len(self._posts[uid])
            self._posts[uid] += [{"id": f"dp{n + i}", "text": t, "status": "draft", "channelId": ch,
                                  "dueAt": (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if h is not None else None,
                                  "assets": [_image(IMG.format(img))] if img else [], "externalLink": None}
                                 for i, (ch, h, t, img) in enumerate(SEED_DRAFTS)]
            n = len(self._posts[uid])
            self._posts[uid] += [{"id": f"dp{n + i}", "text": t, "status": "error", "channelId": ch, "error": {"message": why},
                                  "dueAt": (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                  "assets": [_image(IMG.format(img))] if img else [], "externalLink": None}
                                 for i, (ch, h, t, img, why) in enumerate(SEED_FAILED)]
            ch, h, t, img = SEED_LONG
            self._posts[uid].append({"id": f"dp{len(self._posts[uid])}", "text": t, "status": "scheduled", "channelId": ch,
                                     "dueAt": (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "assets": [],
                                     "externalLink": None})
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
        post = {"id": f"dp{secrets.token_hex(3)}", "text": inp["text"], "status": status, "dueAt": due, "channelId": inp["channelId"],
                "assets": assets, "externalLink": None}
        posts.append(post)
        return post

    def edit_post(self, uid: str, inp: dict) -> dict:
        post = next((p for p in self._seed(uid) if p["id"] == inp["id"]), None)
        if not post:
            from .buffer_api import BufferError
            raise BufferError("Post not found", 404)
        if "text" in inp:
            post["text"] = inp["text"]
        if "dueAt" in inp:
            post["dueAt"] = inp["dueAt"]
        if inp.get("saveToDraft") is False and post["status"] == "draft":
            post["status"] = "scheduled"
        if post["status"] == "error" and "dueAt" in inp:  # a failed post given a new time goes back in the queue
            post["status"], post["error"] = "scheduled", None
        return {k: post[k] for k in ("id", "text", "status", "dueAt", "channelId")}

    def delete_post(self, uid: str, post_id: str) -> dict:
        posts = self._seed(uid)
        self._posts[uid] = [p for p in posts if p["id"] != post_id]
        return {"id": post_id}

    def posting_schedules(self, uid: str, org_id: str) -> list[dict]:
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        return [{"id": cid, "timezone": "Europe/Dublin", "postingSchedule": [{"day": d, "times": times, "paused": d == "sun"} for d in days]}
                for cid, times in SLOTS.items()]

    # a free-plan-sized sample, so the limit warnings are visible in the sample workspace
    def org_limits(self, uid: str, org_id: str) -> dict:
        return {"id": "demo_org", "channelCount": len(CHANNELS), "limits": {"scheduledPosts": 10, "channels": 6}}

    def daily_limits(self, uid: str, channel_ids: list[str], date: str | None = None) -> list[dict]:
        posts = self._seed(uid)
        # Like Buffer: count the posts within 24 hours of the intended time (now when no time is given), not every post
        # ever scheduled, so a sample X post three days out isn't refused because of today's posts.
        def _t(s):
            try:
                d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        at = _t(date) if date else None
        at = at or datetime.now(timezone.utc)
        out = []
        for cid in channel_ids:
            n = sum(p["channelId"] == cid and p["status"] == "scheduled" and
                    (lambda d: d is None or abs(d - at) < timedelta(hours=24))(_t(p.get("dueAt"))) for p in posts)
            limit = 3 if cid == "demo_x" else None
            out.append({"channelId": cid, "limit": limit, "scheduled": n, "sent": 0, "isAtLimit": bool(limit and n >= limit)})
        return out

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

    def prefetch_posts(self, uid, *a, **k):
        if not is_demo(uid):  # the sample's in-memory Buffer needs no prefetch (and must never reach the real one)
            return self.real.prefetch_posts(uid, *a, **k)

    def create_post(self, uid, *a, **k):
        return self._pick(uid).create_post(uid, *a, **k)

    def create_idea(self, uid, *a, **k):
        return self._pick(uid).create_idea(uid, *a, **k)

    def edit_post(self, uid, *a, **k):
        return self._pick(uid).edit_post(uid, *a, **k)

    def delete_post(self, uid, *a, **k):
        return self._pick(uid).delete_post(uid, *a, **k)

    def posting_schedules(self, uid, *a, **k):
        return self._pick(uid).posting_schedules(uid, *a, **k)

    def org_limits(self, uid, *a, **k):
        return self._pick(uid).org_limits(uid, *a, **k)

    def daily_limits(self, uid, *a, **k):
        return self._pick(uid).daily_limits(uid, *a, **k)
