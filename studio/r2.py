"""The one S3-compatible (Cloudflare R2) client: settings, SigV4 query signing and the few object calls HuhQueue needs.

Shared by One take's resumable uploads (studio/take_upload.py, PR #107) and the media storage backend (studio/blobstore.py,
docs/STORAGE.md), so there is a single signer and a single set of settings:

``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``, ``R2_BUCKET`` (all four, or the feature is off);
``R2_JURISDICTION`` (``eu`` or ``fedramp``) for a bucket created in a jurisdiction: its S3 host is
``https://<account>.<jurisdiction>.r2.cloudflarestorage.com`` (HuhQueue's bucket is in ``eu``); ``R2_ENDPOINT`` overrides
the host entirely (https only, e.g. another S3-compatible store); ``R2_REGION`` (default ``auto``).
Every signature counts toward the free-tier caps (studio/r2usage.py).

The endpoint is the operator's own bucket, never a user URL, so it isn't routed through netguard; it must be https.
Stdlib only for signing (no boto3 in the 512 MB web instance).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx

URL_TTL = 3600
BROWSER_URL_TTL = 900   # a presigned GET handed to a browser lives at most 15 minutes (studio/app.py r2_redirect)
JURISDICTIONS = ("eu", "fedramp")


def config() -> dict | None:
    """The bucket, or None (feature off) unless all four settings are there."""
    acct, key, secret, bucket = (os.environ.get(k, "").strip() for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))
    if not (acct and key and secret and bucket):
        return None
    endpoint = endpoint_for(acct)
    if not endpoint:
        return None
    return {"endpoint": endpoint, "key": key, "secret": secret, "bucket": bucket, "region": os.environ.get("R2_REGION", "auto")}


def endpoint_for(acct: str) -> str | None:
    """The S3 host: ``R2_ENDPOINT`` when set (https only), else the account's host in ``R2_JURISDICTION`` (eu, fedramp)
    or the default one. An unknown jurisdiction or a non-https endpoint turns the feature off (None), never a guess."""
    explicit = os.environ.get("R2_ENDPOINT", "").strip().rstrip("/")
    if explicit:
        return explicit if explicit.startswith("https://") and urlparse(explicit).hostname else None
    if not re.fullmatch(r"[0-9a-f]{32}", acct or ""):
        return None  # a Cloudflare account id is 32 hex characters: anything else can't be put into a host name
    jur = os.environ.get("R2_JURISDICTION", "").strip().lower()
    if jur and jur not in JURISDICTIONS:
        return None
    return f"https://{acct}.{jur + '.' if jur else ''}r2.cloudflarestorage.com"


def presign(cfg: dict, method: str, key: str, params: dict | None = None, expires: int = URL_TTL, now: datetime | None = None,
            length: int | None = None) -> str:
    """AWS Signature V4 query-string auth (path-style), UNSIGNED-PAYLOAD. Stdlib only. ``length``: the PUT's
    Content-Length is signed too, so the URL can't carry a bigger body than the part it was issued for."""
    from . import r2usage
    r2usage.note(method)  # free-tier caps: a write is a Class A and a read a Class B operation (deletes are free)
    t = now or datetime.now(timezone.utc)
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    host = urlparse(cfg["endpoint"]).netloc
    uri = "/" + quote(cfg["bucket"], safe="") + "/" + quote(key, safe="/~")
    scope = f"{day}/{cfg['region']}/s3/aws4_request"
    hdrs = {**({"content-length": str(int(length))} if length is not None else {}), "host": host}
    signed = ";".join(sorted(hdrs))
    q = {**(params or {}), "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{cfg['key']}/{scope}",
         "X-Amz-Date": amz, "X-Amz-Expires": str(int(expires)), "X-Amz-SignedHeaders": signed}
    qs = "&".join(f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(q.items()))
    canon = "".join(f"{k}:{hdrs[k]}\n" for k in sorted(hdrs))
    creq = "\n".join([method, uri, qs, canon, signed, "UNSIGNED-PAYLOAD"])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = ("AWS4" + cfg["secret"]).encode()
    for part in (day, cfg["region"], "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f"{cfg['endpoint']}{uri}?{qs}&X-Amz-Signature={sig}"


class R2Error(Exception):
    """The bucket didn't answer or refused (never carries a signed URL or a key)."""


class Bucket:
    """Object calls on the configured bucket, each with a 5-minute presigned URL."""

    def __init__(self, cfg: dict, http: httpx.Client | None = None, timeout: float = 60):
        self.cfg, self.timeout = cfg, timeout
        self.http = http or httpx.Client(timeout=timeout, trust_env=False)  # no proxy env: straight to the operator's bucket

    def _call(self, method: str, key: str, content: bytes | None = None, headers: dict | None = None) -> httpx.Response:
        try:
            return self.http.request(method, presign(self.cfg, method, key, None, 300), content=content, headers=headers or {},
                                     timeout=self.timeout)
        except httpx.HTTPError as e:
            raise R2Error(f"storage unreachable ({e.__class__.__name__})") from None

    def put(self, key: str, data: bytes, mime: str = "application/octet-stream") -> None:
        r = self._call("PUT", key, data, {"content-type": mime or "application/octet-stream"})
        if r.status_code >= 300:
            raise R2Error(f"storage refused the upload (HTTP {r.status_code})")

    def get(self, key: str, start: int | None = None, end: int | None = None) -> bytes:
        hdr = {"range": f"bytes={int(start or 0)}-{'' if end is None else int(end)}"} if start is not None or end is not None else {}
        r = self._call("GET", key, None, hdr)
        if r.status_code == 404:
            raise R2Error("storage has no such object (HTTP 404)")
        if r.status_code >= 300:
            raise R2Error(f"storage refused the read (HTTP {r.status_code})")
        return r.content

    def delete(self, key: str) -> bool:
        """True once the object is gone (404 counts: already deleted)."""
        r = self._call("DELETE", key)
        return r.status_code < 300 or r.status_code == 404

    def get_url(self, key: str, ttl: int = 900) -> str:
        """A short-lived presigned GET (the worker's download, a redirect for a big file)."""
        return presign(self.cfg, "GET", key, None, max(1, int(ttl)))

    def browser_url(self, key: str) -> str:
        """The presigned GET a browser is redirected to: never longer than BROWSER_URL_TTL (15 minutes)."""
        return self.get_url(key, BROWSER_URL_TTL)
