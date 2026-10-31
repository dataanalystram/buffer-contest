// HuhQueue front end. Talks only to this server; Buffer tokens never reach the browser.
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

// Buffer-style character counting (mirrors studio/preflight.py count_text; the server is the source of truth)
const LIMITS = { twitter: 280, linkedin: 3000, instagram: 2196, facebook: 5000, threads: 500, bluesky: 300, mastodon: 500, tiktok: 2200, youtube: 5000, pinterest: 500, googlebusiness: 4000, startPage: 2000 };
const URL_RE = /https?:\/\/[^\s<>"]+/g;
const SEG = typeof Intl !== "undefined" && Intl.Segmenter ? new Intl.Segmenter() : null;
const graphemeList = (t) => (SEG ? [...SEG.segment(t)].map((x) => x.segment) : [...t]);
const X_LIGHT = [[0, 4351], [8192, 8205], [8208, 8223], [8242, 8247]];
const isEmoji = (g) => /\p{Extended_Pictographic}|\u200d|\ufe0f/u.test(g) && [...g].some((c) => c.codePointAt(0) > 0x22ff);
function xCount(t) {
  let n = 0;
  for (const g of graphemeList(t.normalize("NFC").replace(URL_RE, "\u0000"))) {
    if (g === "\u0000") { n += 23; continue; }
    if (isEmoji(g)) { n += 2; continue; }
    for (const c of g) { const cp = c.codePointAt(0); n += X_LIGHT.some(([a, b]) => cp >= a && cp <= b) ? 1 : 2; }
  }
  return n;
}
function count(svc, t) {
  t = t.replace(/\r\n/g, "\n");
  if (svc === "twitter") return xCount(t);
  if (svc === "bluesky") return graphemeList(t.replace(URL_RE, (u) => { try { const p = new URL(u); return p.hostname + u.split(p.host)[1].slice(0, 16); } catch (_) { return u; } })).length;
  if (svc === "mastodon") return t.replace(URL_RE, "x".repeat(23)).replace(/(@[A-Za-z0-9_]+)@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "$1").length;
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

// ---------------------------------------------------------------- preflight ("Never Bounced")
// One panel used by the composer cards and the Planner dialog. It asks the server (the same check REST and MCP use),
// shows a green/amber/red chip, the findings with their source, a one-click "Fix it", and a "Send anyway" box for warnings.
const PF_WORD = { ok: "Ready", warning: "Check", error: "Blocked" };
const PF_ICON = { ok: "✓", warning: "!", error: "×" };
function pfChip(r, extra = "") {
  return `<span class="pf-chip s-${esc(r.status)}" data-testid="preflight-chip" data-status="${esc(r.status)}" ${extra}><b aria-hidden="true">${PF_ICON[r.status]}</b><span class="sr">${PF_WORD[r.status]}: </span>${esc(r.chip)}</span>`;
}
function pfPanel(box, getPost, onFix) {
  // ack: the warnings the person ticked "Send anyway" for. A re-check with the same warnings keeps the tick; new ones clear it.
  const st = { result: null, seq: 0, timer: 0, before: null, ack: "", inflight: null };
  const warnSig = (r) => (r && r.status === "warning" ? r.findings.filter((f) => f.level === "warning").map((f) => f.message).join("|") : "");
  box.classList.add("pf"); box.setAttribute("aria-live", "polite");
  const draw = () => {
    const r = st.result;
    if (!r) { box.innerHTML = `<div class="pf-row"><span class="pf-chip s-wait" data-testid="preflight-chip" data-status="checking">Checking…</span></div>`; return; }
    const fixes = (r.fixes || []).filter((f) => f.id !== "reencode");
    const items = r.findings.map((f) => `<li class="pf-f l-${esc(f.level)}" data-testid="preflight-finding" data-level="${esc(f.level)}"><span class="pf-lvl">${esc({ error: "Error", warning: "Warning", info: "Note" }[f.level])}</span><span>${esc(f.message)}${f.source ? ` <a href="${esc(f.source.split(" ; ")[0])}" target="_blank" rel="noopener" data-testid="preflight-source">Rule</a>` : ""}</span></li>`).join("");
    box.innerHTML = `<div class="pf-row">${pfChip(r)}
        ${fixes.length ? `<button type="button" class="btn2 sm pf-fix" data-testid="preflight-fix" title="${esc(fixes.map((f) => f.label).join(" · "))}">Fix it</button>` : ""}
        ${st.before ? `<button type="button" class="link" data-testid="preflight-undo">Undo fix</button>` : ""}</div>
      ${items ? `<ul class="pf-list" data-testid="preflight-findings">${items}</ul>` : ""}
      ${r.status === "warning" ? `<label class="chk pf-ov" data-testid="preflight-override-label"><input type="checkbox" data-testid="preflight-override"${st.ack && st.ack === warnSig(r) ? " checked" : ""}> Send anyway: I've read the warning</label>` : ""}`;
    const ov = $("[data-testid=preflight-override]", box);
    if (ov) ov.onchange = () => { st.ack = ov.checked ? warnSig(st.result) : ""; };
    const fb = $("[data-testid=preflight-fix]", box);
    if (fb) fb.onclick = fix;
    const ub = $("[data-testid=preflight-undo]", box);
    if (ub) ub.onclick = () => { const b = st.before; st.before = null; onFix({ text: b.text, media_urls: b.media_urls, media_url: b.media_urls[0] || "" }); run(); };
  };
  async function run(now = false) {
    clearTimeout(st.timer);
    const go = async () => {
      const post = getPost(); if (!post || !post.channel_id) { box.innerHTML = ""; st.result = null; return; }
      const my = ++st.seq; st.result = null; draw();
      try { const r = await api("/api/preflight", { method: "POST", body: post }); if (my === st.seq) { st.result = r.channels[0]; draw(); box.dispatchEvent(new CustomEvent("preflight", { bubbles: true })); } }
      catch (e) { if (my === st.seq) box.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
    };
    const track = () => { const p = go(); st.inflight = p; p.finally(() => { if (st.inflight === p) st.inflight = null; }); return p; };
    if (now) return track();
    st.timer = setTimeout(() => { st.timer = 0; track(); }, 450);
  }
  // Before a send: run a debounced check now and wait for any check in flight, so the send judges the post as it is.
  async function settle() {
    if (st.timer) { clearTimeout(st.timer); st.timer = 0; await run(true); }
    else if (st.inflight) await st.inflight;
    else if (!st.result) await run(true);
  }
  async function fix() {
    const post = getPost(), b = $("[data-testid=preflight-fix]", box);
    b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Fixing…`;
    try {
      const r = await api("/api/preflight/fix", { method: "POST", body: { ...post, fixes: "all" } });
      st.before = r.before; onFix(r); st.result = r.preflight; st.seq++; draw();
      box.dispatchEvent(new CustomEvent("preflight", { bubbles: true }));
      toast(r.applied.length ? `Fixed for ${r.preflight.network}: ${r.applied.length} change${r.applied.length > 1 ? "s" : ""}` + (r.needs_reencode ? " · the video still needs re-encoding" : "") : "Nothing to fix automatically", 4000);
    } catch (e) { toast(e.message, 5000); draw(); }
  }
  return {
    run, settle, get result() { return st.result; },
    override() { return !!st.result && st.result.status === "warning" && !!st.ack && st.ack === warnSig(st.result); },
    blocked() { // why sending must wait, or "" when it can go
      const r = st.result; if (!r) return "Preflight is still checking this post.";
      if (r.status === "error") return "Preflight found something Buffer would reject. Use Fix it or change the post.";
      if (r.status === "warning" && !this.override()) return "Preflight has a warning. Fix it, or tick Send anyway.";
      return "";
    },
  };
}
function pfStrip() {
  const strip = $("#pfStrip"); if (!strip) return;
  const cards = [...document.querySelectorAll("[data-testid=draft-card]")];
  const rs = cards.map((c) => c._pf && c._pf.result).filter(Boolean);
  strip.hidden = !cards.length;
  const n = { ok: 0, warning: 0, error: 0 }; rs.forEach((r) => n[r.status]++);
  strip.innerHTML = `<span class="pf-sum" data-testid="preflight-summary">Preflight: ${n.ok} ready${n.warning ? `, ${n.warning} to check` : ""}${n.error ? `, ${n.error} blocked` : ""}${rs.length < cards.length ? " (checking…)" : ""}</span>` +
    cards.map((c, i) => c._pf && c._pf.result ? `<button type="button" class="pf-jump" data-testid="preflight-strip-chip" data-i="${i}" data-service="${esc(c.dataset.service)}" aria-label="Go to the ${esc(netName(c.dataset.service))} post">${pfChip(c._pf.result)}</button>` : "").join("");
  strip.querySelectorAll(".pf-jump").forEach((b) => (b.onclick = () => { const c = cards[+b.dataset.i]; c.scrollIntoView({ behavior: "smooth", block: "center" }); $("textarea", c).focus({ preventScroll: true }); }));
}
document.addEventListener("preflight", pfStrip);

// ---------------------------------------------------------------- boot
async function boot() {
  const p = new URLSearchParams(location.search);
  if (p.get("error")) toast("Buffer: " + p.get("error"), 6000);
  if (p.get("connected")) toast("Connected to Buffer");
  const shared = p.get("url"); // from the bookmarklet: /?url=<page you were reading>
  if (p.has("error") || p.has("connected") || shared) history.replaceState({}, "", "/");
  const s = await api("/api/session");
  S.csrf = s.csrf; S.session = s;
  if (s.repo) $("#repoLink").href = s.repo; else $("#repoLink").hidden = true;
  showStats();
  if (!s.connected) {
    $("#landing").hidden = false;
    if (!s.configured) { $("#configNote").hidden = false; $("#connectBtn").classList.add("disabled"); }
    return;
  }
  $("#app").hidden = false; $("#topRight").hidden = false; $("#landNav").hidden = true; $("#demoBar").hidden = !s.demo;
  $("#acctBtn").textContent = (s.user.name || s.user.email || "Account") + (s.user.organization ? " · " + s.user.organization : "");
  setKill(s.user.kill_switch);
  snippets(s.mcp_url);
  await loadChannels();
  budget();
  if (shared && /^https?:\/\//.test(shared)) { $("#urlInput").value = shared; showTab("studio"); $("#draftForm").requestSubmit(); }
  else showTab(new URLSearchParams(location.search).get("tab") || "agent");
}

async function showStats() {
  try {
    const st = await api("/api/stats"), n = st.buffer_accounts, m = st.posts_sent_to_buffer;
    if (!n) return;
    const html = `<span class="live" aria-hidden="true"></span><span><b>${n.toLocaleString()}</b> Buffer account${n === 1 ? "" : "s"} connected</span><span><b>${m.toLocaleString()}</b> post${m === 1 ? "" : "s"} sent to Buffer</span><span><b>${(st.sample_workspaces_open || 0).toLocaleString()}</b> trying the sample today</span>`;
    for (const el of [$("#landStats"), $("#homeStats")]) { el.innerHTML = html; el.hidden = false; }
  } catch (_) {}
}
$("#bookmarklet").href = `javascript:location.href='${location.origin}/?url='+encodeURIComponent(location.href)`;
$("#bookmarklet").onclick = (e) => { e.preventDefault(); toast("Drag this link to your bookmarks bar, then click it on any article."); };
function setKill(on) {
  const p = $("#killPill"); p.textContent = on ? "Agents paused" : "Agents on"; p.className = "status " + (on ? "off" : "on");
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
  box.innerHTML = S.channels.length ? "" : `<span class="muted">No channels in this Buffer organization yet. Add one in Buffer, then refresh.</span>`;
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
  const b = $("#draftBtn"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Reading the page…`;
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
    const el = document.createElement("div"); el.className = "draft deal svc-" + d.service; el.style.setProperty("--i", i); el.setAttribute("data-testid", "draft-card"); el.dataset.service = d.service;
    el.innerHTML = `
      <div class="post">
        <div class="dh">${avatar(ch)}<div class="who"><b>${esc(d.channel_name)}</b><small>${esc(ch.name ? "@" + ch.name : "")}</small></div><span class="net">${esc(netName(d.service))}</span></div>
        <textarea data-testid="draft-text" aria-label="Post for ${esc(d.channel_name)} on ${esc(netName(d.service))}">${esc(d.text)}</textarea>
        <ol class="thread" data-testid="thread-preview" hidden></ol>
        <img class="pic" alt="" hidden>
      </div>
      <div class="meter"><div class="bar" aria-hidden="true"><i></i></div><div class="row"><span>${d.engine === "ai" ? "Written by AI" : "Written from the article"}</span><span data-testid="draft-count"></span></div></div>
      <div class="media"><input data-testid="draft-media" type="url" aria-label="Image or video URL" placeholder="Image or video URL (https)" value="${esc(d.media_url)}"></div>
      <div data-testid="preflight-panel"></div>
      <div class="dact"><select data-testid="draft-mode" aria-label="How to send">${modeOptions(ch.needs_media && !d.media_url ? "draft" : "queue")}</select>
        <input type="datetime-local" data-testid="draft-due" aria-label="Date and time" hidden>
        <button class="btn2" data-testid="send-draft">Send to Buffer</button></div>
      <div data-testid="draft-status"></div>`;
    const ta = $("textarea", el), meter = $("[data-testid=draft-count]", el), mode = $("[data-testid=draft-mode]", el), due = $("[data-testid=draft-due]", el);
    const bar = $(".bar", el), thread = $(".thread", el), pic = $(".pic", el), media = $("[data-testid=draft-media]", el);
    const upd = () => {
      const lim = limitFor(d.service), parts = splitThread(d.service, ta.value);
      const n = Math.max(...parts.map((p) => count(d.service, p)), 0);
      const lvl = n > lim ? "over" : n > lim * 0.9 ? "near" : "";
      meter.innerHTML = `<span class="${lvl}">${lvl === "over" ? "Too long: " : ""}${n.toLocaleString()} / ${lim.toLocaleString()}${parts.length > 1 ? " in the longest part" : ""}</span>`;
      bar.className = "bar " + lvl; bar.firstChild.style.width = Math.min(100, (n / lim) * 100) + "%";
      thread.hidden = parts.length < 2;
      thread.innerHTML = parts.length < 2 ? "" : parts.map((p, i) => `<li><span class="n">${i + 1} of ${parts.length}${count(d.service, p) > lim ? ", too long" : ""}</span>${esc(p)}</li>`).join("");
    };
    const updPic = () => { const u = media.value.trim(); pic.hidden = !/^https:\/\/\S+\.(png|jpe?g|gif|webp)(\?|$)/i.test(u) && !(u && u === d.media_url); if (!pic.hidden) pic.src = u; };
    pic.onerror = () => (pic.hidden = true);
    el._pf = pfPanel($("[data-testid=preflight-panel]", el), () => ({ channel_id: d.channel_id, text: ta.value, media_url: media.value.trim(), mode: mode.value }),
      (r) => { ta.value = r.text; media.value = r.media_url || ""; upd(); updPic(); });
    ta.addEventListener("input", () => { upd(); el._pf.run(); }); upd(); media.addEventListener("input", () => { updPic(); el._pf.run(); }); updPic();
    mode.addEventListener("change", () => { due.hidden = mode.value !== "schedule"; el._pf.run(); });
    $("[data-testid=send-draft]", el).onclick = () => sendCard(el, d);
    box.appendChild(el);
  });
  document.querySelectorAll("[data-testid=draft-card]").forEach((c) => c._pf.run(true));
  pfStrip();
}

async function sendCard(el, d, forcedMode) {
  const st = $("[data-testid=draft-status]", el), btn = $("[data-testid=send-draft]", el);
  const mode = forcedMode || $("[data-testid=draft-mode]", el).value;
  const dueVal = $("[data-testid=draft-due]", el).value;
  const body = { channel_id: d.channel_id, text: $("textarea", el).value, media_url: $("[data-testid=draft-media]", el).value.trim(), mode };
  if (mode === "schedule") { if (!dueVal) { st.innerHTML = `<div class="st err">Pick a date and time</div>`; return false; } body.due_at = new Date(dueVal).toISOString(); }
  if (el._pf) {
    await el._pf.settle();
    const why = el._pf.blocked();
    if (why) { st.innerHTML = `<div class="st err" data-testid="draft-err">${esc(why)}</div>`; return false; }
    if (el._pf.override()) body.override_warnings = true;
  }
  btn.disabled = true; btn.innerHTML = `<span class="spin" aria-hidden="true"></span>Sending…`;
  try {
    const pv = await api("/api/posts/preview", { method: "POST", body });
    const res = await api("/api/posts/submit", { method: "POST", body: { confirm_token: pv.confirm_token } });
    const label = { draft: "Saved as a draft in Buffer", queue: "Added to your Buffer queue", next: "Up next in Buffer", now: "Sharing now via Buffer", schedule: "Scheduled in Buffer" }[res.mode] || "Sent to Buffer";
    st.innerHTML = `<div class="st ok" data-testid="draft-ok">${label}${res.post?.dueAt ? " · " + new Date(res.post.dueAt).toLocaleString() : ""}</div>` + pv.warnings.map((w) => `<div class="st warn">${esc(w)}</div>`).join("");
    btn.textContent = "Sent"; el.classList.add("sent"); return true;
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
  try { await api("/api/ideas", { method: "POST", body: { title: S.article.title, text: (first ? first.value : S.article.description) } }); toast("Saved to your Buffer ideas"); }
  catch (e) { toast(e.message, 5000); }
};
$("#refreshCh").onclick = () => loadChannels(true);

// ---------------------------------------------------------------- tabs
document.querySelectorAll(".rail button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
function showTab(name) {
  if (name === "calendar" || name === "queue") { // both live in the Planner now
    if (name === "queue") { try { const s = JSON.parse(localStorage.getItem("hq_planner") || "{}"); s.view = "list"; localStorage.setItem("hq_planner", JSON.stringify(s)); } catch (_) {} }
    name = "planner";
  }
  if (!document.getElementById("tab-" + name)) name = "agent";
  if (name !== "planner") history.replaceState(null, "", name === "agent" ? "/" + (S.session?.demo ? "?demo=1" : "") : "/?tab=" + name);
  document.querySelectorAll(".rail button").forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("on", on); on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"); });
  window.scrollTo({ top: 0 });
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  ({ home: loadHome, radar: loadRadar, approvals: loadApprovals, agents: loadTokens, activity: loadAudit }[name] || (window.HS_TABS || {})[name] || (() => {}))();
}

// ---------------------------------------------------------------- home: what needs you
$("#quickForm").addEventListener("submit", (ev) => { ev.preventDefault(); $("#urlInput").value = $("#quickUrl").value; showTab("studio"); $("#draftForm").requestSubmit(); });
async function loadHome() {
  const name = S.session.demo ? "" : (S.session.user.name || "").split(" ")[0];
  const [appr, upcoming] = await Promise.all([refreshBadge(), api("/api/queue?status=scheduled").catch(() => [])]);
  $("#homeTitle").textContent = appr.length ? `${appr.length === 1 ? "One post is" : appr.length + " posts are"} waiting for you` : name ? `All clear, ${name}` : "All clear";
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
  box.innerHTML = todo.length ? todo.map(([t, sub, btn, go]) => `<li data-testid="todo-item"><div class="what"><span>${t}</span><small>${sub}</small></div><button class="${go === "approvals" ? "btn accent sm" : "btn2 sm"}" data-testid="todo-go" data-go="${go}">${btn}</button></li>`).join("")
    : `<li data-testid="todo-item"><span class="calm-dot" aria-hidden="true"></span><div class="what"><span>Nothing needs you right now</span><small>Paste a link above, or connect an agent to draft for you.</small></div></li>`;
  box.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => (b.dataset.go === "refresh" ? loadChannels(true).then(loadHome) : showTab(b.dataset.go))));
  const up = $("#upcoming");
  up.innerHTML = upcoming.length ? "" : `<div class="empty"><b>Nothing scheduled yet</b><span>Posts you send to your Buffer queue show up here.</span></div>`;
  upcoming.slice(0, 4).forEach((p) => up.insertAdjacentHTML("beforeend", postItem(p)));
  loadPlan();
  const hs = $("#homeStories");
  api("/api/radar").then((r) => {
    hs.innerHTML = r.stories.length ? r.stories.slice(0, 3).map(storyItem).join("")
      : `<div class="empty"><b>No feeds yet</b><span>Follow the sites you share from in News radar, and new stories land here.</span></div>`;
    wireStories(hs);
  }).catch(() => (hs.innerHTML = ""));
}
// ---------------------------------------------------------------- plan limits: warn before Buffer says no
async function loadPlan() {
  let p; try { p = await api("/api/plan"); } catch (_) { return; }
  const box = $("#planBox");
  const meter = (m) => { const lvl = m.pct >= 100 ? "over" : m.pct >= 80 ? "near" : "";
    return `<div class="pm" data-testid="plan-meter"><div class="row"><span>${esc(m.label)}</span><span class="${lvl}">${m.used.toLocaleString()} of ${m.limit.toLocaleString()}</span></div><div class="bar ${lvl}" aria-hidden="true"><i style="width:${Math.min(100, m.pct)}%"></i></div></div>`; };
  box.innerHTML = p.meters.length ? p.meters.map(meter).join("") + (p.locked_channels.length ? `<div class="st err">Locked in Buffer: ${esc(p.locked_channels.join(", "))}. Posts to these channels won't go out until you unlock them.</div>` : "")
    : `<div class="empty"><b>No plan limits to show</b><span>Buffer didn't share limits for this organization, so HuhQueue checks each channel's daily limit when you send.</span></div>`;
  const api_ = (p.buffer_api.windows || []).find((w) => w.quota && w.remaining / w.quota < 0.1);
  const worst = p.near_limit.sort((x, y) => y.pct - x.pct)[0];
  let msg = "";
  if (worst) msg = worst.pct >= 100 ? `You've used all ${worst.limit} ${worst.label.toLowerCase()} your Buffer plan allows. New posts won't be accepted until some go out or you upgrade.`
    : `You've used ${worst.used} of ${worst.limit} ${worst.label.toLowerCase()} on your Buffer plan.`;
  else if (p.locked_channels.length) msg = `${p.locked_channels.length} channel${p.locked_channels.length > 1 ? "s are" : " is"} locked in Buffer because of your plan.`;
  const bar = $("#limitBar"), key = "hq_limit_dismissed:" + msg;
  let dismissed = false; try { dismissed = sessionStorage.getItem(key) === "1"; } catch (_) {}
  if (msg && !dismissed) { $("#limitText").textContent = msg; $("#limitUpgrade").hidden = false; bar.hidden = false; }
  else if (api_ && !dismissed) { $("#limitText").textContent = `HuhQueue has ${api_.remaining} Buffer API requests left for the next ${api_.window}. Reading may pause briefly; sending still works.`; $("#limitUpgrade").hidden = true; bar.hidden = false; }
  else bar.hidden = true;
  $("#limitUpgrade").href = p.upgrade_url;
  $("#limitDismiss").onclick = () => { try { sessionStorage.setItem(key, "1"); } catch (_) {} bar.hidden = true; };
}

// ---------------------------------------------------------------- news radar
function storyItem(st) {
  const ago = st.published ? timeAgo(st.published * 1000) : "";
  return `<article class="item story" data-testid="story-item">
    <div class="pv-body"><div class="meta">${esc(st.source)}${ago ? `, ${esc(ago)}` : ""}</div>
      <a class="story-title" href="${esc(st.url)}" target="_blank" rel="noopener">${esc(st.title)}</a>
      ${st.summary ? `<p class="muted story-sum">${esc(st.summary)}</p>` : ""}
      <div class="pv-foot"><span></span><button class="btn2 sm" data-testid="story-write" data-url="${esc(st.url)}">Write posts</button></div></div>
    ${st.image ? `<span class="pv-media"><img src="${esc(st.image)}" alt="" loading="lazy"></span>` : ""}</article>`;
}
function timeAgo(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 24) return `${h} h ago`;
  return new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });
}
function wireStories(box) {
  box.querySelectorAll("[data-testid=story-write]").forEach((b) => (b.onclick = () => { $("#urlInput").value = b.dataset.url; showTab("studio"); $("#draftForm").requestSubmit(); }));
}
async function loadRadar() {
  const box = $("#stories"), fl = $("#feeds");
  box.innerHTML = `<p class="muted">Checking your feeds…</p>`;
  try {
    const r = await api("/api/radar");
    fl.innerHTML = r.feeds.map((f) => `<span class="feed" data-testid="feed-row"><span>${esc(new URL(f.url).hostname.replace(/^www\./, ""))}</span><button class="link danger" data-testid="feed-remove" data-id="${f.id}" aria-label="Stop following ${esc(f.url)}">Remove</button></span>`).join("");
    fl.querySelectorAll("[data-testid=feed-remove]").forEach((b) => (b.onclick = async () => { await api(`/api/radar/feeds/${b.dataset.id}`, { method: "DELETE" }); loadRadar(); }));
    box.innerHTML = (r.errors || []).map((e) => `<div class="st err">${esc(new URL(e.feed).hostname)}: ${esc(e.error)}</div>`).join("")
      + (r.stories.length ? r.stories.map(storyItem).join("") : `<div class="empty"><b>No stories yet</b><span>Follow a site's feed above. Most news sites and blogs have one at /feed or /rss.</span></div>`);
    wireStories(box);
  } catch (e) { box.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
$("#feedForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const b = $("[data-testid=feed-add]"); b.disabled = true;
  try { const f = await api("/api/radar/feeds", { method: "POST", body: { url: $("#feedUrl").value.trim() } }); toast(`Following ${f.title}`); $("#feedUrl").value = ""; loadRadar(); }
  catch (e) { toast(e.message, 5000); }
  finally { b.disabled = false; }
});

// A post as it will look: who posts it, the text, its media. The media and the link open the post where it lives:
// on the network once sent, otherwise the channel's schedule in Buffer (Buffer has no per-post URL).
function postItem(p) {
  const ch = chOf(p.channelId), demo = S.session?.demo;
  const when = p.sentAt || p.dueAt, sent = p.status === "sent";
  const at = when ? new Date(when).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
  const label = { scheduled: `Scheduled for ${at}`, sent: `Sent ${at}`, draft: "Draft in Buffer", error: "Failed to send" }[p.status] || p.status;
  const href = demo ? "" : sent && p.externalLink ? p.externalLink : `https://publish.buffer.com/channels/${encodeURIComponent(p.channelId)}/schedule`;
  const go = sent && p.externalLink ? `View on ${netName(ch.service)}` : "Open in Buffer";
  const a = (p.assets || [])[0], isVid = a && /video/.test(a.type + a.mimeType), thumb = a && (a.thumbnail || (!isVid && a.source));
  const media = !a ? "" : `${href ? `<a class="pv-media" href="${esc(href)}" target="_blank" rel="noopener" aria-label="${esc(go)}">` : `<span class="pv-media">`}${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : ""}${isVid ? `<span class="play" aria-hidden="true"></span>` : ""}${(p.assets || []).length > 1 ? `<span class="more">+${p.assets.length - 1}</span>` : ""}${href ? "</a>" : "</span>"}`;
  return `<article class="item pv svc-${esc(ch.service)}${p.error ? " failed" : ""}" data-testid="queue-item">
    <div class="pv-body"><div class="dh">${avatar(ch)}<div class="who"><b>${esc(ch.displayName || ch.name)}</b><small>${esc(ch.name ? "@" + ch.name + " on " : "")}${esc(netName(ch.service))}</small></div></div>
      <pre>${esc(p.text)}</pre>
      ${p.error ? `<div class="st err">${esc(p.error.message)}</div>` : ""}
      <div class="pv-foot"><span class="muted">${esc(label)}</span>${href ? `<a href="${esc(href)}" target="_blank" rel="noopener" data-testid="open-post">${esc(go)}</a>` : ""}</div></div>
    ${media}</article>`;
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
  if (!rows.length) { box.innerHTML = `<div class="empty"><b>Nothing waiting</b><span>When an agent writes a post, it shows up here for you to read before anything reaches Buffer.</span></div>`; return; }
  box.innerHTML = "";
  for (const r of rows) {
    const ch = chOf(r.payload.channel_id);
    const el = document.createElement("article"); el.className = `appr svc-${ch.service}`; el.setAttribute("data-testid", "approval-item");
    el.innerHTML = `<div class="appr-head"><span><b>${esc(r.agent_name)}</b> wrote this for ${esc(ch.displayName || ch.name)} on ${esc(netName(ch.service))}</span><small>${new Date(r.created * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</small></div>
      <div class="appr-body"><div class="dh">${avatar(ch)}<div class="who"><b>${esc(ch.displayName || ch.name)}</b><small>${esc(ch.name ? "@" + ch.name : "")} on ${esc(netName(ch.service))}</small></div></div>
      <textarea data-testid="approval-text" aria-label="Post text">${esc(r.payload.text)}</textarea></div>
      <div class="appr-act"><button class="btn2 danger" data-testid="reject">Reject</button>
      <span class="right"><select data-testid="approval-mode" aria-label="How to send">${modeOptions(r.payload.mode === "schedule" ? "queue" : r.payload.mode)}</select>
      <button class="btn accent" data-testid="approve">Approve and send to Buffer</button></span></div><div data-testid="approval-status"></div>`;
    $("[data-testid=approve]", el).onclick = async () => {
      try { const res = await api(`/api/approvals/${r.id}/approve`, { method: "POST", body: { text: $("textarea", el).value, mode: $("select", el).value } });
        $("[data-testid=approval-status]", el).innerHTML = `<div class="st ok">Sent to Buffer (${esc(res.mode)})</div>`; el.classList.add("sent"); setTimeout(loadApprovals, 1200); refreshBadge(); }
      catch (e) { $("[data-testid=approval-status]", el).innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
    };
    $("[data-testid=reject]", el).onclick = async () => { await api(`/api/approvals/${r.id}/reject`, { method: "POST" }); toast("Rejected. Nothing was sent."); loadApprovals(); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- agents & safety
function snippets(url, token = "hq_YOUR_TOKEN") {
  $("#snipClaude").textContent = `claude mcp add --transport http huhqueue ${url} \\\n  --header "Authorization: Bearer ${token}"`;
  $("#snipJson").textContent = JSON.stringify({ mcpServers: { huhqueue: { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${token}`] } } }, null, 2);
}
async function loadTokens() {
  const r = await api("/api/safety"); setKill(r.kill_switch);
  const box = $("#tokens");
  if (!r.tokens.length) { box.innerHTML = `<p class="muted">No agents yet. Create a token below and paste the setup line into your agent.</p>`; return; }
    const POL = { review: "Needs my approval", draft: "Buffer drafts only", trusted: "Can schedule" };
  box.innerHTML = `<div class="wrap-x"><table class="data"><thead><tr><th scope="col">Agent</th><th scope="col">May do</th><th scope="col" class="num">Per day</th><th scope="col">Last used</th><th scope="col"><span class="sr">Actions</span></th></tr></thead><tbody>${r.tokens.map((t) => `<tr data-testid="token-row"><td><b>${esc(t.name)}</b></td><td class="muted">${esc(POL[t.policy] || t.policy)}</td><td class="num">${t.daily_cap}</td><td class="muted">${t.last_used ? new Date(t.last_used * 1000).toLocaleString() : "Never"}</td><td class="num">${t.revoked ? `<span class="tag">Revoked</span>` : `<button class="link danger" data-testid="revoke-token" data-id="${t.id}">Revoke</button>`}</td></tr>`).join("")}</tbody></table></div>`;
  box.querySelectorAll("[data-testid=revoke-token]").forEach((b) => (b.onclick = async () => { await api(`/api/agent-tokens/${b.dataset.id}`, { method: "DELETE" }); loadTokens(); }));
}
$("#tokenForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const t = await api("/api/agent-tokens", { method: "POST", body: { name: $("#tokName").value, policy: $("#tokPolicy").value, daily_cap: +$("#tokCap").value } });
    const rv = $("#tokenReveal"); rv.hidden = false;
    rv.innerHTML = `<b>Copy this token now. It is shown once.</b><code data-testid="token-value">${esc(t.token)}</code><span><button class="btn2 sm" data-testid="copy-token">Copy token</button></span>`;
    $("[data-testid=copy-token]", rv).onclick = () => navigator.clipboard.writeText(t.token).then(() => toast("Copied"));
    snippets(S.session.mcp_url, t.token); loadTokens();
  } catch (e) { toast(e.message, 5000); }
});
async function toggleKill(on) { const r = await api("/api/safety/kill", { method: "POST", body: { on } }); setKill(r.kill_switch); toast(on ? "Kill switch on. No agent can draft or post until you turn it off." : "Agents can act again"); }
$("#killToggle").onchange = (e) => toggleKill(e.target.checked);
$("#killPill").onclick = () => toggleKill(!$("#killToggle").checked);

// ---------------------------------------------------------------- audit
async function loadAudit() {
  const rows = await api("/api/audit"), box = $("#audit");
  if (!rows.length) { box.innerHTML = `<p class="muted">No activity yet.</p>`; return; }
  box.innerHTML = `<div class="wrap-x"><table class="data"><thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Risk</th><th scope="col">Detail</th></tr></thead><tbody>${rows.map((r) => `<tr data-testid="audit-row"><td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td><span class="tag ${r.ok ? esc(r.risk) : "fail"}">${r.ok ? esc(r.risk) : "failed"}</span></td><td><code>${esc(JSON.stringify(r.detail)).slice(0, 160)}</code></td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- account
$("#acctBtn").onclick = () => { const m = $("#acctMenu"); m.hidden = !m.hidden; $("#acctBtn").setAttribute("aria-expanded", !m.hidden); };
document.addEventListener("click", (e) => { if (!e.target.closest(".acct")) { $("#acctMenu").hidden = true; $("#acctBtn").setAttribute("aria-expanded", "false"); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#acctMenu").hidden) { $("#acctMenu").hidden = true; $("#acctBtn").focus(); } });
$("#deviceBtn").onclick = async () => {
  $("#acctMenu").hidden = true;
  try { const r = await api("/api/device-link", { method: "POST" }); await navigator.clipboard.writeText(r.url).catch(() => {}); toast("One-time link, valid 10 min (copied): " + r.url, 20000); }
  catch (e) { toast(e.message, 5000); }
};
$("#logoutBtn").onclick = async () => { await api("/api/logout", { method: "POST" }); location.href = "/"; };
$("#disconnectBtn").onclick = async () => { if (!confirm("Disconnect Buffer and delete everything HuhQueue stored for you?")) return; await api("/api/disconnect", { method: "POST" }); location.href = "/"; };

boot().catch((e) => toast(e.message, 6000));
