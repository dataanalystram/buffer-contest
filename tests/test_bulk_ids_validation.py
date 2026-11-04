"""Bulk routes check the shape of "ids" (4 Nov 2026 bug: POST /api/comments/bulk with {"ids": 5} answered 500).

A number, a string, an object or a list holding a non-id is a 400 with a message saying what to send, on
/api/comments/bulk, /api/channels/{cid}/comments/bulk-resolve and /api/queue/{cid}/order."""
import pytest

BAD_IDS = [None, 5, "abc", {"a": 1}, True, [None], [5.5], [["x"]], [""], [True]]


def _sync(api, cid):
    api.post("/api/comments/sync", {"channel_id": cid})
    return api.get(f"/api/comments?channel_id={cid}").json()


@pytest.mark.parametrize("ids", BAD_IDS)
def test_comments_bulk_rejects_non_list_ids(api, channel, ids):
    r = api.post("/api/comments/bulk", {"ids": ids, "action": "resolve"})
    assert r.status_code == 400, r.text
    assert "ids" in r.json()["detail"]


def test_comments_bulk_rejects_non_string_fields(api, channel):
    ids = [c["id"] for c in _sync(api, channel["id"])]
    assert api.post("/api/comments/bulk", {"ids": ids, "action": 5}).status_code == 400
    assert api.post("/api/comments/bulk", {"ids": ids, "action": "reply", "text": ["hi"]}).status_code == 400
    # a well-formed request still works, integers are accepted as ids
    assert api.post("/api/comments/bulk", {"ids": ids, "action": "resolve"}).json()["ok"] == len(ids)
    assert api.post("/api/comments/bulk", {"ids": [123456], "action": "resolve"}).json()["ok"] == 0


@pytest.mark.parametrize("ids", [5, "abc", {"a": 1}, [None]])
def test_bulk_resolve_rejects_non_list_ids(api, channel, ids):
    r = api.post(f"/api/channels/{channel['id']}/comments/bulk-resolve", {"ids": ids})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("ids", [5, "abc", {"a": 1}, [{"x": 1}]])
def test_queue_order_rejects_non_list_ids(api, channel, ids):
    r = api.post(f"/api/queue/{channel['id']}/order", {"ids": ids})
    assert r.status_code == 400, r.text
