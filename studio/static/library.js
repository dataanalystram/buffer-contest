// HuhQueue Library: every upload and every generated image, carousel and Reel (GET /api/library, the same as MCP
// list_library), a full-size lightbox, and the picker Make a post uses to attach or replace one channel's media.
"use strict";
const LIB = { items: [], data: null, t: null };
const KIND_NAME = { video: "Video", image: "Image", carousel: "Carousel" };
const SRC_NAME = { upload: "Uploaded", take: "Uploaded (One take)", generated: "Made by the agent", sample: "Sample" };
const libWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "Sample");

function libThumb(it, cls = "") {
  if (it.kind === "video") return it.thumb ? `<img class="${cls}" src="${esc(it.thumb)}" alt="" loading="lazy">`
    : `<video class="${cls}" src="${esc(it.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>`;
  return `<img class="${cls}" src="${esc(it.thumb || it.url)}" alt="" loading="lazy">`;
}
function libCard(it) {
  const { fmtDur, fmtSize } = window.HQ_FMT;
  const used = it.used_in || [];
  const meta = [KIND_NAME[it.kind] + (it.kind === "carousel" && it.slides ? ` · ${it.slides.length} slides` : ""), fmtSize(it.size)];
  if (it.created) meta.push(libWhen(it.created));
  if (it.duration) meta.splice(1, 0, fmtDur(it.duration));
  return `<article class="lib-card" data-testid="library-item" data-id="${esc(it.id)}" data-kind="${esc(it.kind)}">
    <button type="button" class="lib-th" data-act="open" data-testid="library-open" aria-label="Open ${esc(it.name)} full size">${libThumb(it)}${it.kind === "video" ? '<span class="lib-play" aria-hidden="true">▶</span>' : ""}<span class="lib-kind">${esc(KIND_NAME[it.kind])}</span></button>
    <div class="lib-info"><b title="${esc(it.name)}">${esc(it.name)}</b><small>${meta.map(esc).join(" · ")}</small><small class="lib-src">${esc(SRC_NAME[it.source] || it.source)}</small>
      <small class="lib-used" data-testid="library-used">${used.length ? "Used in: " + used.slice(0, 2).map((u) => `<button type="button" class="link" data-run="${u.run_id}" data-testid="library-used-run">${esc(u.title.slice(0, 48))}</button>`).join(", ") + (used.length > 2 ? ` and ${used.length - 2} more` : "") : "Not used in a post yet"}</small></div>
    <div class="lib-acts">${it.read_only ? `<span class="muted sm">Sample · read-only</span>`
      : `<button type="button" class="btn sm" data-act="use" data-testid="library-use">Use in a post</button><button type="button" class="btn2 sm danger" data-act="del" data-testid="library-delete">Delete</button>`}</div>
  </article>`;
}

async function loadLibrary() {
  const grid = $("#libGrid");
  if (!LIB.data) grid.innerHTML = `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  try {
    const q = new URLSearchParams({ q: $("#libQ").value.trim(), kind: $("#libKind").value });
    const d = await api("/api/library?" + q); LIB.data = d; LIB.items = d.items;
    const { fmtSize } = window.HQ_FMT;
    $("#libUsage").textContent = d.read_only ? "Sample items · read-only" : `${fmtSize(d.used)} of ${fmtSize(d.quota)} used · files up to ${fmtSize(d.file_max)}`;
    $("#libUpBtn").hidden = !!d.read_only;
    grid.innerHTML = d.items.length ? d.items.map(libCard).join("")
      : `<div class="empty" data-testid="library-empty"><b>${$("#libQ").value || $("#libKind").value ? "Nothing matches" : "Your Library is empty"}</b><span>Upload a photo or video here or in Make a post, or make a post: every image, carousel and Reel the agent makes lands here.</span></div>`;
    grid.querySelectorAll(".lib-card").forEach(wireLibCard);
  } catch (e) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
function wireLibCard(el) {
  const it = LIB.items.find((x) => String(x.id) === el.dataset.id); if (!it) return;
  el.querySelector("[data-act=open]").onclick = () => openMedia(libMediaList(it), 0, it.name);
  el.querySelectorAll("[data-run]").forEach((b) => (b.onclick = async () => { showTab("agent"); try { const r = await api(`/api/agent/runs/${b.dataset.run}`); if (r.kind === "take") return window.openTake(r.id); window.showRunFromLibrary?.(r); } catch (e) { toast(e.message); } }));
  const use = el.querySelector("[data-act=use]");
  if (use) use.onclick = () => { showTab("agent"); setTimeout(() => window.attachToPost({ media_token: it.media_token, name: it.name, kind: it.kind, url: it.url, thumb: it.thumb }), 50); toast("Attached to a new post. Say what to post and press Make it."); };
  const del = el.querySelector("[data-act=del]");
  if (del) del.onclick = async () => {
    const used = (it.used_in || []).filter((u) => !u.sent);
    if (!confirm(`Delete “${it.name}”? The file is removed for good.` + (used.length ? `\n\nIt's used in ${used.length} post${used.length > 1 ? "s" : ""} that hasn't gone out yet; those posts lose this media.` : ""))) return;
    try {
      await api(`/api/library/${it.id}?force=${used.length ? 1 : 0}`, { method: "DELETE" });
      toast("Deleted"); loadLibrary();
    } catch (e) {
      if (e.status === 409 && confirm(e.message)) { await api(`/api/library/${it.id}?force=1`, { method: "DELETE" }); toast("Deleted"); loadLibrary(); }
      else if (e.status !== 409) toast(e.message, 5000);
    }
  };
}
function libMediaList(it) {
  if (it.kind === "carousel") return (it.slides || [it.url]).map((u) => ({ url: u, type: "photo" }));
  return [{ url: it.url, type: it.kind === "video" ? "video" : "photo" }];
}
$("#libQ").addEventListener("input", () => { clearTimeout(LIB.t); LIB.t = setTimeout(loadLibrary, 250); });
$("#libKind").onchange = loadLibrary;
$("#libFile").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const max = (LIB.data && LIB.data.file_max) || 100 * 1048576;
  if (f.size > max) { e.target.value = ""; return toast(`Files up to ${Math.round(max / 1048576)} MB`); }
  $("#libUsage").innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading ${esc(f.name)}…`;
  try { await window.hqUploadFile(f); toast("Saved to your Library"); } catch (err) { toast(err.message, 6000); }
  e.target.value = ""; loadLibrary();
};

// ---------------------------------------------------------------- full-size viewer (Make a post previews + Library)
const LB = { items: [], i: 0 };
function openMedia(items, i = 0, title = "") {
  LB.items = items || []; LB.i = Math.max(0, Math.min(i, LB.items.length - 1));
  $("#lbTitle").textContent = title; drawLB();
  const d = $("#lightbox"); if (!d.open) d.showModal();
}
function drawLB() {
  const m = LB.items[LB.i]; if (!m) return;
  $("#lbBody").innerHTML = m.type === "video" ? `<video src="${esc(m.url)}" controls autoplay playsinline data-testid="lightbox-video"></video>`
    : `<img src="${esc(m.url)}" alt="Slide ${LB.i + 1} of ${LB.items.length}" data-testid="lightbox-img">`;
  $("#lbPrev").hidden = $("#lbNext").hidden = LB.items.length < 2;
}
$("#lbPrev").onclick = () => { LB.i = (LB.i + LB.items.length - 1) % LB.items.length; drawLB(); };
$("#lbNext").onclick = () => { LB.i = (LB.i + 1) % LB.items.length; drawLB(); };
$("#lbClose").onclick = () => $("#lightbox").close();
$("#lightbox").addEventListener("close", () => ($("#lbBody").innerHTML = ""));
$("#lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") $("#lightbox").close(); });
document.addEventListener("keydown", (e) => { if (!$("#lightbox").open || LB.items.length < 2) return; if (e.key === "ArrowRight") $("#lbNext").click(); if (e.key === "ArrowLeft") $("#lbPrev").click(); });
window.openMedia = openMedia;

// ---------------------------------------------------------------- picker: attach to a post / replace one channel's media
const PICK = { cb: null, opts: {} };
async function pickFromLibrary(cb, opts = {}) {
  PICK.cb = cb; PICK.opts = opts;
  $("#libPickTitle").textContent = opts.title || "Choose from your Library";
  const grid = $("#libPickGrid"); grid.innerHTML = `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  $("#libPick").showModal();
  try {
    const d = await api("/api/library");
    let items = d.items.filter((x) => !x.read_only && x.kind !== "carousel");
    if (opts.video) items = items.filter((x) => x.kind === "video");
    grid.innerHTML = items.length ? items.map((it) => `<button type="button" class="lib-pick-item" data-id="${esc(it.id)}" data-testid="library-picker-item">${libThumb(it)}<span><b>${esc(it.name)}</b><small>${esc(KIND_NAME[it.kind])} · ${esc(SRC_NAME[it.source] || "")}</small></span></button>`).join("")
      : `<div class="empty"><b>${d.read_only ? "The sample Library is read-only" : "Nothing to pick yet"}</b><span>Upload a file with the button above.</span></div>`;
    grid.querySelectorAll("[data-id]").forEach((b) => (b.onclick = () => { const it = items.find((x) => String(x.id) === b.dataset.id); $("#libPick").close(); PICK.cb && PICK.cb(it); }));
  } catch (e) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
$("#libPickClose").onclick = () => $("#libPick").close();
$("#libPickFile").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  $("#libPickTitle").textContent = "Uploading " + f.name + "…";
  try {
    const tok = await window.hqUploadFile(f);
    $("#libPick").close();
    PICK.cb && PICK.cb({ media_token: tok, name: f.name, kind: /^video\//.test(f.type) ? "video" : "image", url: URL.createObjectURL(f) });
  } catch (err) { toast(err.message, 6000); $("#libPickTitle").textContent = PICK.opts.title || "Choose from your Library"; }
  e.target.value = "";
};
window.pickFromLibrary = pickFromLibrary;

window.HS_TABS = Object.assign(window.HS_TABS || {}, { library: loadLibrary });
