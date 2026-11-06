"""MCP tool registry: search_tools, describe_tool, call_tool (5 Nov 2026).

HuhQueue exposes ~190 MCP tools. Putting every schema in an agent's context costs tens of thousands of tokens before it
does anything, so an agent can instead load only these three: find tools by keyword, read one tool's schema, then call
it by name. ``tools/list`` still returns the full list by default; ``/mcp?tools=registry`` (or the tools/list param
``_meta: {"huhqueue/tools": "registry"}``) returns only these three.

Security (docs/MCP_TOOLS.md, "Tool registry"):
* ``call_tool`` is unwrapped in ``mcp.handle`` BEFORE any gate, so the named tool goes through exactly the same path as a
  direct ``tools/call``: kill switch, token policy and scope (the tool's own authz), the paid-API / consent gate
  (studio/consent.py), the server-AI gate, run discipline (caps, idempotency), rate limits and the activity record.
* No recursion: ``call_tool`` can't name search_tools, describe_tool or call_tool.
* Unknown tool: 404. Arguments are checked against the tool's input schema first (400, nothing runs).
* Pure: search_tools and describe_tool only read the static tool list; no user data, no outbound call.
"""
from __future__ import annotations

import re
from typing import Any

from .actions import ActionError

REGISTRY = ("search_tools", "describe_tool", "call_tool")
DEFAULT_RESULTS = 8
MAX_RESULTS = 20
MAX_QUERY = 200
MAX_NAME = 100
SUMMARY_CHARS = 160
# the run layer's own fields (studio/run_discipline.CONTROL) and server_ai: accepted by every tool, as on a direct call
PASSTHROUGH = frozenset({"idempotency_key", "user_asked", "new_post", "new_request", "reason", "approval_id", "server_ai"})

TOOLS: list[dict[str, Any]] = [
    {"name": "search_tools", "description": "Find HuhQueue tools by keyword instead of loading all ~190: searches tool names "
     "and descriptions and returns the best matches (name, one-line summary, required arguments). Then describe_tool for "
     "the full schema and call_tool to run it. Read-only.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "Words for what you want to do, e.g. 'schedule post' or 'make a carousel'"},
         "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "description": f"Default {DEFAULT_RESULTS}"}}}},
    {"name": "describe_tool", "description": "One HuhQueue tool's full description and input schema, by name (from "
     "search_tools). Read-only. An unknown name answers 404.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "call_tool", "description": "Run one HuhQueue tool by name with its arguments (checked against its schema "
     "first). It is exactly the same as calling that tool directly: the same permissions, approvals (needs_user_approval), "
     "kill switch, caps and rate limits apply, and the answer is that tool's answer. It can't call search_tools, "
     "describe_tool or itself.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "description": "The tool to run, e.g. preview_post"},
         "arguments": {"type": "object", "description": "That tool's arguments (see describe_tool)"}}}},
]
READ_ONLY = {"search_tools", "describe_tool"}

_WORD = re.compile(r"[a-z0-9]+")


def _summary(desc: str) -> str:
    """The first sentence of a description, at most SUMMARY_CHARS."""
    first = re.split(r"(?<=[.!?])\s", desc.strip(), maxsplit=1)[0]
    return first if len(first) <= SUMMARY_CHARS else first[:SUMMARY_CHARS - 1].rstrip() + "…"


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    """A whole number clamped to lo..hi (default when missing); 400 on any other type."""
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise ActionError("limit must be a whole number.", 400)
    return max(lo, min(hi, v))


def search(tools: list[dict], args: dict) -> dict:
    """search_tools: the best keyword matches over names (weighted) and descriptions, capped at MAX_RESULTS."""
    q = args.get("query")
    if not isinstance(q, str) or not q.strip():
        raise ActionError("Give query: words for what you want to do.", 400)
    if len(q) > MAX_QUERY:
        raise ActionError(f"query is at most {MAX_QUERY} characters.", 400)
    limit = _int(args.get("limit"), DEFAULT_RESULTS, 1, MAX_RESULTS)
    words = set(_WORD.findall(q.lower()))
    if not words:
        raise ActionError("Give query: words for what you want to do.", 400)
    scored = []
    for i, t in enumerate(tools):
        if t["name"] in REGISTRY:
            continue
        name = t["name"].lower()
        name_words = set(name.split("_"))
        desc_words = set(_WORD.findall(t.get("description", "").lower()))
        score = 0
        for w in words:
            if w == name:
                score += 10
            elif w in name_words:
                score += 4
            elif w in name:
                score += 2
            if w in desc_words:
                score += 1
        if score:
            scored.append((-score, i, t))
    scored.sort(key=lambda x: (x[0], x[1]))
    hits = [{"name": t["name"], "summary": _summary(t.get("description", "")),
             "required": list((t.get("inputSchema") or {}).get("required") or [])} for _s, _i, t in scored[:limit]]
    return {"query": q, "results": hits, "count": len(hits), "total_tools": len(tools),
            "next": "describe_tool(name) for a tool's full schema, then call_tool(name, arguments)."}


def describe(by_name: dict[str, dict], read_only: set[str], args: dict) -> dict:
    """describe_tool: one tool's description and input schema; 404 for an unknown name."""
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ActionError("Give name: the tool to describe (from search_tools).", 400)
    t = by_name.get(name.strip())
    if t is None:
        raise ActionError(f"Unknown tool {name.strip()[:MAX_NAME]}", 404)
    return {"name": t["name"], "description": t.get("description", ""), "inputSchema": t.get("inputSchema") or {"type": "object"},
            "read_only": t["name"] in read_only}


# ------------------------------------------------------------------------------------------------ schema check
_TYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,), "object": (dict,),
          "null": (type(None),)}


def _type_ok(v: Any, typ: Any) -> bool:
    """Whether v is one of the JSON Schema types (a bool is never an integer or number)."""
    types = typ if isinstance(typ, list) else [typ]
    for t in types:
        py = _TYPES.get(t)
        if py is None:      # an unknown or missing type in our own schema: don't refuse on it
            return True
        if t in ("integer", "number") and isinstance(v, bool):
            continue
        if isinstance(v, py):
            return True
    return False


def _check(path: str, v: Any, sch: dict) -> str | None:
    """The first type / enum / range / array-item problem with one value, or None."""
    if not isinstance(sch, dict):
        return None
    typ = sch.get("type")
    if typ is not None and not _type_ok(v, typ):
        return f"{path} must be {' or '.join(typ) if isinstance(typ, list) else typ}."
    if "enum" in sch and v not in sch["enum"]:
        return f"{path} must be one of {', '.join(map(str, sch['enum']))}."
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in sch and v < sch["minimum"]:
            return f"{path} must be at least {sch['minimum']}."
        if "maximum" in sch and v > sch["maximum"]:
            return f"{path} must be at most {sch['maximum']}."
    if isinstance(v, list) and isinstance(sch.get("items"), dict):
        for i, item in enumerate(v):
            err = _check(f"{path}[{i}]", item, sch["items"])
            if err:
                return err
    return None


def validate(schema: dict, args: dict) -> str | None:
    """The first problem with args against a tool's input schema, or None. Checks required arguments and the type, enum
    and range of every declared argument (and array items). Undeclared arguments pass, as on a direct call, unless the
    schema says additionalProperties: false (the run layer's own fields always pass)."""
    if not isinstance(args, dict):
        return "arguments must be an object."
    schema = schema if isinstance(schema, dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for r in schema.get("required") or []:
        if r not in args or args[r] is None:
            return f"Missing required argument: {r}."
    for k, v in args.items():
        if v is None and k in props:   # an optional argument sent as null = left out, as a direct call reads it (args.get)
            continue
        if k in props:
            err = _check(k, v, props[k])
            if err:
                return err
        elif schema.get("additionalProperties") is False and k not in PASSTHROUGH and k != "run_id":
            return f"Unknown argument: {k}."
    return None


def unwrap(by_name: dict[str, dict], args: dict) -> tuple[str, dict]:
    """call_tool's arguments -> (tool name, that tool's arguments), or ActionError (400 bad input / recursion, 404 unknown)."""
    if not isinstance(args, dict):
        raise ActionError("arguments must be an object.", 400)
    extra = set(args) - {"name", "arguments"}
    if extra:
        raise ActionError(f"call_tool takes name and arguments only (got {', '.join(sorted(extra))[:200]}).", 400)
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ActionError("Give name: the tool to run.", 400)
    name = name.strip()
    if len(name) > MAX_NAME:
        raise ActionError("Unknown tool.", 404)
    if name in REGISTRY:
        raise ActionError(f"call_tool can't call {name}: call it directly.", 400)
    tool = by_name.get(name)
    if tool is None:
        raise ActionError(f"Unknown tool {name}", 404)
    inner = args.get("arguments")
    if inner is None:
        inner = {}
    if not isinstance(inner, dict):
        raise ActionError("arguments must be an object.", 400)
    err = validate(tool.get("inputSchema") or {}, inner)
    if err:
        raise ActionError(f"{name}: {err}", 400)
    return name, inner
