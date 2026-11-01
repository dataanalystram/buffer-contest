"""Live QA pass 2 polish, scripted, no live model calls:

1. the tone ("short punchy explainer") reaches the caption prompt as real instructions, per channel;
2. the brain/model attribution survives a reply (no "free starter" while Claude ran);
3. a caption is only called cut off when it really is (full text to the brain, max_tokens retried);
4. YouTube's description doesn't repeat the headline;
5. Activity names Make a post's calls "HuhQueue agent" (the Sources agent keeps its own label);
6. the carousel end card carries the N/N badge like the other slides;
7. the figure cross-check searches by the story (not the tweet's first sentence) and surfaces $1.3M vs $13 million,
   the person's source staying primary.
"""
import io
import json
from pathlib import Path

import httpx
from PIL import Image

from studio import activity, agent_tools, brand, factcheck, hbstyle, house, reader, render, sources

from .test_agent_core import TWEET, owner  # noqa: F401  (pytest fixture)

FIX = Path(__file__).parent / "fixtures"
QA_PROMPT = f"i want to post about this in all my channels {TWEET} make it a short punchy explainer"
FACTS = {"headline": "Subvocal raises $1.3M from YC and Afore Capital",
         "facts": ["Raised $1.3M from YC and Afore Capital", "Glasses read internal speech muscles", "No typing, no talking"],
         "why_it_matters": "Typing is slow and talking to a computer is awkward.", "label": "Launch"}
SERVICES = ("linkedin", "facebook", "youtube", "instagram")
FULL_RUN = [
    ("1. Read the post\n2. Facts\n3. Render\n4. Captions\n5. Check", [("read_url", {"url": TWEET})]),
    ("", [("extract_facts", {})]),
    ("", [("render_carousel", {"channel_ids": ["ch_li", "ch_fb", "ch_ig"]}), ("render_reel", {"channel_ids": ["ch_yt"]})]),
    ("", [("write_captions", {"tone": "short punchy explainer"})]),
    ("", [("check", {})]),
    ("", [("finish", {"message": "Ready."})]),
]
REPLY_RUN = [("1. Rewrite LinkedIn formally\n2. Check", [("write_captions", {"channel_ids": ["ch_li"], "tone": "formal"})]),
             ("", [("check", {})]), ("", [("finish", {"message": "LinkedIn is formal now."})])]


def _claude(owner, monkeypatch, captions=None, cut_first_caption=False):
    """Route api.anthropic.com to a scripted brain (seen['script'] can be swapped for a reply); record every request body."""
    from studio import brain_llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    brain_llm._models.update(at=0.0, plan="", fast="")
    seen = {"bodies": [], "turn": 0, "caption_calls": 0, "script": FULL_RUN}
    inner = owner.router
    caps = captions or {s: f"$1.3M to never type again.\n\nSubvocal's glasses read silent speech. {TWEET}" for s in SERVICES}

    def handler(req):
        if req.url.host != "api.anthropic.com":
            return inner(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-4-5-20251001", "created_at": "Wed 2025-10-01 1:00 AM IST (UTC+01:00)"},
                                                      {"id": "claude-sonnet-4-5-20250929", "created_at": "Mon 2025-09-29 1:00 AM IST (UTC+01:00)"}]})
        body = json.loads(req.content)
        seen["bodies"].append(body)
        usage = {"input_tokens": 500, "output_tokens": 100}
        if "tools" not in body:
            txt = body["messages"][0]["content"]
            if "networks" in txt:
                seen["caption_calls"] += 1
                if cut_first_caption and seen["caption_calls"] == 1:
                    return httpx.Response(200, json={"content": [{"type": "text", "text": '{"linkedin": "Subvocal has rai'}],
                                                     "usage": usage, "stop_reason": "max_tokens"})
                want = json.loads(txt)["networks"].keys()
                out = {k: v for k, v in caps.items() if k in want}
            else:
                out = FACTS
            return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(out)}], "usage": usage, "stop_reason": "end_turn"})
        script = seen["script"]
        text, calls = script[min(seen["turn"], len(script) - 1)]
        seen["turn"] += 1
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": f"t{len(seen['bodies'])}_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
        return httpx.Response(200, json={"content": content, "usage": usage, "stop_reason": "tool_use"})

    owner.app.state.house.http = httpx.Client(transport=httpx.MockTransport(handler))
    owner.app.state.studio.buffer.http = owner.app.state.house.http
    return seen


def _reply(owner, seen, rid, text):
    seen.update(script=REPLY_RUN, turn=0)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": text})
    return owner.get(f"/api/agent/runs/{rid}").json()


def _caption_requests(seen):
    return [json.loads(b["messages"][0]["content"]) for b in seen["bodies"]
            if "tools" not in b and "networks" in b["messages"][0]["content"]]


# ---------------------------------------------------------------- 1. tone
def test_tone_words_become_caption_instructions():
    assert agent_tools.tone_keys("short punchy explainer") == ["punchy", "explainer", "short"]
    ins = agent_tools.tone_instructions("short punchy explainer")
    assert "hook" in ins and "Short sentences" in ins and "plain words" in ins and "under 300" in ins
    assert agent_tools.tone_instructions("") == ""


def test_tone_reaches_the_caption_prompt(owner, monkeypatch):
    seen = _claude(owner, monkeypatch)
    r = owner.post("/api/agent/runs", json={"prompt": QA_PROMPT}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["status"] == "ready", run["steps"]
    req = _caption_requests(seen)[0]
    assert set(req["networks"]) == set(SERVICES)
    for svc, net in req["networks"].items():
        ins = net["tone_instructions"]
        assert "PUNCHY" in ins and "hook" in ins and "EXPLAINER" in ins and "SHORT" in ins, svc
        assert net["conventions"] == agent_tools.CHANNEL_STYLE[svc]
    assert "never repeat the headline" in req["networks"]["youtube"]["conventions"]
    assert "Link in bio" in req["networks"]["instagram"]["conventions"]
    assert "hook line" in req["format"]
    # a reply's tone ("LinkedIn more formal") replaces the prompt's tone for that channel only
    _reply(owner, seen, r["id"], "make LinkedIn more formal")
    last = _caption_requests(seen)[-1]
    assert list(last["networks"]) == ["linkedin"]
    assert "FORMAL" in last["networks"]["linkedin"]["tone_instructions"] and "PUNCHY" not in last["networks"]["linkedin"]["tone_instructions"]


def test_tone_reaches_the_free_writer_too(owner, monkeypatch):
    got = []
    from studio import drafter
    real = drafter.draft
    monkeypatch.setattr(drafter, "draft", lambda chans, article, **k: got.append(k.get("tone", "")) or real(chans, article, **k))
    owner.post("/api/agent/runs", json={"prompt": QA_PROMPT})  # no Claude key: the standard plan and the built-in writer
    assert got and all("PUNCHY" in t and "hook" in t for t in got)


# ---------------------------------------------------------------- 2 + 3. attribution and the false "cut off"
def test_attribution_survives_a_reply_and_captions_are_full(owner, monkeypatch):
    long_li = ("Subvocal has raised $1.3M from YC and Afore Capital.\n\nIts glasses aim to give you the speed of speech without a sound "
               "or a keyboard. Typing is slow, and talking into a computer is awkward.\n\nThe startup is pitching a hands-free "
               f"alternative to typing and speaking for computer input.\nRead more:\n{TWEET}")
    seen = _claude(owner, monkeypatch, captions={s: long_li for s in SERVICES}, cut_first_caption=True)
    r = owner.post("/api/agent/runs", json={"prompt": QA_PROMPT}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    br = run["result"]["brain"]
    assert br["mode"] == "tools" and br["model"].startswith("claude-sonnet") and br["fast_model"].startswith("claude-haiku")
    # the cut caption answer (stop_reason max_tokens) was retried with more room, never saved half-written
    assert seen["caption_calls"] == 2
    cap_bodies = [b for b in seen["bodies"] if "tools" not in b and "networks" in b["messages"][0]["content"]]
    assert cap_bodies[1]["max_tokens"] == 2 * cap_bodies[0]["max_tokens"] >= 4800
    assert run["result"]["captions"]["ch_li"]["text"].endswith(TWEET)
    # what the brain saw from write_captions: the full text and complete flags (the 160-char preview caused the false warning)
    step = next(s for s in run["steps"] if s["tool"] == "write_captions")
    assert step["data"]["captions"]["linkedin"].endswith(TWEET) and step["data"]["complete"]["linkedin"] is True
    assert all(b["max_tokens"] >= 2000 for b in seen["bodies"] if "tools" in b)
    # reply: the brain keeps its attribution, and its context marks shortened captions as previews, never as cut off
    run2 = _reply(owner, seen, r["id"], "LinkedIn more formal")
    br2 = run2["result"]["brain"]
    assert br2["mode"] == "tools" and br2["model"].startswith("claude-sonnet") and br2["fast_model"].startswith("claude-haiku")
    reply_body = next(b for b in seen["bodies"] if "tools" in b and "replied in the thread" in str(b["messages"][0]["content"]))
    ctx = reply_body["messages"][0]["content"]
    assert "[preview]" in ctx and '"complete": true' in ctx
    assert "Never tell the person a caption" in reply_body["system"][0]["text"]
    # the composer asks the same brain endpoint the run used
    b = owner.get("/api/agent/brain").json()["brain"]
    assert b["source"] == "huhqueue claude" and b["model"] == br2["model"]


def test_composer_label_uses_the_run_brain_not_the_caption_engine():
    js = (Path(__file__).parents[2] / "studio" / "static" / "house.js").read_text()
    assert '"/api/agent/brain"' in js and '"Writing with: " + brainName(br)' in js
    assert '"Writing with: " + H.ai.engine' not in js


def test_stop_reason_is_reported_for_both_api_styles(monkeypatch):
    from studio import brain_llm, budget
    monkeypatch.setattr(budget, "byo_check", lambda *a, **k: None)

    class St:
        def audit(self, *a, **k):
            pass

    def run(style, payload):
        http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
        base = "https://api.anthropic.com/v1" if style == "anthropic" else "https://api.openai.com/v1"
        eng = {"source": "your key", "provider": "anthropic" if style == "anthropic" else "openai", "base_url": base,
               "api_key": "k" * 20, "model": "m", "fast_model": "m", "style": style}
        return brain_llm.call(eng, St(), "u", "l", system="s", messages=[{"role": "user", "content": "x"}], http=http)

    assert run("anthropic", {"content": [{"type": "text", "text": "{"}], "usage": {}, "stop_reason": "max_tokens"})["stop_reason"] == "max_tokens"
    assert run("openai", {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}], "usage": {}})["stop_reason"] == "max_tokens"
    assert run("openai", {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}})["stop_reason"] == "stop"


# ---------------------------------------------------------------- 4. YouTube dedupe
def test_youtube_description_does_not_repeat_the_headline():
    title = FACTS["headline"]
    qa = (f"{title}\n\nSubvocal has raised $1.3M from YC and Afore Capital. Its glasses aim to give users the speed of speech.\n\n"
          f"Read more: {TWEET}")
    out = agent_tools.dedupe_youtube(qa, title)
    assert not out.startswith(title) and out.count("Afore Capital") == 1 and out.endswith(TWEET)
    fresh = f"Never type again? Subvocal's glasses read silent speech.\n\n{TWEET}"
    assert agent_tools.dedupe_youtube(fresh, title) == fresh
    assert agent_tools.dedupe_youtube(title, title) == title  # never empties a caption


def test_youtube_caption_in_a_run_is_deduped(owner, monkeypatch):
    title = FACTS["headline"]
    caps = {s: f"Never type again.\n\n{TWEET}" for s in SERVICES}
    caps["youtube"] = f"{title}\n\nSubvocal has raised $1.3M from YC and Afore Capital.\n\n{TWEET}"
    _claude(owner, monkeypatch, captions=caps)
    r = owner.post("/api/agent/runs", json={"prompt": QA_PROMPT}).json()
    res = owner.get(f"/api/agent/runs/{r['id']}").json()["result"]
    yt = res["captions"]["ch_yt"]["text"]
    assert not yt.startswith(title) and yt.count("Afore Capital") == 1
    pk = next(p for p in res["packages"] if p["service"] == "youtube")
    assert pk["options"]["title"] == title  # the title lives in its own field


# ---------------------------------------------------------------- 5. Activity label
def test_activity_labels_make_a_post_as_huhqueue_agent(owner, monkeypatch):
    _claude(owner, monkeypatch)
    owner.post("/api/agent/runs", json={"prompt": QA_PROMPT})
    apps = {r["app"] for r in owner.app.state.store.q("SELECT app FROM audit WHERE action='ai_call'")}
    assert apps == {"huhqueue-agent"}
    assert activity.APPS["huhqueue-agent"] == "HuhQueue agent" and activity.APPS["huhqueue"] == "HuhQueue Sources agent"
    tok = activity.begin(surface="ui")  # outside a Make a post run an app-side AI call keeps the Sources agent label
    try:
        activity.set_usage("Anthropic (HuhQueue)", "claude-haiku-4-5", 10, 5)
        assert activity.enrich("u", "human", "ai_call", "read", {})["app"] == "huhqueue"
    finally:
        activity.end(tok)


# ---------------------------------------------------------------- 6. end card badge
def test_carousel_end_card_has_the_page_badge():
    kit = dict(brand.DEFAULT_KIT, handle="huhbyte.ai", end_card={"on": True, "cta": "Follow for daily AI", "line": "AI news, explained simply."})
    p = brand.visual_params(kit, [])
    plain, _ = render.end_card(kit, dict(p), ratio="4:5")
    badged, meta = render.end_card(kit, dict(p), ratio="4:5", n=(4, 4))
    a = Image.open(io.BytesIO(plain)).convert("L")
    b = Image.open(io.BytesIO(badged)).convert("L")
    w = a.size[0]
    box = (40, 30, 260, 110) if p.get("logo_position", "tr") in ("tr", "br") else (w - 260, 30, w - 40, 110)
    diff = sum(1 for x, y in zip(a.crop(box).getdata(), b.crop(box).getdata()) if abs(x - y) > 30)
    assert diff > 200, "no N/N badge on the end card"
    assert not hbstyle.overflow(meta, (meta["width"], meta["height"]))


def test_run_carousel_end_card_is_numbered(owner, monkeypatch):
    calls = []
    real = render.end_card
    monkeypatch.setattr(render, "end_card", lambda *a, **k: calls.append(k.get("n")) or real(*a, **k))
    _claude(owner, monkeypatch)
    owner.post("/api/agent/runs", json={"prompt": QA_PROMPT})
    assert (4, 4) in calls  # cover 1/4, post 2/4, facts 3/4, end card 4/4


# ---------------------------------------------------------------- 7. cross-check
def test_factcheck_fixture_unit():
    fx = json.loads((FIX / "corroborate_subvocal.json").read_text())
    others = [{"source": b["source"], "url": b["url"], "text": b["title"] + " " + fx["articles"][b["url"]]["description"]} for b in fx["bing"]]
    out = factcheck.conflicts({"source": "X @AarushYadav47", "text": "We've raised $1.3M from YC & Afore Capital"}, others)
    assert len(out) == 1 and out[0]["ours"] == "$1.3M" and out[0]["theirs_source"] == "msn.com"
    assert not factcheck.conflicts({"source": "X", "text": "raised $1.3M"}, [o for o in others if o["source"] != "msn.com"])


def test_cross_check_searches_by_story_and_keeps_the_post_primary(owner, monkeypatch):
    fx = json.loads((FIX / "corroborate_subvocal.json").read_text())
    queries = []

    def bing(q, http=None, limit=10):
        queries.append(q)
        return fx["bing"] if "subvocal" in q.lower() else []  # the founder's first sentence never names the company
    real = REAL_CORROBORATE
    monkeypatch.setattr(house.House, "_corroborate", real)
    monkeypatch.setattr(sources, "bing_news", bing)
    monkeypatch.setattr(sources, "google_news", lambda q, http=None, limit=10: fx["google"])
    monkeypatch.setattr(reader, "read_article", lambda url, http=None: {"url": url, "title": "", **fx["articles"].get(url, {})})
    _claude(owner, monkeypatch)
    r = owner.post("/api/agent/runs", json={"prompt": QA_PROMPT}).json()
    res = owner.get(f"/api/agent/runs/{r['id']}").json()["result"]
    assert any("subvocal" in q.lower() for q in queries)
    assert {o["source"] for o in res["other_outlets"]} == {"msn.com", "techstartups.example"}
    c = res["conflicts"]
    assert len(c) == 1 and c[0]["ours"] == "$1.3M" and c[0]["ours_source"] == "X @AarushYadav47" and "13 million" in c[0]["theirs"]
    assert c[0]["question"].startswith("X @AarushYadav47 says $1.3M")
    assert all(p["status"] == "hold" for p in res["packages"])
    assert "$1.3M" in res["story"]["headline"]  # the original post's figure stays primary; nothing was swapped


REAL_CORROBORATE = house.House._corroborate  # captured at import, before the owner fixture stubs it
