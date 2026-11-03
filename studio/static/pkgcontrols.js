// Make a post package controls (studio/post_controls.py; UI = REST = MCP), owner bug list of 3 Nov 2026:
//  * "Changed since saved to Buffer · Update draft" when a package's media changed after it went to Buffer
//    (update_package_draft: the same Buffer draft takes the new media; a scheduled post is never changed here)
//  * Undo / "Back to the Reel" (undo_package_media: every media change keeps the version before it)
//  * Music: None (the default, HuhQueue never adds music) | Instagram music (a Reel; the picker in reelmusic.js).
//    A carousel says where music is added (the Instagram app: Buffer's API takes music on Reels only).
//  * the agent's one-question cards with buttons (result.ask: Keep Reel / Switch, which brand kit, which track)
//  * "All templates…": every built-in and custom template with its first slide, in the run's kit
"use strict";
(() => {
const E = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const VID = /\.(mp4|mov|m4v|webm)(\?|$)/i;
const isReel = (p) => p.service === "instagram" && p.format === "reel" && (p.media_urls || []).some((u) => VID.test(u));
const refresh = async (r) => { const n = await api(`/api/agent/runs/${r.id}`); showRun(n); if (["running", "rendering"].includes(n.status) && window.watch) watch(n.id); return n; };

// ---------------------------------------------------------------- package card
function changed(p) {
  const c = p.changed_since_sent;
  if (!c) return "";
  const draft = p.buffer_draft && p.buffer_draft.buffer_post_id;
  return `<div class="st warn pc-changed" role="status" data-testid="pkg-changed">
    <span><b>Changed since ${c.mode === "draft" ? "saved to Buffer" : "sent to Buffer"}</b>${draft ? "" :
      ` <small class="muted">The post in Buffer still has the old ${c.format === "reel" ? "video" : "media"}. Send this version, or change it in Buffer.</small>`}</span>
    ${draft ? `<button type="button" class="btn sm" data-testid="pkg-update-draft">Update draft</button>` : ""}</div>`;
}
function undo(p, sent) {
  if (!p.undo || sent) return "";
  return `<button type="button" class="btn2 sm pc-undo" data-testid="pkg-undo" title="Go back to the previous version of this post">↶ ${E(p.undo.label || "Undo")}</button>`;
}
function music(p, sent) {
  if (p.service !== "instagram") return "";
  if (!isReel(p)) {
    return `<div class="pc-music" data-testid="pkg-music"><small class="muted sm">Music</small>
      <p class="muted sm pc-note" data-testid="music-carousel-note">Add music to a carousel in the Instagram app: Buffer can put Instagram music on Reels only.</p></div>`;
  }
  const au = (p.options || {}).ig_audio;
  const seg = sent ? `<span class="muted sm">${au ? "" : "None"}</span>` : `<div class="pc-seg" role="radiogroup" aria-label="Music for this Reel" data-testid="music-seg">
      <button type="button" role="radio" aria-checked="${!au}" class="${au ? "" : "on"}" data-testid="music-off">None</button>
      <button type="button" role="radio" aria-checked="${!!au}" class="${au ? "on" : ""}" data-testid="music-add">Instagram music</button></div>`;
  const chip = au && window.HQMusicChip ? window.HQMusicChip(au, { actions: !sent }) : "";
  return `<div class="pc-music pkg-music" data-testid="pkg-music"><small class="muted sm">Music</small>${seg}${chip}
    ${au ? "" : `<small class="muted xs">Nothing is added unless you pick it.</small>`}</div>`;
}
function card(p, sent) { return changed(p) + music(p, sent) + (undo(p, sent) ? `<div class="pc-row">${undo(p, sent)}</div>` : ""); }

function wire(el, r, pk) {
  const up = el.querySelector("[data-testid=pkg-update-draft]");
  if (up) up.onclick = async () => {
    up.disabled = true; up.textContent = "Updating…";
    try { const out = await api(`/api/agent/runs/${r.id}/packages/${encodeURIComponent(pk.channel_id)}/update-draft`, { method: "POST", body: {} });
      toast(out.draft === "recreated" ? "That draft was gone from Buffer, so a new draft was saved" : "Buffer draft updated with the new media", 4500); await refresh(r); }
    catch (e) { toast(e.message, 6000); up.disabled = false; up.textContent = "Update draft"; }
  };
  const un = el.querySelector("[data-testid=pkg-undo]");
  if (un) un.onclick = async () => {
    un.disabled = true;
    try { showRun(await api(`/api/agent/runs/${r.id}/undo`, { method: "POST", body: { channel: pk.channel_id } })); toast("Back to the previous version"); }
    catch (e) { toast(e.message, 6000); un.disabled = false; }
  };
  const none = el.querySelector("[data-testid=music-off]"), ig = el.querySelector("[data-testid=music-add]");
  if (none) none.onclick = async () => {
    if (none.classList.contains("on")) return;
    none.disabled = true;
    try { await api(`/api/agent/runs/${r.id}/music`, { method: "POST", body: { channel: pk.channel_id, music: "none" } }); toast("No music on this Reel"); await refresh(r); }
    catch (e) { toast(e.message, 6000); none.disabled = false; }
  };
  if (ig) ig.onclick = () => window.openMusicPicker && window.openMusicPicker(r, pk);
  if (window.wireMusic) window.wireMusic(el, r, pk);  // the chip's Change / Remove (reelmusic.js)
}

// ---------------------------------------------------------------- the agent's question with buttons (result.ask)
function askCard(res, busy) {
  const a = res && res.ask;
  if (!a || busy || !(a.choices || []).length) return "";
  return `<div class="st warn ask-card pc-ask" role="group" aria-label="${E(a.question)}" data-testid="ask-card" data-kind="${E(a.kind)}">
    <span>${E(a.question)}</span><div class="row">${a.choices.map((c, i) => `<button type="button" class="${i === a.choices.length - 1 && a.kind === "reel_to_carousel" ? "btn" : "btn2"} sm" data-testid="ask-choice" data-i="${i}">${E(c.label)}</button>`).join("")}</div></div>`;
}
askCard.wire = (box, r) => {
  const a = (r.result || {}).ask;
  if (!a) return;
  box.querySelectorAll("[data-testid=ask-choice]").forEach((b) => (b.onclick = async () => {
    const c = a.choices[+b.dataset.i]; if (!c) return;
    box.querySelectorAll("[data-testid=ask-choice]").forEach((x) => (x.disabled = true));
    try {
      if (a.kind === "reel_to_carousel" && c.body) { const n = await api(`/api/agent/runs/${r.id}/template`, { method: "POST", body: c.body }); showRun(n); if (window.watch) watch(n.id); }
      else if (a.kind === "music" && c.body) { await api(`/api/agent/runs/${r.id}/music`, { method: "POST", body: c.body }); toast(`Music: ${c.label}`); await refresh(r); }
      else if (c.say) { $("#replyIn").value = c.say; $("#replyForm").requestSubmit(); }
    } catch (e) { toast(e.message, 6000); box.querySelectorAll("[data-testid=ask-choice]").forEach((x) => (x.disabled = false)); }
  }));
};

// ---------------------------------------------------------------- All templates…
let DLG = null;
async function allTemplates(r) {
  if (DLG) { try { DLG.close(); } catch (_) {} DLG.remove(); }
  const dlg = document.createElement("dialog");
  dlg.className = "pl-dialog pc-tpl-dialog"; dlg.setAttribute("aria-labelledby", "pcTplT"); dlg.dataset.testid = "tpl-all-dialog";
  dlg.setAttribute("data-testid", "tpl-all-dialog");
  dlg.innerHTML = `<div class="pc-tpl-head"><div><h2 id="pcTplT">All templates</h2><p class="muted sm" data-testid="tpl-all-kit">Drawn from this post in your kit…</p></div>
    <button type="button" class="icon-btn" data-testid="tpl-all-close" aria-label="Close"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <div class="pc-tpl-grid" data-testid="tpl-all-grid"><p class="muted sm"><span class="spin" aria-hidden="true"></span> Drawing every template…</p></div>`;
  document.body.append(dlg); DLG = dlg;
  const close = () => { try { dlg.close(); } catch (_) {} dlg.remove(); if (DLG === dlg) DLG = null; };
  dlg.querySelector("[data-testid=tpl-all-close]").onclick = close;
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  let t;
  try { t = await api(`/api/agent/runs/${r.id}/templates?all=1`); } catch (e) { dlg.querySelector("[data-testid=tpl-all-grid]").innerHTML = `<p class="st warn">${E(e.message)}</p>`; return; }
  dlg.querySelector("[data-testid=tpl-all-kit]").textContent = `Each one drawn from this post${t.kit ? ` in your ${t.kit} kit` : ""}. Pick one to use it.`;
  const grid = dlg.querySelector("[data-testid=tpl-all-grid]");
  grid.innerHTML = (t.options || []).map((o) => `<button type="button" class="pc-tpl${o.current ? " on" : ""}" data-tpl="${E(o.id)}" data-testid="tpl-all-item" aria-pressed="${o.current}" title="${E(o.about || o.name)}">
      ${o.preview ? `<img src="${escUrl(o.preview)}" alt="">` : `<span class="mp-tpl-ph" aria-hidden="true"></span>`}
      <b>${E(o.name)}</b><small class="muted">${o.current ? "In use" : o.custom ? "Yours" : E(o.about || "")}</small></button>`).join("") ||
    `<p class="muted sm">No templates.</p>`;
  grid.querySelectorAll("[data-tpl]").forEach((b) => (b.onclick = async () => {
    if (b.classList.contains("on")) { close(); return; }
    grid.querySelectorAll("[data-tpl]").forEach((x) => (x.disabled = true)); b.classList.add("busy");
    try { const n = await api(`/api/agent/runs/${r.id}/template`, { method: "POST", body: { template: b.dataset.tpl } }); close(); showRun(n); if (window.watch) watch(n.id); }
    catch (e) { toast(e.message, 6000); grid.querySelectorAll("[data-tpl]").forEach((x) => (x.disabled = false)); b.classList.remove("busy"); }
  }));
}

window.hqPkgControls = { card, wire, music, changed, undo, isReel };
window.hqAskCard = askCard;
window.hqAllTemplates = allTemplates;
})();
