// One take > Trim & caption: one window per clip (the same edits as REST PATCH /api/takes/{id}/clips/{clip} and the MCP
// tool adjust_clip). Left: the clip in 9:16 with play, CC (the captions as you've edited them) and clip x / N. Right: tabs
// Trim (In/Out with half-second nudges, a filmstrip with handles to drag), Transcript (fix words: the burned-in captions
// follow; the cover hook), Caption (one per channel of this take, counted against its network's limit, AI rewrite) and,
// when the take has an Instagram channel, Music (Instagram's catalog through Buffer). Save sends one PATCH: timing,
// transcript and hook re-render the clip on the video worker; captions and music save at once. Esc closes.
"use strict";
(() => {
const LIM = (typeof LIMITS !== "undefined" && LIMITS) || { twitter: 280, linkedin: 3000, instagram: 2196, facebook: 5000, threads: 500, bluesky: 300, tiktok: 2200, youtube: 5000 };
const MIN_S = 3, MAX_S = 90, NUDGE = 0.5, FRAMES = 10, PAD = 8;
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9']+/g, " ").trim();
const clock = (s) => { s = Math.max(0, +s || 0); const m = Math.floor(s / 60); return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };
const stamp = (s) => { s = Math.max(0, +s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = (s % 60).toFixed(1).padStart(4, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`; };
const count = (svc, t) => [...(svc === "twitter" || svc === "x" ? String(t).replace(/https?:\/\/\S+/g, "x".repeat(23)) : String(t))].length;
const $c = (id) => document.getElementById(id);
let CE = null;

function take() { return typeof T !== "undefined" ? T.take : null; }
function clipOf(id) { return ((take() || {}).clips || []).find((c) => c.id === id); }
function lead(t) { return t.style === "opus" || t.sample ? 0 : 0.5; }  // the classic render opens on a 0.5 s cover frame
function canCut(t) { return !!t.can_rerender; }
function services(t) { return [...new Set(((t.input || {}).channels || []).map((x) => x.service))]; }
function igChannel(t) { return ((t.input || {}).channels || []).find((x) => x.service === "instagram"); }
function range(c) { return { lo: Math.max(c.trim_min ?? 0, c.start - PAD), hi: Math.min(c.trim_max ?? c.end + PAD, c.end + PAD) }; }

window.openClipEditor = function (id) {
  const t = take(), c = clipOf(id); if (!t || !c) return;
  CE = { id, t, c, s: c.start, e: c.end, text: c.transcript || "", hook: c.hook || "", caps: { ...(c.post_captions || {}) },
         svc: services(t)[0], music: c.music || null, musicTouched: false, picker: null, gen: (CE ? CE.gen : 0) + 1, cc: CE ? CE.cc : false };
  const dlg = $c("clipDialog");
  const list = t.clips || [], n = list.findIndex((x) => x.id === id) + 1;
  $c("ceNum").textContent = `${n} / ${list.length}`;
  $c("cePrevClip").disabled = n <= 1; $c("ceNextClip").disabled = n >= list.length;
  $c("ceTitle").textContent = `Trim & caption · clip ${n}`;
  const v = $c("cePlayer");
  v.src = c.url ? c.url + (c.rendered_at ? (c.url.includes("?") ? "&" : "?") + "v=" + Math.round(c.rendered_at) : "") : "";
  v.poster = c.cover_url || "";
  v.muted = false;
  // timing
  const cut = canCut(t);
  ["ceHIn", "ceHOut"].forEach((h) => ($c(h).disabled = !cut));
  document.querySelectorAll("#clipDialog [data-nudge]").forEach((b) => (b.disabled = !cut));
  $c("ceText").readOnly = !cut; $c("ceHook").readOnly = !cut;
  $c("ceTrimNote").textContent = !cut
    ? (t.sample ? "The view-only sample can't re-render, so its timing, transcript and cover stay as they are. Sign in to trim it; caption edits work here."
       : "This server has no video worker, so only captions and music can change.")
    : t.sample ? "The sample's full talk isn't hosted, so its clips trim inside their moment. Saving re-renders the clip on the video worker."
    : "Drag the handles or nudge In and Out. Saving re-renders this clip on the video worker (a minute or two).";
  // transcript + hook
  $c("ceText").value = CE.text; $c("ceHook").value = CE.hook;
  // captions
  const svcs = services(t);
  $c("ceSvcs").innerHTML = svcs.map((s) => `<button type="button" class="chip" data-svc="${esc(s)}" data-testid="clip-caption-service" aria-pressed="false">${typeof netMark === "function" ? netMark(s) : ""}<span>${esc(netName(s))}</span></button>`).join("");
  $c("ceSvcs").querySelectorAll("[data-svc]").forEach((b) => (b.onclick = () => pickSvc(b.dataset.svc)));
  pickSvc(CE.svc);
  // music: only for takes with an Instagram channel; the picker loads when the tab is opened (one Buffer search)
  const ig = igChannel(t);
  $c("ceTabMusic").hidden = !ig;
  $c("ceMusic").innerHTML = ""; $c("ceMusic").className = "";
  // product card overlay (Commerce): classic One take clips only; text on the brand kit
  const pc = c.product_card || null;
  CE.prod = pc ? { ...pc } : null;
  $c("ceTabProd").hidden = t.style === "opus" || !cut;
  $c("ceProdOn").checked = !!pc;
  $c("ceProdName").value = (pc && pc.name) || ""; $c("ceProdPrice").value = (pc && pc.price) || "";
  $c("ceProdCta").value = pc ? pc.cta || "" : "Shop now"; $c("ceProdLabel").value = pc ? pc.link_label || "" : "Link in bio";  // defaults only for a new card
  $c("ceProdPos").value = (pc && pc.position) || "bottom";
  $c("ceProdFields").hidden = !pc;
  $c("ceErr").textContent = "";
  tab("trim");
  paintTrim(); paintDirty(); frames();
  if (!dlg.open) { if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", ""); }
  $c("ceTabTrim").focus({ preventScroll: true });
};

// ------------------------------------------------------------------ tabs
function tab(name) {
  document.querySelectorAll("#clipDialog [data-cetab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.cetab === name));
  ({ trim: "cePaneTrim", text: "cePaneText", cap: "cePaneCap", music: "cePaneMusic", prod: "cePaneProd" }[name] && Object.entries({ trim: "cePaneTrim", text: "cePaneText", cap: "cePaneCap", music: "cePaneMusic", prod: "cePaneProd" })
    .forEach(([k, id]) => ($c(id).hidden = k !== name)));
  if (name === "music" && CE && !CE.picker) {
    const ig = igChannel(CE.t);
    CE.picker = HQMusic.mount($c("ceMusic"), { channelId: ig.id, value: CE.music,
      onChange: (v) => { CE.music = v; CE.musicTouched = true; paintDirty(); } });
    CE.picker.search("");
  }
}
document.querySelectorAll("#clipDialog [data-cetab]").forEach((b) => (b.onclick = () => tab(b.dataset.cetab)));
$c("clipDialog").querySelector(".ce-tabs").addEventListener("keydown", (e) => {  // arrow keys move between tabs
  if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const tabs = [...document.querySelectorAll("#clipDialog [data-cetab]")].filter((b) => !b.hidden);
  const i = tabs.indexOf(document.activeElement); if (i < 0) return;
  const nx = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]; nx.focus(); tab(nx.dataset.cetab); e.preventDefault();
});

// ------------------------------------------------------------------ trim
function clampTrim(edge, val) {
  const { c } = CE, lo = c.trim_min ?? 0, hi = c.trim_max ?? Infinity;
  val = Math.round(val * 10) / 10;
  if (edge === "s") { val = Math.max(lo, Math.min(val, CE.e - MIN_S)); val = Math.max(val, CE.e - MAX_S); CE.s = val; }
  else { val = Math.min(hi, Math.max(val, CE.s + MIN_S)); val = Math.min(val, CE.s + MAX_S); CE.e = val; }
}
function paintTrim() {
  if (!CE) return;
  const { c } = CE, { lo, hi } = range(c), span = Math.max(0.1, hi - lo);
  $c("ceIn").textContent = stamp(CE.s); $c("ceOut").textContent = stamp(CE.e);
  $c("ceLen").textContent = `${(CE.e - CE.s).toFixed(1)} s`;
  const a = ((CE.s - lo) / span) * 100, b = ((CE.e - lo) / span) * 100;
  $c("ceHIn").style.left = a + "%"; $c("ceHOut").style.left = b + "%";
  $c("ceSel").style.left = a + "%"; $c("ceSel").style.width = (b - a) + "%";
  for (const [h, v] of [["ceHIn", CE.s], ["ceHOut", CE.e]]) {
    const el = $c(h); el.setAttribute("aria-valuemin", lo.toFixed(1)); el.setAttribute("aria-valuemax", hi.toFixed(1));
    el.setAttribute("aria-valuenow", v.toFixed(1)); el.setAttribute("aria-valuetext", stamp(v));
  }
  $c("ceLo").textContent = stamp(CE.s); $c("ceHi").textContent = stamp(CE.e);
  const beyond = CE.s < c.start - 0.05 || CE.e > c.end + 0.05;
  $c("cePrevNote").textContent = beyond ? "The preview plays the current cut. The new part shows after you save." : "";
  paintTime(); paintDirty();
}
document.querySelectorAll("#clipDialog [data-nudge]").forEach((b) => (b.onclick = () => {
  if (!CE) return; const [edge, d] = b.dataset.nudge.split(":"); clampTrim(edge, (edge === "s" ? CE.s : CE.e) + +d); paintTrim(); seekToIn(edge);
}));
function dragHandle(el, edge) {
  el.addEventListener("pointerdown", (ev) => {
    if (el.disabled || !CE) return;
    el.setPointerCapture(ev.pointerId); el.classList.add("drag");
    const move = (e) => { const r = $c("ceStrip").getBoundingClientRect(), { lo, hi } = range(CE.c);
      clampTrim(edge, lo + Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (hi - lo)); paintTrim(); };
    const up = () => { el.classList.remove("drag"); el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); seekToIn(edge); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); ev.preventDefault();
  });
  el.addEventListener("keydown", (e) => {
    if (!CE || el.disabled) return;
    const d = { ArrowLeft: -0.1, ArrowRight: 0.1, ArrowDown: -0.1, ArrowUp: 0.1, PageDown: -1, PageUp: 1 }[e.key];
    if (d == null) return;
    clampTrim(edge, (edge === "s" ? CE.s : CE.e) + d * (e.shiftKey ? 5 : 1)); paintTrim(); e.preventDefault();
  });
}
dragHandle($c("ceHIn"), "s"); dragHandle($c("ceHOut"), "e");

// filmstrip: frames of the rendered clip where it covers the range; the rest of the range (outside today's cut) is shaded
async function frames() {
  const box = $c("ceFrames"), my = CE.gen, { c, t } = CE, { lo, hi } = range(c);
  box.innerHTML = Array.from({ length: FRAMES }, () => `<span class="ce-fr"></span>`).join("");
  if (!c.url) return;
  const v = document.createElement("video"); v.muted = true; v.preload = "auto"; v.crossOrigin = "anonymous"; v.src = c.url;
  try { await new Promise((ok, no) => { v.onloadeddata = ok; v.onerror = no; setTimeout(no, 8000); }); } catch (_) { return; }
  const cv = document.createElement("canvas"); cv.width = 54; cv.height = 96; const g = cv.getContext("2d");
  const cells = [...box.children];
  for (let i = 0; i < FRAMES; i++) {
    if (!CE || CE.gen !== my) return;
    const ts = lo + ((i + 0.5) / FRAMES) * (hi - lo);
    if (ts < c.start || ts > c.end) { cells[i].classList.add("out"); continue; }
    v.currentTime = Math.min(v.duration - 0.1, ts - c.start + lead(t));
    try { await new Promise((ok, no) => { v.onseeked = ok; setTimeout(no, 3000); }); } catch (_) { continue; }
    try { g.drawImage(v, 0, 0, cv.width, cv.height); cells[i].style.backgroundImage = `url(${cv.toDataURL("image/jpeg", 0.6)})`; } catch (_) { return; }
  }
  v.removeAttribute("src"); v.load();
}

// ------------------------------------------------------------------ preview: plays the chosen In..Out of the current cut
const player = $c("cePlayer");
function inAt() { return Math.max(0, Math.max(CE.s, CE.c.start) - CE.c.start + lead(CE.t)); }
function outAt() { return Math.max(inAt() + 0.2, Math.min(CE.e, CE.c.end) - CE.c.start + lead(CE.t)); }
function seekToIn(edge) { if (!CE || !player.src) return; player.currentTime = edge === "e" ? Math.max(inAt(), outAt() - 2) : inAt(); }
function paintTime() {
  if (!CE) return;
  const cur = Math.max(0, player.currentTime - inAt()), tot = CE.e - CE.s;
  $c("ceTime").textContent = `${clock(cur)} / ${clock(tot)}`;
  $c("ceBar").style.width = `${Math.min(100, (cur / Math.max(0.1, outAt() - inAt())) * 100)}%`;
  paintCc();
}
$c("cePlay").onclick = () => {
  if (!CE || !player.src) return;
  if (player.paused) { if (player.currentTime < inAt() - 0.05 || player.currentTime >= outAt() - 0.05) player.currentTime = inAt(); player.play().catch(() => {}); }
  else player.pause();
};
player.addEventListener("play", () => { $c("cePlay").setAttribute("aria-label", "Pause"); $c("cePlay").innerHTML = `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>`; });
player.addEventListener("pause", () => { $c("cePlay").setAttribute("aria-label", "Play"); $c("cePlay").innerHTML = `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5l12 7-12 7z"/></svg>`; });
player.addEventListener("timeupdate", () => { if (!CE) return; if (!player.paused && player.currentTime >= outAt()) { player.pause(); player.currentTime = inAt(); } paintTime(); });
player.addEventListener("loadedmetadata", () => { if (CE) { player.currentTime = inAt(); paintTime(); } });
// CC: the words as edited, timed to the voice (an edited transcript is spread over the clip's words in order)
function ccWords() {
  const ws = (CE.c.words || []).filter((w) => w.s >= CE.c.start - 0.05 && w.e <= CE.c.end + 0.05);
  const toks = CE.text.split(/\s+/).filter(Boolean);
  if (!ws.length || !toks.length) return [];
  if (toks.length === ws.length) return ws.map((w, i) => ({ ...w, w: toks[i] }));
  const s0 = ws[0].s, e0 = ws[ws.length - 1].e, step = (e0 - s0) / toks.length;
  return toks.map((w, i) => ({ w, s: s0 + i * step, e: s0 + (i + 1) * step }));
}
function paintCc() {
  const el = $c("ceCcText"); el.hidden = !CE.cc; if (!CE.cc) return;
  const at = CE.c.start + player.currentTime - lead(CE.t), ws = ccWords();
  const i = ws.findIndex((w) => w.e >= at);
  el.textContent = i < 0 ? "" : ws.slice(Math.max(0, i - 1), i + 3).map((w) => w.w).join(" ");
}
$c("ceCc").onclick = () => { if (!CE) return; CE.cc = !CE.cc; $c("ceCc").setAttribute("aria-pressed", CE.cc); paintCc(); };
$c("cePrevClip").onclick = () => step(-1);
$c("ceNextClip").onclick = () => step(1);
async function step(d) {
  if (!CE) return; const list = CE.t.clips || [], i = list.findIndex((x) => x.id === CE.id) + d;
  if (i < 0 || i >= list.length) return;
  if (changes().length && !(await hqConfirm("Leave this clip without saving your changes?", { ok: "Leave clip", danger: true }))) return;
  player.pause(); window.openClipEditor(list[i].id);
}

// ------------------------------------------------------------------ transcript + hook
$c("ceText").addEventListener("input", () => { if (!CE) return; CE.text = $c("ceText").value; paintHook(); paintDirty(); paintCc(); });
$c("ceHook").addEventListener("input", () => { if (!CE) return; CE.hook = $c("ceHook").value; paintHook(); paintDirty(); });
function hookOk() { const q = norm(CE.hook.replace(/…$/, "")); return q.split(" ").length >= 3 && ` ${norm(CE.text)} `.includes(` ${q} `); }
function paintHook() {
  const ok = hookOk(), help = $c("ceHookHelp");
  help.textContent = ok ? "At least 3 words, quoted from the transcript." : "Use a line from the transcript (at least 3 words in a row).";
  help.classList.toggle("st-err", !ok); $c("ceHook").setAttribute("aria-invalid", !ok);
}

// ------------------------------------------------------------------ captions, one per channel network
function pickSvc(svc) {
  if (!CE) return; CE.svc = svc;
  $c("ceSvcs").querySelectorAll("[data-svc]").forEach((b) => { const on = b.dataset.svc === svc; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  $c("ceCaption").value = CE.caps[svc] || ""; paintCount();
}
function limit(svc) { return (CE.t.caption_limits || {})[svc] || LIM[svc] || 2000; }
function paintCount() {
  const svc = CE.svc, n = count(svc, $c("ceCaption").value), lim = limit(svc), el = $c("ceCount");
  el.textContent = n > lim ? `Too long: ${n.toLocaleString()} / ${lim.toLocaleString()}` : `${n.toLocaleString()} / ${lim.toLocaleString()}`;
  el.className = "ce-count" + (n > lim ? " over" : n > lim * 0.9 ? " near" : "");
  const bar = $c("ceLenBar"); bar.style.width = Math.min(100, (n / lim) * 100) + "%"; bar.className = n > lim ? "over" : n > lim * 0.9 ? "near" : "";
}
$c("ceCaption").addEventListener("input", () => { if (!CE) return; CE.caps[CE.svc] = $c("ceCaption").value; paintCount(); paintDirty(); });
$c("ceRewrite").onclick = async () => {
  if (!CE) return; const b = $c("ceRewrite"), svc = CE.svc; b.disabled = true; b.textContent = "Rewriting…";
  try {
    const r = await api(`/api/takes/${CE.t.id}/clips/${encodeURIComponent(CE.id)}/rewrite`, { method: "POST", body: { service: svc, text: CE.caps[svc] || "" } });
    if (CE && CE.svc === svc) { CE.caps[svc] = r.caption; $c("ceCaption").value = r.caption; paintCount(); paintDirty(); }
    toast(`Rewritten${r.engine ? " by " + r.engine : ""}. Review it, then save.`, 4000);
  } catch (e) { toast(e.message, 6000); }
  finally { b.disabled = false; b.textContent = "Rewrite with AI"; }
};

// ------------------------------------------------------------------ product card
function prodValue() {
  if (!$c("ceProdOn").checked) return null;
  return { name: $c("ceProdName").value.trim(), price: $c("ceProdPrice").value.trim(), cta: $c("ceProdCta").value.trim(),
           link_label: $c("ceProdLabel").value.trim(), position: $c("ceProdPos").value, code: (CE.prod && CE.prod.code) || "" };
}
$c("ceProdOn").addEventListener("change", () => { $c("ceProdFields").hidden = !$c("ceProdOn").checked; paintDirty(); });
["ceProdName", "ceProdPrice", "ceProdCta", "ceProdLabel", "ceProdPos"].forEach((id) => $c(id).addEventListener("input", () => paintDirty()));

// ------------------------------------------------------------------ what changed, and save
function changes() {
  if (!CE) return [];
  const { c } = CE, out = [];
  if (Math.abs(CE.s - c.start) > 0.04 || Math.abs(CE.e - c.end) > 0.04) out.push("timing");
  if (CE.text.trim().replace(/\s+/g, " ") !== (c.transcript || "").trim()) out.push("transcript");
  if (CE.hook.trim() !== (c.hook || "")) out.push("cover hook");
  if (Object.keys(CE.caps).some((s) => (CE.caps[s] || "") !== ((c.post_captions || {})[s] || ""))) out.push("captions");
  if (CE.musicTouched && JSON.stringify(CE.music || null) !== JSON.stringify(c.music || null)) out.push("music");
  const pv = prodValue(), was = c.product_card || null;
  if (JSON.stringify(pv) !== JSON.stringify(was ? { name: was.name, price: was.price, cta: was.cta, link_label: was.link_label, position: was.position, code: was.code || "" } : null)) out.push("product card");
  return out;
}
function paintDirty() {
  const ch = changes(), re = ch.some((x) => ["timing", "transcript", "cover hook", "product card"].includes(x));
  $c("ceDirty").textContent = ch.length ? `Unsaved: ${ch.join(", ")}${re ? ". Saving re-renders this clip." : "."}` : "No changes yet.";
  $c("ceSave").disabled = !ch.length;
}
function close() { player.pause(); if (CE && CE.picker) CE.picker.stop(); const d = $c("clipDialog"); if (d.open && typeof d.close === "function") d.close(); else d.removeAttribute("open"); }
$c("ceCancel").onclick = close; $c("ceClose").onclick = close;
$c("clipDialog").addEventListener("close", () => { player.pause(); if (CE && CE.picker) CE.picker.stop(); CE = null; if (typeof T !== "undefined") T.edit = null; });
$c("ceForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!CE) return;
  const { c, t } = CE, ch = changes(); if (!ch.length) return close();
  const body = {};
  if (ch.includes("timing")) { body.start_ms = Math.round(CE.s * 1000); body.end_ms = Math.round(CE.e * 1000); }
  if (ch.includes("transcript")) body.transcript = CE.text;
  if (ch.includes("cover hook")) {
    if (!hookOk()) { tab("text"); $c("ceErr").textContent = "The cover hook has to be a line from the transcript (at least 3 words in a row)."; return; }
    body.hook = CE.hook.trim();
  }
  if (ch.includes("captions")) body.captions = Object.fromEntries(Object.keys(CE.caps).filter((s) => (CE.caps[s] || "") !== ((c.post_captions || {})[s] || "")).map((s) => [s, CE.caps[s]]));
  if (ch.includes("product card")) {
    const pv = prodValue();
    if (pv && !pv.name) { tab("prod"); $c("ceErr").textContent = "Give the product a name, or turn the product card off."; return; }
    body.product_card = pv;
  }
  if (ch.includes("music")) { body.music = CE.music; if (CE.music) { body.music_volume = CE.music.music_volume; body.original_volume = CE.music.original_volume; } }
  const btn = $c("ceSave"); btn.disabled = true; $c("ceErr").textContent = ""; btn.innerHTML = `<span class="spin" aria-hidden="true"></span>Saving…`;
  try {
    const out = await api(`/api/takes/${t.id}/clips/${encodeURIComponent(CE.id)}`, { method: "PATCH", body });
    const n = (t.clips || []).findIndex((x) => x.id === CE.id) + 1;
    close(); showTake(out);
    toast(out.rerendering ? `Re-rendering clip ${n}. It gets its new video in a minute or two.` : ch.length === 1 && ch[0] === "captions" ? "Caption saved" : "Saved", 5000);
    if (["running", "rendering"].includes(out.status)) watchTake(out.id);
  } catch (e) { $c("ceErr").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Save changes"; }
});
window.ceRefresh = function (t) { if (CE && CE.t.id === t.id) CE.t = t; };
})();
