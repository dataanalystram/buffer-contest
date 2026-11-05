"""Commerce on Channel3: the web-only half (REST routes, /go, the BYO key forms, the consent registration).

The core (studio/commerce.py) stays importable on the video worker; this module holds what needs FastAPI and the web
app's integrations storage. See docs/COMMERCE.md.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import consent, demo_guard
from .actions import Actor
from .commerce import (
    BYO_FIELD,
    DISCLOSURE,
    FNS,
    FREE_CREDITS,
    GO_PER_MIN,
    KEY_PATTERN,
    SEARCH_CREDITS,
    CommerceError,
    CreditLimit,
    _limit,
    _request,
    _str,
    clear_scope,
    ensure,
    fallback_page,
    key_view,
    resolve_click,
    usage,
    uses_huhqueue_key,
    with_disclosure,
)


def save_key(studio, a: Actor, body: Any) -> dict:
    if a.kind != "human":
        raise CommerceError("Only a person can add a Channel3 key.", 403)
    if not isinstance(body, dict):
        raise CommerceError("Send a JSON object.", 400)
    v = body.get("value")
    if not isinstance(v, str) or not KEY_PATTERN.fullmatch(v.strip()):
        raise CommerceError("Paste the whole Channel3 API key (no spaces).", 400)
    v = v.strip()
    _limit("k:" + a.uid, 10, "key tests")
    _request(v, "GET", "/v1/categories", uid=a.uid, params={"limit": 1}, byo=True)   # free call: the key works
    from . import integrations
    cfg, sec = integrations._row(studio.store, a.uid)
    sec[BYO_FIELD] = v
    cfg.setdefault("integrations", {})["c3"] = {"saved": time.time()}
    integrations._write(studio.store, a.uid, cfg, sec)
    clear_scope(a.uid)
    studio.store.audit(a.uid, a.label, "commerce_key_saved", "read", {"integration": "channel3"})
    return key_view(studio.store, a.uid)


def remove_key(studio, a: Actor) -> dict:
    if a.kind != "human":
        raise CommerceError("Only a person can remove a Channel3 key.", 403)
    from . import integrations
    cfg, sec = integrations._row(studio.store, a.uid)
    sec.pop(BYO_FIELD, None)
    (cfg.get("integrations") or {}).pop("c3", None)
    integrations._write(studio.store, a.uid, cfg, sec)
    clear_scope(a.uid)
    studio.store.audit(a.uid, a.label, "commerce_key_removed", "read", {"integration": "channel3"})
    return key_view(studio.store, a.uid)


# ================================================================================================ consent registration
def _always(_studio, _a, _args) -> bool:
    return True


def _on_huhqueue_key(studio, a, _args) -> bool:
    return uses_huhqueue_key(studio.store, a.uid)


def register_consent() -> None:
    from . import consent
    consent.register("search_products", "Channel3", "a product search (1 free credit)",
                     lambda _a: {"credits": SEARCH_CREDITS, "usd": 0, "free_credits_month": FREE_CREDITS, "free_only": True},
                     needs=_always)
    consent.register("get_product", "Channel3", "product details on HuhQueue's Channel3 key (free call)",
                     lambda _a: {"credits": 0, "usd": 0}, needs=_on_huhqueue_key)
    consent.register("attach_product_to_post", "Channel3", "a shop link on HuhQueue's Channel3 key (free call)",
                     lambda _a: {"credits": 0, "usd": 0}, needs=_always)


register_consent()


# ================================================================================================ REST
def add_routes(app, house, actor) -> None:
    studio = house.studio
    ensure(studio.store)

    def _run(name: str, request: Request, body: Any, write: bool):
        a = actor(request, write=write)
        if body is not None and not isinstance(body, dict):
            raise CommerceError("Send a JSON object.", 400)
        args = dict(body or {})
        if a.kind == "agent" and consent.gated(name, args, set()):   # a bearer token on REST: the same approval gate as MCP
            wait = consent.gate(studio, a, name, args)
            if wait is not None:
                return wait
        try:
            return FNS[name](studio, a, {k: v for k, v in args.items() if k != "approval_id"})
        except CreditLimit as e:
            return JSONResponse({"error": str(e), "reason": "credit_limit", "resets": e.resets,
                                 "usage": usage(studio.store, a.uid)}, status_code=429)

    @app.post("/api/commerce/search")
    def commerce_search(request: Request, body: Any = Body(default=None)):
        """search_products: {query, budget_min, budget_max, country, currency, in_stock, limit}; 1 free credit."""
        return _run("search_products", request, body, True)

    @app.get("/api/commerce/products/{product_id}")
    def commerce_product(product_id: str, request: Request, country: str = "", currency: str = ""):
        """get_product: fresh price and offers (free)."""
        return _run("get_product", request, {"product_id": product_id, "country": country, "currency": currency}, False)

    @app.post("/api/commerce/attach")
    def commerce_attach(request: Request, body: Any = Body(default=None)):
        """attach_product_to_post: {run_id, product_id, caption, cta, link_label} -> /go link, card, caption with #Ad."""
        return _run("attach_product_to_post", request, body, True)

    @app.post("/api/commerce/caption")
    def commerce_caption(request: Request, body: Any = Body(default=None)):
        """The caption helper alone: {caption, link, affiliate} -> caption with #Ad first and the disclosure line."""
        actor(request)
        if not isinstance(body, dict):
            raise CommerceError("Send a JSON object.", 400)
        cap = _str(body, "caption", 5000)
        link = _str(body, "link", 300)
        aff = body.get("affiliate", True)
        if not isinstance(aff, bool):
            raise CommerceError("affiliate must be true or false.", 400)
        return {"caption": with_disclosure(cap, link, aff), "disclosure": DISCLOSURE if aff else ""}

    @app.get("/api/commerce/usage")
    def commerce_usage(request: Request):
        a = actor(request)
        return usage(studio.store, a.uid)

    @app.get("/api/commerce/key")
    def commerce_key(request: Request):
        return key_view(studio.store, actor(request).uid)

    @app.post("/api/commerce/key")
    def commerce_key_save(request: Request, body: Any = Body(default=None)):
        return save_key(studio, actor(request, write=True), body)

    @app.delete("/api/commerce/key")
    def commerce_key_remove(request: Request):
        return remove_key(studio, actor(request, write=True))

    @app.get("/go/{code}", include_in_schema=False)
    def go(code: str, request: Request):
        try:
            _limit("go:" + demo_guard.client_ip(request), GO_PER_MIN, "clicks")
        except CommerceError:
            return HTMLResponse("Too many clicks from this network. Wait a minute.", status_code=429, headers={"Retry-After": "60"})
        target, info = resolve_click(studio, code)
        if target:
            return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                                      "X-Robots-Tag": "noindex"})
        return HTMLResponse(fallback_page(info.get("title", "")), status_code=404 if not info else 410,
                            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
