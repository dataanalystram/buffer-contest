// HuhQueue front end: the agent, its timeline, before/after, the Style Ledger, the brand kit, AI engine.
"use strict";
const H = { src: "topic", run: null, poll: null, sel: new Set(), ai: null, upload: null };
const STATUS_ICON = { pending: "", running: '<span class="spin" aria-hidden="true"></span>', done: "✓", warn: "!", failed: "×" };
const QUICK_FIXES = ["Logo smaller", "Logo top left", "No hashtags", "Less hype", "Headlines in sentence case", "Darker overlay", "No end card"];
const fmtWhen = (iso) => (iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const isVid = (u) => /\.(mp4|mov|m4v|webm)(\?|$)/i.test(u || "");

window.HS_TABS = Object.assign(window.HS_TABS || {}, { agent: loadAgent, ai: loadAI });

// ---------------------------------------------------------------- agent composer
function setSrc(src) {
  H.src = src;
  document.querySelectorAll(".seg [data-src]").forEach((b) => { const on = b.dataset.src === src; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  $("#fTopic").hidden = src !== "topic"; $("#fLink").hidden = src !== "link"; $("#fMedia").hidden = src !== "media";
}
document.querySelectorAll(".seg [data-src]").forEach((b) => (b.onclick = () => setSrc(b.dataset.src)));

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
function pickFile(f) {
  if (f.size > 40 * 1024 * 1024) return toast("Files up to 40 MB");
  H.upload = f; $("#dropTxt").textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
}
const readB64 = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

$("#agentForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!H.sel.size) return toast("Pick at least one channel");
  const body = { channel_ids: [...H.sel], format: $("#agFormat").value };
  const b = $("#agGo"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Starting…`;
  try {
    if (H.src === "topic") { body.query = $("#agQuery").value.trim(); if (!body.query) throw new Error("Give the agent a topic"); }
    if (H.src === "link") { body.url = $("#agUrl").value.trim(); if (!body.url) throw new Error("Paste a link"); }
    if (H.src === "media") {
      if (!H.upload) throw new Error("Choose a photo or video first");
      b.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
      const up = await api("/api/media", { method: "POST", body: { name: H.upload.name, data: await readB64(H.upload) } });
      body.media_token = up.token; body.brief = $("#agBrief").value.trim();
    }
    const run = await api("/api/agent/runs", { method: "POST", body });
    showRun(run); watch(run.id);
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; b.textContent = "Make it"; }
});

function watch(id) {
  clearTimeout(H.poll);
  const tick = async () => {
    try {
      const r = await api(`/api/agent/runs/${id}`); showRun(r);
      if (["running", "rendering"].includes(r.status)) H.poll = setTimeout(tick, 1400);
      else { loadRuns(); readyBadge(); }
    } catch (e) { toast(e.message); }
  };
  H.poll = setTimeout(tick, 900);
}

// ---------------------------------------------------------------- run view
function showRun(r) {
  H.run = r; $("#runView").hidden = false;
  $("#timeline").innerHTML = r.steps.map((s) => `<li class="step ${s.status}" data-testid="step" data-key="${s.key}">
      <span class="dot" aria-hidden="true">${STATUS_ICON[s.status] || ""}</span>
      <div><b>${esc(s.label)}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}${stepExtra(s)}</div></li>`).join("");
  const res = r.result || {}, media = res.media || [];
  const rev = (res.revisions || []).slice(-1)[0];
  let html = "";
  if (rev && rev.after && rev.before && rev.before[0] && rev.after[0] && !isVid(rev.after[0])) {
    html += `<div class="ba" data-testid="before-after"><div class="ba-frame"><img src="${esc(rev.after[0])}" alt="After"><div class="ba-before" style="width:50%"><img src="${esc(rev.before[0])}" alt="Before"></div><span class="ba-tag l">Before</span><span class="ba-tag r">After</span></div>
      <input type="range" min="0" max="100" value="50" aria-label="Compare before and after" data-testid="ba-slider"><p class="muted sm">“${esc(rev.correction)}” → ${esc((rev.rules || []).join(" · ") || "no new rule")}</p></div>`;
  }
  if (media.length) {
    html += `<div class="strip" data-testid="media-strip">${media.map((m, i) => isVid(m.url) || m.type === "video"
      ? `<video src="${esc(m.url)}" controls playsinline preload="metadata" data-testid="media-video"></video>`
      : `<a href="${esc(m.url)}" target="_blank" rel="noopener"><img src="${esc(m.url)}" alt="Slide ${i + 1}" data-testid="media-img"></a>`).join("")}</div>`;
  } else if (["running", "rendering"].includes(r.status)) html += `<div class="ph" aria-hidden="true"><span class="spin"></span></div>`;
  $("#mediaBox").innerHTML = html;
  const ba = $("#mediaBox .ba input");
  if (ba) ba.oninput = () => ($("#mediaBox .ba-before").style.width = ba.value + "%");
  const done = ["ready", "failed"].includes(r.status) && res.format;
  $("#reviseForm").hidden = !done || r.status === "failed";
  $("#quickfix").innerHTML = QUICK_FIXES.map((q) => `<button type="button" class="chip mini" data-fix="${esc(q)}">${esc(q)}</button>`).join("");
  $("#quickfix").querySelectorAll("[data-fix]").forEach((b) => (b.onclick = () => { $("#reviseIn").value = b.dataset.fix.toLowerCase(); $("#reviseForm").requestSubmit(); }));
  const pk = res.packages || [];
  $("#pkgHead").hidden = !pk.length || r.status !== "ready";
  $("#packages").innerHTML = r.status === "ready" ? pk.map(pkgCard).join("") : "";
  wirePackages(r);
}

function stepExtra(s) {
  const d = s.data || {};
  if (s.key === "find" && d.leads) return `<ul class="leads">${d.leads.slice(0, 3).map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a> <span class="muted">${esc(l.source || "")}</span></li>`).join("")}</ul>`;
  if (s.key === "verify" && d.url) return `<ul class="leads"><li>Read: <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.site)}</a></li>${(d.other_outlets || []).map((o) => `<li>Also: <a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.source)}</a></li>`).join("")}</ul>`;
  if (s.key === "structure" && d.facts) return `<ul class="facts">${d.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
  return "";
}

function checkList(chk) {
  if (!chk) return "";
  const fails = chk.results.filter((x) => !x.pass), passes = chk.results.filter((x) => x.pass);
  return `<div class="chk-res ${chk.pass ? "ok" : "bad"}" data-testid="brand-check"><b>${chk.pass ? "Passes your brand rules" : "Breaks " + fails.length + " rule" + (fails.length > 1 ? "s" : "")}</b>
    ${fails.map((f) => `<span class="rl bad">✕ ${esc(f.rule)}${f.why ? ` <em>${esc(f.why)}</em>` : ""}</span>`).join("")}
    ${passes.length ? `<details><summary>${passes.length} rule${passes.length > 1 ? "s" : ""} checked</summary>${passes.map((f) => `<span class="rl">✓ ${esc(f.rule)}</span>`).join("")}</details>` : ""}</div>`;
}

function pkgCard(p) {
  const ch = chOf(p.channel_id), sent = ["sent_to_buffer", "pending_approval"].includes(p.status);
  const thumbs = (p.media_urls || []).slice(0, 4).map((u) => (isVid(u) ? `<span class="vt">▶ Reel</span>` : `<img src="${esc(u)}" alt="">`)).join("");
  return `<article class="pkg" data-testid="package" data-ch="${esc(p.channel_id)}">
    <header>${avatar(ch)}<div><b>${esc(p.channel_name)}</b><small>${esc(netName(p.service))}</small></div><span class="cnt ${p.length > p.limit ? "over" : ""}" data-testid="pkg-count">${p.length}/${p.limit}</span></header>
    <div class="thumbs">${thumbs || `<span class="muted sm">Text only</span>`}</div>
    <textarea data-testid="pkg-text" rows="5" ${sent ? "disabled" : ""}>${esc(p.text)}</textarea>
    ${checkList(p.check)}
    ${sent ? `<div class="st ok" data-testid="pkg-sent">${p.status === "pending_approval" ? "Waiting in Approvals" : "In Buffer"}${p.sent && p.sent.due_at ? " · " + esc(fmtWhen(p.sent.due_at)) : ""}</div>`
      : `<div class="pkg-act"><select data-testid="pkg-mode">${modeOptions("queue")}</select><input type="datetime-local" data-testid="pkg-when" hidden aria-label="When">
        <button class="btn sm" data-testid="pkg-send">Send to Buffer</button></div>`}
  </article>`;
}

function wirePackages(r) {
  document.querySelectorAll("#packages .pkg").forEach((el) => {
    const ch = el.dataset.ch, ta = el.querySelector("textarea"), mode = el.querySelector("[data-testid=pkg-mode]"), when = el.querySelector("[data-testid=pkg-when]");
    let t;
    ta.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
      try { const p = await api(`/api/agent/runs/${r.id}/packages/${ch}`, { method: "POST", body: { text: ta.value } });
        el.querySelector("[data-testid=pkg-count]").textContent = `${p.length}/${p.limit}`;
        el.querySelector(".chk-res").outerHTML = checkList(p.check); } catch (e) { toast(e.message); } }, 450); };
    if (mode) mode.onchange = () => (when.hidden = mode.value !== "schedule");
    const send = el.querySelector("[data-testid=pkg-send]");
    if (send) send.onclick = async () => {
      send.disabled = true;
      try {
        const body = { mode: mode.value };
        if (mode.value === "schedule") { if (!when.value) throw new Error("Pick a time"); body.due_at = new Date(when.value).toISOString(); }
        const out = await api(`/api/agent/runs/${r.id}/packages/${ch}/schedule`, { method: "POST", body });
        toast(out.status === "pending_approval" ? "Waiting for approval" : "Sent to Buffer" + (out.warnings?.length ? " · " + out.warnings[0] : ""), 4500);
        const fresh = await api(`/api/agent/runs/${r.id}`); showRun(fresh); readyBadge();
      } catch (e) { toast(e.message, 6000); send.disabled = false; }
    };
  });
}

$("#reviseForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const c = $("#reviseIn").value.trim(); if (!c || !H.run) return;
  const b = $("[data-testid=revise-go]"); b.disabled = true;
  try {
    const r = await api(`/api/agent/runs/${H.run.id}/revise`, { method: "POST", body: { correction: c } });
    const box = $("#learned"); box.hidden = false;
    box.innerHTML = r.learned.length ? `<div class="st ok">Learned: ${r.learned.map((x) => esc(x.rule)).join(" · ")}. Every future post follows ${r.learned.length > 1 ? "these" : "this"}.</div>` : `<div class="st warn">${esc(r.message)}</div>`;
    $("#reviseIn").value = ""; showRun(r); watch(r.id);
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; }
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

async function loadAgent() {
  agentChips(); loadRuns(); readyBadge();
  try { H.ai = await api("/api/ai"); $("#agEngine").textContent = "Writing with: " + H.ai.engine; } catch (_) {}
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
