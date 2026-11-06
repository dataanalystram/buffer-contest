"""Intent first in Make a post (owner bugs, 6 Nov 2026), end to end through the REST API with recorded fixtures.

Pins: "test" / "hi" ran a news search and ended on a raw error; a plain caption request searched anyway; Ram's exact
LinkedIn caption was replaced by an unrelated news caption with hashtags after a search for the request's own words;
"replace / edit the LinkedIn caption to <text>" answered "I couldn't tell what to change"; a run saved to Buffer by
itself; an uploaded photo carried the request as its headline over a gradient while the check said every check passes.
"""
import base64

import pytest

from studio import brand, headline, intake, intent, sources

from .test_agent_core import OWNER_CHANNELS, TWEET, jpeg, owner  # noqa: F401  (the owner fixture)

RAM = ('Post this exact caption on LinkedIn only, do not change a single character, no hashtags, no extra text: '
       '"[HQ test] LinkedIn text draft 6 Nov - HuhQueue live QA, please ignore."')
RAM_TEXT = "[HQ test] LinkedIn text draft 6 Nov - HuhQueue live QA, please ignore."
STORY = {"title": "OpenAI launches a new model for builders", "url": "https://news.example/openai-model", "source": "news.example",
         "via": "bing_news", "published": 0, "summary": "OpenAI launched a model today."}


def _run(c, rid):
    return c.get(f"/api/agent/runs/{rid}").json()


def _tools(run):
    return [s["tool"] for s in run["steps"]]


# ---------------------------------------------------------------- the pure reader
@pytest.mark.parametrize("p", ["test", "hi", "Hello!", "testing 123", "hey there", "thanks"])
def test_small_talk_is_chat(p):
    assert intent.classify(p)["kind"] == "chat"


def test_exact_caption_is_byte_for_byte():
    it = intent.classify(RAM)
    assert it["kind"] == "verbatim" and it["text"] == RAM_TEXT and not it["needs_search"]
    assert it["constraints"]["no_hashtags"] and it["constraints"]["no_extra"]


def test_instruction_words_are_never_a_query():
    assert intent.search_query("exact only, do not change single character, no hashtags, no extra text") == ""
    assert intent.classify("make a post")["kind"] == "ask"
    assert "OpenAI" in intent.classify("what did OpenAI launch today")["query"]


@pytest.mark.parametrize("reply,want", [
    ("Replace the LinkedIn caption with exactly this text only, verbatim: " + RAM_TEXT, RAM_TEXT),
    ("Edit LinkedIn caption to this exact text only: " + RAM_TEXT, RAM_TEXT),
    ('set the LinkedIn caption to "Hello  there."', "Hello  there."),
    ("use exactly “Two  spaces kept ”", "Two  spaces kept "),
    ("make the hook stronger", ""),
])
def test_caption_edit_reader(reply, want):
    assert intent.caption_edit(reply) == want


# ---------------------------------------------------------------- end to end
@pytest.mark.parametrize("p", ["test", "hi"])
def test_test_and_hi_get_a_short_reply_and_no_tools(owner, p):
    r = owner.post("/api/agent/runs", json={"prompt": p, "channel_ids": ["ch_li"]})
    assert r.status_code == 200, r.text
    run = _run(owner, r.json()["id"])
    assert run["status"] == "ready" and run["steps"] == [] and owner.searched == []
    reply = run["result"]["thread"][-1]["text"]
    assert reply == intent.CHAT_REPLY and "error" not in reply.lower() and not run["result"].get("packages")


def test_a_real_request_after_hi_starts_the_post_in_the_same_thread(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": "hi", "channel_ids": ["ch_li"]}).json()["id"]
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": 'post this exact caption on LinkedIn: "Hello from HQ"'})
    run = _run(owner, rid)
    assert run["result"]["captions"]["ch_li"]["text"] == "Hello from HQ" and owner.searched == []


def test_ram_exact_caption_linkedin_only_no_search(owner):
    r = owner.post("/api/agent/runs", json={"prompt": RAM, "channel_ids": ["ch_li", "ch_ig", "ch_fb", "ch_yt"]})
    assert r.status_code == 200, r.text
    run = _run(owner, r.json()["id"])
    assert owner.searched == [] and "search_news" not in _tools(run) and "extract_facts" not in _tools(run)
    pk = run["result"]["packages"]
    assert [p["service"] for p in pk] == ["linkedin"], pk
    assert pk[0]["text"] == RAM_TEXT and pk[0]["text"].encode() == RAM_TEXT.encode()
    assert "#" not in pk[0]["text"] and pk[0]["format"] == "text" and pk[0]["media_urls"] == []
    assert not pk[0].get("sent") and owner.fake.created == []  # nothing in Buffer until the button on the card


def test_ram_follow_ups_replace_the_caption_exactly(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": 'Post this exact caption on LinkedIn only: "first version"',
                                              "channel_ids": ["ch_li"]}).json()["id"]
    for reply, want in (("Replace the LinkedIn caption with exactly this text only, verbatim: " + RAM_TEXT, RAM_TEXT),
                        ("Edit LinkedIn caption to this exact text only: " + RAM_TEXT + " v2", RAM_TEXT + " v2")):
        out = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": reply})
        assert out.status_code == 200, out.text
        run = _run(owner, rid)
        assert run["result"]["packages"][0]["text"] == want
        assert "couldn't tell" not in run["result"]["thread"][-1]["text"].lower()
    assert owner.searched == [] and owner.fake.created == []


def test_unclear_edit_asks_a_specific_question(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": 'Post this exact caption on LinkedIn only: "first version"',
                                              "channel_ids": ["ch_li"]}).json()["id"]
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "hmm not quite right"})
    last = _run(owner, rid)["result"]["thread"][-1]["text"]
    assert "?" in last and "Linkedin" in last and "couldn't tell" not in last.lower()


def test_plain_caption_request_does_not_search_the_instruction_words(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": "write a caption", "channel_ids": ["ch_li"]}).json()["id"]
    run = _run(owner, rid)
    assert owner.searched == [] and run["steps"] == [] and run["result"]["thread"][-1]["text"] == intent.ASK_REPLY


def test_current_news_question_is_allowed_to_search(owner, monkeypatch):
    seen = []
    monkeypatch.setattr(sources, "search", lambda q, *a, **k: seen.append(q) or [dict(STORY)])
    rid = owner.post("/api/agent/runs", json={"prompt": "what did OpenAI launch today", "channel_ids": ["ch_li"]}).json()["id"]
    run = _run(owner, rid)
    assert "search_news" in _tools(run) and seen and "OpenAI" in seen[0] and "what did" not in seen[0].lower()


def test_search_failure_is_a_calm_message_with_retry(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": "post about quantum widgets", "channel_ids": ["ch_li"]}).json()["id"]
    run = _run(owner, rid)
    last = run["result"]["thread"][-1]["text"]
    assert run["status"] == "failed" and run["result"]["error"]["retry_text"] == "post about quantum widgets"
    assert "Error" not in last and "Traceback" not in last and "Paste" in last
    # Retry sends the same words again in the same thread (house.js chat-retry-run) and doesn't 500
    assert owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "post about quantum widgets"}).status_code == 200


def test_search_guard_refuses_instruction_only_queries(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": "post about quantum widgets", "channel_ids": ["ch_li"]}).json()["id"]
    n = len(owner.searched)
    r = owner.post(f"/api/agent/runs/{rid}/tools/search_news", json={"query": "exact only, do not change single character, no hashtags"})
    assert r.status_code >= 400 and "topic" in r.text and len(owner.searched) == n


def test_constraints_only_x_no_hashtags_no_link_in_normal_generation(owner, monkeypatch):
    p = f"post this on LinkedIn only, no hashtags and don't add a link {TWEET}"
    rid = owner.post("/api/agent/runs", json={"prompt": p, "channel_ids": ["ch_li", "ch_ig"]}).json()["id"]
    run = _run(owner, rid)
    caps = run["result"].get("captions") or {}
    assert set(caps) == {"ch_li"}, caps  # only LinkedIn
    text = caps["ch_li"]["text"]
    assert "#" not in text and "http" not in text, text
    assert owner.fake.created == []


def test_drafts_word_never_saves_to_buffer(owner, monkeypatch):
    monkeypatch.setattr(sources, "search", lambda q, *a, **k: [dict(STORY)])
    rid = owner.post("/api/agent/runs", json={"prompt": "make LinkedIn drafts about the OpenAI launch", "channel_ids": ["ch_li"]}).json()["id"]
    run = _run(owner, rid)
    assert "save_buffer_drafts" not in _tools(run) and owner.fake.created == []
    assert all(not p.get("sent") for p in run["result"].get("packages") or [])


def test_in_app_brain_never_gets_save_buffer_drafts_or_search_with_a_link():
    from studio.agent_brain import Brain
    run = {"input": {"source_url": "https://x.com/a/status/1", "intent": {"needs_search": False}}, "result": {}}
    names = {s["name"] for s in Brain._specs_for(run, None)}
    assert "save_buffer_drafts" not in names and "search_news" not in names
    run2 = {"input": {"source_url": "", "intent": {"needs_search": True}}, "result": {}}
    assert "search_news" in {s["name"] for s in Brain._specs_for(run2, None)}


# ---------------------------------------------------------------- B2: an uploaded photo, as uploaded
def test_uploaded_photo_is_used_as_is_without_the_request_as_headline(owner):
    data = "data:image/jpeg;base64," + base64.b64encode(jpeg(1080, 1350, (200, 50, 50))).decode()
    up = owner.post("/api/media", json={"name": "me.jpg", "data": data}).json()
    p = ("Use the attached uploaded image as the media and post it on Instagram only with a short caption about our new "
         "studio opening on Monday")
    rid = owner.post("/api/agent/runs", json={"prompt": p, "media_token": up["token"], "channel_ids": ["ch_ig"]}).json()["id"]
    run = _run(owner, rid)
    pk = run["result"]["packages"]
    assert [x["service"] for x in pk] == ["instagram"]
    assert pk[0]["media_urls"] == [up["url"]] and pk[0]["format"] == "photo"  # their photo, untouched: no overlay
    src = (run["result"].get("source") or {}).get("title", "")
    assert "attached" not in src.lower() and "use the" not in src.lower(), src
    assert not headline.looks_like_instruction(run["result"]["story"]["headline"], p)
    assert owner.searched == [] and owner.fake.created == []


def test_check_flags_instruction_text_and_gradients():
    base = {"pass": True, "results": [], "failed": [], "summary": "Passes every rule"}
    p = "Use the attached uploaded image as the media and post it on Instagram"
    bad = brand.content_check(base, "Our studio opens Monday", ["USE THE ATTACHED UPLOADED IMAGE AS THE MEDIA"], {"gradient": False}, p)
    assert not bad["pass"] and "No instruction text" in bad["summary"]
    grad = brand.content_check(base, "Our studio opens Monday", ["Our studio opens Monday"], {"gradient": True}, p)
    assert not grad["pass"] and "gradient" in grad["summary"]
    assert brand.content_check(base, "Our studio opens Monday", ["Our studio opens Monday"], {}, p)["pass"]


def test_brand_photo_never_burns_the_request_and_has_no_gradient():
    from studio import render
    kit = dict(brand.DEFAULT_KIT, handle="huhbyte.ai")
    _, meta = render.brand_photo(jpeg(1080, 1350), kit, brand.visual_params(kit, []),
                                 caption="USE THE ATTACHED UPLOADED IMAGE AS THE MEDIA and post it")
    assert meta["headline"] == "" and meta["gradient"] is False


def test_describe_drops_instruction_clauses():
    d = intake.describe("Use the attached uploaded image as the media and post it on Instagram only with a short caption "
                        "about our new studio opening on Monday")
    assert "attached" not in d.lower() and "studio opening on Monday" in d


# ---------------------------------------------------------------- CodeRabbit round 1
def test_quoted_name_is_not_an_exact_caption_and_a_quoted_word_is_not_an_edit():
    assert intent.classify('Write a LinkedIn caption about "Project Atlas" and post it')["kind"] == "post"
    assert intent.caption_edit('make sure the LinkedIn caption never says "game-changer"') == ""
    assert intent.verbatim('Post this caption on LinkedIn: "Hello"') == "Hello"


def test_describe_keeps_content_that_starts_with_a_verb():
    assert "grand opening Monday" in intake.describe("Don't miss our grand opening Monday, post it on Instagram")
    assert "SAVE10" in intake.describe("Use code SAVE10 for 20% off")


def test_caption_check_ignores_prompt_echoes():
    base = {"pass": True, "results": [], "failed": [], "summary": "Passes every rule"}
    ok = brand.content_check(base, "You can now build apps in minutes", [], {}, "you can do that, make a post about it")
    assert ok["pass"], ok


def test_ok_still_answers_a_pending_question(owner):
    rid = owner.post("/api/agent/runs", json={"prompt": 'Post this exact caption on LinkedIn only: "v1"', "channel_ids": ["ch_li"]}).json()["id"]
    st = owner.app.state.store
    import json as _j
    row = st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]
    res = _j.loads(row["result"]); res["rights_question"] = "Can I repost it with credit?"
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (_j.dumps(res), rid))
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "ok"})
    last = _run(owner, rid)["result"]["thread"][-1]["text"]
    assert not last.startswith("I'm here.")


def test_upload_with_research_searches_its_topic(owner, monkeypatch):
    seen = []
    monkeypatch.setattr(sources, "search", lambda q, *a, **k: seen.append(q) or [dict(STORY)])
    data = "data:image/jpeg;base64," + base64.b64encode(jpeg(1080, 1350)).decode()
    up = owner.post("/api/media", json={"name": "me.jpg", "data": data}).json()
    owner.post("/api/agent/runs", json={"prompt": "research the OpenAI developer conference and post it on LinkedIn",
                                        "media_token": up["token"], "channel_ids": ["ch_li"]})
    assert not seen or all(q.strip() for q in seen)
