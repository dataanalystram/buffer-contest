"""Make a post follows Buffer, and Instagram music Buffer can't load never reaches it (owner, 9 Nov 2026: "when I deleted
the draft from Buffer, Make a post is not updated with the edit options again ... make sure it is bidirectional sync";
"Gifts to your future self" went to Buffer as the Reel's music and Buffer showed "Couldn't load track").

Pins: opening a run (REST GET /api/agent/runs/<id>, MCP get_run) re-checks its packages against the account's Buffer posts
(studio/post_sync.py): deleted there -> ready and editable again with a buffer_sync note; scheduled / sent / failed there ->
sent.buffer_status; caption edited there -> taken over; Approvals rejected -> ready. Nothing changes inside the 2-minute
grace or when Buffer can't be read. A track Buffer's instagramAudio check refuses is never saved or sent; HQ_AUDIO_CHECK=0
turns the check off."""
import json
import time
from pathlib import Path

from studio import mcp
from studio.actions import Actor
from studio.agent_brain import Brain

ST = Path(__file__).parents[2] / "studio" / "static"
VIDEO = "https://cdn.example.com/reel.mp4"
GIFT = {"audio_id": "285192784590236", "title": "Gifts to your future self", "type": "originalSound", "duration_ms": 39101,
        "creator_username": "adam.dodson"}


def _uid(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


def _run(c, **pk_extra):
    pk = {"channel_id": "ch_ig", "service": "instagram", "channel_name": "huhbyte.ai", "text": "A new model shipped today",
          "status": "ready", "format": "reel", "media_urls": [VIDEO], "check": {"pass": True},
          "media": [{"url": VIDEO, "type": "video", "ratio": "9:16"}], **pk_extra}
    return c.app.state.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                               (_uid(c), "agent", json.dumps({"prompt": "x"}), "ready", "[]", json.dumps({"packages": [pk]}),
                                time.time(), time.time()))


def _pk(c, rid):
    return c.get(f"/api/agent/runs/{rid}").json()["result"]["packages"][0]


def _saved(c, rid):
    return json.loads(c.app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])["packages"][0]


def _sent(pid, mode="draft", age=3600, **kw):
    return {"status": "sent_to_buffer", "sent": {"mode": mode, "buffer_post_id": pid, "at": time.time() - age, **kw}}


# ------------------------------------------------------------------ Buffer -> Make a post
def test_a_draft_deleted_in_buffer_comes_back_editable(human, fake):
    rid = _run(human, **_sent("bp_gone"))
    out = human.get(f"/api/agent/runs/{rid}").json()
    pk = out["result"]["packages"][0]
    assert out["buffer_check"] == "ok"
    assert pk["status"] == "ready" and "sent" not in pk
    assert pk["buffer_sync"]["event"] == "deleted" and pk["buffer_sync"]["buffer_post_id"] == "bp_gone"
    assert _saved(human, rid)["status"] == "ready"  # stored, so every surface agrees
    # and it can be saved again: a fresh draft, and the note is gone
    human.app.state.studio.media_reach = lambda urls: []
    fake.media[VIDEO] = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "draft", "override_warnings": True})
    assert r.status_code == 200, r.text
    s = _saved(human, rid)
    assert s["status"] == "sent_to_buffer" and "buffer_sync" not in s and s["sent"]["at"] > time.time() - 60


def test_a_post_saved_moments_ago_is_never_called_deleted(human):
    rid = _run(human, **_sent("bp_new", age=10))
    pk = _pk(human, rid)
    assert pk["status"] == "sent_to_buffer" and "buffer_sync" not in pk


def test_scheduled_and_edited_in_buffer_show_as_they_are(human, fake):
    fake.posts += [{"id": "bp_s", "text": "Edited in Buffer by hand", "status": "scheduled", "dueAt": "2030-01-07T09:30:00.000Z",
                    "channelId": "ch_ig"}]
    rid = _run(human, **_sent("bp_s"))
    pk = _pk(human, rid)
    assert pk["status"] == "sent_to_buffer"
    assert pk["sent"]["buffer_status"] == "scheduled" and pk["sent"]["due_at"] == "2030-01-07T09:30:00.000Z"
    assert pk["text"] == "Edited in Buffer by hand" and pk["sent"]["edited_in_buffer"]


def test_a_caption_edit_alone_is_marked_edited_in_buffer(human, fake):
    fake.posts += [{"id": "bp_c", "text": "Only the caption changed", "status": "draft", "channelId": "ch_ig"}]
    rid = _run(human, **_sent("bp_c", buffer_status="draft"))
    pk = _pk(human, rid)
    assert pk["text"] == "Only the caption changed" and pk["sent"]["edited_in_buffer"]
    assert _saved(human, rid)["sent"]["edited_in_buffer"]  # stored, not only in this answer


def test_an_edit_saved_while_buffer_was_read_wins(human, fake, monkeypatch):
    from studio import post_sync
    rid = _run(human, **_sent("bp_gone"))
    st = human.app.state.store
    real = post_sync.reconcile

    def slow(*a, **k):  # the person saves a caption while Buffer is being read
        out = real(*a, **k)
        st.x("UPDATE agent_runs SET updated=? WHERE id=?", (time.time() + 5, rid))
        return out
    monkeypatch.setattr(post_sync, "reconcile", slow)
    out = human.get(f"/api/agent/runs/{rid}").json()
    assert _saved(human, rid)["status"] == "sent_to_buffer"  # not overwritten; the next open re-checks
    assert out["result"]["packages"][0]["status"] == "sent_to_buffer"  # and not shown as if it had been
    assert "buffer_check" not in out


def test_published_and_failed_in_buffer(human, fake):
    fake.posts += [{"id": "bp_ok", "text": "A new model shipped today", "status": "sent", "dueAt": "2030-01-05T19:58:00.000Z",
                    "channelId": "ch_ig", "externalLink": "https://instagram.com/p/x"},
                   {"id": "bp_err", "text": "A new model shipped today", "status": "error", "dueAt": "2030-01-05T19:58:00.000Z",
                    "channelId": "ch_ig", "error": {"message": "Video too long"}}]
    ok = _pk(human, _run(human, **_sent("bp_ok", "queue")))
    assert ok["sent"]["buffer_status"] == "sent" and ok["sent"]["link"] == "https://instagram.com/p/x"
    assert ok["text"] == "A new model shipped today"  # same caption: not called edited
    bad = _pk(human, _run(human, **_sent("bp_err", "queue")))
    assert bad["sent"]["buffer_status"] == "failed" and "Video too long" in bad["sent"]["error"]


def test_a_changed_draft_that_was_deleted_loses_update_draft(human):
    rid = _run(human, buffer_draft={"buffer_post_id": "bp_gone", "at": time.time() - 3600},
               changed_since_sent={"mode": "draft", "buffer_post_id": "bp_gone", "format": "reel"})
    pk = _pk(human, rid)
    assert pk["status"] == "ready" and "buffer_draft" not in pk and "changed_since_sent" not in pk
    assert pk["buffer_sync"]["event"] == "deleted"


def test_buffer_unreachable_changes_nothing(human, fake):
    rid = _run(human, **_sent("bp_gone"))
    fake.gql_error = {"message": "Internal", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}
    human.app.state.studio.buffer.refresh(_uid(human))
    out = human.get(f"/api/agent/runs/{rid}?refresh_buffer=1").json()
    pk = out["result"]["packages"][0]
    assert pk["status"] == "sent_to_buffer" and "buffer_sync" not in pk
    assert out["buffer_check"] == "unavailable"  # the card doesn't say "Checked with Buffer"


def test_rejected_in_approvals_comes_back(human):
    st = human.app.state.store
    aid = st.x("INSERT INTO approvals(user_id,agent_id,payload,status,created) VALUES(?,?,?,?,?)",
               (_uid(human), None, "{}", "rejected", time.time()))
    rid = _run(human, status="pending_approval", sent={"mode": "queue", "approval_id": aid})
    pk = _pk(human, rid)
    assert pk["status"] == "ready" and pk["buffer_sync"]["event"] == "rejected"


def test_mcp_get_run_syncs_the_same_way(human):
    rid = _run(human, **_sent("bp_gone"))
    out = mcp.call_tool(human.app.state.studio, Actor(_uid(human)), "get_run", {"run_id": rid})
    assert out["result"]["packages"][0]["status"] == "ready"
    schema = next(t for t in mcp.TOOLS if t["name"] == "get_run")["inputSchema"]["properties"]
    assert "refresh_buffer" in schema


# ------------------------------------------------------------------ music Buffer can't load
def test_a_track_buffer_cant_load_is_never_saved(human, fake):
    fake.unloadable_audio.add(GIFT["audio_id"])
    rid = _run(human)
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": GIFT})
    assert r.status_code == 409 and "Couldn't load track" in r.json()["error"] and "Gifts to your future self" in r.json()["error"]
    assert "ig_audio" not in (_saved(human, rid).get("options") or {})
    ok = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": {**GIFT, "audio_id": "101953222865674"}})
    assert ok.status_code == 200, ok.text


def test_a_saved_track_buffer_no_longer_loads_is_not_sent(human, fake):
    rid = _run(human, options={"ig_type": "reel", "ig_audio": {**GIFT, "music_volume": 100, "original_volume": 100}})
    fake.unloadable_audio.add(GIFT["audio_id"])
    human.app.state.studio.media_reach = lambda urls: []
    fake.media[VIDEO] = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    n = len(fake.created)
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "draft", "override_warnings": True})
    assert r.status_code == 409 and "Couldn't load track" in r.json()["error"]
    assert len(fake.created) == n  # nothing reached Buffer


def test_only_buffer_answers_about_the_track_count():
    from types import SimpleNamespace
    from studio import audio_check
    from studio.buffer_api import BufferError

    def studio_raising(msg):
        def fn(uid, ch, aid):
            raise BufferError(msg)
        return SimpleNamespace(buffer=SimpleNamespace(instagram_audio=fn))
    a = Actor("u1")
    for msg in ("Buffer could not complete the request (FORBIDDEN: Not authorized)",
                "Buffer could not complete the request (UNAUTHENTICATED: sign in)",
                "Buffer could not complete the request (RATE_LIMIT_EXCEEDED: slow down)",
                "Buffer could not complete the request (502: )"):
        assert audio_check.check_track(studio_raising(msg), a, "ch", GIFT)["state"] == "unknown", msg
    gone = "Buffer could not complete the request (NOT_FOUND: Audio asset is not available to generate)"
    assert audio_check.check_track(studio_raising(gone), a, "ch", GIFT)["state"] == "unavailable"


def test_the_check_can_be_turned_off(human, fake, monkeypatch):
    monkeypatch.setenv("HQ_AUDIO_CHECK", "0")
    fake.unloadable_audio.add(GIFT["audio_id"])
    rid = _run(human)
    assert human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": GIFT}).status_code == 200


def test_the_brain_answers_music_questions_from_the_post():
    hits = ["i added the music by instagram didnt pick?", "the music is not applied to the video", "why is there no music",
            "it didn't apply the music", "music isn't playing"]
    for t in hits:
        assert Brain.MUSIC_STATUS_RE.search(t), t
    for t in ("add lofi music", "use some upbeat music", "make the hook stronger"):
        assert not Brain.MUSIC_STATUS_RE.search(t), t
    assert Brain.MUSIC_BED_RE.search("add a cinematic music bed") and not Brain.MUSIC_BED_RE.search("add lofi music")


def test_the_brain_music_reply_says_where_the_music_is(human, fake):
    h = human.app.state.house
    rid = _run(human, options={"ig_type": "reel", "ig_audio": {**GIFT, "music_volume": 100, "original_volume": 100}})
    a = Actor(_uid(human))
    out = h.brain._music_status(a, rid)
    msg = out["result"]["thread"][-1]["text"]
    assert "Gifts to your future self" in msg and "Instagram adds it when it posts" in msg and "Buffer can load it" in msg
    fake.unloadable_audio.add(GIFT["audio_id"])
    rid2 = _run(human, options={"ig_type": "reel", "ig_audio": {**GIFT, "music_volume": 100, "original_volume": 100}})
    out = h.brain._music_status(a, rid2)
    assert "Couldn't load track" in out["result"]["thread"][-1]["text"] and "took it off" in out["result"]["thread"][-1]["text"]
    assert "ig_audio" not in (_saved(human, rid2).get("options") or {})


def test_ui_shows_buffer_state_and_the_way_back():
    js = (ST / "house.js").read_text()
    for tid in ("pkg-buffer-sync", "pkg-buffer-check", "pkg-buffer-error", "pkg-buffer-edited"):
        assert f'data-testid="{tid}"' in js
    assert "refresh_buffer=1" in js and "Deleted in Buffer" in js
    assert 'n.buffer_check === "ok"' in js  # "Checked with Buffer" only for a finished check (missing = not finished)


def test_the_card_can_play_the_track_with_the_reel(human, fake):
    """"I selected the music but it didn't get attached to the video": the track stays out of the file (Instagram adds it
    when it posts), and the card plays Instagram's preview with the Reel. The preview link is kept, never sent to Buffer."""
    from studio.actions import audio_input, clean_audio
    au = clean_audio({**GIFT, "preview_url": "https://video.fbcdn.example/p.mp4"})
    assert au["preview_url"] == "https://video.fbcdn.example/p.mp4" and "preview" not in json.dumps(audio_input(au)).lower()
    assert "preview_url" not in clean_audio({**GIFT, "preview_url": "http://insecure.example/p.mp4"})
    rid = _run(human)
    pk = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": {**GIFT, "preview_url": "https://old.example/p.mp4"}}).json()
    assert pk["options"]["ig_audio"]["preview_url"] == "https://old.example/p.mp4"  # Buffer's check had no newer link
    js, pc = (ST / "reelmusic.js").read_text(), (ST / "pkgcontrols.js").read_text()
    assert "function withVideo" in js and 'data-testid="music-how"' in pc and 'data-testid="music-with-video"' in pc


def test_a_lookup_that_fails_leaves_the_check_incomplete():
    from studio import post_sync
    res = {"packages": [{"channel_id": "c", "status": "sent_to_buffer",
                         "sent": {"mode": "draft", "buffer_post_id": "bp_x", "at": time.time() - 3600}}]}
    index = {"posts": {}, "sent_complete": False}  # the sent list was full: absence proves nothing

    def boom(pid):
        raise RuntimeError("Buffer busy")
    assert not post_sync.reconcile(None, Actor("u1"), res, index, boom)
    assert index["incomplete"] and res["packages"][0]["status"] == "sent_to_buffer"
