"""Gemini on Oracle Cloud (OCI Generative AI), called with OCI API-key request signing and no SDK.

Credentials come only from the environment (never the database, never logs):
  OCI_USER, OCI_TENANCY, OCI_FINGERPRINT, OCI_REGION (default eu-paris-1), OCI_PRIVATE_KEY (PEM text),
  OCI_COMPARTMENT (optional; defaults to the tenancy OCID).

The native chat API (POST /20231130/actions/chat, apiFormat GENERIC) serves google.gemini-2.5-* on demand. Requests are
signed per Oracle's HTTP signature scheme (rsa-sha256 over date, (request-target), host, and for a body
x-content-sha256, content-type and content-length). The call goes through netguard (pinned IP, no redirects) to the
regional inference host only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from email.utils import formatdate
from urllib.parse import urlparse

import httpx

API_VERSION = "20231130"
MODELS = {
    "gemini-2.5-flash": "google.gemini-2.5-flash",
    "gemini-2.5-pro": "google.gemini-2.5-pro",
    "gemini-2.5-flash-lite": "google.gemini-2.5-flash-lite",
}
# Estimated USD per million tokens (input, output), Google's public Gemini 2.5 list prices as an upper bound for the
# ledger; Oracle's on-demand price is at or below this. Used only to meter the trial credit, never to bill.
PRICES = {
    "google.gemini-2.5-pro": (1.25, 10.00),
    "google.gemini-2.5-flash": (0.30, 2.50),
    "google.gemini-2.5-flash-lite": (0.10, 0.40),
}


class OCIError(Exception):
    pass


def configured() -> bool:
    e = os.environ.get
    return all((e(k) or "").strip() for k in ("OCI_USER", "OCI_TENANCY", "OCI_FINGERPRINT", "OCI_PRIVATE_KEY"))


def region() -> str:
    r = (os.environ.get("OCI_REGION") or "eu-paris-1").strip()
    if not r.replace("-", "").isalnum():
        raise OCIError("OCI_REGION is not a region name.")
    return r


def host() -> str:
    return f"inference.generativeai.{region()}.oci.oraclecloud.com"


def model_id(name: str) -> str:
    if name.startswith(("google.gemini-", "ocid1.generativeairoutingprofile.")):
        return name
    return MODELS.get(name, MODELS["gemini-2.5-flash"])


def price(model: str, tin: int = 0, tout: int = 0) -> float:
    p = PRICES.get(model_id(model), PRICES["google.gemini-2.5-pro"])
    return round((tin * p[0] + tout * p[1]) / 1_000_000, 6)


def _key():
    from cryptography.hazmat.primitives import serialization
    pem = (os.environ.get("OCI_PRIVATE_KEY") or "").replace("\\n", "\n").strip().encode()
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError):
        raise OCIError("OCI_PRIVATE_KEY isn't a readable PEM private key.") from None


def sign_headers(method: str, url: str, body: bytes | None, *, date: str | None = None) -> dict:
    """The headers for one signed OCI request (Authorization carries only the key id and the signature)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    p = urlparse(url)
    target = p.path + (f"?{p.query}" if p.query else "")
    h = {"date": date or formatdate(usegmt=True), "host": p.hostname or ""}
    names = ["date", "(request-target)", "host"]
    if body is not None:
        h["x-content-sha256"] = base64.b64encode(hashlib.sha256(body).digest()).decode()
        h["content-type"] = "application/json"
        h["content-length"] = str(len(body))
        names += ["x-content-sha256", "content-type", "content-length"]
    lines = [f"(request-target): {method.lower()} {target}" if n == "(request-target)" else f"{n}: {h[n]}" for n in names]
    sig = base64.b64encode(_key().sign("\n".join(lines).encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    e = os.environ.get
    key_id = f"{e('OCI_TENANCY', '').strip()}/{e('OCI_USER', '').strip()}/{e('OCI_FINGERPRINT', '').strip()}"
    h["authorization"] = (f'Signature version="1",keyId="{key_id}",algorithm="rsa-sha256",'
                          f'headers="{" ".join(names)}",signature="{sig}"')
    return h


def _content(c) -> list:
    if isinstance(c, str):
        return [{"type": "TEXT", "text": c}] if c else []
    out = []
    for b in c or []:
        if isinstance(b, dict) and b.get("type") in ("text", "TEXT"):
            out.append({"type": "TEXT", "text": b.get("text") or ""})
        elif isinstance(b, str):
            out.append({"type": "TEXT", "text": b})
    return out


def to_generic(system: str, messages: list, tools: list | None, max_tokens: int, model: str) -> dict:
    """OpenAI-style history (as brain_llm keeps it for non-Anthropic engines) -> OCI GENERIC chat request."""
    msgs = [{"role": "SYSTEM", "content": [{"type": "TEXT", "text": system}]}] if system else []
    for m in messages:
        role = (m.get("role") or "user").lower()
        if role == "tool":
            msgs.append({"role": "TOOL", "toolCallId": m.get("tool_call_id") or "", "content": _content(m.get("content"))})
        elif role == "assistant":
            am = {"role": "ASSISTANT", "content": _content(m.get("content"))}
            if m.get("tool_calls"):
                am["toolCalls"] = [{"id": tc.get("id"), "type": "FUNCTION", "name": tc["function"]["name"],
                                    "arguments": tc["function"].get("arguments") or "{}"} for tc in m["tool_calls"]]
            msgs.append(am)
        elif role == "system":
            msgs.append({"role": "SYSTEM", "content": _content(m.get("content"))})
        else:
            msgs.append({"role": "USER", "content": _content(m.get("content")) or [{"type": "TEXT", "text": " "}]})
    req = {"apiFormat": "GENERIC", "messages": msgs, "maxTokens": int(max_tokens), "temperature": 0.4, "isStream": False}
    if tools:
        req["tools"] = [{"type": "FUNCTION", "name": t["name"], "description": t.get("description") or "",
                         "parameters": t.get("input_schema") or {"type": "object", "properties": {}}} for t in tools]
    return {"compartmentId": (os.environ.get("OCI_COMPARTMENT") or os.environ.get("OCI_TENANCY") or "").strip(),
            "servingMode": {"servingType": "ON_DEMAND", "modelId": model_id(model)}, "chatRequest": req}


def from_generic(j: dict) -> dict:
    """OCI chat response -> {text, tool_calls (OpenAI shape), finish, in, out}."""
    cr = j.get("chatResponse") or {}
    ch = (cr.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    text = "".join(b.get("text") or "" for b in msg.get("content") or [] if (b.get("type") or "").upper() == "TEXT")
    tcs = []
    for tc in msg.get("toolCalls") or []:
        args = tc.get("arguments")
        tcs.append({"id": tc.get("id") or tc.get("name"), "type": "function",
                    "function": {"name": tc.get("name"), "arguments": args if isinstance(args, str) else json.dumps(args or {})}})
    u = cr.get("usage") or {}
    fin = (ch.get("finishReason") or "").lower()
    return {"text": text, "tool_calls": tcs, "finish": "length" if fin in ("max_tokens", "length") else fin,
            "in": int(u.get("promptTokens") or 0), "out": int(u.get("completionTokens") or 0)}


def chat(system: str, messages: list, *, model: str = "gemini-2.5-flash", tools: list | None = None, max_tokens: int = 1500,
         http: httpx.Client | None = None, timeout: float = 90) -> dict:
    """One signed chat call. Raises OCIError with a short, secret-free reason."""
    from . import netguard
    if not configured():
        raise OCIError("Gemini on Oracle Cloud isn't set up on this server.")
    url = f"https://{host()}/{API_VERSION}/actions/chat"
    body = json.dumps(to_generic(system, messages, tools, max_tokens, model), separators=(",", ":")).encode()
    headers = {k: v for k, v in sign_headers("POST", url, body).items() if k not in ("host", "content-length")}  # httpx sets both
    try:
        r = netguard.safe_post(url, http=http, content=body, headers=headers, timeout=timeout, allowed_hosts={host()})
    except netguard.FetchError as e:
        raise OCIError(f"The Oracle endpoint isn't allowed: {e}") from None
    except httpx.HTTPError as e:
        raise OCIError(f"Couldn't reach Oracle Cloud ({e.__class__.__name__}).") from None
    if r.status_code >= 400:
        try:
            msg = (r.json().get("message") or "")[:400]
        except ValueError:
            msg = ""
        raise OCIError(f"Oracle Cloud answered HTTP {r.status_code}{': ' + msg if msg else ''}")
    try:
        return from_generic(r.json())
    except (ValueError, AttributeError, KeyError, IndexError):
        raise OCIError("Oracle Cloud returned an answer that couldn't be read.") from None
