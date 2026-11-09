"""The 9 Nov 2026 Glasshand run (owner bug report): misspelt networks, per-network formats, the exact hook, the credit
of the video actually used, "your link" after a second link, and a channel added mid-thread."""
from __future__ import annotations

from studio import intake
from studio.agent_brain import asked_formats
from studio.agent_tools import with_hook

CH = [{"id": "i", "service": "instagram"}, {"id": "y", "service": "youtube"}, {"id": "f", "service": "facebook"},
      {"id": "l", "service": "linkedin"}]
ASK = ("i want to post a reel and short on sintagram, youtube and facebook next is on linkedin i need caraosul so fetech "
       "the video and create this with proper hook")


def test_misspelt_networks_and_carousel_still_count():
    p = intake.parse(ASK, CH)
    assert p["channels_why"] == "the channels you named" and set(p["channel_ids"]) == {"i", "y", "f", "l"}
    assert p["formats"] == {"instagram": "reel", "youtube": "reel", "facebook": "reel", "linkedin": "carousel"}


def test_ordinary_words_are_not_networks():
    p = intake.parse("an instant classic: the car racing story, a linked list explainer", CH)
    assert p["channels_why"] == "all connected channels" and p["formats"] == {} and p["format"] == "auto"


def test_a_later_message_wins_per_network():
    run = {"input": {"prompt": "https://x.com/a/status/1"}, "result": {"thread": [
        {"role": "user", "text": ASK}, {"role": "agent", "text": "a carousel for linkedin"},
        {"role": "user", "text": "actually a reel on linkedin too"}]}}
    f = asked_formats(run)
    assert f["linkedin"] == "reel" and f["instagram"] == "reel"


def test_the_hook_is_the_first_line_word_for_word():
    h = "Your AI agent can now rent its own real iPhone or Mac with one prompt."
    assert with_hook("AI agent with its own real iPhone? That's now a thing.\n\nConnect it.", h) == h + "\n\nConnect it."
    assert with_hook("Glasshand is here.\nMore.", h) == h + "\n\nGlasshand is here.\nMore."
    assert with_hook(h.upper().rstrip(".") + "\n\nBody", h).startswith(h.upper().rstrip("."))



def test_a_connected_channel_named_in_channel_ids_joins_the_run(human, monkeypatch):
    import json
    from studio.actions import Actor
    from studio.agent_tools import Tools
    house = human.app.state.house
    uid = human.app.state.store.session(human.cookies.get("hq_sid"))["user_id"]
    conn = [{"id": "ig1", "service": "instagram", "name": "huhbyte.ai"}, {"id": "li1", "service": "linkedin", "name": "HB"},
            {"id": "gone", "service": "tiktok", "name": "t", "isDisconnected": True}]
    monkeypatch.setattr(house.studio, "channels", lambda a: conn)
    inp = {"prompt": "x", "channels": [{"id": "li1", "service": "linkedin", "name": "HB"}]}
    rid = house.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                        (uid, "agent", json.dumps(inp), "ready", "[]", "{}", 0, 0))
    t = Tools(house, Actor(uid), rid)
    run = t.state()
    assert [c["id"] for c in t.channels(run, ["instagram"])] == ["ig1"]  # by service
    assert t.channels(run, ["gone"]) == [] and t.channels(run, ["nope"]) == []  # disconnected / unknown: nothing
    assert [c["id"] for c in t.state()["input"]["channels"]] == ["li1", "ig1"]  # saved on the run



def test_two_formats_in_one_sentence_split_per_network():
    from studio.intake import channel_formats
    assert channel_formats("a reel on instagram and facebook, and for linkedin a carousel") == {
        "instagram": "reel", "facebook": "reel", "linkedin": "carousel"}
    assert channel_formats("carousel on linkedin and a reel on instagram") == {"linkedin": "carousel", "instagram": "reel"}
