// HuhQueue front end: Make a post (the agent, its timeline, per-channel posts, the reply thread), the Style Ledger, AI engine.
"use strict";
const H = { run: null, poll: null, sel: new Set(), ai: null, upload: null, attach: null };
const STATUS_ICON = { pending: "", running: '<span class="spin" aria-hidden="true"></span>', done: "✓", warn: "!", failed: "×" };
// suggestion chips under the reply box: one box edits any channel, its media or its caption
const REPLY_CHIPS = ["Make the hook stronger", "Redo YouTube at 30s", "LinkedIn more formal", "Shorter captions", "Logo smaller", "No hashtags"];
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
}

const drop = $("#drop");
["dragover", "dragenter"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
drop.addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) pickFile(ev.dataTransfer.files[0]); });
$("#agFile").onchange = (e) => e.target.files[0] && pickFile(e.target.files[0]);
const UPLOAD_MAX = () => (S.session?.demo ? 8 : 40) * 1048576;
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
  if (!body.prompt && !H.upload && !H.attach) return toast("Tell the agent what to post, paste a link, or add your photo or video");
  const b = $("#agGo"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Starting…`;
  try {
    if (H.upload) {
      b.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
      body.media_token = await uploadFile(H.upload); body.media_name = H.upload.name;
    } else if (H.attach) { body.media_token = H.attach.media_token; body.media_name = H.attach.name; }
    const run = await api("/api/agent/runs", { method: "POST", body });
    showRun(run); watch(run.id);
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; b.textContent = "Make it"; }
});

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
  $("#timeline").innerHTML = r.steps.map((s) => `<li class="step ${s.status}" data-testid="step" data-key="${s.key}">
      <span class="dot" aria-hidden="true">${STATUS_ICON[s.status] || ""}</span>
      <div><b>${esc(s.label)}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}${stepExtra(s)}</div></li>`).join("");
  const res = r.result || {}, media = res.media || [], busy = ["running", "rendering"].includes(r.status);
  const pk = res.packages || [];
  let html = "";
  if (res.upload) {
    const u = res.upload;
    html += `<div class="run-upload" data-testid="run-upload">${u.type === "video" ? `<video src="${esc(u.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(u.url)}" alt="">`}
      <span><b>Your ${esc(u.type)}${u.name ? ": " + esc(u.name) : ""}</b><small>${u.transcript ? "Transcript: “" + esc(u.transcript.slice(0, 160)) + (u.transcript.length > 160 ? "…”" : "”") : esc(u.transcript_note || "The agent reads it first")}</small></span></div>`;
  }
  if (!pk.length && media.length) {  // while the agent works: what it has made so far (each channel's own card follows)
    html += `<div class="strip" data-testid="media-strip">${media.map((m, i) => isVid(m.url) || m.type === "video"
      ? `<video src="${esc(m.url)}" controls playsinline preload="metadata" data-testid="media-video"></video>`
      : `<a href="${esc(m.url)}" target="_blank" rel="noopener"><img src="${esc(m.url)}" alt="Slide ${i + 1}" data-testid="media-img"></a>`).join("")}</div>`;
  } else if (busy && !pk.length) html += `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  $("#mediaBox").innerHTML = html;
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
  $("#agentThread").hidden = !th.length;
  $("#agentThread").innerHTML = th.map((m) => `<div class="msg ${m.role}" data-testid="thread-msg"><b>${m.role === "user" ? "You" : "Agent"}</b><span>${esc(m.text)}</span></div>`).join("") +
    ((res.conflicts || []).length ? `<div class="st warn" data-testid="agent-question">${esc(res.conflicts[0].question)}</div>` : "");
  // one edit box for every run, after it finishes (also older runs: the server routes it)
  $("#replyForm").hidden = busy;
  $("#replyChips").innerHTML = REPLY_CHIPS.map((q) => `<button type="button" class="chip mini" data-testid="reply-chip" data-say="${esc(q)}">${esc(q)}</button>`).join("");
  $("#replyChips").querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => { $("#replyIn").value = b.dataset.say; $("#replyForm").requestSubmit(); }));
}

function stepExtra(s) {
  const d = s.data || {};
  if (s.key === "find" && d.leads) return `<ul class="leads">${d.leads.slice(0, 3).map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a> <span class="muted">${esc(l.source || "")}</span></li>`).join("")}</ul>`;
  if (s.key === "verify" && d.url) return `<ul class="leads"><li>Read: <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.site)}</a></li>${(d.other_outlets || []).map((o) => `<li>Also: <a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.source)}</a></li>`).join("")}</ul>`;
  if (s.key === "structure" && d.facts) return `<ul class="facts">${d.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
  if (s.tool === "extract_facts" && d.facts) return `<ul class="facts">${d.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
  if (s.tool === "search_news" && d.leads) return `<ul class="leads">${d.leads.slice(0, 3).map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a></li>`).join("")}</ul>`;
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
function mediaLabel(p) {
  const m = p.media || [], first = m[0] || {};
  const parts = [p.format === "carousel" && m.length > 1 ? `Carousel · ${m.length} slides` : (first.type === "video" ? (p.format === "reel" ? "Reel" : "Video") : FMT_NAME[p.format] || "Image")];
  if (first.ratio) parts.push(first.ratio);
  if (first.type === "video") parts.push(first.duration ? fmtDur(first.duration) : "…");
  if (first.role === "original") parts.push("your file");
  return parts.join(" · ");
}
function pkgMedia(p) {
  const m = p.media && p.media.length ? p.media : (p.media_urls || []).map((u) => ({ url: u, type: isVid(u) ? "video" : "photo", ratio: p.ratio }));
  if (!m.length) return `<div class="pkg-media empty" data-testid="pkg-media"><span class="muted sm">${p.service === "youtube" || p.service === "tiktok" ? "Needs a video" : "Text only"}</span></div>`;
  const ar = (r) => (r ? `aspect-ratio:${r.replace(":", " / ")}` : "");
  const first = m[0];
  const main = first.type === "video"  // #t: the browser shows a real frame before play, never a black tile
    ? `<video src="${esc(first.url)}#t=0.1" controls playsinline preload="metadata" data-testid="pkg-video" style="${ar(first.ratio)}"></video>`
    : `<button type="button" class="pm-img" data-open="0" data-testid="pkg-media-open" aria-label="Open full size" style="${ar(first.ratio || "4:5")}"><img src="${esc(first.url)}" alt="${esc(netName(p.service))} ${esc(p.format)}, slide 1"></button>`;
  const strip = m.length > 1 ? `<div class="pm-strip" data-testid="pkg-slides">${m.map((x, i) => `<button type="button" data-open="${i}" data-testid="pkg-slide" aria-label="Slide ${i + 1}"><img src="${esc(x.url)}" alt=""></button>`).join("")}</div>` : "";
  return `<div class="pkg-media" data-testid="pkg-media">${main}${strip}
    <div class="pm-meta"><span class="pm-fmt" data-testid="pkg-format">${esc(mediaLabel(p))}</span>
      <button type="button" class="link" data-open="0" data-testid="pkg-fullsize">Full size</button></div></div>`;
}

function pkgCard(p) {
  const ch = chOf(p.channel_id), sent = ["sent_to_buffer", "pending_approval"].includes(p.status);
  const help = PKG_MODES[0][2];
  return `<article class="pkg" data-testid="package" data-ch="${esc(p.channel_id)}">
    <header>${window.chMark ? window.chMark(ch && ch.id ? ch : { service: p.service }, true) : avatar(ch)}<div><b>${esc(p.channel_name)}</b><small>${esc(netName(p.service))}</small></div><span class="cnt ${p.length > p.limit ? "over" : ""}" data-testid="pkg-count">${p.length}/${p.limit}</span></header>
    ${pkgMedia(p)}
    ${sent ? "" : `<div class="pm-acts"><button type="button" class="btn2 sm" data-testid="pkg-replace">Replace</button><button type="button" class="btn2 sm" data-testid="pkg-regen">Regenerate</button></div>`}
    ${p.note ? `<small class="muted sm" data-testid="pkg-note">${esc(p.note)}</small>` : ""}
    <label class="sr" for="pt-${esc(p.channel_id)}">Caption for ${esc(p.channel_name)}</label>
    <textarea id="pt-${esc(p.channel_id)}" data-testid="pkg-text" rows="5" ${sent ? "disabled" : ""}>${esc(p.text)}</textarea>
    ${checkList(p.check)}
    ${p.status === "hold" ? `<div class="st warn" data-testid="pkg-hold">On hold: answer the question above first</div>` : sent ? `<div class="st ok" data-testid="pkg-sent">${p.status === "pending_approval" ? "Waiting in Approvals" : "In Buffer"}${p.sent && p.sent.mode === "draft" ? " as a draft (not posted)" : ""}${p.sent && p.sent.due_at ? " · " + esc(fmtWhen(p.sent.due_at)) : ""}</div>`
      : `<div class="pkg-act"><select data-testid="pkg-mode" aria-label="What to do with this post">${PKG_MODES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><input type="datetime-local" data-testid="pkg-when" hidden aria-label="When">
        <button class="btn sm" data-testid="pkg-send">${PKG_MODES[0][3]}</button></div><small class="muted sm pkg-help" data-testid="pkg-help">${esc(help)}</small>`}
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
        el.querySelector(".chk-res").outerHTML = checkList(p.check); } catch (e) { toast(e.message); } }, 450); };
    // a video's length when the server didn't record it: read from the file itself
    const v = el.querySelector("[data-testid=pkg-video]");
    const setDur = () => { const f = el.querySelector("[data-testid=pkg-format]"); if (f && f.textContent.includes("…") && isFinite(v.duration)) f.textContent = f.textContent.replace("…", fmtDur(v.duration)); };
    if (v) { v.addEventListener("loadedmetadata", setDur); if (v.readyState >= 1) setDur(); }
    el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => window.openMedia && window.openMedia((pk.media && pk.media.length ? pk.media : (pk.media_urls || []).map((u) => ({ url: u, type: isVid(u) ? "video" : "photo" }))), +b.dataset.open, `${netName(pk.service)} · ${mediaLabel(pk)}`)));
    const busyBtn = (b, txt) => { b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>${txt}`; };
    const rep = el.querySelector("[data-testid=pkg-replace]");
    if (rep) rep.onclick = () => window.pickFromLibrary && window.pickFromLibrary(async (item) => {
      busyBtn(rep, "Replacing…");
      try { showRun(await api(`/api/agent/runs/${r.id}/packages/${ch}/media`, { method: "POST", body: { token: item.media_token } })); toast("Media replaced"); }
      catch (e) { toast(e.message, 6000); rep.disabled = false; rep.textContent = "Replace"; }
    }, { title: `Replace ${netName(pk.service)} media`, upload: true, video: pk.service === "youtube" || pk.service === "tiktok" });
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

async function loadRuns() {
  const box = $("#runList");
  try {
    const rows = await api("/api/agent/runs");
    box.innerHTML = rows.length ? rows.map((x) => `<button class="runrow" data-testid="run-row" data-id="${x.id}" data-kind="${esc(x.kind)}">${x.cover ? (isVid(x.cover) ? `<span class="vt">▶</span>` : `<img src="${esc(x.cover)}" alt="">`) : `<span class="vt">·</span>`}
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
window.loadLedger = async function () { try { drawLedger((await api("/api/brand")).rules); } catch (e) { $("#ledger").innerHTML = `<div class="st err">${esc(e.message)}</div>`; } };
// the brand check of the sample cover against the ledger, shown under the kit's live preview
window.brandCheck = async function (box) { try { const p = await api("/api/brand/preview", { method: "POST" }); box.innerHTML = checkList(p.check); } catch (_) { box.innerHTML = ""; } };
function drawLedger(rules) {
  const box = $("#ledger");
  box.innerHTML = rules.length ? `<ul class="rules">${rules.map((r) => `<li class="${r.active ? "" : "off"}" data-testid="rule-row"><span class="tag ${r.kind}">${r.kind === "visual" ? "Look" : "Words"}</span>
      <div class="rtxt"><b>${esc(r.rule)}</b><small>From “${esc(r.source)}”${r.hits ? ` · caught ${r.hits} time${r.hits > 1 ? "s" : ""}` : ""}</small></div>
      <label class="switch sm"><input type="checkbox" role="switch" data-act="${r.id}" ${r.active ? "checked" : ""} aria-label="Rule on"><span></span></label>
      <button class="link danger" data-del="${r.id}" aria-label="Delete rule">Delete</button></li>`).join("")}</ul>`
    : `<div class="empty"><b>No rules yet</b><span>Correct a post (“logo smaller”, “no hype”) and HuhQueue writes the rule here. Or teach one above.</span></div>`;
  box.querySelectorAll("[data-act]").forEach((c) => (c.onchange = async () => { await api(`/api/ledger/${c.dataset.act}`, { method: "POST", body: { active: c.checked } }); loadLedger(); }));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/ledger/${b.dataset.del}`, { method: "DELETE" }); loadLedger(); }));
}
$("#ruleForm").addEventListener("submit", async (ev) => {
  ev.preventDefault(); const v = $("#ruleIn").value.trim(); if (!v) return;
  try { const r = await api("/api/ledger", { method: "POST", body: { correction: v } });
    toast(r.added.length ? "Learned: " + r.added.map((x) => x.rule).join(" · ") : r.message, 5000);
    if (r.added.length) $("#ruleIn").value = ""; drawLedger(r.ledger); } catch (e) { toast(e.message, 5000); }
});
// ---------------------------------------------------------------- AI engine
async function loadAI() {
  const v = await api("/api/ai"); H.ai = v;
  $("#aiNow").innerHTML = v.own ? `Your agent writes with <b>your ${esc(v.presets[v.own.provider]?.label || v.own.provider)} key</b> (${esc(v.own.model)}, ${esc(v.own.key_hint)}).`
    : v.free_starter ? `Your agent writes with the <b>free starter model</b> (${v.free_daily} AI calls a day). Add your own key for more, or use HuhQueue from Claude or ChatGPT.`
    : `Your agent writes with the <b>built-in writer</b> (no AI). Add a key or connect Claude/ChatGPT for richer captions.`;
  const sel = $("#aiProv");
  sel.innerHTML = Object.entries(v.presets).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  sel.value = v.own?.provider || "openai";
  const sync = () => { const p = v.presets[sel.value]; $("#aiModel").value = v.own?.provider === sel.value ? v.own.model : p.model; $("#aiBaseF").hidden = sel.value !== "custom"; $("#aiBase").value = p.base_url || ""; };
  sel.onchange = sync; sync();
  $("#aiRemove").hidden = !v.own;
}
$("#aiForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const b = $("[data-testid=ai-save]"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Testing…`;
  try { await api("/api/ai", { method: "POST", body: { provider: $("#aiProv").value, model: $("#aiModel").value, base_url: $("#aiBase").value, api_key: $("#aiKey").value } });
    $("#aiKey").value = ""; toast("Key works and is saved"); loadAI(); }
  catch (e) { toast(e.message, 6000); } finally { b.disabled = false; b.textContent = "Test and save"; }
});
$("#aiRemove").onclick = async () => { await api("/api/ai", { method: "POST", body: { remove: true } }); toast("Key removed"); loadAI(); };
