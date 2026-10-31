"""How the video worker proves it is the worker. SECURITY_RULES 2.

The app never puts a job token in the workflow dispatch (dispatch inputs are shown on the run page and the old
``echo ::add-mask::`` step printed the token in the log before masking it). Instead the workflow run asks GitHub for an
OpenID Connect ID token (``permissions: id-token: write``) with the app's URL as audience and trades it once at
``POST /api/worker/jobs/{id}/claim`` for that job's token. The app checks GitHub's signature (RS256, GitHub's published
keys) and that the run is a ``workflow_dispatch`` of this repository's ``render.yml`` or ``take.yml`` on the deploy branch.

A job can be claimed once, inside its claim window. The token it gets back lives only in the worker's memory, reads the
job spec once, posts each of the job's own clips once, and dies with the final result.
"""
from __future__ import annotations

import base64
import json
import os
import time

import httpx

GITHUB_ISS = "https://token.actions.githubusercontent.com"
JWKS_URL = GITHUB_ISS + "/.well-known/jwks"
_jwks: dict = {"at": 0.0, "keys": {}}


class OIDCError(ValueError):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _keys(http: httpx.Client | None = None, refresh: bool = False) -> dict:
    if refresh or not _jwks["keys"] or time.time() - _jwks["at"] > 3600:
        c = http or httpx.Client(timeout=10, follow_redirects=False)
        r = c.get(JWKS_URL)
        r.raise_for_status()
        _jwks["keys"] = {k["kid"]: k for k in r.json().get("keys", []) if k.get("kty") == "RSA"}
        _jwks["at"] = time.time()
    return _jwks["keys"]


def verify(jwt: str, *, audience: str, repo: str, workflows: tuple[str, ...], ref: str,
           jwks: dict | None = None, http: httpx.Client | None = None, now: float | None = None) -> dict:
    """The verified claims of a GitHub Actions OIDC token, or OIDCError. ``jwks`` (kid -> JWK) is for tests."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        h64, p64, s64 = (jwt or "").split(".")
        head, claims, sig = json.loads(_b64d(h64)), json.loads(_b64d(p64)), _b64d(s64)
    except (ValueError, TypeError):
        raise OIDCError("malformed token")
    if head.get("alg") != "RS256":
        raise OIDCError("unexpected algorithm")
    keys = jwks if jwks is not None else _keys(http)
    k = keys.get(head.get("kid"))
    if k is None and jwks is None:
        k = _keys(http, refresh=True).get(head.get("kid"))
    if not k:
        raise OIDCError("unknown signing key")
    pub = rsa.RSAPublicNumbers(int.from_bytes(_b64d(k["e"]), "big"), int.from_bytes(_b64d(k["n"]), "big")).public_key()
    try:
        pub.verify(sig, f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise OIDCError("bad signature")
    t = time.time() if now is None else now
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    want = {f"{repo}/.github/workflows/{w}@{ref}" for w in workflows}
    if claims.get("iss") != GITHUB_ISS:
        raise OIDCError("wrong issuer")
    if audience.rstrip("/") not in [str(x or "").rstrip("/") for x in auds]:
        raise OIDCError("wrong audience")
    if float(claims.get("exp", 0)) < t - 30 or float(claims.get("nbf", claims.get("iat", 0))) > t + 60:
        raise OIDCError("expired or not yet valid")
    if claims.get("repository") != repo or claims.get("event_name") != "workflow_dispatch":
        raise OIDCError("not this repository's dispatched run")
    if claims.get("workflow_ref") not in want:
        raise OIDCError("not the worker workflow")
    return claims


def settings() -> dict:
    repo = os.environ.get("GH_REPO", "dataanalystram/buffer-contest")
    ref = os.environ.get("GH_REF", "main")
    return {"repo": repo, "ref": ref if ref.startswith("refs/") else f"refs/heads/{ref}"}
