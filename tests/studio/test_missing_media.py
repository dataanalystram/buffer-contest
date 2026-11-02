"""Owner bug, 2 Nov 2026: run 20's ready Instagram carousel showed a broken thumbnail (/m/<token>.jpg answered 404) after
its slides were deleted from the Library. The Planner thumbnail falls back to the first slide that still exists, a
package says which files are gone (and can't be sent), and a startup / periodic check counts missing media."""
import json
import time

from studio import house as house_mod
from studio.actions import Actor

from .conftest import image_bytes


def _uid(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


def _tok(c, name):
    return c.app.state.house.put_media(Actor(_uid(c)), image_bytes(64, 80, "JPEG"), name, kind="render", meta={"type": "photo"})


def _run(c, urls, channel_id="ch_ig"):
    pk = {"channel_id": channel_id, "service": "instagram", "channel_name": "huhbyte.ai", "text": "Screen Studio 4.0",
          "status": "ready", "format": "carousel", "media_urls": urls, "check": {"pass": True},
          "media": [{"url": u, "type": "photo", "ratio": "4:5"} for u in urls]}
    return c.app.state.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                               (_uid(c), "agent", json.dumps({"prompt": "x"}), "ready", "[]", json.dumps({"packages": [pk]}),
                                time.time(), time.time()))


def _drop(c, tok):
    c.app.state.store.x("DELETE FROM media WHERE token=?", (tok,))


def _ready(c, rid):
    return next(i for i in c.get("/api/calendar").json()["items"] if i["kind"] == "ready" and i["run_id"] == rid)


def test_thumbnail_falls_back_to_the_first_slide_that_still_exists(human):
    t1, t2, t3 = (_tok(human, f"s{i}.jpg") for i in (1, 2, 3))
    rid = _run(human, [f"https://hq.test/m/{t}" for t in (t1, t2, t3)])
    assert _ready(human, rid)["thumb"] == f"https://hq.test/m/{t1}" and _ready(human, rid)["media_state"] == "ok"
    _drop(human, t1)  # the cover slide is gone (a forced Library delete)
    it = _ready(human, rid)
    assert it["thumb"] == f"https://hq.test/m/{t2}" and human.get(it["thumb"].replace("https://hq.test", "")).status_code == 200
    assert it["media_state"] == "partial" and it["media_missing"] == 1 and it["pass"] is False


def test_every_file_gone_shows_a_clear_state_not_a_broken_image(human):
    t1, t2 = _tok(human, "a.jpg"), _tok(human, "b.jpg")
    rid = _run(human, [f"https://hq.test/m/{t1}", f"https://hq.test/m/{t2}"])
    _drop(human, t1)
    _drop(human, t2)
    it = _ready(human, rid)
    assert it["thumb"] == "" and it["media_state"] == "missing" and it["media_missing"] == 2
    assert "deleted from your Library" in it["error"] and it["pass"] is False


def test_run_view_marks_missing_files_and_refuses_to_send(human):
    t1, t2 = _tok(human, "a.jpg"), _tok(human, "b.jpg")
    rid = _run(human, [f"https://hq.test/m/{t1}", f"https://hq.test/m/{t2}"])
    _drop(human, t1)
    r = human.get(f"/api/agent/runs/{rid}")
    assert r.status_code == 200, r.text
    pk = r.json()["result"]["packages"][0]
    assert pk["media_state"] == "partial" and pk["media_missing"] == [f"https://hq.test/m/{t1}"]
    s = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "queue"})
    assert s.status_code == 409 and "deleted from your Library" in s.text
    saved = json.loads(human.app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    assert "media_state" not in saved["packages"][0]  # read fresh every time, never saved


def test_outside_urls_and_library_uploads_count_as_present(human):
    h = human.app.state.house
    up = human.post("/api/library/upload?name=clip.jpg", content=image_bytes(32, 32, "JPEG")).json()["media_token"]
    st = h.media_state([{"media_urls": ["https://cdn.example.com/a.jpg", f"https://hq.test/m/{up}"]}])
    assert st[0]["state"] == "ok" and st[0]["thumb"] == "https://cdn.example.com/a.jpg"
    assert h.media_token_of("https://hq.test/m/abc.jpg?x=1") == "abc.jpg" and h.media_token_of("https://cdn.example.com/m/a") == ""


def test_media_check_finds_missing_files_and_reports_them(human):
    t1 = _tok(human, "a.jpg")
    ok = _run(human, [f"https://hq.test/m/{_tok(human, 'b.jpg')}"])
    bad = _run(human, [f"https://hq.test/m/{t1}"])
    _drop(human, t1)
    h = human.app.state.house.media_audit()
    assert [b["run_id"] for b in h["runs"]] == [bad] and h["files_missing"] == 1 and ok not in [b["run_id"] for b in h["runs"]]
    mine = human.get("/api/media/health").json()
    assert mine["runs"] == [{"run_id": bad, "missing": 1, "state": "missing"}] and mine["checked_at"]
    hz = human.get("/healthz")
    assert hz.status_code == 200 and hz.json()["media"]["missing_files"] == 1


def test_media_check_thread_runs_once_at_startup(human, monkeypatch):
    seen = []
    monkeypatch.setattr(human.app.state.house, "media_audit", lambda: seen.append(1) or {})
    assert house_mod.start_media_check(human.app.state.house, every=0) is None  # 0 turns it off
    t = house_mod.start_media_check(human.app.state.house, every=3600, first_delay=0)
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert t is not None and t.daemon and seen == [1]


def test_planner_and_package_ui_handle_missing_media():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "studio" / "static"
    pl = (root / "planner.js").read_text()
    assert 'data-testid="planner-media-missing"' in pl and 'addEventListener("error"' in pl and "onerror=" not in pl
    hj = (root / "house.js").read_text()
    assert 'data-testid="pkg-media-missing"' in hj and "media_missing" in hj
