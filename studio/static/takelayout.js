// One take > Layout: per-clip layout switcher (scene by scene) and caption look, the same as REST
// GET/POST /api/takes/{id}/clips/{clip}/layout and the MCP tools get_clip_layout / set_clip_layout. Saving re-renders the
// clip on the video worker. Also the account's "Smart layout (beta)" switch (POST /api/takes/settings/smart-layout).
// Late answers never land on a different clip: every load is aborted when the sheet closes or moves to another clip.
"use strict";
(() => {
const IC = {  // Lucide icons (ISC): scan-face, maximize, rows-2, monitor, sparkles, captions
  auto: '<path d="M9.94 14.06 4 20M14.06 9.94 20 4M12 3v2M12 19v2M3 12h2M19 12h2"/><circle cx="12" cy="12" r="3"/>',
  track: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/>',
  fit: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  split: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 12h18"/>',
  screen: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
  captions: '<rect width="18" height="14" x="3" y="5" rx="2"/><path d="M7 15h4M15 15h2M7 11h2M13 11h4"/>',
};
const icon = (k) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${IC[k] || ""}</svg>`;
const NAMES = { auto: "Auto", track: "Follow face", fit: "Full frame", split: "Split", screen: "Screen + face" };
let L = null;
const $l = (id) => document.getElementById(id);
const clock = (s) => { s = Math.max(0, +s || 0); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };

function sheet() {
  let d = $l("clipLayoutDialog");
  if (d) return d;
  d = document.createElement("dialog");
  d.id = "clipLayoutDialog";
  d.className = "tl-dialog";
  d.setAttribute("aria-labelledby", "tlTitle");
  d.innerHTML = `<form method="dialog" class="tl-form">
    <header class="tl-head"><h2 id="tlTitle">Layout &amp; captions</h2><button type="submit" class="btn2 sm" aria-label="Close" data-testid="layout-close">Close</button></header>
    <p class="muted xs" id="tlNote"></p>
    <ol class="tl-scenes" id="tlScenes" data-testid="layout-scenes"></ol>
    <fieldset class="tl-caps"><legend>${icon("captions")} Caption look</legend><div class="tl-chips" id="tlCaps" role="radiogroup" aria-label="Caption look"></div></fieldset>
    <label class="tl-smart"><input type="checkbox" id="tlSmart" data-testid="layout-smart"> <span><b>Smart layout (beta)</b> · lets a free vision model suggest layouts from up to 12 still frames of each clip (no audio). Free endpoints may log what they receive. Applies to every take in your account.</span></label>
    <p class="st err xs" id="tlErr" role="alert"></p>
    <footer class="tl-foot"><button type="button" class="btn sm" id="tlSave" data-testid="layout-save">Save and re-render</button></footer>
  </form>`;
  document.body.appendChild(d);
  d.addEventListener("close", () => { if (L && L.ctl) L.ctl.abort(); L = null; });
  $l("tlSave").onclick = save;
  $l("tlSmart").onchange = smart;
  return d;
}

function draw() {
  const v = L.view;
  $l("tlTitle").textContent = `Layout & captions · clip ${L.n}`;
  $l("tlNote").textContent = v.plan.length ? "Each scene gets its own layout. Auto is the automatic choice; a choice you make here always wins."
    : "This clip hasn't been rendered with scene layouts yet. Pick a layout for the whole clip, or a caption look.";
  const rows = v.plan.length ? v.plan : [{ scene: 0, start: 0, end: 0, layout: "auto" }];
  $l("tlScenes").innerHTML = rows.map((s) => {
    const cur = L.pick[s.scene] || "auto";
    return `<li class="tl-scene" data-scene="${s.scene}"><span class="tl-when">${v.plan.length ? `Scene ${s.scene + 1} · ${clock(s.start)}–${clock(s.end)} · now ${esc(NAMES[s.layout] || s.layout)}` : "Whole clip"}</span>
      <div class="tl-seg" role="radiogroup" aria-label="Layout for scene ${s.scene + 1}">${v.layouts.map((k) => `<button type="button" role="radio" class="chip sm" data-lay="${k}" aria-checked="${cur === k}" data-testid="layout-${k}">${icon(k)}<span>${esc(NAMES[k] || k)}</span></button>`).join("")}</div></li>`;
  }).join("");
  $l("tlScenes").querySelectorAll(".tl-scene").forEach((li) => li.querySelectorAll("[data-lay]").forEach((b) => (b.onclick = () => {
    L.pick[+li.dataset.scene] = b.dataset.lay; draw();
  })));
  $l("tlCaps").innerHTML = v.caption_presets.map((p) => `<button type="button" role="radio" class="chip sm" data-cap="${esc(p.id)}" aria-checked="${L.cap === p.id}" data-testid="caption-${esc(p.id)}">${esc(p.label)}</button>`).join("");
  $l("tlCaps").querySelectorAll("[data-cap]").forEach((b) => (b.onclick = () => { L.cap = b.dataset.cap; draw(); }));
}

window.openClipLayout = async function (id) {
  const t = typeof T !== "undefined" ? T.take : null; if (!t) return;
  if (L && L.ctl) L.ctl.abort();
  const d = sheet();
  const list = t.clips || [];
  L = { id, take: t.id, n: list.findIndex((c) => c.id === id) + 1, ctl: new AbortController(), pick: {}, orig: {}, cap: null, view: null };
  const my = L;
  $l("tlErr").textContent = ""; $l("tlScenes").innerHTML = `<li class="muted xs"><span class="spin" aria-hidden="true"></span> Loading…</li>`;
  if (!d.open) d.showModal();
  try {
    const [v, s] = await Promise.all([api(`/api/takes/${t.id}/clips/${encodeURIComponent(id)}/layout`, { signal: my.ctl.signal }),
      api("/api/takes/settings/smart-layout", { signal: my.ctl.signal })]);
    if (L !== my) return;  // the sheet moved on: drop this late answer
    my.view = v; my.cap = v.caption_preset;
    Object.entries(v.overrides || {}).forEach(([k, o]) => { my.pick[+k] = o.layout; my.orig[+k] = o; });
    $l("tlSmart").checked = !!s.smart_layout;
    draw();
  } catch (e) { if (L === my && e.name !== "AbortError") $l("tlErr").textContent = e.message; }
};

async function save() {
  if (!L || !L.view) return;
  const my = L, b = $l("tlSave");
  const scenes = {};
  const n = Math.max(1, my.view.plan.length);
  for (let i = 0; i < n; i++) {
    const o = my.orig[i], p = my.pick[i] || "auto";
    // an unchanged scene keeps its saved options (a brand background, a stacked screen) set through REST or MCP
    scenes[String(i)] = o && o.layout === p ? { layout: o.layout, ...(o.bg ? { bg: o.bg } : {}), ...(o.style ? { style: o.style } : {}) } : p;
  }
  b.disabled = true;
  try {
    await api(`/api/takes/${my.take}/clips/${encodeURIComponent(my.id)}/layout`, { method: "POST", body: { scenes, caption_preset: my.cap }, signal: my.ctl.signal });
    if (L !== my) return;
    toast("Re-rendering the clip with its new layout…");
    $l("clipLayoutDialog").close();
    if (typeof watchTake === "function") watchTake(my.take);
  } catch (e) { if (L === my && e.name !== "AbortError") $l("tlErr").textContent = e.message; }
  finally { b.disabled = false; }
}

async function smart() {
  const box = $l("tlSmart");
  try { const r = await api("/api/takes/settings/smart-layout", { method: "POST", body: { smart_layout: box.checked } }); box.checked = !!r.smart_layout; }
  catch (e) { box.checked = !box.checked; $l("tlErr").textContent = e.message; }
}
})();
