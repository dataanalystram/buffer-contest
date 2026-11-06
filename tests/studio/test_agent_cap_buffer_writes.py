"""6 Nov 2026 (owner): an AI app's 20 renders used up a daily cap of 10 before one draft was saved. The daily cap now counts
and blocks Buffer writes only (Studio.BUFFER_WRITES), default 50, max 500; the kill switch still stops every agent action.
Old caps of 20 or less move to 50 once (data_migrations marker), sample tokens stay at 10."""
import time

from studio.actions import ActionError, Actor, Studio
from studio.store import Store

from .conftest import agent_token, mcp_call


def _agent(human, cap):
    agent_token(human, policy="draft", cap=cap)
    st = human.app.state.store
    row = dict(st.q("SELECT * FROM agent_tokens ORDER BY id DESC LIMIT 1")[0])
    return st, Actor(row["user_id"], kind="agent", agent=row)


def test_20_renders_then_a_draft_is_allowed_on_a_cap_of_10(human):
    """Fails on main: renders, image edits and runs counted, so the draft was refused."""
    st, a = _agent(human, 10)
    studio = human.app.state.studio
    for i in range(20):  # what an AI app does before it saves: renders, edits, previews
        studio._guard_write(a, ("render_carousel", "edit_image", "agent_run", "fetch_image")[i % 4])
        st.audit(a.uid, a.label, ("render_carousel", "edit_image", "agent_run", "fetch_image")[i % 4], "write", {}, ok=True)
    studio._guard_write(a, "replace_draft")  # the draft is not blocked


def test_the_cap_blocks_at_the_buffer_write_and_says_when_it_resets(human):
    st, a = _agent(human, 2)
    studio = human.app.state.studio
    first = time.time() - 3600
    for t in (first, first + 60):
        st.x("INSERT INTO audit(ts,user_id,actor,action,risk,detail,ok) VALUES(?,?,?,?,?,?,1)",
             (t, a.uid, a.label, "submit_post", "draft", "{}"))
    for w in Studio.BUFFER_WRITES:
        try:
            studio._guard_write(a, w)
            raise AssertionError(f"{w} was not blocked")
        except ActionError as e:
            assert e.status == 429, e
            msg = str(e)
            assert "2 Buffer posts per day" in msg and "Only Buffer writes count" in msg
            assert time.strftime("%H:%M UTC", time.gmtime(first + 86400)) in msg  # 24 h after the oldest counted write
    for other in ("render_carousel", "edit_image", "agent_run", "remember_rule", "upload_media"):
        studio._guard_write(a, other)  # still allowed past the cap
    st.set_kill(a.uid, True)
    try:
        studio._guard_write(a, "render_carousel")
        raise AssertionError("kill switch did not stop a render")
    except ActionError as e:
        assert e.status == 423


def test_cap_over_mcp_counts_only_buffer_writes(human):
    tok = agent_token(human, policy="review", cap=1)
    for i in range(21):  # 21 non-Buffer writes over MCP
        res, out = mcp_call(human, tok, "remember_rule", {"correction": f"rule number {i}"}) if i < 3 else \
            mcp_call(human, tok, "list_channels", {})
        assert not res["isError"], out
    res, out = mcp_call(human, tok, "create_idea", {"title": "t", "text": "x"})
    assert not res["isError"], out
    res, out = mcp_call(human, tok, "create_idea", {"title": "t2", "text": "y"})
    assert res["isError"] and "daily cap" in out


def test_migration_moves_old_caps_to_50_once_and_keeps_samples(tmp_path):
    path = str(tmp_path / "m.db")
    st = Store(path, "s" * 64)
    now = time.time()
    rows = [("acc_a", 10), ("acc_b", 20), ("acc_c", 30), ("demo_x", 10)]
    for uid, cap in rows:
        st.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) VALUES(?,?,?,?,?,?,?,?)",
             (uid, uid, uid * 8, "review", cap, None, now, now + 3600))
        st.x("INSERT INTO oauth_refresh(token_hash,client_id,user_id,agent_id,policy,daily_cap,expires,used) VALUES(?,?,?,?,?,?,?,0)",
             ("r" + uid, "c", uid, 1, "review", cap, now + 3600))
    st.x("DELETE FROM data_migrations")  # as if this database predates the change
    Store(path, "s" * 64)  # the next boot runs it
    want = {"acc_a": 50, "acc_b": 50, "acc_c": 30, "demo_x": 10}
    assert {r["user_id"]: r["daily_cap"] for r in st.q("SELECT user_id, daily_cap FROM agent_tokens")} == want
    assert {r["user_id"]: r["daily_cap"] for r in st.q("SELECT user_id, daily_cap FROM oauth_refresh")} == want
    # idempotent: a person lowers a cap to 5 afterwards; later boots never raise it again
    st.x("UPDATE agent_tokens SET daily_cap=5 WHERE user_id='acc_a'")
    Store(path, "s" * 64)
    Store(path, "s" * 64)
    assert st.q("SELECT daily_cap FROM agent_tokens WHERE user_id='acc_a'")[0]["daily_cap"] == 5
    assert st.q("SELECT COUNT(*) n FROM data_migrations")[0]["n"] == 1
