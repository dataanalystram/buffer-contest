// HuhQueue Library: every upload and every generated image, carousel and Reel (GET /api/library, the same as MCP
// list_library), a full-size lightbox, and the picker Make a post uses to attach or replace one channel's media.
"use strict";
const LIB = { items: [], data: null, t: null, view: "items", sel: new Set(), last: null, bin: null, binPage: 1 };
// "View run" opens in a new tab as /?tab=library&run=<id>: read it before boot rewrites the URL, open it once the Library loads
LIB.deepRun = new URLSearchParams(location.search).get("run");
const libNav = () => (window.HQ_NAV = (window.HQ_NAV || 0) + 1);  // a Library view change counts as navigation (boot keeps it)
const KIND_NAME = { video: "Video", image: "Image", carousel: "Carousel" };
const SRC_NAME = { upload: "Uploaded", take: "Uploaded (One take)", link: "From a link", send: "Sent from a device", generated: "Made by the agent", sample: "Sample" };
const libWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "Sample");

function libThumb(it, cls = "") {
  if (it.kind === "video") {
    if (it.thumb) return `<img class="${cls}" src="${esc(it.thumb)}" alt="" loading="lazy">`;
    const s = it.thumb_src && it.thumb_src.url && window.hqThumb ? window.hqThumb(it.thumb_src.url, it.thumb_src.img || "") : "";
    return s ? `<img class="${cls}" src="${esc(s)}" alt="" loading="lazy" data-fallback="${esc(it.url)}">`
      : `<video class="${cls}" src="${esc(it.url)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>`;
  }
  return `<img class="${cls}" src="${esc(it.thumb || it.url)}" alt="" loading="lazy">`;
}
function libCard(it) {
  const { fmtDur, fmtSize } = window.HQ_FMT;
  const used = it.used_in || [];
  const meta = [KIND_NAME[it.kind] + (it.kind === "carousel" && it.slides ? ` · ${it.slides.length} slides` : ""), fmtSize(it.size)];
  if (it.created) meta.push(libWhen(it.created));
  if (it.duration) meta.splice(1, 0, fmtDur(it.duration));
  const sel = it.read_only ? "" : `<label class="lib-sel" title="Select"><input type="checkbox" data-testid="library-select" aria-label="Select ${esc(it.name)}"${LIB.sel.has(String(it.id)) ? " checked" : ""}></label>`;
  return `<article class="lib-card${LIB.sel.has(String(it.id)) ? " picked" : ""}" data-testid="library-item" data-id="${esc(it.id)}" data-kind="${esc(it.kind)}">${sel}
    <button type="button" class="lib-th" data-act="open" data-testid="library-open" aria-label="Open ${esc(it.name)} full size">${libThumb(it)}${it.kind === "video" ? '<span class="lib-play" aria-hidden="true">▶</span>' : ""}<span class="lib-kind">${esc(KIND_NAME[it.kind])}</span></button>
    <div class="lib-info"><b title="${esc(it.name)}">${esc(it.name)}</b><small>${meta.map(esc).join(" · ")}</small><small class="lib-src">${esc(SRC_NAME[it.source] || it.source)}</small>
      <small class="lib-used" data-testid="library-used">${used.length ? "Used in: " + used.slice(0, 2).map((u) => `<button type="button" class="link" data-run="${esc(u.run_id)}" data-testid="library-used-run">${esc(u.title.slice(0, 48))}</button>`).join(", ") + (used.length > 2 ? ` and ${used.length - 2} more` : "") : "Not used in a post yet"}</small></div>
    <div class="lib-acts">${it.read_only ? `<span class="muted sm">Sample · read-only</span>`
      : `<button type="button" class="btn sm" data-act="use" data-testid="library-use">Use in a post</button><button type="button" class="btn2 sm" data-act="draft" data-testid="library-create-draft">Create draft</button>${it.run_id ? `<a class="btn2 sm" href="/?tab=library&run=${esc(it.run_id)}" data-act="run" data-testid="library-view-run">View run</a>` : ""}<button type="button" class="btn2 sm danger" data-act="del" data-testid="library-delete">Delete</button>`}</div>
  </article>`;
}

async function loadLibrary() {
  if (LIB.view === "bin") return loadBin();
  const grid = $("#libGrid");
  if (LIB.deepRun) { const rid = LIB.deepRun; LIB.deepRun = null; openRunById(rid); return; }
  if (window.LIB_PRESET) {  // Make a post's "View all →" carries its filter (Reels -> Videos, Drafts -> Drafts ...)
    $("#libKind").value = window.LIB_PRESET.kind || ""; $("#libQ").value = ""; LIB.page = 1; window.LIB_PRESET = null;
  }
  if (!LIB.data) grid.innerHTML = `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  try {
    const q = new URLSearchParams({ q: $("#libQ").value.trim(), kind: $("#libKind").value });
    const d = await api("/api/library?" + q); LIB.data = d; LIB.items = d.items;
    const { fmtSize } = window.HQ_FMT;
    $("#libUsage").textContent = d.read_only ? "Sample items · read-only" : `${fmtSize(d.used)} of ${fmtSize(d.quota)} used · files up to ${fmtSize(d.file_max)}`;
    $("#libIns").hidden = !!d.read_only;
    $("#libLinkMax").textContent = fmtSize(d.file_max);
    libBinCount(d.bin_count || 0);
    const per = 12, pages = Math.max(1, Math.ceil(d.items.length / per));
    LIB.page = Math.min(LIB.page || 1, pages);
    grid.innerHTML = d.items.length ? d.items.slice((LIB.page - 1) * per, LIB.page * per).map(libCard).join("")
      : ($("#libQ").value || $("#libKind").value
        ? `<div class="empty" data-testid="library-empty"><b>Nothing matches</b><span>Nothing in your Library fits this filter yet. <button type="button" class="link" data-testid="library-show-all">Show everything</button></span></div>`
        : `<div class="empty" data-testid="library-empty"><b>Your Library is empty</b><span>Upload a photo or video here or in Make a post, or make a post: every image, carousel and Reel the agent makes lands here.</span></div>`);
    const sa = grid.querySelector("[data-testid=library-show-all]");
    if (sa) sa.onclick = () => { $("#libKind").value = ""; $("#libQ").value = ""; LIB.page = 1; loadLibrary(); };
    grid.querySelectorAll(".lib-card").forEach(wireLibCard);
    LIB.pageIds = d.items.slice((LIB.page - 1) * per, LIB.page * per).filter((x) => !x.read_only).map((x) => String(x.id));
    $("#libSelAllWrap").hidden = !LIB.pageIds.length; libSelPaint();
    grid.querySelectorAll(".lib-th img[data-fallback]").forEach((im) => (im.onerror = () => {
      im.outerHTML = `<video src="${esc(im.dataset.fallback)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>`; }));
    window.hqPager && window.hqPager($("#libPager"), { total: d.items.length, page: LIB.page, per, label: "Library pages",
      onPage: (n) => { LIB.page = n; loadLibrary(); grid.scrollIntoView({ behavior: "smooth", block: "start" }); } });
  } catch (e) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
function wireLibCard(el) {
  const it = LIB.items.find((x) => String(x.id) === el.dataset.id); if (!it) return;
  el.querySelector("[data-act=open]").onclick = () => openMedia(libMediaList(it), 0, it.name);
  el.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => openRunById(b.dataset.run)));
  wireSel(el);
  const vr = el.querySelector("[data-act=run]");
  if (vr) vr.onclick = (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; e.preventDefault(); openRunById(it.run_id); };
  const dr = el.querySelector("[data-act=draft]");
  if (dr) dr.onclick = () => openDraft(it);
  const use = el.querySelector("[data-act=use]");
  if (use) use.onclick = () => { showTab("agent"); setTimeout(() => window.attachToPost({ media_token: it.media_token, name: it.name, kind: it.kind, url: it.url, thumb: it.thumb }), 50); toast("Attached to a new post. Say what to post and press Make it."); };
  // a delete goes to the Trash (7 days): the toast offers Undo, which restores the item and every post's files
  const undoToast = (item, r) => {
    const t = document.getElementById("toast"); if (!t) return toast("Moved to the Bin");
    t.innerHTML = `Moved to the Bin for ${(r && r.in_trash_days) || 7} days. <button type="button" class="linkish" data-testid="lib-undo">Undo</button>`;
    t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 8000);
    const b = t.querySelector("[data-testid=lib-undo]");
    if (b) b.onclick = async () => { try { await api(`/api/library/${item.id}/restore`, { method: "POST", body: {} }); toast("Restored"); loadLibrary(); } catch (e) { toast(e.message, 5000); } };
  };
  const del = el.querySelector("[data-act=del]");
  if (del) del.onclick = async () => {
    const r = await libDeleteDialog([it]); if (!r) return;
    loadLibrary(); if (r.mode === "bin") undoToast(it, r); else toast("Deleted permanently");
  };
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
  grid.querySelectorAll(".lib-card,.lib-bin-card").forEach((c) => { const on = LIB.sel.has(c.dataset.id); c.classList.toggle("picked", on); const b = c.querySelector("[data-testid=library-select]"); if (b) b.checked = on; });
  const ids = LIB.pageIds || [], n = LIB.sel.size, all = $("#libSelAll");
  all.checked = ids.length > 0 && ids.every((k) => LIB.sel.has(k)); all.indeterminate = !all.checked && ids.some((k) => LIB.sel.has(k));
  $("#libBulk").hidden = !n; $("#libBulkN").textContent = `${n} selected`;
  const bin = LIB.view === "bin";
  $("#libBulkBin").hidden = bin; $("#libBulkRestore").hidden = !bin;
}
function libSelClear() { LIB.sel.clear(); LIB.last = null; libSelPaint(); }
$("#libSelAll").onchange = (e) => { (LIB.pageIds || []).forEach((k) => (e.target.checked ? LIB.sel.add(k) : LIB.sel.delete(k))); libSelPaint(); };
$("#libBulkCancel").onclick = libSelClear;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && LIB.sel.size && !document.querySelector("dialog[open]")) libSelClear(); });
const selItems = () => [...LIB.sel].map((k) => (LIB.view === "bin" ? (LIB.bin || []) : LIB.items).find((x) => String(x.id) === k) || { id: k, name: "item" });
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const refusedNote = (rf) => (!rf || !rf.length ? "" : rf.length === 1 ? rf[0].error : `${plural(rf.length, "item")} not deleted: ${rf[0].error}`);
$("#libBulkBin").onclick = async () => {
  const r = await libDeleteDialog(selItems(), "bin"); if (!r) return;
  libSelClear(); loadLibrary(); toast(`${plural(r.deleted.length, "item")} moved to the Bin` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000);
};
$("#libBulkPerm").onclick = async () => {
  const items = selItems();
  if (LIB.view === "bin") {
    if (!(await hqConfirm(`Delete ${plural(items.length, "item")} permanently? The files are removed now. This can't be undone.`, { ok: "Delete permanently", danger: true }))) return;
    try { const r = await api("/api/library/bin/purge", { method: "POST", body: { ids: items.map((x) => +x.id) } }); libSelClear(); loadBin(); toast(`${plural(r.purged.length, "item")} deleted permanently` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000); }
    catch (e) { toast(e.message, 6000); }
    return;
  }
  const r = await libDeleteDialog(items, "permanent"); if (!r) return;
  libSelClear(); loadLibrary(); toast(`${plural(r.deleted.length, "item")} deleted permanently` + (r.refused.length ? ` · ${refusedNote(r.refused)}` : ""), 6000);
};
$("#libBulkRestore").onclick = async () => {
  try { const r = await api("/api/library/bin/restore", { method: "POST", body: { ids: selItems().map((x) => +x.id) } }); libSelClear(); loadBin(); toast(`${plural(r.restored.length, "item")} restored`); }
  catch (e) { toast(e.message, 6000); }
};

// ---------------------------------------------------------------- the delete choice: Move to Bin (default) or Delete permanently
function libDeleteDialog(items, preset) {
  return new Promise((done) => {
    const dlg = $("#libDel"), one = items.length === 1;
    const used = items.flatMap((x) => (x.used_in || []).filter((u) => !u.sent || u.scheduled));
    $("#libDelTitle").textContent = one ? `Delete “${items[0].name}”?` : `Delete ${plural(items.length, "item")}?`;
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
  if (LIB.view === v) return; LIB.view = v; libNav(); LIB.sel.clear(); LIB.last = null;
  const bin = v === "bin";
  $("#libViewItems").classList.toggle("on", !bin); $("#libViewBin").classList.toggle("on", bin);
  $("#libViewItems").setAttribute("aria-pressed", String(!bin)); $("#libViewBin").setAttribute("aria-pressed", String(bin));
  $("#libBinNote").hidden = !bin; $("#libEmptyBin").hidden = true;
  document.querySelector("[data-testid=library-bar]").hidden = bin;
  loadLibrary();
}
$("#libViewItems").onclick = () => libSetView("items");
$("#libViewBin").onclick = () => libSetView("bin");
const leftLabel = (n) => (n <= 0 ? "Removed for good today" : n === 1 ? "1 day left" : `${n} days left`);
function binCard(it) {
  const { fmtSize } = window.HQ_FMT;
  const th = it.kind === "video" ? `<video src="${esc(it.url)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(it.thumb || it.url)}" alt="" loading="lazy">`;
  return `<article class="lib-card lib-bin-card${LIB.sel.has(String(it.id)) ? " picked" : ""}" data-testid="bin-item" data-id="${esc(it.id)}">
    <label class="lib-sel" title="Select"><input type="checkbox" data-testid="library-select" aria-label="Select ${esc(it.name)}"${LIB.sel.has(String(it.id)) ? " checked" : ""}></label>
    <div class="lib-th lib-th-bin">${th}<span class="lib-kind">${esc(KIND_NAME[it.kind] || it.kind)}</span><span class="lib-left${it.days_left <= 1 ? " soon" : ""}" data-testid="bin-days-left">${esc(leftLabel(it.days_left))}</span></div>
    <div class="lib-info"><b title="${esc(it.name)}">${esc(it.name)}</b><small>${esc(fmtSize(it.size))} · deleted ${esc(libWhen(it.deleted_at))}</small>
      ${it.scheduled_use ? `<small class="lib-warn">Used in a scheduled post</small>` : ""}</div>
    <div class="lib-acts"><button type="button" class="btn sm" data-act="restore" data-testid="bin-restore">Restore</button><button type="button" class="btn2 sm danger" data-act="purge" data-testid="bin-delete-permanently">Delete permanently</button></div>
  </article>`;
}
async function loadBin() {
  const grid = $("#libGrid");
  if (!LIB.bin) grid.innerHTML = `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  try {
    const d = await api("/api/library/bin"); LIB.bin = d.items; libBinCount(d.total);
    const per = 12, pages = Math.max(1, Math.ceil(d.items.length / per));
    LIB.binPage = Math.min(LIB.binPage || 1, pages);
    const page = d.items.slice((LIB.binPage - 1) * per, LIB.binPage * per);
    LIB.pageIds = page.map((x) => String(x.id));
    $("#libSelAllWrap").hidden = !page.length; $("#libEmptyBin").hidden = !d.items.length;
    grid.innerHTML = page.length ? page.map(binCard).join("")
      : `<div class="empty" data-testid="bin-empty"><b>The Bin is empty</b><span>Items you move to the Bin stay here for ${d.days} days, then they're removed for good.</span></div>`;
    grid.querySelectorAll(".lib-bin-card").forEach((el) => {
      const it = d.items.find((x) => String(x.id) === el.dataset.id); wireSel(el);
      el.querySelector("[data-act=restore]").onclick = async () => { try { await api(`/api/library/${it.id}/restore`, { method: "POST", body: {} }); LIB.sel.delete(String(it.id)); toast("Restored to your Library"); loadBin(); } catch (e) { toast(e.message, 5000); } };
      el.querySelector("[data-act=purge]").onclick = async () => {
        if (!(await hqConfirm(`Delete “${it.name}” permanently? The file is removed now. This can't be undone.`, { ok: "Delete permanently", danger: true }))) return;
        try { const r = await api("/api/library/bin/purge", { method: "POST", body: { ids: [it.id] } }); LIB.sel.delete(String(it.id)); if (r.refused.length) toast(r.refused[0].error, 7000); else toast("Deleted permanently"); loadBin(); } catch (e) { toast(e.message, 5000); }
      };
    });
    libSelPaint();
    if (window.hqPager) window.hqPager($("#libPager"), { total: d.items.length, page: LIB.binPage, per, label: "Bin pages",
      onPage: (n) => { LIB.binPage = n; loadBin(); grid.scrollIntoView({ behavior: "smooth", block: "start" }); } });
  } catch (e) { grid.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
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
  $("#libDraftTitle").textContent = `Create draft: ${it.name}`;
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

// From a link: Drive / Dropbox / OneDrive / iCloud share links or a direct link (POST /api/library/import = MCP import_from_link)
$("#libLinkBtn").onclick = () => { $("#libLinkErr").hidden = true; $("#libLinkUrl").value = ""; $("#libLink").showModal(); $("#libLinkUrl").focus(); };
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
