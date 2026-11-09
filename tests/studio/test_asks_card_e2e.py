"""The run view's "What you asked" card, the missing-channel tab, the hook / credit chips, the first request in the bubble,
the agent's face and the name row above the text (owner, 9 Nov 2026), in a real browser. Buffer is FakeBuffer and the
video render is mocked (the fixture of test_makepost_nov3_fixes_e2e)."""
from __future__ import annotations

import json

from .test_makepost_nov3_fixes_e2e import A, _ctx, _open, live  # noqa: F401  (live is a fixture)
from .test_agent_ux_nov3 import FACTS, GH

ASK = "a reel on instagram and facebook, and for linkedin a carousel"


def _seed(app):
    h = app.state.house
    rid = h.brain.start(A, {"prompt": f"{ASK} {GH}", "channel_ids": ["ch_li", "ch_fb"], "brain": "external"})["id"]
    run = h.run(A, rid)
    # the owner's Glasshand run: Instagram was asked for (a typo) but never joined the run, so it has no post
    run["input"]["channels"] = [c for c in run["input"]["channels"] if c["service"] != "instagram"]
    h.store.x("UPDATE agent_runs SET input=? WHERE id=?", (json.dumps(run["input"]), rid))
    res = run["result"]
    res["source"] = {"url": GH, "title": "vercel-labs/agent-desktop", "site": "github.com", "description": FACTS[0],
                     "paragraphs": FACTS, "image": "https://example.com/hero.jpg", "media": []}
    h._save(rid, run["steps"], res)
    h.brain.tool(A, rid, "extract_facts", {"headline": "Vercel's Agent Desktop lets AI agents use your computer", "facts": FACTS})
    h.brain.tool(A, rid, "render_carousel", {})
    h.brain.tool(A, rid, "write_captions", {"hook": "Your AI agent can now use your whole computer."})
    h.brain.tool(A, rid, "check", {})
    return rid


def test_asked_vs_made_card_and_missing_channel(chromium, live):  # noqa: F811
    rid = _seed(live["app"])
    asks = live["app"].state.house.run(A, rid)["asks"]
    assert asks["formats"] == {"instagram": "reel", "facebook": "reel", "linkedin": "carousel"}
    assert asks["hook"].rstrip(".") == "Your AI agent can now use your whole computer"  # the headline tidy drops an end period
    ctx, page, errors = _ctx(chromium, live["base"], 1280)
    try:
        _open(page, live["base"], rid)
        card = page.get_by_test_id("ask-card")
        card.wait_for()
        rows = {r.get_attribute("data-svc"): r.inner_text() for r in page.get_by_test_id("ask-row").all()}
        assert "not made yet" in rows["instagram"] and "made" in rows["linkedin"]
        assert "made as" in rows["facebook"]  # asked a Reel, it is a carousel: flagged, with a fix button
        assert page.get_by_test_id("ask-fix").count() == 1
        assert page.get_by_test_id("ask-missing-tab").count() == 1
        assert "Your AI agent can now use your whole computer" in page.get_by_test_id("ask-meta").inner_text()
        assert page.get_by_test_id("ask-hook").count() >= 1
        # the bubble shows what they asked first, the agent's name row sits above its text with the face
        assert ASK in page.locator("#mpUser .mp-bubble").inner_text()
        # the hook chip only fills the composer (nothing is sent)
        page.get_by_test_id("ask-hook").first.click()
        assert "word for word" in page.get_by_test_id("reply-input").input_value()
        # "Make it" sends a normal reply naming Instagram
        page.get_by_test_id("ask-make").click()
        page.wait_for_function("() => [...document.querySelectorAll('[data-testid=thread-msg]')].some(m => /Instagram too/.test(m.innerText))",
                               timeout=20000)
        assert not errors, errors
    finally:
        ctx.close()


def test_agent_name_row_sits_above_its_text(chromium, live):  # noqa: F811
    rid = _seed(live["app"])
    live["app"].state.house.brain.reply(A, rid, "thanks")
    ctx, page, errors = _ctx(chromium, live["base"], 1280)
    try:
        _open(page, live["base"], rid)
        msg = page.locator("#agentThread .msg.agent").first
        msg.wait_for()
        b, t = msg.locator("b").first.bounding_box(), msg.locator(".md").first.bounding_box()
        assert b["y"] + b["height"] <= t["y"] + 1  # the label never sits on the text's line (the overlap)
        assert msg.locator("img[src='/static/agent-avatar.webp']").count() == 1
        assert not errors, errors
    finally:
        ctx.close()
