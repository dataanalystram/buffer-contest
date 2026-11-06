"""Disconnect and delete removes every per-person table (privacy review, 2 Nov 2026), not just the store's own."""
from studio import store as store_mod


def test_delete_user_clears_tables_other_modules_own(app):
    st = app.state.store
    uid = "acc_del_1"
    st.upsert_user(uid, "a@example.com", "A", "org", "Org", {"access_token": "x", "refresh_token": "y", "expires_at": 0})
    seeded = []
    for i, t in enumerate(store_mod.OWNED_ELSEWHERE):
        try:
            st.q(f"SELECT COUNT(*) AS n FROM {t}")
        except Exception:  # noqa: BLE001  (a table this build creates lazily and hasn't yet)
            continue
        cols = [c for c in ("id",) if t in ("send_codes", "upload_sessions")]
        # a composite primary key is NOT NULL on Postgres: give those tables their key columns
        keys = {"template_slot_runs": {"slot_id": i + 1, "occurrence": 0, "channel_id": "c"}}.get(t, {})
        names = ", ".join(cols + list(keys) + ["user_id"])
        vals = [f"x{i}" for _ in cols] + list(keys.values()) + [uid]
        st.x(f"INSERT INTO {t}({names}) VALUES({', '.join('?' * len(vals))})", tuple(vals))
        seeded.append(t)
    assert {"brand_kit_library", "custom_templates", "source_runs"} & set(seeded), seeded
    st.delete_user(uid)
    for t in seeded:
        assert st.q(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id=?", (uid,))[0]["n"] == 0, t
    assert st.user(uid) is None


def test_delete_user_clears_library_purged_tokens(app):
    """The Bin purge's token list (library_purged) is per-person data too, so Disconnect and delete clears it (4 Nov 2026)."""
    st = app.state.store
    uid = "acc_del_2"
    st.upsert_user(uid, "b@example.com", "B", "org", "Org", {"access_token": "x", "refresh_token": "y", "expires_at": 0})
    _ = app.state.house.library  # the Library creates its tables on first use
    st.x("INSERT INTO library_purged(token,user_id,at) VALUES(?,?,?)", ("run:1", uid, 0.0))
    st.x("INSERT INTO library_purged(token,user_id,at) VALUES(?,?,?)", ("tok_keep", "someone_else", 0.0))
    st.delete_user(uid)
    assert st.q("SELECT COUNT(*) AS n FROM library_purged WHERE user_id=?", (uid,))[0]["n"] == 0
    assert st.q("SELECT COUNT(*) AS n FROM library_purged WHERE user_id=?", ("someone_else",))[0]["n"] == 1
