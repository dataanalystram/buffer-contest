// HuhQueue front end: Make a post (the agent, its timeline, per-channel posts, the reply thread), the Style Ledger, AI engine.
"use strict";
const H = { run: null, poll: null, sel: new Set(), ai: null, upload: null, attach: null };
const STATUS_ICON = { pending: "", running: '<span class="spin" aria-hidden="true"></span>', done: "✓", warn: "!", failed: "×" };
// suggestion chips under the reply box: one box edits any channel, its media or its caption
// relevant to THIS post (owner, 3 Nov 2026: "Redo YouTube at 30s" showed when only Instagram was selected)
// A short reply with no tools ("test", "hi", or the one question asked before there's anything to post): the run has no
// plan, steps or posts, so there's no agent card and nothing to retry (owner, 6 Nov 2026: "Standard plan, written with
// built-in" over the reply, and "Try again with a different angle").
const chatOnly = (r) => { const res = (r && r.result) || {};
  return !!res.chat && ["chat", "ask"].includes(res.chat.kind) && !(res.packages || []).length && !(r.steps || []).length; };
const TPL_Q = "What templates can you use?";
// The template list, answered on the page (an AI app's run has no agent to ask; posting a message there redrew and
// restarted the preview, 6 Nov 2026). Read-only: picking one is the AI app's job ("use the funding template").
let TPL_DLG = null;
async function templateList() {
  if (TPL_DLG) { try { TPL_DLG.close(); } catch (_) {} TPL_DLG.remove(); }
  const dlg = document.createElement("dialog");
  dlg.className = "pl-dialog pc-tpl-dialog"; dlg.setAttribute("aria-labelledby", "hqTplListT"); dlg.setAttribute("data-testid", "tpl-list-dialog");
  dlg.innerHTML = `<div class="pc-tpl-head"><div><h2 id="hqTplListT">Templates you can use</h2><p class="muted sm">Ask your AI app to use one by name, for example “use the Launch template”.</p></div>
    <button type="button" class="icon-btn" data-testid="tpl-list-close" aria-label="Close"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <ul class="tpl-list" data-testid="tpl-list"><li class="muted sm"><span class="spin" aria-hidden="true"></span> Loading templates…</li></ul>`;
  document.body.append(dlg); TPL_DLG = dlg;
  const close = () => { try { dlg.close(); } catch (_) {} dlg.remove(); if (TPL_DLG === dlg) TPL_DLG = null; };
  dlg.querySelector("[data-testid=tpl-list-close]").onclick = close;
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  const ul = dlg.querySelector("[data-testid=tpl-list]");
  try {
    const t = await api("/api/templates");
    if (TPL_DLG !== dlg) return;  // closed or replaced while loading
    ul.innerHTML = (t.templates || []).map((o) => `<li data-testid="tpl-list-item"><b>${esc(o.name || o.id)}</b>${o.about ? ` <span class="muted sm">${esc(o.about)}</span>` : ""}${o.custom ? ` <small class="muted">Yours</small>` : ""}</li>`).join("") ||
      `<li class="muted sm">No templates.</li>`;
  } catch (e) { if (TPL_DLG === dlg) ul.innerHTML = `<li class="st warn">${esc(e.message)}</li>`; }
}
window.hqTemplateList = templateList;
function replyChips(r) {
  const pk = (r.result || {}).packages || [], svc = new Set(pk.map((p) => p.service)), fm = new Set(pk.map((p) => p.format));
  if (chatOnly(r)) return [];
  if (!pk.length) return ["Try again with a different angle", TPL_Q];
  const out = ["Make the hook stronger", "Shorter captions"];
  if (fm.has("carousel")) out.push("Restyle slide 2");
  if (fm.has("reel")) out.push("Add captions", "Trim the first 3 s");
  if (svc.has("youtube")) out.push("Redo YouTube at 30s");
  if (svc.has("linkedin")) out.push("LinkedIn more formal");
  out.push(TPL_Q);
  return out.slice(0, 6);
}
// agent replies are markdown: escaped first, then **bold**, *italic*, `code`, [links](https://...) and "- " lists. Nothing
// else becomes HTML (owner, 3 Nov 2026: replies showed a raw **Question:**)
// A bare exception class is never shown (4 Nov 2026): runs saved before the fix still say "Unexpected error: AttributeError"
const BARE_ERR = /^\s*Unexpected error:\s*[A-Za-z_][\w.]*\s*$/;
const PLAIN_ERR = "Something went wrong on my side, so I stopped. What's made so far is kept. Press Retry to try again.";
function plainErr(t) { return BARE_ERR.test(String(t || "")) ? PLAIN_ERR : t; }
window.hqPlainErr = plainErr;
function md(t) {
  const inline = (x) => x.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"'<>]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  let html = "", list = false;
  for (const ln of esc(t || "").split("\n")) {
    const m = ln.match(/^\s*[-*•]\s+(.*)$/);
    if (m) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(m[1])}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    const h = ln.match(/^\s*#{1,4}\s+(.*)$/);
    html += h ? `<b class="md-h">${inline(h[1])}</b><br>` : (ln.trim() ? inline(ln) + "<br>" : "<br>");
  }
  if (list) html += "</ul>";
  return html.replace(/(<br>)+$/, "");
}
window.hqMd = md;
const BUSY = (st) => ["running", "rendering"].includes(st);
// what each send option does, in plain words (owner: "Ready for Buffer" / "Buffer draft" confused him)
const PKG_MODES = [["draft", "Save as Buffer draft (not posted)", "Goes to your Buffer drafts. Nothing is posted until you publish it there.", "Save draft"],
  ["queue", "Add to queue", "Buffer posts it at the channel's next open time slot.", "Add to queue"],
  ["schedule", "Schedule…", "Pick a day and time; Buffer posts it then.", "Schedule"]];
const fmtWhen = (iso) => (iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const isVid = (u) => /\.(mp4|mov|m4v|webm)(\?|$)/i.test(u || "");
const fmtDur = (s) => { if (!s && s !== 0) return ""; s = Math.round(+s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtSize = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
window.HQ_FMT = { fmtDur, fmtSize, isVid };

window.HS_TABS = Object.assign(window.HS_TABS || {}, { agent: loadAgent, ai: loadAI });
// a boot that finished after the agent screen (or a run) opened: redraw the channel chips only, the thread stays
window.HS_REDRAW = Object.assign(window.HS_REDRAW || {}, { agent: () => agentChips() });

// ---------------------------------------------------------------- agent composer: one prompt, an optional file of yours
function countPrompt() { const n = $("#agQuery").value.length; $("#agCount").textContent = `${n.toLocaleString()} / 4,000`; $("#agCount").classList.toggle("over", n > 3800); }
$("#agQuery").addEventListener("input", countPrompt);

function agentChips() {
  const box = $("#agChips");
  if (!H.sel.size) defaultChannels().forEach((c) => H.sel.add(c.id));
  box.innerHTML = "";
  for (const c of S.channels) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "chip svc-" + c.service + (H.sel.has(c.id) ? " on" : "") + (c.isDisconnected ? " dis" : "");
    el.setAttribute("data-testid", "agent-chip"); el.setAttribute("aria-pressed", H.sel.has(c.id));
    el.innerHTML = `${avatar(c)}<span>${esc(c.displayName || c.name)}</span><small>${esc(netName(c.service))}</small>`;
    el.onclick = () => { if (c.isDisconnected) return; H.sel.has(c.id) ? H.sel.delete(c.id) : H.sel.add(c.id); el.classList.toggle("on"); el.setAttribute("aria-pressed", H.sel.has(c.id)); };
    box.appendChild(el);
  }
  const add = document.createElement("button");  // connect another channel (Settings → Connected channels)
  add.type = "button"; add.className = "chip mp-add"; add.dataset.go = "settings"; add.setAttribute("data-testid", "mp-add-channel");
  add.setAttribute("aria-label", "Connect another channel"); add.textContent = "+";
  box.appendChild(add);
}

const drop = $("#drop");
["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
drop.addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) pickFile(ev.dataTransfer.files[0]); });
$("#agFile").onchange = (e) => e.target.files[0] && pickFile(e.target.files[0]);
const UPLOAD_MAX = () => (S.session?.demo ? 8 : 100) * 1048576;  // the Library's limit (100 MB a file)
function pickFile(f) {
  if (f.size > UPLOAD_MAX()) return toast(`Files up to ${UPLOAD_MAX() / 1048576} MB here`);
  H.upload = f; H.attach = null; drawAttached();
}
function drawAttached() {
  const box = $("#agAttached"), f = H.upload, a = H.attach;
  box.hidden = !f && !a; drop.hidden = !!(f || a);
  if (!f && !a) { box.innerHTML = ""; return; }
  const name = f ? f.name : a.name, vid = f ? /^video\//.test(f.type) : a.kind === "video";
  const src = f ? URL.createObjectURL(f) : (a.thumb || a.url);
  const thumb = vid ? `<video src="${esc(f ? src : a.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(src)}" alt="">`;
  box.innerHTML = `${thumb}<span class="att-txt"><b>${esc(name)}</b><small>${vid ? "Video" : "Photo"}${f ? " · " + fmtSize(f.size) : " · from your Library"} · the agent reads it first</small></span>
    <button type="button" class="link" data-testid="agent-attached-remove">Remove</button>`;
  box.querySelector("[data-testid=agent-attached-remove]").onclick = () => { H.upload = null; H.attach = null; $("#agFile").value = ""; drawAttached(); };
}
window.attachToPost = (item) => { H.upload = null; H.attach = item; drawAttached(); $("#agQuery").focus(); };
$("#agLib").onclick = () => window.pickFromLibrary && window.pickFromLibrary((item) => window.attachToPost(item), { title: "Attach from your Library" });
const readB64 = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

// a file of yours -> a media token: a real account keeps it in its Library (raw upload, sniffed and capped on the server);
// the sample workspace keeps it with the run only
async function uploadFile(f, source = "upload") {
  if (S.session?.demo) return (await api("/api/media", { method: "POST", body: { name: f.name, data: await readB64(f), source } })).token;
  const r = await fetch(`/api/library/upload?name=${encodeURIComponent(f.name)}&source=${source}`, { method: "POST", credentials: "same-origin",
    headers: { "X-HQ-CSRF": S.csrf, "Content-Type": f.type || "application/octet-stream" }, body: f });
  let j = {}; try { j = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j.media_token;
}
window.hqUploadFile = uploadFile;

// the video theme picker (studio/static/themes.js), mounted minimally; the Make a post redesign re-mounts it
if (window.HQThemes && $("#agTheme")) H.themePicker = window.HQThemes.mount($("#agTheme"), { value: "auto", onChange: (id) => { H.theme = id; } });

$("#agentForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!H.sel.size) return toast("Pick at least one channel");
  const body = { channel_ids: [...H.sel], format: $("#agFormat").value, prompt: $("#agQuery").value.trim(), theme: H.theme || "auto" };
  try { body.add_logo = localStorage.getItem("hq_add_logo") !== "0"; } catch (_) { body.add_logo = true; }
  if (!body.prompt && !H.upload && !H.attach) return toast("Tell the agent what to post, paste a link, or add your photo or video");
  const b = $("#agGo"), label = b.innerHTML; b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Starting…`;
  if (H.theme) body.theme = H.theme;
  try {
    if (H.upload) {
      b.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
      body.media_token = await uploadFile(H.upload); body.media_name = H.upload.name;
    } else if (H.attach) { body.media_token = H.attach.media_token; body.media_name = H.attach.name; }
    await addKeyframes(body);
    const run = await startRun(body);
    if (!run) return;
    if (run.kind === "take") {  // a split request: answered in the chat thread, clips picked in One take
      if (window.mpOnTake) window.mpOnTake(run, body.prompt);
      else { toast("Splitting the video into clips. Pick the ones you want when they're ready.", 5000); if (window.openTake) window.openTake(run.id); }
      return;
    }
    showRun(run); watch(run.id);
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; b.innerHTML = label; }
});

// the agent sees the video, not only hears it: 4 keyframes from the browser (keyframes.js), sent with the run
async function addKeyframes(body) {
  const vid = H.upload ? /^video\//.test(H.upload.type) : H.attach && H.attach.kind === "video";
  if (!vid || !window.hqKeyframes) return;
  const kf = await window.hqKeyframes(H.upload || H.attach.url);
  if (kf.frames.length) body.keyframes = kf.frames;
  if (kf.duration) body.media_duration = Math.round(kf.duration * 100) / 100;
}
window.hqAddKeyframes = addKeyframes;

// The open thread's live refresh while its run works (owner ask, 5 Nov 2026). Each poll sends the last ETag and an
// unchanged busy run answers 304 with no body, read from md5s on the server (house.run_tag, no run text: no DB egress).
// Unchanged answers back off (1.4 s up to 10 s); a change draws at once and resets the pace. A hidden tab asks nothing at
// all and catches up the moment it is shown (HQLoad.wake). A newer watcher, another thread, leaving the thread or the
// screen stops it (stopWatch): the request in flight is aborted (AbortController), so a late answer never draws.
const POLL_MIN = 1400, POLL_MAX = 10000;
async function runPoll(id, tag, signal) {
  const o = { headers: { "Content-Type": "application/json" }, credentials: "same-origin", cache: "no-store", signal };
  if (tag) o.headers["If-None-Match"] = tag;
  const r = await fetch(`/api/agent/runs/${encodeURIComponent(id)}`, o);
  if (r.status === 304) return { same: true, tag };
  let j = null; try { j = await r.json(); } catch (_) { j = {}; }
  if (!r.ok) { const e = new Error(j.error || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return { same: false, run: j, tag: r.headers.get("ETag") || "" };
}
function stopWatch() {
  clearTimeout(H.poll); H.watching = null;
  if (H.unwake) { H.unwake(); H.unwake = null; }
  if (H.abort) { H.abort.abort(); H.abort = null; }
  if (H.unvis) { H.unvis(); H.unvis = null; }
}
function watch(id) {
  // keeps polling through a failed request (a deploy, a cold start): one error used to stop the UI at "Write captions"
  stopWatch(); H.watching = id;
  const gen = H.gen = (H.gen || 0) + 1, mine = () => H.watching === id && H.gen === gen;  // even for the same run id
  const ac = H.abort = new AbortController();
  let errs = 0, tag = "", pace = POLL_MIN, inflight = false, again = false;
  const wakeUp = () => HQLoad.wake(() => { if (!mine()) return; clearTimeout(H.poll); tick(); });
  // a hidden tab never polls: it waits for the tab to be shown again (loading.js wake), then catches up at once
  const later = (ms) => {
    if (H.unwake) H.unwake();
    H.unwake = null;
    if (document.hidden) { H.unwake = wakeUp(); return; }
    const d = window.HQLoad && HQLoad.pollMs ? HQLoad.pollMs(ms) : ms;
    H.poll = setTimeout(tick, d);
    H.unwake = d > ms ? wakeUp() : null;
  };
  const tick = async () => {
    if (!mine()) return;  // another thread was picked since (chat.js stops this poll), or this run is watched afresh
    if (H.unwake) { H.unwake(); H.unwake = null; }
    if (document.hidden) return later(POLL_MIN);  // no request while hidden
    // one request at a time (CodeRabbit, #124): a wake while one is in flight runs right after it, never alongside
    if (inflight) { again = true; return; }
    inflight = true;
    try {
      const got = await runPoll(id, tag, ac.signal); errs = 0;
      if (!mine()) return;  // the answer landed after another thread was picked: never drawn over it
      if (got.same) { pace = Math.min(POLL_MAX, Math.round(pace * 1.6)); return later(pace); }  // unchanged: back off
      const r = got.run; tag = got.tag; pace = POLL_MIN;
      showRun(r);
      if (["running", "rendering"].includes(r.status)) later(1400);
      else { if (H.watching === id) { H.watching = null; H.abort = null; if (H.unvis) { H.unvis(); H.unvis = null; } } loadRuns(); readyBadge(); }
    } catch (e) {
      if (!mine()) return;  // a newer watcher owns the poll now: a late failure never touches its timer or wake listener
      if (window.HQLoad && HQLoad.aborted(e)) return;
      errs += 1;
      if (errs === 3) toast("Reconnecting to the agent…");
      later(Math.min(15000, 1500 * 2 ** Math.min(errs, 4)));
    } finally {
      inflight = false;
      if (again && mine()) { again = false; clearTimeout(H.poll); tick(); }  // the one chain continues from here
      again = false;
    }
  };
  // the tab hidden mid-wait: the pending poll is dropped at once (not sent later while hidden) and the wake catches up
  const onVis = () => { if (!mine() || !document.hidden) return; clearTimeout(H.poll); if (H.unwake) H.unwake(); H.unwake = wakeUp(); };
  document.addEventListener("visibilitychange", onVis);
  H.unvis = () => document.removeEventListener("visibilitychange", onVis);
  H.poll = setTimeout(tick, 900);
}
// leaving the thread's screen stops the poll; coming back to a run that is still working starts it again
function resumeWatch() { if (H.run && H.watching == null && ["running", "rendering"].includes(H.run.status) && !$("#tab-agent").hidden) watch(H.run.id); }
window.stopWatch = stopWatch; window.resumeWatch = resumeWatch;
// back on the tab after changing something in Buffer: a finished post re-reads its run, which re-checks Buffer
// (studio/post_sync.py, from the cached post lists), at most every 20 s
let bufSeen = 0;
document.addEventListener("visibilitychange", async () => {
  const r = H.run;
  if (document.hidden || !r || r.kind === "take" || ["running", "rendering"].includes(r.status) || $("#tab-agent").hidden) return;
  const linked = ((r.result || {}).packages || []).some((p) => p.sent || p.buffer_draft);
  if (!linked || Date.now() - bufSeen < 20000) return;
  bufSeen = Date.now();
  try {
    const n = await api(`/api/agent/runs/${r.id}`);
    if (!H.run || H.run.id !== r.id || ["running", "rendering"].includes(H.run.status)) return;
    // redraw only when Buffer changed something, and never under a caption or field the person is typing in
    if (JSON.stringify((n.result || {}).packages || []) === JSON.stringify((H.run.result || {}).packages || [])) return;
    const f = document.activeElement;
    if (f && f.closest && f.closest("#runView") && (f.matches("input, textarea, select") || f.isContentEditable)) return;
    showIfOn(r.id, n);
  } catch (_) { /* the next open re-checks */ }
});

// ---------------------------------------------------------------- run view
function showRun(r) {
  window.HQ_NAV = (window.HQ_NAV || 0) + 1;  // boot() keeps a run the user opened while it loaded (see app.js)
  if (r && !r.thread_id && H.run && H.run.id === r.id && H.run.thread_id) r.thread_id = H.run.thread_id;  // keep the address
  H.run = r; $("#runView").hidden = false;
  window.mpOnRun && window.mpOnRun(r);
  // a step that ran again with nothing new ("Check everything" x4) is one row with its count (the latest result)
  const groups = [];
  for (const s of r.steps) {
    const g = groups.find((x) => x.tool === s.tool && x.label === s.label && (s.tool === "check" || x === groups[groups.length - 1]));
    if (g) { g.n += 1; g.s = s; } else groups.push({ tool: s.tool, label: s.label, n: 1, s });
  }
  $("#timeline").innerHTML = groups.map(({ s, n }) => `<li class="step ${s.status}" data-testid="step" data-key="${s.key}">
      <span class="dot" aria-hidden="true">${STATUS_ICON[s.status] || ""}</span>
      <div><b>${esc(s.label)}${n > 1 ? ` <span class="xn" data-testid="step-count">×${n}</span>` : ""}</b>${s.detail ? `<small>${esc(plainErr(s.detail))}</small>` : ""}${stepExtra(s)}</div></li>`).join("");
  const res = r.result || {}, media = res.media || [], busy = ["running", "rendering"].includes(r.status);
  const pk = res.packages || [];
  let html = "";
  const legacy = ["media", "news"].includes(r.kind);
  if (legacy && !busy) {  // owner P0: a run from the old pipeline is shown as such and remade by the agent, never re-run
    html += `<div class="st warn legacy-run" data-testid="legacy-run"><span><b>Made before the agent (old pipeline).</b> Its video and captions may repeat your request instead of telling the story.</span>
      ${res.superseded_by ? `<button type="button" class="btn2 sm" data-testid="legacy-open-new" data-run="${res.superseded_by}">Open the new version</button>`
        : `<button type="button" class="btn sm" data-testid="legacy-redo">Redo with the agent</button>`}</div>`;
  }
  if (res.upload) {
    const u = res.upload;
    html += `<div class="run-upload" data-testid="run-upload">${u.type === "video" ? `<video src="${esc(u.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(u.url)}" alt="">`}
      <span><b>Your ${esc(u.type)}${u.name ? ": " + esc(u.name) : ""}</b><small>${u.transcript ? "Transcript: “" + esc(u.transcript.slice(0, 160)) + (u.transcript.length > 160 ? "…”" : "”") : esc(u.transcript_note || "The agent reads it first")}</small></span></div>`;
  }
  if (!pk.length && media.length) {  // while the agent works: what it has made so far (each channel's own card follows)
    html += `<div class="strip" data-testid="media-strip">${media.map((m, i) => isVid(m.url) || m.type === "video"
      ? `<video src="${esc(m.url)}" controls playsinline preload="metadata" data-testid="media-video"></video>`
      : `<a href="${escUrl(m.url)}" target="_blank" rel="noopener"><img src="${esc(m.url)}" alt="Slide ${i + 1}" data-testid="media-img"></a>`).join("")}</div>`;
  } else if (busy && !pk.length) html += `<div class="sk-posts" aria-hidden="true" data-testid="posts-skeleton">${'<div class="sk-post"><span class="sk sk-head"></span><span class="sk sk-media"></span><span class="sk sk-line"></span><span class="sk sk-line short"></span></div>'.repeat(2)}</div>`;
  $("#mediaBox").innerHTML = html;
  const redo = $("[data-testid=legacy-redo]");
  if (redo) redo.onclick = async () => { redo.disabled = true;
    try { const n = await api(`/api/agent/runs/${r.id}/messages`, { method: "POST", body: { text: "redo" } }); if (showIfOn(r.id, n)) watch(n.id); loadRuns(); }
    catch (e) { toast(e.message, 5000); redo.disabled = false; } };
  const opn = $("[data-testid=legacy-open-new]");
  if (opn) opn.onclick = async () => { const n = await api(`/api/agent/runs/${opn.dataset.run}`); showRun(n); if (["running", "rendering"].includes(n.status)) watch(n.id); };
  agentExtras(r, busy);
  const ready = ["ready"].includes(r.status) || (!busy && pk.length);
  $("#pkgHead").hidden = !pk.length || !ready;
  $("#pkgHelp").hidden = $("#pkgHead").hidden;
  $("#packages").classList.toggle("multi", !!ready && pk.length > 1);
  $("#packages").innerHTML = ready ? pkgTabs(r, pk) + pk.map(pkgCard).join("") + (pk.some((p) => p.status === "ready")
    ? `<div class="pkg-all"><button class="btn2" data-testid="save-drafts">Save all as Buffer drafts (not posted)</button></div>` : "") : "";
  if (ready) wireTabs();
  const sd = $("[data-testid=save-drafts]");
  if (sd) sd.onclick = async () => {
    sd.disabled = true;
    try { const out = await api(`/api/agent/runs/${r.id}/tools/save_buffer_drafts`, { method: "POST", body: {} });
      toast(out.drafts.map((d) => `${netName(d.service)}: ${d.status.replaceAll("_", " ")}`).join(" · "), 6000); showIfOn(r.id, await api(`/api/agent/runs/${r.id}`)); readyBadge(); }
    catch (e) { toast(e.message, 6000); sd.disabled = false; }
  };
  wirePackages(r);
  if (window.hqAsks) window.hqAsks(r);  // "What you asked" card, missing-channel tabs, hook / credit chips (asks.js)
}

function agentExtras(r, busy) {
  const res = r.result || {}, br = res.brain || {}, agent = r.kind === "agent" || br.mode === "mcp", quiet = !busy && chatOnly(r);
  const card = document.querySelector("[data-testid=mp-agent-msg]");
  if (card) card.hidden = quiet;  // a short reply: just the reply, no empty agent card above it
  $("#agentBrain").hidden = !agent || !br.mode || quiet;
  if (agent && br.mode && !quiet) {
    // the same words in the run view and the composer, after a reply too (QA: "free starter" showed while Claude ran)
    $("#agentBrain").textContent = br.mode === "mcp" ? `Made in ${brainName(br)}. ${br.hint || ""}`.trim() : (br.mode === "tools" ? "Planned and run by " : "Standard plan, written with ") + brainName(br) +
      ((res.notes || []).length ? " · " + res.notes[res.notes.length - 1] : "");
    if (br.mode !== "mcp") $("#agEngine").textContent = "Writing with: " + brainName(br);  // a reply here runs our engine, not the app
  }
  const plan = res.plan || [];
  $("#agentPlan").hidden = !plan.length;
  $("#agentPlan").innerHTML = plan.map((p) => `<li>${esc(p)}</li>`).join("");
  const th = (res.thread || []).slice(1);
  $("#agentThread").innerHTML = th.map((m) => `<div class="msg ${m.role}" data-testid="thread-msg"><b>${m.role === "user" ? "You" : "Agent"}</b><span class="md">${m.role === "user" ? esc(m.text) : md(plainErr(m.text))}</span></div>`).join("") +
    // something broke on our side: a plain sentence (in the thread) and a Retry button that sends the same words again
    (r.status === "failed" && !busy && (res.error || BARE_ERR.test(((th[th.length - 1] || {}).text) || "")) ? `<div class="st warn ask-card" data-testid="chat-retry-card">
      <span>That didn't finish.${res.error && res.error.ref ? ` Reference <code>${esc(res.error.ref)}</code>.` : ""}</span>
      <div class="row"><button type="button" class="btn sm" data-testid="chat-retry-run">Retry</button></div></div>` : "") +
    // someone else's video: ONE question, answered with a button (never typed twice)
    (res.rights_question && !res.recap && !(res.rights || {}).mode && !busy ? `<div class="st warn ask-card" role="group" aria-label="Video rights" data-testid="rights-card">
      <span class="md">${md(res.rights_question)}</span><div class="row"><button type="button" class="btn sm" data-testid="rights-yes" data-say="Yes, I have the rights to repost it with credit">Yes, I have the rights</button>
      <button type="button" class="btn2 sm" data-testid="rights-slides" data-say="Use slides instead">Use slides instead</button></div></div>` : "") +
    (r.status === "failed" && res.stopped && !busy ? `<div class="st warn ask-card" data-testid="chat-stopped"><span>Stopped, as you asked. What's made so far is kept.</span>
      <div class="row"><button type="button" class="btn sm" data-testid="mp-resume">Resume</button></div></div>` : "") +
    (r.status === "interrupted" ? `<div class="st warn ask-card" data-testid="interrupted"><span>The server restarted while I was working${(res.interrupted || {}).after ? ` (after “${esc(res.interrupted.after)}”)` : ""}. What's made so far is kept.</span>
      <div class="row"><button type="button" class="btn sm" data-testid="mp-resume">Resume</button></div></div>` : "") +
    (window.hqAskCard ? window.hqAskCard(res, busy) : "") +
    ((res.conflicts || []).length ? `<div class="st warn" data-testid="agent-question">${esc(res.conflicts[0].question)}</div>` : "") +
    // the weekly recap's one rights question, answered inline: yes, title cards, or type the story numbers below
    (res.recap && res.recap.awaiting_rights && res.rights_question ? `<div class="st warn" data-testid="recap-rights">${esc(res.rights_question)}
      <div class="row"><button type="button" class="btn primary sm" data-testid="recap-rights-yes" data-say="Yes, use them with credit">Use them with credit</button>
      <button type="button" class="btn ghost sm" data-testid="recap-rights-no" data-say="No, title cards instead">Title cards instead</button></div></div>` : "");
  // only the answer buttons (the card itself is data-testid=rights-card: a click on it must not send "undefined")
  // the rights / Resume card can come before the first reply: show the thread whenever it has anything in it
  $("#agentThread").hidden = !$("#agentThread").innerHTML.trim();
  if (window.hqAskCard) window.hqAskCard.wire($("#agentThread"), r);  // Keep Reel / Switch, which kit, which track
  $("#agentThread").querySelectorAll("button[data-say][data-testid^=recap-rights-],button[data-say][data-testid^=rights-]").forEach((b) => (b.onclick = () => { $("#replyIn").value = b.dataset.say; $("#replyForm").requestSubmit(); }));
  const rt = $("#agentThread [data-testid=chat-retry-run]");
  if (rt) rt.onclick = async () => { rt.disabled = true;
    // an old run saved before res.error existed: repeat the last thing you asked (resume when there was no reply yet)
    const lastAsk = th.length > 1 ? ([...th].reverse().find((m) => m.role === "user") || {}).text : "";
    const again = (((res.error || {}).retry_text) || (res.error ? "" : lastAsk) || "").trim();
    try { const n = again ? await api(`/api/agent/runs/${r.id}/messages`, { method: "POST", body: { text: again } })
      : await api(`/api/agent/runs/${r.id}/resume`, { method: "POST", body: {} }); if (showIfOn(r.id, n)) watch(n.id); }
    catch (e) { toast(e.message, 5000); rt.disabled = false; } };
  const rs = $("#agentThread [data-testid=mp-resume]");
  if (rs) rs.onclick = async () => { rs.disabled = true;
    try { const n = await api(`/api/agent/runs/${r.id}/resume`, { method: "POST", body: {} }); if (showIfOn(r.id, n)) watch(n.id); } catch (e) { toast(e.message, 5000); rs.disabled = false; } };
  // VIDEO FIRST: the launch video can't be fetched here (YouTube only) or there is none -> an upload button in the chat
  const ask = res.video_ask;
  if (ask && !busy && !(res.upload && res.upload.type === "video")) {
    const box = document.createElement("div");
    box.className = "st warn video-ask"; box.dataset.testid = "video-ask"; box.setAttribute("data-testid", "video-ask");
    box.innerHTML = `<span data-testid="video-ask-text">${esc(ask.text)}</span>
      <span class="row"><label class="btn sm primary va-up"><input type="file" accept="${esc(ask.accept || "video/mp4,video/quicktime")}" class="sr" data-testid="video-ask-file">Upload video</label>
      ${ask.launch_url ? `<a class="link" href="${escUrl(ask.launch_url)}" target="_blank" rel="noopener" data-testid="video-ask-open">Open the launch video</a>` : ""}
      <small class="muted">${esc(ask.label || "MP4/MOV")}, up to ${Number(ask.max_mb) || 100} MB · or use Upload video below</small></span>`;
    $("#agentThread").appendChild(box);
    $("#agentThread").hidden = false;
    const inp = box.querySelector("[data-testid=video-ask-file]");
    inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return;
      if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v)$/i.test(f.name)) return toast("Upload an MP4 or MOV video");
      if (f.size > (Number(ask.max_mb) || 100) * 1048576) return toast(`Videos up to ${Number(ask.max_mb) || 100} MB here`);
      const lab = box.querySelector(".va-up"); lab.classList.add("busy"); lab.lastChild.textContent = "Uploading…";
      try {
        const tok = await uploadFile(f);
        const n = await api(`/api/agent/runs/${r.id}/messages`, { method: "POST", body: { text: "Here's the video: " + f.name, media_token: tok } });
        if (showIfOn(r.id, n)) watch(n.id);
      } catch (e) { toast(e.message, 6000); lab.classList.remove("busy"); lab.lastChild.textContent = "Upload video"; }
    };
  }
  // one edit box for every run, after it finishes (also older runs: the server routes it)
  $("#replyForm").hidden = busy;
  $("#replyChips").innerHTML = replyChips(r).map((q) => `<button type="button" class="chip mini" data-testid="reply-chip" data-say="${esc(q)}">${esc(q)}</button>`).join("");
  $("#replyChips").querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => {
    if (b.dataset.say === TPL_Q && r.kind !== "agent") return void templateList();  // answered here: no message, no redraw
    $("#replyIn").value = b.dataset.say; $("#replyForm").requestSubmit();
  }));
  drawReady(r, busy);
  drawTemplates(r, busy);
  // "where is the post" / "give me an option to upload": the answer points at the thing on the page
  const f = res.focus, fk = f ? `${r.id}:${f.at}` : "";
  if (H.focusRun !== r.id) { H.focusRun = r.id; H.focusSeen = fk; }
  else if (fk && H.focusSeen !== fk && !busy) { H.focusSeen = fk; setTimeout(() => focusOn(f.target), 150); }
}

function flash(els) { els.forEach((el) => { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 2600); }); }
function focusOn(target) {
  if (target === "packages") { const p = $("#packages"); p.scrollIntoView({ behavior: "smooth", block: "start" }); flash([...p.querySelectorAll(".pkg")]); }
  else if (target === "upload") { const u = $("#replyUpload"); u.scrollIntoView({ behavior: "smooth", block: "center" }); flash([u]); u.querySelector("input").focus(); }
  else if (target === "templates") { const t = $("#mpTpl"); if (!t.hidden) { t.scrollIntoView({ behavior: "smooth", block: "center" }); flash([t]); } }
}
window.hqFocusOn = focusOn;

// the final state at the top: what's ready per channel, with its media
function drawReady(r, busy) {
  const box = $("#mpReady"); if (!box) return;
  const pk = (r.result || {}).packages || [];
  if (!pk.length || busy) { box.hidden = true; box.innerHTML = ""; return; }
  const word = (p) => (p.sent ? (p.status === "pending_approval" ? "Waiting for approval" : BUF_WORD[p.sent.buffer_status] || "In Buffer") : p.status === "hold" ? "On hold" : p.changed_since_sent ? "Changed since saved" : p.buffer_sync && p.buffer_sync.event === "deleted" ? "Deleted in Buffer" : "Ready");
  box.hidden = false;
  box.innerHTML = `<span class="mp-ready-h">Your posts</span>` + pk.map((p) => { const f = pkgFiles(p)[0];
    const th = f ? (f.type === "video" ? `<video src="${escUrl(f.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${escUrl(f.url)}" alt="">`) : `<span class="mp-ready-ph" aria-hidden="true"></span>`;
    return `<button type="button" class="mp-ready-i st-${esc(p.sent ? "sent" : p.status)}" data-ch="${esc(p.channel_id)}" data-testid="mp-ready-item" aria-label="Go to the ${esc(netName(p.service))} post">${th}<span><b>${esc(netName(p.service))}</b><small data-testid="mp-ready-meta">${esc(readyLabel(p))} · ${word(p)}</small></span></button>`; }).join("");
  // a video's ratio and length come from the thumbnail's own metadata when the server didn't record them (never "… · …")
  box.querySelectorAll("video").forEach((v) => { const set = () => { const sm = v.closest(".mp-ready-i")?.querySelector("small"); if (!sm || !v.videoWidth) return;
      const parts = sm.textContent.split(" · "); if (parts[0] !== "Video" || /\d+:\d+/.test(parts[1] || "")) return;
      const extra = [ratioOf(v.videoWidth, v.videoHeight), isFinite(v.duration) ? fmtDur(v.duration) : ""].filter(Boolean);
      sm.textContent = [parts[0], ...extra, ...parts.slice(1)].join(" · "); };
    v.addEventListener("loadedmetadata", set); if (v.readyState >= 1) set(); });
  box.querySelectorAll("[data-ch]").forEach((b) => (b.onclick = () => { pkgSelect(b.dataset.ch); const c = document.querySelector(`#packages .pkg[data-ch="${CSS.escape(b.dataset.ch)}"]`); if (c) { c.scrollIntoView({ behavior: "smooth", block: "start" }); flash([c]); } }));
}

// "Template: <name> · why" and the Change template chips (previews drawn from this post's own facts in its kit)
async function drawTemplates(r, busy) {
  const box = $("#mpTpl"); if (!box) return;
  const res = r.result || {}, pk = res.packages || [], tp = res.template;
  if (!tp || !pk.length || busy || r.kind !== "agent" || res.recap) { box.hidden = true; return; }
  const sl = Object.entries(tp.slide_names || {}).map(([i, n]) => `slide ${esc(i)}: ${esc(n)}`).join(" · ");
  const slides = pk.some((p) => ["carousel", "photo"].includes(p.format));
  const line = `<p class="mp-tpl-line" data-testid="mp-template-line"><span class="muted">Template:</span> <b data-testid="mp-template-name">${esc(tp.name)}</b>
    <span class="muted">· <span data-testid="mp-template-why">${esc(tp.why)}</span>${tp.kit ? ` · in your ${esc(tp.kit)} kit` : ""}${sl ? " · " + sl : ""}</span>
    ${slides ? "" : `<small class="muted sm">Your posts are videos; pick a template to make the image channels slides.</small>`}</p>`;
  const key = [r.id, tp.id, JSON.stringify(tp.slide_names || {}), pk.map((p) => (p.media_urls || [])[0]).join()].join("|");
  box.hidden = false;
  if (H.tplKey === key && H.tplHtml) { box.innerHTML = H.tplHtml; wireTpl(r); return; }
  H.tplKey = key;
  box.innerHTML = line + `<div class="mp-tpl-row" data-testid="mp-template-chips"><span class="mp-tpl-h">Change template</span><span class="muted sm">Drawing previews…</span></div>`;
  let t;
  try { t = await api(`/api/agent/runs/${r.id}/templates`); } catch (_) { t = { options: [] }; }
  if (H.tplKey !== key) return;
  H.tplHtml = line + `<div class="mp-tpl-row" role="group" aria-label="Change template" data-testid="mp-template-chips"><span class="mp-tpl-h">Change template</span>
    ${(t.options || []).map((o) => `<button type="button" class="mp-tpl${o.current ? " on" : ""}" data-tpl="${esc(o.id)}" data-testid="mp-template-chip" aria-pressed="${o.current ? "true" : "false"}" title="${esc(o.about || o.name)}" aria-label="${esc(o.name)}${o.current ? " (in use)" : ""}">
      ${o.preview ? `<img src="${escUrl(o.preview)}" alt="" loading="lazy">` : `<span class="mp-tpl-ph" aria-hidden="true"></span>`}<span>${esc(o.name)}</span></button>`).join("")}
    <button type="button" class="mp-tpl more" data-testid="mp-template-all" aria-label="List every template">All templates…</button></div>`;
  box.innerHTML = H.tplHtml; wireTpl(r);
}
function wireTpl(r) {
  const box = $("#mpTpl");
  box.querySelectorAll("[data-tpl]").forEach((b) => (b.onclick = async () => {
    if (b.classList.contains("on")) return;
    box.querySelectorAll("[data-tpl]").forEach((x) => (x.disabled = true)); b.classList.add("busy");
    try { const n = await api(`/api/agent/runs/${r.id}/template`, { method: "POST", body: { template: b.dataset.tpl } }); if (showIfOn(r.id, n)) watch(n.id); }
    catch (e) { toast(e.message, 6000); box.querySelectorAll("[data-tpl]").forEach((x) => (x.disabled = false)); b.classList.remove("busy"); }
  }));
  const all = box.querySelector("[data-testid=mp-template-all]");
  if (all) all.onclick = () => (window.hqAllTemplates ? window.hqAllTemplates(r) : ($("#replyIn").value = "What templates can you use?", $("#replyForm").requestSubmit()));
}

function stepExtra(s) {
  const d = s.data || {};
  if (s.key === "find" && d.leads) return `<ul class="leads">${d.leads.slice(0, 3).map((l) => `<li><a href="${escUrl(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a> <span class="muted">${esc(l.source || "")}</span></li>`).join("")}</ul>`;
  if (s.key === "verify" && d.url) return `<ul class="leads"><li>Read: <a href="${escUrl(d.url)}" target="_blank" rel="noopener">${esc(d.site)}</a></li>${(d.other_outlets || []).map((o) => `<li>Also: <a href="${escUrl(o.url)}" target="_blank" rel="noopener">${esc(o.source)}</a></li>`).join("")}</ul>`;
  if (s.key === "structure" && d.facts) return `<ul class="facts">${d.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
  if (s.tool === "extract_facts" && d.facts) return `<ul class="facts">${d.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
  if (s.tool === "search_news" && d.leads) return `<ul class="leads">${d.leads.slice(0, 3).map((l) => `<li><a href="${escUrl(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a></li>`).join("")}</ul>`;
  if (s.tool === "read_upload" && d.transcript) return `<p class="muted sm">“${esc(d.transcript.slice(0, 200))}${d.transcript.length > 200 ? "…" : ""}”</p>`;
  return "";
}

// the brand check: which rules were checked, open on a click (owner: "2 rules checked" didn't say which)
function checkList(chk) {
  if (!chk) return "";
  const res = chk.results || [], fails = res.filter((x) => !x.pass), passes = res.filter((x) => x.pass);
  const kind = (k) => (k === "visual" ? "Look" : "Words");
  if (!res.length) return `<div class="chk-res ok" data-testid="brand-check"><b>No brand rules yet</b><small class="muted">Teach one in Brand › Style Ledger, or reply “never say …”.</small></div>`;
  return `<div class="chk-res ${chk.pass ? "ok" : "bad"}" data-testid="brand-check"><b>${chk.pass ? "Passes your brand rules" : "Breaks " + fails.length + " rule" + (fails.length > 1 ? "s" : "")}</b>
    ${fails.map((f) => `<span class="rl bad">✕ ${esc(f.rule)}${f.why ? ` <em>${esc(f.why)}</em>` : ""}</span>`).join("")}
    ${passes.length ? `<details data-testid="brand-rules"><summary data-testid="brand-rules-toggle">Show the ${passes.length} rule${passes.length > 1 ? "s" : ""} it passed</summary><ul class="rl-list">${passes.map((f) => `<li class="rl" data-testid="brand-rule"><span class="tag ${f.kind}">${kind(f.kind)}</span> ✓ ${esc(f.rule)}</li>`).join("")}</ul></details>` : ""}</div>`;
}

const FMT_NAME = { reel: "Reel", carousel: "Carousel", photo: "Image", video: "Video" };
// each network's own word for a video post (owner, 6 Nov 2026: every chip said "Reel"). Same table as
// studio/video_names.py: Instagram Reel, Facebook Reel (9:16) or Video, YouTube Short (vertical, up to 3 min) or Video,
// Pinterest Video Pin, everyone else Video. The server sends p.video_name; older posts are worked out here.
const VIDEO_NAMES = { instagram: "Reel", facebook: "Video", youtube: "Video", tiktok: "Video", linkedin: "Video", twitter: "Video", x: "Video", threads: "Video", bluesky: "Video", mastodon: "Video", pinterest: "Video Pin", googlebusiness: "Video" };
function videoName(p, ratio, duration) {
  if (p.video_name) return p.video_name;
  const o = p.options || {}, svc = String(p.service || "").toLowerCase();
  if (svc === "instagram") return o.ig_type === "story" ? "Story" : "Reel";
  if (svc === "facebook") return { story: "Story", reel: "Reel" }[o.fb_type] || "Video";
  if (svc === "youtube") {
    const [w, h] = String(ratio || "").split(":").map(Number), vertical = ratio ? w / h <= 1 : true;
    const secs = Number(duration) || 0;  // an unreadable length doesn't decide it (same as video_names.py)
    return vertical && (!secs || secs <= 180) ? "Short" : "Video";
  }
  return VIDEO_NAMES[svc] || "Video";
}
window.HQ_VIDEO_NAME = videoName;
// one list of files per card, whatever shape the package has (older runs only have media_urls)
// files deleted from the Library (the server's media_missing) are left out, so a card never shows a broken image
const pkgFiles = (p) => { const gone = new Set(p.media_missing || []);
  return (p.media && p.media.length ? p.media : (p.media_urls || []).map((u) => ({ url: u, type: isVid(u) ? "video" : "photo", ratio: p.ratio }))).filter((x) => !gone.has(x.url)); };
const MEDIA_GONE = "This post's media was deleted from your Library, so it can't be sent. Make the post again (or upload the files) to get new media.";
function pkgMissing(p) {
  if (!p.media_state || p.media_state === "ok") return "";
  const n = (p.media_missing || []).length;
  return `<div class="st warn" role="status" data-testid="pkg-media-missing">${p.media_state === "missing" ? esc(MEDIA_GONE)
    : `${n} of this post's files ${n === 1 ? "was" : "were"} deleted from your Library. It can't be sent until you make it again.`}</div>`;
}
const RATIOS = [["9:16", 9 / 16], ["4:5", 0.8], ["1:1", 1], ["16:9", 16 / 9]];
const ratioOf = (w, h) => (w && h ? RATIOS.reduce((b, r) => (Math.abs(r[1] - w / h) < Math.abs(b[1] - w / h) ? r : b))[0] : "");
// owner P0: a video was labelled "Image". A video is always "<network's word> · ratio · length" (Reel, Short, Video); the ratio and length come from the
// file itself when the server didn't record them.
function mediaLabel(p) {
  const m = pkgFiles(p), first = m[0] || {};
  if (!m.length) return p.media_state === "missing" ? "Media deleted" : "Text only";  // owner, 6 Nov 2026: "Image · Ready" on text
  if (first.type === "video" || isVid(first.url)) {
    const r = first.ratio || ratioOf(first.width, first.height);
    const parts = [videoName(p, r, first.duration), r || "…", first.duration ? fmtDur(first.duration) : "…"];
    if (first.role === "original") parts.push("your file");
    return parts.join(" · ");
  }
  const parts = [p.format === "carousel" && m.length > 1 ? `Carousel · ${m.length} slides` : FMT_NAME[p.format] || "Image"];
  if (first.ratio) parts.push(first.ratio);
  return parts.join(" · ");
}
window.HQ_MEDIA_LABEL = mediaLabel;
// the short label for the ready summary: no "…" for a ratio or length that isn't known yet
const readyLabel = (p) => mediaLabel(p).split(" · ").filter((x) => x !== "…").join(" · ");
function pkgMedia(p) {
  const m = pkgFiles(p);
  if (!m.length && p.media_state === "missing") return `<div class="pkg-media empty" data-testid="pkg-media"><span class="muted sm" data-testid="pkg-media-gone">Media deleted</span></div>`;
  if (!m.length) return `<div class="pkg-media empty" data-testid="pkg-media"><span class="muted sm">${p.service === "youtube" || p.service === "tiktok" ? "Needs a video" : "Text only"}</span></div>`;
  const ar = (r) => (r ? `aspect-ratio:${r.replace(":", " / ")}` : "");
  const first = m[0];
  const main = first.type === "video"  // #t: the browser shows a real frame before play, never a black tile
    ? `<video src="${esc(first.url)}#t=0.1" controls playsinline preload="metadata" data-testid="pkg-video" style="${ar(first.ratio)}"></video>`
    : `<button type="button" class="pm-img" data-open="0" data-testid="pkg-media-open" aria-label="Open full size" style="${ar(first.ratio || "4:5")}"><img src="${esc(first.url)}" alt="${esc(netName(p.service))} ${esc(p.format)}, slide 1"></button>`;
  const thumb = (x) => (x.type === "video" ? `<video src="${esc(x.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(x.url)}" alt="">`);
  const strip = m.length > 1 ? `<div class="pm-strip" data-testid="pkg-slides">${m.map((x, i) => `<button type="button" data-open="${i}" data-testid="pkg-slide" aria-label="Slide ${i + 1}">${thumb(x)}</button>`).join("")}</div>` : "";
  // the weekly recap: the same stories as one 9:16 Reel, ready as its own post next to the carousel
  const reel = (p.reel || [])[0];
  const reelBox = reel ? `<div class="pkg-reel" data-testid="pkg-reel"><small class="muted sm">Reel · 9:16${reel.duration ? " · " + fmtDur(reel.duration) : ""} · its own post</small>
      <video src="${esc(reel.url)}#t=0.1" controls playsinline preload="metadata" data-testid="pkg-reel-video" style="aspect-ratio:9 / 16;max-height:360px"></video></div>` : "";
  return `<div class="pkg-media" data-testid="pkg-media">${main}${strip}${reelBox}
    <div class="pm-meta"><span class="pm-fmt" data-testid="pkg-format">${esc(mediaLabel(p))}</span>
      <button type="button" class="link" data-open="0" data-testid="pkg-fullsize">Full size</button></div></div>`;
}

// the branded cover variants (studio/media/covers.py): the chosen one is the Reel's opening frame (its cover on
// Instagram / TikTok); YouTube gets the 1280x720 one as its thumbnail. Click another to swap.
function pkgCovers(p, sent) {
  const cv = p.covers || [];
  if (!cv.length) return "";
  const key = p.service === "youtube" ? "16:9" : (p.ratio === "4:5" ? "4:5" : "9:16");
  const yt = p.service === "youtube" && p.thumbnail;
  return `<div class="pkg-covers" data-testid="pkg-covers"><small class="muted sm">${p.service === "youtube" ? "Thumbnail" : "Cover"} · pick one</small>
    <div class="pc-row">${cv.map((c, i) => `<button type="button" class="pc${i === p.cover_index ? " on" : ""}" data-cover="${i}" data-testid="pkg-cover"
      aria-pressed="${i === p.cover_index}" aria-label="Cover ${i + 1}${i === p.cover_index ? " (in use)" : ""}" ${sent ? "disabled" : ""}
      style="aspect-ratio:${key.replace(":", " / ")}"><img src="${esc((c.urls || {})[key] || Object.values(c.urls || {})[0] || "")}" alt="" loading="lazy"></button>`).join("")}</div>
    ${yt ? `<a class="link sm" href="${escUrl(p.thumbnail)}" target="_blank" rel="noopener" download data-testid="pkg-yt-thumb">Download the 1280×720 thumbnail</a>` : ""}</div>`;
}

// what a saved post says, once, on its card (owner, 6 Nov 2026, B3/B7): "Saved to Buffer as draft · <id>" with a link to
// the channel in Buffer; nothing reaches Buffer before the person presses the button on this card
// what the post is in Buffer right now (studio/post_sync.py re-checks it whenever the run opens: owner, 9 Nov 2026)
const BUF_WORD = { draft: "Draft in Buffer", scheduled: "Scheduled in Buffer", needs_approval: "Waiting for approval in Buffer", sent: "Published", failed: "Failed in Buffer" };
const BUF_LINE = { draft: "Draft in Buffer (not posted)", scheduled: "Scheduled in Buffer", needs_approval: "Waiting for approval in Buffer", sent: "Published by Buffer", failed: "Buffer couldn't publish it" };
function sentLine(p) {
  if (p.status === "pending_approval") return "Waiting in Approvals" + (p.sent && p.sent.due_at ? " · " + esc(fmtWhen(p.sent.due_at)) : "");
  const s = p.sent || {}, id = s.buffer_post_id ? String(s.buffer_post_id) : "";
  const href = s.link || `https://publish.buffer.com/channels/${encodeURIComponent(p.channel_id || "")}/schedule`;
  const head = BUF_LINE[s.buffer_status] || (s.mode === "draft" ? "Saved to Buffer as draft (not posted)" : "In Buffer");
  const when = s.buffer_status === "sent" ? s.sent_at : s.buffer_status === "draft" ? "" : s.due_at;
  return `${head}${id ? ` · <code data-testid="pkg-buffer-id">${esc(id)}</code>` : ""}`
    + `${when ? " · " + esc(fmtWhen(when)) : ""} · <a class="link sm" href="${escUrl(href)}" target="_blank" rel="noopener" data-testid="pkg-buffer-link">${s.buffer_status === "sent" && s.link ? "Open the post" : "Open in Buffer"}</a>`
    + `${s.buffer_status === "failed" && s.error ? `<br><small data-testid="pkg-buffer-error">${esc(s.error)}</small>` : ""}`
    + `${s.edited_in_buffer ? `<br><small class="muted" data-testid="pkg-buffer-edited">The caption was edited in Buffer; this is Buffer's version.</small>` : ""}`
    + ` <button type="button" class="link sm" data-testid="pkg-buffer-check" title="Read this post from Buffer again">Check Buffer</button>`;
}
// a post that came back from Buffer (deleted there, or rejected in Approvals): editable again, and it says why
function syncLine(p) {
  const b = p.buffer_sync;
  if (!b || ["sent_to_buffer", "pending_approval"].includes(p.status)) return "";
  const what = b.event === "rejected" ? "It was rejected in Approvals" : `The ${b.mode === "draft" ? "draft" : "post"} was deleted in Buffer`;
  return `<div class="st warn" role="status" data-testid="pkg-buffer-sync">${what}, so it's back here to edit. Save or schedule it again when it's ready.</div>`;
}
function pkgCard(p) {
  const ch = chOf(p.channel_id), sent = ["sent_to_buffer", "pending_approval"].includes(p.status);
  const help = PKG_MODES[0][2];
  // the caption count: the server's when it sent one, else counted here the way Buffer counts (owner bug, 4 Nov 2026: the
  // header read "undefined/undefined" for a package saved without length / limit)
  const len = Number.isFinite(p.length) ? p.length : count(p.service || "", p.text || ""), lim = Number.isFinite(p.limit) ? p.limit : limitFor(p.service);
  return `<article class="pkg" data-testid="package" data-ch="${esc(p.channel_id)}">
    <header>${window.chMark ? window.chMark(ch && ch.id ? ch : { service: p.service }, true) : avatar(ch)}<div><b>${esc(p.channel_name || netName(p.service))}</b><small>${esc(netName(p.service))}</small></div><span class="cnt ${len > lim ? "over" : ""}" data-testid="pkg-count">${len}/${lim}</span></header>
    ${pkgMedia(p)}
    <div class="pkg-side">
    ${syncLine(p)}
    ${sent ? "" : pkgMissing(p)}
    ${pkgCovers(p, sent)}
    ${window.hqPkgControls ? window.hqPkgControls.card(p, sent) : window.pkgMusic ? window.pkgMusic(p, sent) : ""}
    ${sent || H.run && ["media", "news"].includes(H.run.kind) ? "" : `<div class="pm-acts"><button type="button" class="btn2 sm" data-testid="pkg-replace">Replace</button><button type="button" class="btn2 sm" data-testid="pkg-regen">Regenerate</button></div>`}
    ${p.note ? `<small class="muted sm" data-testid="pkg-note">${esc(p.note)}</small>` : ""}
    <label class="sr" for="pt-${esc(p.channel_id)}">Caption for ${esc(p.channel_name)}</label>
    <textarea id="pt-${esc(p.channel_id)}" data-testid="pkg-text" rows="5" ${sent ? "disabled" : ""}>${esc(p.text)}</textarea>
    ${checkList(p.check)}
    ${window.ledgerDraft ? window.ledgerDraft(p) : ""}
    ${p.status === "hold" ? `<div class="st warn" data-testid="pkg-hold">On hold: answer the question above first</div>` : sent ? `<div class="st ok" data-testid="pkg-sent">${sentLine(p)}</div>`
      : `<div class="pkg-act"><select data-testid="pkg-mode" aria-label="What to do with this post">${PKG_MODES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><input type="datetime-local" data-testid="pkg-when" hidden aria-label="When">
        <button class="btn sm" data-testid="pkg-send"${p.media_state && p.media_state !== "ok" ? ` disabled title="${esc(MEDIA_GONE)}"` : ""}>${PKG_MODES[0][3]}</button></div><small class="muted sm pkg-help" data-testid="pkg-help">${esc(help)}</small>${p.buffer_draft && p.buffer_draft.buffer_post_id ? ` <button type="button" class="link sm" data-testid="pkg-buffer-check" title="Is the saved draft still in Buffer?">Check Buffer</button>` : ""}`}
    </div>
  </article>`;
}
// multi-channel review (owner ask, 4 Nov 2026): one tab per channel across the top (a scroll-snap strip on a narrow
// screen); the card below is that channel's media and caption. The choice stays while the run redraws.
const PKG_TAB = { run: null, ch: "" };
const fmtLine = (p) => { const m = pkgFiles(p)[0] || {}, r = m.ratio || ratioOf(m.width, m.height) || p.ratio || "";
  const vid = m.type === "video" || (m.url && isVid(m.url)) || (p.format === "reel" && !m.url);
  return [vid ? videoName(p, r, m.duration) : FMT_NAME[p.format] || (m.url ? "Image" : "Text"), r].filter(Boolean).join(" · "); };
function pkgTabs(r, pk) {
  if (pk.length < 2) return "";
  if (PKG_TAB.run !== r.id || !pk.some((p) => p.channel_id === PKG_TAB.ch)) { PKG_TAB.run = r.id; PKG_TAB.ch = pk[0].channel_id; }
  return `<div class="pkg-tabs-wrap"><div class="pkg-tabs" role="tablist" aria-label="Channels in this post" data-testid="pkg-tabs">${pk.map((p) => { const on = p.channel_id === PKG_TAB.ch, ch = chOf(p.channel_id);
    return `<button type="button" role="tab" class="pkg-tab${on ? " on" : ""}" id="pkt-${esc(p.channel_id)}" aria-selected="${on}" aria-controls="pkp-${esc(p.channel_id)}" tabindex="${on ? 0 : -1}" data-tab="${esc(p.channel_id)}" data-testid="pkg-tab">
      ${window.chMark ? window.chMark(ch && ch.id ? ch : { service: p.service }, true) : ""}<span><b>${esc(p.channel_name || netName(p.service))}</b><small>${esc([p.channel_name ? netName(p.service) : "", fmtLine(p)].filter(Boolean).join(" · "))}</small></span></button>`; }).join("")}</div></div>`;
}
function pkgSelect(ch, focus) {
  const box = $("#packages"); if (!box.classList.contains("multi")) return;
  PKG_TAB.ch = ch;
  box.querySelectorAll(".pkg-tab").forEach((t) => { const on = t.dataset.tab === ch; t.classList.toggle("on", on); t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); if (on) t.scrollIntoView({ block: "nearest", inline: "nearest" }); });
  box.querySelectorAll(".pkg").forEach((c) => (c.hidden = c.dataset.ch !== ch));
}
window.pkgSelect = pkgSelect;
function wireTabs() {
  const box = $("#packages"), tabs = [...box.querySelectorAll(".pkg-tab")]; if (!tabs.length) return;
  box.querySelectorAll(".pkg").forEach((c) => { c.id = "pkp-" + c.dataset.ch; c.setAttribute("role", "tabpanel"); c.setAttribute("aria-labelledby", "pkt-" + c.dataset.ch); });
  tabs.forEach((t, i) => { t.onclick = () => pkgSelect(t.dataset.tab);
    t.onkeydown = (e) => { const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key]; if (!k && e.key !== "Home" && e.key !== "End") return; e.preventDefault();
      const j = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + k + tabs.length) % tabs.length; pkgSelect(tabs[j].dataset.tab, true); }; });
  pkgSelect(PKG_TAB.ch);
}

function wirePackages(r) {
  document.querySelectorAll("#packages .pkg").forEach((el) => {
    const ch = el.dataset.ch, ta = el.querySelector("textarea"), mode = el.querySelector("[data-testid=pkg-mode]"), when = el.querySelector("[data-testid=pkg-when]");
    const pk = (r.result.packages || []).find((p) => p.channel_id === ch) || {};
    let t;
    const bc = el.querySelector("[data-testid=pkg-buffer-check]");
    if (bc) bc.onclick = async () => {  // read this post from Buffer now (not the few-minute cache): deleted / scheduled / edited there
      bc.disabled = true; bc.textContent = "Checking…";
      try { const n = await api(`/api/agent/runs/${r.id}?refresh_buffer=1`); showIfOn(r.id, n);
        const np = ((n.result || {}).packages || []).find((p) => p.channel_id === ch) || {};
        toast(n.buffer_check === "unavailable" ? "Buffer couldn't be reached just now, so this may be out of date. Try again in a minute."
          : np.buffer_sync && !np.sent && !np.buffer_draft ? "It's no longer in Buffer, so you can edit it again" : "Checked with Buffer", n.buffer_check === "unavailable" ? 6000 : 4000); }
      catch (e) { toast(e.message, 6000); bc.disabled = false; bc.textContent = "Check Buffer"; }
    };
    ta.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
      try { const p = await api(`/api/agent/runs/${r.id}/packages/${ch}`, { method: "POST", body: { text: ta.value } });
        el.querySelector("[data-testid=pkg-count]").textContent = `${p.length}/${p.limit}`;
        el.querySelector(".chk-res").outerHTML = checkList(p.check); window.ledgerSuggested?.(p.suggestions); } catch (e) { toast(e.message); } }, 450); };
    // a video's length when the server didn't record it: read from the file itself
    const v = el.querySelector("[data-testid=pkg-video]");
    const setDur = () => { const f = el.querySelector("[data-testid=pkg-format]"); if (!f || !f.textContent.includes("…")) return;
      const parts = f.textContent.split(" · ");
      if (parts[1] === "…" && v.videoWidth) parts[1] = ratioOf(v.videoWidth, v.videoHeight);
      if (parts[2] === "…" && isFinite(v.duration)) parts[2] = fmtDur(v.duration);
      f.textContent = parts.join(" · "); };
    if (v) { v.addEventListener("loadedmetadata", setDur); if (v.readyState >= 1) setDur(); }
    el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => window.openMedia && window.openMedia((pk.media && pk.media.length ? pk.media : (pk.media_urls || []).map((u) => ({ url: u, type: isVid(u) ? "video" : "photo" }))), +b.dataset.open, `${netName(pk.service)} · ${mediaLabel(pk)}`)));
    const busyBtn = (b, txt) => { b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>${txt}`; };
    const rep = el.querySelector("[data-testid=pkg-replace]");
    if (rep) rep.onclick = () => window.pickFromLibrary && window.pickFromLibrary(async (item) => {
      busyBtn(rep, "Replacing…");
      try { showIfOn(r.id, await api(`/api/agent/runs/${r.id}/packages/${ch}/media`, { method: "POST", body: { token: item.media_token } })); toast("Media replaced"); }
      catch (e) { toast(e.message, 6000); rep.disabled = false; rep.textContent = "Replace"; }
    }, { title: `Replace ${netName(pk.service)} media`, upload: true, video: pk.service === "youtube" || pk.service === "tiktok" });
    el.querySelectorAll("[data-cover]").forEach((b) => (b.onclick = async () => {
      if (b.classList.contains("on")) return;
      el.querySelectorAll("[data-cover]").forEach((x) => (x.disabled = true));
      b.classList.add("busy");
      try { const n = await api(`/api/agent/runs/${r.id}/packages/${ch}/cover`, { method: "POST", body: { index: +b.dataset.cover } });
        toast("Swapping the cover…"); if (showIfOn(r.id, n)) watch(n.id); }
      catch (e) { toast(e.message, 6000); el.querySelectorAll("[data-cover]").forEach((x) => (x.disabled = false)); b.classList.remove("busy"); }
    }));
    if (window.hqPkgControls) window.hqPkgControls.wire(el, r, pk);  // Update draft, Undo, Music (pkgcontrols.js)
    else if (window.wireMusic) window.wireMusic(el, r, pk);  // Instagram music on the Reel (reelmusic.js)
    const regen = el.querySelector("[data-testid=pkg-regen]");
    if (regen) regen.onclick = async () => {
      busyBtn(regen, "Regenerating…");
      try { showIfOn(r.id, await api(`/api/agent/runs/${r.id}/packages/${ch}/media`, { method: "POST", body: {} })); toast(`${netName(pk.service)} regenerated`); }
      catch (e) { toast(e.message, 6000); regen.disabled = false; regen.textContent = "Regenerate"; }
    };
    if (mode) mode.onchange = () => {
      const m = PKG_MODES.find((x) => x[0] === mode.value);
      when.hidden = mode.value !== "schedule"; el.querySelector("[data-testid=pkg-help]").textContent = m[2]; el.querySelector("[data-testid=pkg-send]").textContent = m[3];
    };
    const send = el.querySelector("[data-testid=pkg-send]");
    if (send) send.onclick = async () => {
      send.disabled = true;
      try {
        const body = { mode: mode.value };
        if (mode.value === "schedule") { if (!when.value) throw new Error("Pick a day and time"); body.due_at = new Date(when.value).toISOString(); }
        const out = await api(`/api/agent/runs/${r.id}/packages/${ch}/schedule`, { method: "POST", body });
        toast(out.status === "pending_approval" ? "Waiting for approval" : (mode.value === "draft" ? "Saved as a Buffer draft (not posted)" : "Sent to Buffer") + (out.warnings?.length ? " · " + out.warnings[0] : ""), 4500);
        const fresh = await api(`/api/agent/runs/${r.id}`); showIfOn(r.id, fresh); readyBadge();
      } catch (e) { toast(e.message, 6000); send.disabled = false; }
    };
  });
}

$("#replyForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const t = $("#replyIn").value.trim(); if (!t || !H.run || $("#runView").classList.contains("hc-loading")) return;  // never to the previous thread
  const b = $("[data-testid=reply-go]"); b.disabled = true;
  const rid = H.run.id;  // the reply belongs to the thread it was written in, even if another one is picked while it sends
  try { const r = await api(`/api/agent/runs/${rid}/messages`, { method: "POST", body: { text: t } }); if (onRun(rid)) { clearIfSame(t); showRun(r); watch(r.id); } else { missedReply(rid, t); toast("Sent to the thread it was written in"); } }
  catch (e) { toast(e.message, 5000); } finally { b.disabled = false; }
});
// still on this run, and no other thread loading over it: an answer for it may be drawn
const onRun = (rid) => !!H.run && String(H.run.id) === String(rid) && !$("#runView").classList.contains("hc-loading");
// the composer is cleared only if it still holds what was sent: words typed while it sent are kept
const clearIfSame = (sent) => { const ri = $("#replyIn"); if (ri.value.trim() === sent) ri.value = ""; };
// a reply answered while a thread loaded over its own: the words it sent leave the composer, and if that thread is still the
// one held (a failed switch is restoring it) chat.js polls it once more so the reply shows up (H.missed)
const missedReply = (rid, sent) => { if (sent) clearIfSame(sent); if (H.run && String(H.run.id) === String(rid)) H.missed = String(rid); };

// Upload video in the composer, on every turn of the thread (and drop a file on the thread): it becomes this post's Reel
const REPLY_MAX = () => (S.session?.demo ? 8 : 100) * 1048576;
async function replyUpload(f) {
  if (!H.run || $("#runView").classList.contains("hc-loading")) return;  // another thread is loading: never this one's upload
  if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm)$/i.test(f.name)) return toast("Upload an MP4 or MOV video");
  if (f.size > REPLY_MAX()) return toast(`Videos up to ${REPLY_MAX() / 1048576} MB here`);
  if (BUSY(H.run.status)) return toast("The agent is still working on this post. Upload once it's done.");
  const lab = $("#replyUpload"), sp = lab.querySelector("span");
  lab.classList.add("busy"); sp.textContent = "Uploading…";
  // the run and the words are taken when the upload starts: the video goes to the thread it was dropped on, never to
  // one picked while it uploads, and the answer is drawn only while that thread is still on screen
  const rid = H.run.id, said = $("#replyIn").value.trim();
  try {
    const tok = await uploadFile(f);
    const n = await api(`/api/agent/runs/${rid}/messages`, { method: "POST", body: { text: said || "Here's the video: " + f.name, media_token: tok } });
    if (onRun(rid)) { clearIfSame(said); showRun(n); watch(n.id); } else { missedReply(rid, said); toast("Video added to the thread it was dropped on"); }
  } catch (e) { toast(e.message, 6000); }
  finally { lab.classList.remove("busy"); sp.textContent = "Upload video"; $("#replyFile").value = ""; }
}
window.hqReplyUpload = replyUpload;
$("#replyFile").onchange = (e) => e.target.files[0] && replyUpload(e.target.files[0]);
(() => {
  const rv = $("#runView");
  const files = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
  ["dragover", "dragenter"].forEach((ev) => rv.addEventListener(ev, (e) => { if (!H.run || !files(e)) return; e.preventDefault(); rv.classList.add("drop-on"); }));
  rv.addEventListener("dragleave", (e) => { if (!rv.contains(e.relatedTarget)) rv.classList.remove("drop-on"); });
  rv.addEventListener("drop", (e) => { rv.classList.remove("drop-on"); const f = e.dataTransfer && e.dataTransfer.files[0]; if (!f || !H.run) return; e.preventDefault(); replyUpload(f); });
})();

async function loadRuns() {
  const box = $("#runList");
  try {
    const rows = await api("/api/agent/runs?limit=60");
    if (window.mpRecent) return window.mpRecent(rows);  // Make a post v2: Recent content cards (makepost.js)
    box.innerHTML = rows.length ? rows.map((x) => `<button class="runrow" data-testid="run-row" data-id="${esc(x.id)}" data-kind="${esc(x.kind)}">${x.cover ? (isVid(x.cover) ? `<span class="vt">▶</span>` : `<img src="${esc(x.cover)}" alt="">`) : `<span class="vt">·</span>`}
      <span class="rt"><b>${esc(x.title)}</b><small>${esc(x.status)} · ${new Date(x.created * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}${x.packages ? " · " + x.packages + " posts" : ""}</small></span></button>`).join("")
      : `<div class="empty"><b>No runs yet</b><span>Give the agent a topic like “AI agents” and watch it work.</span></div>`;
    box.querySelectorAll("[data-id]").forEach((b) => (b.onclick = async () => { if (b.dataset.kind === "take") return window.openTake(b.dataset.id); const r = await api(`/api/agent/runs/${b.dataset.id}`); showRun(r); if (["running", "rendering"].includes(r.status)) watch(r.id); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  } catch (_) {}
}

function brainName(b) {
  if (!b || typeof b !== "object") return "the built-in writer";
  if (b.mode === "mcp") return b.label || "Your AI app · via MCP";  // an AI app drove this run: its model is chosen there
  if (b.source === "free starter") return "the free starter model";
  const m = b.model || b.provider || b.source || "the built-in writer";
  return b.fast_model && b.fast_model !== b.model ? `${m} (captions: ${b.fast_model})` : m;
}

window.showRunFromLibrary = (r) => { showRun(r); window.scrollTo({ top: 0 }); };
// draw an in-thread action's answer only while its run is still the one on screen (-> whether it was drawn)
function showIfOn(rid, n) { if (!onRun(rid)) return false; showRun(n); return true; }
window.hqOnRun = onRun;
window.showRun = showRun; window.watchRun = watch; window.loadRuns = loadRuns; window.H_STATE = H;

async function loadAgent() {
  agentChips(); loadRuns(); readyBadge();
  // the brain that will actually run (operator Claude, your key, the free starter), not only the caption writer
  try {
    H.brain = (await api("/api/agent/brain")).brain;
    if (!(H.run && H.run.kind === "agent" && (H.run.result || {}).brain)) $("#agEngine").textContent = "Writing with: " + brainName(H.brain);
  } catch (_) {}
}

// ---------------------------------------------------------------- Planner badge (the Planner itself lives in planner.js)
function readyBadgeFrom(c) {
  const n = c.items.filter((i) => i.kind === "ready").length;
  const b = $("#readyBadge"); b.hidden = !n; b.textContent = n;
}
async function readyBadge() {
  try { readyBadgeFrom(await api("/api/calendar")); } catch (_) {}
}

// ---------------------------------------------------------------- Style Ledger + brand check (the kit itself: brandstudio.js)
// the brand check of the sample cover against the ledger, shown under the kit's live preview (the Ledger itself: ledger.js)
window.brandCheck = async function (box) { try { const p = await api("/api/brand/preview", { method: "POST" }); box.innerHTML = checkList(p.check); } catch (_) { box.innerHTML = ""; } };
// ---------------------------------------------------------------- AI engine
// Settings > AI engine (engine cards, Your keys, Logs) is drawn by aiengine.js; this keeps H.ai for the agent screen.
async function loadAI() {
  try { H.ai = await api("/api/ai"); } catch (_) { H.ai = null; }
}

// "split this into 5 shorts <link>": a long video becomes Opus-style clips (a One take run). Someone else's video needs
// the person's word that they may repost it: asked inline under the composer, once, then the same request goes again.
async function startRun(body) {
  try { return await api("/api/agent/runs", { method: "POST", body }); }
  catch (e) {
    if (e.status !== 409 || !/^Rights:/.test(e.message)) throw e;
    const ok = await askRights(e.message.replace(/^Rights:\s*/, ""));
    if (!ok) return null;
    // the person read the server's statement (it carries link_ingest.ATTESTATION) and said yes: that is the consent record
    return api("/api/agent/runs", { method: "POST", body: { ...body, rights_confirmed: true, rights_attested: true } });
  }
}
function askRights(text) {
  return new Promise((resolve) => {
    let box = document.getElementById("agRights");
    if (!box) {
      box = document.createElement("div");
      box.id = "agRights"; box.className = "card rights-ask"; box.setAttribute("role", "group"); box.setAttribute("data-testid", "agent-rights");
      $("#agGo").closest("form").appendChild(box);
    }
    box.hidden = false;
    box.innerHTML = `<p class="sm" data-testid="agent-rights-text">${esc(text)}</p>
      <div class="row"><button type="button" class="btn primary sm" data-testid="agent-rights-yes">I have the right to use it</button>
      <button type="button" class="btn ghost sm" data-testid="agent-rights-no">Cancel</button></div>`;
    const done = (v) => { box.hidden = true; resolve(v); };
    box.querySelector("[data-testid=agent-rights-yes]").onclick = () => done(true);
    box.querySelector("[data-testid=agent-rights-no]").onclick = () => done(false);
    box.querySelector("[data-testid=agent-rights-yes]").focus();
  });
}
