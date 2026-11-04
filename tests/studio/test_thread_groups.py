"""Make a post thread list: each day group appears once (4 Nov 2026 bug: "Today" twice).

The sidebar used to open a new heading every time the group changed while walking the rows in id order, so a backdated
run (an import, a seeded sample, an MCP run) sitting between two of today's runs printed "Today" a second time. The
grouping helper in chat.js is run under node in several timezones, across a daylight-saving change, and the runs API is
checked to list newest-created first."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

import pytest

STATIC = pathlib.Path(__file__).resolve().parents[2] / "studio" / "static"


def _helper() -> str:
    src = (STATIC / "chat.js").read_text()
    m = re.search(r"  const GROUPS = .*?\n  function groupThreads\(rows, now\) \{.*?\n  \}\n", src, re.S)
    assert m, "groupThreads helper missing from chat.js"
    return m.group(0)


def _run(tz: str, rows: list, now_ms: int) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = _helper() + f"\nconsole.log(JSON.stringify(groupThreads({json.dumps(rows)}, {now_ms}).map(([g, l]) => [g, l.map((x) => x.id)])));"
    env = {**os.environ, "TZ": tz}
    return json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True, env=env).stdout)


def _local(tz: str, iso: str) -> float:
    """Epoch seconds of a wall-clock time in tz (via node, so the test and the helper agree on the zone rules)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    y, mo, d, h, mi = map(int, re.split(r"[-T:]", iso))
    js = f"console.log(new Date({y}, {mo - 1}, {d}, {h}, {mi}).getTime() / 1000)"
    return float(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True, env={**os.environ, "TZ": tz}).stdout)


@pytest.mark.parametrize("tz", ["Europe/Dublin", "America/Los_Angeles", "Asia/Kolkata", "UTC"])
def test_each_group_once_whatever_the_row_order(tz):
    now = _local(tz, "2026-11-04T16:00")
    rows = [  # id order (the old API order) with backdated rows between today's
        {"id": 14, "created": now - 60}, {"id": 13, "created": now - 9 * 86400}, {"id": 12, "created": now - 3600},
        {"id": 11, "created": now - 86400}, {"id": 10, "created": now - 3 * 86400}, {"id": 9, "created": now - 120},
    ]
    out = _run(tz, rows, int(now * 1000))
    names = [g for g, _ in out]
    assert names == ["Today", "Yesterday", "Previous 7 days", "Older"]
    assert len(names) == len(set(names))
    assert dict(out)["Today"] == [14, 9, 12]  # newest first inside a group


def test_day_edges_follow_the_local_calendar_across_dst():
    tz = "Europe/Dublin"  # clocks went back 25 Oct 2026: that day was 25 hours long
    now = _local(tz, "2026-10-26T00:30")
    rows = [
        {"id": 1, "created": _local(tz, "2026-10-26T00:10")},  # today
        {"id": 2, "created": _local(tz, "2026-10-25T00:20")},  # yesterday, 24h 10m ago: "midnight - 86400000" called it older
        {"id": 3, "created": _local(tz, "2026-10-24T23:50")},  # the day before yesterday
    ]
    out = dict(_run(tz, rows, int(now * 1000)))
    assert out["Today"] == [1] and out["Yesterday"] == [2] and out["Previous 7 days"] == [3]


def test_runs_api_lists_newest_created_first(human):
    st = human.app.state.store
    uid = dict(st.q("SELECT id FROM users")[0])["id"]
    now = time.time()
    for created in (now - 5 * 86400, now - 100, now - 2 * 86400):  # inserted out of created order
        st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
             (uid, "agent", json.dumps({"prompt": "x"}), "ready", "[]", "{}", created, created))
    rows = human.get("/api/agent/runs?limit=10").json()
    got = [r["created"] for r in rows]
    assert got == sorted(got, reverse=True)
