"""Live check (opt-in, LIVE=1): a link with no video never becomes a Reel on the deployed studio.

    LIVE=1 python -m pytest tests/live/ep_live_check.py -q        # HQ_BASE defaults to the Render demo

It opens a demo session, starts an agent run from a link that has no video (HQ_NOVIDEO_URL, default a text-only essay)
for every channel the demo has, waits for it, then asserts:

* the run is ready and NO package is a Reel / video;
* the plan says "No video found, carousel only";
* any video-only channel the demo happens to have (YouTube, TikTok) is skipped with a note. When the demo has no such
  channel (it has none today) this part is simply not checked: the test never depends on a YouTube channel existing
  (owner bug 5, 2 Nov 2026: the old script failed because the demo has no YouTube channel).

Skipped unless LIVE=1, so CI never reaches the network. Spends no AI on our side; the demo run uses the server's own
budget rules.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="live check: set LIVE=1")

BASE = os.environ.get("HQ_BASE", "https://huhqueue-studio.onrender.com").rstrip("/")
NOVIDEO_URL = os.environ.get("HQ_NOVIDEO_URL", "https://www.paulgraham.com/greatwork.html")
VIDEO_ONLY = {"youtube", "tiktok"}
NO_VIDEO = "no video found, carousel only"


@pytest.fixture(scope="module")
def demo():
    import httpx
    c = httpx.Client(base_url=BASE, timeout=90, follow_redirects=True)
    r = c.get("/demo")
    assert r.status_code < 400, f"/demo answered {r.status_code}"
    sess = c.get("/api/session").json()
    c.headers["X-HQ-CSRF"] = sess.get("csrf", "")
    yield c
    c.close()


def _channels(c) -> list[dict]:
    got = c.get("/api/channels").json()
    got = got.get("channels", got) if isinstance(got, dict) else got
    return [x for x in got if isinstance(x, dict) and x.get("id")]


def _wait(c, rid: int, limit: float = 150) -> dict:
    t0, run = time.time(), {}
    while time.time() - t0 < limit:
        run = c.get(f"/api/agent/runs/{rid}").json()
        if run.get("status") in ("ready", "failed", "error"):
            return run
        time.sleep(5)
    return run


def test_no_video_link_gives_no_reel_and_says_carousel_only(demo):
    chans = _channels(demo)
    assert chans, "the demo has no channels"
    r = demo.post("/api/agent/runs", json={"prompt": f"make a post about this link {NOVIDEO_URL}",
                                           "channel_ids": [x["id"] for x in chans], "format": "auto"})
    assert r.status_code == 200, r.text[:300]
    rid = r.json()["id"]
    run = _wait(demo, rid)
    assert run.get("status") == "ready", f"run {rid} ended {run.get('status')}: {[s.get('detail') for s in run.get('steps') or []][-3:]}"
    res = run.get("result") or {}
    pk = res.get("packages") or []
    assert pk, "no packages"
    reels = [p for p in pk if str(p.get("format", "")).lower() in ("reel", "video", "short")]
    assert not reels, f"a no-video link made video packages: {[p.get('service') for p in reels]}"
    for p in pk:
        assert not any(str(u).lower().endswith((".mp4", ".mov", ".webm")) for u in p.get("media") or []), p.get("service")
    assert any(NO_VIDEO in str(x).lower() for x in res.get("plan") or []), res.get("plan")
    # video-only channels: checked only when the demo has one
    cm = res.get("channel_media") or {}
    for ch in chans:
        if ch.get("service") in VIDEO_ONLY:
            got = cm.get(ch["id"]) or {}
            assert got.get("format") == "skipped" and got.get("note"), (ch.get("service"), got)
            assert ch.get("service") not in [p.get("service") for p in pk]
