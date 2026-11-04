"""Library premium (owner ask, 3 Nov 2026): GET /api/library filters and pages on the server. UI = REST = MCP.

Pins: type tabs (video | carousel | image | audio) with per-type counts that ignore the type filter itself; created_by
agent | you; used_in used | unused | draft | scheduled | sent; run (made by or used in that run); date windows; q matches
the card title (a generated item's run headline) too; per/offset page with the total before paging; bad values are 400;
the list reads the runs once whatever the item count (no N+1); MCP list_library takes the same filters."""
import json
import time

import pytest

from studio import library
from studio.actions import Actor

from .test_agent_core import jpeg
from .test_makepost_library import owner, upload, uid_of  # noqa: F401  (owner is a fixture)


def _run(owner, uid, headline, tok=None, sent=None, created=None):
    h = owner.app.state.house
    pk = {"channel_id": "ch_li", "service": "linkedin", "text": "x", "status": "ready"}
    if tok:
        url = h.media_url(tok)
        pk.update(media_urls=[url], media=[{"url": url, "type": "photo", "token": tok}])
    if sent:
        pk["sent"] = {"mode": sent, "buffer_post_id": "p1"}
    res = {"story": {"headline": headline}, "packages": [pk]}
    t = created or time.time()
    return owner.app.state.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                                   (uid, "agent", json.dumps({"prompt": headline}), "ready", "[]", json.dumps(res), t, t))


def _gen(owner, uid, headline, sent=None, created=None):
    """A picture the agent made in a run (the run's own post uses it)."""
    h = owner.app.state.house
    a = Actor(uid)
    tok = h.put_media(a, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350})
    rid = _run(owner, uid, headline, tok, sent, created)
    iid = h.library.register_generated(a, [{"token": tok, "url": h.media_url(tok), "type": "photo"}], "photo", rid, created)
    return iid, rid


@pytest.fixture
def lib(owner):
    uid = uid_of(owner)
    ups = [upload(owner, jpeg(600 + i, 750), f"holiday {i}.jpg")["id"] for i in range(3)]
    vid = upload(owner)["id"]  # my clip.mp4
    g1, r1 = _gen(owner, uid, "Agent Desktop ships", sent="draft")
    g2, r2 = _gen(owner, uid, "Screen Studio 4.0 launches", sent="queue", created=time.time() - 40 * 86400)
    owner.app.state.store.x("UPDATE library_items SET created=? WHERE id=?", (time.time() - 10 * 86400, ups[2]))
    return {"ups": ups, "vid": vid, "g1": g1, "g2": g2, "r1": r1, "r2": r2, "uid": uid}


def ids(r):
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


def test_type_tabs_and_facets(owner, lib):
    d = owner.get("/api/library").json()
    assert d["facets"]["types"] == {"all": 6, "video": 1, "carousel": 0, "image": 5, "audio": 0}
    assert ids(owner.get("/api/library?type=video")) == {lib["vid"]}
    r = owner.get("/api/library?type=image").json()
    assert len(r["items"]) == 5 and r["facets"]["types"]["video"] == 1  # the tab counts ignore the type filter itself
    assert owner.get("/api/library?type=audio").json()["items"] == []
    assert len(owner.get("/api/library?type=all").json()["items"]) == 6


def test_created_by_used_in_run_and_date(owner, lib):
    assert ids(owner.get("/api/library?created_by=agent")) == {lib["g1"], lib["g2"]}
    assert ids(owner.get("/api/library?created_by=you")) == set(lib["ups"]) | {lib["vid"]}
    assert ids(owner.get("/api/library?used_in=used")) == {lib["g1"], lib["g2"]}
    assert ids(owner.get("/api/library?used_in=unused")) == set(lib["ups"]) | {lib["vid"]}
    assert ids(owner.get("/api/library?used_in=draft")) == {lib["g1"]}
    assert ids(owner.get("/api/library?used_in=scheduled")) == {lib["g2"]}
    assert ids(owner.get(f"/api/library?run={lib['r1']}")) == {lib["g1"]}
    assert ids(owner.get("/api/library?date=7d")) == {lib["g1"], lib["vid"], lib["ups"][0], lib["ups"][1]}
    assert ids(owner.get("/api/library?date=30d")) == {lib["g1"], lib["vid"], *lib["ups"]}
    assert ids(owner.get("/api/library?created_by=agent&date=30d")) == {lib["g1"]}  # filters combine
    runs = owner.get("/api/library").json()["facets"]["runs"]
    assert [r["run_id"] for r in runs] == [lib["r2"], lib["r1"]] and runs[1]["title"] == "Agent Desktop ships"


def test_search_matches_the_card_title_and_type(owner, lib):
    d = owner.get("/api/library?q=desktop").json()
    assert [i["id"] for i in d["items"]] == [lib["g1"]]
    assert d["items"][0]["title"] == "Agent Desktop ships" and d["items"][0]["name"] == "Image.jpg"
    assert ids(owner.get("/api/library?q=holiday")) == set(lib["ups"])
    assert ids(owner.get("/api/library?q=video")) == {lib["vid"]}
    assert owner.get("/api/library?q=nothing-like-this").json()["items"] == []


def test_server_pages_with_the_total_before_paging(owner, lib):
    a = owner.get("/api/library?per=4&offset=0").json()
    b = owner.get("/api/library?per=4&offset=4").json()
    assert a["total"] == b["total"] == 6 and len(a["items"]) == 4 and len(b["items"]) == 2
    assert not {i["id"] for i in a["items"]} & {i["id"] for i in b["items"]}
    f = owner.get("/api/library?per=2&type=image&created_by=you").json()
    assert f["total"] == 3 and len(f["items"]) == 2 and f["filters"] == {"type": "image", "created_by": "you"}


@pytest.mark.parametrize("q", ["type=gif", "created_by=bob", "used_in=maybe", "date=2y", "run=abc"])
def test_bad_filter_values_are_400(owner, lib, q):
    r = owner.get("/api/library?" + q)
    assert r.status_code == 400, r.text


def test_one_runs_query_whatever_the_item_count(owner, lib, monkeypatch):
    """No N+1: the list reads agent_runs once and library_items once, however many items there are."""
    st = owner.app.state.store
    seen = []
    real = st.q

    def spy(sql, *a, **k):
        seen.append(sql)
        return real(sql, *a, **k)
    monkeypatch.setattr(st, "q", spy)

    def count():
        seen.clear()
        assert owner.get("/api/library?per=12").status_code == 200
        return (sum("FROM agent_runs" in s for s in seen),
                sum("FROM library_items WHERE user_id=? AND storage NOT IN" in s for s in seen))
    small = count()
    for i in range(8):
        upload(owner, jpeg(700 + i, 700), f"more {i}.jpg")
    assert small == count() == (1, 1)


def test_mcp_list_library_takes_the_same_filters(owner, lib):
    a = Actor(lib["uid"])
    r = library.call_tool(owner.app.state.studio, a, "list_library", {"type": "image", "created_by": "agent", "per": 1})
    assert r["total"] == 2 and len(r["items"]) == 1
    schema = next(t for t in library.TOOLS if t["name"] == "list_library")["inputSchema"]["properties"]
    assert {"type", "created_by", "used_in", "run", "date"} <= set(schema)
