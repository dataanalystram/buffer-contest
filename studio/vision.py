"""Image judgments for the agent: Jev (TypeSafe System One, jev-latest) first, SubQ second, Claude vision as the
fallback (budgeted), then pixel rules. The model judges; code decides (docs/AGENT_PIPELINE.md).

- judge_candidate(image, story): is this picture about the story, is it good enough, does it carry burned-in text
  (which must never sit under our headline), is it a face, a product, a screenshot or a logo?
- judge_frame(frame, brand): on a rendered frame, is the logo visible, is any text cut off, is the headline readable?
- pick(candidates): the best hero image by those judgments, or None (render without a photo rather than a wrong one).
The free sample never calls a model: it gets the rules answer.
"""
from __future__ import annotations

import base64
import io
import os
import uuid

import httpx

from . import netguard

PROVIDERS = [("jev", "https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE_API_KEY"),
             ("subq", "https://api.subq.ai/v1/systemone", "subq-snap-1.1", "SUBQ_API_KEY")]


def prepare(data: bytes) -> bytes | None:
    """Upright baseline JPEG, 280..1568 px per side (System One's limits)."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        im.thumbnail((1568, 1568))
        if min(im.size) < 280:
            s = 280 / min(im.size)
            im = im.resize((max(280, int(im.width * s)), max(280, int(im.height * s))))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=88)
        return b.getvalue()
    except Exception:
        return None


def _ask(state: dict, questions: dict, images: dict[str, bytes], http=None) -> tuple[dict, str] | None:
    assets = {}
    for ref, data in images.items():
        prepped = prepare(data)
        if not prepped:
            return None
        assets[ref] = {"type": "image", "format": "jpg", "data_base64": base64.b64encode(prepped).decode()}
    for name, url, model, env in PROVIDERS:
        key = os.environ.get(env)
        if not key:
            continue
        try:
            r = netguard.safe_post(url, http=http, json={"model": model, "state": state, "assets": assets, "questions": questions},
                                   headers={"Authorization": f"Bearer {key}", "TypeSafe-Danger-Zone-Images": "true"}, timeout=40,
                                   allowed_hosts={url.split("/")[2]}, limit=1_000_000)
            if r.status_code >= 400:
                continue
            ans = r.json().get("answers") or {}
            if ans:
                return ans, name
        except (netguard.FetchError, httpx.HTTPError, ValueError):
            continue
    return None


def _noul(a: dict, q: str, default: float = 0.5) -> float:
    try:
        return float((a.get(q) or {}).get("noul", default))
    except (TypeError, ValueError):
        return default


def judge_candidate(image: bytes, story: str, demo: bool = False, http=None) -> dict:
    rules = _pixel_rules(image)
    if demo:
        return {**rules, "provider": "rules"}
    ref = f"img-{uuid.uuid4()}"
    got = _ask({"story": story[:600], "picture": ref}, {
        "on_topic": {"type": "noul", "instructions": "Does `picture` show the subject of `story` (the product, the people, the company or the thing it describes)?"},
        "good": {"type": "noul", "instructions": "Is `picture` sharp, well lit and good enough to be the main image of a social post?"},
        "burned_text": {"type": "noul", "instructions": "Does `picture` have large captions, subtitles or headline text burned into it?"},
        "kind": {"type": "choice", "instructions": "What is `picture` mainly?",
                 "criteria": {"person": "A person or people", "product": "A product, device or app screen", "logo": "A logo or wordmark only",
                              "screenshot": "A screenshot of a website, post or document", "scene": "A place, event or other scene",
                              "graphic": "A chart, illustration or designed graphic"}},
    }, {ref: image}, http)
    if not got:
        return {**rules, "provider": "rules"}
    a, prov = got
    return {"on_topic": _noul(a, "on_topic"), "good": _noul(a, "good"), "burned_text": _noul(a, "burned_text"),
            "kind": (a.get("kind") or {}).get("choice") or "scene", "provider": prov, "width": rules["width"], "height": rules["height"]}


def decide_hero(j: dict) -> str:
    """Code decides from the judgments: 'hero' (top band), 'inset' (small circle, e.g. a face or a logo), or 'skip'."""
    if j.get("width", 0) < 300 or j.get("height", 0) < 200:
        return "skip"
    if j.get("on_topic", 0.5) < 0.35 or j.get("good", 0.5) < 0.3:
        return "skip"
    if j.get("kind") in ("logo",) or j.get("kind") == "person" and j.get("width", 0) < 500:
        return "inset"
    return "hero"  # burned-in text is fine in the hero band: the headline never sits on the photo


def judge_frame(frame: bytes, meta: dict, demo: bool = False, http=None) -> dict:
    """Brand compliance on a rendered frame. Clipping is proven from the drawn text boxes (exact); the model adds what
    only eyes can see (logo visible, headline readable)."""
    from . import hbstyle
    clipped = hbstyle.overflow(meta, (meta.get("width") or 1080, meta.get("height") or 1350))
    out = {"clipped": bool(clipped), "clipped_boxes": clipped, "logo_visible": bool(meta.get("has_logo")), "readable": 0.9, "provider": "rules"}
    if demo or not meta.get("has_logo"):
        return out
    ref = f"frm-{uuid.uuid4()}"
    got = _ask({"frame": ref}, {
        "logo_visible": {"type": "noul", "instructions": "Is a brand logo or wordmark clearly visible in `frame`?"},
        "readable": {"type": "noul", "instructions": "Is the main headline in `frame` easy to read (good contrast, nothing covering it)?"},
        "cut_off": {"type": "noul", "instructions": "Is any text in `frame` cut off at an edge or overlapping other text?"},
    }, {ref: frame}, http)
    if got:
        a, prov = got
        out.update(logo_visible=_noul(a, "logo_visible") >= 0.5, readable=_noul(a, "readable"), model_cut_off=_noul(a, "cut_off"), provider=prov)
    return out


def _pixel_rules(image: bytes) -> dict:
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(image)).convert("L")
        w, h = im.size
        sd = ImageStat.Stat(im.resize((64, 64))).stddev[0]
        return {"on_topic": 0.6, "good": 0.75 if sd > 18 and min(w, h) >= 400 else 0.35, "burned_text": 0.5, "kind": "scene",
                "width": w, "height": h}
    except Exception:
        return {"on_topic": 0.0, "good": 0.0, "burned_text": 0.5, "kind": "scene", "width": 0, "height": 0}
