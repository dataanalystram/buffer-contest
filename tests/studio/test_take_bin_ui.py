"""One take's Bin in the browser UI (owner ask, 5 Nov 2026): the REST answers the Bin view draws from, and the page's
own wiring. The browser side is tests/studio/test_take_bin_e2e.py (CI ui-smoke).

Pins: GET /api/takes/bin gives each row what the view shows (title or hook, the take it came from, days left) and only
the caller's own; one clip is purged with {clips: [{take_id, clip_id}]} without touching its take; bad purge entries
answer 400, never 500; the page has a Takes / Bin tablist, each view loads through its own AbortController
(HQLoad.start), and a view change or leaving One take aborts the other's request; a failed load shows a message with
Retry; Delete forever asks first; a delete offers Undo; styles use theme tokens and no gradients."""
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


def test_the_page_wires_the_bin_with_abort_retry_and_tokens():
    html, js, css = (ST / "index.html").read_text(), (ST / "take.js").read_text(), (ST / "onetake.css").read_text()
    for tid in ("take-view-takes", "take-view-bin", "take-bin", "take-bin-count", "take-list"):
        assert f'data-testid="{tid}"' in html
    assert 'role="tablist"' in html and 'aria-controls="takeBin"' in html
    # one live request per view, cancelled on a view change and when One take is left
    assert "HQLoad.start(TK_KEYS.takes)" in js and "HQLoad.start(TK_KEYS.bin)" in js
    assert re.search(r"function tkView\(v\)[\s\S]*?tkAbort\(T\.view\)", js)
    assert 'if ($("#tab-take").hidden) tkAbort();' in js
    assert js.count("HQLoad.aborted(e) || !tk.live()") >= 2                              # a lost race never draws
    # a failed load says so with Retry, never a silent empty list
    assert '"take-bin")' in js and '"take-list")' in js and "-retry" in js and 'role="alert"' in js
    lists = js[js.index("async function loadTakes"):js.index("async function loadTake()")]
    assert "catch (_) {}" not in lists
    # Restore, Delete forever behind a confirm, Undo right after a delete
    assert 'data-testid="take-bin-restore"' in js and 'data-testid="take-bin-delete-forever"' in js
    act = js[js.index("async function tkBinAct"):]
    assert "hqConfirm(" in act and "can't be undone" in act
    assert "window.toastUndo(msg, undo" in js
    # theme tokens, lavender accent, no gradients
    bin_css = css[css.index("Your takes / Bin"):]
    assert "gradient(" not in bin_css and "gradient(" not in js[js.index("list of takes, and the Bin"):]
    assert "var(--hc-lav" in bin_css and "var(--muted)" in bin_css and "var(--bad)" in bin_css
    assert "@media (max-width: 480px)" in bin_css
