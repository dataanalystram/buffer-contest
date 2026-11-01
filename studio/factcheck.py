"""Cross-source number check. Money figures in the story are compared with what other outlets say about the same story;
when two sources disagree ("$1.3M" on the founder's post, "$13 million" on msn.com) the agent flags it, holds the post
and asks the person. It never picks one itself.
"""
from __future__ import annotations

import re

MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}
NUM_RE = re.compile(r"(?P<cur>[$€£₹])\s?(?P<n>\d[\d,]*(?:\.\d+)?)\s?(?P<mul>k|thousand|mn|m|million|bn|b|billion|t|trillion)?\b"
                    r"|(?P<n2>\d[\d,]*(?:\.\d+)?)\s?(?P<mul2>million|billion|thousand|mn|bn)\s?(?P<cur2>dollars|usd|euros|eur|pounds|rupees)?",
                    re.I)


def amounts(text: str) -> list[dict]:
    out = []
    for m in NUM_RE.finditer(text or ""):
        n = (m.group("n") or m.group("n2") or "").replace(",", "")
        try:
            v = float(n)
        except ValueError:
            continue
        mul = (m.group("mul") or m.group("mul2") or "").lower()
        v *= MULT.get(mul, 1)
        cur = m.group("cur") or ("$" if (m.group("cur2") or "").lower() in ("dollars", "usd") else "")
        if not cur and not mul:
            continue  # bare numbers ("3 facts", "2026") are not claims we can compare
        out.append({"kind": "money", "value": v, "text": m.group(0).strip()})
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.051 * max(abs(a), abs(b), 1e-9)


def conflicts(primary: dict, others: list[dict]) -> list[dict]:
    """primary/others: {source, text, url}. One conflict per primary figure that another source states differently and
    never states the same way."""
    mine = amounts(primary.get("text", ""))
    out, seen = [], set()
    for a in mine:
        if a["text"] in seen:
            continue
        for o in others:
            theirs = amounts(o.get("text", ""))
            if not theirs or any(_close(a["value"], b["value"]) for b in theirs):
                continue
            b = min(theirs, key=lambda b: abs(b["value"] - a["value"]))
            if not (0.05 <= b["value"] / max(a["value"], 1e-9) <= 20):
                continue  # a different amount altogether (a valuation next to a round), not the same claim
            seen.add(a["text"])
            out.append({"ours": a["text"], "ours_source": primary.get("source", ""), "theirs": b["text"],
                        "theirs_source": o.get("source", ""), "theirs_url": o.get("url", ""),
                        "question": f"{primary.get('source') or 'The source'} says {a['text']}, {o.get('source') or 'another outlet'} "
                                    f"says {b['text']}. Which is right? Reply with the figure, e.g. “it's {a['text']}”."})
            break
    return out
