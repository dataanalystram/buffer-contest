// HuhQueue Library: every upload and every generated image, carousel and Reel (GET /api/library, the same as MCP
// list_library), a full-size lightbox, and the picker Make a post uses to attach or replace one channel's media.
// Premium layout (owner, 3 Nov 2026): type tabs, search, filter pills, grid | list, a selection bar, ⋯ menus (bottom
// sheets on a phone), pagination. Filters, search and pages run on the server; one /api/library answer draws a page.
"use strict";
const LIB = { items: [], data: null, t: null, view: "items", sel: new Set(), last: null, bin: null, binPage: 1, page: 1, per: 12, seq: 0,
  f: { type: "", date: "", created_by: "", used_in: "", run: "" }, layout: "grid" };
try { LIB.layout = localStorage.getItem("hq_lib_layout") === "list" ? "list" : "grid"; } catch (_) { /* private mode */ }
// "View run" opens in a new tab as /?tab=library&run=<id>: read it before boot rewrites the URL, open it once the Library loads
LIB.deepRun = new URLSearchParams(location.search).get("run");
// a Library view change (Library | Bin, a View run) counts as navigation, the same as showTab: boot keeps the screen, and
// one made before boot's session + channels arrived (S.navEarly) gets the boot-end redraw (HS_REDRAW.library below)
const libNav = () => { window.HQ_NAV = (window.HQ_NAV || 0) + 1; if (typeof S !== "undefined" && !S.bootData) S.navEarly = true; };
const KIND_NAME = { video: "Video", image: "Image", carousel: "Carousel", audio: "Audio" };
const SRC_NAME = { upload: "Uploaded by you", take: "Uploaded (One take)", link: "From a link", send: "Sent from a device", generated: "Made by the agent", sample: "Sample" };
const libWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "Sample");
const svg = (d, extra = "") => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"${extra}>${d}</svg>`;
const IC = {
  play: svg('<path d="M7 5l12 7-12 7z"/>'),
  car: svg('<rect x="3" y="6" width="13" height="13" rx="2"/><path d="M8 3h11a2 2 0 0 1 2 2v11"/>'),
  img: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>'),
  reel: svg('<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M3 8h18M8 3l3 5M14 3l3 5"/><path d="M10 12v5l4-2.5z"/>'),
  vid: svg('<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/>'),
  audio: svg('<path d="M9 18V6l11-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
  spark: svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
  user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
  more: svg('<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>'),
  draft: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>'),
  bin: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  run: svg('<path d="M4 12h4l3-8 4 16 3-8h2"/>'),
  open: svg('<path d="M15 3h6v6M10 14L21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>'),
  post: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h5"/>'),
  cal: svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
  layers: svg('<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>'),
  tag: svg('<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8" r="1.3"/>'),
  chev: '<svg class="ic chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
  search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
  up: svg('<path d="M12 15V4M8 8l4-4 4 4"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>'),
};
const FILTERS = [
  { key: "date", label: "Date", icon: IC.cal, all: "All time", opts: [["1d", "Last 24 hours"], ["7d", "Last 7 days"], ["30d", "Last 30 days"], ["90d", "Last 90 days"], ["365d", "Last 12 months"]] },
  { key: "created_by", label: "Created by", icon: IC.layers, all: "All", opts: [["agent", "Made by the agent"], ["you", "Uploaded by you"]] },
  { key: "used_in", label: "Used in", icon: IC.tag, all: "All posts", opts: [["used", "Used in a post"], ["unused", "Not used yet"], ["draft", "Drafts"], ["scheduled", "Scheduled"], ["sent", "Sent"]] },
  { key: "run", label: "Generation", icon: IC.spark, all: "All runs", opts: [] },
];
const typeIcon = (k) => ({ video: IC.vid, image: IC.img, carousel: IC.car, audio: IC.audio })[k] || IC.img;

function libThumb(it, cls = "") {
  if (it.kind === "video") {
    if (it.thumb) return `<img class="${cls}" src="${esc(it.thumb)}" alt="" loading="lazy" decoding="async">`;
    const s = it.thumb_src && it.thumb_src.url && window.hqThumb ? window.hqThumb(it.thumb_src.url, it.thumb_src.img || "") : "";
    return s ? `<img class="${cls}" src="${esc(s)}" alt="" loading="lazy" decoding="async" data-fallback="${esc(it.url)}">`
      : `<video class="${cls}" src="${esc(it.url)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>`;
  }
  return `<img class="${cls}" src="${esc(it.thumb || it.url)}" alt="" loading="lazy" decoding="async">`;
}
const isReel = (it) => it.kind === "video" && (it.format === "reel" || /^reel\b/i.test(it.name || ""));
function libBadge(it) {
  if (it.kind === "carousel") return `<span class="lp-badge">${IC.car}Carousel${it.slides ? ` · ${it.slides.length} slides` : ""}</span>`;
  if (isReel(it)) return `<span class="lp-badge reel">${IC.reel}REEL</span>`;
  return `<span class="lp-badge">${typeIcon(it.kind)}${esc(KIND_NAME[it.kind] || it.kind)}</span>`;
}
const selBox = (it) => `<label class="lp-sel" title="Select"><input type="checkbox" data-testid="library-select" aria-label="Select ${esc(it.title || it.name)}"${LIB.sel.has(String(it.id)) ? " checked" : ""}></label>`;
const byLine = (it, extra = "") => {
  const agent = it.source === "generated";
  return `<small class="lp-by${agent ? "" : " you"}">${agent ? IC.spark : IC.user}<span>${esc(SRC_NAME[it.source] || it.source)}</span>${it.created ? `<span class="lp-dot lp-when" aria-hidden="true">·</span><span class="lp-when">${esc(libWhen(it.created))}</span>` : ""}${extra}</small>`;
};
function libCard(it) {
  const { fmtDur, fmtSize } = window.HQ_FMT;
  const used = it.used_in || [], title = it.title || it.name, picked = LIB.sel.has(String(it.id));
  const dur = it.kind === "video" ? `<span class="lp-dur">${IC.play}${it.duration ? esc(fmtDur(it.duration)) : ""}</span>` : "";
  const menu = it.read_only ? "" : `<span class="lp-mw"><button type="button" class="lp-more" data-act="more" data-testid="library-more" aria-label="More actions for ${esc(title)}" aria-haspopup="menu" aria-expanded="false">${IC.more}</button>
    <div class="lp-menu r" role="menu" aria-label="${esc(title)}" hidden>
      <button type="button" class="lp-mi" role="menuitem" data-act="use" data-testid="library-use">${IC.play}<span>Use in post</span></button>
      <button type="button" class="lp-mi" role="menuitem" data-act="draft" data-testid="library-create-draft">${IC.draft}<span>Create draft</span></button>
      ${it.run_id ? `<a class="lp-mi" role="menuitem" href="/?tab=library&run=${esc(it.run_id)}" data-act="run" data-testid="library-view-run">${IC.run}<span>View run</span></a>` : ""}
      <button type="button" class="lp-mi" role="menuitem" data-act="open" data-testid="library-open-full">${IC.open}<span>Open full size</span></button>
      ${used.length ? `<hr><div class="lp-menu-h">Used in</div>${used.slice(0, 4).map((u) => `<button type="button" class="lp-mi" role="menuitem" data-run="${esc(u.run_id)}" data-testid="library-used-run">${IC.post}<span>${esc(u.title.slice(0, 60))}</span></button>`).join("")}` : ""}
      <hr><button type="button" class="lp-mi danger" role="menuitem" data-act="del" data-testid="library-delete">${IC.bin}<span>Move to bin…</span></button>
    </div></span>`;
  const meta = [it.kind === "carousel" && it.slides ? `Carousel · ${it.slides.length} slides` : KIND_NAME[it.kind] || it.kind, fmtSize(it.size)];
  const usedTxt = used.length ? `Used in ${used.length === 1 ? "1 post" : used.length + " posts"}` : "Not used yet";
  return `<article class="lp-card${picked ? " picked" : ""}" data-testid="library-item" data-id="${esc(it.id)}" data-kind="${esc(it.kind)}">
    ${it.read_only ? "" : selBox(it)}
    <div class="lp-thw${it.kind === "carousel" ? " stack" : ""}"><button type="button" class="lp-th" data-act="open" data-testid="library-open" aria-label="Open ${esc(title)} full size">${libThumb(it)}</button>${libBadge(it)}${dur}</div>
    <div class="lp-info">
      <div class="lp-trow"><b class="lp-t" title="${esc(title)}">${esc(title)}</b>${menu}</div>
      <small class="lp-meta"><span>${meta.map(esc).join(" · ")}</span>${it.read_only ? `<span class="lp-dot" aria-hidden="true">·</span><span>Sample · read-only</span>` : ""}</small>
      ${byLine(it)}
      <small class="lp-meta lp-used" data-testid="library-used"><span>${esc(usedTxt)}</span></small>
    </div>
  </article>`;
}
const skeleton = (n = 8) => Array.from({ length: n }, () => `<div class="lp-card lp-skel" aria-hidden="true"><div class="lp-thw"></div><i></i><i></i></div>`).join("");
const libFiltered = () => !!($("#libQ").value.trim() || Object.values(LIB.f).some(Boolean));

// ---------------------------------------------------------------- load: one /api/library answer per page (filters on the server)
async function loadLibrary() {
  if (LIB.view === "bin") return loadBin();
  const grid = $("#libGrid");
  if (LIB.deepRun) { const rid = LIB.deepRun; LIB.deepRun = null; openRunById(rid); return; }
  if (window.LIB_PRESET) {  // Make a post's "View all →" carries its filter (Reels -> Videos, Drafts -> Drafts ...)
    const k = window.LIB_PRESET.kind || ""; window.LIB_PRESET = null;
    LIB.f = { type: "", date: "", created_by: "", used_in: "", run: "" }; $("#libQ").value = ""; LIB.page = 1;
    if (["video", "image", "carousel"].includes(k)) LIB.f.type = k;
    else if (k === "draft") LIB.f.used_in = "draft";
    else if (k === "upload") LIB.f.created_by = "you";
    else if (k === "generated") LIB.f.created_by = "agent";
    libPaintControls();
  }
  if (!LIB.data) { grid.innerHTML = skeleton(); grid.setAttribute("aria-busy", "true"); }
  const seq = ++LIB.seq;
  try {
    const q = new URLSearchParams({ q: $("#libQ").value.trim(), per: LIB.per, offset: (Math.max(1, LIB.page) - 1) * LIB.per });
    Object.entries(LIB.f).forEach(([k, v]) => v && q.set(k, v));
    const d = await api("/api/library?" + q);
    if (seq !== LIB.seq) return;  // a newer filter / page answered already
    if (!d.items.length && d.total && LIB.page > 1) { LIB.page = Math.ceil(d.total / LIB.per); return loadLibrary(); }
    LIB.data = d; LIB.items = d.items;
    libDraw(d);
  } catch (e) { if (seq === LIB.seq) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; grid.setAttribute("aria-busy", "false"); } }
}
// draws the Library from an /api/library answer: loadLibrary after it fetches, and the boot-end redraw from LIB.data
function libDraw(d) {
  const grid = $("#libGrid"), { fmtSize } = window.HQ_FMT;
  grid.setAttribute("aria-busy", "false");
  grid.classList.toggle("lp-list", LIB.layout === "list");
  $("#libUsage").textContent = d.read_only ? "Sample items · read-only" : `${fmtSize(d.used)} of ${fmtSize(d.quota)} used · files up to ${fmtSize(d.file_max)}`;
  $("#libIns").hidden = !!d.read_only;
  $("#libLinkMax").textContent = fmtSize(d.file_max);
  libBinCount(d.bin_count || 0);
  const types = (d.facets && d.facets.types) || {};
  document.querySelectorAll("#libTabs [data-n]").forEach((s) => { const n = types[s.dataset.n || "all"]; s.textContent = n == null ? "" : n; });
  FILTERS[3].opts = ((d.facets && d.facets.runs) || []).map((r) => [String(r.run_id), r.title]);
  if (LIB.f.run && !FILTERS[3].opts.some(([v]) => v === LIB.f.run)) FILTERS[3].opts.unshift([LIB.f.run, `Run ${LIB.f.run}`]);
  libPaintControls();
  const total = d.total ?? d.items.length, offset = d.offset || 0;
  LIB.page = Math.floor(offset / LIB.per) + 1;
  grid.innerHTML = d.items.length ? d.items.map(libCard).join("") : libEmpty(d);
  grid.querySelectorAll(".lp-card[data-testid=library-item]").forEach(wireLibCard);
  grid.querySelectorAll("[data-empty-act]").forEach((b) => (b.onclick = () => {
    const a = b.dataset.emptyAct;
    if (a === "clear") libClearFilters(); else if (a === "upload") $("#libFile").click(); else if (a === "link") $("#libLinkBtn").click();
    else if (a === "make") showTab("agent");
  }));
  LIB.pageIds = d.items.filter((x) => !x.read_only).map((x) => String(x.id));
  $("#libSelAllWrap").hidden = !LIB.pageIds.length && !LIB.sel.size;
  libSelPaint();
  grid.querySelectorAll(".lp-th img[data-fallback]").forEach((im) => (im.onerror = () => {
    im.outerHTML = `<video src="${esc(im.dataset.fallback)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>`; }));
  libFoot(total, offset, d.items.length, "items", (n) => { LIB.page = n; loadLibrary(); libTop(); });
}
function libFoot(total, offset, n, word, onPage) {
  $("#libFoot").hidden = !total;
  $("#libShowing").textContent = total ? `Showing ${offset + 1}–${offset + n} of ${total} ${total === 1 ? word.replace(/s$/, "") : word}` : "";
  if (window.hqPager) window.hqPager($("#libPager"), { total, page: Math.floor(offset / LIB.per) + 1, per: LIB.per, label: "Library pages", onPage });
}
const libTop = () => { const t = $("#tab-library"); if (t && t.getBoundingClientRect().top < 0) t.scrollIntoView({ behavior: "smooth", block: "start" }); };
function libEmpty(d) {
  const box = (ic, t, s, acts = "") => `<div class="lp-empty" data-testid="library-empty"><span class="lp-empty-ic" aria-hidden="true">${ic}</span><b>${t}</b><span>${s}</span>${acts ? `<span class="lp-empty-acts">${acts}</span>` : ""}</div>`;
  if (libFiltered()) {
    const what = LIB.f.type === "audio" ? "No audio yet" : "Nothing matches";
    const why = LIB.f.type === "audio" ? "Voiceovers and music the agent makes will show up here." : "Nothing in your Library fits these filters yet.";
    return box(LIB.f.type === "audio" ? IC.audio : IC.search, what, why, `<button type="button" class="btn2 sm" data-empty-act="clear" data-testid="library-show-all">Clear filters</button>`);
  }
  if (d.read_only) return box(IC.img, "The sample Library is read-only", "Connect your own workspace to keep uploads and everything the agent makes.");
  return box(IC.img, "Your Library is empty", "Upload a photo or video, or make a post: every image, carousel and Reel the agent makes lands here.",
    `<button type="button" class="btn sm" data-empty-act="upload" data-testid="library-empty-upload">Upload a file</button><button type="button" class="btn2 sm" data-empty-act="make" data-testid="library-empty-make">Make a post</button>`);
}
function wireLibCard(el) {
  const it = LIB.items.find((x) => String(x.id) === el.dataset.id); if (!it) return;
  const open = () => openMedia(libMediaList(it), 0, it.title || it.name);
  el.querySelectorAll("[data-act=open]").forEach((b) => (b.onclick = () => { closeMenus(); open(); }));
  el.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => { closeMenus(); openRunById(b.dataset.run); }));
  wireSel(el);
  const more = el.querySelector("[data-act=more]");
  if (more) more.onclick = (e) => { e.stopPropagation(); toggleMenu(more, more.nextElementSibling); };
  const vr = el.querySelector("[data-act=run]");
  if (vr) vr.onclick = (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; e.preventDefault(); closeMenus(); openRunById(it.run_id); };
  const dr = el.querySelector("[data-act=draft]");
  if (dr) dr.onclick = () => { closeMenus(); openDraft(it); };
  const use = el.querySelector("[data-act=use]");
  if (use) use.onclick = () => { closeMenus(); useInPost(it); };
  const del = el.querySelector("[data-act=del]");
  if (del) del.onclick = async () => {
    closeMenus();
    const r = await libDeleteDialog([it]); if (!r) return;
    LIB.sel.delete(String(it.id)); loadLibrary(); if (r.mode === "bin") undoToast(it, r); else toast("Deleted permanently");
  };
  // hover preview: a muted, looping copy of the video, only while the pointer is on it (fetched on hover, never before)
  if (it.kind === "video" && matchMedia("(hover:hover) and (pointer:fine)").matches) {
    const th = el.querySelector(".lp-th"); let tm = 0;
    th.addEventListener("pointerenter", () => { tm = setTimeout(() => {
      if (th.querySelector(".lp-prev")) return;
      const v = document.createElement("video");
      Object.assign(v, { muted: true, loop: true, playsInline: true, preload: "auto", src: it.url, className: "lp-prev" });
      v.setAttribute("aria-hidden", "true"); th.appendChild(v); v.play().catch(() => v.remove());
    }, 220); });
    th.addEventListener("pointerleave", () => { clearTimeout(tm); const v = th.querySelector(".lp-prev"); if (v) { v.pause(); v.removeAttribute("src"); v.load(); v.remove(); } });
  }
}
function useInPost(it) {
  showTab("agent");
  setTimeout(() => window.attachToPost({ media_token: it.media_token, name: it.name, kind: it.kind, url: it.url, thumb: it.thumb }), 50);
  toast("Attached to a new post. Say what to post and press Make it.");
}
// a delete goes to the Bin (7 days): the toast offers Undo, which restores the item and every post's files
function undoToast(item, r) {
  const t = document.getElementById("toast"); if (!t) return toast("Moved to the Bin");
  t.innerHTML = `Moved to the Bin for ${(r && r.in_trash_days) || 7} days. <button type="button" class="linkish" data-testid="lib-undo">Undo</button>`;
  t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 8000);
  const b = t.querySelector("[data-testid=lib-undo]");
  if (b) b.onclick = async () => { try { await api(`/api/library/${item.id}/restore`, { method: "POST", body: {} }); toast("Restored"); loadLibrary(); } catch (e) { toast(e.message, 5000); } };
}
function libMediaList(it) {
  if (it.kind === "carousel") return (it.slides || [it.url]).map((u) => ({ url: u, type: "photo" }));
  return [{ url: it.url, type: it.kind === "video" ? "video" : "photo" }];
}
// ---------------------------------------------------------------- open a run (Used in, View run, a /?tab=library&run= link)
async function openRunById(rid) {
  showTab("agent");
  try { const r = await api(`/api/agent/runs/${encodeURIComponent(rid)}`); libNav(); if (r.kind === "take") return window.openTake(r.id); window.showRunFromLibrary?.(r); }
  catch (e) { toast(e.message); }
}
window.hqOpenRun = openRunById;

// ---------------------------------------------------------------- menus: popovers on desktop, bottom sheets on a phone
const isSheet = () => matchMedia("(max-width:640px)").matches;
let MENU = null;
function closeMenus(focusBack) {
  if (!MENU) return;
  const { btn, menu } = MENU; MENU = null;
  menu.hidden = true; btn.setAttribute("aria-expanded", "false");
  const card = btn.closest(".lp-card, .lp-mw"); if (card) card.style.zIndex = "";
  const sc = document.getElementById("libScrim"); if (sc) sc.remove();
  if (focusBack) btn.focus();
}
function toggleMenu(btn, menu) {
  if (MENU && MENU.menu === menu) return closeMenus(true);
  closeMenus();
  menu.hidden = false; btn.setAttribute("aria-expanded", "true"); MENU = { btn, menu };
  if (isSheet()) {  // a bottom sheet over a scrim, both in the menu's own stacking context
    const sc = document.createElement("div"); sc.className = "lp-scrim"; sc.id = "libScrim"; sc.setAttribute("aria-hidden", "true");
    sc.onclick = (e) => { e.stopPropagation(); closeMenus(true); }; menu.parentNode.insertBefore(sc, menu);
  } else {  // a popover above the cards after it; open it upward when it would spill under the window
    const card = btn.closest(".lp-card") || btn.closest(".lp-mw"); if (card) card.style.zIndex = "30";
    menu.style.top = ""; menu.style.bottom = "";
    const r = menu.getBoundingClientRect();
    if (r.bottom > innerHeight - 8 && r.height < btn.getBoundingClientRect().top - 16) { menu.style.top = "auto"; menu.style.bottom = "calc(100% + 6px)"; }
  }
  const first = menu.querySelector("[role^=menuitem]:not([hidden])"); if (first) first.focus();
}
document.addEventListener("click", (e) => { if (MENU && !MENU.menu.contains(e.target) && !MENU.btn.contains(e.target)) closeMenus(); });
document.addEventListener("keydown", (e) => {
  if (!MENU) return;
  const items = [...MENU.menu.querySelectorAll("[role^=menuitem]")].filter((x) => !x.hidden && x.offsetParent !== null);
  const i = items.indexOf(document.activeElement);
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenus(true); }
  else if (e.key === "ArrowDown") { e.preventDefault(); (items[i + 1] || items[0])?.focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); (items[i - 1] || items[items.length - 1])?.focus(); }
  else if (e.key === "Home") { e.preventDefault(); items[0]?.focus(); }
  else if (e.key === "End") { e.preventDefault(); items[items.length - 1]?.focus(); }
  else if (e.key === "Tab") closeMenus();
}, true);

// ---------------------------------------------------------------- tabs, filters, search, layout
function libPaintControls() {
  document.querySelectorAll("#libTabs [role=tab]").forEach((b) => { const on = b.dataset.type === LIB.f.type; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
  const tl = $("#libTabs"), st = tl.querySelector("[aria-selected=true]");  // keep the chosen tab in view on a phone
  if (st && (st.offsetLeft < tl.scrollLeft || st.offsetLeft + st.offsetWidth > tl.scrollLeft + tl.clientWidth)) tl.scrollLeft = st.offsetLeft - 8;
  $("#libPills").innerHTML = FILTERS.map((f) => {
    const cur = LIB.f[f.key], label = cur ? ((f.opts.find(([v]) => v === cur) || [])[1] || cur) : f.all;
    return `<span class="lp-mw"><button type="button" class="lp-pill${cur ? " on" : ""}" data-f="${f.key}" data-testid="library-filter-${f.key.replace("_", "-")}" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(f.label)}: ${esc(label)}">${f.icon}<span class="lp-pill-k">${esc(f.label)}</span><span class="lp-pill-v">${esc(label)}</span>${IC.chev}</button>
      <div class="lp-menu" role="menu" aria-label="${esc(f.label)}" hidden>${[["", f.all], ...f.opts].map(([v, l]) => `<button type="button" class="lp-mi" role="menuitemradio" aria-checked="${String(v === cur)}" data-v="${esc(v)}" data-testid="library-filter-option"><span>${esc(l)}</span></button>`).join("")}${f.key === "run" && !f.opts.length ? `<div class="lp-menu-h">No runs behind these items yet</div>` : ""}</div></span>`;
  }).join("");
  $("#libPills").querySelectorAll(".lp-pill").forEach((b) => {
    const menu = b.nextElementSibling;
    b.onclick = (e) => { e.stopPropagation(); toggleMenu(b, menu); };
    menu.querySelectorAll("[data-v]").forEach((o) => (o.onclick = () => { LIB.f[b.dataset.f] = o.dataset.v; closeMenus(); libRefilter(); $(`#libPills [data-f=${b.dataset.f}]`)?.focus(); }));
  });
  $("#libGridBtn").setAttribute("aria-pressed", String(LIB.layout === "grid")); $("#libListBtn").setAttribute("aria-pressed", String(LIB.layout === "list"));
}
function libRefilter() { LIB.page = 1; LIB.sel.clear(); LIB.last = null; libPaintControls(); loadLibrary(); }
function libClearFilters() { LIB.f = { type: "", date: "", created_by: "", used_in: "", run: "" }; $("#libQ").value = ""; libRefilter(); }
document.querySelectorAll("#libTabs [role=tab]").forEach((b) => {
  b.onclick = () => { if (LIB.f.type === b.dataset.type) return; LIB.f.type = b.dataset.type; libRefilter(); };
  b.onkeydown = (e) => {
    const tabs = [...document.querySelectorAll("#libTabs [role=tab]")], i = tabs.indexOf(b);
    const to = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(i + tabs.length - 1) % tabs.length] : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
    if (to) { e.preventDefault(); to.focus(); to.click(); }
  };
});
function libSetLayout(l) {
  LIB.layout = l; try { localStorage.setItem("hq_lib_layout", l); } catch (_) { /* private mode */ }
  $("#libGrid").classList.toggle("lp-list", l === "list"); libPaintControls();
}
$("#libGridBtn").onclick = () => libSetLayout("grid");
$("#libListBtn").onclick = () => libSetLayout("list");
// "/" jumps to the search while the Library is open (⌘K stays the app's command palette)
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || $("#tab-library").hidden || LIB.view === "bin") return;
  const t = e.target; if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) || document.querySelector("dialog[open]")) return;
  e.preventDefault(); $("#libQ").focus();
});

// ---------------------------------------------------------------- selection: click, shift-click a range, select all on the page
function wireSel(el) {
  const cb = el.querySelector("[data-testid=library-select]"); if (!cb) return;
  cb.onclick = (e) => {
    const id = el.dataset.id, ids = LIB.pageIds || [];
    if (e.shiftKey && LIB.last && ids.includes(LIB.last)) {
      const [x, y] = [ids.indexOf(LIB.last), ids.indexOf(id)].sort((m, n) => m - n);
      ids.slice(x, y + 1).forEach((k) => (cb.checked ? LIB.sel.add(k) : LIB.sel.delete(k)));
    } else if (cb.checked) LIB.sel.add(id); else LIB.sel.delete(id);
    LIB.last = id; libSelPaint();
  };
}
function libSelPaint() {
  const grid = $("#libGrid");
  grid.querySelectorAll(".lp-card[data-id]").forEach((c) => { const on = LIB.sel.has(c.dataset.id); c.classList.toggle("picked", on); const b = c.querySelector("[data-testid=library-select]"); if (b) b.checked = on; });
  const ids = LIB.pageIds || [], n = LIB.sel.size, all = $("#libSelAll"), bin = LIB.view === "bin";
  all.checked = ids.length > 0 && ids.every((k) => LIB.sel.has(k)); all.indeterminate = !all.checked && ids.some((k) => LIB.sel.has(k));
  $("#libBulk").classList.toggle("has-sel", n > 0);
  $("#libBulkN").textContent = `${n} selected`;
  $("#libBulkCancel").hidden = !n;
  ["libBulkUse", "libBulkDraft", "libBulkBin", "libBulkMoreWrap"].forEach((k) => ($("#" + k).hidden = bin));
  $("#libBulkRestore").hidden = !bin; $("#libBulkPerm").hidden = !bin;
  ["libBulkUse", "libBulkDraft"].forEach((k) => { const b = $("#" + k); b.disabled = n !== 1; b.title = n > 1 ? "Pick one item for this" : ""; });
  ["libBulkBin", "libBulkMore", "libBulkRestore", "libBulkPerm"].forEach((k) => ($("#" + k).disabled = !n));
}
function libSelClear() { LIB.sel.clear(); LIB.last = null; libSelPaint(); }
$("#libSelAll").onchange = (e) => { (LIB.pageIds || []).forEach((k) => (e.target.checked ? LIB.sel.add(k) : LIB.sel.delete(k))); libSelPaint(); };
$("#libBulkCancel").onclick = libSelClear;
$("#libBulkClearMenu").onclick = () => { closeMenus(); libSelClear(); };
$("#libBulkMore").onclick = (e) => { e.stopPropagation(); toggleMenu($("#libBulkMore"), $("#libBulkMenu")); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && LIB.sel.size && !MENU && !document.querySelector("dialog[open]")) libSelClear(); });
const selItems = () => [...LIB.sel].map((k) => (LIB.view === "bin" ? (LIB.bin || []) : LIB.items).find((x) => String(x.id) === k) || { id: k, name: "item" });
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const refusedNote = (rf) => (!rf || !rf.length ? "" : rf.length === 1 ? rf[0].error : `${plural(rf.length, "item")} not deleted: ${rf[0].error}`);
$("#libBulkUse").onclick = () => { const [it] = selItems(); if (it && it.media_token) { libSelClear(); useInPost(it); } };
$("#libBulkDraft").onclick = () => { const [it] = selItems(); if (it && it.media_token) openDraft(it); };
$("#libBulkBin").onclick = async () => {
  const r = await libDeleteDialog(selItems(), "bin"); if (!r) return;
  libSelClear(); loadLibrary(); toast(`${plural(r.deleted.length, "item")} moved to the Bin` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000);
};
async function libBulkPermanent() {
  closeMenus();
  const items = selItems();
  if (LIB.view === "bin") {
    if (!(await hqConfirm(`Delete ${plural(items.length, "item")} permanently? The files are removed now. This can't be undone.`, { ok: "Delete permanently", danger: true }))) return;
    try { const r = await api("/api/library/bin/purge", { method: "POST", body: { ids: items.map((x) => +x.id) } }); libSelClear(); loadBin(); toast(`${plural(r.purged.length, "item")} deleted permanently` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000); }
    catch (e) { toast(e.message, 6000); }
    return;
  }
  const r = await libDeleteDialog(items, "permanent"); if (!r) return;
  libSelClear(); loadLibrary(); toast(`${plural(r.deleted.length, "item")} deleted permanently` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000);
}
$("#libBulkPerm").onclick = libBulkPermanent;
$("#libBulkPermMenu").onclick = libBulkPermanent;
$("#libBulkRestore").onclick = async () => {
  try { const r = await api("/api/library/bin/restore", { method: "POST", body: { ids: selItems().map((x) => +x.id) } }); libSelClear(); loadBin(); toast(`${plural(r.restored.length, "item")} restored`); }
  catch (e) { toast(e.message, 6000); }
};

// ---------------------------------------------------------------- the delete choice: Move to Bin (default) or Delete permanently
function libDeleteDialog(items, preset) {
  return new Promise((done) => {
    const dlg = $("#libDel"), one = items.length === 1;
    const used = items.flatMap((x) => (x.used_in || []).filter((u) => !u.sent || u.scheduled));
    $("#libDelTitle").textContent = one ? `Delete “${items[0].title || items[0].name}”?` : `Delete ${plural(items.length, "item")}?`;
    $("#libDelUsed").hidden = !used.length;
    $("#libDelUsed").textContent = used.length ? `Used in ${plural(used.length, "post")} that hasn't gone out or is scheduled; those posts lose this media until it's restored. Media a scheduled post uses can't be deleted permanently.` : "";
    const radios = dlg.querySelectorAll("input[name=libDelMode]");
    radios.forEach((r) => (r.checked = r.value === (preset || "bin")));
    const go = $("#libDelGo"), err = $("#libDelErr");
    const paint = () => { const p = dlg.querySelector("input[name=libDelMode]:checked").value === "permanent"; go.textContent = p ? "Delete permanently" : "Move to Bin"; go.classList.toggle("danger-fill", p); };
    radios.forEach((r) => (r.onchange = paint)); paint(); err.hidden = true; go.disabled = false;
    let result = null, force = false;
    $("#libDelCancel").onclick = () => dlg.close();
    dlg.onclose = () => done(result);
    $("#libDelForm").onsubmit = async (e) => {
      e.preventDefault();
      const mode = dlg.querySelector("input[name=libDelMode]:checked").value;
      go.disabled = true;
      try {
        if (one) {
          const r = await api(`/api/library/${items[0].id}?mode=${mode}&force=${force || used.length ? 1 : 0}`, { method: "DELETE" });
          result = { ...r, deleted: [r.id], refused: [] };
        } else {
          result = await api("/api/library/bulk-delete", { method: "POST", body: { ids: items.map((x) => +x.id), mode, force: force || !!used.length } });
        }
        dlg.close();
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false; go.disabled = false;
        if (ex.status === 409 && !/can't be deleted permanently/.test(ex.message)) { force = true; go.textContent = "Delete anyway"; }
      }
    };
    dlg.showModal();
  });
}

// ---------------------------------------------------------------- the Bin
function libBinCount(n) { const b = $("#libBinCount"); b.hidden = !n; b.textContent = n; }
function libSetView(v) {
  if (LIB.view === v) return; LIB.view = v; libNav(); LIB.sel.clear(); LIB.last = null; closeMenus();
  const bin = v === "bin";
  $("#libViewItems").classList.toggle("on", !bin); $("#libViewBin").classList.toggle("on", bin);
  $("#libViewItems").setAttribute("aria-pressed", String(!bin)); $("#libViewBin").setAttribute("aria-pressed", String(bin));
  $("#libBinBar").hidden = !bin; $("#libBinNote").hidden = !bin; $("#libEmptyBin").hidden = true;
  document.querySelector("[data-testid=library-bar]").hidden = bin; $("#libPills").hidden = bin;
  loadLibrary();
}
$("#libViewItems").onclick = () => libSetView("items");
$("#libViewBin").onclick = () => libSetView("bin");
const leftLabel = (n) => (n <= 0 ? "Removed for good today" : n === 1 ? "1 day left" : `${n} days left`);
function binCard(it) {
  const { fmtSize } = window.HQ_FMT;
  const th = it.kind === "video" ? `<video src="${esc(it.url)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(it.thumb || it.url)}" alt="" loading="lazy" decoding="async">`;
  return `<article class="lp-card lp-bin${LIB.sel.has(String(it.id)) ? " picked" : ""}" data-testid="bin-item" data-id="${esc(it.id)}">
    ${selBox(it)}
    <div class="lp-thw${it.kind === "carousel" ? " stack" : ""}"><div class="lp-th" style="cursor:default;opacity:.82">${th}</div>${libBadge(it)}<span class="lp-left${it.days_left <= 1 ? " soon" : ""}" data-testid="bin-days-left">${esc(leftLabel(it.days_left))}</span></div>
    <div class="lp-info"><div class="lp-trow"><b class="lp-t" title="${esc(it.name)}">${esc(it.name)}</b></div>
      <small class="lp-meta"><span>${esc(KIND_NAME[it.kind] || it.kind)} · ${esc(fmtSize(it.size))} · deleted ${esc(libWhen(it.deleted_at))}</span></small>
      ${it.scheduled_use ? `<small class="lp-meta lp-warn"><span>Used in a scheduled post</span></small>` : ""}
      <div class="lp-binacts"><button type="button" class="lp-act" data-act="restore" data-testid="bin-restore">Restore</button><button type="button" class="lp-act danger" data-act="purge" data-testid="bin-delete-permanently">Delete permanently</button></div></div>
  </article>`;
}
async function loadBin() {
  const grid = $("#libGrid");
  if (!LIB.bin) { grid.innerHTML = skeleton(4); grid.setAttribute("aria-busy", "true"); }
  try {
    const d = await api("/api/library/bin"); LIB.bin = d.items; LIB.binData = d;
    binDraw(d);
  } catch (e) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
// draws the Bin from an /api/library/bin answer: loadBin after it fetches, and the boot-end redraw from LIB.binData
function binDraw(d) {
  const grid = $("#libGrid");
  grid.setAttribute("aria-busy", "false"); grid.classList.toggle("lp-list", LIB.layout === "list");
  libBinCount(d.total);
  const per = LIB.per, pages = Math.max(1, Math.ceil(d.items.length / per));
  LIB.binPage = Math.min(LIB.binPage || 1, pages);
  const page = d.items.slice((LIB.binPage - 1) * per, LIB.binPage * per);
  LIB.pageIds = page.map((x) => String(x.id));
  $("#libSelAllWrap").hidden = !page.length; $("#libEmptyBin").hidden = !d.items.length;
  grid.innerHTML = page.length ? page.map(binCard).join("")
    : `<div class="lp-empty" data-testid="bin-empty"><span class="lp-empty-ic" aria-hidden="true">${IC.bin}</span><b>The Bin is empty</b><span>Items you move to the Bin stay here for ${d.days} days, then they're removed for good.</span></div>`;
  grid.querySelectorAll(".lp-bin").forEach((el) => {
    const it = d.items.find((x) => String(x.id) === el.dataset.id); wireSel(el);
    el.querySelector("[data-act=restore]").onclick = async () => { try { await api(`/api/library/${it.id}/restore`, { method: "POST", body: {} }); LIB.sel.delete(String(it.id)); toast("Restored to your Library"); loadBin(); } catch (e) { toast(e.message, 5000); } };
    el.querySelector("[data-act=purge]").onclick = async () => {
      if (!(await hqConfirm(`Delete “${it.name}” permanently? The file is removed now. This can't be undone.`, { ok: "Delete permanently", danger: true }))) return;
      try { const r = await api("/api/library/bin/purge", { method: "POST", body: { ids: [it.id] } }); LIB.sel.delete(String(it.id)); if (r.refused.length) toast(r.refused[0].error, 7000); else toast("Deleted permanently"); loadBin(); } catch (e) { toast(e.message, 5000); }
    };
  });
  libSelPaint();
  libFoot(d.items.length, (LIB.binPage - 1) * per, page.length, "items", (n) => { LIB.binPage = n; loadBin(); libTop(); });
}
$("#libEmptyBin").onclick = async () => {
  if (!(await hqConfirm("Empty the Bin? Every item in it is deleted permanently now. This can't be undone.", { ok: "Empty bin", danger: true }))) return;
  try { const r = await api("/api/library/bin/purge", { method: "POST", body: { all: true } }); libSelClear(); loadBin(); toast(`${plural(r.purged.length, "item")} deleted permanently` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000); }
  catch (e) { toast(e.message, 6000); }
};

// ---------------------------------------------------------------- Create draft: the item's media + its run's caption per channel
async function openDraft(it) {
  const dlg = $("#libDraft"), chs = $("#libDraftChs"), err = $("#libDraftErr"), go = $("#libDraftGo");
  err.hidden = true; go.disabled = true; go.textContent = "Save draft";
  $("#libDraftTitle").textContent = `Create draft: ${it.title || it.name}`;
  $("#libDraftMedia").innerHTML = libThumb(it);
  chs.innerHTML = `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  dlg.showModal();
  let pre;
  try { pre = await api(`/api/library/${it.id}/draft`); } catch (e) { chs.innerHTML = ""; err.textContent = e.message; err.hidden = false; return; }
  const name = (s) => (typeof netName === "function" ? netName(s) : s);
  chs.innerHTML = pre.channels.length ? pre.channels.map((c, i) => `<div class="lib-dch" data-testid="library-draft-channel" data-channel="${esc(c.channel_id)}">
      <label class="lib-dch-h"><input type="checkbox" data-testid="library-draft-pick"${c.selected ? " checked" : ""}> <b>${esc(c.name || "")}</b> <small class="muted">${esc(name(c.service || ""))}${c.from_run ? " · caption from the run" : ""}</small></label>
      <label class="sr" for="libDt${i}">Caption for ${esc(c.name || "")}</label><textarea id="libDt${i}" rows="3" data-testid="library-draft-text">${esc(c.text || pre.default_text || "")}</textarea></div>`).join("")
    : `<div class="empty"><b>No channels connected</b><span>Connect a channel in Buffer first.</span></div>`;
  const paint = () => { const n = chs.querySelectorAll("[data-testid=library-draft-pick]:checked").length; go.disabled = !n; go.textContent = n > 1 ? `Save ${n} drafts` : "Save draft"; };
  chs.querySelectorAll("[data-testid=library-draft-pick]").forEach((b) => (b.onchange = paint)); paint();
  $("#libDraftCancel").onclick = () => dlg.close();
  $("#libDraftForm").onsubmit = async (e) => {
    e.preventDefault();
    const pick = [...chs.querySelectorAll(".lib-dch")].filter((x) => x.querySelector("[data-testid=library-draft-pick]").checked)
      .map((x) => ({ channel_id: x.dataset.channel, text: x.querySelector("textarea").value }));
    go.disabled = true; go.innerHTML = `<span class="spin" aria-hidden="true"></span>Saving…`;
    try {
      const r = await api(`/api/library/${it.id}/draft`, { method: "POST", body: { channels: pick } });
      const bad = r.results.filter((x) => !x.ok);
      if (!bad.length) { dlg.close(); toast(r.saved > 1 ? `${r.saved} drafts saved in Buffer` : "Draft saved in Buffer"); loadLibrary(); return; }
      err.textContent = (r.saved ? `${r.saved} saved. ` : "") + bad.map((x) => `${(pre.channels.find((c) => c.channel_id === x.channel_id) || {}).name || x.channel_id}: ${x.error}`).join(" · ");
      err.hidden = false; paint();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; paint(); }
  };
}

$("#libQ").addEventListener("input", () => { clearTimeout(LIB.t); LIB.t = setTimeout(() => { LIB.page = 1; LIB.sel.clear(); loadLibrary(); }, 250); });
$("#libQ").addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#libQ").value) { e.stopPropagation(); $("#libQ").value = ""; LIB.page = 1; loadLibrary(); } });
$("#libAddBtn").onclick = (e) => { e.stopPropagation(); toggleMenu($("#libAddBtn"), $("#libAddMenu")); };
$("#libUpBtn").onclick = () => { closeMenus(); $("#libFile").click(); };
$("#libSendBtn").addEventListener("click", () => closeMenus());
$("#libFile").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const max = (LIB.data && LIB.data.file_max) || 100 * 1048576;
  if (f.size > max) { e.target.value = ""; return toast(`Files up to ${Math.round(max / 1048576)} MB`); }
  $("#libUsage").innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading ${esc(f.name)}…`;
  try { await window.hqUploadFile(f); toast("Saved to your Library"); } catch (err) { toast(err.message, 6000); }
  e.target.value = ""; loadLibrary();
};

// From a link: Drive / Dropbox / OneDrive / iCloud share links or a direct link (POST /api/library/import = MCP import_from_link)
$("#libLinkBtn").onclick = () => { closeMenus(); $("#libLinkErr").hidden = true; $("#libLinkUrl").value = ""; $("#libLink").showModal(); $("#libLinkUrl").focus(); };
$("#libLinkCancel").onclick = () => $("#libLink").close();
$("#libLinkForm").onsubmit = async (e) => {
  e.preventDefault();
  const url = $("#libLinkUrl").value.trim(); if (!url) return;
  const go = $("#libLinkGo"); go.disabled = true; go.innerHTML = `<span class="spin" aria-hidden="true"></span>Importing…`;
  try {
    const d = await api("/api/library/import", { method: "POST", body: { url } });
    $("#libLink").close(); toast(`Saved to your Library: ${d.name}`); loadLibrary();
  } catch (err) { $("#libLinkErr").textContent = err.message; $("#libLinkErr").hidden = false; }
  go.disabled = false; go.textContent = "Import";
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
    grid.innerHTML = items.length ? items.map((it) => `<button type="button" class="lib-pick-item" data-id="${esc(it.id)}" data-testid="library-picker-item">${libThumb(it)}<span><b>${esc(it.title || it.name)}</b><small>${esc(KIND_NAME[it.kind])} · ${esc(SRC_NAME[it.source] || "")}</small></span></button>`).join("")
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

libPaintControls();
window.HS_TABS = Object.assign(window.HS_TABS || {}, { library: loadLibrary });
// boot-end redraw of a Library opened while boot loaded (app.js reloadOpenScreen): redraw the open view (Library or Bin)
// from the answer it already has, never a second /api/library or /api/library/bin; a fetch still in flight draws itself.
window.HS_REDRAW = Object.assign(window.HS_REDRAW || {}, { library: () => {
  if (LIB.view === "bin") { if (LIB.binData) binDraw(LIB.binData); } else if (LIB.data) libDraw(LIB.data);
} });
