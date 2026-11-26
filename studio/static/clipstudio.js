// Clip studio: the full-screen editor for one One take clip (owner brief, 26 Nov 2026). The person and the AI director
// edit ONE draft on the server (studio/clip_draft.py: REST /api/takes/{id}/clips/{clip}/draft, /direct, /draft/render;
// MCP get_clip_draft, edit_clip_draft, direct_clip, render_clip_draft). Nothing renders until Render.
//   left: clips, script (words: click to seek, shift/drag to select), caption styles (rendered previews), brand
//   centre: the last render, with the draft's elements as layers you can select, drag and resize; safe zones
//   right: the director (requests become real draft edits, shown with its cursor), inspector, review
//   bottom: timeline on the clip's clock: playhead, scrub, zoom, words, hook, captions, graphics, layout, audio
// Undo/redo: a stack of drafts per clip, saved back to the server; an agent operation is one step.
"use strict";
(() => {
const $s = (id) => document.getElementById(id);
const E = (s) => (typeof esc === "function" ? esc(s) : String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
const REDUCED = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const FPS = 30;
const clock = (s) => { s = Math.max(0, +s || 0); const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
// where each element sits by default (shares of the 1080x1920 frame: x = centre, y = top; the caption band: its middle)
const EL = {
  headline: { label: "Headline", def: { x: 0.5, y: 0.13 }, w: 0.8, h: 0.11 },
  behind: { label: "Words behind you", def: { x: 0.5, y: 0.18 }, w: 0.86, h: 0.13 },
  captions: { label: "Captions", def: { x: 0.5, y: 0.693 }, w: 0.82, h: 0.075, mid: true, fixX: true },
  logo: { label: "Logo", def: { x: 0.89, y: 0.033 }, w: 0.13, h: 0.06 },
  tag: { label: "Brand words", def: { x: 0.5, y: 0.86 }, w: 0.42, h: 0.045 },
};
const Y_RANGE = { captions: [0.151, 0.765] };
const C = {
  take: null, clips: [], i: 0, state: null, draft: null, hist: {}, sel: null, words: [], wsel: null,
  pxs: 24, recipes: null, busy: false, open: false, wave: {},
};
window.CST = C;

// ------------------------------------------------------------------------------------------------ open and close
async function openStudio(take, clipId) {
  if (!take) return;
  C.take = take; C.clips = (take.clips || []).filter((c) => c.url);
  if (!C.clips.length) { toast("This take has no rendered clips yet.", 5000); return; }
  C.i = Math.max(0, C.clips.findIndex((c) => c.id === clipId));
  C.open = true;
  $s("cstudio").hidden = false; document.body.classList.add("cst-on");
  const u = new URL(location.href); u.searchParams.set("take", take.id); u.searchParams.set("studio", C.clips[C.i].id);
  history.replaceState(history.state, "", u);
  if (!C.recipes) api("/api/takes/recipes").then((r) => { C.recipes = r; drawStyles(); }).catch(() => {});
  await loadClip();
  $s("cstPlay").focus();
}
function closeStudio() {
  C.open = false; const v = $s("cstVideo"); v.pause();
  $s("cstudio").hidden = true; document.body.classList.remove("cst-on");
  const u = new URL(location.href); u.searchParams.delete("studio"); history.replaceState(history.state, "", u);
  if (C.take && typeof tkShowIfOpen === "function") tkShowIfOpen(C.take.id).catch(() => {});
}
window.openClipStudio = (clipId) => openStudio(typeof T !== "undefined" ? T.take : null, clipId);
$s("cstBack").onclick = closeStudio;

function clip() { return C.clips[C.i]; }
function base() { return `/api/takes/${encodeURIComponent(C.take.id)}/clips/${encodeURIComponent(clip().id)}`; }

async function loadClip() {
  const c = clip(); C.sel = null; C.wsel = null;
  $s("cstTitle").textContent = `Clip ${C.i + 1} of ${C.clips.length} · ${c.hook || ""}`;
  const v = $s("cstVideo"), src = c.url + (c.rendered_at ? (c.url.includes("?") ? "&" : "?") + "v=" + Math.round(c.rendered_at) : "");
  if (v.dataset.src !== src) { v.dataset.src = src; v.src = src; v.poster = c.cover_url || ""; }
  C.words = (c.words || []).filter((w) => w.e > c.start && w.s < c.end);
  drawClips();
  try {
    C.state = await api(base() + "/draft");
  } catch (e) {
    C.state = { draft: { hook: c.hook, hook_fx: (c.tracks || {}).headline ? "headline" : "none", place: {}, caption_preset: c.caption_preset, fx: null, start: c.start, end: c.end, hook_words: "" },
      rendered: null, dirty: false, log: [], can_render: false, hook_fx_choices: ["none", "headline"], err: e.message };
  }
  C.draft = JSON.parse(JSON.stringify(C.state.draft));
  const h = C.hist[c.id] || (C.hist[c.id] = { undo: [], redo: [] });
  if (C.state.dirty && C.state.rendered && !h.undo.length) h.undo.push(JSON.parse(JSON.stringify(C.state.rendered)));   // edits made elsewhere (the agent chat): Undo goes back to what's rendered
  drawAll();
  fitTl();
  loadWave(c);
}
function drawAll() { drawState(); drawLayers(); drawScript(); drawStyles(); drawBrand(); drawLog(); drawInspector(); drawReview(); drawTimeline(); drawSugg(); }

// ------------------------------------------------------------------------------------------------ draft and history
function rendered() { return (C.state && C.state.rendered) || {}; }
function changed(k) { return JSON.stringify(C.draft[k] ?? null) !== JSON.stringify(rendered()[k] ?? null); }
function dirtyN() { return ["hook", "hook_fx", "hook_words", "place", "caption_preset", "fx", "start", "end", "broll", "punch_in", "trim_pauses", "split"].filter(changed).length; }
function drawState() {
  const c = clip(), n = dirtyN(), h = C.hist[c.id] || { undo: [], redo: [] };
  const busy = c.status === "rendering" || C.busy;
  $s("cstState").textContent = busy ? "Rendering…" : n ? `Draft · ${n} change${n === 1 ? "" : "s"} not rendered` : "Up to date";
  $s("cstState").dataset.state = busy ? "busy" : n ? "draft" : "clean";
  $s("cstUndo").disabled = !h.undo.length || busy; $s("cstRedo").disabled = !h.redo.length || busy;
  const sample = !!C.take.sample;
  $s("cstRender").disabled = !n || busy || !C.state.can_render;
  $s("cstRender").title = sample ? "The sample can't re-render: start a take from your own video to keep edits." : !C.state.can_render ? "This server has no video worker." : "";
  $s("cstDiscard").disabled = !n || busy;
  $s("cstSpread").disabled = !!n || busy || !["ready", "needs_review"].includes(c.status);
  $s("cstBanner").hidden = !n;
  $s("cstBusy").hidden = !busy;
}
// one edit = one undo step; the whole draft goes to the server (the person and agents share it)
async function commit(next, note, { push = true } = {}) {
  const c = clip(), h = C.hist[c.id];
  const prev = JSON.parse(JSON.stringify(C.draft));
  C.draft = next; drawAll();
  try {
    const out = await api(base() + "/draft", { method: "PUT", body: { draft: next, merge: false, note } });
    C.state = out; C.draft = JSON.parse(JSON.stringify(out.draft));
    if (push) { h.undo.push(prev); h.redo = []; }
    drawAll();
  } catch (e) { C.draft = prev; drawAll(); toast(e.message, 6000); }
}
function edit(fn, note) { const n = JSON.parse(JSON.stringify(C.draft)); fn(n); return commit(n, note); }
async function undo() {
  const h = C.hist[clip().id]; if (!h || !h.undo.length) return;
  const to = h.undo.pop(); h.redo.push(JSON.parse(JSON.stringify(C.draft)));
  await commit(to, "Undo", { push: false });
}
async function redo() {
  const h = C.hist[clip().id]; if (!h || !h.redo.length) return;
  const to = h.redo.pop(); h.undo.push(JSON.parse(JSON.stringify(C.draft)));
  await commit(to, "Redo", { push: false });
}
$s("cstUndo").onclick = undo; $s("cstRedo").onclick = redo;
$s("cstDiscard").onclick = async () => {
  if (!(await hqConfirm("Discard the changes in this draft? The clip goes back to how it's rendered now.", { ok: "Discard changes", danger: true }))) return;
  C.hist[clip().id].undo.push(JSON.parse(JSON.stringify(C.draft)));
  try { C.state = await api(base() + "/draft", { method: "PUT", body: { discard: true } }); C.draft = JSON.parse(JSON.stringify(C.state.draft)); drawAll(); }
  catch (e) { toast(e.message, 6000); }
};
$s("cstRender").onclick = async () => {
  C.busy = true; drawState();
  try {
    C.state = await api(base() + "/draft/render", { method: "POST", body: {} });
    clip().status = "rendering"; C.hist[clip().id] = { undo: [], redo: [] };
    watchRender(clip().id);
  } catch (e) { toast(e.message, 6000); }
  finally { C.busy = false; drawState(); }
};
// the take is polled until this clip is back from the worker; then the new video loads in place
function watchRender(cid) {
  const tick = async () => {
    if (!C.open || !C.take) return;
    try {
      const t = await api(`/api/takes/${encodeURIComponent(C.take.id)}`);
      const nc = (t.clips || []).find((x) => x.id === cid);
      if (nc && nc.status !== "rendering") {
        C.take = t; C.clips = (t.clips || []).filter((c) => c.url);
        C.i = Math.max(0, C.clips.findIndex((c) => c.id === cid));
        if (typeof T !== "undefined" && T.take && String(T.take.id) === String(t.id)) T.take = t;
        await loadClip(); toast(nc.status === "failed" ? "The render failed. The previous video is kept." : "Rendered. This is the new clip.", 5000);
        return;
      }
    } catch (e) { /* try again */ }
    setTimeout(tick, 4000);
  };
  setTimeout(tick, 3000);
}
$s("cstSpread").onclick = async () => {
  const id = C.take && C.take.id;
  closeStudio();
  if (id && typeof tkShowIfOpen === "function") { try { await tkShowIfOpen(id); } catch (e) { /* the bar below may still be there */ } }
  const bar = document.getElementById("spreadBar");
  if (bar && !bar.hidden) { bar.scrollIntoView({ block: "center" }); const g = document.getElementById("spreadGo"); if (g && !g.hidden) g.focus(); }
  toast("The Spread makes Buffer drafts or queue slots. Nothing posts by itself.", 5000);
};

// ------------------------------------------------------------------------------------------------ left: clips
function drawClips() {
  $s("cstClips").innerHTML = C.clips.map((c, k) => {
    const d = (c.end - c.start).toFixed(0), s = c.score != null && c.score !== "" ? (Math.max(0, Math.min(100, +c.score)) / 10).toFixed(1) : "";
    const flag = c.status === "needs_review" ? `<span class="cst-flag">Needs a look</span>` : c.status === "rendering" ? `<span class="cst-muted">Rendering</span>` : "";
    return `<li><button type="button" class="cst-clip${k === C.i ? " on" : ""}" id="cst-clip-${E(c.id)}" data-testid="cst-clip" data-k="${k}" aria-current="${k === C.i}">
      <span class="cst-thumb">${c.cover_url ? `<img src="${E(c.cover_url)}" alt="" loading="lazy">` : ""}<span class="cst-dur">${d} s</span></span>
      <span class="cst-cl"><b>${E(c.hook || "Clip " + (k + 1))}</b><span class="cst-muted">${k + 1}${s ? ` · score ${s}` : ""}</span>${flag}</span></button></li>`;
  }).join("");
  $s("cstClips").querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => { C.i = +b.dataset.k; $s("cstVideo").pause(); loadClip(); }));
}
document.querySelectorAll("#cstudio [data-ltab]").forEach((b) => (b.onclick = () => ltab(b.dataset.ltab)));
function ltab(k) {
  document.querySelectorAll("#cstudio [data-ltab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.ltab === k)));
  document.querySelectorAll("#cstudio [data-lpane]").forEach((x) => (x.hidden = x.dataset.lpane !== k));
}
document.querySelectorAll("#cstudio [data-rtab]").forEach((b) => (b.onclick = () => rtab(b.dataset.rtab)));
function rtab(k) {
  document.querySelectorAll("#cstudio [data-rtab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.rtab === k)));
  document.querySelectorAll("#cstudio [data-rpane]").forEach((x) => (x.hidden = x.dataset.rpane !== k));
}

// ------------------------------------------------------------------------------------------------ time mapping
// The video is the finished clip: cover frame, then (when one was joined) the cold open, then the body. A word's place
// on that clock is exact when nothing was cut out of the body; with pauses cut it's close, and the hint says so.
function lead() { const tr = clip().tracks || {}; return (tr.cover_s || 0.2) + (tr.cold_open_s || 0); }
function approx() { const c = clip(); return (c.trimmed_s || 0) > 0 || !!((c.tracks || {}).cold_open_s); }
function tOf(src) { return lead() + (src - C.draft.start); }
function dur() { const c = clip(), v = $s("cstVideo"); return (v.duration && isFinite(v.duration) ? v.duration : +c.duration) || (c.end - c.start); }

// ------------------------------------------------------------------------------------------------ left: script
function drawScript() {
  const c = clip();
  $s("cstScript").innerHTML = C.words.length ? C.words.map((w, k) => {
    const out = w.s < C.draft.start - 0.01 || w.e > C.draft.end + 0.01;
    const on = C.wsel && k >= C.wsel[0] && k <= C.wsel[1];
    return `<button type="button" class="cst-w${out ? " cut" : ""}${on ? " sel" : ""}" id="cst-w-${k}" data-testid="cst-word" data-k="${k}">${E(w.w)}</button>`;
  }).join("") : `<p class="cst-muted">No words saved for this clip.</p>`;
  let down = null;
  $s("cstScript").querySelectorAll("[data-k]").forEach((b) => {
    b.onpointerdown = (e) => { down = +b.dataset.k; if (e.shiftKey && C.wsel) { selWords(Math.min(C.wsel[0], down), Math.max(C.wsel[1], down)); down = null; } };
    b.onpointerenter = () => { if (down != null && down !== +b.dataset.k) selWords(Math.min(down, +b.dataset.k), Math.max(down, +b.dataset.k), true); };
    b.onclick = (e) => {
      if (e.shiftKey) return;
      const k = +b.dataset.k;
      if (down === k && !(C.wsel && C.wsel[0] !== C.wsel[1] && k >= C.wsel[0] && k <= C.wsel[1])) { selWords(k, k); seek(tOf(C.words[k].s)); }
      down = null;
    };
  });
  document.addEventListener("pointerup", () => (down = null), { once: true });
  void c;
}
function selWords(a, b, live) {
  C.wsel = [a, b];
  document.querySelectorAll("#cstScript .cst-w").forEach((x) => x.classList.toggle("sel", +x.dataset.k >= a && +x.dataset.k <= b));
  document.querySelectorAll("#cstTracks .cst-wt").forEach((x) => x.classList.toggle("sel", +x.dataset.k >= a && +x.dataset.k <= b));
  drawSel(); if (!live) drawSugg();
}
function selText() { return C.wsel ? C.words.slice(C.wsel[0], C.wsel[1] + 1).map((w) => w.w).join(" ") : ""; }
function drawSel() {
  const box = $s("cstSel");
  if (!C.wsel) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<span>Selected: “${E(selText().slice(0, 80))}”</span><button type="button" class="cst-x" id="cstSelClear" data-testid="cst-selection-clear" aria-label="Clear the selection">×</button>`;
  $s("cstSelClear").onclick = () => { C.wsel = null; drawScript(); drawTimeline(); drawSel(); drawSugg(); };
}

// ------------------------------------------------------------------------------------------------ left: styles, brand
function drawStyles() {
  const box = $s("cstStyles"); if (!box || !C.draft) return;
  const caps = (C.recipes && C.recipes.captions) || [];
  box.innerHTML = caps.length ? caps.map((c) => `<button type="button" class="cst-style${C.draft.caption_preset === c.id ? " on" : ""}" id="cst-style-${E(c.id)}" data-testid="cst-style-${E(c.id)}" data-style="${E(c.id)}" aria-pressed="${C.draft.caption_preset === c.id}">
      <img src="/static/captions/${E(c.id)}.webp" alt="" loading="lazy"><b>${E(c.label)}</b><span>${E(c.about || "")}</span></button>`).join("")
    : `<p class="cst-muted">Loading caption styles…</p>`;
  box.querySelectorAll("[data-style]").forEach((b) => (b.onclick = () => edit((d) => (d.caption_preset = b.dataset.style), `Captions: ${b.querySelector("b").textContent}`)));
}
function drawBrand() {
  const P = C.draft.place || {}, lg = P.logo || {}, tg = P.tag || {};
  $s("cstBrand").innerHTML = `
    <label class="cst-row"><input type="checkbox" id="cstLogoOn" data-testid="cst-logo-on" ${lg.on !== false ? "checked" : ""}> Show the logo</label>
    <label class="cst-field">Brand words <span class="cst-muted">your name, handle or tagline</span>
      <input type="text" id="cstTagText" data-testid="cst-tag-text" maxlength="40" value="${E(tg.text || "")}" placeholder="@yourbrand"></label>
    <p class="cst-muted">Drag the logo or the brand words on the frame to place them. The rest of the look comes from your brand kit.</p>`;
  $s("cstLogoOn").onchange = (e) => edit((d) => { d.place = d.place || {}; d.place.logo = { ...(d.place.logo || {}), on: e.target.checked }; }, e.target.checked ? "Logo shown" : "Logo hidden");
  $s("cstTagText").onchange = (e) => edit((d) => { d.place = d.place || {}; const t = e.target.value.trim(); d.place.tag = { ...(d.place.tag || {}), text: t, on: !!t }; }, "Brand words");
}

// ------------------------------------------------------------------------------------------------ centre: layers
function elOn(k) {
  const d = C.draft, P = (d.place || {})[k] || {};
  if (k === "headline") return d.hook_fx === "headline";
  if (k === "behind") return d.hook_fx === "behind";
  if (k === "logo") return P.on !== false;
  if (k === "tag") return P.on !== false && !!(P.text || "").trim();
  return true;
}
function pos(k) { return { ...EL[k].def, ...(((C.draft.place || {})[k]) || {}) }; }
function box(k) {
  const d = EL[k], p = pos(k), sc = p.scale || 1, w = Math.min(0.96, d.w * sc), h = d.h * sc;
  return { left: d.fixX ? 0.5 - w / 2 : p.x - w / 2, top: d.mid ? p.y - h / 2 : p.y, w, h };
}
function behindWord() {
  const hw = (C.draft.hook_words || "").trim();
  if (hw) return hw.toUpperCase();
  return ((C.draft.hook || "").split(/\s+/).filter((w) => w.length > 3).sort((a, b) => b.length - a.length)[0] || "").replace(/[^\w'’-]/g, "").toUpperCase();
}
function elChanged(k) {
  const a = ((C.draft.place || {})[k]) || {}, b = ((rendered().place || {})[k]) || {};
  if (JSON.stringify(a) !== JSON.stringify(b)) return true;
  if ((k === "headline" || k === "behind") && (changed("hook_fx") || changed("hook") || changed("hook_words"))) return true;
  return k === "captions" && changed("caption_preset");
}
function drawLayers() {
  const show = $s("cstOutlines").checked;
  const L = $s("cstLayers");
  L.innerHTML = Object.keys(EL).filter(elOn).map((k) => {
    const b = box(k), p = pos(k), ch = elChanged(k);
    const txt = k === "headline" ? (C.draft.hook || "").toUpperCase() : k === "behind" ? behindWord() : k === "captions" ? (C.draft.caption_preset || "").replace(/_/g, " ") : k === "logo" ? "Logo" : ((C.draft.place || {}).tag || {}).text || "";
    const ring = k === "behind" && p.ring ? `<span class="cst-ring" aria-hidden="true"></span>` : "";
    return `<div class="cst-layer k-${k}${ch ? " ch" : ""}${C.sel && C.sel.el === k ? " sel" : ""}${show || ch || (C.sel && C.sel.el === k) ? "" : " ghost"}" id="cst-layer-${k}" data-testid="cst-layer-${k}" data-el="${k}" tabindex="0" role="button"
      aria-label="${EL[k].label}${ch ? ", changed in the draft" : ""}: drag to move, arrow keys to nudge"
      style="left:${(b.left * 100).toFixed(2)}%;top:${(b.top * 100).toFixed(2)}%;width:${(b.w * 100).toFixed(2)}%;height:${(b.h * 100).toFixed(2)}%">
      ${ring}<span class="cst-lt">${E(txt)}</span><span class="cst-ltag">${EL[k].label}${ch ? " · draft" : ""}</span>
      ${k !== "captions" ? `<span class="cst-handle" data-testid="cst-resize-${k}" aria-hidden="true"></span>` : ""}</div>`;
  }).join("");
  L.querySelectorAll("[data-el]").forEach(dragLayer);
  if (C.sel && C.sel.el && C.focusLayer) { const f = document.getElementById(`cst-layer-${C.sel.el}`); if (f) f.focus({ preventScroll: true }); }
  $s("cstSafeZ").hidden = !$s("cstSafe").checked;
}
$s("cstSafe").onchange = drawLayers; $s("cstOutlines").onchange = drawLayers;
function clampPos(k, x, y, sc) {
  const d = EL[k], w = Math.min(0.96, d.w * sc), h = d.h * sc, r = Y_RANGE[k] || [0.04, 0.92 - h * 0.2];
  const out = { y: +Math.max(r[0], Math.min(r[1], y)).toFixed(4) };
  if (!d.fixX) out.x = +Math.max(Math.max(0.05, w / 2), Math.min(Math.min(0.95, 1 - w / 2), x)).toFixed(4);
  return out;
}
function dragLayer(el) {
  const k = el.dataset.el, stage = $s("cstStage");
  const apply = (p) => { const b = boxFrom(k, p); Object.assign(el.style, { left: `${(b.left * 100).toFixed(2)}%`, top: `${(b.top * 100).toFixed(2)}%`, width: `${(b.w * 100).toFixed(2)}%`, height: `${(b.h * 100).toFixed(2)}%` }); };
  el.onpointerdown = (e) => {
    select({ el: k });
    e.preventDefault(); el.focus({ preventScroll: true }); el.setPointerCapture(e.pointerId);
    const r = stage.getBoundingClientRect(), p0 = pos(k), sc0 = p0.scale || 1;
    const resize = e.target.classList.contains("cst-handle");
    const sx = e.clientX, sy = e.clientY;
    let cur = { ...p0 };
    el.onpointermove = (ev) => {
      if (resize) {
        const g = 1 + ((ev.clientX - sx) / r.width) * 2.2;
        cur = { ...p0, scale: +Math.max(0.5, Math.min(2, sc0 * g)).toFixed(3) };
      } else {
        cur = { ...p0, ...clampPos(k, p0.x + (ev.clientX - sx) / r.width, p0.y + (ev.clientY - sy) / r.height, sc0) };
      }
      apply(cur); el.classList.add("drag");
    };
    el.onpointerup = el.onpointercancel = () => {
      el.onpointermove = null; el.classList.remove("drag");
      const moved = Math.abs((cur.x ?? 0) - (p0.x ?? 0)) > 0.002 || Math.abs(cur.y - p0.y) > 0.002 || (cur.scale || 1) !== sc0;
      if (moved) placeEl(k, resize ? { scale: cur.scale } : (EL[k].fixX ? { y: cur.y } : { x: cur.x, y: cur.y }), resize ? `Resized the ${EL[k].label.toLowerCase()}` : `Moved the ${EL[k].label.toLowerCase()}`);
    };
  };
  el.onkeydown = (e) => {
    const st = e.shiftKey ? 0.05 : 0.01, p = pos(k);
    const dx = { ArrowLeft: -st, ArrowRight: st }[e.key] || 0, dy = { ArrowUp: -st, ArrowDown: st }[e.key] || 0;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select({ el: k }); return; }
    if (!dx && !dy) return;
    e.preventDefault(); C.focusLayer = true;
    placeEl(k, clampPos(k, (p.x ?? 0.5) + dx, p.y + dy, p.scale || 1), `Moved the ${EL[k].label.toLowerCase()}`).finally(() => (C.focusLayer = false));
  };
}
function boxFrom(k, p) { const d = EL[k], sc = p.scale || 1, w = Math.min(0.96, d.w * sc), h = d.h * sc; return { left: d.fixX ? 0.5 - w / 2 : p.x - w / 2, top: d.mid ? p.y - h / 2 : p.y, w, h }; }
function placeEl(k, v, note) {
  return edit((d) => { d.place = d.place || {}; d.place[k] = { ...(d.place[k] || {}), ...v }; if (k === "captions") delete d.place[k].x; }, note);
}

// ------------------------------------------------------------------------------------------------ selection sync
function select(s) {
  C.sel = s;
  document.querySelectorAll("#cstLayers .cst-layer").forEach((x) => x.classList.toggle("sel", !!(s && s.el === x.dataset.el)));
  document.querySelectorAll("#cstTracks [data-sel]").forEach((x) => x.classList.toggle("sel", !!s && x.dataset.sel === selKey(s)));
  drawInspector();
  if (s) rtab("inspect");
}
function selKey(s) { return s.el ? "el:" + s.el : s.fx != null ? "fx:" + s.fx : s.broll != null ? "br:" + s.broll : ""; }

// ------------------------------------------------------------------------------------------------ right: inspector
function drawInspector() {
  const box = $s("cstInspect"), s = C.sel, d = C.draft;
  if (!s) { box.innerHTML = `<p class="cst-muted">Select an element on the frame or a block on the timeline.</p>`; return; }
  if (s.broll != null) {
    const b = (d.broll || [])[s.broll];
    if (!b) { box.innerHTML = `<p class="cst-muted">That cutaway is gone.</p>`; return; }
    box.innerHTML = `<h3 class="cst-ih">B-roll</h3><p class="cst-quote">${b.thumb ? `<img class="cst-brthumb" src="${E(b.thumb)}" alt="" loading="lazy"> ` : ""}${E(b.query || "Stock footage")}</p>
      <p class="cst-muted">${(b.e - b.s).toFixed(1)} s long, starting at ${b.s.toFixed(1)} s. Your voice plays over it. ${E(b.credit || "")}</p>
      <button type="button" class="btn2 sm danger" id="cstBrDel" data-testid="cst-broll-delete">Remove this B-roll</button>`;
    $s("cstBrDel").onclick = () => { C.sel = null; edit((n) => n.broll.splice(s.broll, 1), "Removed the B-roll"); };
    return;
  }
  if (s.fx != null) {
    const f = (d.fx || [])[s.fx];
    if (!f) { box.innerHTML = `<p class="cst-muted">This graphic was chosen automatically by the last render. Pick graphics yourself with the director or below.</p>`; return; }
    box.innerHTML = `<h3 class="cst-ih">${f.kind === "stat" ? "Counter" : "Kinetic line"}</h3><p class="cst-quote">“${E(f.text)}”</p>
      <div class="cst-seg" role="group" aria-label="Kind"><button type="button" id="cstFxStat" data-testid="cst-fx-kind-stat" aria-pressed="${f.kind === "stat"}">Counter</button><button type="button" id="cstFxLine" data-testid="cst-fx-kind-line" aria-pressed="${f.kind === "line"}">Kinetic line</button></div>
      <button type="button" class="btn2 sm danger" id="cstFxDel" data-testid="cst-fx-delete">Remove this graphic</button>`;
    $s("cstFxStat").onclick = () => edit((n) => (n.fx[s.fx].kind = "stat"), "Graphic: counter");
    $s("cstFxLine").onclick = () => edit((n) => (n.fx[s.fx].kind = "line"), "Graphic: kinetic line");
    $s("cstFxDel").onclick = () => { C.sel = null; edit((n) => n.fx.splice(s.fx, 1), `Removed “${f.text}”`); };
    return;
  }
  const k = s.el, p = pos(k), choices = (C.state && C.state.hook_fx_choices) || ["none", "headline"];
  const num = (a, label, v, min, max, step) => `<label class="cst-num">${label}<input type="number" id="cst-${k}-${a}" data-testid="cst-${k}-${a}" data-a="${a}" value="${(+v).toFixed(a === "scale" ? 2 : 3)}" min="${min}" max="${max}" step="${step}"></label>`;
  let extra = "";
  if (k === "headline" || k === "behind") {
    extra = `<div class="cst-seg" role="group" aria-label="Hook look">${choices.map((c) => `<button type="button" data-hookfx="${c}" id="cst-hookfx-${c}" data-testid="cst-hookfx-${c}" aria-pressed="${d.hook_fx === c}">${{ none: "None", headline: "Headline", behind: "Words behind you" }[c]}</button>`).join("")}</div>
      <label class="cst-field">Hook line <span class="cst-muted">said in the clip</span><textarea id="cstHook" data-testid="cst-hook" rows="2" maxlength="120">${E(d.hook || "")}</textarea></label>
      ${k === "behind" ? `<label class="cst-field">Words behind you <span class="cst-muted">1 to 3 words of the hook</span><input type="text" id="cstHookWords" data-testid="cst-hook-words" maxlength="40" value="${E(d.hook_words || "")}" placeholder="${E(behindWord())}"></label>
      <label class="cst-row"><input type="checkbox" id="cstRing" data-testid="cst-ring" ${p.ring ? "checked" : ""}> Orbit ring around you</label>` : ""}`;
  }
  if (k === "captions") {
    extra = `<p class="cst-muted">Style: ${E((d.caption_preset || "").replace(/_/g, " "))}. Change it in Styles. The face check still runs after the render.</p>`;
  }
  box.innerHTML = `<h3 class="cst-ih">${EL[k].label}</h3>${extra}
    <div class="cst-nums">${EL[k].fixX ? "" : num("x", "X", p.x, 0.05, 0.95, 0.01)}${num("y", "Y", p.y, 0.04, 0.92, 0.01)}${k !== "captions" ? num("scale", "Size", p.scale || 1, 0.5, 2, 0.05) : ""}</div>
    <button type="button" class="btn2 sm" id="cstElReset" data-testid="cst-el-reset">Automatic place</button>`;
  box.querySelectorAll("[data-a]").forEach((i) => (i.onchange = () => {
    const a = i.dataset.a, v = +i.value; if (!isFinite(v)) return;
    placeEl(k, a === "scale" ? { scale: Math.max(0.5, Math.min(2, v)) } : clampPos(k, a === "x" ? v : (p.x ?? 0.5), a === "y" ? v : p.y, p.scale || 1), `${EL[k].label}: ${a}`);
  }));
  box.querySelectorAll("[data-hookfx]").forEach((b) => (b.onclick = () => { edit((n) => (n.hook_fx = b.dataset.hookfx), `Hook look: ${b.textContent}`).then(() => { if (b.dataset.hookfx !== "none") select({ el: b.dataset.hookfx === "behind" ? "behind" : "headline" }); }); }));
  const hk = $s("cstHook"); if (hk) hk.onchange = () => edit((n) => (n.hook = hk.value.trim()), "Hook line");
  const hw = $s("cstHookWords"); if (hw) hw.onchange = () => edit((n) => (n.hook_words = hw.value.trim()), "Words behind you");
  const rg = $s("cstRing"); if (rg) rg.onchange = () => placeEl("behind", { ring: rg.checked }, rg.checked ? "Orbit ring on" : "Orbit ring off");
  $s("cstElReset").onclick = () => edit((n) => { if (n.place) delete n.place[k]; }, `${EL[k].label}: automatic place`);
}

// ------------------------------------------------------------------------------------------------ right: review
function drawReview() {
  const c = clip(), r = c.review || {}, fs = c.face_safe;
  const rows = [];
  if (fs && fs.status) rows.push(["Captions clear of faces", fs.status === "ok" ? "ok" : "warning", fs.max_cover != null ? `most covered ${(fs.max_cover * 100).toFixed(0)}%` : fs.status]);
  else rows.push(["Captions clear of faces", "wait", "not measured"]);
  (r.issues || []).forEach((i) => rows.push([i.what, "warning", `${i.why} Try: ${i.fix}`]));
  (r.works || []).forEach((w) => rows.push([w, "ok", ""]));
  const pf = Object.values(c.preflight || {}), bad = pf.filter((p) => p.status !== "ok");
  if (pf.length) rows.push(["Channel checks", bad.length ? "warning" : "ok", bad.length ? bad.map((p) => p.chip).join("; ") : `${pf.length} of ${pf.length} pass`]);
  $s("cstReview").innerHTML = `<p class="cst-muted">From the last render. A draft is checked again when it renders.</p><ul class="cst-checks">${rows.map(([w, s, v]) =>
    `<li class="s-${s}" data-testid="cst-check" data-status="${s}"><b aria-hidden="true">${{ ok: "✓", warning: "!", wait: "–" }[s]}</b><span>${E(w)}${v ? `<small>${E(v)}</small>` : ""}</span></li>`).join("")}</ul>
    ${c.status === "needs_review" && !C.take.sample ? `<div class="cst-needs"><p><b>Needs your look.</b> ${E(c.review_reason || "")}</p><button type="button" class="btn sm" id="cstReviewOk" data-testid="cst-review-ok">Looks fine, use it</button></div>` : ""}`;
  const ok = $s("cstReviewOk"); if (ok) ok.onclick = async () => { if (typeof reviewOk === "function") { await reviewOk(c.id); c.status = "ready"; drawReview(); drawState(); } };
}

// ------------------------------------------------------------------------------------------------ right: director
const SUGG = ["Make the number pop without covering my face", "Make the opening stronger", "Put the words behind my head with an orbit ring",
  "Use more cinematic captions", "Add b-roll", "Fewer effects", "Move the captions higher"];
function drawSugg() {
  const list = C.wsel ? ["Make this pop", "Use this as the hook", "Cut this from the clip", "Put these words behind my head"] : SUGG;
  $s("cstSugg").innerHTML = list.map((s, k) => `<button type="button" class="cst-chip" id="cst-sugg-${k}" data-testid="cst-suggestion" data-s="${E(s)}">${E(s)}</button>`).join("");
  $s("cstSugg").querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => { const s = b.dataset.s; ask(s === "Use this as the hook" ? "make the opening stronger with this line" : s); }));
}
function drawLog() {
  const log = (C.state && C.state.log) || [];
  const fold = [];
  log.slice(-40).forEach((e) => { const p = fold[fold.length - 1]; if (p && e.by !== "agent" && p.by !== "agent" && p.what === e.what) p.n = (p.n || 1) + 1; else fold.push({ ...e }); });
  const turns = [...fold.map((e) => ({ at: e.at, e })), ...((C.qa && C.qa[clip().id]) || []).map((q) => ({ at: q.at, q }))].sort((x, y) => x.at - y.at);
  const tm = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $s("cstLog").innerHTML = turns.length ? turns.map(({ e, q }) => {
    if (q) return `<li class="cst-turn me" data-testid="cst-chat-msg" data-role="user"><p>${E(q.text)}</p></li><li class="cst-turn ai${q.notyet ? " notyet" : ""}" data-testid="cst-chat-msg" data-role="agent"><p>${E(q.reply)}</p></li>`;
    if (e.by === "agent") {
      const ops = (e.ops || []).filter((o) => o.what);
      return `<li class="cst-turn me" data-testid="cst-chat-msg" data-role="user"><p>${E(e.what)}</p></li><li class="cst-turn ai" data-testid="cst-log-entry" data-role="agent" data-by="agent">${e.reply ? `<p>${E(e.reply)}</p>` : ""}${ops.length ? `<ul class="cst-ops">${ops.map((o) => `<li><b>${E(o.what)}</b>${o.why ? `<span>${E(o.why)}</span>` : ""}</li>`).join("")}</ul>` : ""}<time class="cst-muted">${tm(e.at)}</time></li>`;
    }
    return `<li class="cst-sys" data-testid="cst-log-entry" data-by="${E(e.by)}"><span>${E(e.what)}${e.n > 1 ? ` ×${e.n}` : ""}</span><time>${tm(e.at)}</time></li>`;
  }).join("") : `<li class="cst-empty"><b>Chat about this clip</b><span>Ask a question or tell me what to change: the opening, captions, graphics, words behind your head, B-roll, zoom, length. My edits are drafts you can undo.</span></li>`;
  $s("cstLog").scrollTop = 1e6;
}
$s("cstAsk").onsubmit = (e) => { e.preventDefault(); ask($s("cstAskIn").value); };
$s("cstAskIn").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask($s("cstAskIn").value); } };
async function ask(text) {
  text = String(text || "").trim(); if (!text || C.busy) return;
  const c = clip(), h = C.hist[c.id], prev = JSON.parse(JSON.stringify(C.draft));
  const body = { instruction: text };
  if (C.wsel) body.selection = { start: C.words[C.wsel[0]].s, end: C.words[C.wsel[1]].e };
  $s("cstAskGo").disabled = true; $s("cstAskIn").value = ""; C.busy = true;
  const pend = document.createElement("li"); pend.className = "cst-msg agent pending"; pend.innerHTML = `<p class="cst-req">“${E(text)}”</p><p class="cst-muted"><span class="spin" aria-hidden="true"></span> Working on the draft…</p>`;
  $s("cstLog").appendChild(pend); $s("cstLog").scrollTop = 1e6;
  try {
    const out = await api(base() + "/direct", { method: "POST", body });
    const ops = out.ops || [];
    // the cursor visits what each edit changed, in order; the draft updates after each visit
    for (const o of ops) await visit(o.focus || {});
    C.state = out; C.draft = JSON.parse(JSON.stringify(out.draft));
    if (ops.length) { h.undo.push(prev); h.redo = []; }
    drawAll();
    const last = ops[ops.length - 1];
    if (last && last.focus && last.focus.element) select({ el: last.focus.element }); else rtab("director");
    if (!ops.length) { ((C.qa = C.qa || {})[c.id] = (C.qa[c.id] || [])).push({ at: Date.now() / 1000, text, reply: out.reply, notyet: (out.not_yet || []).length > 0 }); drawLog(); }
  } catch (e) { toast(e.message, 6000); drawLog(); }
  finally { pend.remove(); C.busy = false; $s("cstAskGo").disabled = false; hideCursor(); drawState(); }
}
// ------------------------------------------------------------------------------------------------ the agent cursor
function cursorTo(r) {
  const cur = $s("cstCursor");
  cur.hidden = false; cur.classList.add("on");
  const x = r.left + r.width / 2, y = r.top + Math.min(r.height / 2, 24);
  cur.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  return new Promise((ok) => setTimeout(ok, REDUCED() ? 60 : 320));
}
function hideCursor() { const cur = $s("cstCursor"); setTimeout(() => cur.classList.remove("on"), 600); }
async function visit(f) {
  let target = null;
  if (f.element) {
    if (!elOn(f.element)) { target = $s("cstStage"); }
    else target = document.getElementById(`cst-layer-${f.element}`) || $s("cstStage");
  } else if (f.s != null) {
    const t = tOf(f.s); seek(t);
    const x0 = t * C.pxs, w = Math.max(24, ((f.e || f.s) - f.s) * C.pxs);
    const sc = $s("cstTlScroll"); sc.scrollLeft = Math.max(0, x0 - sc.clientWidth / 3);
    const row = document.querySelector(`#cstTracks [data-track="${f.track || "words"}"]`) || $s("cstTracks");
    const rr = row.getBoundingClientRect(), base0 = $s("cstTlIn").getBoundingClientRect();
    target = { getBoundingClientRect: () => ({ left: base0.left + x0 + 120, top: rr.top, width: w, height: rr.height }) };
    flash({ left: base0.left + x0 + 120, top: rr.top, width: w, height: rr.height });
  } else if (f.track) {
    target = document.querySelector(`#cstTracks [data-track="${f.track}"]`);
  }
  if (!target) return;
  const r = target.getBoundingClientRect();
  if (target.classList) { target.classList.add("cst-agent-hi"); setTimeout(() => target.classList && target.classList.remove("cst-agent-hi"), 900); }
  await cursorTo(r);
}
function flash(r) {
  const f = document.createElement("div"); f.className = "cst-flash"; f.setAttribute("aria-hidden", "true");
  Object.assign(f.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  document.body.appendChild(f); setTimeout(() => f.remove(), 900);
}

// ------------------------------------------------------------------------------------------------ transport
const v = $s("cstVideo");
function seek(t) { v.currentTime = Math.max(0, Math.min(dur() - 0.01, t)); tick(); }
$s("cstPlay").onclick = () => (v.paused ? v.play().catch(() => {}) : v.pause());
v.onplay = v.onpause = () => {
  $s("cstPlay").setAttribute("aria-label", v.paused ? "Play" : "Pause");
  $s("cstPlay").innerHTML = v.paused ? `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>` : `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h4v16H7zM13 4h4v16h-4z"/></svg>`;
};
$s("cstFrameBack").onclick = () => { v.pause(); seek(v.currentTime - 1 / FPS); };
$s("cstFrameFwd").onclick = () => { v.pause(); seek(v.currentTime + 1 / FPS); };
$s("cstMute").onclick = () => { v.muted = !v.muted; $s("cstMute").setAttribute("aria-pressed", String(v.muted)); };
v.ontimeupdate = tick; v.onloadedmetadata = () => { fitTl(); tick(); };
function tick() {
  $s("cstTime").textContent = `${clock(v.currentTime)} / ${clock(dur())}`;
  $s("cstPh").style.left = `${120 + v.currentTime * C.pxs}px`;
  const src = v.currentTime - lead() + C.draft.start;
  let hit = -1;
  if (!approx()) C.words.forEach((w, k) => { if (src >= w.s && src < w.e) hit = k; });
  document.querySelectorAll("#cstScript .cst-w.now, #cstTracks .cst-wt.now").forEach((x) => x.classList.remove("now"));
  if (hit >= 0) { const a = document.getElementById(`cst-w-${hit}`), b = document.getElementById(`cst-wt-${hit}`); if (a) a.classList.add("now"); if (b) b.classList.add("now"); }
  const bp = $s("cstBrPrev"), bhit = (C.draft.broll || []).find((b) => v.currentTime >= b.s && v.currentTime < b.e);
  if (bp) { bp.hidden = !bhit; if (bhit) { bp.style.backgroundImage = bhit.thumb ? `url("${bhit.thumb.replace(/"/g, "")}")` : ""; bp.textContent = bhit.thumb ? "" : "B-roll"; bp.dataset.q = bhit.query || ""; } }
  // the layers show over the moments they're on screen (hook: the opening; the rest: all clip long)
  const hookOn = v.currentTime < lead() + 2.9;
  ["headline", "behind"].forEach((k) => { const el = document.getElementById(`cst-layer-${k}`); if (el) el.classList.toggle("off", !hookOn && !(C.sel && C.sel.el === k)); });
}
document.addEventListener("keydown", (e) => {
  if (!C.open || $s("cstudio").hidden) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { if (tag === "input" || tag === "textarea") return; e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (e.key === " " && tag !== "button") { e.preventDefault(); $s("cstPlay").click(); }
  if (e.key === "Escape" && !document.querySelector("dialog[open]")) { if (C.sel) { C.sel = null; select(null); } else closeStudio(); }
  if (e.key === "," || e.key === ".") { v.pause(); seek(v.currentTime + (e.key === "," ? -1 : 1) / FPS); }
});

// ------------------------------------------------------------------------------------------------ timeline
function fxSpans() {
  // the draft's picks on the clip's clock (from the words they're on); with no picks, what the last render drew
  const d = C.draft;
  if (Array.isArray(d.fx)) {
    return d.fx.map((f, k) => {
      const toks = f.text.toLowerCase().split(/\s+/).map((t) => t.replace(/[^\w%$€£.']/g, ""));
      const norm = C.words.map((w) => String(w.w).toLowerCase().replace(/[^\w%$€£.']/g, ""));
      let n = 0, at = -1;
      for (let i = 0; i + toks.length <= norm.length; i++) { if (toks.every((t, j) => norm[i + j] === t)) { if (n === (f.nth || 0)) { at = i; break; } n++; } }
      if (at < 0) return null;
      const s = tOf(C.words[at].s), e = Math.max(tOf(C.words[at + toks.length - 1].e) + 0.35, s + (f.kind === "stat" ? 1.8 : 0.8));
      return { k, s, e, label: f.text, kind: f.kind, draft: true };
    }).filter(Boolean);
  }
  return ((clip().tracks || {}).fx || []).map((f, k) => ({ k: -1 - k, s: f.s, e: f.e, label: f.text || f.kind, kind: f.kind, draft: false }));
}
function drawTimeline() {
  if (!C.draft) return;
  const D = dur(), W = Math.max(400, D * C.pxs), c = clip(), tr = c.tracks || {};
  $s("cstTlIn").style.width = `${W + 140}px`;
  const step = C.pxs >= 60 ? 1 : C.pxs >= 24 ? 2 : C.pxs >= 12 ? 5 : 10;
  let ruler = "";
  for (let t = 0; t <= D + 0.001; t += step) ruler += `<span style="left:${120 + t * C.pxs}px">${clock(t).replace(/\.0$/, "")}</span>`;
  $s("cstRuler").innerHTML = ruler;
  const blk = (s, e, cls, label, attrs = "") => `<span class="cst-blk ${cls}" style="left:${(s * C.pxs).toFixed(1)}px;width:${Math.max(cls.startsWith("fx") ? 56 : 6, (e - s) * C.pxs).toFixed(1)}px" ${attrs} title="${E(label)}">${E(label)}</span>`;
  const lane = (key, label, inner, note) => `<div class="cst-lane" data-track="${key}" data-testid="cst-track-${key}"><span class="cst-ll">${label}</span><div class="cst-lb">${inner || `<span class="cst-none">${note || ""}</span>`}</div></div>`;
  const d = C.draft;
  // hook
  let hook = "";
  if (tr.cold_open_s) hook += blk(tr.cover_s || 0.2, (tr.cover_s || 0.2) + tr.cold_open_s, "hook", "Cold open");
  if (d.hook_fx === "headline") hook += blk(lead(), lead() + 2.8, `hook${changed("hook_fx") || changed("hook") ? " draft" : ""}`, "Headline", `data-sel="el:headline" data-el="headline" data-testid="cst-blk-headline"`);
  if (d.hook_fx === "behind") hook += blk(0.2, 2.8, `hook${changed("hook_fx") ? " draft" : ""}`, `Behind: ${behindWord()}`, `data-sel="el:behind" data-el="behind" data-testid="cst-blk-behind"`);
  // words on their real time
  const words = C.words.map((w, k) => {
    const s = tOf(w.s), e = tOf(w.e), cut = w.s < d.start - 0.01 || w.e > d.end + 0.01;
    return `<span class="cst-wt${cut ? " cut" : ""}${C.wsel && k >= C.wsel[0] && k <= C.wsel[1] ? " sel" : ""}" id="cst-wt-${k}" data-k="${k}" style="left:${(s * C.pxs).toFixed(1)}px;width:${Math.max(4, (e - s) * C.pxs - 1).toFixed(1)}px" title="${E(w.w)}">${C.pxs >= 30 ? E(w.w) : ""}</span>`;
  }).join("");
  const caps = blk(lead(), D - 0.1, `cap${changed("caption_preset") ? " draft" : ""}`, `Captions · ${(d.caption_preset || "").replace(/_/g, " ")}`, `data-sel="el:captions" data-el="captions" data-testid="cst-blk-captions"`);
  const fx = fxSpans().map((f) => blk(f.s, f.e, `fx${f.draft ? " draft" : ""}`, `${f.kind === "stat" ? "Counter" : f.kind === "line" ? "Line" : f.kind}: ${f.label}`,
    `data-sel="fx:${f.k}" data-fx="${f.k}" data-testid="cst-blk-fx"`)).join("");
  const shots = (tr.shots || []).map((x) => blk(x.s, x.e, "shot", { track: "Speaker", fit: "Whole frame", split: "Split", screen: "Screen", content: "Screen" }[x.mode] || x.mode || "Shot")).join("");
  const wave = C.wave[c.id];
  const audio = wave ? `<canvas class="cst-wave" id="cstWave" data-testid="cst-waveform" width="${Math.round(W)}" height="36"></canvas>` : "";
  const brl = (d.broll || []).map((b, k) => blk(b.s, b.e, `fx${changed("broll") ? " draft" : ""}`, `B-roll: ${b.query || "footage"}`, `data-sel="br:${k}" data-br="${k}" data-testid="cst-blk-broll"`)).join("");
  $s("cstTracks").innerHTML = lane("hook", "Hook", hook, "No hook graphic")
    + lane("words", "Words", words, "No words saved")
    + lane("captions", "Captions", caps)
    + lane("graphics", "Graphics", fx, Array.isArray(d.fx) ? "None (your choice)" : "None in the last render")
    + lane("broll", "B-roll", brl, "None. Ask the director: “add b-roll of …”")
    + lane("layout", "Framing", shots, "Not recorded for this render")
    + lane("audio", "Audio", audio, C.wave[c.id] === null ? "Waveform unavailable" : "Reading the audio…");
  if (wave) paintWave(wave, W);
  $s("cstTracks").querySelectorAll("[data-el]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); select({ el: b.dataset.el }); }));
  $s("cstTracks").querySelectorAll("[data-br]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const k = +b.dataset.br; select({ broll: k }); seek(d.broll[k].s); }));
  $s("cstTracks").querySelectorAll("[data-fx]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const k = +b.dataset.fx; select({ fx: k >= 0 ? k : null, auto: k < 0 }); seek(fxSpans().find((f) => f.k === k).s); }));
  $s("cstTracks").querySelectorAll(".cst-wt").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); const k = +b.dataset.k; if (e.shiftKey && C.wsel) selWords(Math.min(C.wsel[0], k), Math.max(C.wsel[1], k)); else { selWords(k, k); seek(tOf(C.words[k].s)); } };
  });
  if (C.sel) document.querySelectorAll(`#cstTracks [data-sel="${selKey(C.sel)}"]`).forEach((x) => x.classList.add("sel"));
  $s("cstTlHint").textContent = approx() ? "Pauses were cut from this clip, so word positions are close, not exact." : "";
  tick();
}
$s("cstZoom").oninput = () => { C.pxs = +$s("cstZoom").value; drawTimeline(); };
function fitTl() { const w = $s("cstTlScroll").clientWidth - 136; C.pxs = Math.max(4, Math.min(160, w / Math.max(1, dur()))); $s("cstZoom").value = Math.round(C.pxs); drawTimeline(); }
$s("cstFit").onclick = fitTl;
// click or drag on the ruler / empty lanes to scrub
(() => {
  const area = $s("cstTlIn");
  const at = (ev) => (ev.clientX - area.getBoundingClientRect().left - 120) / C.pxs;
  area.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".cst-blk, .cst-wt")) return;
    if (e.clientX - area.getBoundingClientRect().left < 120) return;
    area.setPointerCapture(e.pointerId); seek(at(e));
    const mv = (ev) => seek(at(ev));
    area.addEventListener("pointermove", mv);
    area.addEventListener("pointerup", () => area.removeEventListener("pointermove", mv), { once: true });
  });
})();
// the waveform: the clip's own audio, read in the browser (no server work); unavailable says so
async function loadWave(c) {
  if (c.id in C.wave) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) throw new Error("no audio");
    if (new URL(c.url, location.href).origin !== location.origin) throw new Error("other origin");
    const buf = await (await fetch(c.url)).arrayBuffer();
    const ctx = new AC(); const au = await ctx.decodeAudioData(buf); ctx.close && ctx.close();
    const ch = au.getChannelData(0), n = 2000, step = Math.floor(ch.length / n) || 1, peaks = new Float32Array(n);
    for (let i = 0; i < n; i++) { let m = 0; for (let j = i * step, z = Math.min(ch.length, j + step); j < z; j += 8) m = Math.max(m, Math.abs(ch[j])); peaks[i] = m; }
    C.wave[c.id] = { peaks, dur: au.duration };
  } catch (e) { C.wave[c.id] = null; }
  if (clip() && clip().id === c.id) drawTimeline();
}
function paintWave(w, W) {
  const cv = $s("cstWave"); if (!cv) return;
  const g = cv.getContext("2d"), H = cv.height, n = w.peaks.length, px = (w.dur * C.pxs) / n;
  g.clearRect(0, 0, cv.width, H);
  g.fillStyle = getComputedStyle(cv).color || "#888";
  for (let i = 0; i < n; i++) { const h = Math.max(1, w.peaks[i] * (H - 4)); g.fillRect(i * px, (H - h) / 2, Math.max(1, px - 0.5), h); }
  void W;
}

// ------------------------------------------------------------------------------------------------ resizable panels
function splitter(id, prop, min, max, sign) {
  const sp = $s(id), root = $s("cstudio");
  sp.onpointerdown = (e) => {
    sp.setPointerCapture(e.pointerId);
    const start = sign.horizontal ? e.clientY : e.clientX, cur = parseFloat(getComputedStyle(root).getPropertyValue(prop)) || 0;
    sp.onpointermove = (ev) => { const d = ((sign.horizontal ? ev.clientY : ev.clientX) - start) * sign.dir; root.style.setProperty(prop, `${Math.max(min, Math.min(max, cur + d))}px`); };
    sp.onpointerup = () => { sp.onpointermove = null; drawLayers(); };
  };
  sp.onkeydown = (e) => {
    const k = sign.horizontal ? { ArrowUp: -1, ArrowDown: 1 } : { ArrowLeft: -1, ArrowRight: 1 };
    if (!(e.key in k)) return; e.preventDefault();
    const cur = parseFloat(getComputedStyle(root).getPropertyValue(prop)) || 0;
    root.style.setProperty(prop, `${Math.max(min, Math.min(max, cur + k[e.key] * 16 * sign.dir))}px`);
  };
}
splitter("cstSplitL", "--cst-left", 0, 420, { dir: 1 });
splitter("cstSplitR", "--cst-right", 280, 520, { dir: -1 });
splitter("cstSplitB", "--cst-tl", 120, 420, { dir: -1, horizontal: true });

// ------------------------------------------------------------------------------------------------ entry points
// the rail's Clip studio: the latest take with clips, its first clip
$s("railStudio").onclick = async () => {
  try {
    if (typeof T !== "undefined" && T.take && (T.take.clips || []).some((c) => c.url)) return openStudio(T.take, (T.take.clips.find((c) => c.url) || {}).id);
    const rows = await api("/api/takes");
    const r = (rows || []).find((x) => ["ready", "rendering"].includes(x.status));
    if (!r) { showTab("take"); toast("Make a take first: Clip studio opens on its clips.", 6000); return; }
    const t = await api(`/api/takes/${encodeURIComponent(r.id)}`);
    showTab("take"); if (typeof tkOpen === "function") await tkOpen(t.id, { scroll: false });
    openStudio((typeof T !== "undefined" && T.take) || t, (t.clips || []).find((c) => c.url)?.id);
  } catch (e) { toast(e.message, 6000); }
};
// ?take=<id>&studio=<clip> opens straight into the studio once the take is on screen
window.addEventListener("load", () => {
  const want = new URLSearchParams(location.search).get("studio"); if (!want) return;
  let n = 0; const wait = () => {
    if (typeof T !== "undefined" && T.take && (T.take.clips || []).some((c) => c.id === want && c.url)) return openStudio(T.take, want);
    if (++n < 40) setTimeout(wait, 500);
  };
  setTimeout(wait, 600);
});
window.addEventListener("resize", () => { if (C.open) drawLayers(); });
})();
