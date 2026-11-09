"""Starter feeds (studio/starter_feeds.py): a new account's News is never empty. Skipping setup follows a default set of
tech-news feeds on the first open of For you; finishing setup follows feeds that match the chosen interests and drops
the starter feeds that no longer match. Hand-followed feeds always stay, and a starter feed the person unfollowed is
never added back."""
import pytest

from studio import radar, starter_feeds as sf


def _feed(url: str, *a, **k) -> dict:
    title = sf.LABELS.get(url, url)
    word = {"arXiv cs.AI": "A new arXiv study on reasoning", "TechCrunch AI": "OpenAI ships a model"}.get(title, f"Story from {title}")
    return {"title": title, "stories": [{"title": word, "url": f"https://example.com/{abs(hash(url))}", "summary": "", "published": 1.0, "image": ""}]}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("HQ_STARTER_FEEDS", "1")
    monkeypatch.setattr(radar, "fetch", _feed)


def urls(human):
    return [f["url"] for f in human.get("/api/radar").json()["feeds"]]


def test_skipped_setup_gets_current_news_on_first_open(human, live):
    r = human.get("/api/radar").json()
    assert [f["url"] for f in r["feeds"]] == sf.DEFAULT
    assert len(r["stories"]) == len(sf.DEFAULT) and not r["errors"]
    # removing them all is respected: the next open doesn't re-add them
    for f in r["feeds"]:
        human.delete(f"/api/radar/feeds/{f['id']}")
    assert urls(human) == []


def test_finishing_setup_follows_feeds_for_the_interests(human, live):
    human.get("/api/radar")  # first open before setup: the defaults
    human.post("/api/radar/feeds", json={"url": "https://mine.example.com/feed"})  # hand-followed
    o = human.put("/api/onboarding", json={"content_types": ["research", "ai_news"], "done": True}).json()
    got = urls(human)
    assert "https://mine.example.com/feed" in got  # never removed
    assert sf.BY_TYPE["research"][0] in got and sf.BY_TYPE["ai_news"][0] in got
    assert "https://www.theverge.com/rss/index.xml" not in got  # a default that matches neither interest goes
    assert {x["label"] for x in o["following"]} >= {"arXiv cs.AI", "TechCrunch AI"}
    r = human.get("/api/radar").json()
    by = {s["source"]: s for s in r["stories"]}
    assert by["arXiv cs.AI"]["why_for_you"].startswith("Matches")       # "study" is a research word
    assert by["Hugging Face Blog"]["why_for_you"] == "Picked for Research papers"  # from a feed chosen for it


def test_unfollowed_starter_feed_is_not_added_back_and_cap_holds(human, live):
    human.put("/api/onboarding", json={"content_types": ["ai_news"]})
    r = human.get("/api/radar").json()
    gone = next(f for f in r["feeds"] if f["url"] == sf.BY_TYPE["ai_news"][0])
    human.delete(f"/api/radar/feeds/{gone['id']}")
    human.put("/api/onboarding", json={"content_types": list(sf.BY_TYPE), "done": True})
    got = urls(human)
    assert gone["url"] not in got and len(got) <= radar.MAX_FEEDS
    assert sf.BY_TYPE["india_tech"][0] in got  # every interest gets its best feed first


def test_existing_account_with_interests_and_a_feed_gets_matching_feeds_once(human, live, app):
    """Accounts that finished setup before starter feeds existed: the next open of For you adds their interests' feeds."""
    from studio import onboarding
    human.post("/api/radar/feeds", json={"url": "https://mine.example.com/feed"})
    uid = app.state.store.q("SELECT user_id FROM feeds")[0]["user_id"]
    onboarding.ensure(app.state.store)
    app.state.store.x("INSERT INTO user_profiles(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                      (uid, '{"content_types": ["research"], "done": true}', 1.0))
    got = urls(human)
    assert got[0] == "https://mine.example.com/feed" and sf.BY_TYPE["research"][0] in got
    gone = [f for f in human.get("/api/radar").json()["feeds"] if f["url"] == sf.BY_TYPE["research"][0]][0]
    human.delete(f"/api/radar/feeds/{gone['id']}")
    assert sf.BY_TYPE["research"][0] not in urls(human)  # only once


def test_switch_off_and_agents_never_seed(human, monkeypatch):
    monkeypatch.setattr(radar, "fetch", _feed)
    assert urls(human) == []  # HQ_STARTER_FEEDS=0 (tests default)
    assert sf.wanted([]) == sf.DEFAULT and len(sf.wanted(list(sf.BY_TYPE))) == radar.MAX_FEEDS
