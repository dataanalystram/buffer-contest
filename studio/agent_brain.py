"""The in-app agent: plan -> act -> check -> revise, with tools (agent_tools), driven by the person's prompt.

Brains, in order (brain_llm.engines): the operator's Claude key (budgeted, tool use), the person's own key (tool use when
the provider supports it), the free starter model (a simpler, fixed plan; the model only writes facts and captions),
no AI (the same plan with the rule writers). Whatever the brain, the steps are the same tools, run in order, each one
stored as a step the UI shows live; a reply in the run's thread ("make the hook stronger", "redo YouTube at 30s")
continues the same run. The same tools are MCP tools, so a person's own ChatGPT/Claude can be the brain instead.

Order is deterministic: rules first, then the source, facts, renders per channel, captions, the check, drafts only when
asked. The check step always runs last and builds the packages; nothing is published (Buffer drafts at most).
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import activity, brain_llm, budget, demo_guard, intake, llm
from .actions import ActionError, Actor
from .agent_tools import LABELS, SPECS, TEXT_FIRST, VIDEO_ONLY, Tools

MAX_TURNS = int(os.environ.get("HQ_BRAIN_TURNS", "16"))
STALE = int(os.environ.get("HQ_RUN_STALE", "900"))
_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("HQ_BRAIN_WORKERS", "4")), thread_name_prefix="agent-brain")
_running: set[int] = set()
KEYFRAMES_MAX = 4
KEYFRAME_B64_MAX = 400_000  # one 768 px JPEG frame as a data URL is ~60-150 KB
_rlock = threading.Lock()

SYSTEM = """You are HuhQueue's post editor. You turn the person's request into on-brand social posts for their Buffer channels,
using ONLY the tools. Rules:
- A link the person pasted is THE source. Read it with read_url. Never search instead of reading their link.
- Words that say what to DO ("search it", "research", "post on all channels", "make a reel", "you can do that") are
  instructions, never the topic. The topic comes from the link, the research and the upload's own content.
- Context.upload is the person's OWN photo/video. read_upload has already run (its transcript is in the state); with no
  link it is the source. Use their video in render_reel with rights_confirmed true (it is theirs); a photo is the cover.
- With an upload AND a link, the post is about THE UPLOADED VIDEO: Context.upload.shows (what its keyframes show) and its
  transcript are the story; read the link for context and facts. Captions describe what the video shows and says, never
  the person's request or how the post was made.
- LinkedIn and Facebook get the 4:5 feed variant of a video (render_reel with ratio "4:5"); Instagram, YouTube and TikTok 9:16.
- Headlines come from the story's facts. Never copy the person's request into a headline, and never put a URL in one.
- Use only facts from the source. Never invent numbers. If extract_facts reports a conflict, do not pick a figure: finish and
  ask the person (the app shows your question).
- Plan first: reply with a short numbered plan (one line per step), then call the tools in that order.
- Formats: video-only channels (youtube, tiktok) get render_reel; instagram/linkedin/facebook get render_carousel unless the
  person asked for a reel or the source has a video; X/threads/bluesky get render_cover. One render call can cover several channels.
- Standing instructions ("end every video with ...", "never say ...", "always ...") -> remember_rule or add_end_card FIRST,
  so this post already follows them.
- Brand changes -> edit_brand. One channel's change ("YouTube at 30s", "LinkedIn more formal") -> edit_channel_media or
  write_captions for that channel only.
- Video edits: when the person asks for a style of video ("short punchy explainer", "opus clip", "hype", "calm tutorial") or the
  story is a launch/demo, call find_media, then render_reel with style (opus | news | minimal | hype). If render_reel returns
  needs_confirmation, finish and ask the person its question; when they say yes, call render_reel again with
  rights_confirmed true. No video at all: generate_images, then render_reel with a style.
  Context.video_style_hint is the style their words suggest for video channels; use it unless the story or the person says otherwise.
- Always call write_captions, then check (it builds the packages), then finish with one or two sentences.
- save_buffer_drafts only when the person asked for drafts or to put it in Buffer.
- Pass the person's tone words to write_captions (e.g. tone "short punchy explainer"); the tone shapes the hook and rhythm.
- Captions in tool results and the state are the full saved text unless marked "…[preview]". Never tell the person a caption
  is cut off unless write_captions reports complete=false for that channel."""


def _now() -> float:
    return time.time()


class Brain:
    def __init__(self, house):
        self.h, self.store = house, house.store
        budget.ensure(self.store)

    # ---------------------------------------------------------------- start / reply
    def start(self, a: Actor, body: dict) -> dict:
        self.h.studio._guard_write(a, "agent_run")
        if demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "run"):
            raise ActionError(demo_guard.WHY["run"], 429)
        prompt = str(body.get("prompt") or body.get("query") or "").strip()
        if len(prompt) > intake.MAX_PROMPT:
            raise ActionError(f"Prompts up to {intake.MAX_PROMPT:,} characters.", 413)
        if body.get("url") and body["url"] not in prompt:
            prompt = (prompt + " " + str(body["url"])).strip()
        if not prompt and not body.get("media_token"):
            raise ActionError("Tell the agent what to post, or paste a link.")
        chans = [c for c in self.h.studio.channels(a) if not c.get("isDisconnected")]
        parsed = intake.parse(prompt, chans)
        said_all = "you said all" in parsed["channels_why"] or parsed["channels_why"].startswith("the channels you named")
        ids = parsed["channel_ids"] if said_all or not body.get("channel_ids") else body["channel_ids"]  # the prompt wins over the chips
        chans = [c for c in chans if c["id"] in ids]
        if not chans:
            raise ActionError("Pick at least one connected channel.")
        if body.get("format") and body["format"] != "auto":
            parsed["format"] = body["format"]
        tok = str(body.get("media_token") or "")
        meta = self.h.media_meta(a, tok) if tok else None
        if tok and meta is None:
            raise ActionError("Unknown media token. Upload the file first.", 404)
        inp = {"prompt": prompt, "intake": parsed, "source_url": parsed["source_url"], "media_token": tok,
               "brief": str(body.get("brief") or "")[:1000],
               "theme": str(body.get("theme") or "")[:40],
               "channels": [{"id": c["id"], "service": c["service"], "name": c.get("displayName") or c["name"]} for c in chans]}
        res = {"thread": [{"role": "user", "text": prompt or "(your file)", "at": _now()}], "plan": [], "brain": {}}
        if tok:
            # the person's own file is an input to the agent (read_upload), never a pipeline of its own
            typ = "video" if (meta or {}).get("type") == "video" else "photo"
            res["upload"] = {"token": tok, "type": typ, "url": self.h.media_url(tok), "name": str(body.get("media_name") or "")[:80]}
            if typ == "video":
                res["clip_token"], res["clip_owned"] = tok, True
                frames = self._keyframes(a, body.get("keyframes"))
                if frames:
                    res["upload"]["frames"] = frames
                dur = body.get("media_duration")
                if isinstance(dur, (int, float)) and 0 < dur < 36000:
                    res["upload"]["duration"] = round(float(dur), 2)
        rid = self.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                           (a.uid, "agent", json.dumps(inp), "running", json.dumps([]), json.dumps(res), _now(), _now()))
        self.store.audit(a.uid, a.label, "agent_run", "read", {"run": rid, "kind": "agent", "source": "link" if parsed["source_url"] else "topic"})
        if body.get("brain") != "external":  # external: the person's own assistant drives the tools over MCP
            self._spawn(self._go, a, rid, None)
        else:
            self.h._save(rid, [], res, "ready")
        return self.h.run(a, rid)

    def _keyframes(self, a: Actor, frames) -> list[str]:
        """Up to 4 keyframes the browser took from the person's video (canvas -> JPEG data URLs, because the hosted app has
        no FFmpeg). Size is checked on the base64 text before decoding, the bytes must be a JPEG/PNG and are re-encoded by
        Pillow before anything else sees them (SECURITY_RULES 6). Bad frames are skipped, never fatal."""
        out = []
        if not isinstance(frames, list):
            return out
        from . import vision
        for f in frames[:KEYFRAMES_MAX]:
            if not isinstance(f, str) or len(f) > KEYFRAME_B64_MAX:
                continue
            b64 = f.split(",", 1)[1] if f.startswith("data:") and "," in f else f
            try:
                raw = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError):
                continue
            if not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
                continue
            clean = vision.prepare(raw, 768)
            if clean:
                out.append(self.h.put_media(a, clean, f"keyframe{len(out) + 1}.jpg", kind="keyframe", meta={"type": "photo", "role": "keyframe"}))
        return out

    def tool(self, a: Actor, rid: int, name: str, args: dict) -> dict:
        """One tool on one run, for REST and MCP (a person's own assistant as the brain)."""
        run = self.h.run(a, rid)
        if run["kind"] != "agent":
            raise ActionError("Agent tools work on runs started with make_post.")
        tools = Tools(self.h, a, rid)
        out = self.call(a, rid, tools, name, args or {})
        run = self.h.run(a, rid)
        if name == "check" and run["result"].get("packages"):
            self.h._save(rid, run["steps"], run["result"], "ready")
        return out

    def reply(self, a: Actor, rid: int, text: str) -> dict:
        self.h.studio._guard_write(a, "agent_reply")
        text = (text or "").strip()[:intake.MAX_PROMPT]
        if not text:
            raise ActionError("Say what to change.")
        run = self.h.run(a, rid)
        if run["kind"] in ("media", "news"):
            return self.h.redo_with_agent(a, rid, text)  # a pre-agent run: redone by the agent, never the old pipeline
        if run["kind"] != "agent":
            return self.h.revise(a, rid, text)
        with _rlock:
            if rid in _running:
                raise ActionError("The agent is still working on this post. Your message will fit in once it's done.", 409)
        res = run["result"]
        res.setdefault("thread", []).append({"role": "user", "text": text, "at": _now()})
        self.h._save(rid, run["steps"], res, "running")
        self._spawn(self._go, a, rid, text)
        return self.h.run(a, rid)

    def channel_media(self, a: Actor, rid: int, channel_id: str, token: str = "") -> dict:
        """One channel's media: replace it with the person's own file (a Library item or an upload), or regenerate it
        (token empty). Then the check runs again, so the package, its preview and its brand check stay true."""
        # attributed to this run in Activity, like every other agent action on it (house._spawn_run)
        tok = activity.begin(**{**(activity._ctx.get() or {}), "internal_app": "huhqueue-agent", "run_id": rid})
        try:
            return self._channel_media(a, rid, channel_id, token)
        finally:
            activity.end(tok)

    def _channel_media(self, a: Actor, rid: int, channel_id: str, token: str = "") -> dict:
        self.h.studio._guard_write(a, "agent_channel_media")
        run = self.h.run(a, rid)
        if run["kind"] != "agent":
            raise ActionError("This post was made before the agent. Press “Redo with the agent” (or reply in its thread) to remake it.", 409)
        with _rlock:
            if rid in _running:
                raise ActionError("The agent is still working on this post. Try again when it's done.", 409)
        ch = next((c for c in run["input"]["channels"] if c["id"] == channel_id or c["service"] == channel_id), None)
        if not ch:
            raise ActionError("That channel isn't in this post.", 404)
        pk = next((p for p in run["result"].get("packages") or [] if p["channel_id"] == ch["id"]), None)
        if pk and pk.get("sent"):
            raise ActionError("This post is already in Buffer; change it there.", 409)
        tools = Tools(self.h, a, rid)
        if token:
            meta = self.h.media_meta(a, token)
            if meta is None:
                raise ActionError("Unknown file. Upload it or pick it from your Library first.", 404)
            typ = "video" if meta.get("type") == "video" else "photo"
            if ch["service"] in VIDEO_ONLY and typ != "video":
                raise ActionError(f"{ch['service'].title()} needs a video.")
            key = self._step(a, rid, "replace_media", {}, f"Use your file on {ch['name']}")
            run = self.h.run(a, rid)
            run["result"].setdefault("channel_media", {})[ch["id"]] = {
                "format": "reel" if typ == "video" else "photo", "note": "Your file, as you uploaded it.",
                "media": [{"token": token, "url": self.h.media_url(token), "type": typ, "name": "your file", "role": "original"}]}
            self.h._save(rid, run["steps"], run["result"])
            self._done(a, rid, key, "done", f"{typ} from your Library")
        else:
            self.call(a, rid, tools, "edit_channel_media", {"channel": ch["id"]}, label=f"Regenerate {ch['name']}")
        self.call(a, rid, tools, "check", {})
        run = self.h.run(a, rid)
        self.h._save(rid, run["steps"], run["result"], "ready" if run["result"].get("packages") else run["status"])
        return self.h.run(a, rid)

    def _spawn(self, fn, *args):
        if self.h.sync:
            fn(*args)
        else:
            _pool.submit(self._safe, fn, *args)

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- the loop
    def _go(self, a: Actor, rid: int, message: str | None) -> None:
        with _rlock:
            _running.add(rid)
        # Activity labels this run's model calls "HuhQueue agent" (Make a post), not the Sources agent
        tok = activity.begin(**{**(activity._ctx.get() or {}), "internal_app": "huhqueue-agent", "run_id": rid})
        tools = Tools(self.h, a, rid)
        try:
            if message is None and (self.h.run(a, rid)["result"].get("upload") or {}).get("read") is None \
                    and self.h.run(a, rid)["result"].get("upload"):
                self.call(a, rid, tools, "read_upload", {})  # the upload is an input tool, read first whatever the brain
            engs = brain_llm.engines(self.store, a.uid, self.h.s, self.h.http, demo=tools.demo)
            eng = next((e for e in engs if e.get("tools")), None)
            done = False
            if eng:
                try:
                    done = self._llm_loop(a, rid, tools, eng, message)
                except budget.BudgetExceeded as e:
                    self._note(a, rid, str(e))
                except llm.LLMError as e:
                    self._note(a, rid, f"The AI brain didn't answer ({str(e)[:120]}), so I followed the standard plan.")
            if not done:
                self._planner(a, rid, tools, message, engs)
            run = self.h.run(a, rid)
            res = run["result"]
            status = "ready" if res.get("packages") else "failed"
            if res.get("conflicts"):
                status = "ready"  # packages are on hold; the question is in the thread
            self.h._save(rid, run["steps"], res, status)
        except ActionError as e:
            self._fail(a, rid, str(e))
        except Exception as e:  # never leave a run spinning (the bug the owner hit)
            traceback.print_exc()
            self._fail(a, rid, f"Unexpected error: {e.__class__.__name__}")
        finally:
            activity.end(tok)
            with _rlock:
                _running.discard(rid)

    def _note(self, a, rid, text):
        run = self.h.run(a, rid)
        run["result"].setdefault("notes", []).append(text)
        run["result"].setdefault("thread", []).append({"role": "agent", "text": text, "at": _now()})
        self.h._save(rid, run["steps"], run["result"])

    def _fail(self, a, rid, why):
        run = self.h.run(a, rid)
        for s in run["steps"]:
            if s["status"] == "running":
                s["status"], s["detail"] = "failed", why[:400]
        run["result"].setdefault("thread", []).append({"role": "agent", "text": why, "at": _now()})
        self.h._save(rid, run["steps"], run["result"], "failed")

    def _step(self, a, rid, tool, args, label=None):
        run = self.h.run(a, rid)
        n = len(run["steps"]) + 1
        run["steps"].append({"key": f"s{n}", "tool": tool, "label": label or LABELS.get(tool, tool), "status": "running",
                             "detail": _args_line(tool, args), "data": None, "at": _now()})
        self.h._save(rid, run["steps"], run["result"])
        return f"s{n}"

    def _done(self, a, rid, key, status, detail, data=None):
        run = self.h.run(a, rid)
        for s in run["steps"]:
            if s["key"] == key:
                s["status"], s["detail"], s["at"] = status, (detail or "")[:400], _now()
                if data is not None:
                    s["data"] = data
        self.h._save(rid, run["steps"], run["result"])

    def call(self, a, rid, tools: Tools, name: str, args: dict, label: str | None = None) -> dict:
        key = self._step(a, rid, name, args, label)
        try:
            out = tools.run_tool(name, args)
        except ActionError as e:
            self._done(a, rid, key, "failed", str(e))
            raise
        self._done(a, rid, key, "warn" if (out.get("conflicts") or out.get("problems") or out.get("clipped")) else "done",
                   _result_line(name, out), out)
        return out

    def _plan(self, a, rid, items: list[str], brain: dict):
        run = self.h.run(a, rid)
        run["result"]["plan"] = items
        run["result"]["brain"] = brain
        self.h._save(rid, run["steps"], run["result"])

    # ---------------------------------------------------------------- tool-use brain
    def _llm_loop(self, a, rid, tools: Tools, eng: dict, message: str | None) -> bool:
        run = self.h.run(a, rid)
        res, inp = run["result"], run["input"]
        ctx = {"channels": inp["channels"], "intake": {k: inp["intake"][k] for k in ("urls", "format", "seconds", "tone", "rules", "topic")},
               "kit": {k: v for k, v in self.h.kit(a).items() if k in ("name", "handle", "voice", "colors", "font")},
               "ledger": [r["rule"] for r in self.h.rules(a)],
               "state": _state_summary(res)}
        if res.get("upload"):
            up = res["upload"]
            ctx["upload"] = {"type": up["type"], "name": up.get("name", ""), "transcript": (up.get("transcript") or "")[:1500],
                             "shows": ((up.get("seen") or {}).get("summary") or ""),
                             "is_source": (res.get("source") or {}).get("via") == "upload"}
        ctx["intake"]["research_asked"] = bool(inp["intake"].get("research"))
        if any(c["service"] in VIDEO_ONLY for c in inp["channels"]) or inp["intake"].get("format") == "reel":
            # a hint for video channels from the person's words ("short punchy explainer" -> opus); the brain decides
            from .media import styles as media_styles
            ctx["video_style_hint"] = media_styles.explicit(inp["prompt"]) or media_styles.pick(" ".join([inp["prompt"], inp["intake"].get("tone") or ""]))
        user = (f"Request: {inp['prompt']}\n\nContext: {json.dumps(ctx, default=str)[:5000]}" if message is None else
                f"The person replied in the thread: {message}\n\nCurrent state: {json.dumps(_state_summary(res), default=str)[:9000]}\n"
                "Change only what they asked, then write_captions for the affected channels, check, finish.")
        messages = [{"role": "user", "content": user}]
        brain = {"mode": "tools", "provider": eng["provider"], "model": eng["model"], "fast_model": eng.get("fast_model") or eng["model"],
                 "source": eng["source"]}
        self._plan(a, rid, res.get("plan") or [], brain)
        label = a.label
        finished = False
        for turn in range(MAX_TURNS):
            out = brain_llm.call(eng, self.store, a.uid, label, system=SYSTEM, messages=messages, tools=SPECS + [_CHECK_SPEC],
                                 max_tokens=2000, http=self.h.http)
            if turn == 0 and out["text"]:
                plan = [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in out["text"].splitlines() if re.match(r"^\s*\d+[.)]", ln)]
                if plan:
                    self._plan(a, rid, plan[:12], {**brain, "model": out["model"]})
            messages.append(out["assistant"])
            if not out["tool_calls"]:
                if out["text"]:
                    self._say(a, rid, out["text"])
                break
            results = []
            for tc in out["tool_calls"]:
                if tc["name"] == "finish":
                    self._ensure_tail(a, rid, tools)
                    self._say(a, rid, tc["input"].get("message") or "Done.")
                    finished = True
                    results.append(brain_llm.tool_result(eng, tc["id"], tc["name"], {"ok": True}))
                    continue
                try:
                    r = self.call(a, rid, tools, tc["name"], tc["input"])
                except ActionError as e:
                    r = {"error": str(e)}
                results.append(brain_llm.tool_result(eng, tc["id"], tc["name"], r))
            if eng["style"] == "anthropic":
                messages.append({"role": "user", "content": results})
            else:
                messages += results
            if finished:
                break
        if not finished:
            self._ensure_tail(a, rid, tools)
        return True

    def _ensure_tail(self, a, rid, tools):
        """Whatever the model did, captions exist for every channel and the check ran last."""
        run = self.h.run(a, rid)
        res = run["result"]
        if not res.get("story"):
            return
        missing_media = [c["id"] for c in run["input"]["channels"] if c["id"] not in (res.get("channel_media") or {})]
        if missing_media:
            self._renders(a, rid, tools, run, missing_media)
        run = self.h.run(a, rid)
        cm = run["result"].get("channel_media") or {}
        feed = [c["id"] for c in run["input"]["channels"] if c["service"] in ("linkedin", "facebook")
                and (cm.get(c["id"]) or {}).get("format") == "reel" and (cm.get(c["id"]) or {}).get("ratio", "9:16") == "9:16"
                and any(m.get("type") == "video" and m.get("role") != "original" for m in (cm.get(c["id"]) or {}).get("media") or [])]
        if feed:  # the model gave a feed channel the vertical cut: LinkedIn / Facebook always get the 4:5 variant
            extra = {"style": cm[feed[0]]["style"]} if cm[feed[0]].get("style") else {}
            self.call(a, rid, tools, "render_reel", {"channel_ids": feed, "ratio": "4:5", **extra}, label="Render the 4:5 feed version")
        run = self.h.run(a, rid)
        missing = [c["id"] for c in run["input"]["channels"] if c["id"] not in (run["result"].get("captions") or {})]
        if missing:
            self.call(a, rid, tools, "write_captions", {"channel_ids": missing})
        self.call(a, rid, tools, "check", {})

    def _say(self, a, rid, text):
        run = self.h.run(a, rid)
        run["result"].setdefault("thread", []).append({"role": "agent", "text": text[:1500], "at": _now()})
        self.h._save(rid, run["steps"], run["result"])

    # ---------------------------------------------------------------- fixed plan (free model / no AI)
    def _formats(self, run) -> dict[str, list[str]]:
        inp, res = run["input"], run["result"]
        fmt = inp["intake"]["format"]
        has_video = bool(res.get("clip_token")) or any(m["type"] == "video" for m in (res.get("source") or {}).get("media") or [])
        groups: dict[str, list[str]] = {"reel": [], "reel45": [], "carousel": [], "photo": [], "text": []}
        for c in inp["channels"]:
            svc = c["service"]
            if svc in VIDEO_ONLY:
                f = "reel"
            elif fmt == "text":
                f = "text"  # Make a post v2: "Text" in the format menu is a caption with no media
            elif has_video and svc in ("facebook", "linkedin") and fmt in ("auto", "reel"):
                f = "reel45"  # the feed variant: 4:5 video for LinkedIn / Facebook, 9:16 for Reels and Shorts
            elif fmt != "auto":
                f = fmt
            elif has_video and svc == "instagram":
                f = "reel"
            elif svc in TEXT_FIRST or svc == "pinterest":
                f = "photo"
            else:
                f = "carousel"
            groups[f].append(c["id"])
        return groups

    def _renders(self, a, rid, tools, run, only: list[str] | None = None):
        g = self._formats(run)
        sec = run["input"]["intake"].get("seconds")
        from .media import styles as media_styles
        style = media_styles.explicit(run["input"].get("prompt") or "")  # only when the person named a look
        if style and (g["reel"] or g["reel45"]) and not run["result"].get("clip_owned"):
            self.call(a, rid, tools, "find_media", {})
        text_ids = [i for i in g["text"] if not only or i in only]
        if text_ids:
            run = self.h.run(a, rid)
            tools._set_media(run, text_ids, [], "text", {"note": "Text only, as you asked."})
            tools.save(run)
        for f, tool in (("carousel", "render_carousel"), ("photo", "render_cover"), ("reel", "render_reel"), ("reel45", "render_reel")):
            ids = [i for i in g[f] if not only or i in only]
            if ids:
                vid = f in ("reel", "reel45")
                extra = {**({"seconds": sec} if vid and sec else {}), **({"style": style} if vid and style else {}),
                         **({"ratio": "4:5"} if f == "reel45" else {})}
                self.call(a, rid, tools, tool, {"channel_ids": ids, **extra})

    def _planner(self, a, rid, tools: Tools, message: str | None, engs: list[dict]):
        run = self.h.run(a, rid)
        inp, res = run["input"], run["result"]
        free = next((e for e in engs if not e.get("tools")), None)
        brain = {"mode": "plan", "provider": (free or {}).get("provider") or "rules", "model": (free or {}).get("model") or "built-in",
                 "source": (free or {}).get("source") or "built-in writer"}
        if message is not None:
            return self._refine(a, rid, tools, message, brain)
        it = inp["intake"]
        up = res.get("upload") or {}
        from_upload = bool(up) and not it["source_url"] and (res.get("source") or {}).get("via") == "upload"
        plan = [f"Read your {up['type']}" + (" and transcribe it" if up.get("type") == "video" else "")] if up else []
        plan += [f"Save {len(it['rules'])} standing rule(s) to your Style Ledger"] if it["rules"] else []
        plan += [f"Read your link ({it['source_url'][:60]})" if it["source_url"] else
                 ("Use your upload as the story" if from_upload else f"Search the news for “{(it['topic'] or inp['prompt'])[:60]}”"),
                 "Pull out the facts and cross-check the numbers", "Render each channel in its format", "Write one caption per channel",
                 "Check brand rules, clipping and the source"]
        wants_drafts = bool(re.search(r"\bdrafts?\b|\bput (?:it|them) in buffer\b|\bsave (?:it|them) to buffer\b", inp["prompt"], re.I))
        if wants_drafts:
            plan.append("Save them as Buffer drafts")
        self._plan(a, rid, plan, brain)
        for r in it["rules"]:
            self.call(a, rid, tools, "remember_rule", {"correction": r})
        if it["source_url"]:
            self.call(a, rid, tools, "read_url", {"url": it["source_url"]})
        elif from_upload:
            pass  # read_upload made the person's own file (its words + their line about it) the source
        elif up and not (it["research"] and it["topic"]):
            raise ActionError(f"Tell me what your {up['type']} is about (one line), or paste the link it comes from.")
        else:
            out = self.call(a, rid, tools, "search_news", {"query": it["topic"] or inp["prompt"][:200]})
            opened = False
            for ld in out["leads"][:4]:
                try:
                    self.call(a, rid, tools, "read_url", {"url": ld["url"]}, label="Open the best story")
                    opened = True
                    break
                except ActionError:
                    continue
            if not opened:
                raise ActionError("None of the top stories opened (paywalls or blocked pages). Paste a link you can read.")
        self.call(a, rid, tools, "extract_facts", {})
        run = self.h.run(a, rid)
        note = (run["result"].get("source") or {}).get("media_note")
        if note:
            self._say(a, rid, note)
        self._renders(a, rid, tools, run)
        tone = it.get("tone") or ""
        self.call(a, rid, tools, "write_captions", {"tone": tone} if tone else {})
        self.call(a, rid, tools, "check", {})
        if wants_drafts:
            try:
                self.call(a, rid, tools, "save_buffer_drafts", {})
            except ActionError as e:
                self._say(a, rid, str(e))
        run = self.h.run(a, rid)
        res = run["result"]
        if not res.get("conflicts"):
            n = len(res.get("packages") or [])
            self._say(a, rid, f"{n} post{'s' if n != 1 else ''} ready: " + ", ".join(f"{p['service']} ({p['format']})" for p in res.get("packages") or [])
                      + ". Reply here to change anything, e.g. “make the hook stronger” or “redo YouTube at 30s”.")

    def _refine(self, a, rid, tools: Tools, message: str, brain: dict):
        run = self.h.run(a, rid)
        services = [c["service"] for c in run["input"]["channels"]]
        r = intake.refine(message, services)
        targets = r["services"] or None
        did = []
        standing = intake.standing_rules(message)
        brand_words = re.search(r"\blogo\b|#[0-9a-f]{6}\b|\bfont\b|\bcolou?r\b|\baccent\b", message, re.I)
        self._plan(a, rid, (run["result"].get("plan") or []) + [f"Your reply: {message[:80]}"], brain)
        for s in standing:
            self.call(a, rid, tools, "remember_rule", {"correction": s})
            did.append("rule")
        if brand_words and not standing:
            self.call(a, rid, tools, "edit_brand", {"instruction": message})
            did.append("brand")
        for act in r["actions"]:
            if act["do"] == "resolve_number":
                self._resolve(a, rid, act["value"])
                did.append("number")
            elif act["do"] == "duration":
                for svc in (targets or [s for s in services if s in VIDEO_ONLY] or services[:1]):
                    self.call(a, rid, tools, "edit_channel_media", {"channel": svc, "seconds": act["seconds"], "format": "reel"})
                did.append("duration")
            elif act["do"] == "format":
                for svc in (targets or services):
                    self.call(a, rid, tools, "edit_channel_media", {"channel": svc, "format": act["format"]})
                did.append("format")
            elif act["do"] == "captions":
                for svc in (targets or [s for s in services if s in VIDEO_ONLY or s == "instagram"]):
                    self.call(a, rid, tools, "edit_channel_media", {"channel": svc, "captions": True, "format": "reel"})
                did.append("captions")
            elif act["do"] == "tone":
                self.call(a, rid, tools, "write_captions", {"channel_ids": targets, "tone": act["tone"]} if targets else {"tone": act["tone"]})
                did.append("tone")
            elif act["do"] == "hook":
                hook = self._stronger_hook(a, rid)
                self.call(a, rid, tools, "write_captions", {"hook": hook})
                run2 = self.h.run(a, rid)
                self._renders(a, rid, tools, run2)
                did.append("hook")
            elif act["do"] == "rerender":
                self._renders(a, rid, tools, self.h.run(a, rid), [c["id"] for c in run["input"]["channels"] if not targets or c["service"] in targets])
                did.append("rerender")
        if "brand" in did or ("rule" in did and not set(did) - {"rule"}):
            self._renders(a, rid, tools, self.h.run(a, rid))  # brand and visual rules show on every channel at once
        if not did:
            learned = tools.run_tool("remember_rule", {"correction": message})
            if learned.get("rules"):
                self._renders(a, rid, tools, self.h.run(a, rid))
                did.append("rule")
        if did:
            self.call(a, rid, tools, "check", {})
            self._say(a, rid, "Done: " + ", ".join(dict.fromkeys({"rule": "saved to your Style Ledger", "brand": "brand kit updated",
                                                                   "number": "figure confirmed", "duration": "video length changed",
                                                                   "format": "format changed", "captions": "captions added", "tone": "captions rewritten",
                                                                   "hook": "stronger hook", "rerender": "re-rendered"}[d] for d in did)) + ".")
        else:
            self._say(a, rid, "I couldn't tell what to change. Try “make the hook stronger”, “redo YouTube at 30s”, “LinkedIn more formal”, "
                               "“logo smaller” or “use #FF5A1F”.")

    def _resolve(self, a, rid, value: str):
        run = self.h.run(a, rid)
        res = run["result"]
        for c in res.get("conflicts") or []:
            res.setdefault("resolved", []).append({**c, "answer": value, "at": _now()})
            if value.replace(" ", "").lower() != c["ours"].replace(" ", "").lower():
                st = res["story"]
                st["headline"] = st["headline"].replace(c["ours"], value)
                st["facts"] = [f.replace(c["ours"], value) for f in st["facts"]]
        res["conflicts"], res["needs_answer"] = [], ""
        self.h._save(rid, run["steps"], res)
        tools = Tools(self.h, a, rid)
        self._renders(a, rid, tools, self.h.run(a, rid))
        self.call(a, rid, tools, "write_captions", {})

    def _stronger_hook(self, a, rid) -> str:
        run = self.h.run(a, rid)
        st = run["result"]["story"]
        eng = llm.resolve(self.store, a.uid, self.h.s)
        eng = self.h._ai_reserve(a, eng)
        if eng:
            try:
                d = llm.chat_json(eng, "You write social media hooks. Use only the facts given; never invent numbers.",
                                  "Return JSON {hook}: max 9 words, lead with the most surprising fact, no hype words.\n" +
                                  json.dumps({"headline": st["headline"], "facts": st["facts"]}), http=self.h.http, max_tokens=120)
                self.h._ai_audit(a, eng, "hook")
                if d.get("hook"):
                    return str(d["hook"])[:100]
            except llm.LLMError:
                pass
        num = next((f for f in st["facts"] if re.search(r"[$€£]\d|\d+%|\d+[MBK]\b", f)), "")
        base = num or st["headline"]
        words = base.split()
        return " ".join(words[:9]).rstrip(".,") if words else st["headline"]


_CHECK_SPEC = {"name": "check", "description": "Check every channel (brand rules, clipped text, about the source, fact conflicts) and build the packages. Call after write_captions.",
               "input_schema": {"type": "object", "properties": {}}}


def _args_line(tool: str, args: dict) -> str:
    if tool in ("read_url", "x_oembed"):
        return args.get("url", "")[:120]
    if tool == "search_news":
        return f"“{args.get('query', '')[:80]}”"
    if tool in ("remember_rule", "edit_brand"):
        return f"“{(args.get('correction') or args.get('instruction') or '')[:100]}”"
    if tool == "edit_channel_media":
        return " ".join(f"{k}={v}" for k, v in args.items())[:120]
    return ""


def _result_line(tool: str, out: dict) -> str:
    if tool in ("read_url", "x_oembed"):
        if out.get("author"):
            return f"Post by {out['author']} (@{out['handle']}): “{out['text'][:140]}”"
        return f"{out.get('site')}: {out.get('title', '')[:140]}"
    if tool == "search_news":
        return f"{len(out.get('leads') or [])} stories; best: {(out.get('leads') or [{}])[0].get('title', '')[:120]}"
    if tool == "extract_facts":
        c = out.get("conflicts") or []
        return (out["headline"] + (f" · also reported by {', '.join(out['also_reported_by'])}" if out.get("also_reported_by") else "")
                + (f" · CONFLICT: {c[0]['question']}" if c else ""))
    if tool.startswith("render_") or tool == "edit_channel_media":
        n = len(out.get("media") or [])
        return f"{out.get('format', '')} {n} file{'s' if n != 1 else ''} for {len(out.get('channels') or [])} channel(s)".strip() + (
            f" · {out['note']}" if out.get("note") else "") + (" · TEXT CLIPPED" if out.get("clipped") else "")
    if tool == "write_captions":
        return f"{len(out.get('captions') or {})} caption(s)"
    if tool == "check":
        p = out.get("problems") or []
        return f"{out.get('packages', 0)} package(s)" + (f" · {'; '.join(p)}" if p else " · every check passes") + (
            " · on hold: a figure needs your answer" if out.get("conflicts") else "")
    if tool in ("remember_rule", "add_end_card"):
        return "; ".join(out.get("rules") or []) or out.get("message", "")
    if tool == "edit_brand":
        return "; ".join(out.get("changed") or []) or out.get("message", "")
    if tool == "save_buffer_drafts":
        return ", ".join(f"{d['service']}: {d['status']}" for d in out.get("drafts") or [])
    return ""


def _state_summary(res: dict) -> dict:
    st = res.get("story") or {}
    return {"source": {k: (res.get("source") or {}).get(k) for k in ("url", "site", "title")},
            "story": {k: st.get(k) for k in ("headline", "facts")}, "conflicts": res.get("conflicts") or [],
            "channels": {cid: {"format": v.get("format"), "files": len(v.get("media") or []), "seconds": v.get("seconds")}
                         for cid, v in (res.get("channel_media") or {}).items()},
            "captions": {cid: _preview(v) for cid, v in (res.get("captions") or {}).items()}}


def _preview(cap: dict, n: int = 300) -> dict:
    """A caption for the brain's context, marked as a preview when shortened so it is never mistaken for a cut-off caption."""
    t = cap.get("text") or ""
    return {"text": t if len(t) <= n else t[:n] + " …[preview]", "chars": len(t), "complete": not cap.get("trimmed")}


def tool_specs_for_mcp() -> list[dict]:
    """The same tools for a person's own assistant: each MCP tool takes run_id plus the tool's arguments."""
    out = []
    for sp in SPECS + [_CHECK_SPEC]:
        if sp["name"] == "finish":
            continue
        sch = json.loads(json.dumps(sp["input_schema"]))
        sch.setdefault("properties", {})["run_id"] = {"type": "integer", "description": "From make_post (or start_post)"}
        sch["required"] = ["run_id"] + list(sch.get("required") or [])
        out.append({"name": "post_" + sp["name"], "description": "Agent tool on one post run: " + sp["description"], "inputSchema": sch})
    return out
