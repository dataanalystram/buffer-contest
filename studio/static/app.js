// HuhQueue Studio front end. Talks only to this server; Buffer tokens never reach the browser.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MODES = [["draft", "Buffer draft"], ["queue", "Add to queue"], ["next", "Share next"], ["now", "Share now"], ["schedule", "Schedule…"]];
const S = { csrf: "", channels: [], selected: new Set(), drafts: [], article: null, session: null };

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
  if (o.method !== "GET") o.headers["X-HQ-CSRF"] = S.csrf;
  if (opts.body !== undefined) o.body = JSON.stringify(opts.body);
  const r = await fetch(path, o);
  let j = null; try { j = await r.json(); } catch (_) { j = {}; }
  if (!r.ok) { const e = new Error(j.error || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return j;
}
function toast(msg, ms = 3200) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms); }

// Buffer-style character counting (mirrors studio/drafter.py)
const LIMITS = { twitter: 280, linkedin: 3000, instagram: 2196, facebook: 5000, threads: 500, bluesky: 300, mastodon: 500, tiktok: 2200, youtube: 5000, pinterest: 500, googlebusiness: 4000, startPage: 2000 };
const URL_RE = /https?:\/\/\S+/g;
function count(svc, t) {
  if (svc === "bluesky") return [...t.replace(URL_RE, (u) => { try { return new URL(u).hostname.slice(0, 40) + "x".repeat(16); } catch (_) { return u; } })].length;
  if (svc === "twitter" || svc === "mastodon") return t.replace(URL_RE, "x".repeat(23)).length;
  if (svc === "linkedin") return t.replace(URL_RE, "x".repeat(24)).length;
  if (svc === "instagram") return t.length + (t.match(/\n/g) || []).length;
  return t.length;
}
const limitFor = (svc) => LIMITS[svc] || 2000;

// ---------------------------------------------------------------- boot
async function boot() {
  const p = new URLSearchParams(location.search);
  if (p.get("error")) toast("Buffer: " + p.get("error"), 6000);
  if (p.get("connected")) toast("Connected to Buffer ✓");
  if (p.has("error") || p.has("connected")) history.replaceState({}, "", "/");
  const s = await api("/api/session");
  S.csrf = s.csrf; S.session = s;
  $("#repoLink").href = s.repo;
  if (!s.connected) {
    $("#landing").hidden = false;
    if (!s.configured) { $("#configNote").hidden = false; $("#connectBtn").classList.add("disabled"); }
    return;
  }
  $("#app").hidden = false; $("#topRight").hidden = false;
  $("#acctBtn").textContent = (s.user.name || s.user.email || "Account") + (s.user.organization ? " · " + s.user.organization : "");
  setKill(s.user.kill_switch);
  snippets(s.mcp_url);
  await loadChannels();
  refreshBadge(); budget();
}

function setKill(on) {
  const p = $("#killPill"); p.textContent = on ? "Agents paused" : "Agents on"; p.className = "pill " + (on ? "off" : "on");
  $("#killToggle").checked = !!on;
}
async function budget() {
  try {
    const l = await api("/api/limits"); const w = (l.buffer_api.windows || [])[0];
    $("#budgetPill").textContent = w ? `API ${w.remaining}/${w.quota ?? "?"} · ${w.window}` : "API budget ok";
  } catch (_) {}
}

// ---------------------------------------------------------------- channels
async function loadChannels(refresh = false) {
  const box = $("#chips");
  try { S.channels = await api("/api/channels" + (refresh ? "?refresh=1" : "")); }
  catch (e) { box.innerHTML = `<span class="st err">${esc(e.message)}</span>`; return; }
  if (!S.selected.size) S.channels.filter((c) => !c.isDisconnected).forEach((c) => S.selected.add(c.id));
  box.innerHTML = S.channels.length ? "" : `<span class="hint">No channels in this Buffer organization yet. Add one in Buffer, then refresh.</span>`;
  for (const c of S.channels) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "chip" + (S.selected.has(c.id) ? " on" : "") + (c.isDisconnected ? " dis" : "");
    el.dataset.testid = "channel-chip"; el.setAttribute("data-testid", "channel-chip"); el.dataset.id = c.id;
    el.innerHTML = `${c.avatar ? `<img src="${esc(c.avatar)}" alt="">` : `<span class="av"></span>`}<span>${esc(c.displayName || c.name)}</span><small>${esc(c.service)}</small>`;
    el.onclick = () => { if (c.isDisconnected) return toast("Reconnect this channel in Buffer first"); S.selected.has(c.id) ? S.selected.delete(c.id) : S.selected.add(c.id); el.classList.toggle("on"); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- drafting
$("#draftForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const url = $("#urlInput").value.trim(); if (!url) return;
  if (!S.selected.size) return toast("Pick at least one channel");
  const b = $("#draftBtn"); b.disabled = true; b.innerHTML = `<span class="spin"></span>Reading the page &amp; writing drafts… (AI can take ~30 s)`;
  try {
    const r = await api("/api/draft", { method: "POST", body: { url, channel_ids: [...S.selected], tone: $("#toneSelect").value } });
    S.article = r.article; S.drafts = r.drafts; renderArticle(); renderDrafts();
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; b.textContent = "Draft posts"; budget(); }
});

function renderArticle() {
  const a = S.article, box = $("#article"); box.hidden = !a;
  if (!a) return;
  box.innerHTML = `<div class="art">${a.image ? `<img src="${esc(a.image)}" alt="">` : ""}<div><h4>${esc(a.title)}</h4><p>${esc(a.site)}${a.description ? " · " + esc(a.description).slice(0, 160) : ""}</p></div></div>`;
}
function modeOptions(sel = "draft") { return MODES.map(([v, l]) => `<option value="${v}" ${v === sel ? "selected" : ""}>${l}</option>`).join(""); }

function renderDrafts() {
  const box = $("#drafts"); box.innerHTML = "";
  $("#studioEmpty").hidden = S.drafts.length > 0; $("#bulk").hidden = !S.drafts.length;
  $("#bulkMode").innerHTML = modeOptions("queue").replace('<option value="schedule"', '<option disabled value="schedule"');
  S.drafts.forEach((d, i) => {
    const ch = S.channels.find((c) => c.id === d.channel_id) || {};
    const el = document.createElement("div"); el.className = "draft"; el.setAttribute("data-testid", "draft-card"); el.dataset.service = d.service;
    el.innerHTML = `
      <div class="dh">${ch.avatar ? `<img class="av" src="${esc(ch.avatar)}" alt="">` : `<span class="av"></span>`}<b>${esc(d.channel_name)}</b><span class="svc">${esc(d.service)}</span></div>
      <textarea data-testid="draft-text">${esc(d.text)}</textarea>
      <div class="meter"><span data-testid="draft-count"></span><span>${d.engine === "ai" ? "AI draft" : "Draft"} · edit freely</span></div>
      <div class="media"><input data-testid="draft-media" placeholder="Image or video URL (https)" value="${esc(d.media_url)}">${d.media_url ? `<img src="${esc(d.media_url)}" alt="">` : ""}</div>
      <div class="dact"><select data-testid="draft-mode">${modeOptions(ch.needs_media && !d.media_url ? "draft" : "queue")}</select>
        <input type="datetime-local" data-testid="draft-due" hidden>
        <button class="btn" data-testid="send-draft">Send to Buffer</button></div>
      <div data-testid="draft-status"></div>`;
    const ta = $("textarea", el), meter = $("[data-testid=draft-count]", el), mode = $("[data-testid=draft-mode]", el), due = $("[data-testid=draft-due]", el);
    const upd = () => { const n = count(d.service, ta.value), lim = limitFor(d.service); meter.innerHTML = `<span class="${n > lim ? "over" : n > lim * 0.9 ? "near" : ""}">${n} / ${lim}</span>`; };
    ta.addEventListener("input", upd); upd();
    mode.addEventListener("change", () => (due.hidden = mode.value !== "schedule"));
    $("[data-testid=send-draft]", el).onclick = () => sendCard(el, d);
    box.appendChild(el);
  });
}

async function sendCard(el, d, forcedMode) {
  const st = $("[data-testid=draft-status]", el), btn = $("[data-testid=send-draft]", el);
  const mode = forcedMode || $("[data-testid=draft-mode]", el).value;
  const dueVal = $("[data-testid=draft-due]", el).value;
  const body = { channel_id: d.channel_id, text: $("textarea", el).value, media_url: $("[data-testid=draft-media]", el).value.trim(), mode };
  if (mode === "schedule") { if (!dueVal) { st.innerHTML = `<div class="st err">Pick a date and time</div>`; return false; } body.due_at = new Date(dueVal).toISOString(); }
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Sending…`;
  try {
    const pv = await api("/api/posts/preview", { method: "POST", body });
    const res = await api("/api/posts/submit", { method: "POST", body: { confirm_token: pv.confirm_token } });
    const label = { draft: "Saved as a draft in Buffer", queue: "Added to your Buffer queue", next: "Up next in Buffer", now: "Sharing now via Buffer", schedule: "Scheduled in Buffer" }[res.mode] || "Sent to Buffer";
    st.innerHTML = `<div class="st ok" data-testid="draft-ok">✓ ${label}${res.post?.dueAt ? " · " + new Date(res.post.dueAt).toLocaleString() : ""}</div>` + pv.warnings.map((w) => `<div class="st warn">${esc(w)}</div>`).join("");
    btn.textContent = "Sent ✓"; return true;
  } catch (e) { st.innerHTML = `<div class="st err" data-testid="draft-err">${esc(e.message)}</div>`; btn.disabled = false; btn.textContent = "Send to Buffer"; return false; }
  finally { budget(); }
}
$("#sendAll").onclick = async () => {
  const mode = $("#bulkMode").value; let ok = 0;
  const cards = [...document.querySelectorAll("[data-testid=draft-card]")];
  for (let i = 0; i < cards.length; i++) { if ($("[data-testid=send-draft]", cards[i]).disabled) continue; if (await sendCard(cards[i], S.drafts[i], mode)) ok++; }
  toast(`${ok} of ${cards.length} sent to Buffer`);
};
$("#saveIdea").onclick = async () => {
  if (!S.article) return;
  const first = $("[data-testid=draft-text]"); 
  try { await api("/api/ideas", { method: "POST", body: { title: S.article.title, text: (first ? first.value : S.article.description) } }); toast("Saved to your Buffer Ideas ✓"); }
  catch (e) { toast(e.message, 5000); }
};
$("#refreshCh").onclick = () => loadChannels(true);

// ---------------------------------------------------------------- tabs
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  ({ approvals: loadApprovals, queue: loadQueue, agents: loadTokens, activity: loadAudit }[name] || (() => {}))();
}

// ---------------------------------------------------------------- approvals
async function refreshBadge() {
  try { const r = await api("/api/approvals"); const b = $("#apprBadge"); b.textContent = r.length; b.hidden = !r.length; return r; } catch (_) { return []; }
}
async function loadApprovals() {
  const box = $("#approvals"), rows = await refreshBadge();
  if (!rows.length) { box.innerHTML = `<div class="empty">Nothing waiting. When an agent submits a post, it shows up here for you to approve.</div>`; return; }
  box.innerHTML = "";
  for (const r of rows) {
    const ch = S.channels.find((c) => c.id === r.payload.channel_id) || { service: "?", name: r.payload.channel_id };
    const el = document.createElement("div"); el.className = "item"; el.setAttribute("data-testid", "approval-item");
    el.innerHTML = `<div class="meta"><b>${esc(r.agent_name)}</b> wants to post to <b>${esc(ch.displayName || ch.name)}</b> (${esc(ch.service)}) · ${new Date(r.created * 1000).toLocaleString()}</div>
      <textarea data-testid="approval-text">${esc(r.payload.text)}</textarea>
      <div class="dact"><select data-testid="approval-mode">${modeOptions(r.payload.mode === "schedule" ? "queue" : r.payload.mode)}</select>
      <button class="btn" data-testid="approve">Approve → Buffer</button><button class="ghost" data-testid="reject">Reject</button></div><div data-testid="approval-status"></div>`;
    $("[data-testid=approve]", el).onclick = async () => {
      try { const res = await api(`/api/approvals/${r.id}/approve`, { method: "POST", body: { text: $("textarea", el).value, mode: $("select", el).value } });
        $("[data-testid=approval-status]", el).innerHTML = `<div class="st ok">✓ Sent to Buffer (${esc(res.mode)})</div>`; setTimeout(loadApprovals, 1200); }
      catch (e) { $("[data-testid=approval-status]", el).innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
    };
    $("[data-testid=reject]", el).onclick = async () => { await api(`/api/approvals/${r.id}/reject`, { method: "POST" }); loadApprovals(); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- queue
async function loadQueue() {
  const box = $("#queue"); box.innerHTML = `<p class="hint">Loading from Buffer…</p>`;
  try {
    const rows = await api("/api/queue?status=" + $("#queueStatus").value);
    box.innerHTML = rows.length ? "" : `<div class="empty">Nothing here in Buffer.</div>`;
    for (const p of rows) {
      const ch = S.channels.find((c) => c.id === p.channelId) || {};
      box.insertAdjacentHTML("beforeend", `<div class="item" data-testid="queue-item"><div class="meta">${esc(ch.displayName || ch.name || p.channelId)} · ${esc(ch.service || "")} · ${esc(p.status)}${p.dueAt ? " · " + new Date(p.dueAt).toLocaleString() : ""}</div><pre>${esc(p.text)}</pre></div>`);
    }
  } catch (e) { box.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
  budget();
}
$("#queueStatus").onchange = loadQueue; $("#queueReload").onclick = loadQueue;

// ---------------------------------------------------------------- agents & safety
function snippets(url, token = "hq_YOUR_TOKEN") {
  $("#snipClaude").textContent = `claude mcp add --transport http huhqueue ${url} \\\n  --header "Authorization: Bearer ${token}"`;
  $("#snipJson").textContent = JSON.stringify({ mcpServers: { huhqueue: { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${token}`] } } }, null, 2);
}
async function loadTokens() {
  const r = await api("/api/safety"); setKill(r.kill_switch);
  const box = $("#tokens");
  if (!r.tokens.length) { box.innerHTML = `<p class="hint">No agent tokens yet.</p>`; return; }
  box.innerHTML = `<table><tr><th>Name</th><th>Policy</th><th>Daily cap</th><th>Last used</th><th></th></tr>${r.tokens.map((t) => `<tr data-testid="token-row"><td>${esc(t.name)}</td><td><span class="tag">${esc(t.policy)}</span></td><td>${t.daily_cap}</td><td>${t.last_used ? new Date(t.last_used * 1000).toLocaleString() : "never"}</td><td>${t.revoked ? "revoked" : `<button class="link danger" data-testid="revoke-token" data-id="${t.id}">Revoke</button>`}</td></tr>`).join("")}</table>`;
  box.querySelectorAll("[data-testid=revoke-token]").forEach((b) => (b.onclick = async () => { await api(`/api/agent-tokens/${b.dataset.id}`, { method: "DELETE" }); loadTokens(); }));
}
$("#tokenForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const t = await api("/api/agent-tokens", { method: "POST", body: { name: $("#tokName").value, policy: $("#tokPolicy").value, daily_cap: +$("#tokCap").value } });
    const rv = $("#tokenReveal"); rv.hidden = false;
    rv.innerHTML = `<b>Copy this token now — it's shown once.</b><br><code data-testid="token-value">${esc(t.token)}</code> <button class="link" data-testid="copy-token">copy</button>`;
    $("[data-testid=copy-token]", rv).onclick = () => navigator.clipboard.writeText(t.token).then(() => toast("Copied"));
    snippets(S.session.mcp_url, t.token); loadTokens();
  } catch (e) { toast(e.message, 5000); }
});
async function toggleKill(on) { const r = await api("/api/safety/kill", { method: "POST", body: { on } }); setKill(r.kill_switch); toast(on ? "Kill switch ON: agents are paused" : "Agents can act again"); }
$("#killToggle").onchange = (e) => toggleKill(e.target.checked);
$("#killPill").onclick = () => toggleKill(!$("#killToggle").checked);

// ---------------------------------------------------------------- audit
async function loadAudit() {
  const rows = await api("/api/audit"), box = $("#audit");
  if (!rows.length) { box.innerHTML = `<p class="hint">No activity yet.</p>`; return; }
  box.innerHTML = `<table><tr><th>When</th><th>Who</th><th>Action</th><th>Risk</th><th>Detail</th></tr>${rows.map((r) => `<tr data-testid="audit-row"><td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td><span class="tag ${r.ok ? esc(r.risk) : "fail"}">${r.ok ? esc(r.risk) : "failed"}</span></td><td><code>${esc(JSON.stringify(r.detail)).slice(0, 160)}</code></td></tr>`).join("")}</table>`;
}

// ---------------------------------------------------------------- account
$("#acctBtn").onclick = () => ($("#acctMenu").hidden = !$("#acctMenu").hidden);
$("#deviceBtn").onclick = async () => {
  $("#acctMenu").hidden = true;
  try { const r = await api("/api/device-link", { method: "POST" }); await navigator.clipboard.writeText(r.url).catch(() => {}); toast("One-time link, valid 10 min (copied): " + r.url, 20000); }
  catch (e) { toast(e.message, 5000); }
};
$("#logoutBtn").onclick = async () => { await api("/api/logout", { method: "POST" }); location.href = "/"; };
$("#disconnectBtn").onclick = async () => { if (!confirm("Disconnect Buffer and delete everything HuhQueue stored for you?")) return; await api("/api/disconnect", { method: "POST" }); location.href = "/"; };

boot().catch((e) => toast(e.message, 6000));
