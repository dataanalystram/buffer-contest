"""Audit, 4 Nov 2026: every permanent delete of media / Library files writes the "deleted by owner" record
(library_purged) in the same transaction as the DELETE, so /healthz never counts the owner's own deletes as lost files.
/healthz headlines the UNIQUE missing-file count; missing_refs (one per channel package) is the secondary figure."""
import ast
import pathlib

import pytest

from studio import library
from studio.actions import Actor

from .test_library_bin import _counts
from .test_makepost_library import owner, upload, uid_of  # noqa: F401  (owner is a fixture)
from .test_missing_media import _drop, _run, _tok

STUDIO = pathlib.Path(__file__).resolve().parents[2] / "studio"
# (module, function) -> why it may delete media / Library rows
RECORDS = {("library", "_purge_rows"), ("house", "preview_kit"),
           ("take_bin", "_drop_tokens")}  # One Take's Bin (owned elsewhere): inside TakeBin._purge's tx
EXEMPT = {("library", "_drop"): "an upload that never finished: its token was never served",
          ("store", "delete_user"): "Disconnect and delete: the account's runs go too, nothing is left to count"}


def _deletes():
    out = set()
    for py in STUDIO.glob("*.py"):
        tree = ast.parse(py.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                        any(k in node.value for k in ("DELETE FROM media ", "DELETE FROM library_items ")):
                    out.add((py.stem, fn.name))
                if _is_media_release(node):  # studio/blobstore.release(store, "media", ...) deletes media rows too
                    out.add((py.stem, fn.name))
    return out


def _is_media_release(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "release" and \
        len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "media"


def _deletes_media(tree) -> bool:
    return "DELETE FROM media" in ast.unparse(tree) or any(_is_media_release(n) for n in ast.walk(tree))


def _src(mod, name):
    tree = ast.parse((STUDIO / f"{mod}.py").read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_every_permanent_media_delete_is_recorded_or_exempt_with_a_reason():
    found = _deletes()
    assert found, "the scan found no DELETE statements: the pattern is stale"
    assert found <= RECORDS | set(EXEMPT), sorted(found - RECORDS - set(EXEMPT))  # main: house.preview_kit, purge_media
    for mod, name in RECORDS - {("take_bin", "_drop_tokens")}:
        fn = _src(mod, name)
        txt = ast.unparse(fn)
        assert "record_owner_deletes(" in txt, (mod, name)
        withs = [w for w in ast.walk(fn) if isinstance(w, ast.With) and ".tx()" in ast.unparse(w.items[0].context_expr)]
        assert any(_deletes_media(w) and "record_owner_deletes(" in ast.unparse(w) for w in withs), (mod, name)


def test_a_new_brand_preview_notes_the_old_one_as_deleted_by_owner(owner):
    h, a = owner.app.state.house, Actor(uid_of(owner))
    first = h.preview_kit(a)
    t1 = first["cover"].rsplit("/", 1)[1]
    h.preview_kit(a)
    st = owner.app.state.store
    assert not st.q("SELECT 1 FROM media WHERE token=?", (t1,))
    assert st.q("SELECT user_id FROM library_purged WHERE token=?", (t1,))[0]["user_id"] == a.uid
    assert h.media_removed({t1}) == (set(), {t1})


def test_the_record_and_the_delete_commit_together(owner, monkeypatch):
    h, a = owner.app.state.house, Actor(uid_of(owner))
    t1 = h.preview_kit(a)["cover"].rsplit("/", 1)[1]
    up = upload(owner)

    def boom(*_a, **_k):
        raise RuntimeError("library_purged write failed")
    monkeypatch.setattr(library, "record_owner_deletes", boom)
    with pytest.raises(RuntimeError):
        h.preview_kit(a)
    st = owner.app.state.store
    assert st.q("SELECT 1 FROM media WHERE token=?", (t1,))  # rolled back: the preview is still there
    with pytest.raises(RuntimeError):
        h.library.delete(a, up["id"], force=True, mode="permanent")
    assert _counts(owner, up["id"])[0] == 1  # the Library item survived the failed record
    monkeypatch.undo()
    assert owner.delete(f"/api/library/{up['id']}?force=1&mode=permanent").status_code == 200
    assert st.q("SELECT 1 FROM library_purged WHERE token=?", (up["media_token"],))


def test_healthz_headlines_unique_missing_files_with_refs_second(human):
    t1 = _tok(human, "shared.jpg")
    for ch in ("ch_ig", "ch_li", "ch_x"):  # one carousel slide in three channel packages
        _run(human, [f"https://hq.test/m/{t1}"], channel_id=ch)
    _drop(human, t1)
    human.app.state.house.media_audit()
    m = human.get("/healthz").json()["media"]
    assert m["missing_files"] == 1 and m["missing_refs"] == 3, m  # main: missing_files was the reference count (3)
