"""`huhqueue` command line.

  huhqueue serve                      UI + API on localhost:8787, edge on :8788, worker in-process
  huhqueue worker                     run only the worker (if you serve elsewhere)
  huhqueue schedule ...               schedule a post through the REST API (for pipelines)
  huhqueue list / channels / upload / retry / cancel / tick / token / passwd
  huhqueue ai set|show|test|models    your own AI model (NVIDIA, Gemini, Groq, OpenRouter, Ollama, OpenAI, Anthropic)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

import httpx


# ----------------------------------------------------------------------------- server commands
def cmd_serve(a) -> None:
    import uvicorn

    from .api.app import create_app, create_edge_app
    from .core import Core

    core = Core()
    app = create_app(core, start_worker=not a.no_worker)
    edge = create_edge_app(core)
    s = core.settings
    edge_server = uvicorn.Server(uvicorn.Config(edge, host=a.edge_host or "127.0.0.1", port=s.public_port, log_level="warning"))
    threading.Thread(target=edge_server.run, daemon=True).start()
    print(f"HuhQueue {__import__('huhqueue').__version__}")
    print(f"  UI + API   http://localhost:{s.port}")
    print(f"  Edge       http://127.0.0.1:{s.public_port}  (media + OAuth callbacks; tunnel this port only)")
    print(f"  Data       {s.data_dir}")
    uvicorn.run(app, host=s.host, port=s.port, log_level="info")


def cmd_worker(a) -> None:
    from .core import Core
    from .worker import run_forever
    run_forever(Core())


def cmd_passwd(a) -> None:
    import getpass

    from .core import Core
    from .security import Auth
    pw = a.password or getpass.getpass("New HuhQueue password: ")
    Auth(Core()).set_password(pw)
    print("Password updated (all sessions logged out).")


def cmd_agents(a) -> None:
    """Kill switch from the host: huhqueue agents pause [--reason ...] | resume | status"""
    import getpass
    from . import safety
    from .core import Core
    core = Core()
    who = f"cli:{getpass.getuser()}"
    if a.op == "pause":
        st = safety.set_paused(core, True, who, a.reason or "")
    elif a.op == "resume":
        st = safety.set_paused(core, False, who)
    else:
        st = safety.state(core)
    print(json.dumps(st, indent=2))


def cmd_ai(a) -> None:
    """Local AI provider setup, like `passwd`: writes the encrypted key straight into this install's database.
    The key is read from a prompt or an environment variable, never from the command line (shell history)."""
    import getpass

    from .core import Core
    from .services import assistant
    core = Core()
    if a.action == "set":
        payload = {k: v for k, v in (("provider", a.provider), ("base_url", a.base_url), ("model", a.model),
                                     ("max_tokens", a.max_tokens)) if v is not None}
        if a.clear_key:
            payload["clear_key"] = True
        elif a.key_env:
            payload["api_key"] = os.environ.get(a.key_env, "")
            if not payload["api_key"]:
                sys.exit(f"${a.key_env} is empty")
        elif a.provider not in assistant.KEYLESS:
            payload["api_key"] = getpass.getpass("API key (leave empty to keep the saved one): ")
        try:
            assistant.set_ai_settings(core, payload)
        except ValueError as e:
            sys.exit(str(e))
    if a.action == "models":
        out = assistant.list_models(core)
        print("\n".join(out["models"]) or out["error"])
        return
    print(json.dumps(assistant.test_connection(core) if a.action == "test" else assistant.status(core), indent=2))


def cmd_mcp(a) -> None:
    from .mcp_server import McpServer
    McpServer(a.url, a.token).serve()


def cmd_token(a) -> None:
    from .core import Core
    from .security import Auth
    _, raw = Auth(Core()).mint_token(a.name, a.scopes.split(","), a.tools.split(",") if a.tools else [],
                                      not a.no_approval, a.rate)
    print(raw)
    print("Put it in HUHQ_API_TOKEN for the CLI / your pipeline. It is shown only once.", file=sys.stderr)


# ----------------------------------------------------------------------------- API client commands
class Client:
    def __init__(self, url: str | None = None, token: str | None = None, transport=None):
        from .config import _load_dotenv
        _load_dotenv(Path.cwd() / ".env")
        self.url = (url or os.environ.get("HUHQ_URL") or f"http://localhost:{os.environ.get('HUHQ_PORT', '8787')}").rstrip("/")
        tok = token or os.environ.get("HUHQ_API_TOKEN", "")
        self.http = httpx.Client(base_url=self.url, timeout=600, transport=transport,
                                 headers={"Authorization": f"Bearer {tok}"} if tok else {})

    def req(self, method: str, path: str, **kw):
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                d = r.json().get("detail")
            except Exception:
                d = r.text
            raise SystemExit(f"Error {r.status_code}: {json.dumps(d, indent=2) if not isinstance(d, str) else d}")
        return r.json()

    def upload(self, paths: list[str]) -> list[str]:
        ids = []
        for p in paths:
            if p.startswith(("http://", "https://")):
                ids.append(self.req("POST", "/api/media/from-url", json={"url": p})["id"])
                continue
            path = Path(p).expanduser()
            files = sorted(x for x in path.iterdir() if x.is_file()) if path.is_dir() else [path]
            for f in files:
                with open(f, "rb") as fh:
                    res = self.req("POST", "/api/media", files={"files": (f.name, fh)})
                for e in res.get("errors", []):
                    print("skip:", e, file=sys.stderr)
                ids += [m["id"] for m in res["media"]]
        return ids


def _client(a) -> Client:
    return Client(a.url, a.token)


def cmd_schedule(a) -> None:
    c = _client(a)
    caption = a.caption or ""
    if a.caption_file:
        caption = Path(a.caption_file).read_text(encoding="utf-8")
    media_ids = c.upload(a.media) if a.media else []
    channels = a.channel
    if a.all_channels:
        channels = [ch["id"] for ch in c.req("GET", "/api/channels") if ch["status"] == "active"]
    if not channels:
        raise SystemExit("Give --channel (id or provider:username, repeatable) or --all-channels")
    options: dict = {}
    for kv in a.option or []:
        k, _, v = kv.partition("=")
        options[k] = {"true": True, "false": False}.get(v.lower(), v)
    if a.type:
        options["ig_type"] = a.type
    if a.first_comment:
        options["first_comment"] = a.first_comment
    if a.cta:
        options["follow_cta"] = a.cta
    when = "now" if a.now else "draft" if a.draft else "custom" if a.at else "prioritize" if a.next else "queue"
    body = {"caption": caption, "media_ids": media_ids, "channel_ids": channels, "when": when, "scheduled_at": a.at,
            "timezone": a.timezone, "override_rate_limit": a.major_news, "tags": a.tag or [], "options": options, "topic_key": a.topic or "",
            "source": "cli"}
    res = c.req("POST", "/api/posts", json=body)
    if a.now:
        c.req("POST", "/api/worker/tick")
    for t in res["targets"]:
        print(f"{t['id']}  {t['channel']['provider']:<9} @{t['channel']['username']:<20} {t['status']:<10} {t['scheduled_local'] or '-'}")
    for n in res.get("notes", []):
        print("note:", n, file=sys.stderr)
    if a.json:
        print(json.dumps(res, indent=2))


def cmd_list(a) -> None:
    c = _client(a)
    params = {k: v for k, v in {"status": a.status, "channel_id": a.channel}.items() if v}
    for t in c.req("GET", "/api/posts", params=params):
        cap = (t["caption"] or "").replace("\n", " ")[:50]
        print(f"{t['id']}  {t['status']:<12} {t['channel']['provider']:<9} {t['scheduled_local'] or t['published_at'] or '-':<24} {cap}")


def cmd_channels(a) -> None:
    for ch in _client(a).req("GET", "/api/channels"):
        print(f"{ch['id']}  {ch['provider']:<9} @{ch['username']:<22} {ch['status']:<15} limit {ch['daily_limit']}/day  tz {ch['timezone']}")


def cmd_upload(a) -> None:
    for i in _client(a).upload(a.paths):
        print(i)


def cmd_simple(a) -> None:
    c = _client(a)
    if a.cmd == "retry":
        print(c.req("POST", f"/api/targets/{a.id}/retry")["status"])
    elif a.cmd == "cancel":
        c.req("PATCH", f"/api/targets/{a.id}", json={"when": "draft"})
        print("moved to drafts")
    elif a.cmd == "delete":
        c.req("DELETE", f"/api/targets/{a.id}")
        print("deleted")
    elif a.cmd == "tick":
        print(json.dumps(c.req("POST", "/api/worker/tick")))


def cmd_actions(a) -> None:
    """List every action from the registry (what the token may run)."""
    data = _client(a).req("GET", "/api/actions")
    for x in data["actions"]:
        if a.all or x.get("method"):
            print(f"{x['id']:<28} {x['risk']:<12} {x['title']}")


def cmd_run(a) -> None:
    """huhqueue run post.schedule caption="hi" channel_ids=all when=queue   (values are JSON when they parse as JSON)"""
    inp = json.loads(a.json) if a.json else {}
    for kv in a.args:
        k, _, v = kv.partition("=")
        try:
            inp[k] = json.loads(v)
        except ValueError:
            inp[k] = v.split(",") if k.endswith("_ids") or k == "ids" else v
        if k.endswith("_ids") and isinstance(inp[k], str):
            inp[k] = inp[k].split(",")
    body = {"input": inp, "dry_run": a.dry_run}
    out = _client(a).req("POST", f"/api/actions/{a.action}:run", json=body)
    print(json.dumps(out, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="huhqueue", description="Self-hosted social scheduler")
    p.add_argument("--url", help="API base (default HUHQ_URL or http://localhost:8787)")
    p.add_argument("--token", help="API token (default HUHQ_API_TOKEN)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run UI/API + edge + worker")
    s.add_argument("--no-worker", action="store_true")
    s.add_argument("--edge-host", default="127.0.0.1")
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("worker", help="run only the worker").set_defaults(fn=cmd_worker)
    s = sub.add_parser("passwd", help="set the app password")
    s.add_argument("--password")
    s.set_defaults(fn=cmd_passwd)
    s = sub.add_parser("token", help="mint a scoped API token for pipelines / agents")
    s.add_argument("--name", default="pipeline")
    s.add_argument("--scopes", default="read,write,publish,comments", help="comma list of read,write,publish,comments,admin")
    s.add_argument("--tools", default="", help="MCP tool allow-list (comma list); empty = all the scopes permit")
    s.add_argument("--no-approval", action="store_true", help="let this token publish without human approval")
    s.add_argument("--rate", type=int, default=60, help="requests per minute")
    s.set_defaults(fn=cmd_token)
    s = sub.add_parser("agents", help="agent kill switch: pause | resume | status")
    s.add_argument("op", choices=["pause", "resume", "status"])
    s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_agents)
    s = sub.add_parser("actions", help="list every action in the registry")
    s.add_argument("--all", action="store_true", help="include UI-only navigation entries")
    s.set_defaults(fn=cmd_actions)
    s = sub.add_parser("run", help="run any registry action: huhqueue run <id> key=value ...")
    s.add_argument("action")
    s.add_argument("args", nargs="*")
    s.add_argument("--json", default="", help="input as a JSON object")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("ai", help="set up / show / test your own AI model (key stays on this machine)")
    s.add_argument("action", choices=["set", "show", "test", "models"])
    s.add_argument("--provider", help="openai, nim, gemini, groq, openrouter, ollama or anthropic")
    s.add_argument("--base-url", help="any OpenAI-compatible URL (default: the provider's)")
    s.add_argument("--model")
    s.add_argument("--max-tokens", type=int)
    s.add_argument("--key-env", help="read the API key from this environment variable instead of a prompt")
    s.add_argument("--clear-key", action="store_true")
    s.set_defaults(fn=cmd_ai)
    s = sub.add_parser("mcp", help="run the MCP server on stdio (uses HUHQ_URL + HUHQ_API_TOKEN)")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("schedule", help="create a post")
    s.add_argument("--channel", "-c", action="append", default=[], help="channel id or provider:username (repeat)")
    s.add_argument("--all-channels", action="store_true", help="post to every connected channel")
    s.add_argument("--caption", "-m")
    s.add_argument("--caption-file")
    s.add_argument("--media", action="append", help="file, folder or URL (repeat; order = carousel order)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--now", action="store_true")
    g.add_argument("--at", help="ISO time, e.g. 2026-10-23T09:30 (read in --timezone / channel tz)")
    g.add_argument("--next", action="store_true", help="prioritize: top of the queue")
    g.add_argument("--draft", action="store_true")
    s.add_argument("--timezone")
    s.add_argument("--type", choices=["auto", "feed", "reel", "story", "carousel"], help="Instagram post type")
    s.add_argument("--first-comment")
    s.add_argument("--cta", choices=["off", "card", "comment", "both"], help="Follow @huhbyte CTA for videos")
    s.add_argument("--major-news", action="store_true", help="bypass the daily rate guard")
    s.add_argument("--tag", action="append")
    s.add_argument("--topic", help="topic key (e.g. 2026-10-22-gpt6); refused if already used on a channel")
    s.add_argument("--option", "-o", action="append", help="key=value network option, e.g. privacy_level=SELF_ONLY")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_schedule)

    s = sub.add_parser("list", help="list posts")
    s.add_argument("--status")
    s.add_argument("--channel")
    s.set_defaults(fn=cmd_list)
    sub.add_parser("channels", help="list channels").set_defaults(fn=cmd_channels)
    s = sub.add_parser("upload", help="upload media files/folders, print ids")
    s.add_argument("paths", nargs="+")
    s.set_defaults(fn=cmd_upload)
    for name in ("retry", "cancel", "delete"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(fn=cmd_simple)
    sub.add_parser("tick", help="run one worker tick now").set_defaults(fn=cmd_simple)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
