"""Growth: campaigns, UTM builder, short links, link-in-bio (Start Page), hashtags, automations, CSV import."""
from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from huhqueue.connectors.fake import FakeConnector
from huhqueue.models import Automation, Link, LinkClick, Post, PostTarget
from huhqueue.services import growth as g
from huhqueue.worker import Worker

from .conftest import future

PUBLIC = "https://q.huhbyte.ai"


def _public(core):
    core.update_prefs({"public_base_url": PUBLIC})


def _sent(core, channel_id, caption, *, days_ago=40, insights=None, tags=None, campaign=""):
    with core.db.session() as s:
        p = Post(caption=caption, tags=tags or [], campaign=campaign)
        s.add(p)
        s.flush()
        t = PostTarget(post_id=p.id, channel_id=channel_id, status="sent",
                       published_at=datetime.utcnow() - timedelta(days=days_ago), insights=insights or {},
                       permalink="https://example.invalid/p/1")
        s.add(t)
        s.flush()
        return t.id


def _ctx(core, tid):
    with core.db.session() as s:
        return Worker(core).build_context(s, s.get(PostTarget, tid))


# ================================================================== campaigns
def test_campaign_crud_rename_and_report(api, core, channel):
    c = api.post("/api/campaigns", {"name": "DevDay week", "start_date": "2026-11-03", "end_date": "2026-11-07"}).json()
    assert c["color"].startswith("#") and c["posts"] == 0
    assert api.post("/api/campaigns", {"name": "DevDay week"}).status_code == 409
    assert api.post("/api/campaigns", {"name": "Bad", "start_date": "2026-11-09", "end_date": "2026-11-01"}).status_code == 400
    assert api.post("/api/campaigns", {"name": "Bad2", "color": "red"}).status_code == 400
    api.post("/api/posts", {"caption": "launch", "channel_ids": [channel["id"]], "when": "draft", "campaign": "DevDay week"})
    _sent(core, channel["id"], "sent one", insights={"views": 100, "likes": 7, "comments": 2}, campaign="DevDay week")
    api.post("/api/links", {"url": "https://huhbyte.ai/devday", "campaign": "DevDay week"})
    row = next(x for x in api.get("/api/campaigns").json() if x["name"] == "DevDay week")
    assert row["posts"] == 2 and row["stats"]["sent"] == 1 and row["stats"]["views"] == 100 and row["stats"]["engagements"] == 9
    # rename moves posts and links
    api.patch(f"/api/campaigns/{c['id']}", {"name": "DevDay 2026"})
    d = api.get(f"/api/campaigns/{c['id']}").json()
    assert d["name"] == "DevDay 2026" and len(d["targets"]) == 2 and len(d["links"]) == 1 and d["channels"][0]["sent"] == 1
    # delete keeps posts, untagged
    assert api.delete(f"/api/campaigns/{c['id']}").json()["posts_untagged"] == 2
    assert api.get("/api/posts?limit=10").json()[0]["campaign"] == ""


def test_legacy_free_text_campaign_gets_a_row(api, channel):
    api.post("/api/posts", {"caption": "x", "channel_ids": [channel["id"]], "when": "draft", "campaign": "Old label"})
    assert any(c["name"] == "Old label" and c["posts"] == 1 for c in api.get("/api/campaigns").json())


# ================================================================== short links
def test_short_link_redirect_and_click_logged(api, core, edge, app):
    ln = api.post("/api/links", {"url": "https://huhbyte.ai/gpt6", "code": "gpt6", "utm": {"source": "newsletter"}}).json()
    assert ln["url"] == "https://huhbyte.ai/gpt6?utm_source=newsletter" and ln["short_url"].endswith("/l/gpt6")
    assert api.post("/api/links", {"url": "https://huhbyte.ai/x", "code": "gpt6"}).status_code == 409
    assert api.post("/api/links", {"url": "https://huhbyte.ai/x", "code": "api"}).status_code == 400
    assert api.post("/api/links", {"url": "not a url"}).status_code == 400
    r = edge.get("/l/gpt6", headers={"referer": "https://instagram.com/", "user-agent": "Mozilla/5.0"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://huhbyte.ai/gpt6?utm_source=newsletter"
    edge.get("/l/gpt6", headers={"user-agent": "facebookexternalhit/1.1"}, follow_redirects=False)   # preview bot: not counted
    edge.head("/l/gpt6", follow_redirects=False)                                                   # HEAD: not counted
    TestClient(app).get("/l/gpt6", follow_redirects=False)                                         # the UI app serves it too, no login
    d = api.get(f"/api/links/{ln['id']}").json()
    assert d["clicks"] == 2 and d["referrers"][0] == {"referrer": "instagram.com", "clicks": 1}
    assert sum(x["clicks"] for x in d["daily"]) == 2
    with core.db.session() as s:
        ua = s.query(LinkClick).first().ua_hash
    assert ua and "Mozilla" not in ua                                                              # hashed, no raw UA
    assert edge.get("/l/nope", follow_redirects=False).status_code == 404
    api.patch(f"/api/links/{ln['id']}", {"title": "GPT-6", "campaign": "Launch"})
    assert api.get("/api/links?q=GPT-6").json()[0]["campaign"] == "Launch"
    api.delete(f"/api/links/{ln['id']}")
    assert api.get("/api/links").json() == []


# ================================================================== UTM
def test_utm_builder_preview_and_defaults(api, channel):
    api.put("/api/utm/defaults/*", {"medium": "social"})
    api.put(f"/api/utm/defaults/{channel['id']}", {"source": "ig-{username}"})
    r = api.post("/api/utm/preview", {"caption": "read https://huhbyte.ai/a?x=1#top now", "channel_ids": [channel["id"]],
                                       "campaign": "Dev Day"}).json()
    one = r["channels"][channel["id"]]
    assert one["params"] == {"source": "ig-huhbyte_demo", "medium": "social", "campaign": "dev-day"}
    assert one["urls"][0]["tagged"] == "https://huhbyte.ai/a?x=1&utm_source=ig-huhbyte_demo&utm_medium=social&utm_campaign=dev-day#top"
    assert g.tag_url("https://a.io/?utm_source=keep", {"source": "x", "medium": "y"}) == "https://a.io/?utm_source=keep&utm_medium=y"


def test_utm_applied_on_publish(api, core, channel):
    _public(core)
    t = api.post("/api/posts", {"caption": "GPT-6 explained https://huhbyte.ai/gpt6", "channel_ids": [channel["id"]],
                                "when": "custom", "scheduled_at": future(60), "campaign": "Launch",
                                "options": {"utm": {"enabled": True, "shorten": True}, "first_comment": "Source: https://openai.com/x"}}).json()["targets"][0]
    ctx = _ctx(core, t["id"])
    assert f"{PUBLIC}/l/" in ctx.caption and "huhbyte.ai/gpt6" not in ctx.caption
    assert "utm_source=fake" in ctx.options["first_comment"] or f"{PUBLIC}/l/" in ctx.options["first_comment"]
    links = api.get("/api/links?source=utm").json()
    assert {ln["campaign"] for ln in links} == {"Launch"} and len(links) == 2
    assert any("utm_campaign=launch" in ln["url"] and "utm_source=fake" in ln["url"] for ln in links)
    _ctx(core, t["id"])                                   # building again reuses the same links
    assert len(api.get("/api/links?source=utm").json()) == 2
    # the stored post is untouched
    assert api.get(f"/api/targets/{t['id']}").json()["caption"] == "GPT-6 explained https://huhbyte.ai/gpt6"
    # link clicks roll into the campaign report
    with core.db.session() as s:
        s.query(Link).filter(Link.source == "utm").update({"clicks": 3})
    assert next(c for c in api.get("/api/campaigns").json() if c["name"] == "Launch")["stats"]["clicks"] == 6


def test_utm_global_auto_and_per_post_opt_out(api, core, channel):
    api.put("/api/utm/defaults/*", {"auto": True})
    assert api.get("/api/utm/defaults").json()["*"]["auto"] is True
    a = api.post("/api/posts", {"caption": "see https://huhbyte.ai/a", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    assert "utm_medium=social" in _ctx(core, a["id"]).caption
    b = api.post("/api/posts", {"caption": "see https://huhbyte.ai/b", "channel_ids": [channel["id"]], "when": "draft",
                                "options": {"utm": {"enabled": False}}}).json()["targets"][0]
    assert _ctx(core, b["id"]).caption == "see https://huhbyte.ai/b"


# ================================================================== link in bio
def _bio(api, base="http://127.0.0.1:9/healthz"):
    return api.post("/api/bio", {"slug": "huhbyte", "title": "HuhByte AI", "bio": "Daily AI news <b>simple</b>",
                                 "blocks": [{"type": "heading", "text": "Start here"},
                                            {"type": "link", "label": "Newsletter", "url": "https://huhbyte.ai/news", "highlight": True},
                                            {"type": "link", "label": "YouTube", "url": "https://youtube.com/@huhbyte"},
                                            {"type": "link", "label": "Hidden", "url": "https://huhbyte.ai/h", "hidden": True},
                                            {"type": "posts", "count": 3},
                                            {"type": "socials", "auto": True}]}).json()


def test_bio_page_public_render(api, core, edge, channel):
    _sent(core, channel["id"], "Robot does kung fu", days_ago=1)
    p = _bio(api)
    assert p["theme"]["preset"] == "lavender" and p["public_url"].endswith("/bio/huhbyte")
    assert all(b.get("link_id") for b in p["blocks"] if b["type"] == "link")
    html = edge.get("/bio/huhbyte").text
    assert "HuhByte AI" in html and "&lt;b&gt;simple&lt;/b&gt;" in html and "<b>simple" not in html     # escaped
    assert "Newsletter" in html and "Hidden" not in html and "/l/" in html and "https://huhbyte.ai/news" not in html
    assert "Robot does kung fu" in html and "#F6F1FF" in html                                         # posts grid, lavender theme
    for leak in ("tok-1234", channel["id"], "access_token", "/api/"):
        assert leak not in html
    assert api.post("/api/bio", {"slug": "huhbyte"}).status_code == 409
    assert api.post("/api/bio", {"slug": "Bad Slug!"}).status_code == 400
    assert api.post("/api/bio", {"slug": "x2", "blocks": [{"type": "link", "label": "a", "url": "javascript:alert(1)"}]}).status_code == 400
    # unpublished: 404 publicly, preview still works for the owner
    api.patch(f"/api/bio/{p['id']}", {"published": False})
    assert edge.get("/bio/huhbyte").status_code == 404
    assert "not published" in api.get(f"/api/bio/{p['id']}/preview").text
    assert TestClient(edge.app).get(f"/api/bio/{p['id']}").status_code == 404     # the edge app has no private API


def test_bio_click_analytics(api, core, edge):
    p = _bio(api)
    news = next(b for b in p["blocks"] if b.get("label") == "Newsletter")
    code = news["short_url"].rsplit("/", 1)[1]
    edge.get("/bio/huhbyte")
    edge.get("/bio/huhbyte")
    r = edge.get(f"/l/{code}", headers={"referer": "http://localhost/bio/huhbyte", "user-agent": "Mozilla"}, follow_redirects=False)
    assert r.headers["location"] == "https://huhbyte.ai/news"
    d = api.get(f"/api/bio/{p['id']}").json()
    assert d["views"] == 2 and d["clicks"] == 1
    assert d["stats"]["views"] == 2 and d["stats"]["clicks"] == 1 and d["stats"]["ctr"] == 50.0
    top = d["stats"]["blocks"][0]
    assert {k: top[k] for k in ("id", "label", "clicks")} == {"id": news["id"], "label": "Newsletter", "clicks": 1}
    assert next(b for b in d["blocks"] if b["id"] == news["id"])["clicks"] == 1
    # relabel keeps the short link + stats; a new URL starts a new link
    blocks = [dict(b, label="Weekly newsletter") if b["id"] == news["id"] else b for b in d["blocks"]]
    d2 = api.patch(f"/api/bio/{p['id']}", {"blocks": blocks}).json()
    assert next(b for b in d2["blocks"] if b["id"] == news["id"])["link_id"] == news["link_id"]
    blocks = [dict(b, url="https://huhbyte.ai/news2") if b["id"] == news["id"] else b for b in d2["blocks"]]
    d3 = api.patch(f"/api/bio/{p['id']}", {"blocks": blocks}).json()
    assert next(b for b in d3["blocks"] if b["id"] == news["id"])["link_id"] != news["link_id"]


def test_bio_theme_validation(api):
    p = api.post("/api/bio", {"slug": "night", "theme": {"preset": "night", "buttons": "square"}}).json()
    assert p["theme"]["preset"] == "night" and p["blocks"]                      # default blocks
    assert api.patch(f"/api/bio/{p['id']}", {"theme": {"preset": "neon"}}).status_code == 400
    assert api.patch(f"/api/bio/{p['id']}", {"theme": {"accent": "purple"}}).status_code == 400
    assert api.delete(f"/api/bio/{p['id']}").json()["deleted"] == p["id"]


# ================================================================== hashtags
def test_hashtag_groups_stats_and_suggest(api, core, channel):
    _sent(core, channel["id"], "big #AI news #LLM", days_ago=2, insights={"likes": 50, "views": 1000})
    _sent(core, channel["id"], "small #AI story #Robots", days_ago=3, insights={"likes": 2})
    api.post("/api/posts", {"caption": "next #ai up", "channel_ids": [channel["id"]], "when": "custom", "scheduled_at": future(90)})
    stats = {r["tag"].lower(): r for r in api.get("/api/hashtags/stats").json()}
    assert stats["#ai"]["uses"] == 3 and stats["#ai"]["sent"] == 2 and stats["#ai"]["scheduled"] == 1
    assert stats["#llm"]["avg_score"] > stats["#robots"]["avg_score"]
    gr = api.post("/api/hashtags/groups", {"name": "LLMs", "text": "AI, LLM OpenAI"}).json()
    assert gr["text"] == "#AI #LLM #OpenAI" and gr["count"] == 3 and gr["uses"] >= 4
    assert api.post("/api/hashtags/groups", {"name": "llms", "text": "#x"}).status_code == 409
    assert api.post("/api/hashtags/groups", {"name": "Empty", "text": ""}).status_code == 400
    api.put("/api/settings", {"hashtag_limit": 2})
    assert api.post("/api/hashtags/groups", {"name": "Too many", "text": "#a #b #c"}).status_code == 400
    api.patch(f"/api/hashtags/groups/{gr['id']}", {"name": "Language models"})
    names = [x["name"] for x in api.get("/api/hashtags/groups").json()]
    assert "Language models" in names and "AI news" in names                         # the default group is kept
    assert any(x["name"] == "Language models" for x in api.get("/api/library/hashtag_groups").json())   # composer sees it
    sug = api.get("/api/hashtags/suggest", params={"caption": "new #AI model"}).json()
    assert sug["tags"][0]["tag"] == "#LLM" and all(t["tag"].lower() != "#ai" for t in sug["tags"])
    assert any(x["name"] == "Language models" for x in sug["groups"])
    api.delete(f"/api/hashtags/groups/{gr['id']}")
    assert api.delete(f"/api/hashtags/groups/{gr['id']}").status_code == 404


# ================================================================== automations
def test_auto_first_comment_at_publish(api, core, channel):
    rule = api.post("/api/automations", {"kind": "first_comment", "channel_ids": [channel["id"]],
                                         "config": {"text": "Sources in bio · {campaign}"}}).json()
    assert rule["next_run_at"] is None and rule["active"]
    a = api.post("/api/posts", {"caption": "one", "channel_ids": [channel["id"]], "when": "draft", "campaign": "Launch"}).json()["targets"][0]
    assert _ctx(core, a["id"]).options["first_comment"] == "Sources in bio · Launch"
    b = api.post("/api/posts", {"caption": "two", "channel_ids": [channel["id"]], "when": "draft",
                                "options": {"first_comment": "my own"}}).json()["targets"][0]
    assert _ctx(core, b["id"]).options["first_comment"] == "my own"                 # if_empty keeps a hand-written one
    api.patch(f"/api/automations/{rule['id']}", {"config": {"mode": "append"}})
    assert _ctx(core, b["id"]).options["first_comment"].startswith("my own\n\nSources in bio")
    assert api.get(f"/api/automations/{rule['id']}").json()["state"]["applied"] == 2
    # run now writes it onto queued posts (visible in the composer); preview lists them first
    assert api.get(f"/api/automations/{rule['id']}/preview").json()["items"]
    api.post(f"/api/automations/{rule['id']}/run")
    assert api.get(f"/api/targets/{a['id']}").json()["options"]["first_comment"].startswith("Sources in bio")
    api.patch(f"/api/automations/{rule['id']}", {"active": False})
    c = api.post("/api/posts", {"caption": "three", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    assert not _ctx(core, c["id"]).options.get("first_comment")
    assert api.post("/api/automations", {"kind": "first_comment", "channel_ids": [channel["id"]], "config": {}}).status_code == 400


def test_evergreen_fills_empty_slot_respecting_gap(api, core, channel):
    best = _sent(core, channel["id"], "evergreen best", days_ago=60, insights={"likes": 90})
    _sent(core, channel["id"], "evergreen ok", days_ago=60, insights={"likes": 5})
    _sent(core, channel["id"], "too fresh", days_ago=2, insights={"likes": 500})
    a = api.post("/api/automations", {"kind": "evergreen", "channel_ids": [channel["id"]],
                                      "config": {"min_age_days": 30, "max_recycles": 1, "per_run": 1}}).json()
    prev = api.get(f"/api/automations/{a['id']}/preview").json()["items"]
    assert [x["target_id"] for x in prev][:1] == [best] and all(x["caption"] != "too fresh" for x in prev)
    assert g.run_due_automations(core) == 1
    q = [t for t in api.get("/api/posts?status=scheduled").json()]
    assert len(q) == 1 and q[0]["caption"] == "evergreen best" and q[0]["options"]["recycled_from"] == best
    nxt = api.post(f"/api/automations/{a['id']}/run").json()
    assert nxt["created"] == 1                                                            # one per run: the next best story
    assert sorted(t["caption"] for t in api.get("/api/posts?status=scheduled").json()) == ["evergreen best", "evergreen ok"]
    assert api.post(f"/api/automations/{a['id']}/run").json()["created"] == 0          # copies pending + max_recycles=1
    with core.db.session() as s:
        t = s.get(PostTarget, q[0]["id"])
        t.status, t.published_at = "sent", datetime.utcnow() - timedelta(days=45)
    assert api.post(f"/api/automations/{a['id']}/run").json()["created"] == 0          # the recycled copy is not recycled again


def test_recurring_generates_next_occurrence(api, core, channel):
    a = api.post("/api/automations", {"kind": "recurring", "channel_ids": [channel["id"]], "name": "Weekly recap",
                                      "config": {"caption": "Weekly AI recap", "frequency": "daily", "time": "23:59",
                                                 "timezone": "UTC", "lead_hours": 48, "max_occurrences": 2}}).json()
    assert a["next_run_at"]
    assert api.get(f"/api/automations/{a['id']}").json()["upcoming"]
    made = g.run_automation(core, a["id"])
    assert made["created"] == 2 and made["automation"]["active"] is False               # series capped at 2
    posts = api.get("/api/posts?status=scheduled").json()
    assert sorted(p["caption"] for p in posts) == ["Weekly AI recap", "Weekly AI recap"]
    assert g.next_occurrence({"frequency": "weekly", "weekdays": [0], "time": "09:00", "timezone": "Europe/Dublin",
                              "anchor_date": "2026-10-26"}, datetime(2026, 10, 26, 10, 0)) == datetime(2026, 11, 2, 9, 0)
    assert g.next_occurrence({"frequency": "monthly", "day_of_month": 31, "time": "09:00", "timezone": "UTC",
                              "anchor_date": "2026-01-31"}, datetime(2026, 2, 1)) == datetime(2026, 2, 28, 9, 0)


FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>AI Blog</title>
<item><title>Post B</title><link>https://ai.example/b</link><guid>b</guid><description>&lt;p&gt;Second&lt;/p&gt;</description></item>
<item><title>Post A</title><link>https://ai.example/a</link><guid>a</guid><description>First</description></item>
</channel></rss>"""


def test_rss_new_items_once(api, core, channel, monkeypatch):
    feed = {"xml": FEED}
    monkeypatch.setattr(core, "http", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=feed["xml"]))))
    a = api.post("/api/automations", {"kind": "rss", "channel_ids": [channel["id"]],
                                      "config": {"url": "https://ai.example/rss", "backfill": 2, "template": "{title} {link}"}}).json()
    assert api.post("/api/rss/check", {"url": "https://ai.example/rss"}).json()["count"] == 2
    assert g.run_automation(core, a["id"])["created"] == 2
    assert g.run_automation(core, a["id"])["created"] == 0
    drafts = api.get("/api/posts?status=draft").json()
    assert sorted(d["caption"] for d in drafts) == ["Post A https://ai.example/a", "Post B https://ai.example/b"]
    feed["xml"] = FEED.replace("<item>", "<item><title>Post C</title><link>https://ai.example/c</link><guid>c</guid></item><item>", 1)
    assert g.run_automation(core, a["id"])["created"] == 1
    feed["xml"] = "not xml"
    r = g.run_automation(core, a["id"])
    assert r["created"] == 0 and "valid" in r["error"]


def test_broken_automation_never_stops_the_worker(api, core, channel):
    a = api.post("/api/automations", {"kind": "evergreen", "channel_ids": [channel["id"]], "config": {}}).json()
    with core.db.session() as s:
        s.get(Automation, a["id"]).config = {}          # corrupt: missing keys
    assert g.run_due_automations(core) == 0
    assert "last_error" in api.get(f"/api/automations/{a['id']}").json()["state"]
    t = api.post("/api/posts", {"caption": "still publishes", "channel_ids": [channel["id"]], "when": "now"}).json()
    assert Worker(core).tick()["published"] >= 1
    assert FakeConnector.published[-1]["caption"] == "still publishes" and t


# ================================================================== CSV import
def test_csv_import_dry_run_errors_then_commit(api, channel):
    csv = ("caption,channels,scheduled_at,tags,campaign,topic_key\n"
           f"Hello world,huhbyte_demo,{(datetime.utcnow() + timedelta(days=2)).strftime('%Y-%m-%d %H:%M')},news|ai,CSV week,t1\n"
           ",huhbyte_demo,,,,\n"
           "Old one,huhbyte_demo,2020-01-01 09:00,,,\n"
           "Dup topic,huhbyte_demo,,,,t1\n"
           "No channel,nobody,,,,\n")
    pre = api.post("/api/import/csv/preview", {"csv": csv, "timezone": "UTC"}).json()
    assert pre["total"] == 5 and pre["valid"] == 1 and pre["invalid"] == 4
    errs = {r["row"]: " ".join(r["errors"]) for r in pre["rows"]}
    assert "Empty row" in errs[3] and "past" in errs[4] and "row 2" in errs[5]
    assert api.post("/api/import/csv", {"csv": csv, "timezone": "UTC", "skip_invalid": False}).status_code == 422
    assert api.get("/api/posts").json() == []                                     # the dry run created nothing
    done = api.post("/api/import/csv", {"csv": csv, "timezone": "UTC"}).json()
    assert done["imported"] == 1 and done["failed"] == 4
    p = api.get("/api/posts?status=scheduled").json()
    assert len(p) == 1 and p[0]["caption"] == "Hello world" and p[0]["campaign"] == "CSV week" and p[0]["tags"] == ["news", "ai"]
    assert "caption" in api.get("/api/import/csv/template").text
    semi = "caption;when\nSemicolon row;draft\n"
    assert api.post("/api/import/csv", {"csv": semi, "channel_ids": [channel["id"]]}).json()["imported"] == 1
    assert api.post("/api/import/csv/preview", {"csv": "foo,bar\n1,2\n"}).status_code == 400


def test_growth_actions_in_registry(api):
    ids = {a["id"] for a in api.get("/api/actions").json()["actions"]}
    assert {"bio.create", "link.create", "utm.preview", "campaign.create", "hashtag.group_create", "automation.create",
            "import.csv", "nav.growth"} <= ids
    r = api.post("/api/actions/link.create:run", {"input": {"url": "https://huhbyte.ai/r"}}).json()
    assert r["status"] == "done" and r["output"]["code"]
