"""The free demo is view-only (owner, 2 Nov 2026).

* Every AI run is canned: the demo spends nothing (no model, Buffer, video worker, research or context.dev call). The
  spend kinds in demo_guard (research, ai, import, feed, video) are closed for demo users, so every run takes the
  bundled path that already exists for a used-up allowance: the sample story, the built-in writer, the sample brand,
  the sample Reel and the precomputed One take.
* Belt and braces: while a demo request runs (and in the background work it starts), every outbound HTTP call is
  refused in-process (OFFLINE below), so nothing can leave the server even if a code path forgot to check.
* Every write or destructive action (delete, wipe, send, schedule, connect, settings, uploads, tokens) answers
  "In the real app this would ... The demo doesn't change anything." and changes no data. Writes are denied by default:
  only the routes in ALLOW (dry runs, previews and the canned runs) go through.

Each "Try the demo" already gets its own demo_<id> user, so one visitor never sees or touches another's workspace.
HQ_DEMO_VIEW_ONLY=0 turns the old capped (spending) demo back on.
"""
from __future__ import annotations

import contextvars
import os
import re
import threading

import httpx

# ---------------------------------------------------------------------------------------------------- view-only rules
def view_only() -> bool:
    """On unless HQ_DEMO_VIEW_ONLY=0 (read on every call, so a test or an operator can switch it without a restart)."""
    return os.environ.get("HQ_DEMO_VIEW_ONLY", "1") != "0"


WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def message(would: str) -> str:
    return f"In the real app this would {would}. The demo doesn't change anything."


# Writes a demo session may make: dry runs, previews and the canned AI runs (they only touch the demo's own run rows).
ALLOW = [
    ("POST", r"/api/logout"),
    ("POST", r"/api/agent/runs"), ("POST", r"/api/agent/runs/\d+/(messages|revise|theme)"),
    ("POST", r"/api/agent/runs/\d+/tools/[a-z_]+"),  # minus BLOCKED_TOOLS (checked here and again inside the tools)
    ("POST", r"/api/takes/sample"),
    ("POST", r"/api/draft"), ("POST", r"/api/link/preview"), ("POST", r"/api/check"),
    ("POST", r"/api/posts/preview"), ("POST", r"/api/preflight"), ("POST", r"/api/preflight/fix"),
    ("POST", r"/api/brand/preview"), ("POST", r"/api/brand/themes-preview"), ("POST", r"/api/brand-kits/preview"),
    ("POST", r"/api/brand-kits/palette"), ("POST", r"/api/brand-studio/preview"),
    ("PUT", r"/api/onboarding"),  # the guided tour's progress in this demo session, nothing else
    # Looking at the sample brands: switch between the bundled kits / example brands (this visitor's own demo rows only),
    # the cached sample extraction (a draft, nothing saved; live sites stay off), and template previews (the bundled
    # example Reel: the video spend kind is closed, so nothing reaches the worker).
    ("POST", r"/api/brand-kits/[^/]+/activate"), ("POST", r"/api/brand-studio/examples/[^/]+"),
    ("POST", r"/api/brand/extract"), ("POST", r"/api/templates/render"),
    # The News radar's recorded sample scan (the demo's scan never fetches) and its dry-run packages; a real send
    # (dry_run false) is refused inside SourcesAgent.draft.
    ("POST", r"/api/sources/scan"), ("POST", r"/api/sources/draft"),
    # One take's The Spread: the dry-run plan on the Planner; placing it (confirm_token) is refused inside Takes.place.
    ("POST", r"/api/takes/\d+/place"),
]
# Agent tools that change settings or send: refused in the demo however they are reached (button, REST or the agent).
BLOCKED_TOOLS = {
    "save_buffer_drafts": "save these posts as drafts in your Buffer",
    "remember_rule": "save that rule to your Style Ledger",
    "add_end_card": "save that end card for every future video",
    "edit_brand": "change your brand kit",
}
# What each blocked write would have done, for the message. First match wins; anything unlisted gets the generic line.
WOULD = [
    (r"^/api/agent-tokens$", "POST", "create an agent token for Claude, ChatGPT or your own script"),
    (r"^/api/agent-tokens/", None, "change or revoke that agent token"),
    (r"^/api/agent/runs/\d+/packages/[^/]+/schedule$", None, "schedule this post in your Buffer queue"),
    (r"^/api/agent/runs/\d+/packages/", None, "save this change to the post"),
    (r"^/api/approvals/[^/]+/approve$", None, "approve this post and send it to Buffer"),
    (r"^/api/approvals/[^/]+/reject$", None, "reject this agent post"),
    (r"^/api/approvals/[^/]+/reschedule$", None, "move this post to a new time"),
    (r"^/api/ai/keys/", None, "save or remove your own AI key"),
    (r"^/api/ai(/engine)?$", None, "change your AI engine"),
    (r"^/api/brand/(from-website|onboard)$", None, "import your brand from your website"),
    (r"^/api/brand/samples/[^/]+/schedule$", None, "schedule this sample in your Buffer queue"),
    (r"^/api/brand-kits/[^/]+$", "DELETE", "delete this brand kit"),
    (r"^/api/brand", None, "save your brand kit"),
    (r"^/api/templates/slots", None, "schedule a template to post on a timetable"),
    (r"^/api/device-link$", None, "make a one-time link to open your account on another device"),
    (r"^/api/disconnect$", None, "disconnect Buffer and delete everything HuhQueue keeps for you"),
    (r"^/api/ideas$", None, "save this idea to your Buffer"),
    (r"^/api/integrations/[^/]+/test$", None, "test that integration's key"),
    (r"^/api/integrations/", None, "connect or change that integration"),
    (r"^/api/ledger", None, "change your Style Ledger"),
    (r"^/api/library/[^/]+$", "DELETE", "delete this file from your Library"),
    (r"^/api/library/", None, "add this file to your Library"),
    (r"^/api/(media|uploads)", None, "upload your file"),
    (r"^/api/posts/submit$", None, "send this post to Buffer"),
    (r"^/api/posts/[^/]+$", "DELETE", "delete this post from Buffer"),
    (r"^/api/posts/", None, "change this post in Buffer"),
    (r"^/api/prefs/", None, "save your appearance setting"),
    (r"^/api/radar/feeds", "DELETE", "unfollow this feed"),
    (r"^/api/radar/feeds", None, "follow this feed"),
    (r"^/api/safety/kill$", None, "stop every agent with the kill switch"),
    (r"^/api/send-codes$", None, "make a code to send a file from your phone"),
    (r"^/api/sources/refresh$", None, "scan your sources for new stories"),
    (r"^/api/sources/", None, "change your News sources"),
    (r"^/api/takes/\d+/clips/", None, "save your clip edit"),
    (r"^/api/takes$", None, "split your own long video into clips on the video worker"),
    (r"^/api/worker/", None, "hand this job to the video worker"),
    (r"^/(mcp|oauth/)", None, "let an AI app act on your account"),
    (r"^/(send/|u/)", None, "upload your file"),
]
_ALLOW = [(m, re.compile(p + r"$")) for m, p in ALLOW]
_WOULD = [(re.compile(p), m, w) for p, m, w in WOULD]
_TOOL = re.compile(r"/api/agent/runs/\d+/tools/([a-z_]+)$")


def blocked(method: str, path: str) -> str | None:
    """The view-only message for a demo write, or None when the write is allowed (a dry run or a canned run)."""
    if not view_only() or method not in WRITE_METHODS:
        return None
    m = _TOOL.match(path)
    if m and m.group(1) in BLOCKED_TOOLS:
        return message(BLOCKED_TOOLS[m.group(1)])
    if any(meth == method and rx.match(path) for meth, rx in _ALLOW):
        return None
    for rx, meth, would in _WOULD:
        if rx.search(path) and (meth is None or meth == method):
            return message(would)
    return message("save this change")


def tool_blocked(uid: str | None, tool: str) -> str | None:
    from .demo_guard import is_demo
    if view_only() and is_demo(uid) and tool in BLOCKED_TOOLS:
        return message(BLOCKED_TOOLS[tool])
    return None


# ---------------------------------------------------------------------------------------------------- no outbound calls
OFFLINE: contextvars.ContextVar[bool] = contextvars.ContextVar("hq_demo_offline", default=False)
_refused: list[str] = []  # the last refused URLs (no query string), for tests and the log
_lock = threading.Lock()


class DemoOffline(httpx.ConnectError):
    """Raised instead of an outbound HTTP call made for a demo request. A ConnectError, so every existing "the network
    failed" path (the sample story, the built-in writer, the sample Reel) handles it."""


def refused() -> list[str]:
    with _lock:
        return list(_refused)


def _refuse(url) -> None:
    u = str(url).split("?")[0][:200]
    with _lock:
        _refused.append(u)
        del _refused[:-200]


def install() -> None:
    """Patch httpx (sync and async), and requests when present, once: an outbound call made while OFFLINE is set raises
    instead of leaving the server. The demo's sample media and pages are all served locally."""
    if getattr(httpx.Client.send, "_hq_demo", False):
        return
    orig, aorig = httpx.Client.send, httpx.AsyncClient.send

    def send(self, request, *a, **k):
        if OFFLINE.get():
            _refuse(request.url)
            raise DemoOffline("The demo makes no outbound calls.", request=request)
        return orig(self, request, *a, **k)

    async def asend(self, request, *a, **k):
        if OFFLINE.get():
            _refuse(request.url)
            raise DemoOffline("The demo makes no outbound calls.", request=request)
        return await aorig(self, request, *a, **k)

    send._hq_demo = asend._hq_demo = True
    httpx.Client.send, httpx.AsyncClient.send = send, asend
    try:  # the Oracle SDK (Gemini on OCI) speaks requests, not httpx
        import requests
    except ImportError:
        return
    rorig = requests.Session.send

    def rsend(self, request, *a, **k):
        if OFFLINE.get():
            _refuse(request.url)
            raise requests.ConnectionError("The demo makes no outbound calls.")
        return rorig(self, request, *a, **k)
    requests.Session.send = rsend


def submit(pool, fn, *args):
    """pool.submit that carries this request's context (OFFLINE, the activity attribution) into the worker thread."""
    return pool.submit(contextvars.copy_context().run, fn, *args)
