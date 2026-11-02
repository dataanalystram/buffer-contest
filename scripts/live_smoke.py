"""Post-deploy smoke of the live studio: /healthz, the key pages and the key read APIs answer 200. Run after every deploy.

    python scripts/live_smoke.py [--base https://huhqueue-studio.onrender.com] [--json out.json] [--no-demo]

Public pages are fetched anonymously. The signed-in APIs are read inside a free sample workspace (GET /demo), the same one
the landing page opens, so no Buffer account, token or secret is needed and nothing is posted anywhere: every call here is
a GET. Each check runs on its own with its own timeout, so one slow or failing endpoint is reported and the rest still run
(the owner's isolation rule). Exits 1 when any check fails. Only paths and status codes are printed, never cookies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

PAGES = ["/healthz", "/", "/app", "/docs", "/docs/api", "/api/docs", "/robots.txt", "/sitemap.xml", "/favicon.ico",
         "/og.png", "/security.txt", "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
         "/static/app.js"]
APIS = ["/api/session", "/api/me", "/api/channels", "/api/queue", "/api/calendar", "/api/library", "/api/inbox",
        "/api/brand", "/api/brand-studio", "/api/themes", "/api/templates", "/api/integrations", "/api/ai",
        "/api/activity?tab=all", "/api/limits", "/api/plan", "/api/stats", "/api/sources", "/api/recommendations",
        "/api/radar", "/api/approvals", "/api/onboarding", "/api/prefs/appearance", "/api/safety", "/api/preflight/rules",
        "/api/takes", "/api/agent/runs", "/api/agent/brain"]


def check(c: httpx.Client, path: str, timeout: float) -> dict:
    t = time.time()
    try:
        r = c.get(path, timeout=timeout)
        ok = r.status_code == 200
        out = {"path": path, "status": r.status_code, "ok": ok, "ms": int((time.time() - t) * 1000)}
        if path == "/healthz" and ok:
            try:
                j = r.json()
                out["commit"] = j.get("commit") or j.get("version") or ""
            except ValueError:
                pass
        if not ok:
            out["why"] = r.text[:160].replace("\n", " ")
        return out
    except Exception as e:  # noqa: BLE001 - one failing endpoint is a result, never a crash of the run
        return {"path": path, "status": None, "ok": False, "ms": int((time.time() - t) * 1000),
                "why": f"{type(e).__name__}: {str(e)[:120]}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("HQ_BASE", "https://huhqueue-studio.onrender.com"))
    ap.add_argument("--json", default="")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--no-demo", action="store_true", help="skip the signed-in API checks")
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    results = []
    with httpx.Client(base_url=base, follow_redirects=True, headers={"User-Agent": "huhqueue-live-smoke"}) as anon:
        for p in PAGES:
            results.append(check(anon, p, a.timeout))
    if not a.no_demo:
        with httpx.Client(base_url=base, follow_redirects=False, headers={"User-Agent": "huhqueue-live-smoke"}) as c:
            try:
                r = c.get("/demo", timeout=a.timeout)
                signed_in = r.status_code in (302, 303) and "error=" not in r.headers.get("location", "") and bool(c.cookies)
                results.append({"path": "/demo", "status": r.status_code, "ok": signed_in,
                                **({} if signed_in else {"why": "no sample session: " + r.headers.get("location", "")[:160]})})
            except Exception as e:  # noqa: BLE001
                signed_in = False
                results.append({"path": "/demo", "status": None, "ok": False, "why": f"{type(e).__name__}: {str(e)[:120]}"})
            if signed_in:
                for p in APIS:
                    results.append(check(c, p, a.timeout))
    bad = [r for r in results if not r["ok"]]
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['status']!s:>4} {r.get('ms', 0):>6}ms {r['path']}"
              + (f"  ({r['why']})" if r.get("why") else "") + (f"  commit={r['commit']}" if r.get("commit") else ""))
    print(f"\n{len(results) - len(bad)}/{len(results)} ok")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"base": base, "results": results, "failed": len(bad)}, f, indent=2)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
