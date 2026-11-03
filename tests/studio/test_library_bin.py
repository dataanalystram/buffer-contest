"""Library Bin, delete choice, bulk delete, Create draft and View run (owner ask, 3 Nov 2026). UI = REST = MCP.

Pins: a delete defaults to the Bin (7 days, restorable, days_left shown); a permanent delete removes the file and its row
(chunks / media rows gone) and can't be undone; a permanent delete of media a post scheduled in Buffer uses is refused
outright (force can't override), a Bin move of it warns; bulk delete checks each item on its own; Restore selected,
Delete selected permanently and Empty bin; the auto purge after 7 days; /healthz counts binned / owner-deleted files apart
from missing ones; Create draft prefills the run's per-channel captions and saves Buffer DRAFTS through preview -> submit;
items made by a run carry run_id (View run), others don't; the Library answer carries bin_count and pages."""
from studio import library

from .test_agent_core import TWEET, jpeg
from .test_makepost_library import owner, upload, uid_of  # noqa: F401  (owner is a fixture)


def _run_with(owner, up, prompt=f"post this on all channels {TWEET}"):
    return owner.post("/api/agent/runs", json={"prompt": prompt, "media_token": up["media_token"]}).json()


def _mark_sent(owner, rid, mode):
    from studio.actions import Actor
    h = owner.app.state.house
    run = h.run(Actor(uid_of(owner)), rid)
    for p in run["result"].get("packages") or []:
        p["sent"], p["status"] = {"mode": mode, "buffer_post_id": "p1"}, "sent_to_buffer"
    h._save(rid, run["steps"], run["result"])


def _counts(owner, iid):
    st = owner.app.state.store
    return (st.q("SELECT COUNT(*) n FROM library_items WHERE id=?", (iid,))[0]["n"],
            st.q("SELECT COUNT(*) n FROM library_chunks WHERE item_id=?", (iid,))[0]["n"])


def test_delete_defaults_to_bin_with_days_left_and_restore(owner):
    up = upload(owner)
    lst = owner.get("/api/library").json()
    assert lst["bin_count"] == 0 and lst["bin_days"] == 7
    r = owner.delete(f"/api/library/{up['id']}").json()
    assert r["mode"] == "bin" and r["in_trash_days"] == 7
    lst = owner.get("/api/library").json()
    assert lst["bin_count"] == 1 and up["id"] not in [i["id"] for i in lst["items"]]
    b = owner.get("/api/library/bin").json()
    it = b["items"][0]
    assert it["id"] == up["id"] and it["days_left"] == 7 and b["total"] == 1
    assert owner.get(it["url"]).status_code == 200  # the owner still sees the binned file (thumbnail / preview)
    assert owner.get("/m/" + up["media_token"]).status_code == 404  # nothing else does
    assert owner.post("/api/library/bin/restore", json={"ids": [up["id"]]}).json()["restored"] == [up["id"]]
    assert owner.get("/m/" + up["media_token"]).status_code == 200 and owner.get("/api/library").json()["bin_count"] == 0


def test_permanent_delete_removes_file_and_row(owner):
    up = upload(owner)
    r = owner.delete(f"/api/library/{up['id']}?mode=permanent").json()
    assert r["deleted"] and r["mode"] == "permanent" and "restore" not in r
    assert _counts(owner, up["id"]) == (0, 0)
    assert owner.get("/m/" + up["media_token"]).status_code == 404
    assert owner.get("/api/library/bin").json()["items"] == []
    assert owner.post(f"/api/library/{up['id']}/restore", json={}).status_code == 404
    other = upload(owner, jpeg(640, 640), "o.jpg")
    assert owner.delete(f"/api/library/{other['id']}?mode=nuke").status_code == 400


def test_permanent_delete_of_generated_media_drops_media_rows(owner):
    from studio.actions import Actor
    h = owner.app.state.house
    a = Actor(uid_of(owner))
    toks = [h.put_media(a, jpeg(400, 500), f"s{i}.jpg", kind="render", meta={"type": "photo"}) for i in range(2)]
    iid = h.library.register_generated(a, [{"token": t, "url": f"/m/{t}", "type": "photo"} for t in toks], "carousel", None)
    owner.delete(f"/api/library/{iid}")  # to the Bin first, then for good from the Bin
    assert owner.get(f"/api/library/bin/{iid}/file?i=1").status_code == 200
    assert owner.post("/api/library/bin/purge", json={"ids": [iid]}).json()["purged"] == [iid]
    st = owner.app.state.store
    assert st.q("SELECT COUNT(*) n FROM media WHERE token IN (?,?,?,?)", tuple(toks) + tuple(library.TRASH_TOKEN + t for t in toks))[0]["n"] == 0


def test_scheduled_post_media_refuses_permanent_delete_and_warns_on_bin(owner):
    up = upload(owner)
    rid = _run_with(owner, up)["id"]
    _mark_sent(owner, rid, "schedule")
    perm = owner.delete(f"/api/library/{up['id']}?mode=permanent&force=1")
    assert perm.status_code == 409 and "scheduled in Buffer" in perm.json()["error"] and "can't be deleted permanently" in perm.json()["error"]
    assert _counts(owner, up["id"])[0] == 1
    warn = owner.delete(f"/api/library/{up['id']}")
    assert warn.status_code == 409 and "scheduled in Buffer" in warn.json()["error"]
    assert owner.delete(f"/api/library/{up['id']}?force=1").json()["mode"] == "bin"
    # from the Bin too: permanent removal is refused while the scheduled post uses it (Empty bin skips it)
    out = owner.post("/api/library/bin/purge", json={"all": True}).json()
    assert out["purged"] == [] and out["refused"][0]["id"] == up["id"] and _counts(owner, up["id"])[0] == 1
    r = owner.post("/api/library/bulk-delete", json={"ids": [up["id"]], "mode": "permanent", "force": True})
    assert r.status_code == 200  # the item is in the Bin now: bulk delete reports it as not found, deletes nothing
    assert _counts(owner, up["id"])[0] == 1


def test_buffer_draft_media_permanent_delete_goes_ahead(owner):
    up = upload(owner)
    rid = _run_with(owner, up)["id"]
    _mark_sent(owner, rid, "draft")
    assert owner.delete(f"/api/library/{up['id']}?mode=permanent").status_code == 200  # a Buffer draft isn't a scheduled post
    assert _counts(owner, up["id"]) == (0, 0)


def test_bulk_delete_bin_and_permanent_each_item_checked(owner):
    a, b = upload(owner, jpeg(500, 500), "a.jpg"), upload(owner, jpeg(510, 510), "b.jpg")
    c = upload(owner, jpeg(520, 520), "c.jpg")
    _run_with(owner, c)  # c is used by a post that hasn't gone out
    r = owner.post("/api/library/bulk-delete", json={"ids": [a["id"], b["id"], c["id"]], "mode": "bin"}).json()
    assert sorted(r["deleted"]) == sorted([a["id"], b["id"]]) and r["refused"][0]["id"] == c["id"] and r["refused"][0]["needs_force"]
    assert owner.get("/api/library").json()["bin_count"] == 2
    r = owner.post("/api/library/bulk-delete", json={"ids": [c["id"]], "mode": "permanent", "force": True}).json()
    assert r["deleted"] == [c["id"]] and _counts(owner, c["id"]) == (0, 0)
    assert owner.post("/api/library/bulk-delete", json={"ids": [], "mode": "bin"}).status_code == 400
    assert owner.post("/api/library/bulk-delete", json={"ids": list(range(1, 300)), "mode": "bin"}).status_code == 400
    assert owner.post("/api/library/bulk-delete", json={"ids": [a["id"]], "mode": "x"}).status_code == 400


def test_bin_restore_selected_purge_selected_and_empty(owner):
    ids = [upload(owner, jpeg(500 + i * 10, 500), f"x{i}.jpg")["id"] for i in range(4)]
    owner.post("/api/library/bulk-delete", json={"ids": ids, "mode": "bin"})
    pg = owner.get("/api/library/bin?per=2").json()
    assert pg["total"] == 4 and len(pg["items"]) == 2
    assert owner.post("/api/library/bin/restore", json={"ids": [ids[0], 99999]}).json() == {"restored": [ids[0]], "missing": [99999]}
    assert owner.post("/api/library/bin/purge", json={"ids": [ids[1]]}).json()["purged"] == [ids[1]]
    p = owner.post("/api/library/bin/purge", json={"all": True}).json()
    assert sorted(p["purged"]) == sorted(ids[2:]) and owner.get("/api/library/bin").json()["items"] == []
    assert [i["id"] for i in owner.get("/api/library").json()["items"]] == [ids[0]]


def test_auto_purge_after_seven_days_and_days_left(owner, monkeypatch):
    up = upload(owner)
    owner.delete(f"/api/library/{up['id']}")
    lib = owner.app.state.house.library
    assert lib.purge_trash() == 0  # still inside 7 days
    now = library._now()
    monkeypatch.setattr(library, "_now", lambda: now + 6.5 * 86400)
    assert owner.get("/api/library/bin").json()["items"][0]["days_left"] == 1
    monkeypatch.setattr(library, "_now", lambda: now + 7 * 86400 + 5)
    assert lib.purge_trash() == 1 and _counts(owner, up["id"]) == (0, 0)


def test_healthz_does_not_count_binned_or_owner_deleted_files_as_missing(owner):
    for u in (upload(owner), upload(owner, jpeg(700, 700), "p.jpg")):
        _run_with(owner, u)
    h = owner.app.state.house
    base = h.media_audit()["files_missing"]
    gen = [i for i in owner.get("/api/library").json()["items"] if i["source"] == "generated"]
    assert len(gen) >= 2
    owner.delete(f"/api/library/{gen[0]['id']}?force=1")
    assert owner.delete(f"/api/library/{gen[1]['id']}?force=1&mode=permanent").status_code == 200
    m = h.media_audit()
    assert m["files_missing"] == base and m["files_in_bin"] >= 1 and m["files_deleted_by_owner"] >= 1
    hz = owner.get("/healthz").json()["media"]
    assert hz["missing_files"] == base and hz["files_in_bin"] >= 1


def test_view_run_link_only_for_items_a_run_made(owner):
    up = upload(owner)
    _run_with(owner, up)
    items = owner.get("/api/library").json()["items"]
    mine = next(i for i in items if i["id"] == up["id"])
    assert mine["run_id"] is None  # an upload: no "View run" (its posts are under Used in)
    gen = [i for i in items if i["source"] == "generated"]
    assert gen and all(i["run_id"] for i in gen)
    assert owner.get(f"/api/agent/runs/{gen[0]['run_id']}").status_code == 200


def test_create_draft_prefills_per_channel_captions_and_saves_drafts(owner):
    up = upload(owner)
    _run_with(owner, up)
    gen = next(i for i in owner.get("/api/library").json()["items"] if i["source"] == "generated")
    pre = owner.get(f"/api/library/{gen['id']}/draft").json()
    assert pre["run_id"] == gen["run_id"] and pre["media_urls"] and pre["media"][0]["type"] == "video"
    ticked = [c for c in pre["channels"] if c["selected"]]
    assert ticked and all(c["from_run"] and c["text"] for c in ticked)
    n0 = len(owner.fake.created)
    pick = ticked[0]
    out = owner.post(f"/api/library/{gen['id']}/draft", json={"channels": [{"channel_id": pick["channel_id"]}]}).json()
    assert out["saved"] == 1, out
    assert len(owner.fake.created) == n0 + 1 and owner.fake.created[-1].get("saveToDraft") is True
    assert owner.fake.created[-1]["text"] == pick["text"]
    bad = owner.post(f"/api/library/{gen['id']}/draft", json={"channels": [{"channel_id": "nope"}]}).json()
    assert bad["saved"] == 0 and "isn't connected" in bad["results"][0]["error"]
    assert owner.post(f"/api/library/{gen['id']}/draft", json={"channels": []}).status_code == 400


def test_library_pages_and_mcp_parity(owner):
    for i in range(3):
        upload(owner, jpeg(600 + i * 10, 600), f"m{i}.jpg")
    pg = owner.get("/api/library?per=2&offset=2").json()
    assert pg["total"] >= 3 and len(pg["items"]) <= 2 and pg["offset"] == 2
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    def call(name, args=None):
        return owner.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name, "arguments": args or {}}}).json()["result"]
    names = {t["name"] for t in owner.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"list_library_bin", "restore_library_items", "purge_library_bin", "bulk_delete_library", "draft_from_library"} <= names
    ids = [i["id"] for i in call("list_library")["structuredContent"]["items"]]
    r = call("bulk_delete_library", {"ids": ids[:2], "mode": "bin"})
    assert not r["isError"] and len(r["structuredContent"]["deleted"]) == 2
    assert len(call("list_library_bin")["structuredContent"]["items"]) == 2
    assert call("restore_library_items", {"ids": ids[:1]})["structuredContent"]["restored"] == ids[:1]
    assert call("purge_library_bin", {"ids": ids[1:2]})["structuredContent"]["purged"] == ids[1:2]
    assert not call("delete_library_item", {"item_id": ids[0], "mode": "permanent"})["isError"]
    pre = call("draft_from_library", {"item_id": ids[2]})
    assert not pre["isError"] and pre["structuredContent"]["channels"]


def test_bin_needs_auth_and_csrf(owner):
    up = upload(owner)
    owner.delete(f"/api/library/{up['id']}")
    from fastapi.testclient import TestClient
    anon = TestClient(owner.app, base_url="https://hq.test")
    for m, u in [("get", "/api/library/bin"), ("get", f"/api/library/bin/{up['id']}/file"), ("post", "/api/library/bin/restore"),
                 ("post", "/api/library/bin/purge"), ("post", "/api/library/bulk-delete"), ("get", f"/api/library/{up['id']}/draft")]:
        r = getattr(anon, m)(u, **({"json": {"ids": [up["id"]]}} if m == "post" else {}))
        assert r.status_code in (401, 403), (u, r.status_code)
    no_csrf = TestClient(owner.app, base_url="https://hq.test", cookies=owner.cookies)
    assert no_csrf.post("/api/library/bin/purge", json={"all": True}).status_code in (401, 403)
    assert _counts(owner, up["id"])[0] == 1
