"""One take and the one Bin (owner ask, 5 Nov 2026): the REST answers the Library Bin draws One take's rows from, and the
pages' wiring. The browser side is tests/studio/test_take_bin_e2e.py and test_library_one_take_e2e.py (CI ui-smoke).

Pins: GET /api/takes/bin gives each row what the Bin shows (title or hook, the take it came from, days left) and only
the caller's own; one clip is purged with {clips: [{take_id, clip_id}]} without touching its take; bad purge entries
answer 400, never 500; One take has no Takes / Bin toggle any more (its deletes go to Library › Bin, with Undo); the
Library Bin loads /api/takes/bin with its own list through one AbortController and offers Restore and Delete permanently
(behind a confirm) for takes and clips; Your takes loads through HQLoad.start and leaving One take aborts it; a failed
load shows Retry; take cards keep a fixed track with an ellipsis and a ⋯ menu; theme tokens, no gradients."""
import json
import re
import time
from pathlib import Path

ST = Path(__file__).parents[2] / "studio" / "static"


def _take(st, uid="acc_1", clips=None, binned_at=None, name="Kickoff talk"):
    res = {"clips": clips or []}
    if binned_at is not None:
        res["bin"] = {"at": binned_at, "status": "ready", "tokens": []}
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (uid, "take", json.dumps({"name": name, "channels": []}), "binned" if binned_at is not None else "ready", "[]",
                 json.dumps(res), time.time(), time.time()))


def test_the_bin_answers_what_the_view_draws_and_a_clip_purges_alone(human, app):
    st, now = app.state.store, time.time()
    gone = _take(st, binned_at=now - 2 * 86400, name="Old podcast", clips=[{"id": "c1", "status": "ready"}])
    keep = _take(st, clips=[{"id": "c1", "status": "ready", "hook": "Ship it small"},
                            {"id": "c2", "status": "binned", "hook": "The one we cut", "bin": {"at": now - 6.5 * 86400, "status": "ready", "tokens": []}}])
    _take(st, uid="acc_other", binned_at=now, name="Not yours")
    b = human.get("/api/takes/bin").json()
    assert b["days"] == 7
    assert [(x["take_id"], x["title"], x["clips"], x["days_left"]) for x in b["takes"]] == [(gone, "Old podcast", 1, 5)]
    assert [(x["take_id"], x["clip_id"], x["hook"], x["take_title"], x["days_left"]) for x in b["clips"]] == [(keep, "c2", "The one we cut", "Kickoff talk", 1)]
    assert "Not yours" not in json.dumps(b)
    out = human.post("/api/takes/bin/purge", json={"clips": [{"take_id": keep, "clip_id": "c2"}]}).json()
    assert out["purged_clips"] == [{"take_id": keep, "clip_id": "c2"}] and out["purged_takes"] == []
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (keep,))[0]["result"])
    assert [c["id"] for c in res["clips"]] == ["c1"]                                     # the take and its other clip stay
    assert keep in [x["id"] for x in human.get("/api/takes").json()]
    assert [x["take_id"] for x in human.get("/api/takes/bin").json()["takes"]] == [gone]


def test_bad_purge_entries_are_400_not_500(human):
    for body in ({"clips": ["c2"]}, {"clips": [7]}, {"clips": [{"clip_id": "c1"}]}, {"clips": "c1"}, {"take_ids": {"a": 1}}):
        r = human.post("/api/takes/bin/purge", json=body)
        assert r.status_code == 400, (body, r.text)


def test_one_take_has_no_bin_of_its_own_and_the_library_bin_lists_its_takes_and_clips():
    html, js, css = (ST / "index.html").read_text(), (ST / "take.js").read_text(), (ST / "onetake.css").read_text()
    lib, lcss = (ST / "library.js").read_text(), (ST / "library.css").read_text()
    # One take: no Takes / Bin toggle, no Bin view, no Bin requests of its own
    for gone in ('data-testid="take-view-bin"', 'data-testid="take-bin"', 'data-testid="take-bin-count"', 'data-tkview'):
        assert gone not in html, gone
    assert "/api/takes/bin" not in js and "loadTakeBin" not in js and "T.view" not in js
    assert 'data-testid="take-list"' in html and 'data-testid="take-bin-hint"' in html and "Library › Bin" in html
    # deletes still offer Undo, and say where the Bin is
    assert "window.toastUndo(msg, undo" in js and js.count("Library › Bin") >= 2
    # Your takes: one live request, aborted when One take is left; a failed load says so with Retry
    assert "HQLoad.start(TK_KEY)" in js and 'if ($("#tab-take").hidden) { tkAbort();' in js
    assert "HQLoad.aborted(e) || !tk.live()" in js and '"take-list")' in js and 'role="alert"' in js
    # cards: a ⋯ menu (the Library's menus), never a Delete on top of the title
    assert 'data-testid="take-more"' in js and "window.hqMenu.toggle(b, b.nextElementSibling)" in js
    assert "take-rowwrap" not in js and "position: absolute" not in css[css.index("/* Your takes (5 Nov 2026)"):]
    cards = css[css.index("/* Your takes (5 Nov 2026)"):]
    assert "minmax(min(280px, 100%), 1fr)" in cards and "min-width: 0" in cards and "text-overflow: ellipsis" in cards
    assert "gradient(" not in cards and "var(--line)" in cards and "outline: 2px solid var(--focus)" in cards
    # the Library Bin: One take's takes and clips next to Library items, Restore + Delete permanently (confirm first)
    assert 'api("/api/takes/bin", { signal: tk.signal })' in lib and "takeBinItems(tb)" in lib
    assert "/api/takes/bin/purge" in lib and "hqConfirm(`Delete “${it.name}” permanently?" in lib
    assert ".lp-th.lp-th-ph" in lcss
