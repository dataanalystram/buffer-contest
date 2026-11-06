"""Make a post composer fixes found in the live check of #137 (owner, 6 Nov 2026), plus CodeRabbit's deferred #137 items.

Pins: a short reply to "test" drew an empty "HuhQueue agent · Standard plan" card and a "Try again with a different angle"
chip; the Inbox's top-right "Make a post" kept the previous story in the composer; a text-only post's review chip said
"Image · Ready"; "without an overlay" counted as asking for an overlay; a late regenerate answer redrew an old run over a
new post; channel words inside a quoted exact caption picked channels; an exact caption with an upload and no link."""
import base64
import re
from pathlib import Path

import pytest

from studio import agent_tools

from .test_agent_core import jpeg, owner  # noqa: F401  (the owner fixture)

STATIC = Path(__file__).resolve().parents[2] / "studio" / "static"


def _js(name):
    return (STATIC / name).read_text(encoding="utf-8")


def _run(c, rid):
    return c.get(f"/api/agent/runs/{rid}").json()


# ---------------------------------------------------------------- (1) a short reply has no plan card, no retry chips
def test_short_reply_hides_the_plan_card_and_retry_chips():
    hs = _js("house.js")
    assert "chatOnly" in hs  # agentExtras / replyChips know a chat-only reply
    body = hs[hs.index("function replyChips"):hs.index("function md(")]
    assert re.search(r"chatOnly\(r\)\)\s*return \[\]", body), body[:400]
    ex = hs[hs.index("function agentExtras"):]
    assert "mp-agent-msg" in ex[:1500]  # the agent card is hidden for a chat-only reply


def test_test_prompt_is_a_chat_only_run(owner):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": "test", "channel_ids": ["ch_li"]}).json()
    res = r["result"]
    assert res["chat"]["kind"] == "chat" and not res.get("packages") and not res.get("plan") and not r["steps"]


# ---------------------------------------------------------------- (2) Inbox top-right Make a post: a clean new thread
def test_inbox_make_a_post_starts_a_new_thread():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<button[^>]*data-testid="inbox-make"[^>]*>', html).group(0)
    assert "data-new" in tag, tag


# ---------------------------------------------------------------- (3) the review chip reflects the real media
def test_text_only_post_label_is_text_only():
    hs = _js("house.js")
    ml = hs[hs.index("function mediaLabel"):hs.index("window.HQ_MEDIA_LABEL")]
    assert '"Text only"' in ml and "!m.length" in ml, ml


# ---------------------------------------------------------------- "without an overlay" is not an overlay request
@pytest.mark.parametrize("p,want", [
    ("Post my photo on Instagram without an overlay", False),
    ("no headline, no text on the image, just the photo", False),
    ("Don't add a title, post it as it is", False),
    ("post it with no overlay or branding", False),
    ("Add an overlay with the headline", True),
    ("make it a carousel", True),
    ("post it without text but use my brand kit", True),
])
def test_overlay_negation(p, want):
    assert agent_tools.overlay_asked(p) is want


def test_photo_without_an_overlay_is_posted_as_uploaded(owner):  # noqa: F811
    data = "data:image/jpeg;base64," + base64.b64encode(jpeg(1080, 1350, (40, 90, 200))).decode()
    up = owner.post("/api/media", json={"name": "me.jpg", "data": data}).json()
    rid = owner.post("/api/agent/runs", json={"prompt": "Post my photo on Instagram without an overlay, caption about our studio opening",
                                               "media_token": up["token"], "channel_ids": ["ch_ig"]}).json()["id"]
    pk = _run(owner, rid)["result"]["packages"]
    assert pk[0]["format"] == "photo" and pk[0]["media_urls"] == [up["url"]], pk[0]


# ---------------------------------------------------------------- exact captions: channel words inside the quote
def test_channel_words_inside_an_exact_caption_do_not_pick_channels(owner):  # noqa: F811
    cap = "Big news for our Instagram and Facebook fans"
    rid = owner.post("/api/agent/runs", json={"prompt": f'Post this exact caption on LinkedIn only: "{cap}"',
                                               "channel_ids": ["ch_li", "ch_ig", "ch_fb", "ch_yt"]}).json()["id"]
    pk = _run(owner, rid)["result"]["packages"]
    assert [p["service"] for p in pk] == ["linkedin"] and pk[0]["text"] == cap, pk
    assert owner.searched == [] and owner.fake.created == []


def test_exact_caption_with_an_upload_and_no_link(owner):  # noqa: F811
    data = "data:image/jpeg;base64," + base64.b64encode(jpeg(1080, 1080, (10, 120, 60))).decode()
    up = owner.post("/api/media", json={"name": "shop.jpg", "data": data}).json()
    cap = "Opening Monday. See you there!"
    rid = owner.post("/api/agent/runs", json={"prompt": f'Post this exact caption on Instagram: "{cap}"', "media_token": up["token"],
                                               "channel_ids": ["ch_ig"]}).json()["id"]
    run = _run(owner, rid)
    pk = run["result"]["packages"]
    assert [p["service"] for p in pk] == ["instagram"] and pk[0]["text"] == cap
    assert pk[0]["format"] == "photo" and pk[0]["media_urls"] == [up["url"]]
    assert owner.searched == [] and owner.fake.created == [] and run["status"] == "ready"


# ---------------------------------------------------------------- late answers never redraw an old run
def test_late_answers_are_guarded_by_the_open_run():
    mp = _js("makepost.js")
    regen = mp[mp.index('$("#mpRegen").onclick'):]
    regen = regen[:regen.index("};")]
    assert "onRun" in regen or "hqOnRun" in regen, regen
    hs = _js("house.js")
    assert "function showIfOn" in hs and "window.hqOnRun" in hs
    assert "showRun(await api(`/api/agent/runs/${r.id}" not in hs  # every in-thread action checks the run is still open
