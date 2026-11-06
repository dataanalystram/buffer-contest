"""One take, one run at a time (owner ask, 6 Nov 2026: "when I click one run it shows the history of how it ran, but each
run is different, and I don't see a fresh-run or back button to start a new run. Also I don't see the main score and the
reason"). The REST and wiring side; the browser side is tests/studio/test_take_run_view_e2e.py.

Pins: GET /api/takes/<id> gives every clip its score and its reason, falling back to the take's own moments (by moment_id)
and then to the scorer's reasons list for takes saved before the clip carried them; a score that isn't a number is None
(drawn as "Not scored"), never a crash. take.js draws a run bar (All takes, the run's title, date, status, New take), keeps
the open run in the URL (?take=<id>, pushState) so browser Back returns to Your takes, reads the address back on Back /
Forward, aborts the open and the status poll when the view changes, and every clip row shows a Score pill (out of 10) and
its reason, or "Not scored"."""
import json
import time
from pathlib import Path

ST = Path(__file__).parents[2] / "studio" / "static"


def _take(st, clips, moments=None, uid="acc_1", name="Kickoff talk", status="ready"):
    res = {"clips": clips, "moments": moments or []}
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (uid, "take", json.dumps({"name": name, "channels": []}), status, "[]", json.dumps(res), time.time(), time.time()))


def _clip(cid, mid, **kw):
    return {"id": cid, "moment_id": mid, "status": "ready", "start": 10.0, "end": 40.0, "hook": "Ship it small", "text": "Ship it small.",
            "captions": {}, "placements": [], **kw}


def test_every_clip_carries_its_score_and_reason_even_from_an_older_take(human, app):
    st = app.state.store
    rid = _take(st, [
        _clip("c1", "m1", score=82.4, why="Opens with a question (built-in scorer)."),   # on the clip itself
        _clip("c2", "m2"),                                                               # older take: only its moment has them
        _clip("c3", "m3", score=None, reasons=["opens with a question", "a number up front"]),  # opus: reasons list only
        _clip("c4", "m9"),                                                               # nothing anywhere
        _clip("c5", "m5", score="not a number", why=""),                                 # garbage never crashes the view
    ], moments=[{"id": "m2", "score": 77.7, "why": "Strong opening line, clean ending, steady pace (built-in scorer)."},
                {"id": "m3", "score": 64.0, "reason": ""}, {"id": "m5", "score": "x"}, "junk"])
    t = human.get(f"/api/takes/{rid}").json()
    got = {c["id"]: (c["score"], c["why"]) for c in t["clips"]}
    assert got["c1"] == (82.4, "Opens with a question (built-in scorer).")
    assert got["c2"] == (77.7, "Strong opening line, clean ending, steady pace (built-in scorer).")
    assert got["c3"] == (64.0, "Opens with a question; a number up front")
    assert got["c4"] == (None, "")
    assert got["c5"] == (None, "")
    assert t["created"] and t["status"] == "ready" and t["input"]["name"] == "Kickoff talk"   # what the run bar names


def test_another_users_take_is_still_404(human, app):
    rid = _take(app.state.store, [_clip("c1", "m1", score=80)], uid="acc_other", name="Not yours")
    assert human.get(f"/api/takes/{rid}").status_code == 404


def test_the_run_view_has_a_back_and_a_new_take_and_keeps_the_run_in_the_address():
    html, js, css = (ST / "index.html").read_text(), (ST / "take.js").read_text(), (ST / "onetake.css").read_text()
    view = html[html.index('<section id="takeView"'):html.index("</section>", html.index('<section id="takeView"'))]
    for tid in ('data-testid="take-runbar"', 'data-testid="take-back"', 'data-testid="take-new"', 'data-testid="take-run-id"'):
        assert tid in view, tid
    assert "All takes" in view and "New take" in view
    # the open run is in the URL, pushed (browser Back leaves it), and read back on Back/Forward before app.js redraws
    assert 'p.set("take", String(id))' in js and "history.pushState(st, \"\", url)" in js
    assert 'window.addEventListener("popstate", tkPop, true)' in js
    # leaving the run: the open and the status poll are aborted, a late answer is never drawn
    assert 'HQLoad.start("takepoll")' in js and 'HQLoad.cancel("takepoll")' in js and 'HQLoad.cancel("takeopen")' in js
    assert 'if (!op.live() || $("#tab-take").hidden) return;' in js
    # loading state while a run opens, and an error with Retry in place
    assert 'HQLoad.rows(3, "Loading the clips")' in js and '"take-open")' in js
    # New take resets the form for a fresh run; All takes keeps what was typed
    reset = js[js.index("function tkResetForm("):js.index("function tkLeaveRun(")]
    for part in ("T.file = null", '$("#tkFile").value = ""', '$("#tkUrl").value = ""', '$("#tkOwns").checked = false', 'setTakeSrc("upload")'):
        assert part in reset, part
    # score + reason on every row, "Not scored" when there is none; sort by score puts unscored last
    assert ">Not scored<" in js and 'data-testid="clip-score"' in js and 'data-testid="clip-why"' in js
    assert "No reason saved for this clip." in js and "Score (high → low)" in html
    assert "gradient(" not in css and ".tk-runbar" in css and ".clip-scoreline" in css
