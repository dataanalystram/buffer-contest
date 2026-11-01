"""Settings -> Integrations: the key-based integrations a person pastes once (Product Hunt, X, YouTube Data API).

One card per integration: what it unlocks, how to get a key, a password field, Test (a live check that reports a status
only), Save, Remove and a masked status ("Connected · …abcd · tested 2 min ago").

Storage: the same sealed per-user secret the Sources agent already reads (source_settings.secret, Fernet via
Store.seal/unseal, rotated with TOKEN_KEY), so the Product Hunt adapter, the X reader and media search pick a key up the
moment it is saved. A key value is never returned to the browser, never logged and never put in an audit row; only its
last four characters are shown. Only a signed-in person can add, test or remove a key (never an agent token).

Product Hunt OAuth (optional): when PH_CLIENT_ID and PH_CLIENT_SECRET are set on the server, /integrations/producthunt/
connect starts the authorization-code flow with a one-time state sealed into the person's session, and
/integrations/producthunt/callback exchanges the code (POST https://api.producthunt.com/v2/oauth/token) and stores the
token for that person. A code without our state is never exchanged (login-CSRF). Without the env vars the callback is a
friendly page that tells the person to copy their Developer Token instead (the Redirect URI the Product Hunt form needs).

Security (work/plans/SECURITY_RULES.md): every live check goes through netguard (safe_get / safe_post: host pinned, no
redirects, bytes capped); keys travel in headers or are stripped from anything echoed; tests are rate-capped per person.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import Body, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import demo_guard, netguard

UA = {"User-Agent": "Mozilla/5.0 (HuhQueue Integrations; +https://huhqueue-studio.onrender.com)"}
PH_CALLBACK_PATH = "/integrations/producthunt/callback"
PH_CONNECT_PATH = "/integrations/producthunt/connect"
PH_AUTHORIZE = "https://api.producthunt.com/v2/oauth/authorize"
PH_TOKEN = "https://api.producthunt.com/v2/oauth/token"
PH_GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
TESTS_PER_HOUR = 20


class IntegrationError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def catalog(public_url: str) -> dict[str, dict]:
    cb = public_url.rstrip("/") + PH_CALLBACK_PATH
    return {
        "ph": {"field": "ph_token", "pattern": r"[A-Za-z0-9_\-.=]{20,200}", "name": "Product Hunt", "what": "developer token",
               "unlocks": "Today's top Product Hunt launches in News → Sources and the story scan.",
               "get_url": "https://api.producthunt.com/v2/oauth/applications",
               "steps": ["Open api.producthunt.com/v2/oauth/applications and choose Add an application.",
                         f"Name it HuhQueue, set the Redirect URI to {cb}, and create it.",
                         "On the application page choose Create Token, copy the Developer Token and paste it here."],
               "redirect_uri": cb, "source": "ph"},
        "x": {"field": "x_bearer", "pattern": r"[A-Za-z0-9%_\-.=]{20,200}", "name": "X / Twitter", "what": "API bearer token",
              "unlocks": "Reads X posts and their media through the official API (Make a post with an X link, and the X source in News).",
              "get_url": "https://developer.x.com/en/portal/dashboard",
              "steps": ["Open the X developer portal and pick (or create) a project and app.",
                        "Under Keys and tokens, generate the Bearer Token.",
                        "Copy it and paste it here. HuhQueue never scrapes logged-in X."],
              "source": "x"},
        "yt": {"field": "yt_key", "pattern": r"[A-Za-z0-9_\-]{30,60}", "name": "YouTube Data API", "what": "API key",
               "unlocks": "Find media searches YouTube for a launch or demo video with your own quota.",
               "get_url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
               "steps": ["In Google Cloud console, enable the YouTube Data API v3 for a project.",
                         "Go to APIs & Services → Credentials → Create credentials → API key.",
                         "Copy the key (restrict it to the YouTube Data API) and paste it here."],
               "source": ""},
    }


# ================================================================================================ storage
def _row(store, uid: str) -> tuple[dict, dict]:
    rows = store.q("SELECT data, secret FROM source_settings WHERE user_id=?", (uid,))
    cfg = json.loads(rows[0]["data"]) if rows and rows[0]["data"] else {}
    sec = (store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
    return cfg, sec


def _write(store, uid: str, cfg: dict, sec: dict) -> None:
    store.x("INSERT INTO source_settings(user_id,data,secret,updated) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, secret=excluded.secret, updated=excluded.updated",
            (uid, json.dumps(cfg), store.seal(sec) if sec else "", time.time()))


def user_secret(store, uid: str, field: str) -> str:
    """One sealed key for server-side use (never for a response)."""
    try:
        return _row(store, uid)[1].get(field, "") or ""
    except Exception:  # noqa: BLE001 - a missing table or unreadable row means "no key"
        return ""


# ================================================================================================ live checks
def _status_message(name: str, code: int) -> str:
    if code in (401, 403):
        return f"{name} refused this key (HTTP {code}). Check you copied the whole key."
    if code == 429:
        return f"{name} is rate-limiting right now; the key may still be fine. Try again in a few minutes."
    return f"{name} answered HTTP {code}."


def check_key(key: str, value: str, http=None) -> dict:
    """A live check of one key. Returns {ok, message}; the key itself never appears in the message or an exception."""
    try:
        if key == "ph":
            r = netguard.safe_post(PH_GRAPHQL, http=http, json={"query": "query { posts(first: 1) { edges { node { id } } } }"},
                                   headers={**UA, "Authorization": f"Bearer {value}", "Accept": "application/json"},
                                   timeout=12, allowed_hosts={"api.producthunt.com"}, limit=200_000)
            name = "Product Hunt"
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    return {"ok": False, "message": "Product Hunt didn't return JSON."}
                if body.get("errors") and not body.get("data"):
                    return {"ok": False, "message": "Product Hunt refused this token. Use the Developer Token from your application page."}
                return {"ok": True, "message": "Product Hunt accepted the token."}
        elif key == "x":
            r = netguard.safe_get("https://api.x.com/2/usage/tweets", http=http, headers={**UA, "Authorization": f"Bearer {value}"},
                                  limit=200_000, timeout=12, https_only=True)
            name = "X"
            if r.status_code == 200:
                return {"ok": True, "message": "X accepted the bearer token."}
        elif key == "yt":
            url = "https://www.googleapis.com/youtube/v3/videoCategories?" + urlencode({"part": "id", "regionCode": "US", "key": value})
            r = netguard.safe_get(url, http=http, headers=UA, limit=200_000, timeout=12, https_only=True)
            name = "YouTube"
            if r.status_code == 200:
                return {"ok": True, "message": "YouTube accepted the API key."}
            if r.status_code == 400:  # Google answers 400 "API key not valid"
                return {"ok": False, "message": "YouTube says this API key isn't valid."}
        else:
            raise IntegrationError("Unknown integration.", 404)
        return {"ok": False, "message": _status_message(name, r.status_code)}
    except netguard.FetchError:
        return {"ok": False, "message": "The check couldn't reach the service safely. Try again in a moment."}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"The service didn't answer ({e.__class__.__name__})."}


# ================================================================================================ the service
class Integrations:
    def __init__(self, house, public_url: str):
        self.house, self.store, self.public_url = house, house.store, public_url.rstrip("/")
        self.cards = catalog(self.public_url)
        self._tests: dict[str, list[float]] = {}
        from . import sources_agent
        sources_agent.ensure(self.store)

    @property
    def http(self):
        return getattr(self.house, "http", None)

    def _person(self, a, what: str = "manage keys") -> None:
        if a.kind != "human":
            raise IntegrationError(f"Only a person can {what}.", 403)

    def _card(self, key: str) -> dict:
        if key not in self.cards:
            raise IntegrationError("Unknown integration.", 404)
        return self.cards[key]

    def view(self, a) -> dict:
        cfg, sec = _row(self.store, a.uid)
        meta = cfg.get("integrations") or {}
        out = []
        for k, c in self.cards.items():
            val = sec.get(c["field"]) or ""
            m = meta.get(k) or {}
            out.append({"key": k, "name": c["name"], "what": c["what"], "unlocks": c["unlocks"], "get_url": c["get_url"],
                        "steps": c["steps"], "redirect_uri": c.get("redirect_uri", ""), "connected": bool(val),
                        "hint": ("…" + val[-4:]) if val else "", "tested": m.get("tested") if val else None,
                        "tested_ok": m.get("ok") if val else None, "test_message": m.get("message", "") if val else "",
                        "saved": m.get("saved") if val else None,
                        "oauth": k == "ph" and bool(os.environ.get("PH_CLIENT_ID") and os.environ.get("PH_CLIENT_SECRET"))})
        return {"integrations": out, "ai_keys": "settings#aiH", "sample": demo_guard.is_demo(a.uid)}

    def _valid(self, c: dict, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise IntegrationError(f"Paste your {c['name']} {c['what']} first.")
        if not re.fullmatch(c["pattern"], value):
            raise IntegrationError(f"That doesn't look like a {c['name']} {c['what']}.")
        return value

    def _rate(self, uid: str) -> None:
        now = time.time()
        hits = [t for t in self._tests.get(uid, []) if t > now - 3600]
        if len(hits) >= TESTS_PER_HOUR:
            raise IntegrationError("Too many key tests this hour. Try again later.", 429)
        hits.append(now)
        self._tests[uid] = hits

    def save(self, a, key: str, body: dict) -> dict:
        self._person(a)
        c = self._card(key)
        value = self._valid(c, str((body or {}).get("value") or ""))
        cfg, sec = _row(self.store, a.uid)
        sec[c["field"]] = value
        if c["source"]:
            cfg.setdefault("on", {})[c["source"]] = True
        cfg.setdefault("integrations", {})[key] = {"saved": time.time()}
        _write(self.store, a.uid, cfg, sec)
        self.store.audit(a.uid, a.label, "integration_saved", "read", {"integration": key})
        return self.view(a)

    def remove(self, a, key: str) -> dict:
        self._person(a)
        c = self._card(key)
        cfg, sec = _row(self.store, a.uid)
        sec.pop(c["field"], None)
        if c["source"]:
            cfg.setdefault("on", {})[c["source"]] = False
        (cfg.get("integrations") or {}).pop(key, None)
        _write(self.store, a.uid, cfg, sec)
        self.store.audit(a.uid, a.label, "integration_removed", "read", {"integration": key})
        return self.view(a)

    def test(self, a, key: str, body: dict) -> dict:
        """Test the pasted value (without saving it) or, with no value, the saved key. Status only, never the key."""
        self._person(a, "test keys")
        c = self._card(key)
        pasted = str((body or {}).get("value") or "").strip()
        cfg, sec = _row(self.store, a.uid)
        value = self._valid(c, pasted) if pasted else sec.get(c["field"], "")
        if not value:
            raise IntegrationError(f"Paste your {c['name']} {c['what']} to test it.")
        if demo_guard.is_demo(a.uid):
            return {"ok": False, "key": key, "message": "The sample workspace never calls outside services. Connect Buffer to test a real key.",
                    "tested": time.time()}
        self._rate(a.uid)
        res = check_key(key, value, self.http)
        now = time.time()
        if not pasted or pasted == sec.get(c["field"]):  # remember the result against the saved key only
            cfg.setdefault("integrations", {}).setdefault(key, {}).update({"tested": now, "ok": res["ok"], "message": res["message"]})
            _write(self.store, a.uid, cfg, sec)
        self.store.audit(a.uid, a.label, "integration_tested", "read", {"integration": key, "ok": res["ok"]}, ok=res["ok"])
        return {**res, "key": key, "tested": now}

    # ---------------------------------------------------------------- Product Hunt OAuth
    def ph_configured(self) -> bool:
        return bool(os.environ.get("PH_CLIENT_ID") and os.environ.get("PH_CLIENT_SECRET"))

    def ph_authorize_url(self, state: str) -> str:
        return PH_AUTHORIZE + "?" + urlencode({"client_id": os.environ.get("PH_CLIENT_ID", ""), "redirect_uri": self.public_url + PH_CALLBACK_PATH,
                                                "response_type": "code", "scope": "public", "state": state})

    def ph_exchange(self, uid: str, code: str) -> tuple[bool, str]:
        if not re.fullmatch(r"[A-Za-z0-9_\-.~]{4,400}", code or ""):
            return False, "That sign-in code doesn't look right."
        try:
            r = netguard.safe_post(PH_TOKEN, http=self.http, json={
                "client_id": os.environ.get("PH_CLIENT_ID", ""), "client_secret": os.environ.get("PH_CLIENT_SECRET", ""),
                "redirect_uri": self.public_url + PH_CALLBACK_PATH, "code": code, "grant_type": "authorization_code"},
                headers={**UA, "Accept": "application/json"}, timeout=15, allowed_hosts={"api.producthunt.com"}, limit=100_000)
        except (netguard.FetchError, httpx.HTTPError):
            return False, "Product Hunt couldn't be reached to finish connecting."
        if r.status_code != 200:
            return False, f"Product Hunt didn't accept the sign-in (HTTP {r.status_code})."
        try:
            tok = str(r.json().get("access_token") or "")
        except ValueError:
            tok = ""
        if not re.fullmatch(self.cards["ph"]["pattern"], tok):
            return False, "Product Hunt didn't send back a usable token."
        cfg, sec = _row(self.store, uid)
        sec["ph_token"] = tok
        cfg.setdefault("on", {})["ph"] = True
        cfg.setdefault("integrations", {})["ph"] = {"saved": time.time(), "via": "oauth"}
        _write(self.store, uid, cfg, sec)
        self.store.audit(uid, "you", "integration_saved", "read", {"integration": "ph", "via": "oauth"})
        return True, "Product Hunt is connected."


# ================================================================================================ the callback page
def callback_page(title: str, body: str, ok: bool = True) -> str:
    """A small standalone page (no script: it renders under script-src 'self')."""
    t, b = html.escape(title), body
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{t} · HuhQueue</title><link rel="icon" href="/favicon.ico">
<style>body{{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0f0f12;color:#f2f2f4;display:grid;place-items:center;min-height:100vh}}
main{{max-width:560px;margin:24px;padding:32px;border-radius:16px;background:#18181d;border:1px solid #2a2a31}}
h1{{font-size:22px;margin:0 0 12px}} .mark{{display:inline-grid;place-items:center;width:40px;height:40px;border-radius:12px;margin-bottom:16px;
background:{'#1f3d2b' if ok else '#3d2a1f'};color:{'#6ee7a0' if ok else '#ffb37a'};font-weight:700}} ol{{padding-left:20px}} li{{margin:6px 0}}
code{{background:#24242b;padding:2px 6px;border-radius:6px;word-break:break-all}} a.btn{{display:inline-block;margin-top:16px;padding:10px 16px;border-radius:10px;
background:#ff5a1f;color:#fff;text-decoration:none;font-weight:600}} .muted{{color:#a6a6b0;font-size:14px}}</style></head>
<body><main data-testid="ph-callback"><div class="mark" aria-hidden="true">{'✓' if ok else '!'}</div><h1>{t}</h1>{b}
<a class="btn" href="/?tab=settings&amp;sub=integrations" data-testid="ph-callback-settings">Open Settings → Integrations</a></main></body></html>"""


def created_body(redirect_uri: str) -> str:
    return ("<p>Your Product Hunt application is set up. One step left: copy your <b>Developer Token</b> from the Product Hunt "
            "API dashboard and paste it in <b>HuhQueue → Settings → Integrations</b>.</p><ol>"
            "<li>Open <a href=\"https://api.producthunt.com/v2/oauth/applications\" rel=\"noopener\" target=\"_blank\" style=\"color:#ffb37a\">"
            "api.producthunt.com/v2/oauth/applications</a> and open your application.</li>"
            "<li>Choose <b>Create Token</b> and copy the Developer Token.</li>"
            "<li>In HuhQueue, open Settings → Integrations → Product Hunt, paste it, then Test and Save.</li></ol>"
            f"<p class=\"muted\">The Redirect URI for your application is <code>{html.escape(redirect_uri)}</code>.</p>")


def new_state() -> str:
    return secrets.token_urlsafe(24)


# ================================================================================================ routes
STATE_COOKIE = "hq_ph_state"
STATE_TTL = 600


def add_routes(app, house, actor, cookie_name: str, public_url: str, secure_cookies: bool = True) -> Integrations:
    """REST for the cards (person-only) and the two Product Hunt browser routes. Registered from app.create_app."""
    from .actions import ActionError

    integ = Integrations(house, public_url)
    house.integrations = integ
    store = house.store

    def call(fn, *args):
        try:
            return fn(*args)
        except IntegrationError as e:
            raise ActionError(str(e), e.status)

    @app.get("/api/integrations")
    def integ_list(request: Request):
        return integ.view(actor(request))

    @app.post("/api/integrations/{key}")
    def integ_save(key: str, request: Request, body: dict = Body(default={})):
        return call(integ.save, actor(request, write=True), key, body)

    @app.post("/api/integrations/{key}/test")
    def integ_test(key: str, request: Request, body: dict = Body(default={})):
        return call(integ.test, actor(request, write=True), key, body)

    @app.delete("/api/integrations/{key}")
    def integ_remove(key: str, request: Request):
        return call(integ.remove, actor(request, write=True), key)

    def _uid(request: Request) -> str:
        sess = store.session(request.cookies.get(cookie_name))
        uid = (sess or {}).get("user_id") or ""
        return uid if uid and store.user(uid) else ""

    def _page(title: str, body: str, ok: bool = True, status: int = 200) -> HTMLResponse:
        r = HTMLResponse(callback_page(title, body, ok), status_code=status)
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Robots-Tag"] = "noindex"
        return r

    @app.get(PH_CONNECT_PATH, include_in_schema=False)
    def ph_connect(request: Request):
        uid = _uid(request)
        if not uid:
            return _page("Sign in first", "<p>Open HuhQueue and connect your Buffer account, then try again from Settings → Integrations.</p>", False, 401)
        if not integ.ph_configured() or demo_guard.is_demo(uid):
            return _page("Use your Developer Token", created_body(integ.cards["ph"]["redirect_uri"]), True)
        nonce = new_state()
        resp = RedirectResponse(integ.ph_authorize_url(nonce), 302)
        resp.set_cookie(STATE_COOKIE, store.seal({"n": nonce, "uid": uid, "exp": time.time() + STATE_TTL}), max_age=STATE_TTL,
                        httponly=True, secure=secure_cookies, samesite="lax", path="/integrations/producthunt")
        return resp

    @app.get(PH_CALLBACK_PATH, include_in_schema=False)
    def ph_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        redirect_uri = integ.cards["ph"]["redirect_uri"]
        if error:
            return _page("Product Hunt didn't connect", f"<p>Product Hunt answered: {html.escape(error[:80])}. You can still paste your "
                         "Developer Token in Settings → Integrations.</p>" + created_body(redirect_uri), False)
        if not code:
            return _page("Product Hunt app created", created_body(redirect_uri))
        uid = _uid(request)
        pending = store.unseal(request.cookies.get(STATE_COOKIE)) or {}
        valid = (bool(uid) and bool(state) and pending.get("uid") == uid and float(pending.get("exp") or 0) > time.time()
                 and secrets.compare_digest(str(pending.get("n") or ""), state))
        if not valid or not integ.ph_configured():
            if uid:
                store.audit(uid, "system", "integration_failed", "auth", {"integration": "ph", "reason": "state mismatch or not configured"}, ok=False)
            r = _page("Almost there", "<p>This sign-in didn't start from your HuhQueue session, so it wasn't used.</p>" + created_body(redirect_uri), False)
        else:
            ok, msg = integ.ph_exchange(uid, code)
            r = _page("Product Hunt connected" if ok else "Product Hunt didn't connect",
                      f"<p>{html.escape(msg)}</p>" + ("<p>Today's launches now show up in News → Sources.</p>" if ok else created_body(redirect_uri)), ok)
        r.delete_cookie(STATE_COOKIE, path="/integrations/producthunt")
        return r

    return integ
