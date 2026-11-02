"""Live MCP smoke of the deployed studio, client-brain tools only (no HuhQueue AI, $0).

    HQ_AGENT_TOKEN=... python scripts/mcp_live.py [--base https://huhqueue-studio.onrender.com] [--url LINK] [--json out.json]

Checks, in order, and exits non-zero if any fails:
  1. tools/list answers and lists the client-brain tools (prints the count)
  2. research_link on a link: title, paragraphs, images (og/share image ranked ahead of logos)
  3. render_carousel with slides written here (the client is the brain): >= 3 slides, spent_usd 0
  4. ledger: get_activity (tab agent) since the start of this run has NO Anthropic call and $0 AI cost

The agent token comes from HQ_AGENT_TOKEN (a review-policy token from Settings -> Agents). When it is unset the request goes
out without one, for a gateway that adds it on the wire. The token is never printed. Nothing is posted to Buffer: the
script never calls create_package, submit_post or any publishing tool.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

CLIENT_BRAIN = ["research_link", "render_carousel", "edit_video", "render_status", "split_video", "create_package"]


class MCP:
    def __init__(self, base: str, token: str = ""):
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        self.c = httpx.Client(base_url=base.rstrip("/"), headers=h, timeout=180)
        self.n = 0

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        r.raise_for_status()
        body = r.text
        if r.headers.get("content-type", "").startswith("text/event-stream"):  # one SSE message
            body = next((ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")), "{}")
        j = json.loads(body)
        if "error" in j:
            raise RuntimeError(f"{method}: {j['error']}")
        return j["result"]

    def call(self, name: str, args: dict) -> tuple[bool, dict]:
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        txt = (res.get("content") or [{}])[0].get("text", "")
        try:
            out = res.get("structuredContent") or json.loads(txt)
        except ValueError:
            out = {"text": txt}
        return bool(res.get("isError")), out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("HQ_BASE", "https://huhqueue-studio.onrender.com"))
    ap.add_argument("--url", default="https://www.ycombinator.com/companies/ernd")
    ap.add_argument("--json", default="")
    o = ap.parse_args(argv)
    m = MCP(o.base, os.environ.get("HQ_AGENT_TOKEN", ""))
    results, out = [], {}

    def rec(name, ok, detail=""):
        results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
        print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)

    t0 = time.time() - 5
    m.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcp_live", "version": "1"}})
    names = [t["name"] for t in m.rpc("tools/list").get("tools", [])]
    missing = [n for n in CLIENT_BRAIN if n not in names]
    out["tools"] = len(names)
    rec("tools/list", names and not missing, f"{len(names)} tools; missing {missing}" if missing else f"{len(names)} tools")

    err, art = m.call("research_link", {"url": o.url})
    imgs, logos = art.get("images") or [], art.get("logos") or []
    out["research"] = {"title": art.get("title"), "paragraphs": len(art.get("paragraphs") or []), "images": len(imgs),
                       "logos": len(logos), "spent_usd": art.get("spent_usd")}
    rec("research_link", not err and art.get("title") and (art.get("paragraphs") or art.get("description")),
        out["research"] if not err else art)
    rec("research_link: logos never ahead of a real picture",
        not err and (not logos or not imgs or imgs[0] not in logos or len(imgs) == len(logos)),
        [u.split("?")[0] for u in imgs[:2]])  # no query strings in logs (presigned URLs carry tokens)

    hero = next((u for u in imgs if u not in logos), imgs[0] if imgs else "")
    name = (art.get("title") or "This company").split(":")[0].split("|")[0].strip()[:40]
    slides = [{"eyebrow": "LIVE CHECK", "headline": f"{name} In Three Slides", "accent_word": "Three",
               "body": (art.get("description") or "What the page says.")[:140], **({"image": hero} if hero else {})},
              {"eyebrow": "WHAT IT IS", "headline": "The One-Line Pitch", "body": (art.get("description") or "")[:160] or "See the source.",
               "footnote": f"Source: {o.url}"},
              {"eyebrow": "THE SOURCE", "headline": "Read The Original", "body": "Every fact here comes from the linked page."}]
    err, car = m.call("render_carousel", {"slides": slides, "keep_case": [name.split()[0]] if name else []})
    out["carousel"] = {"slides": car.get("slides"), "fit_problems": car.get("fit_problems"), "spent_usd": car.get("spent_usd"),
                       "media_urls": [str(u).split("?")[0] for u in car.get("media_urls") or []]}
    rec("render_carousel", not err and (car.get("slides") or 0) >= 3 and car.get("spent_usd") == 0,
        {k: out["carousel"][k] for k in ("slides", "spent_usd")} if not err else car)

    err, act = m.call("get_activity", {"tab": "agent", "limit": 100})
    rows = [x for x in act.get("items") or [] if (x.get("ts") or 0) >= t0]
    anth = [x for x in rows if "anthropic" in str(x.get("provider") or (x.get("detail") or {}).get("provider") or "").lower()
            or "claude" in str(x.get("model") or "").lower()]
    cost = round(sum(float(x.get("cost_usd") or 0) for x in rows), 6)
    out["ledger"] = {"rows_since_start": len(rows), "anthropic_calls": len(anth), "cost_usd": cost}
    rec("ledger: $0 Anthropic", not err and not anth and cost == 0, out["ledger"] if not err else act)

    out["results"] = results
    if o.json:
        with open(o.json, "w") as f:
            json.dump(out, f, indent=1, default=str)
    ok = all(r["ok"] for r in results)
    print(f"{sum(r['ok'] for r in results)}/{len(results)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
