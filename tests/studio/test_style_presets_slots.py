"""Style Ledger presets, template provenance and recurring slots end to end (owner, 6 Nov 2026).

A) Built-in presets: listed with every part (hooks, first line, length per network, on-screen text, CTA, hashtag caps with
   Instagram at most 5, banned phrases), applied as ordinary ledger rules (REST and MCP, MCP in two steps), re-applying never
   duplicates, a second preset switches the first one's rules off, edits before applying, and the free built-in drafter
   writes captions in the preset's shape (first line under 125 characters, one CTA, hashtags within the cap).
B) Provenance: every template and ledger rule says Built-in / Created via MCP by <app> · <model> / Created by you; MCP
   creates record the calling token's app and the model it reported; older rows are migrated.
C) Recurring slots: a weekly recap slot (Mon 20:00 Europe/Dublin) with the clock moved forward renders the recap from that
   week's posts and makes ONE Buffer draft per channel at the right UTC time, never twice (re-run, restart), across the
   daylight-saving change, stops when deleted, and the ticker survives a database error.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from studio import brand, brand_studio as BS, drafter, provenance, style_presets, templates as T

from .conftest import agent_token, mcp_call

UTC = timezone.utc


def _uid(c):
    return c.app.state.store.q("SELECT id FROM users")[0]["id"]  # one person per test app


# ------------------------------------------------------------------------------------------------ A) presets
def test_every_preset_has_every_part_and_instagram_never_more_than_five_hashtags():
    assert [p["id"] for p in style_presets.PRESETS] == ["hook_first", "educator", "build_in_public", "news_fast", "podcast_clip", "data_led",
                                                         "linkedin_thread", "genz_casual"]
    for p in style_presets.listing():
        for k in ("hook_formulas", "first_line", "structure", "on_screen", "cta", "length", "hashtags", "banned"):
            assert p[k], (p["id"], k)
        assert p["hashtags"]["instagram"] <= 5 and p["hashtags"]["twitter"] <= 2
        assert any("125" in t for t in p["first_line"]), p["id"]
        assert p["cta"]["line"] and p["cta"]["rules"]
        kinds = {r["check"]["type"] for r in p["rules"]}
        assert {"guideline", "banned_words", "max_hashtags", "max_length"} <= kinds
        ig = [r for r in p["rules"] if r["check"]["type"] == "max_hashtags" and r["scope"] == ["instagram"]]
        assert len(ig) == 1 and ig[0]["check"]["n"] <= 5


def test_apply_preset_writes_rules_once_and_a_second_preset_switches_the_first_off(human):
    c = human
    mine = c.post("/api/ledger", json={"correction": "never say synergy"}).json()
    assert mine["added"]
    lst = c.get("/api/ledger/presets").json()["presets"]
    assert len(lst) == 8 and all(p["provenance"]["label"] == "Built-in" for p in lst)
    r1 = c.post("/api/ledger/presets/hook_first/apply", json={}).json()
    n = r1["added"]
    assert n >= 20 and r1["replaced"] == 0
    again = c.post("/api/ledger/presets/hook_first/apply", json={}).json()
    assert again["added"] == n and again["replaced"] == n  # re-applying replaces, never duplicates
    ov = c.get("/api/ledger").json()
    hf = [r for r in ov["rules"] if (r["check"] or {}).get("preset") == "hook_first"]
    assert len(hf) == n and all(r["active"] and r["origin"] == "preset" for r in hf)
    assert all(r["provenance"]["label"] == "Built-in" for r in hf)
    assert any(r["group"] == "hooks" for r in hf) and any(g["id"] == "hooks" for g in ov["groups"])
    assert next(p for p in ov["presets"] if p["id"] == "hook_first")["active"] is True
    r2 = c.post("/api/ledger/presets/news_fast/apply", json={}).json()
    assert len([s for s in r2["switched_off"] if "Hook-first" in s["why"]]) == n
    ov = c.get("/api/ledger").json()
    assert not any(r["active"] for r in ov["rules"] if (r["check"] or {}).get("preset") == "hook_first")
    syn = next(r for r in ov["rules"] if r["rule"].lower().startswith("never use") and "synergy" in r["rule"].lower())
    assert syn["active"] and syn["provenance"]["label"] == "Created by you"  # the person's own rule is left alone
    assert c.post("/api/ledger/presets/nope/apply", json={}).status_code == 404
    assert c.post("/api/ledger/presets/hook_first/apply", json={"edits": ["x"]}).status_code == 400


def test_preset_edits_before_applying_and_guideline_wording_after(human):
    c = human
    rules = next(p for p in c.get("/api/ledger/presets").json()["presets"] if p["id"] == "educator")["rules"]
    ig = next(i for i, r in enumerate(rules) if r["check"]["type"] == "max_hashtags" and r["scope"] == ["instagram"])
    out = c.post("/api/ledger/presets/educator/apply", json={"edits": {"0": {"drop": True}, "1": {"text": "Open on the mistake"},
                                                                      str(ig): {"n": 12}}}).json()
    assert out["added"] == len(rules) - 1
    got = {r["rule"]: r for r in out["rules"]}
    assert "Open on the mistake" in got and rules[0]["rule"] not in got
    assert next(r for r in out["rules"] if r["check"]["type"] == "max_hashtags" and r["scope"] == ["instagram"])["check"]["n"] == 5
    g = next(r for r in out["rules"] if r["rule"] == "Open on the mistake")
    upd = c.patch(f"/api/ledger/rules/{g['id']}", json={"rule": "Open on the mistake people make most"}).json()
    assert upd["rule"] == "Open on the mistake people make most" and upd["check"]["type"] == "guideline"


def test_mcp_apply_preset_is_two_steps_and_records_the_app(human):
    tok = agent_token(human, policy="draft")
    _, lst = mcp_call(human, tok, "list_style_presets")
    assert {p["id"] for p in lst["presets"]} == set(style_presets.BY_ID)
    _, held = mcp_call(human, tok, "apply_style_preset", {"preset": "podcast_clip"})
    assert held["needs_confirmation"] and held["preview"]["rules"]
    assert not [r for r in human.get("/api/ledger").json()["rules"] if (r["check"] or {}).get("preset")]
    _, done = mcp_call(human, tok, "apply_style_preset", {"preset": "podcast_clip", "confirm_token": held["confirm_token"]})
    assert done["added"] == len(held["preview"]["rules"])
    r = next(r for r in human.get("/api/ledger").json()["rules"] if (r["check"] or {}).get("preset") == "podcast_clip")
    assert r["provenance"]["source"] == "builtin" and r["provenance"]["label"].startswith("Built-in · applied via MCP by ")


TOPICS = [
    {"url": "https://news.example.com/a", "title": "Google cuts Gemini API prices by 50% for developers", "site": "Example News",
     "description": "Google said on Tuesday it would halve the price of its Gemini API for most developers starting next month.",
     "paragraphs": ["The cut applies to the Flash and Pro tiers and takes effect on 1 December, the company said in a blog post.",
                    "Analysts say the move puts pressure on OpenAI and Anthropic, which charge more per million tokens."]},
    {"url": "https://news.example.com/b", "title": "Meta releases an open model that runs on a laptop", "site": "Tech Daily",
     "description": "The 8 billion parameter model runs offline on a 16 GB laptop and is free for commercial use.",
     "paragraphs": ["Meta says the model matches last year's larger models on reasoning tests while using a fraction of the memory.",
                    "Developers can download it today from Hugging Face under the company's community licence."]},
    {"url": "https://news.example.com/c", "title": "Startup ships an AI agent that books meetings", "site": "Founder Weekly",
     "description": "The two-person team says the agent booked 1,200 meetings in its first week of beta.",
     "paragraphs": ["\u201cWe built it in six weeks because our own calendars were a mess,\u201d the founder said in an interview.",
                    "The beta is free for teams of up to five people, with a paid plan planned for next year."]},
]


@pytest.mark.parametrize("pid", list(style_presets.BY_ID))
@pytest.mark.parametrize("ti", range(3))
def test_free_drafter_writes_in_the_preset_shape(pid, ti):
    art = TOPICS[ti]
    rules = [{**r, "id": i, "active": True} for i, r in enumerate(style_presets.rules_for(style_presets.BY_ID[pid]))]
    st = style_presets.style_from_rules(rules)
    assert st and st["preset"] == pid and st["cta"]
    chans = [{"id": f"c_{s}", "service": s} for s in ("instagram", "twitter", "linkedin")]
    before = {d["service"]: d["text"] for d in drafter.draft(chans, art)}
    after = {d["service"]: d["text"] for d in drafter.draft(chans, art, style=st)}
    for svc, text in after.items():
        scoped = brand.for_channel(rules, svc)
        fixed = drafter.fit(svc, brand.apply_text_rules(text, scoped), drafter.rule_limit(svc, scoped))
        chk = brand.brand_check(fixed, None, scoped, {})
        assert chk["pass"], (pid, svc, chk["summary"], fixed)
        first = fixed.split("\n")[0]
        assert 0 < len(first) <= 125, first
        assert st["cta"] in fixed, fixed  # the one CTA always survives trimming
        assert len(brand.HASHTAG_RE.findall(fixed)) <= st["hashtags"].get(svc, 5)
        assert fixed != before[svc]


# ------------------------------------------------------------------------------------------------ B) provenance
ASKED = {"user_asked": True, "reason": "the person asked for a new template"}
def test_template_provenance_built_in_you_and_mcp_with_and_without_a_model(human):
    c = human
    lst = c.get("/api/templates").json()["templates"]
    assert all(t["provenance"]["label"] == "Built-in" for t in lst if not t.get("custom"))
    body = {"name": "Funding note", "slides": [{"type": "front", "title": "{company} raised {amount}"}]}
    mine = c.post("/api/templates/custom", json=body).json()
    assert mine["provenance"] == {"source": "user", "client": "", "model": "", "label": "Created by you"}
    tok = agent_token(c, policy="draft")
    r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "create_template", "arguments": {**body, "name": "Launch note", **ASKED}, "_meta": {"model": "claude-opus-4-1"}}}).json()
    made = json.loads(r["result"]["content"][0]["text"])
    assert made["provenance"]["source"] == "mcp" and made["provenance"]["model"] == "claude-opus-4-1"
    assert made["provenance"]["label"] == f"Created via MCP by {made['provenance']['client']} · claude-opus-4-1"
    assert made["provenance"]["client"]  # the token's app (Claude), never blank
    _, plain = mcp_call(c, tok, "create_template", {**body, "name": "Hire note", **ASKED})
    assert plain["provenance"]["label"].endswith("(model unknown)")
    by = {t["name"]: t for t in c.get("/api/templates").json()["templates"]}
    assert by["Funding note"]["provenance"]["label"] == "Created by you"
    assert by["Launch note"]["provenance"]["label"].endswith("· claude-opus-4-1")


def test_rows_from_before_provenance_are_migrated(app, human):
    from studio import custom_templates, ledger
    store = app.state.store
    uid = _uid(human)
    now = time.time()
    store.x("INSERT INTO custom_templates(user_id,slug,name,data,created,updated,created_by) VALUES(?,?,?,?,?,?,?)",
            (uid, "old-agent", "Old agent", json.dumps({"name": "Old agent", "slides": [{"type": "front"}]}), now, now, "agent:7:Cursor"))
    store.x("INSERT INTO custom_templates(user_id,slug,name,data,created,updated,created_by) VALUES(?,?,?,?,?,?,?)",
            (uid, "old-mine", "Old mine", json.dumps({"name": "Old mine", "slides": [{"type": "front"}]}), now, now, "you"))
    store.x("INSERT INTO style_rules(user_id,kind,spec,rule,source,active,created,origin) VALUES(?,?,?,?,?,1,?,?)",
            (uid, "text", json.dumps({"type": "no_emoji"}), "No emojis", "no emojis", now, "agent"))
    custom_templates.ensure(store)
    ledger.ensure(store)
    by = {t["name"]: t for t in custom_templates.attach(app.state.house).list(type("A", (), {"uid": uid})())}
    assert by["Old agent"]["provenance"]["label"] == "Created via MCP by Cursor (model unknown)"
    assert by["Old mine"]["provenance"]["label"] == "Created by you"
    rule = next(r for r in human.get("/api/ledger").json()["rules"] if r["rule"] == "No emojis")
    assert rule["provenance"]["label"] == "Created via MCP by an AI app (model unknown)"


def test_provenance_labels():
    assert provenance.label("builtin") == "Built-in"
    assert provenance.label("mcp", "ChatGPT", "") == "Created via MCP by ChatGPT (model unknown)"
    assert provenance.label("mcp", "Claude<script>", "gpt-5") == "Created via MCP by Claudescript · gpt-5"
    assert provenance.label(None) == "Created by you"


# ------------------------------------------------------------------------------------------------ C) recurring slots
DUB = ZoneInfo("Europe/Dublin")


def _next_monday_20(min_days=8):
    """A Monday 20:00 in Dublin at least min_days from now: a real future time, so the test never goes stale."""
    d = datetime.now(DUB).replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(days=min_days)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d.astimezone(UTC)


def _last_sunday(year, month):
    d = datetime(year, month, 31 if month in (3, 10) else 30, 12, 0, tzinfo=UTC)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _posts(fake, occ):
    fake.posts = [
        {"id": "s1", "status": "sent", "channelId": "ch_x", "text": "OpenAI ships a faster model. It halves latency.",
         "sentAt": _iso(occ - timedelta(days=5)), "dueAt": _iso(occ - timedelta(days=5))},
        {"id": "s2", "status": "sent", "channelId": "ch_li", "text": "Meta opens its laptop model. Free for commercial use.",
         "sentAt": _iso(occ - timedelta(days=3)), "dueAt": _iso(occ - timedelta(days=3))},
        {"id": "old", "status": "sent", "channelId": "ch_x", "text": "Last month's story. Should not be in the recap.",
         "sentAt": _iso(occ - timedelta(days=40)), "dueAt": _iso(occ - timedelta(days=40))},
    ]


def _slot(c, **kw):
    body = {"template": "weekly_recap", "weekday": "mon", "time": "20:00", "tz": "Europe/Dublin", "channel_ids": ["ch_x", "ch_li"], **kw}
    r = c.post("/api/templates/slots", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _actor(human):
    from studio.actions import Actor
    return Actor(_uid(human))


def test_weekly_recap_slot_drafts_once_per_channel_at_the_right_utc_time(app, human, fake):
    c, store, bs = human, app.state.store, app.state.house.brand_studio
    occ = _next_monday_20()  # Monday 20:00 in Dublin: 20:00 UTC in winter, 19:00 UTC in summer
    _posts(fake, occ)
    s = _slot(c)
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (occ.timestamp(), s["id"]))
    assert BS.tick_once(bs, now=occ - timedelta(minutes=90)) == 0  # 90 min early: not yet
    assert fake.created == []
    assert BS.tick_once(bs, now=occ - timedelta(minutes=55)) == 1  # inside the hour before: drafts now
    assert len(fake.created) == 2 and {i["channelId"] for i in fake.created} == {"ch_x", "ch_li"}
    assert all(i["saveToDraft"] and i["dueAt"][:19] == occ.isoformat()[:19] for i in fake.created), [i["dueAt"] for i in fake.created]
    assert all(i["assets"] for i in fake.created)  # the rendered recap slides ride along
    row = dict(store.q("SELECT * FROM template_slots WHERE id=?", (s["id"],))[0])
    res = json.loads(row["last_result"])
    assert res["content"].startswith("2 stories from this week"), res["content"]
    assert datetime.fromtimestamp(row["next_fire"], UTC) == T.next_fire("mon", "20:00", "Europe/Dublin", occ + timedelta(minutes=1))
    # the same pass again, a restart that lost the next_fire update, and a "Run now" for that time: no second draft
    assert BS.tick_once(bs, now=occ - timedelta(minutes=50)) == 0
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (occ.timestamp(), s["id"]))
    BS.tick_once(bs, now=occ - timedelta(minutes=45))
    assert len(fake.created) == 2
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (occ.timestamp(), s["id"]))
    again = bs.run_slot(_actor(human), s["id"], now=occ - timedelta(minutes=40))
    assert {d["status"] for d in again["drafts"]} == {"already_drafted"} and len(fake.created) == 2
    # deleting the slot stops it (and its claims go with it)
    assert c.delete(f"/api/templates/slots/{s['id']}").json()["deleted"] == s["id"]
    assert BS.tick_once(bs, now=occ + timedelta(days=7, minutes=-30)) == 0 and len(fake.created) == 2
    assert not store.q("SELECT 1 FROM template_slot_runs WHERE slot_id=?", (s["id"],))


def test_two_tickers_at_once_still_make_one_draft_per_channel(app, human, fake):
    """During a deploy the old and the new instance both tick for a minute."""
    store, bs = app.state.store, app.state.house.brand_studio
    s = _slot(human)
    occ = _next_monday_20()
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (occ.timestamp(), s["id"]))
    now = occ - timedelta(minutes=30)
    ts = [threading.Thread(target=BS.tick_once, args=(bs, now)) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert len(fake.created) == 2 and sorted(i["channelId"] for i in fake.created) == ["ch_li", "ch_x"]


def test_slot_follows_dublin_daylight_saving():
    y = datetime.now(UTC).year + 1
    spring, autumn = _last_sunday(y, 3), _last_sunday(y, 10)
    before = T.next_fire("mon", "20:00", "Europe/Dublin", spring - timedelta(days=7))
    assert before == (spring - timedelta(days=6)).replace(hour=20, minute=0)  # still GMT: 20:00 UTC
    after = T.next_fire("mon", "20:00", "Europe/Dublin", before + timedelta(minutes=1))
    assert after == (spring + timedelta(days=1)).replace(hour=19, minute=0)  # Irish summer time (UTC+1): 19:00 UTC
    back = T.next_fire("mon", "20:00", "Europe/Dublin", autumn - timedelta(hours=1))
    assert back == (autumn + timedelta(days=1)).replace(hour=20, minute=0)  # back to GMT


def test_slot_across_the_spring_change_drafts_at_19_utc(app, human, fake):
    store, bs = app.state.store, app.state.house.brand_studio
    spring = _last_sunday(datetime.now(UTC).year + 1, 3)
    mon1 = (spring - timedelta(days=6)).replace(hour=20, minute=0)
    mon2 = (spring + timedelta(days=1)).replace(hour=19, minute=0)
    s = _slot(human, channel_ids=["ch_x"])
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (mon1.timestamp(), s["id"]))
    BS.tick_once(bs, now=mon1 - timedelta(minutes=30))
    nf = store.q("SELECT next_fire FROM template_slots WHERE id=?", (s["id"],))[0]["next_fire"]
    assert datetime.fromtimestamp(nf, UTC) == mon2
    BS.tick_once(bs, now=mon2 - timedelta(minutes=30))
    assert [i["dueAt"][:19] for i in fake.created] == [mon1.isoformat()[:19], mon2.isoformat()[:19]]


def test_a_late_fire_lands_ten_minutes_out_and_a_long_outage_is_skipped(app, human, fake):
    store, bs = app.state.store, app.state.house.brand_studio
    s = _slot(human, channel_ids=["ch_x"])
    occ = _next_monday_20()
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (occ.timestamp(), s["id"]))
    BS.tick_once(bs, now=occ + timedelta(minutes=30))  # the server woke half an hour late
    assert fake.created[-1]["dueAt"][:16] == (occ + timedelta(minutes=40)).isoformat()[:16]
    nxt = occ + timedelta(days=7)
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (nxt.timestamp(), s["id"]))
    BS.tick_once(bs, now=nxt + timedelta(hours=16))  # down all night: skipped, not a recap a day late
    assert len(fake.created) == 1
    row = dict(store.q("SELECT next_fire, last_result FROM template_slots WHERE id=?", (s["id"],))[0])
    assert json.loads(row["last_result"])["missed"] is True
    assert datetime.fromtimestamp(row["next_fire"], UTC) == T.next_fire("mon", "20:00", "Europe/Dublin", nxt + timedelta(minutes=1))


def test_the_ticker_survives_a_database_error(app, monkeypatch):
    bs = app.state.house.brand_studio
    calls = {"n": 0}

    def flaky(now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        return []
    monkeypatch.setattr(bs, "due_slots", flaky)
    monkeypatch.setenv("HQ_SLOT_TICK", "1")
    BS.stop_ticker()
    errors = BS.TICK["errors"]
    t = BS.start_ticker(bs, every=0.05)
    try:
        deadline = time.time() + 10
        while calls["n"] < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 3 and t.is_alive()  # it kept ticking after the error (it used to die for good)
        assert BS.TICK["errors"] > errors and BS.tick_snapshot()["on"] is True
    finally:
        BS.stop_ticker()


def test_add_slot_refuses_an_unknown_time_zone_and_healthz_shows_the_ticker(human):
    r = human.post("/api/templates/slots", json={"template": "weekly_recap", "weekday": "mon", "time": "20:00", "tz": "Mars/Base",
                                                 "channel_ids": ["ch_x"]})
    assert r.status_code == 400 and "time zone" in r.text
    h = human.get("/healthz").json()
    assert set(h["slots"]) >= {"on", "every_s", "lead_s", "last_tick_age_s", "ticks", "fired", "errors"}


def test_render_for_my_channels_and_reel_too(human, fake):
    _posts(fake, datetime.now(UTC))
    r = human.post("/api/templates/render", json={"template": "weekly_recap", "services": ["instagram", "linkedin", "twitter"],
                                                  "video": True}).json()
    assert [c["service"] for c in r["channels"]] == ["instagram", "linkedin", "twitter"]
    assert all(c["slides"] for c in r["channels"])
    assert r["content"].startswith("2 stories from this week"), r["content"]
    assert r["video"] and r["video"]["url"]  # the person's Reel, or the labelled example where no video worker runs
    if r["video"].get("example"):
        assert "EXAMPLE" in r["video"]["note"]


# ------------------------------------------------------------------------------------------------ C2) edit, repeat, time zone
def _local(iso, zone):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(zone))


def test_slot_repeats_weekly_on_its_day_and_local_time_for_a_year_in_dublin_and_new_york():
    """52 fires in a row, through both daylight-saving changes, always the slot's weekday and wall-clock time."""
    for wd, at, zone in (("wed", "08:30", "Europe/Dublin"), ("fri", "17:45", "America/New_York"), ("sun", "00:15", "Asia/Kolkata")):
        t = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
        seen = []
        for _ in range(60):
            t = T.next_fire(wd, at, zone, t + timedelta(minutes=1))
            loc = t.astimezone(ZoneInfo(zone))
            assert T.DAYS[loc.weekday()] == wd and loc.strftime("%H:%M") == at, (zone, loc)
            seen.append(t)
        gaps = {round((b - a).total_seconds() / 3600) for a, b in zip(seen, seen[1:])}
        assert gaps <= {167, 168, 169}, gaps  # a week, give or take the clock change
        if zone != "Asia/Kolkata":
            assert {167, 169} <= gaps  # both changes were crossed (Kolkata has none)


def test_edit_slot_moves_the_next_draft_and_switching_off_stops_it(app, human, fake):
    c, store, bs = human, app.state.store, app.state.house.brand_studio
    s = _slot(c)
    assert _local(s["next_fire_iso"], "Europe/Dublin").strftime("%a %H:%M") == "Mon 20:00"
    r = c.patch(f"/api/templates/slots/{s['id']}", json={"weekday": "wed", "time": "08:30"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["weekday"], e["at"], e["tz"], e["channels"]) == ("wed", "08:30", "Europe/Dublin", ["ch_x", "ch_li"])
    occ = datetime.fromisoformat(e["next_fire_iso"])
    assert _local(e["next_fire_iso"], "Europe/Dublin").strftime("%a %H:%M") == "Wed 08:30"
    assert occ > datetime.now(UTC)
    # the ticker drafts the NEW time: one Buffer draft per channel, due at Wednesday 08:30 Dublin
    assert BS.tick_once(bs, now=occ - timedelta(minutes=90)) == 0 and fake.created == []
    assert BS.tick_once(bs, now=occ - timedelta(minutes=55)) == 1
    assert len(fake.created) == 2 and all(i["saveToDraft"] and i["dueAt"][:19] == occ.isoformat()[:19] for i in fake.created)
    row = dict(store.q("SELECT next_fire FROM template_slots WHERE id=?", (s["id"],))[0])
    nxt = datetime.fromtimestamp(row["next_fire"], UTC)
    assert _local(nxt.isoformat(), "Europe/Dublin").strftime("%a %H:%M") == "Wed 08:30" and 6 <= (nxt - occ).days <= 7
    # channels and on/off
    r = c.patch(f"/api/templates/slots/{s['id']}", json={"channel_ids": ["ch_li"], "active": False}).json()
    assert r["channels"] == ["ch_li"] and r["active"] == 0
    assert BS.tick_once(bs, now=datetime.fromisoformat(r["next_fire_iso"]) - timedelta(minutes=30)) == 0 and len(fake.created) == 2
    r = c.patch(f"/api/templates/slots/{s['id']}", json={"active": True}).json()
    assert r["active"] == 1
    nf = datetime.fromisoformat(r["next_fire_iso"])
    assert nf == occ  # switched back on before that Wednesday: the same occurrence, already drafted for ch_li
    BS.tick_once(bs, now=nf - timedelta(minutes=30))
    assert len(fake.created) == 2  # never a second draft for one occurrence and channel, even after an edit
    # the week after, it drafts again (ch_li only now)
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (nxt.timestamp(), s["id"]))
    assert BS.tick_once(bs, now=nxt - timedelta(minutes=30)) == 1
    assert len(fake.created) == 3 and fake.created[-1]["channelId"] == "ch_li"
    assert fake.created[-1]["dueAt"][:19] == nxt.isoformat()[:19]


def test_slot_in_the_accounts_time_zone_new_york(app, human, fake):
    s = _slot(human, weekday="fri", time="09:00", tz="America/New_York")
    loc = _local(s["next_fire_iso"], "America/New_York")
    assert loc.strftime("%a %H:%M") == "Fri 09:00"
    utc = datetime.fromisoformat(s["next_fire_iso"]).astimezone(UTC)
    assert utc.hour in (13, 14) and utc.hour == int(9 - loc.utcoffset().total_seconds() / 3600)  # EDT 13:00 UTC, EST 14:00 UTC


@pytest.mark.parametrize("body", [
    {"weekday": 5}, {"time": "25:00"}, {"weekday": "someday"}, {"active": "yes"}, {"channel_ids": "ch_x"}, {"channel_ids": [1]},
    {"channel_ids": ["x"] * 501}, {"tz": "Mars/Base"}, {"template": "nope"}, {"surprise": 1}, {"channel_ids": ["not_mine"]},
])
def test_edit_slot_refuses_bad_input_with_400(human, body):
    s = _slot(human)
    r = human.patch(f"/api/templates/slots/{s['id']}", json=body)
    assert r.status_code == 400, (body, r.status_code, r.text)
    after = [x for x in human.get("/api/templates/slots").json() if x["id"] == s["id"]][0]
    assert (after["weekday"], after["at"], after["channels"]) == ("mon", "20:00", ["ch_x", "ch_li"])  # unchanged


def test_edit_slot_refuses_a_non_object_body(human):
    s = _slot(human)
    assert human.patch(f"/api/templates/slots/{s['id']}", json=["wed"]).status_code == 400


def test_someone_elses_slot_and_a_missing_one_both_answer_404(app, human):
    store = app.state.store
    theirs = store.x("INSERT INTO template_slots(user_id,template,channels,weekday,at,tz,active,next_fire,created) "
                     "VALUES(?,?,?,?,?,?,1,?,?)", ("someone_else", "weekly_recap", '["ch_z"]', "mon", "20:00", "Europe/Dublin",
                                                  time.time() + 86400, time.time()))
    for sid in (theirs, 999999):
        assert human.patch(f"/api/templates/slots/{sid}", json={"weekday": "tue"}).status_code == 404
        assert human.delete(f"/api/templates/slots/{sid}").status_code == 404
        assert human.post(f"/api/templates/slots/{sid}/run").status_code == 404
    row = dict(store.q("SELECT weekday, user_id FROM template_slots WHERE id=?", (theirs,))[0])
    assert row == {"weekday": "mon", "user_id": "someone_else"}  # untouched


def test_cta_edit_after_applying_reaches_the_free_drafter(human):
    c = human
    rules = c.post("/api/ledger/presets/genz_casual/apply", json={}).json()["rules"]
    cta = next(r for r in rules if (r.get("check") or {}).get("area") == "cta" and "line" in r["check"])
    r = c.patch(f"/api/ledger/rules/{cta['id']}", json={"rule": "Sign off with: tap follow, it's free"})
    assert r.status_code == 200, r.text
    rules = [{**x, "active": True} for x in rules if x["id"] != cta["id"]] + [{**r.json(), "active": True}]
    st = style_presets.style_from_rules(rules)
    assert st["cta"] == "tap follow, it's free"


def test_zero_hashtag_cap_writes_no_hashtags():
    st = {"shape": "hook_idea_payoff", "cta": "Follow.", "hashtags": {"instagram": 0}, "length": {}}
    text = drafter.styled_draft("instagram", TOPICS[0], st)
    assert "#" not in text and "Follow." in text


def test_a_failed_preset_insert_leaves_the_ledger_as_it_was(app, human, monkeypatch):
    """Delete, switch-off and insert are one transaction: a failure part way keeps the earlier preset's rules on."""
    c, store = human, app.state.store
    c.post("/api/ledger/presets/hook_first/apply", json={})
    snap = lambda: [tuple(dict(r).values()) for r in store.q("SELECT id, active, rule FROM style_rules ORDER BY id")]  # noqa: E731
    before = snap()
    real = store.x
    calls = {"n": 0}

    def flaky(sql, args=()):
        if sql.lstrip().upper().startswith("INSERT INTO STYLE_RULES"):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("database went away")
        return real(sql, args)
    monkeypatch.setattr(store, "x", flaky)
    try:
        assert c.post("/api/ledger/presets/data_led/apply", json={}).status_code >= 500
    except RuntimeError:
        pass  # the test client re-raises the server's exception
    monkeypatch.setattr(store, "x", real)
    assert calls["n"] == 3 and snap() == before  # hook_first's rules still on, none of data_led's half-written


def test_banned_phrases_ending_in_punctuation_are_removed_and_flagged():
    rules = [{"kind": "text", "check": {"type": "banned_words", "words": ["agree?", "thoughts?", "game-changer"]}, "rule": "x",
              "scope": [], "active": True}]
    text = "Big news for builders. Agree?\n\nThoughts? It is a game-changer, and the gamechanger-ish part stays."
    out = brand.apply_text_rules(text, rules)
    assert "agree?" not in out.lower() and "thoughts?" not in out.lower() and "game-changer" not in out.lower()
    assert "gamechanger-ish" in out
    assert not brand.brand_check("Ship it. Thoughts?", None, rules, {})["pass"]
    assert brand.brand_check("Ship it. What would you change?", None, rules, {})["pass"]


def test_punctuation_ending_phrase_matches_right_before_a_new_word():
    # CodeRabbit #134: "thoughts?" was missed in "Thoughts?Yes"; a word-ending phrase still never matches inside a word
    rules = [{"kind": "text", "check": {"type": "banned_words", "words": ["thoughts?", "game"]}, "rule": "x", "scope": [],
              "active": True}]
    assert "thoughts?" not in brand.apply_text_rules("Ship it. Thoughts?Yes, it ships.", rules).lower()
    assert not brand.brand_check("Ship it. Thoughts?Yes", None, rules, {})["pass"]
    assert brand.brand_check("A gamechanger, not a banned word.", None, rules, {})["pass"]
    assert "gamechanger" in brand.apply_text_rules("A gamechanger.", rules)
