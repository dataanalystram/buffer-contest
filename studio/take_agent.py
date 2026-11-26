"""The One take agent: a chat for a whole take, like Make a post's. The person (or any agent over MCP) talks to it; it can
answer questions about the take and make real edits across its clips, through the same drafts the Clip Studio uses
(studio/clip_draft.py), so every change is visible there and can be undone. It edits drafts; it renders only when asked
("render", "apply", "export it"). The conversation is kept on the take."""
from __future__ import annotations

import json
import re
import time

from . import clip_draft
from .actions import ActionError, Actor
from .take_layout import _take_id

KEEP, MAX_ACTIONS, MSG_MAX = 30, 4, 600

TOOLS = [
    {"name": "take_chat", "description": "One take agent: talk to a take in plain words (\"make clip 2's opening stronger\", "
     "\"add b-roll of a busy kitchen to the best clip\", \"what did you change?\", \"fewer effects on every clip\"). It "
     "answers and makes real edits to the clips' drafts (same as direct_clip), and renders only when the message asks. "
     "Returns its reply, what it did per clip, and the conversation.",
     "inputSchema": {"type": "object", "required": ["take_id", "message"], "properties": {
         "take_id": {"type": "integer"}, "message": {"type": "string", "maxLength": MSG_MAX},
         "clip_id": {"type": "string", "description": "The clip the person is looking at, if any."}}}},
    {"name": "get_take_chat", "description": "One take agent: the conversation so far on a take.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"}}}},
]
READ_ONLY = {"get_take_chat"}
NAMES = {t["name"] for t in TOOLS}

SYSTEM = ("You are the One take agent in a video clipping studio, talking with the person who owns this take. You can answer "
          "questions about the take's clips and edit them. Answer JSON: {\"reply\": one to three plain sentences, "
          "\"actions\": [{\"do\": \"direct\", \"clip\": clip id, \"instruction\": a creative request for that clip in plain "
          "words}, {\"do\": \"render\", \"clip\": clip id}]}. direct covers: hook/opening, words behind the head, numbers "
          "popping, caption style (clean, minimal, bold, cinematic, documentary, lower third, two tone, typewriter), fewer "
          "effects, moving elements, brand words, hiding the logo, B-roll (\"add b-roll of X\", \"remove the b-roll\"). "
          "Use render only when the person asks to render, apply, export or finish. Never claim an edit you did not put in "
          "actions. Music, sound effects, transitions and voiceover aren't available yet: say so. At most 4 actions. "
          "Questions and chat need no actions.")


def _history(res: dict) -> list[dict]:
    return [m for m in (res.get("chat") or []) if isinstance(m, dict)][-KEEP:]


def get(takes, a: Actor, rid) -> dict:
    run = takes._run(a, _take_id(rid))
    return {"take_id": run["id"], "messages": _history((run["result"] or {}))}


def _context(run: dict, res: dict) -> dict:
    clips = []
    for c in res.get("clips", []):
        if c.get("status") == "binned":
            continue
        d = {**clip_draft.committed(c, run), **(((c.get("draft") or {}).get("edits")) or {})}
        clips.append({"id": c["id"], "status": c.get("status"), "seconds": round(d["end"] - d["start"], 1), "hook": d["hook"][:90],
                      "hook_fx": d["hook_fx"], "captions": d["caption_preset"], "graphics": (len(d["fx"]) if d["fx"] is not None else "auto"),
                      "broll": len(d.get("broll") or []), "has_draft": bool(c.get("draft")), "why": str(c.get("why") or "")[:100]})
    inp = run.get("input") or {}
    return {"take_status": run.get("status"), "sample": bool(res.get("sample")), "clips": clips,
            "settings": {k: inp.get(k) for k in ("recipe", "caption", "instruction", "motion", "headline", "punch_in") if inp.get(k) is not None}}


def _target(msg: str, clips: list[dict], hint: str | None) -> list[str]:
    """Which clips a message is about when no model is there: 'clip 2', 'all clips' / 'every clip', else the one in view."""
    ids = [c["id"] for c in clips if c["status"] in ("ready", "needs_review")]
    low = msg.lower()
    if re.search(r"\b(all|every|each)\b.{0,12}\bclips?\b|\bclips\b", low) and not re.search(r"\bclip\s*\d", low):
        return ids
    m = re.search(r"\bclip\s*#?(\d+)\b", low)
    if m and 0 < int(m.group(1)) <= len(clips):
        return [clips[int(m.group(1)) - 1]["id"]]
    if hint and hint in ids:
        return [hint]
    return ids[:1]


def _done(clip_id: str, out: dict) -> dict:
    return {"clip": clip_id, "ops": [o.get("what") for o in out.get("ops") or []], "not_yet": out.get("not_yet") or [],
            "dirty": bool(out.get("dirty")), "reply": out.get("reply")}


def chat(takes, a: Actor, rid, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.")
    msg = " ".join(str(body.get("message") or "").split())[:MSG_MAX]
    if not msg:
        raise ActionError("Say something to the One take agent.")
    rid = _take_id(rid)
    run = takes._run(a, rid)
    res = run["result"] or {}
    ctx = _context(run, res)
    hint = body.get("clip_id") if isinstance(body.get("clip_id"), str) else None
    wants_render = bool(re.search(r"\b(render|apply (it|this|that|these|the)|export|finish|make it final|bake)\b", msg.lower()))
    reply, actions, by = "", [], "rules"
    if len(ctx["clips"]) == 0:
        reply = ("The clips aren't ready yet. Once they are I can edit them: openings, captions, graphics, B-roll."
                 if run.get("status") in ("running", "rendering") else "This take has no clips to edit.")
    else:
        eng, mchat, _used = takes._moment_chat(a)
        got = None
        if mchat:
            try:
                got = mchat(eng, SYSTEM, json.dumps({"message": msg, "viewing_clip": hint, "take": ctx,
                                                     "conversation": [{"role": m["role"], "text": m["text"][:300]} for m in _history(res)[-8:]]}))
            except Exception as e:  # noqa: BLE001 - the rules answer below
                print(f"take agent: AI failed: {str(e)[:160]}", flush=True)
        ids = {c["id"] for c in ctx["clips"]}
        if isinstance(got, dict) and (got.get("reply") or got.get("actions")):
            by = "AI"
            reply = " ".join(str(got.get("reply") or "").split())[:500]
            for x in (got.get("actions") or [])[:MAX_ACTIONS]:
                if isinstance(x, dict) and x.get("clip") in ids and x.get("do") in ("direct", "render"):
                    actions.append({"do": x["do"], "clip": x["clip"], "instruction": " ".join(str(x.get("instruction") or "").split())[:300]})
        else:
            q = re.search(r"\?\s*$|^(what|why|how|which|can|could|do|does|is|are)\b", msg.lower())
            if q and not clip_draft.rules(msg, {"place": {}, "fx": [], "end": 0, "start": 0, "hook": "", "hook_fx": "none"}, [], None, False)[1]:
                reply = ("I can edit these clips for you: a stronger opening, words behind your head, numbers that pop, caption "
                         "styles, fewer effects, moving the logo or captions, and B-roll. Tell me what you want, and which clip.")
            else:
                actions = [{"do": "direct", "clip": cid, "instruction": msg} for cid in _target(msg, ctx["clips"], hint)][:MAX_ACTIONS]
        if wants_render and not any(x["do"] == "render" for x in actions):
            actions += [{"do": "render", "clip": x["clip"]} for x in actions if x["do"] == "direct"][:MAX_ACTIONS]
            if not actions:
                actions = [{"do": "render", "clip": cid} for cid in _target(msg, ctx["clips"], hint)][:MAX_ACTIONS]
    done = []
    for x in actions:
        try:
            if x["do"] == "direct":
                out = clip_draft.direct(takes, a, rid, x["clip"], {"instruction": x["instruction"] or msg})
                done.append(_done(x["clip"], out))
            else:
                clip_draft.render(takes, a, rid, x["clip"])
                done.append({"clip": x["clip"], "ops": ["Rendering the draft on the video worker"], "not_yet": [], "dirty": False})
        except ActionError as e:
            done.append({"clip": x["clip"], "ops": [], "not_yet": [str(e)[:160]], "dirty": False})
    if done and not reply:
        parts = []
        for d in done:
            what = "; ".join(d["ops"]) or (("Nothing changed: " + "; ".join(d["not_yet"])) if d["not_yet"] else (d.get("reply") or "Nothing changed"))
            parts.append(f"{d['clip']}: {what}")
        reply = " ".join(parts)[:600]
    if done and any(d["ops"] for d in done) and not any(x["do"] == "render" for x in actions):
        reply = reply.rstrip(" .") + ". These are drafts: open Clip Studio to see them, undo any, or say \"render\" to apply."
    if not reply:
        reply = "I didn't find an edit for that. Try: \"make clip 1's opening stronger\" or \"add b-roll of a busy kitchen\"."
    now = round(time.time(), 1)
    turn = [{"role": "user", "text": msg, "at": now},
            {"role": "agent", "text": reply[:900], "at": now, "did": done, "by": by}]
    with takes._take_lock(rid):
        run2 = takes._run(a, rid)
        res2 = run2["result"] or {}
        res2["chat"] = (_history(res2) + turn)[-KEEP:]
        takes._save(rid, run2["steps"], res2)
    return {"take_id": rid, "reply": reply, "did": done, "read_by": by, "messages": res2["chat"]}


def call_tool(studio, actor, name: str, args: dict):
    t = studio.house.takes
    if name == "get_take_chat":
        return get(t, actor, args.get("take_id"))
    if name == "take_chat":
        studio.house.studio._guard_write(actor, "adjust_clip")
        return chat(t, actor, args.get("take_id"), {"message": args.get("message"), "clip_id": args.get("clip_id")})
    return NotImplemented
