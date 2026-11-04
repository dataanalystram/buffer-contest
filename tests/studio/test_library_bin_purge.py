"""Empty bin / Delete permanently (owner bug, live 3 Nov 2026): Ram emptied the Bin, refreshed, and the items showed again;
another refresh showed nothing. Root cause: the purge ran ~6 autocommitted statements per item (147 for 30 items) against
the remote Postgres, one by one, while GETs on other threads interleaved with them, so a refresh mid-purge read half a
Bin; and after the purge the Library's backfill re-registered the purged runs' other renders (a cover, another
channel's picture) as brand-new Library items. Pins: the purge is one transaction (a reader sees the Bin before or after,
never half), a handful of statements whatever the count, removes the rows AND the bytes (media / chunks) before it
answers, keeps media a Buffer-scheduled post uses, never brings a purged run's renders back, answers carry the storage
meter counted from the rows, and the Library / Bin GETs are never cached."""
import json
import threading
import time

from studio.actions import Actor

from .test_agent_core import jpeg
from .test_makepost_library import owner, upload, uid_of  # noqa: F401  (owner is a fixture)


def _gen_with_cover(owner, uid, i, scheduled=False):
    """A run that rendered a picture (registered in the Library) and a cover for another channel (never registered)."""
    h, st, a = owner.app.state.house, owner.app.state.store, Actor(uid)
    tok = h.put_media(a, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo"})
    cov = h.put_media(a, jpeg(320, 400), "cover.jpg", kind="render", meta={"type": "photo"})
    pk = [{"channel_id": "c", "service": "instagram", "format": "photo", "media": [{"token": tok, "url": h.media_url(tok), "type": "photo"}]},
          {"channel_id": "d", "service": "linkedin", "format": "photo", "media": [{"token": cov, "url": h.media_url(cov), "type": "photo"}]}]
    if scheduled:
        pk[0]["sent"] = {"mode": "schedule", "buffer_post_id": "p1"}
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               (uid, "agent", json.dumps({"prompt": f"story {i}"}), "ready", "[]",
                json.dumps({"story": {"headline": f"Story {i}"}, "packages": pk}), time.time() - i, time.time() - i))
    return h.library.register_generated(a, [{"token": tok, "type": "photo"}], "photo", rid), tok, cov


def _seed(owner, n_up=10, n_gen=16):
    uid = uid_of(owner)
    ups = [upload(owner, jpeg(600 + i, 750), f"up {i}.jpg")["id"] for i in range(n_up)]
    gens = [_gen_with_cover(owner, uid, i) for i in range(n_gen)]
    ids = ups + [g[0] for g in gens]
    r = owner.post("/api/library/bulk-delete", json={"ids": ids, "mode": "bin", "force": True}).json()
    assert sorted(r["deleted"]) == sorted(ids)
    return uid, ids, gens


def _n(st, sql, args=()):
    return st.q(sql, args)[0]["n"]


def test_empty_bin_removes_rows_and_bytes_before_answering_and_meter_drops(owner):
    uid, ids, gens = _seed(owner)
    st = owner.app.state.store
    b = owner.get("/api/library/bin")
    assert b.json()["total"] == 26 and b.json()["used"] > 0
    used0 = b.json()["used"]
    r = owner.post("/api/library/bin/purge", json={"all": True}).json()
    assert sorted(r["purged"]) == sorted(ids) and r["refused"] == []
    assert r["used"] < used0 and r["used"] == 0 and r["quota"] > 0  # the meter, counted from the rows now
    # gone before the answer: rows, chunks, media rows (live or binned token), library_media
    assert _n(st, "SELECT COUNT(*) n FROM library_items WHERE user_id=?", (uid,)) == 0
    assert _n(st, f"SELECT COUNT(*) n FROM library_chunks WHERE item_id IN ({','.join('?' * len(ids))})", tuple(ids)) == 0
    assert _n(st, f"SELECT COUNT(*) n FROM library_media WHERE item_id IN ({','.join('?' * len(ids))})", tuple(ids)) == 0
    for _, tok, _ in gens:
        assert _n(st, "SELECT COUNT(*) n FROM media WHERE token IN (?,?)", (tok, "trash~" + tok)) == 0
    assert owner.get("/api/library/bin").json()["total"] == 0


def test_a_purged_runs_other_renders_never_come_back(owner):
    _seed(owner)
    owner.post("/api/library/bin/purge", json={"all": True})
    for _ in range(3):  # every refresh: the Library stays empty (the backfill used to re-add 16 covers as new items)
        d = owner.get("/api/library").json()
        assert d["items"] == [] and d["bin_count"] == 0 and d["used"] == 0


def test_a_refresh_during_the_purge_sees_the_bin_before_or_after_never_half(owner):
    uid, ids, _ = _seed(owner)
    h, st = owner.app.state.house, owner.app.state.store
    lib, a = h.library, Actor(uid)
    real_x = st.x

    def slow_x(sql, args=()):  # a remote database: every statement takes a while
        time.sleep(0.01)
        return real_x(sql, args)
    st.x = slow_x
    seen, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            seen.append(lib.trash(a)["total"])
            time.sleep(0.002)
    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.02)
    lib.purge(a, everything=True)
    stop.set()
    t.join()
    st.x = real_x
    assert seen and set(seen) <= {26, 0}, sorted(set(seen))
    assert lib.trash(a)["total"] == 0


def test_purge_is_a_handful_of_statements_whatever_the_count(owner):
    uid, ids, _ = _seed(owner)
    st = owner.app.state.store
    n, rq, rx = [0], st.q, st.x
    st.q = lambda s, a=(): (n.__setitem__(0, n[0] + 1), rq(s, a))[1]
    st.x = lambda s, a=(): (n.__setitem__(0, n[0] + 1), rx(s, a))[1]
    owner.post("/api/library/bin/purge", json={"all": True})
    st.q, st.x = rq, rx
    assert n[0] <= 20, n[0]  # was ~147 for these 26 items


def test_media_a_scheduled_post_uses_is_kept(owner):
    uid = uid_of(owner)
    keep, tok, _ = _gen_with_cover(owner, uid, 0, scheduled=True)
    other = upload(owner, jpeg(640, 800), "x.jpg")["id"]
    owner.post("/api/library/bulk-delete", json={"ids": [keep, other], "mode": "bin", "force": True})
    r = owner.post("/api/library/bin/purge", json={"all": True}).json()
    assert r["purged"] == [other] and [x["id"] for x in r["refused"]] == [keep]
    assert owner.get("/api/library/bin").json()["total"] == 1
    assert _n(owner.app.state.store, "SELECT COUNT(*) n FROM media WHERE token=?", ("trash~" + tok,)) == 1


def test_meter_moves_with_upload_delete_restore(owner):
    up = upload(owner, jpeg(700, 900), "m.jpg")
    assert up["usage"]["used"] >= up["size"] > 0  # the upload answer carries the meter
    used = owner.get("/api/library").json()["used"]
    r = owner.delete(f"/api/library/{up['id']}").json()
    assert r["used"] == used  # a Bin move keeps the bytes (and the count) until purged
    r = owner.post(f"/api/library/{up['id']}/restore").json()
    assert r["restored"] and r["used"] == used
    r = owner.delete(f"/api/library/{up['id']}?mode=permanent").json()
    assert r["used"] == used - up["size"]
    assert owner.get("/api/library").json()["used"] == used - up["size"]


def test_library_and_bin_answers_are_never_cached(owner):
    upload(owner)
    for url in ("/api/library", "/api/library?per=12", "/api/library/bin", "/api/library/trash"):
        cc = owner.get(url).headers.get("cache-control", "")
        assert "no-store" in cc, (url, cc)
    assert "no-store" in owner.post("/api/library/bin/purge", json={"all": True}).headers.get("cache-control", "")


def test_auto_purge_after_seven_days_is_batched_too(owner):
    uid, ids, _ = _seed(owner, n_up=3, n_gen=2)
    st = owner.app.state.store
    lib = owner.app.state.house.library
    assert lib.purge_trash(days=-1) == 5
    assert _n(st, "SELECT COUNT(*) n FROM library_items WHERE user_id=?", (uid,)) == 0
    assert owner.get("/api/library").json()["items"] == []


def test_store_tx_commits_together_or_not_at_all(app):
    """Store.tx() on the production store too (studio-ci runs this on Postgres): a failure inside rolls every write back."""
    st, lib = app.state.store, app.state.house.library
    a = Actor("acc_tx")
    ids = [lib.save_bytes(a, jpeg(600 + i, 700), f"t{i}.jpg")["id"] for i in range(3)]
    tok = app.state.house.put_media(a, jpeg(800, 1000), "card.jpg", kind="render", meta={"type": "photo"})
    gen = lib.register_generated(a, [{"token": tok, "type": "photo"}], "photo", None)
    try:
        with st.tx():
            st.x("DELETE FROM library_items WHERE id=?", (ids[0],))
            with st.tx():  # an inner unit joins the outer one
                st.x("DELETE FROM library_items WHERE id=?", (ids[1],))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert _n(st, "SELECT COUNT(*) n FROM library_items WHERE user_id=?", ("acc_tx",)) == 4
    lib.bulk_delete(a, ids + [gen], "bin", True)
    assert _n(st, "SELECT COUNT(*) n FROM media WHERE token=?", ("trash~" + tok,)) == 1  # the batched token move
    r = lib.purge(a, everything=True)
    assert sorted(r["purged"]) == sorted(ids + [gen]) and r["used"] == 0
    assert _n(st, "SELECT COUNT(*) n FROM media WHERE token IN (?,?)", (tok, "trash~" + tok)) == 0
    assert _n(st, "SELECT COUNT(*) n FROM library_items WHERE user_id=?", ("acc_tx",)) == 0
    assert _n(st, f"SELECT COUNT(*) n FROM library_chunks WHERE item_id IN ({','.join('?' * len(ids))})", tuple(ids)) == 0
