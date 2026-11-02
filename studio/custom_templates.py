"""Custom post templates per workspace (owner, 2 Nov 2026: "the agent should build NEW templates for different things").

A custom template is a small, validated layout spec, never code: a name, a base layout (one of design_system.LAYOUTS,
or "kit" = the active kit's own), the fields it takes (with sample values for the preview) and up to 8 slides built from
the renderer's own slide types. It renders through studio/templates.py in the ACTIVE brand kit (or the kit_id a render
names), so a custom template follows every kit switch like the built-in ones. The brand's end card is always added last
(brand rule); a "single" template is one card.

UI = REST = MCP = agent tool:
  GET    /api/templates                      list_templates   (built-in + custom; custom ids are "custom:<slug>")
  GET    /api/templates/custom               (the custom ones, the slide types and layouts)
  POST   /api/templates/custom               create_template
  PATCH  /api/templates/custom/{id}          update_template
  DELETE /api/templates/custom/{id}          delete_template
  POST   /api/templates/render               render_template  (custom ids too; kit_id renders in another kit, no switch)
Every create/update/delete is recorded in the Style Ledger (style_rules kind "template") and in Activity.

Security: everything an agent sends is text. Strings are stripped of control characters and angle brackets and capped;
placeholders are only {lower_snake} names (declared in fields, or declared for it); unknown slide types and keys are
dropped; a workspace has at most MAX_CUSTOM templates (creates run under the workspace lock, UNIQUE(user_id, slug)).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import Body, Request

from . import templates

SCHEMA = ("CREATE TABLE IF NOT EXISTS custom_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, slug TEXT, name TEXT, "
          "data TEXT, created REAL, updated REAL, UNIQUE(user_id, slug))")
PREFIX = "custom:"
MAX_CUSTOM = 20
MAX_SLIDES = 8
MAX_FIELDS = 12
LAYOUT_CHOICES = ("kit",) + tuple(templates.LAYOUTS)
# slide type -> its text slots and each slot's cap. "items" is a list (bullets), everything else one line or paragraph.
SLIDE_TYPES: dict[str, dict[str, int]] = {
    "front": {"kicker": 24, "title": 110, "sub": 90},               # the cover card (kicker label, headline, dek)
    "bullets": {"title": 70, "items": 120},                         # a title and up to 5 points
    "story": {"title": 110, "body": 260, "tag": 24, "source": 60},  # a numbered fact slide
    "stat": {"title": 60, "body": 200, "tag": 24},                  # one big figure (title) and what it means (body)
    "quote": {"quote": 220, "speaker": 50, "role": 60},             # a quote card
    "breaking": {"title": 110, "source": 50, "when": 24},           # a single breaking-news style card
}
PH_RE = re.compile(r"\{([a-z][a-z0-9_]{0,23})\}")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f<>]")


class TemplateError(ValueError):
    pass


def ensure(store) -> None:
    sql = SCHEMA
    if store.pg:
        sql = sql.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    store.x(sql)
    if store.pg:
        store.x("ALTER TABLE custom_templates ENABLE ROW LEVEL SECURITY")


def _txt(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", _CTRL.sub("", str(v if v is not None else ""))).strip()[:n]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40].strip("-")
    return s or "template"


def clean_spec(body: dict, base: dict | None = None) -> dict:
    """A create/update body -> the stored spec, validated. Raises TemplateError with what to fix."""
    if not isinstance(body, dict):
        raise TemplateError("Send the template as an object.")
    k: dict[str, Any] = json.loads(json.dumps(base or {}))
    if "name" in body or not k.get("name"):
        k["name"] = _txt(body.get("name"), 60)
    if not k["name"]:
        raise TemplateError("Give the template a name, e.g. 'Funding announcement'.")
    if "about" in body or "description" in body:
        k["about"] = _txt(body.get("about", body.get("description")), 200)
    if "layout" in body or "layout" not in k:
        lay = str(body.get("layout") or "kit")
        if lay not in LAYOUT_CHOICES:
            raise TemplateError(f"layout is one of {', '.join(LAYOUT_CHOICES)} ('kit' = the active kit's own layout).")
        k["layout"] = lay
    if "kind" in body or "kind" not in k:
        k["kind"] = "single" if body.get("kind") == "single" else "carousel"
    if "fields" in body or "fields" not in k:
        fields: list[dict] = []
        for f in (body.get("fields") or [])[:MAX_FIELDS]:
            if isinstance(f, str):
                f = {"key": f}
            if not isinstance(f, dict):
                continue
            key = re.sub(r"[^a-z0-9_]", "_", str(f.get("key") or "").strip().lower())[:24]
            if not key or not key[0].isalpha() or any(x["key"] == key for x in fields):
                continue
            sample = f.get("sample")
            sample = [_txt(x, 120) for x in sample[:5]] if isinstance(sample, list) else _txt(sample, 200)
            fields.append({"key": key, "label": _txt(f.get("label") or key.replace("_", " ").capitalize(), 40), "sample": sample})
        k["fields"] = fields
    if "slides" in body or "slides" not in k:
        slides = []
        for s in (body.get("slides") or [])[:MAX_SLIDES]:
            if not isinstance(s, dict) or s.get("type") not in SLIDE_TYPES:
                continue
            out: dict[str, Any] = {"type": s["type"]}
            for slot, n in SLIDE_TYPES[s["type"]].items():
                if slot not in s:
                    continue
                if slot == "items":
                    v = s["items"]
                    out["items"] = [_txt(x, n) for x in v[:5]] if isinstance(v, list) else _txt(v, n)
                else:
                    out[slot] = _txt(s[slot], n)
            slides.append(out)
        if not slides:
            raise TemplateError("Give at least one slide: [{type: front|bullets|story|stat|quote|breaking, ...slots}]. "
                                "Slots may use {field} placeholders from fields.")
        k["slides"] = slides
    declared = {f["key"] for f in k.get("fields") or []}
    used: set[str] = set()
    for s in k["slides"]:
        for v in s.values():
            for x in (v if isinstance(v, list) else [v]):
                used |= set(PH_RE.findall(str(x)))
    for m in sorted(used - declared)[: max(0, MAX_FIELDS - len(k["fields"]))]:  # never print a bare "{x}" on a card
        k["fields"].append({"key": m, "label": m.replace("_", " ").capitalize(), "sample": m.replace("_", " ").capitalize()})
    if k["kind"] == "single":
        k["slides"] = k["slides"][:1]
    return k


def _fill(v, data: dict) -> Any:
    if isinstance(v, list):
        out: list = []
        for x in v:
            m = PH_RE.fullmatch(str(x).strip())
            if m and isinstance(data.get(m.group(1)), list):  # "{features}" with a list value expands to its items
                out += [_txt(i, 120) for i in data[m.group(1)][:5]]
            else:
                out.append(_fill(x, data))
        return [o for o in out if o][:5]
    s = str(v or "")
    m = PH_RE.fullmatch(s.strip())
    if m and isinstance(data.get(m.group(1)), list):
        return [_txt(i, 120) for i in data[m.group(1)][:5]]

    def rep(mm):
        val = data.get(mm.group(1), "")
        return ", ".join(str(x) for x in val) if isinstance(val, list) else _txt(val, 260)
    return PH_RE.sub(rep, s)


def sample_data(spec: dict) -> dict:
    return {f["key"]: f.get("sample") or f.get("label") or "" for f in spec.get("fields") or []}


def brand_for_layout(b: templates.Brand, layout: str) -> templates.Brand:
    """The kit's Brand with the template's base layout laid over its theme (colours, fonts and logo stay the kit's)."""
    if layout in ("", "kit", None) or layout not in templates.LAYOUTS:
        return b
    from . import design_system
    kit = dict(b.kit)
    th = kit.get("theme") if isinstance(kit.get("theme"), dict) and kit["theme"].get("layout") else design_system.theme_for_kit(kit)
    kit["theme"] = {**th, "layout": layout}
    nb = templates.Brand(kit, None)
    nb.logo, nb.front_img, nb.end_img = b.logo, b.front_img, b.end_img  # same decoded images; nothing re-read
    return nb


def render_slides(spec: dict, data: dict | None, b: templates.Brand, ratio: str = "4:5", max_slides: int | None = None) -> list:
    d = {**sample_data(spec), **{k: v for k, v in (data or {}).items() if isinstance(k, str)}}
    b = brand_for_layout(b, spec.get("layout") or "kit")
    single = spec.get("kind") == "single"
    body = (spec.get("slides") or [])[:1] if single else (spec.get("slides") or [])[: max(2, int(max_slides or 10)) - 1]
    total = 1 if single else len(body) + 1
    out = []
    for i, s in enumerate(body):
        n = (i + 1, total)
        f = {kk: _fill(vv, d) for kk, vv in s.items() if kk != "type"}
        t = s["type"]
        if t == "front":
            out.append(templates.front_card(b, ratio, str(f.get("kicker") or "")[:24], str(f.get("title") or spec["name"])[:140],
                                            str(f.get("sub") or "")[:120], n=n))
        elif t == "bullets":
            items = f.get("items") or []
            items = items if isinstance(items, list) else [x.strip() for x in str(items).split(";") if x.strip()]
            out.append(templates.bullets_slide(b, ratio, str(f.get("title") or ""), [str(x) for x in items][:5], n=n))
        elif t in ("story", "stat"):
            out.append(templates.story_slide(b, ratio, i + 1, str(f.get("title") or ""), str(f.get("body") or ""),
                                             str(f.get("tag") or ""), str(f.get("source") or ""), n=n))
        elif t == "quote":
            out.append(templates.quote_card(b, ratio, str(f.get("quote") or ""), str(f.get("speaker") or ""), str(f.get("role") or ""), n=n))
        elif t == "breaking":
            out.append(templates.breaking_card(b, ratio, str(f.get("title") or ""), str(f.get("source") or ""),
                                               str(f.get("when") or "Just now"), None, n=n))
    if not single:
        out.append(templates.end_card(b, ratio, n=(total, total)))  # brand rule: the Follow end card is always last
    return out


class CustomTemplates:
    def __init__(self, house):
        self.house, self.studio, self.store = house, house.studio, house.store
        ensure(self.store)

    def _rows(self, a) -> list[dict]:
        return [dict(r) for r in self.store.q("SELECT * FROM custom_templates WHERE user_id=? ORDER BY id", (a.uid,))]

    def _find(self, a, ref) -> dict:
        ref = str(ref or "").strip()
        slug = ref[len(PREFIX):] if ref.startswith(PREFIX) else ref
        if slug.isdigit():
            rows = self.store.q("SELECT * FROM custom_templates WHERE id=? AND user_id=?", (int(slug), a.uid))
        else:
            rows = self.store.q("SELECT * FROM custom_templates WHERE slug=? AND user_id=?", (slug, a.uid))
        if not rows:
            raise TemplateError("No such custom template. list_templates shows the ids (custom:<slug>).")
        return dict(rows[0])

    @staticmethod
    def _out(row: dict) -> dict:
        k = json.loads(row["data"] or "{}")
        return {"id": PREFIX + row["slug"], "db_id": row["id"], "name": row["name"], "kind": k.get("kind", "carousel"), "video": False,
                "custom": True, "about": k.get("about") or f"Custom template ({len(k.get('slides') or [])} slides, layout {k.get('layout')})",
                "layout": k.get("layout"), "fields": k.get("fields") or [], "slides": k.get("slides") or [], "updated": row["updated"]}

    def _ledger(self, a, verb: str, row: dict, spec: dict) -> None:
        """One Style Ledger entry per change (kind template). The newest entry for a template is its active one."""
        tid = PREFIX + row["slug"]
        rule = (f"Template '{spec.get('name')}' ({tid}) {verb}: {len(spec.get('slides') or [])} slides "
                f"({', '.join(s['type'] for s in spec.get('slides') or [])}) + end card, layout {spec.get('layout')}")[:300]
        self.store.x("UPDATE style_rules SET active=0 WHERE user_id=? AND kind='template' AND spec LIKE ?",
                     (a.uid, f'%"template": "{tid}"%'))
        self.store.x("INSERT INTO style_rules(user_id,kind,spec,rule,source,active,created) VALUES(?,?,?,?,?,?,?)",
                     (a.uid, "template", json.dumps({"type": "template", "template": tid, "action": verb}), rule,
                      f"{getattr(a, 'kind', '')}:{getattr(a, 'label', '')}"[:120], 0 if verb == "deleted" else 1, time.time()))

    def list(self, a) -> list[dict]:
        return [self._out(r) for r in self._rows(a)]

    def get(self, a, ref) -> dict:
        return self._out(self._find(a, ref))

    def spec(self, a, ref) -> dict:
        return json.loads(self._find(a, ref)["data"] or "{}")

    def create(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "create_template")
        spec = clean_spec(body)
        from .userlocks import held
        with held(a.uid, "templates"):
            rows = self._rows(a)
            if len(rows) >= MAX_CUSTOM:
                raise TemplateError(f"Up to {MAX_CUSTOM} custom templates per workspace. Delete one first.")
            base = slugify(str(body.get("slug") or spec["name"]))
            taken = {r["slug"] for r in rows}
            slug, i = base, 2
            while slug in taken:
                slug, i = f"{base}-{i}", i + 1
            now = time.time()
            tid = self.store.x("INSERT INTO custom_templates(user_id,slug,name,data,created,updated) VALUES(?,?,?,?,?,?)",
                               (a.uid, slug, spec["name"], json.dumps(spec), now, now))
            row = dict(self.store.q("SELECT * FROM custom_templates WHERE user_id=? AND slug=?", (a.uid, slug))[0])
        self._ledger(a, "created", row, spec)
        self.store.audit(a.uid, a.label, "create_template", "read", {"template": PREFIX + slug, "slides": len(spec["slides"]), "id": tid})
        return {**self._out(row), "next": f"render_template(template='{PREFIX}{slug}', data={{...fields}}) renders it in the active kit."}

    def update(self, a, ref, body: dict) -> dict:
        self.studio._guard_write(a, "update_template")
        from .userlocks import held
        with held(a.uid, "templates"):
            row = self._find(a, ref)
            spec = clean_spec(body, json.loads(row["data"] or "{}"))
            self.store.x("UPDATE custom_templates SET name=?, data=?, updated=? WHERE id=? AND user_id=?",
                         (spec["name"], json.dumps(spec), time.time(), row["id"], a.uid))
            row = dict(self.store.q("SELECT * FROM custom_templates WHERE id=?", (row["id"],))[0])
        self._ledger(a, "updated", row, spec)
        self.store.audit(a.uid, a.label, "update_template", "read", {"template": PREFIX + row["slug"], "fields": sorted(body)[:12]})
        return self._out(row)

    def delete(self, a, ref) -> dict:
        self.studio._guard_write(a, "delete_template")
        from .userlocks import held
        with held(a.uid, "templates"):
            row = self._find(a, ref)
            self.store.x("DELETE FROM custom_templates WHERE id=? AND user_id=?", (row["id"], a.uid))
        self._ledger(a, "deleted", row, json.loads(row["data"] or "{}"))
        self.store.audit(a.uid, a.label, "delete_template", "read", {"template": PREFIX + row["slug"]})
        return {"deleted": PREFIX + row["slug"]}


def attach(house) -> CustomTemplates:
    if not getattr(house, "custom_templates", None):
        house.custom_templates = CustomTemplates(house)
    return house.custom_templates


def is_custom(tpl) -> bool:
    return isinstance(tpl, str) and tpl.startswith(PREFIX)


# ------------------------------------------------------------------------------------------------ REST + MCP
_SLOT_PROPS: dict[str, Any] = {}
for _slots in SLIDE_TYPES.values():
    for _slot in _slots:
        _SLOT_PROPS[_slot] = {"type": ["array", "string"], "items": {"type": "string"}} if _slot == "items" else {"type": "string"}
_SLIDE_SCHEMA = {"type": "object", "required": ["type"], "properties": {"type": {"type": "string", "enum": list(SLIDE_TYPES)}, **_SLOT_PROPS},
                 "description": "front {kicker,title,sub} | bullets {title, items[]} | story {title,body,tag,source} | stat {title "
                                "(the figure), body, tag} | quote {quote,speaker,role} | breaking {title,source,when}. Text may use "
                                "{field} placeholders."}
_SPEC_PROPS = {"name": {"type": "string"}, "about": {"type": "string"},
               "layout": {"type": "string", "enum": list(LAYOUT_CHOICES), "description": "kit = the active kit's own layout"},
               "kind": {"type": "string", "enum": ["carousel", "single"]},
               "fields": {"type": "array", "maxItems": MAX_FIELDS, "items": {"type": "object", "properties": {
                   "key": {"type": "string"}, "label": {"type": "string"}, "sample": {"type": ["string", "array"], "items": {"type": "string"}}}}},
               "slides": {"type": "array", "maxItems": MAX_SLIDES, "items": _SLIDE_SCHEMA}}
TOOLS: list[dict[str, Any]] = [
    {"name": "create_template", "description": "Build a NEW post template for this workspace (no HuhQueue AI), e.g. a funding announcement "
     "or a job opening: name, a base layout (kit | bold_news | editorial | minimal | playful), fields (each {key, label, sample}) and "
     f"up to {MAX_SLIDES} slides from the renderer's slide types, with {{field}} placeholders. It renders in the active brand kit, the "
     "Follow end card is always added last, it appears in the Templates tab and in list_templates as custom:<slug>, and the change is "
     f"recorded in the Style Ledger. Up to {MAX_CUSTOM} per workspace. Then render_template(template='custom:<slug>', data).",
     "inputSchema": {"type": "object", "required": ["name", "slides"], "properties": _SPEC_PROPS}},
    {"name": "update_template", "description": "Change a custom template (template: custom:<slug>); only the fields you pass change.",
     "inputSchema": {"type": "object", "required": ["template"], "properties": {"template": {"type": "string"}, **_SPEC_PROPS}}},
    {"name": "delete_template", "description": "Delete a custom template (built-in templates can't be deleted).",
     "inputSchema": {"type": "object", "required": ["template"], "properties": {"template": {"type": "string"}}}},
]
READ_ONLY: set[str] = set()


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    from .actions import ActionError
    ct = attach(studio.house)
    try:
        if name == "create_template":
            return ct.create(actor, args)
        if name == "update_template":
            return ct.update(actor, args.get("template"), {k: v for k, v in args.items() if k != "template"})
        if name == "delete_template":
            return ct.delete(actor, args.get("template"))
    except TemplateError as e:
        raise ActionError(str(e), 400)
    return NotImplemented


def add_routes(app, house, actor) -> None:
    from .actions import ActionError
    ct = attach(house)

    def call(fn, *args):
        try:
            return fn(*args)
        except TemplateError as e:
            raise ActionError(str(e), 400)

    @app.get("/api/templates/custom")
    def ct_list(request: Request):
        return {"templates": ct.list(actor(request)), "max": MAX_CUSTOM, "slide_types": SLIDE_TYPES, "layouts": list(LAYOUT_CHOICES)}

    @app.post("/api/templates/custom")
    def ct_create(request: Request, body: dict = Body(...)):
        return call(ct.create, actor(request, write=True), body)

    @app.patch("/api/templates/custom/{ref}")
    def ct_update(ref: str, request: Request, body: dict = Body(...)):
        return call(ct.update, actor(request, write=True), ref, body)

    @app.delete("/api/templates/custom/{ref}")
    def ct_delete(ref: str, request: Request):
        return call(ct.delete, actor(request, write=True), ref)
