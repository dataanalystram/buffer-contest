"""One take moment picking runs the automatic AI engine order: an engine that refuses (the free starter's 400 "The model
exhausted ...", a rate limit) hands over to the next, and the page never shows a raw engine error."""
from types import SimpleNamespace

from studio import brain_llm, llm, take, takes
from studio.actions import Actor

from .test_take import ANALYSIS

GOOD = {"moments": [{"start": 0, "end": 4, "hook": "", "why": "clear", "score": 90}]}


def _host(reserve=True):
    return SimpleNamespace(store=None, s=None, h=SimpleNamespace(http=None, _ai_reserve=lambda a, e: e if reserve else None))


def _engines(monkeypatch, ids):
    monkeypatch.setattr(brain_llm, "engines", lambda *a, **k: [{"id": i, "source": i, "provider": i} for i in ids])


def test_next_engine_takes_over_when_one_refuses(monkeypatch):
    _engines(monkeypatch, ["nvidia", "hf", "free"])
    tried = []

    def call(e, *a, **k):
        tried.append(e["id"])
        if e["id"] == "nvidia":
            raise llm.LLMError('free answered HTTP 400: {"success":false,"error":{"message":"The model exhausted"}}')
        return {"text": '{"moments": [{"start": 0, "end": 4, "score": 88}]}'}
    monkeypatch.setattr(brain_llm, "call", call)
    eng, chat, used = takes.Takes._moment_chat(_host(), Actor(uid="u1"))
    data = chat(eng, take.MINER_SYSTEM, "TRANSCRIPT")
    assert tried == ["nvidia", "hf"] and data["moments"][0]["score"] == 88
    assert used == {"engine": "hf", "label": "Hugging Face"}


def test_all_engines_fail_gives_the_friendly_scorer_note(monkeypatch, capsys):
    _engines(monkeypatch, ["free"])

    def call(e, *a, **k):
        raise llm.LLMError('free answered HTTP 400: {"success":false,"error":{"message":"The model exhausted its budget"}}')
    monkeypatch.setattr(brain_llm, "call", call)
    eng, chat, used = takes.Takes._moment_chat(_host(), Actor(uid="u1"))
    out = take.mine_moments(ANALYSIS, 3, eng, chat)
    assert out["picked_by"] == take.SCORER_FALLBACK and "exhausted" not in out["picked_by"]
    assert "exhausted" in capsys.readouterr().out  # the raw error stays in the server log
    assert used == {}


def test_free_starter_skipped_when_the_sample_allowance_is_used(monkeypatch):
    _engines(monkeypatch, ["free"])
    monkeypatch.setattr(brain_llm, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    eng, chat, used = takes.Takes._moment_chat(_host(reserve=False), Actor(uid="u1"))
    out = take.mine_moments(ANALYSIS, 3, eng, chat)
    assert out["picked_by"] == take.SCORER_FALLBACK


def test_no_engine_means_the_plain_built_in_scorer(monkeypatch):
    _engines(monkeypatch, [])
    eng, chat, used = takes.Takes._moment_chat(_host(), Actor(uid="u1"))
    assert eng is None and chat is None
    assert take.mine_moments(ANALYSIS, 3, eng, chat)["picked_by"] == "built-in scorer"
