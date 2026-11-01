"""Live-check each AI/reader provider HuhQueue uses with one tiny call, printing only the provider, HTTP status and ok.

    ANTHROPIC_API_KEY=... ANTHROPIC_WORKSPACE_ID=... TYPESAFE_API_KEY=... SUBQ_API_KEY=... FIRECRAWL_API_KEY=... \
    CONTEXT_DEV_API_KEY=... python scripts/provider_check.py

Same endpoints, headers and request shapes as the app (brain_llm, vision, reader, onboarding). Never prints a key, a
header or a response body. Anthropic: one Haiku call, max_tokens 4 (well under a cent).
"""
from __future__ import annotations

import base64
import os
import sys
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from studio import brain_llm, onboarding, reader, vision  # noqa: E402


def tiny_jpeg() -> str:
    import io

    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (320, 320), (200, 120, 40)).save(b, "JPEG")
    return base64.b64encode(vision.prepare(b.getvalue()) or b.getvalue()).decode()


def main() -> int:
    c = httpx.Client(timeout=60)
    out = []

    def rec(name, fn):
        if not fn[1]:
            out.append((name, "skipped (no key)", False))
            return
        try:
            r = fn[0]()
            out.append((name, f"HTTP {r.status_code}", r.status_code < 300))
        except httpx.HTTPError as e:
            out.append((name, e.__class__.__name__, False))

    key = brain_llm.operator_key()
    hdr = brain_llm.anthropic_headers(key) if key else {}
    rec("anthropic models (workspace header: %s)" % ("yes" if "anthropic-workspace-id" in hdr else "no"),
        (lambda: c.get(brain_llm.ANTHROPIC + "/models?limit=5", headers=hdr), key))
    rec("anthropic messages (haiku, 4 tokens)",
        (lambda: c.post(brain_llm.ANTHROPIC + "/messages", headers=hdr, json={
            "model": os.environ.get("HQ_CHECK_HAIKU", "claude-haiku-4-5"), "max_tokens": 4,
            "messages": [{"role": "user", "content": "Say ok."}]}), key))
    img, ref = tiny_jpeg(), f"img-{uuid.uuid4()}"
    for name, url, model, env in vision.PROVIDERS:
        k = os.environ.get(env, "")
        rec(f"{name} vision ({url.split('/')[2]})", (lambda url=url, model=model, k=k: c.post(url, headers={
            "Authorization": f"Bearer {k}", "TypeSafe-Danger-Zone-Images": "true"}, json={
            "model": model, "state": {"picture": ref}, "assets": {ref: {"type": "image", "format": "jpg", "data_base64": img}},
            "questions": {"orange": {"type": "noul", "instructions": "Is `picture` mostly orange?"}}}), k))
    fk = os.environ.get("FIRECRAWL_API_KEY", "")
    rec("firecrawl scrape", (lambda: c.post(reader.FIRECRAWL, headers={"Authorization": f"Bearer {fk}"}, json={
        "url": "https://example.com", "formats": ["markdown"], "onlyMainContent": True, "timeout": 25000}), fk))
    ck = os.environ.get("CONTEXT_DEV_API_KEY", "")
    rec("context.dev brand", (lambda: c.post(onboarding.CONTEXT_DEV, headers={"Authorization": f"Bearer {ck}"},
                                             json={"type": "by_domain", "domain": "buffer.com"}), ck))
    for name, status, ok in out:
        print(f"{'OK ' if ok else 'BAD'} {name}: {status}")
    return 0 if all(ok for _, _, ok in out) else 1


if __name__ == "__main__":
    sys.exit(main())
