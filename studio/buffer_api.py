"""Buffer OAuth 2.0 (Authorization Code + PKCE, private client) and GraphQL client.

* Tokens live only in the server-side store; the browser holds an opaque session id.
* Refresh tokens rotate: every refresh stores the replacement before the call continues, under a
  per-user lock so two requests never spend the same refresh token.
* Rate limits are per app client (shared by every user): we read the RateLimit headers on every
  response, cache org/channel lists, wait out a short Retry-After once and surface longer ones.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import API_URL, AUTH_URL, SCOPES, TOKEN_URL, Settings
from .store import Store

MAX_BACKOFF = 5
CHANNEL_TTL = 300  # seconds; channel lists change rarely, so cache them per user


class BufferError(Exception):
    status = 502

    def __init__(self, message: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        if status:
            self.status = status
        self.retry_after = retry_after


class NotConnected(BufferError):
    status = 401


# ---------------------------------------------------------------- OAuth
def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(s: Settings, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": s.client_id, "redirect_uri": s.redirect_uri, "response_type": "code",
        "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "prompt": "consent",
    })


def _token_request(s: Settings, http: httpx.Client, params: dict, previous: dict | None = None) -> dict:
    body = {"client_id": s.client_id, **params}
    if s.client_secret:
        body["client_secret"] = s.client_secret  # client_secret_post; never HTTP Basic
    r = http.post(TOKEN_URL, data=body, timeout=20)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code >= 400 or j.get("error"):
        raise BufferError(f"Buffer sign-in failed: {j.get('error_description') or j.get('error') or r.status_code}", 401)
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token"),
        "scope": j.get("scope") or (previous or {}).get("scope", ""),
        "expires_at": time.time() + float(j.get("expires_in") or 3600) - 60,
    }


def exchange_code(s: Settings, http: httpx.Client, code: str, verifier: str) -> dict:
    return _token_request(s, http, {"grant_type": "authorization_code", "code": code,
                                    "redirect_uri": s.redirect_uri, "code_verifier": verifier})


# ---------------------------------------------------------------- rate-limit telemetry (app-wide)
class RateBudget:
    """Latest RateLimit header readings for this app client, keyed by window length in seconds."""

    def __init__(self):
        self.windows: dict[int, dict] = {}
        self.blocked_until = 0.0

    def read(self, headers: httpx.Headers) -> None:
        live = ",".join(headers.get_list("ratelimit")) if hasattr(headers, "get_list") else headers.get("ratelimit", "")
        pol = ",".join(headers.get_list("ratelimit-policy")) if hasattr(headers, "get_list") else headers.get("ratelimit-policy", "")
        quotas = {}
        for p in re.split(r',\s*(?=")', pol or ""):
            m_name, m_q, m_w = re.search(r'"([^"]+)"', p), re.search(r"q=(\d+)", p), re.search(r"w=(\d+)", p)
            if m_name and m_q and m_w:
                quotas[m_name.group(1)] = (int(m_q.group(1)), int(m_w.group(1)))
        for p in re.split(r',\s*(?=")', live or ""):
            m_name, m_r, m_t = re.search(r'"([^"]+)"', p), re.search(r"r=(\d+)", p), re.search(r"t=(\d+)", p)
            if not (m_name and m_r):
                continue
            q, w = quotas.get(m_name.group(1), (None, None))
            if w is None:
                m = re.match(r"(\d+)-in-(\d+)(min|day|days)", m_name.group(1))
                if m:
                    q = int(m.group(1))
                    w = int(m.group(2)) * (60 if m.group(3) == "min" else 86400)
            if w:
                self.windows[w] = {"name": m_name.group(1), "remaining": int(m_r.group(1)), "quota": q,
                                   "reset_in": int(m_t.group(1)) if m_t else None, "at": time.time()}

    def snapshot(self) -> dict:
        labels = {900: "15 min", 86400: "24 h", 2592000: "30 days"}
        return {"windows": [{"window": labels.get(w, f"{w}s"), **v} for w, v in sorted(self.windows.items())],
                "blocked_for": max(0, int(self.blocked_until - time.time()))}

    def low(self) -> bool:
        w = self.windows.get(900)
        return bool(w and w["remaining"] is not None and w["remaining"] < 5)


# ---------------------------------------------------------------- GraphQL client
class BufferClient:
    def __init__(self, s: Settings, store: Store, http: httpx.Client | None = None):
        self.s, self.store = s, store
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "HuhQueue-Studio/1.0 (+built on the Buffer API)"})
        self.budget = RateBudget()
        self._locks: dict[str, threading.Lock] = {}
        self._cache: dict[tuple, tuple[float, Any]] = {}

    def _lock(self, uid: str) -> threading.Lock:
        return self._locks.setdefault(uid, threading.Lock())

    def access_token(self, uid: str) -> str:
        with self._lock(uid):
            tok = self.store.tokens(uid)
            if not tok:
                raise NotConnected("Not connected to Buffer. Connect again to continue.")
            if time.time() < tok["expires_at"]:
                return tok["access_token"]
            if not tok.get("refresh_token"):
                self.store.save_tokens(uid, None)
                raise NotConnected("Your Buffer session expired. Connect again to continue.")
            try:
                new = _token_request(self.s, self.http, {"grant_type": "refresh_token",
                                                         "refresh_token": tok["refresh_token"]}, tok)
            except BufferError:
                self.store.save_tokens(uid, None)
                raise NotConnected("Your Buffer session expired. Connect again to continue.")
            if not new.get("refresh_token"):
                self.store.save_tokens(uid, None)
                raise NotConnected("Buffer did not return a replacement refresh token. Connect again.")
            self.store.save_tokens(uid, new)  # rotate: the old refresh token is now dead
            return new["access_token"]

    def gql(self, query: str, variables: dict | None = None, *, uid: str | None = None,
            token: str | None = None, _retried: bool = False) -> dict:
        if self.budget.blocked_until > time.time():
            wait = int(self.budget.blocked_until - time.time()) + 1
            raise BufferError(f"Buffer API rate limit reached; try again in {wait}s", 429, wait)
        bearer = token or self.access_token(uid)
        r = self.http.post(API_URL, json={"query": query, "variables": variables or {}},
                           headers={"Authorization": f"Bearer {bearer}"})
        self.budget.read(r.headers)
        if r.status_code == 401:
            if uid and not _retried:
                tok = self.store.tokens(uid) or {}
                tok["expires_at"] = 0  # force one refresh, then give up
                self.store.save_tokens(uid, tok if tok.get("refresh_token") else None)
                return self.gql(query, variables, uid=uid, _retried=True)
            raise NotConnected("Buffer rejected the access token. Connect again.")
        if r.status_code == 429:
            ra = int(float(r.headers.get("retry-after") or 0))
            if not _retried and 0 < ra <= MAX_BACKOFF:
                time.sleep(ra)
                return self.gql(query, variables, uid=uid, token=token, _retried=True)
            self.budget.blocked_until = time.time() + (ra or 30)
            raise BufferError(f"Buffer API rate limit reached; try again in {ra or 30}s", 429, ra or 30)
        try:
            j = r.json()
        except ValueError:
            raise BufferError(f"Buffer returned HTTP {r.status_code}")
        if r.status_code >= 400 or j.get("errors"):
            e = (j.get("errors") or [{}])[0]
            code = (e.get("extensions") or {}).get("code", "")
            raise BufferError(f"Buffer could not complete the request ({code or r.status_code}: {e.get('message', '')})".strip())
        return j["data"]

    # ---- cached reads
    def _cached(self, key: tuple, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and (time.time() - hit[0] < ttl or self.budget.low()):
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def invalidate(self, uid: str) -> None:
        for k in [k for k in self._cache if k[1] == uid]:
            self._cache.pop(k, None)

    def account(self, *, uid: str | None = None, token: str | None = None) -> dict:
        q = "query { account { id email name organizations { id name } } }"
        return self.gql(q, uid=uid, token=token)["account"]

    def _gql_or(self, rich: str, plain: str | None, variables: dict, uid: str):
        """Ask for the richer field set; if Buffer refuses it (schema change, plan), fall back instead of breaking."""
        try:
            return self.gql(rich, variables, uid=uid)
        except NotConnected:
            raise
        except BufferError as e:
            if e.status == 429 or plain is None:
                raise
            return self.gql(plain, variables, uid=uid)

    def channels(self, uid: str, org_id: str, refresh: bool = False) -> list[dict]:
        q = """query($o: OrganizationId!) { channels(input: {organizationId: $o}) {
                 id name displayName service avatar isDisconnected isQueuePaused %s } }"""
        if refresh:
            self._cache.pop(("channels", uid), None)
        return self._cached(("channels", uid), CHANNEL_TTL, lambda: self._gql_or(
            q % "isLocked weeklyPostingLimit { limit scheduled sent }", q % "", {"o": org_id}, uid)["channels"])

    def org_limits(self, uid: str, org_id: str) -> dict | None:
        """The Buffer plan's limits for this organization, or None if Buffer doesn't share them."""
        q = "query { account { organizations { id channelCount limits { scheduledPosts channels } } } }"

        def fetch():
            try:
                orgs = self.gql(q, uid=uid)["account"]["organizations"]
            except NotConnected:
                raise
            except BufferError as e:
                if e.status == 429:
                    raise
                return None
            return next((o for o in orgs if o["id"] == org_id), orgs[0] if orgs else None)
        return self._cached(("org", uid), CHANNEL_TTL, fetch)

    def daily_limits(self, uid: str, channel_ids: list[str], date: str | None = None) -> list[dict]:
        """Buffer's daily posting limit status per channel at the intended time ([] if unavailable)."""
        q = """query($i: DailyPostingLimitsInput!) { dailyPostingLimits(input: $i) {
                 channelId isAtLimit limit scheduled sent } }"""
        i = {"channelIds": channel_ids, **({"date": date} if date else {})}
        try:
            return self.gql(q, {"i": i}, uid=uid)["dailyPostingLimits"]
        except NotConnected:
            raise
        except BufferError as e:
            if e.status == 429:
                raise
            return []

    POST_FIELDS = "id text status dueAt sentAt channelId externalLink assets { type mimeType source thumbnail } error { message }"

    def posts(self, uid: str, org_id: str, statuses: list[str], first: int = 30) -> list[dict]:
        i = {"organizationId": org_id, "filter": {"status": statuses},
             "sort": [{"field": "dueAt", "direction": "asc"}]}

        def fetch():
            q = """query($i: PostsInput!, $n: Int) { posts(first: $n, input: $i) { edges { node { %s } } } }"""
            data = self._gql_or(q % self.POST_FIELDS, q % "id text status dueAt channelId", {"i": i, "n": first}, uid)
            return [e["node"] for e in data["posts"]["edges"]]
        return self._cached(("posts", uid, tuple(statuses), first), 30, fetch)

    # ---- writes
    def create_post(self, uid: str, inp: dict) -> dict:
        q = """mutation($input: CreatePostInput!) { createPost(input: $input) {
                 ... on PostActionSuccess { post { id text status dueAt channelId } }
                 ... on MutationError { message } } }"""
        r = self.gql(q, {"input": inp}, uid=uid)["createPost"]
        self._cache = {k: v for k, v in self._cache.items() if k[0] != "posts"}
        if "post" not in r:
            raise BufferError(r.get("message") or "Buffer rejected the post", 422)
        return r["post"]

    def create_idea(self, uid: str, org_id: str, title: str, text: str) -> dict:
        q = """mutation($input: CreateIdeaInput!) { createIdea(input: $input) {
                 ... on Idea { id content { title text } }
                 ... on MutationError { message } } }"""
        r = self.gql(q, {"input": {"organizationId": org_id, "content": {"title": title, "text": text}}}, uid=uid)["createIdea"]
        if "id" not in r:
            raise BufferError(r.get("message") or "Buffer rejected the idea", 422)
        return r
