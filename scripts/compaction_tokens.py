"""Tokens the agent sends on one reply in a long thread: before this change vs after (thread compaction + tool-result stubs).

    python scripts/compaction_tokens.py
Builds a long mocked thread (30 turns, 4 channels with full captions, a story with facts) and a reply whose tool loop runs
6 model turns with 6 KB tool results (the cap in brain_llm.tool_result), then counts every model call's input with the
app's own estimator (brain_llm._est_tokens, the number the budget checks). Three ways:
  before   the old reply prompt: no conversation at all, every tool result re-sent whole on each call
  naive    the old prompt plus the whole thread verbatim (what "just send the history" would cost)
  after    the compacted conversation (pinned facts + run summary + recent turns) and old tool results cut to stubs
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import agent_brain, brain_llm, chat_context, untrusted  # noqa: E402

TURNS, LOOP, RESULT = 30, 6, 6000


def mock_run() -> dict:
    caps = {f"ch_{s}": {"text": f"{s} caption: " + "Acme launched an agent that books travel with one approval. " * 20, "trimmed": False}
            for s in ("ig", "li", "x", "yt")}
    th = [{"role": "user", "text": "Post this on all my channels: https://news.example.com/agents", "at": 1},
          {"role": "user", "text": "Yes, I have the rights to repost it with credit", "at": 2}]
    for i in range(TURNS - 2):
        th.append({"role": "user" if i % 2 == 0 else "agent",
                   "text": (f"Change {i}: make the hook stronger and mention the price on LinkedIn, keep the rest. " * 2) if i % 2 == 0
                   else (f"Done: the LinkedIn caption now opens with the price; Instagram and X are unchanged. Slide 2 uses the Funding layout. " * 2),
                   "at": 3 + i})
    return {"input": {"prompt": "Post this", "intake": {"format": "carousel"}, "theme": "auto",
                      "channels": [{"id": f"ch_{s}", "service": n, "name": n} for s, n in (("ig", "instagram"), ("li", "linkedin"), ("x", "twitter"), ("yt", "youtube"))]},
            "result": {"thread": th, "source": {"url": "https://news.example.com/agents", "site": "Example News", "title": "Acme agent"},
                       "story": {"headline": "Acme launches an agent that books travel", "facts": ["It plans trips end to end."] * 6},
                       "captions": caps, "channel_media": {c: {"format": "carousel", "media": [{}] * 5} for c in caps},
                       "rights": {"mode": "news_credit"}, "template": {"id": "funding", "name": "Funding", "why": "you picked it"},
                       "packages": [{"channel_id": c, "applied_rules": [{"rule": "Never use: game-changer"}]} for c in caps]}}


def reply_prompt(run: dict, message: str, mode: str) -> str:
    res = run["result"]
    conv = ""
    if mode == "after":
        conv = f"Conversation so far (it quotes the thread): {untrusted.wrap(chat_context.prompt_block(run, {'name': 'HuhByte'})[:6000], 'thread')}\n"
    elif mode == "naive":
        full = "\n".join(f"{t['role']}: {t['text']}" for t in res["thread"])
        conv = f"Conversation so far (it quotes the thread): {untrusted.wrap(full, 'thread', limit=10**6)}\n"
    return (f"The person replied in the thread: {message}\n\nCurrent state (it quotes outside content): "
            f"{untrusted.wrap(json.dumps(agent_brain._state_summary(res), default=str)[:9000], 'run state')}\n"
            f"Template: {json.dumps({k: (res.get('template') or {}).get(k) for k in ('id', 'name', 'why')})}. "
            f"Rights: {(res.get('rights') or {}).get('mode') or 'not asked'}.\n" + conv +
            "Change only what they asked, then write_captions for the affected channels, check, finish.")


def measure(mode: str, with_fixed: bool = False) -> dict:
    run = copy.deepcopy(mock_run())
    run["result"]["thread"].append({"role": "user", "text": "make the X post shorter", "at": 99})
    messages = [{"role": "user", "content": reply_prompt(run, "make the X post shorter", mode)}]
    eng = {"style": "anthropic"}
    per_call = []
    for turn in range(LOOP):
        if mode == "after" and turn > chat_context.TOOL_KEEP_TURNS:
            messages = chat_context.shrink_tool_results(messages)
        sent = [agent_brain.SYSTEM, messages, agent_brain.SPECS] if with_fixed else messages
        per_call.append(brain_llm._est_tokens(sent))
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{turn}", "name": "write_captions", "input": {}}]})
        messages.append({"role": "user", "content": [brain_llm.tool_result(eng, f"t{turn}", "write_captions", {"result": "x" * RESULT})]})
    return {"first_call": per_call[0], "per_call": per_call, "total": sum(per_call)}


def report() -> dict:
    out = {m: measure(m) for m in ("before", "naive", "after")}
    out["with_system_and_tools"] = {m: measure(m, True)["total"] for m in ("before", "naive", "after")}
    return out


if __name__ == "__main__":
    r = report()
    print(f"one reply in a {TURNS}-turn thread, {LOOP} model calls, {RESULT // 1000} KB tool results (tokens, brain_llm estimator)")
    for m in ("before", "naive", "after"):
        print(f"  {m:7s} first call {r[m]['first_call']:6d}   all calls {r[m]['total']:7d}   per call {r[m]['per_call']}")
    b, a, n = r["before"]["total"], r["after"]["total"], r["naive"]["total"]
    print(f"  after vs before: {100 * (a - b) / b:+.1f}%   after vs naive history: {100 * (a - n) / n:+.1f}%")
    w = r["with_system_and_tools"]
    print(f"  incl. system prompt + tool specs: before {w['before']}  naive {w['naive']}  after {w['after']}  ({100 * (w['after'] - w['before']) / w['before']:+.1f}%)")
