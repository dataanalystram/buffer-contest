// HuhQueue front end: Make a post (the agent, its timeline, per-channel posts, the reply thread), the Style Ledger, AI engine.
"use strict";
const H = { run: null, poll: null, sel: new Set(), ai: null, upload: null, attach: null };
const STATUS_ICON = { pending: "", running: '<span class="spin" aria-hidden="true"></span>', done: "✓", warn: "!", failed: "×" };
// suggestion chips under the reply box: one box edits any channel, its media or its caption
// relevant to THIS post (owner, 3 Nov 2026: "Redo YouTube at 30s" showed when only Instagram was selected)
function replyChips(r) {
  const pk = (r.result || {}).packages || [], svc = new Set(pk.map((p) => p.service)), fm = new Set(pk.map((p) => p.format));
  if (!pk.length) return ["Try again with a different angle", "What templates can you use?"];
  const out = ["Make the hook stronger", "Shorter captions"];
  if (fm.has("carousel")) out.push("Restyle slide 2");
  if (fm.has("reel")) out.push("Add captions", "Trim the first 3 s");
  if (svc.has("youtube")) out.push("Redo YouTube at 30s");
  if (svc.has("linkedin")) out.push("LinkedIn more formal");
  out.push("What templates can you use?");
  return out.slice(0, 6);
}
// agent replies are markdown: escaped first, then **bold**, *italic*, `code`, [links](https://...) and "- " lists. Nothing
// else becomes HTML (owner, 3 Nov 2026: replies showed a raw **Question:**)
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

function watch(id) {
  // keeps polling through a failed request (a deploy, a cold start): one error used to stop the UI at "Write captions"
  clearTimeout(H.poll);
  let errs = 0;
  const tick = async () => {
    try {
      const r = await api(`/api/agent/runs/${id}`); errs = 0; showRun(r);
      if (["running", "rendering"].includes(r.status)) H.poll = setTimeout(tick, 1400);
      else { loadRuns(); readyBadge(); }
    } catch (e) {
      errs += 1;
      if (errs === 3) toast("Reconnecting to the agent…");
      H.poll = setTimeout(tick, Math.min(15000, 1500 * 2 ** Math.min(errs, 4)));
    }
  };
  H.poll = setTimeout(tick, 900);
}

// ---------------------------------------------------------------- run view
function showRun(r) {
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
      <div><b>${esc(s.label)}${n > 1 ? ` <span class="xn" data-testid="step-count">×${n}</span>` : ""}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}${stepExtra(s)}</div></li>`).join("");
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
  } else if (busy && !pk.length) html += `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  $("#mediaBox").innerHTML = html;
  const redo = $("[data-testid=legacy-redo]");
  if (redo) redo.onclick = async () => { redo.disabled = true;
    try { const n = await api(`/api/agent/runs/${r.id}/messages`, { method: "POST", body: { text: "redo" } }); showRun(n); watch(n.id); loadRuns(); }
    catch (e) { toast(e.message, 5000); redo.disabled = false; } };
  const opn = $("[data-testid=legacy-open-new]");
  if (opn) opn.onclick = async () => { const n = await api(`/api/agent/runs/${opn.dataset.run}`); showRun(n); if (["running", "rendering"].includes(n.status)) watch(n.id); };
  agentExtras(r, busy);
  const ready = ["ready"].includes(r.status) || (!busy && pk.length);
  $("#pkgHead").hidden = !pk.length || !ready;
  $("#pkgHelp").hidden = $("#pkgHead").hidden;
  $("#packages").innerHTML = ready ? pk.map(pkgCard).join("") + (pk.some((p) => p.status === "ready")
    ? `<div class="pkg-all"><button class="btn2" data-testid="save-drafts">Save all as Buffer drafts (not posted)</button></div>` : "") : "";
  const sd = $("[data-testid=save-drafts]");
  if (sd) sd.onclick = async () => {
    sd.disabled = true;
    try { const out = await api(`/api/agent/runs/${r.id}/tools/save_buffer_drafts`, { method: "POST", body: {} });
      toast(out.drafts.map((d) => `${netName(d.service)}: ${d.status.replaceAll("_", " ")}`).join(" · "), 6000); showRun(await api(`/api/agent/runs/${r.id}`)); readyBadge(); }
    catch (e) { toast(e.message, 6000); sd.disabled = false; }
  };
  wirePackages(r);
}

function agentExtras(r, busy) {
  const res = r.result || {}, br = res.brain || {}, agent = r.kind === "agent";
  $("#agentBrain").hidden = !agent || !br.mode;
  if (agent && br.mode) {
    // the same words in the run view and the composer, after a reply too (QA: "free starter" showed while Claude ran)
    $("#agentBrain").textContent = (br.mode === "tools" ? "Planned and run by " : "Standard plan, written with ") + brainName(br) +
      ((res.notes || []).length ? " · " + res.notes[res.notes.length - 1] : "");
    $("#agEngine").textContent = "Writing with: " + brainName(br);
  }
  const plan = res.plan || [];
  $("#agentPlan").hidden = !plan.length;
  $("#agentPlan").innerHTML = plan.map((p) => `<li>${esc(p)}</li>`).join("");
  const th = (res.thread || []).slice(1);
  $("#agentThread").innerHTML = th.map((m) => `<div class="msg ${m.role}" data-testid="thread-msg"><b>${m.role === "user" ? "You" : "Agent"}</b><span class="md">${m.role === "user" ? esc(m.text) : md(m.text)}</span></div>`).join("") +
    // someone else's video: ONE question, answered with a button (never typed twice)
    (res.rights_question && !res.recap && !(res.rights || {}).mode && !busy ? `<div class="st warn ask-card" role="group" aria-label="Video rights" data-testid="rights-card">
      <span class="md">${md(res.rights_question)}</span><div class="row"><button type="button" class="btn sm" data-testid="rights-yes" data-say="Yes, I have the rights to repost it with credit">Yes, I have the rights</button>
      <button type="button" class="btn2 sm" data-testid="rights-slides" data-say="Use slides instead">Use slides instead</button></div></div>` : "") +
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
  const rs = $("#agentThread [data-testid=mp-resume]");
  if (rs) rs.onclick = async () => { rs.disabled = true;
    try { const n = await api(`/api/agent/runs/${r.id}/resume`, { method: "POST", body: {} }); showRun(n); watch(n.id); } catch (e) { toast(e.message, 5000); rs.disabled = false; } };
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
        showRun(n); watch(n.id);
      } catch (e) { toast(e.message, 6000); lab.classList.remove("busy"); lab.lastChild.textContent = "Upload video"; }
    };
  }
  // one edit box for every run, after it finishes (also older runs: the server routes it)
  $("#replyForm").hidden = busy;
  $("#replyChips").innerHTML = replyChips(r).map((q) => `<button type="button" class="chip mini" data-testid="reply-chip" data-say="${esc(q)}">${esc(q)}</button>`).join("");
  $("#replyChips").querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => { $("#replyIn").value = b.dataset.say; $("#replyForm").requestSubmit(); }));
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
  const word = (p) => (p.sent ? (p.status === "pending_approval" ? "Waiting for approval" : "In Buffer") : p.status === "hold" ? "On hold" : p.changed_since_sent ? "Changed since saved" : "Ready");
  box.hidden = false;
  box.innerHTML = `<span class="mp-ready-h">Your posts</span>` + pk.map((p) => { const f = pkgFiles(p)[0];
    const th = f ? (f.type === "video" ? `<video src="${escUrl(f.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${escUrl(f.url)}" alt="">`) : `<span class="mp-ready-ph" aria-hidden="true"></span>`;
    return `<button type="button" class="mp-ready-i st-${esc(p.sent ? "sent" : p.status)}" data-ch="${esc(p.channel_id)}" data-testid="mp-ready-item" aria-label="Go to the ${esc(netName(p.service))} post">${th}<span><b>${esc(netName(p.service))}</b><small>${esc(mediaLabel(p))} · ${word(p)}</small></span></button>`; }).join("");
  box.querySelectorAll("[data-ch]").forEach((b) => (b.onclick = () => { const c = document.querySelector(`#packages .pkg[data-ch="${CSS.escape(b.dataset.ch)}"]`); if (c) { c.scrollIntoView({ behavior: "smooth", block: "start" }); flash([c]); } }));
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
    ${slides ? "" : `<small class="muted sm">Your posts are Reels; pick a template to make the image channels slides.</small>`}</p>`;
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
    try { const n = await api(`/api/agent/runs/${r.id}/template`, { method: "POST", body: { template: b.dataset.tpl } }); showRun(n); watch(n.id); }
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
// owner P0: a video was labelled "Image". A video is always "Video · ratio · length"; the ratio and length come from the
// file itself when the server didn't record them.
function mediaLabel(p) {
  const m = pkgFiles(p), first = m[0] || {};
  if (first.type === "video" || isVid(first.url)) {
    const parts = ["Video", first.ratio || ratioOf(first.width, first.height) || "…", first.duration ? fmtDur(first.duration) : "…"];
    if (first.role === "original") parts.push("your file");
    return parts.join(" · ");
  }
  const parts = [p.format === "carousel" && m.length > 1 ? `Carousel · ${m.length} slides` : FMT_NAME[p.format] || "Image"];
  if (first.ratio) parts.push(first.ratio);
  return parts.join(" · ");
}
window.HQ_MEDIA_LABEL = mediaLabel;
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

function pkgCard(p) {
  const ch = chOf(p.channel_id), sent = ["sent_to_buffer", "pending_approval"].includes(p.status);
  const help = PKG_MODES[0][2];
  return `<article class="pkg" data-testid="package" data-ch="${esc(p.channel_id)}">
    <header>${window.chMark ? window.chMark(ch && ch.id ? ch : { service: p.service }, true) : avatar(ch)}<div><b>${esc(p.channel_name)}</b><small>${esc(netName(p.service))}</small></div><span class="cnt ${p.length > p.limit ? "over" : ""}" data-testid="pkg-count">${p.length}/${p.limit}</span></header>
    ${pkgMedia(p)}
    ${sent ? "" : pkgMissing(p)}
    ${pkgCovers(p, sent)}
    ${window.hqPkgControls ? window.hqPkgControls.card(p, sent) : window.pkgMusic ? window.pkgMusic(p, sent) : ""}
    ${sent || H.run && ["media", "news"].includes(H.run.kind) ? "" : `<div class="pm-acts"><button type="button" class="btn2 sm" data-testid="pkg-replace">Replace</button><button type="button" class="btn2 sm" data-testid="pkg-regen">Regenerate</button></div>`}
    ${p.note ? `<small class="muted sm" data-testid="pkg-note">${esc(p.note)}</small>` : ""}
    <label class="sr" for="pt-${esc(p.channel_id)}">Caption for ${esc(p.channel_name)}</label>
    <textarea id="pt-${esc(p.channel_id)}" data-testid="pkg-text" rows="5" ${sent ? "disabled" : ""}>${esc(p.text)}</textarea>
    ${checkList(p.check)}
    ${window.ledgerDraft ? window.ledgerDraft(p) : ""}
    ${p.status === "hold" ? `<div class="st warn" data-testid="pkg-hold">On hold: answer the question above first</div>` : sent ? `<div class="st ok" data-testid="pkg-sent">${p.status === "pending_approval" ? "Waiting in Approvals" : "In Buffer"}${p.sent && p.sent.mode === "draft" ? " as a draft (not posted)" : ""}${p.sent && p.sent.due_at ? " · " + esc(fmtWhen(p.sent.due_at)) : ""}</div>`
      : `<div class="pkg-act"><select data-testid="pkg-mode" aria-label="What to do with this post">${PKG_MODES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><input type="datetime-local" data-testid="pkg-when" hidden aria-label="When">
        <button class="btn sm" data-testid="pkg-send"${p.media_state && p.media_state !== "ok" ? ` disabled title="${esc(MEDIA_GONE)}"` : ""}>${PKG_MODES[0][3]}</button></div><small class="muted sm pkg-help" data-testid="pkg-help">${esc(help)}</small>`}
  </article>`;
}

function wirePackages(r) {
  document.querySelectorAll("#packages .pkg").forEach((el) => {
    const ch = el.dataset.ch, ta = el.querySelector("textarea"), mode = el.querySelector("[data-testid=pkg-mode]"), when = el.querySelector("[data-testid=pkg-when]");
    const pk = (r.result.packages || []).find((p) => p.channel_id === ch) || {};
    let t;
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
      try { showRun(await api(`/api/agent/runs/${r.id}/packages/${ch}/media`, { method: "POST", body: { token: item.media_token } })); toast("Media replaced"); }
      catch (e) { toast(e.message, 6000); rep.disabled = false; rep.textContent = "Replace"; }
    }, { title: `Replace ${netName(pk.service)} media`, upload: true, video: pk.service === "youtube" || pk.service === "tiktok" });
    el.querySelectorAll("[data-cover]").forEach((b) => (b.onclick = async () => {
      if (b.classList.contains("on")) return;
      el.querySelectorAll("[data-cover]").forEach((x) => (x.disabled = true));
      b.classList.add("busy");
      try { const n = await api(`/api/agent/runs/${r.id}/packages/${ch}/cover`, { method: "POST", body: { index: +b.dataset.cover } });
        toast("Swapping the cover…"); showRun(n); watch(n.id); }
      catch (e) { toast(e.message, 6000); el.querySelectorAll("[data-cover]").forEach((x) => (x.disabled = false)); b.classList.remove("busy"); }
    }));
    if (window.hqPkgControls) window.hqPkgControls.wire(el, r, pk);  // Update draft, Undo, Music (pkgcontrols.js)
    else if (window.wireMusic) window.wireMusic(el, r, pk);  // Instagram music on the Reel (reelmusic.js)
    const regen = el.querySelector("[data-testid=pkg-regen]");
    if (regen) regen.onclick = async () => {
      busyBtn(regen, "Regenerating…");
      try { showRun(await api(`/api/agent/runs/${r.id}/packages/${ch}/media`, { method: "POST", body: {} })); toast(`${netName(pk.service)} regenerated`); }
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
        const fresh = await api(`/api/agent/runs/${r.id}`); showRun(fresh); readyBadge();
      } catch (e) { toast(e.message, 6000); send.disabled = false; }
    };
  });
}

$("#replyForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const t = $("#replyIn").value.trim(); if (!t || !H.run) return;
  const b = $("[data-testid=reply-go]"); b.disabled = true;
  try { const r = await api(`/api/agent/runs/${H.run.id}/messages`, { method: "POST", body: { text: t } }); $("#replyIn").value = ""; showRun(r); watch(r.id); }
  catch (e) { toast(e.message, 5000); } finally { b.disabled = false; }
});

// Upload video in the composer, on every turn of the thread (and drop a file on the thread): it becomes this post's Reel
const REPLY_MAX = () => (S.session?.demo ? 8 : 100) * 1048576;
async function replyUpload(f) {
  if (!H.run) return;
  if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm)$/i.test(f.name)) return toast("Upload an MP4 or MOV video");
  if (f.size > REPLY_MAX()) return toast(`Videos up to ${REPLY_MAX() / 1048576} MB here`);
  if (BUSY(H.run.status)) return toast("The agent is still working on this post. Upload once it's done.");
  const lab = $("#replyUpload"), sp = lab.querySelector("span");
  lab.classList.add("busy"); sp.textContent = "Uploading…";
  try {
    const tok = await uploadFile(f);
    const said = $("#replyIn").value.trim();
    const n = await api(`/api/agent/runs/${H.run.id}/messages`, { method: "POST", body: { text: said || "Here's the video: " + f.name, media_token: tok } });
    $("#replyIn").value = ""; showRun(n); watch(n.id);
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
  if (!b) return "the built-in writer";
  if (b.source === "free starter") return "the free starter model";
  const m = b.model || b.provider || b.source || "the built-in writer";
  return b.fast_model && b.fast_model !== b.model ? `${m} (captions: ${b.fast_model})` : m;
}

window.showRunFromLibrary = (r) => { showRun(r); window.scrollTo({ top: 0 }); };
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
    return api("/api/agent/runs", { method: "POST", body: { ...body, rights_confirmed: true } });
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
