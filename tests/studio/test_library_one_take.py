"""The Library's One take filter and the one Bin, REST side (owner ask, 5 Nov 2026; the browser side is
tests/studio/test_library_one_take_e2e.py and tests/studio/test_take_bin_e2e.py).

Pins: GET /api/library?created_by=one_take lists only what One take made or was given (the clips its takes rendered and the
videos uploaded to it), filtered on the server and scoped to the caller (an item pointing at someone else's take run is
not One take's); a bad value answers 400 naming the allowed ones; a take or clip in the Bin leaves the Library list (its
files aren't served) and is counted in bin_count; each clip item carries take_clip so a Library delete goes to the take's
Bin; MCP list_library offers the same filter."""
import json
import time

from studio import house, library

from .conftest import mcp_call, agent_token
from .test_agent_core import jpeg
from .test_makepost_library import MP4

A = house.Actor("acc_1")


def _take(st, uid, clips, status="ready", name="Kickoff talk"):
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (uid, "take", json.dumps({"name": name, "channels": []}), status, "[]", json.dumps({"clips": clips}), time.time(), time.time()))


def _seed(app):
    st, h = app.state.store, app.state.house
    t1 = h.put_media(A, MP4, "c1.mp4", kind="render", meta={"type": "video"})
    t2 = h.put_media(A, MP4 + b"2", "c2.mp4", kind="render", meta={"type": "video"})
    rid = _take(st, "acc_1", [{"id": "c1", "status": "ready", "token": t1, "hook": "Ship it small"},
                              {"id": "c2", "status": "ready", "token": t2, "hook": "Why now"}])
    # an agent run's picture: made by the agent, not by One take
    tok = h.put_media(A, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo"})
    agent_run = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                     ("acc_1", "agent", json.dumps({"prompt": "Launch"}), "ready", "[]",
                      json.dumps({"packages": [{"channel_id": "ch_li", "media": [{"token": tok, "type": "photo"}]}]}), time.time(), time.time()))
    gen = h.library.register_generated(A, [{"token": tok, "type": "photo"}], "photo", agent_run)
    talk = h.library.save_bytes(A, MP4 + b"talk", "talk.mp4", "take")["id"]     # a video uploaded to One take
    mine = h.library.save_bytes(A, jpeg(640, 640), "photo.jpg")["id"]            # a plain upload
    # someone else's take run, and an item of acc_1 that claims that run: never One take's for acc_1
    other = _take(st, "acc_other", [], name="Not yours")
    t3 = h.put_media(A, jpeg(500, 500, (9, 9, 9)), "odd.jpg", kind="render", meta={"type": "photo"})
    odd = h.library.register_generated(A, [{"token": t3, "type": "photo"}], "photo", None)
    st.x("UPDATE library_items SET run_id=? WHERE id=?", (other, odd))
    return {"rid": rid, "t1": t1, "t2": t2, "gen": gen, "talk": talk, "mine": mine, "odd": odd, "other": other}


def test_the_one_take_filter_lists_only_one_takes_items_owner_scoped(human, app):
    s = _seed(app)
    lst = human.get("/api/library").json()       # the backfill registers the take's clips as Library items
    by_tok = {i["media_token"]: i for i in lst["items"]}
    assert s["t1"] in by_tok and s["t2"] in by_tok
    r = human.get("/api/library?created_by=one_take")
    assert r.status_code == 200, r.text
    d = r.json()
    ids = {i["id"] for i in d["items"]}
    assert ids == {by_tok[s["t1"]]["id"], by_tok[s["t2"]]["id"], s["talk"]}, d["items"]
    assert s["gen"] not in ids and s["mine"] not in ids and s["odd"] not in ids        # nor someone else's take run
    assert all(i["one_take"] for i in d["items"]) and d["filters"]["created_by"] == "one_take"
    clip = next(i for i in d["items"] if i["media_token"] == s["t1"])
    assert clip["take_clip"] == {"take_id": s["rid"], "clip_id": "c1"} and clip["run_id"] == s["rid"]   # View run, delete to its Bin
    assert "take_clip" not in next(i for i in d["items"] if i["id"] == s["talk"])
    assert not next(i for i in lst["items"] if i["id"] == s["odd"])["one_take"]
    # combinable with the other filters, case-insensitive like them; the page and the total count only One take's
    assert {i["id"] for i in human.get("/api/library?created_by=ONE_TAKE&type=image").json()["items"]} == set()
    assert human.get("/api/library?created_by=one_take&per=2").json()["total"] == 3


def test_a_bad_created_by_is_400_naming_the_allowed_values(human):
    for bad in ("onetake", "take", "one take", "1", "agent,you"):
        r = human.get("/api/library", params={"created_by": bad})
        assert r.status_code == 400, (bad, r.text)
        assert "one_take" in r.json()["error"]
    assert human.get("/api/library?created_by=all").status_code == 200


def test_a_binned_take_or_clip_leaves_the_library_and_is_counted_in_its_bin(human, app):
    s = _seed(app)
    human.get("/api/library")
    assert human.get("/api/library").json()["bin_count"] == 0
    assert human.delete(f"/api/takes/{s['rid']}/clips/c1").status_code == 200
    d = human.get("/api/library").json()
    toks = {i["media_token"] for i in d["items"]}
    assert s["t1"] not in toks and s["t2"] in toks and d["bin_count"] == 1
    assert s["t1"] not in {i["media_token"] for i in human.get("/api/library?created_by=one_take").json()["items"]}
    assert human.post(f"/api/takes/{s['rid']}/clips/c1/restore", json={}).status_code == 200
    d = human.get("/api/library").json()
    assert s["t1"] in {i["media_token"] for i in d["items"]} and d["bin_count"] == 0
    # the whole take: both clips leave, one entry in the Bin
    assert human.delete(f"/api/takes/{s['rid']}").status_code == 200
    d = human.get("/api/library").json()
    toks = {i["media_token"] for i in d["items"]}
    assert s["t1"] not in toks and s["t2"] not in toks and d["bin_count"] == 1
    # a Library item in its Bin counts too
    human.delete(f"/api/library/{s['mine']}")
    assert human.get("/api/library").json()["bin_count"] == 2


def test_take_index_skips_a_malformed_run_and_never_reads_another_persons(app):
    """Every take run is read, however many newer runs there are; another person's never."""
    st = app.state.store
    st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
         ("acc_1", "take", "{}", "ready", "[]", "not json", time.time(), time.time()))
    st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
         ("acc_1", "take", "{}", "ready", "[]", json.dumps({"clips": [{"id": "c9", "status": "binned", "token": 7}]}), time.time(), time.time()))
    _take(st, "acc_other", [{"id": "c1", "status": "binned", "token": "theirs.mp4"}], status="ready")
    old = _take(st, "acc_1", [{"id": "c1", "status": "binned", "token": "old.mp4"}], name="An old take")
    for i in range(205):  # 205 newer runs: the old take is still read
        st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
             ("acc_1", "agent", "{}", "ready", "[]", "{}", time.time(), time.time()))
    lib = app.state.house.library
    idx = lib._take_index(lib._runs(A))
    assert idx["bin_n"] == 2 and "theirs.mp4" not in idx["binned"] and "old.mp4" in idx["binned"] and idx["clips"] == {}
    assert old in idx["runs"]


def test_mcp_list_library_offers_the_one_take_filter(human, app):
    s = _seed(app)
    tool = next(t for t in library.TOOLS if t["name"] == "list_library")
    assert "one_take" in tool["inputSchema"]["properties"]["created_by"]["enum"] and "one_take" in tool["description"]
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "list_library", {"created_by": "one_take"})
    assert not res["isError"], out
    assert s["talk"] in {i["id"] for i in out["items"]} and all(i["one_take"] for i in out["items"])


def test_binned_takes_are_excluded_before_the_limit_so_a_page_is_never_short(human, app):
    """Binned take/clip tokens are cut in SQL, before LIMIT: the newest rows being binned never shrinks the page."""
    st, h = app.state.store, app.state.house
    old = [h.library.save_bytes(A, jpeg(320 + i, 320), f"old{i}.jpg")["id"] for i in range(3)]
    toks = [h.put_media(A, MP4 + bytes([i]), f"b{i}.mp4", kind="render", meta={"type": "video"}) for i in range(3)]
    _take(st, "acc_1", [{"id": f"c{i}", "status": "binned", "token": t} for i, t in enumerate(toks)], name="Binned clips")
    lib = h.library
    lib.backfill(A)
    for t in toks:  # the binned clips' rows are the newest in the Library
        st.x("UPDATE library_items SET created=? WHERE user_id=? AND token=?", (time.time() + 1000, "acc_1", t))
    out = lib._list(A, limit=3)
    got = [i["id"] for i in out["items"]]
    assert len(got) == 3 and set(got) == set(old), got
    assert not {i["media_token"] for i in out["items"]} & set(toks)


def test_binned_tokens_past_the_sql_cap_widen_the_limit_and_the_page_stays_full(human, app, monkeypatch):
    st, h = app.state.store, app.state.house
    old = [h.library.save_bytes(A, jpeg(330 + i, 330), f"keep{i}.jpg")["id"] for i in range(2)]
    toks = [h.put_media(A, MP4 + b"x" + bytes([i]), f"x{i}.mp4", kind="render", meta={"type": "video"}) for i in range(3)]
    _take(st, "acc_1", [{"id": f"c{i}", "status": "binned", "token": t} for i, t in enumerate(toks)], name="Many binned")
    h.library.backfill(A)
    for t in toks:
        st.x("UPDATE library_items SET created=? WHERE user_id=? AND token=?", (time.time() + 1000, "acc_1", t))
    monkeypatch.setattr(library, "BIN_SQL_MAX", 1)   # two of the three binned tokens fall past the bound list
    got = [i["id"] for i in h.library._list(A, limit=2)["items"]]
    assert got and set(got) == set(old) and len(got) == 2, got


def test_duplicate_rows_of_an_overflow_binned_token_never_shorten_the_page(human, app, monkeypatch):
    """CodeRabbit (#121): a token can have more than one row; the page refills by rows, not by the overflow token count."""
    st, h = app.state.store, app.state.house
    old = [h.library.save_bytes(A, jpeg(340 + i, 340), f"k{i}.jpg")["id"] for i in range(2)]
    tok = h.put_media(A, MP4 + b"dup", "dup.mp4", kind="render", meta={"type": "video"})
    _take(st, "acc_1", [{"id": "c0", "status": "binned", "token": tok}], name="Dup binned")
    h.library.backfill(A)
    row = dict(st.q("SELECT * FROM library_items WHERE user_id=? AND token=?", ("acc_1", tok))[0])
    for _ in range(90):  # many more rows for the same binned token (past any fixed number of refills), all newer
        st.x("INSERT INTO library_items(user_id,name,kind,mime,size,token,storage,source,meta,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
             ("acc_1", row["name"], row["kind"], row["mime"], row["size"], tok, row["storage"], row["source"], row["meta"] or "{}", time.time() + 2000))
    st.x("UPDATE library_items SET created=? WHERE user_id=? AND token=?", (time.time() + 2000, "acc_1", tok))
    monkeypatch.setattr(library, "BIN_SQL_MAX", 0)   # the binned token is past the bound list: skipped as rows are read
    got = [i["id"] for i in h.library._list(A, limit=2)["items"]]
    assert set(got) == set(old) and len(got) == 2, got


def test_a_row_removed_between_refills_never_skips_a_row(human, app, monkeypatch):
    """CodeRabbit (#121): refills continue from the last (created, id) read, not an OFFSET, so a row removed (or added)
    between two reads can't shift the next read past a row that belongs on the page."""
    st, h = app.state.store, app.state.house
    old = [h.library.save_bytes(A, jpeg(360 + i, 360), f"r{i}.jpg")["id"] for i in range(2)]
    tok = h.put_media(A, MP4 + b"gone", "gone.mp4", kind="render", meta={"type": "video"})
    _take(st, "acc_1", [{"id": "c0", "status": "binned", "token": tok}], name="Gone binned")
    h.library.backfill(A)
    row = dict(st.q("SELECT * FROM library_items WHERE user_id=? AND token=?", ("acc_1", tok))[0])
    for _ in range(2):  # three rows of the binned token: the first read (lim + 1 overflow token) takes exactly them
        st.x("INSERT INTO library_items(user_id,name,kind,mime,size,token,storage,source,meta,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
             ("acc_1", row["name"], row["kind"], row["mime"], row["size"], tok, row["storage"], row["source"], row["meta"] or "{}", time.time() + 2000))
    st.x("UPDATE library_items SET created=? WHERE user_id=? AND token=?", (time.time() + 2000, "acc_1", tok))
    monkeypatch.setattr(library, "BIN_SQL_MAX", 0)
    real, reads = st.q, []

    def q(sql, args=()):
        out = real(sql, args)
        if "FROM library_items WHERE" in sql and "ORDER BY created DESC, id DESC" in sql:
            reads.append(len(out))
            if len(reads) == 1:  # between the first read and the refill, one of the binned rows already read is removed
                st.x("DELETE FROM library_items WHERE id=?", (out[0]["id"],))
        return out
    monkeypatch.setattr(st, "q", q)
    got = [i["id"] for i in h.library._list(A, limit=2)["items"]]
    assert len(reads) >= 2 and set(got) == set(old) and len(got) == 2, (got, reads)
