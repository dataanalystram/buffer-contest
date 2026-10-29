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
const THREADS = new Set(["twitter", "bluesky", "threads", "mastodon"]);
const NETWORK = { twitter: "X", linkedin: "LinkedIn", instagram: "Instagram", bluesky: "Bluesky", threads: "Threads", mastodon: "Mastodon", facebook: "Facebook", tiktok: "TikTok", youtube: "YouTube", pinterest: "Pinterest", googlebusiness: "Google Business" };
const netName = (svc) => NETWORK[svc] || svc;
const splitThread = (svc, t) => (THREADS.has(svc) ? t.split(/\n[ \t]*---[ \t]*\n/).filter((p) => p.trim()) : [t]);
const initials = (n) => (n || "?").replace(/[^\p{L}\p{N} ]/gu, "").split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
const avatar = (c) => (c.avatar ? `<img class="av" src="${esc(c.avatar)}" alt="">` : `<span class="av" aria-hidden="true">${esc(initials(c.displayName || c.name))}</span>`);
const chOf = (id) => S.channels.find((c) => c.id === id) || { id, service: "", name: id };

// ---------------------------------------------------------------- boot
async function boot() {
  const p = new URLSearchParams(location.search);
  if (p.get("error")) toast("Buffer: " + p.get("error"), 6000);
  if (p.get("connected")) toast("Connected to Buffer ✓");
  if (p.has("error") || p.has("connected")) history.replaceState({}, "", "/");
  const s = await api("/api/session");
  S.csrf = s.csrf; S.session = s;
  if (s.repo) $("#repoLink").href = s.repo; else $("#repoLink").hidden = true;
  if (!s.connected) {
    $("#landing").hidden = false;
    if (!s.configured) { $("#configNote").hidden = false; $("#connectBtn").classList.add("disabled"); }
    return;
  }
  $("#app").hidden = false; $("#topRight").hidden = false; $("#demoBar").hidden = !s.demo;
  $("#acctBtn").textContent = (s.user.name || s.user.email || "Account") + (s.user.organization ? " · " + s.user.organization : "");
  setKill(s.user.kill_switch);
  snippets(s.mcp_url);
  await loadChannels();
  budget(); showTab("home");
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
    el.classList.add("svc-" + c.service); el.setAttribute("aria-pressed", S.selected.has(c.id));
    el.innerHTML = `${avatar(c)}<span>${esc(c.displayName || c.name)}</span><small>${esc(netName(c.service))}</small>`;
    el.onclick = () => { if (c.isDisconnected) return toast("Reconnect this channel in Buffer first"); S.selected.has(c.id) ? S.selected.delete(c.id) : S.selected.add(c.id); el.classList.toggle("on"); el.setAttribute("aria-pressed", S.selected.has(c.id)); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- drafting
$("#draftForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const url = $("#urlInput").value.trim(); if (!url) return;
  if (!S.selected.size) return toast("Pick at least one channel");
  const b = $("#draftBtn"); b.disabled = true; b.innerHTML = `<span class="spin"></span>Reading the page and writing posts…`;
  try {
    const r = await api("/api/draft", { method: "POST", body: { url, channel_ids: [...S.selected], tone: $("#toneSelect").value, thread: $("#threadChk").checked } });
    S.article = r.article; S.drafts = r.drafts; renderArticle(); renderDrafts();
  } catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; b.textContent = "Write posts"; budget(); }
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
    const el = document.createElement("div"); el.className = "draft svc-" + d.service; el.setAttribute("data-testid", "draft-card"); el.dataset.service = d.service;
    el.innerHTML = `
      <div class="post">
        <div class="dh">${avatar(ch)}<div class="who"><b>${esc(d.channel_name)}</b><small>${esc(ch.name ? "@" + ch.name : "")}</small></div><span class="net">${esc(netName(d.service))}</span></div>
        <textarea data-testid="draft-text" aria-label="Post for ${esc(d.channel_name)} on ${esc(netName(d.service))}">${esc(d.text)}</textarea>
        <ol class="thread" data-testid="thread-preview" hidden></ol>
        <img class="pic" alt="" hidden>
      </div>
      <div class="meter"><span data-testid="draft-count"></span><span>${d.engine === "ai" ? "Written by AI" : "Written from the article"}. Edit freely.</span></div>
      <div class="media"><input data-testid="draft-media" aria-label="Image or video URL" placeholder="Image or video URL (https)" value="${esc(d.media_url)}"></div>
      <div class="dact"><select data-testid="draft-mode" aria-label="How to send">${modeOptions(ch.needs_media && !d.media_url ? "draft" : "queue")}</select>
        <input type="datetime-local" data-testid="draft-due" aria-label="Date and time" hidden>
        <button class="btn" data-testid="send-draft">Send to Buffer</button></div>
      <div data-testid="draft-status"></div>`;
    const ta = $("textarea", el), meter = $("[data-testid=draft-count]", el), mode = $("[data-testid=draft-mode]", el), due = $("[data-testid=draft-due]", el);
    const thread = $(".thread", el), pic = $(".pic", el), media = $("[data-testid=draft-media]", el);
    const upd = () => {
      const lim = limitFor(d.service), parts = splitThread(d.service, ta.value);
      const n = Math.max(...parts.map((p) => count(d.service, p)), 0);
      meter.innerHTML = `<span class="${n > lim ? "over" : n > lim * 0.9 ? "near" : ""}">${n} / ${lim}${parts.length > 1 ? " in the longest post" : ""}</span>`;
      thread.hidden = parts.length < 2;
      thread.innerHTML = parts.length < 2 ? "" : parts.map((p, i) => `<li><span class="n">${i + 1} of ${parts.length}${count(d.service, p) > lim ? ", too long" : ""}</span>${esc(p)}</li>`).join("");
    };
    const updPic = () => { const u = media.value.trim(); pic.hidden = !/^https:\/\/\S+\.(png|jpe?g|gif|webp)(\?|$)/i.test(u) && !(u && u === d.media_url); if (!pic.hidden) pic.src = u; };
    pic.onerror = () => (pic.hidden = true);
    ta.addEventListener("input", upd); upd(); media.addEventListener("input", updPic); updPic();
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
document.querySelectorAll(".rail button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".rail button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  ({ home: loadHome, approvals: loadApprovals, queue: loadQueue, agents: loadTokens, activity: loadAudit }[name] || (() => {}))();
}

// ---------------------------------------------------------------- home: what needs you
$("#quickForm").addEventListener("submit", (ev) => { ev.preventDefault(); $("#urlInput").value = $("#quickUrl").value; showTab("studio"); $("#draftForm").requestSubmit(); });
async function loadHome() {
  const name = (S.session.user.name || "").split(" ")[0];
  $("#homeTitle").textContent = S.session.demo ? "Here's what needs you in the sample workspace." : name ? `Here's what needs you, ${name}.` : "Here's what needs you.";
  const [appr, upcoming] = await Promise.all([refreshBadge(), api("/api/queue?status=scheduled").catch(() => [])]);
  const todo = [];
  if (appr.length) {
    const who = [...new Set(appr.map((a) => a.agent_name))].join(" and ");
    todo.push([`<span class="hl">${appr.length} post${appr.length > 1 ? "s" : ""}</span> from ${esc(who)} waiting for your approval`, "Nothing reaches Buffer until you approve it.", "Review", "approvals"]);
  }
  const unsent = [...document.querySelectorAll("[data-testid=send-draft]")].filter((b) => !b.disabled).length;
  if (unsent) todo.push([`<span class="hl">${unsent} draft${unsent > 1 ? "s" : ""}</span> you wrote but haven't sent`, esc(S.article?.title || ""), "Open", "studio"]);
  const off = S.channels.filter((c) => c.isDisconnected);
  if (off.length) todo.push([`<span class="hl">${off.length} channel${off.length > 1 ? "s" : ""}</span> disconnected in Buffer`, esc(off.map((c) => c.displayName || c.name).join(", ")) + ". Reconnect in Buffer, then refresh.", "Refresh", "refresh"]);
  if (S.session.user.kill_switch) todo.push(["Your agents are paused", "The kill switch is on, so agents can't draft or post.", "Agents", "agents"]);
  const box = $("#todo");
  box.innerHTML = todo.length ? todo.map(([t, sub, btn, go]) => `<li data-testid="todo-item"><span class="ic"></span><div class="what">${t}<small>${sub}</small></div><button class="ghost" data-testid="todo-go" data-go="${go}">${btn}</button></li>`).join("")
    : `<li data-testid="todo-item"><span class="ic calm"></span><div class="what">All clear<small>Paste a link above, or connect an agent to draft for you.</small></div></li>`;
  box.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => (b.dataset.go === "refresh" ? loadChannels(true).then(loadHome) : showTab(b.dataset.go))));
  const up = $("#upcoming");
  up.innerHTML = upcoming.length ? "" : `<div class="empty">Nothing scheduled in Buffer yet.</div>`;
  upcoming.slice(0, 4).forEach((p) => up.insertAdjacentHTML("beforeend", postItem(p)));
}
function postItem(p) {
  const ch = chOf(p.channelId);
  return `<div class="item svc-${esc(ch.service)}" data-testid="queue-item"><div class="meta">${esc(ch.displayName || ch.name)} on ${esc(netName(ch.service))}, ${esc(p.status)}${p.dueAt ? ", " + new Date(p.dueAt).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : ""}</div><pre>${esc(p.text)}</pre></div>`;
}

// ---------------------------------------------------------------- approvals
async function refreshBadge() {
  try {
    const r = await api("/api/approvals");
    for (const b of [$("#apprBadge"), $("#homeBadge")]) { b.textContent = r.length; b.hidden = !r.length; }
    return r;
  } catch (_) { return []; }
}
async function loadApprovals() {
  const box = $("#approvals"), rows = await refreshBadge();
  if (!rows.length) { box.innerHTML = `<div class="empty">Nothing waiting. When an agent writes a post, it shows up here for you to approve.</div>`; return; }
  box.innerHTML = "";
  for (const r of rows) {
    const ch = chOf(r.payload.channel_id);
    const el = document.createElement("div"); el.className = `item agent svc-${ch.service}`; el.setAttribute("data-testid", "approval-item");
    el.innerHTML = `<div class="meta"><b>${esc(r.agent_name)}</b> wrote this for <b>${esc(ch.displayName || ch.name)}</b> on ${esc(netName(ch.service))}, ${new Date(r.created * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</div>
      <textarea data-testid="approval-text">${esc(r.payload.text)}</textarea>
      <div class="dact"><select data-testid="approval-mode">${modeOptions(r.payload.mode === "schedule" ? "queue" : r.payload.mode)}</select>
      <button class="btn" data-testid="approve">Approve and send to Buffer</button><button class="ghost" data-testid="reject">Reject</button></div><div data-testid="approval-status"></div>`;
    $("[data-testid=approve]", el).onclick = async () => {
      try { const res = await api(`/api/approvals/${r.id}/approve`, { method: "POST", body: { text: $("textarea", el).value, mode: $("select", el).value } });
        $("[data-testid=approval-status]", el).innerHTML = `<div class="st ok">✓ Sent to Buffer (${esc(res.mode)})</div>`; setTimeout(loadApprovals, 1200); refreshBadge(); }
      catch (e) { $("[data-testid=approval-status]", el).innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
    };
    $("[data-testid=reject]", el).onclick = async () => { await api(`/api/approvals/${r.id}/reject`, { method: "POST" }); toast("Rejected. Nothing was sent."); loadApprovals(); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- queue
let queueReq = 0;
async function loadQueue() {
  const box = $("#queue"), my = ++queueReq; box.innerHTML = `<p class="hint">Loading from Buffer…</p>`;
  try {
    const rows = await api("/api/queue?status=" + $("#queueStatus").value);
    if (my !== queueReq) return; // a newer request (e.g. filter changed) owns the list
    box.innerHTML = rows.length ? "" : `<div class="empty">Nothing here in Buffer.</div>`;
    let day = "";
    for (const p of rows) {
      const d = p.dueAt ? new Date(p.dueAt).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" }) : "No time set";
      if (d !== day) { day = d; box.insertAdjacentHTML("beforeend", `<div class="day">${esc(d)}</div>`); }
      box.insertAdjacentHTML("beforeend", postItem(p));
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
