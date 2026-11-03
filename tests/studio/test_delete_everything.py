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
        names = ", ".join(cols + ["user_id"])
        vals = [f"x{i}" for _ in cols] + [uid]
        st.x(f"INSERT INTO {t}({names}) VALUES({', '.join('?' * len(vals))})", tuple(vals))
        seeded.append(t)
    assert {"brand_kit_library", "custom_templates", "source_runs"} & set(seeded), seeded
    st.delete_user(uid)
    for t in seeded:
        assert st.q(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id=?", (uid,))[0]["n"] == 0, t
    assert st.user(uid) is None
