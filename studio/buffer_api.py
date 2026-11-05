"""Buffer OAuth 2.0 (Authorization Code + PKCE, private client) and GraphQL client.

* Tokens live only in the server-side store; the browser holds an opaque session id.
* Refresh tokens rotate: every refresh stores the replacement before the call continues, under a
  per-user lock so two requests never spend the same refresh token.
* Rate limits are per app client (shared by every user; developers.buffer.com/guides/api-limits.md: 15 min 100 +10/user,
  24 h 500 +25/user, 30 days 15,000 +100/user). We read the RateLimit headers on every response, cache the org (24 h),
  channel lists and posts, wait out a short Retry-After once and surface longer ones. A 429 blocks only the account that
  got it (it costs nothing and carries Retry-After), and sign-in never waits on anyone's block (login=True).
* Throttle: with ~10% of any window left (LOW_SHARE), cached reads are served even when stale, so the budget that is left
  goes to sign-ins and the posts people make.
"""
from __future__ import annotations

import base64
import hashlib
import os
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
CHANNEL_TTL = int(os.environ.get("HQ_CHANNEL_TTL", "900"))  # seconds; channel lists change rarely (our own writes and Refresh clear it)
ACCOUNT_TTL = 86400  # the account, its organizations and plan limits: once a day per user
POSTS_TTL = int(os.environ.get("HQ_POSTS_TTL", "300"))  # one read per account per 5 minutes for all the statuses a page needs
LOW_SHARE = 0.10  # throttle (serve stale caches) once any window has 10% or less left
POSTS_PAGE = 100


_COUNT = threading.local()


class counting:
    """Count the real Buffer GraphQL requests this thread makes inside the block (the per-action cost the UI shows).
    ``with buffer_api.counting() as c: ...; c.n``. The sample's in-memory Buffer is not counted: it makes no requests."""

    def __enter__(self):
        self.prev, self.n = getattr(_COUNT, "c", None), 0
        _COUNT.c = self
        return self

    def __exit__(self, *exc):
        _COUNT.c = self.prev
        if self.prev is not None:
            self.prev.n += self.n
        return False


def _counted() -> None:
    c = getattr(_COUNT, "c", None)
    if c is not None:
        c.n += 1


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
        self.blocked: dict[str, float] = {}  # per account ("uid" or "token"): a 429 never locks out anyone else

    @property
    def blocked_until(self) -> float:
        return max(self.blocked.values(), default=0.0)

    def block(self, key: str, seconds: float) -> None:
        now = time.time()
        self.blocked = {k: v for k, v in self.blocked.items() if v > now}
        self.blocked[key] = now + seconds

    def blocked_for(self, key: str) -> int:
        return max(0, int(self.blocked.get(key, 0) - time.time()))

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
        """True when any window (15 min, 24 h, 30 days) has LOW_SHARE or less of its quota left."""
        for w in self.windows.values():
            rem, q = w.get("remaining"), w.get("quota")
            if rem is None:
                continue
            if rem <= max(4, int((q or 50) * LOW_SHARE)):
                return True
        return False


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
            token: str | None = None, _retried: bool = False, login: bool = False) -> dict:
        key = uid or "token"
        if not login and self.budget.blocked_for(key):
            wait = self.budget.blocked_for(key) + 1
            raise BufferError(f"Buffer API rate limit reached; try again in {wait}s", 429, wait)
        bearer = token or self.access_token(uid)
        _counted()
        r = self.http.post(API_URL, json={"query": query, "variables": variables or {}},
                           headers={"Authorization": f"Bearer {bearer}"})
        self.budget.read(r.headers)
        if uid:  # every Buffer API call is counted in the account's activity (System tab), never with the token
            m = re.search(r"\b(query|mutation)\b[^{]*\{\s*(\w+)", query)
            self.store.audit(uid, "system", "buffer_api", "system",
                             {"op": f"{m.group(1)} {m.group(2)}" if m else "graphql", "status": r.status_code}, ok=r.status_code < 400)
        if r.status_code == 401:
            if uid and not _retried:
                tok = self.store.tokens(uid) or {}
                tok["expires_at"] = 0  # force one refresh, then give up
                self.store.save_tokens(uid, tok if tok.get("refresh_token") else None)
                return self.gql(query, variables, uid=uid, _retried=True)
            raise NotConnected("Buffer rejected the access token. Connect again.")
        if r.status_code == 429:
            try:
                ra = int(float(r.headers.get("retry-after") or 0))
            except ValueError:
                ra = 0
            if login:  # the sign-in decides what to do (finish later); it never blocks anyone
                raise BufferError(f"Buffer API rate limit reached; try again in {ra or 60}s", 429, ra or 60)
            if not _retried and 0 < ra <= MAX_BACKOFF:
                time.sleep(ra)
                return self.gql(query, variables, uid=uid, token=token, _retried=True)
            self.budget.block(key, ra or 30)
            raise BufferError(f"Buffer API rate limit reached; try again in {ra or 30}s", 429, ra or 30)
        try:
            j = r.json()
        except ValueError:
            raise BufferError(f"Buffer returned HTTP {r.status_code}", r.status_code if r.status_code >= 500 else None)
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

    def account(self, *, uid: str | None = None, token: str | None = None, login: bool = False) -> dict:
        """The Buffer account and its organizations. Sign-in passes login=True: it skips every block, and the answer is
        cached for the account for a day (ACCOUNT_TTL)."""
        q = "query { account { id email name organizations { id name } } }"
        if uid and not login:
            return self._cached(("account", uid), ACCOUNT_TTL, lambda: self.gql(q, uid=uid)["account"])
        acct = self.gql(q, uid=uid, token=token, login=login)["account"]
        if acct.get("id"):
            self._cache[("account", acct["id"])] = (time.time(), acct)
        return acct

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
        return self._cached(("org", uid), ACCOUNT_TTL, fetch)

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
        """Posts by status, from ONE cached read per status per account (POSTS_TTL), whatever page asks and however many
        it wants: browsing Inbox -> Planner -> Make a post costs no Buffer requests inside the window (Buffer's limits
        are per app client and shared by every HuhQueue user). Our own writes clear this account's cache at once;
        refresh() forces a re-read (the Refresh buttons)."""
        out: list[dict] = []
        self.prefetch_posts(uid, org_id, statuses)
        for st in statuses:
            out += self._cached(("posts", uid, st), POSTS_TTL, lambda st=st: self._read_posts(uid, org_id, [st]))
        if len(statuses) > 1:
            out.sort(key=lambda p: p.get("dueAt") or "")
        return out[:first]

    def _read_posts(self, uid: str, org_id: str, sts: list[str]) -> list[dict]:
        q = """query($i: PostsInput!, $n: Int) { posts(first: $n, input: $i) { edges { node { %s } } } }"""
        i = {"organizationId": org_id, "filter": {"status": sts}, "sort": [{"field": "dueAt", "direction": "asc"}]}
        data = self._gql_or(q % self.POST_FIELDS, q % "id text status dueAt channelId", {"i": i, "n": POSTS_PAGE}, uid)
        return [e["node"] for e in data["posts"]["edges"]]

    def prefetch_posts(self, uid: str, org_id: str, statuses: list[str] | tuple[str, ...]) -> None:
        """Every status a page is about to read that isn't cached, in ONE Buffer request (pages used to spend one per
        status: the Planner 4, the Inbox 5). Only a page that came back incomplete (fewer than POSTS_PAGE posts) and whose
        posts all carry a status we asked for is split into the per-status caches; otherwise each status is read alone."""
        now = time.time()
        stale = [st for st in dict.fromkeys(statuses) if not ((h := self._cache.get(("posts", uid, st)))
                                                               and (now - h[0] < POSTS_TTL or self.budget.low()))]
        if len(stale) < 2:
            return
        try:
            nodes = self._read_posts(uid, org_id, stale)
        except NotConnected:
            raise
        except BufferError as e:
            if e.status == 429:
                raise
            return
        want = {st.lower() for st in stale}
        if len(nodes) >= POSTS_PAGE or any((n.get("status") or "").lower() not in want for n in nodes):
            return
        for st in stale:
            self._cache[("posts", uid, st)] = (now, [n for n in nodes if (n.get("status") or "").lower() == st.lower()])

    def refresh(self, uid: str) -> None:
        self.invalidate(uid)

    def _drop_posts(self, uid: str | None = None) -> None:
        self._cache = {k: v for k, v in self._cache.items() if not (k[0] == "posts" and (uid is None or k[1] == uid))}

    # ---- Instagram music for Reels (experimental Buffer API; needs the IG channel connected through a Facebook Page)
    AUDIO_FIELDS = "id title displayArtist creatorUsername duration type previewUrl coverArtworkUrl"

    def search_instagram_audio(self, uid: str, channel_id: str, query: str = "") -> dict:
        """Licensed music + original sounds for one Instagram channel: searchInstagramAudioCatalog (or the trending catalog
        when query is blank). A channel connected with Instagram Login gets {"needs_facebook": True} (Buffer's
        ChannelRefreshRequired); a channel that isn't Instagram gets Buffer's BAD_USER_INPUT as a BufferError."""
        sel = ("... on InstagramAudioCatalogSuccess { failedAudioTypes audio { %s } } "
               "... on ChannelRefreshRequired { message channelIds }") % self.AUDIO_FIELDS
        if (query or "").strip():
            q = "query($c: ChannelId!, $q: String!) { r: searchInstagramAudioCatalog(input: {channelId: $c, query: $q}) { __typename %s } }" % sel
            v = {"c": channel_id, "q": query.strip()[:100]}
        else:
            q = "query($c: ChannelId!) { r: instagramAudioCatalog(input: {channelId: $c}) { __typename %s } }" % sel
            v = {"c": channel_id}
        r = self.gql(q, v, uid=uid)["r"] or {}
        if r.get("__typename") == "ChannelRefreshRequired":
            return {"audio": [], "needs_facebook": True, "message": r.get("message") or ""}
        return {"audio": r.get("audio") or [], "needs_facebook": False, "failed_types": r.get("failedAudioTypes") or []}

    def post_audio(self, uid: str, post_id: str) -> dict | None:
        """The music Buffer stored on a post (InstagramSelectedAudio), or None. Buffer answers FORBIDDEN on this field for
        personal API keys (checked 2 Nov 2026), so None also means "Buffer won't say"."""
        q = """query($i: PostInput!) { post(input: $i) { id status metadata { ... on InstagramPostMetadata { type
                 audio { id title duration type displayArtist creatorUsername audioVolume videoVolume } } } } }"""
        try:
            p = self.gql(q, {"i": {"id": post_id}}, uid=uid)["post"] or {}
        except BufferError as e:
            if "FORBIDDEN" in str(e) or "Not authorized" in str(e):
                return None
            raise
        return (p.get("metadata") or {}).get("audio")

    # ---- writes
    def _publishing_open(self) -> None:
        from . import opsctl
        if opsctl.paused("publishing", self.store):  # the operator's kill switch (studio/opsctl.py): nothing reaches Buffer
            raise BufferError(opsctl.message("publishing"), 503)

    def create_post(self, uid: str, inp: dict) -> dict:
        self._publishing_open()
        q = """mutation($input: CreatePostInput!) { createPost(input: $input) {
                 ... on PostActionSuccess { post { id text status dueAt channelId } }
                 ... on MutationError { message } } }"""
        # Buffer answers HTTP 200 even when it refuses: the reason is in createPost.message (a typed MutationError)
        try:
            r = self.gql(q, {"input": inp}, uid=uid).get("createPost") or {}
        except (httpx.TransportError, BufferError) as e:
            # a timeout / 5xx after Buffer may already have made the post: look before anyone retries, so a retry never
            # leaves a second draft (createPost has no idempotency key)
            if isinstance(e, BufferError) and (e.status or 0) < 500:
                raise
            made = self._just_created(uid, inp)
            if not made:
                raise
            self._drop_posts(uid)
            return made
        self._drop_posts(uid)
        if not r.get("post"):
            raise BufferError(r.get("message") or "Buffer rejected the post without saying why", 422)
        return r["post"]

    def _just_created(self, uid: str, inp: dict, window_s: int = 900) -> dict | None:
        """The post createPost may have made before its answer was lost: same channel, same text, created in the last
        ``window_s`` seconds. One read; None when there is none (or the read fails too)."""
        u = self.store.user(uid) or {}
        if not u.get("org_id"):
            return None
        q = """query($i: PostsInput!) { posts(first: 20, input: $i) { edges { node { id text status dueAt channelId createdAt } } } }"""
        i = {"organizationId": u["org_id"], "filter": {"channelIds": [inp["channelId"]],
                                                     "status": ["draft", "scheduled", "sending", "sent", "needs_approval"]},
             "sort": [{"field": "createdAt", "direction": "desc"}]}
        try:
            edges = self.gql(q, {"i": i}, uid=uid)["posts"]["edges"]
        except Exception:  # noqa: BLE001  (the original failure is what the caller reports)
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for e in edges:
            n = e.get("node") or {}
            try:
                age = (now - datetime.fromisoformat(str(n.get("createdAt") or "").replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                continue
            if n.get("channelId") == inp["channelId"] and (n.get("text") or "") == (inp.get("text") or "") and age <= window_s:
                return {k: n.get(k) for k in ("id", "text", "status", "dueAt", "channelId")}
        return None

    def edit_post(self, uid: str, inp: dict) -> dict:
        """editPost: only the fields given change (Buffer keeps text, assets and slot when they are omitted)."""
        self._publishing_open()
        q = """mutation($input: EditPostInput!) { editPost(input: $input) {
                 ... on PostActionSuccess { post { id text status dueAt channelId } }
                 ... on MutationError { message } } }"""
        r = self.gql(q, {"input": inp}, uid=uid).get("editPost") or {}
        self._drop_posts(uid)
        if not r.get("post"):
            raise BufferError(r.get("message") or "Buffer rejected the change without saying why", 422)
        return r["post"]

    def delete_post(self, uid: str, post_id: str) -> dict:
        q = """mutation($input: DeletePostInput!) { deletePost(input: $input) {
                 ... on DeletePostSuccess { id }
                 ... on MutationError { message } } }"""
        r = self.gql(q, {"input": {"id": post_id}}, uid=uid)["deletePost"]
        self._drop_posts(uid)
        if "id" not in r:
            raise BufferError(r.get("message") or "Buffer could not delete the post", 422)
        return {"id": r["id"]}

    def posting_schedules(self, uid: str, org_id: str) -> list[dict]:
        """Each channel's weekly posting slots (Buffer's queue times) in the channel's timezone; [] if Buffer won't say."""
        q = """query($o: OrganizationId!) { channels(input: {organizationId: $o}) {
                 id timezone postingSchedule { day times paused } } }"""

        def fetch():
            try:
                return self.gql(q, {"o": org_id}, uid=uid)["channels"]
            except NotConnected:
                raise
            except BufferError as e:
                if e.status == 429:
                    raise
                return []
        return self._cached(("slots", uid), CHANNEL_TTL, fetch)

    def create_idea(self, uid: str, org_id: str, title: str, text: str) -> dict:
        q = """mutation($input: CreateIdeaInput!) { createIdea(input: $input) {
                 ... on Idea { id content { title text } }
                 ... on MutationError { message } } }"""
        r = self.gql(q, {"input": {"organizationId": org_id, "content": {"title": title, "text": text}}}, uid=uid)["createIdea"]
        if "id" not in r:
            raise BufferError(r.get("message") or "Buffer rejected the idea", 422)
        return r
