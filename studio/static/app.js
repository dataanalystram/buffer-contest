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
  const justConnected = p.has("connected");
  if (p.has("error") || p.has("connected") || shared) history.replaceState({}, "", "/");
  const s = await api("/api/session");
  S.csrf = s.csrf; S.session = s;
  if (s.repo) $("#repoLink").href = s.repo; else $("#repoLink").hidden = true;
  if (!s.connected) { // the landing page lives at "/" for visitors (served by the server); this page is the app only
    if (!sessionStorage.getItem("hq_bounced")) { sessionStorage.setItem("hq_bounced", "1"); location.replace("/" + location.search); }
    return;
  }
  sessionStorage.removeItem("hq_bounced");
  $("#app").hidden = false; $("#topRight").hidden = false; $("#demoBar").hidden = !s.demo;
  document.body.classList.add("in-app"); document.body.classList.toggle("is-demo", !!s.demo);
  $("#acctBtn").textContent = (s.user.name || s.user.email || "Account") + (s.user.organization ? " · " + s.user.organization : "");
  setKill(s.user.kill_switch);
  if (window.onbState) await onbState().catch(() => {}); // the channels picked in setup are every composer's default
  await loadChannels();
  budget(); refreshBadge(); if (typeof readyBadge === "function") readyBadge();
  api("/api/inbox").then((d) => ibBadge(d.waiting)).catch(() => {});
  if (shared && /^https?:\/\//.test(shared)) { $("#urlInput").value = shared; showTab("studio"); $("#draftForm").requestSubmit(); }
  else showTab(p.get("tab") || "inbox", p.get("sub") || "");
  if (window.onbMaybeOpen) onbMaybeOpen({ justConnected, demo: !!s.demo });
}

$("#bookmarklet").href = `javascript:location.href='${location.origin}/?url='+encodeURIComponent(location.href)`;
$("#bookmarklet").onclick = (e) => { e.preventDefault(); toast("Drag this link to your bookmarks bar, then click it on any article."); };
function setKill(on) {
  const p = $("#killPill"); p.textContent = on ? "Agents paused" : "Agents on"; p.className = "status " + (on ? "off" : "on");
  $("#killToggle").checked = !!on;
}
async function budget() {
  if (S.session?.demo) { $("#budgetPill").textContent = "Sample · 0 API calls"; $("#budgetPill").title = "The sample workspace never calls Buffer"; return; }
  try {
    const l = await api("/api/limits"); const w = (l.buffer_api.windows || [])[0];
    $("#budgetPill").textContent = w ? `API ${w.remaining}/${w.quota ?? "?"} · ${w.window}` : "API budget ok";
  } catch (_) {}
}

// ---------------------------------------------------------------- channels
// One default for every composer (Make a post, Quick text posts, One take): the channels picked in setup, else every
// connected channel. One take then drops the ones that can't take video.
function defaultChannels() {
  const live = S.channels.filter((c) => !c.isDisconnected);
  const pick = (S.onb && Array.isArray(S.onb.channels) ? S.onb.channels : []).filter((id) => live.some((c) => c.id === id));
  return pick.length ? live.filter((c) => pick.includes(c.id)) : live;
}
// "Write as threads" only means something for X, Bluesky, Threads and Mastodon: show it only when one is picked.
function threadOpt() { const o = $("#threadOpt"); if (!o) return; const nets = [...new Set([...S.selected].map((id) => chOf(id).service).filter((s) => THREADS.has(s)))].map(netName);
  o.hidden = !nets.length; if (!nets.length) $("#threadChk").checked = false;
  const t = o.lastChild; if (t && t.nodeType === 3) t.textContent = ` Write ${nets.length > 1 ? nets.slice(0, -1).join(", ") + " and " + nets.at(-1) : nets[0] || ""} posts as threads`; }
async function loadChannels(refresh = false) {
  const box = $("#chips");
  try { S.channels = await api("/api/channels" + (refresh ? "?refresh=1" : "")); }
  catch (e) { box.innerHTML = `<span class="st err">${esc(e.message)}</span>`; return; }
  if (!S.selected.size) defaultChannels().forEach((c) => S.selected.add(c.id));
  box.innerHTML = S.channels.length ? "" : `<span class="muted">No channels in this Buffer organization yet. Add one in Buffer, then refresh.</span>`;
  for (const c of S.channels) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "chip" + (S.selected.has(c.id) ? " on" : "") + (c.isDisconnected ? " dis" : "");
    el.dataset.testid = "channel-chip"; el.setAttribute("data-testid", "channel-chip"); el.dataset.id = c.id;
    el.classList.add("svc-" + c.service); el.setAttribute("aria-pressed", S.selected.has(c.id));
    el.innerHTML = `${avatar(c)}<span>${esc(c.displayName || c.name)}</span><small>${esc(netName(c.service))}</small>`;
    el.onclick = () => { if (c.isDisconnected) return toast("Reconnect this channel in Buffer first"); S.selected.has(c.id) ? S.selected.delete(c.id) : S.selected.add(c.id); el.classList.toggle("on"); el.setAttribute("aria-pressed", S.selected.has(c.id)); threadOpt(); };
    box.appendChild(el);
  }
  threadOpt();
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
    if (r.sample) toast(r.sample, 6000);
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

// ---------------------------------------------------------------- navigation
// Nine screens, each job in exactly one place (docs/IA.md has the old -> new map). Old names keep working: a saved link
// or a ⌘K command for "approvals", "studio", "radar", "sources", "brandstudio", "connect", "agents" or "ai" lands on the
// screen and sub-tab that now does that job.
const TAB_ALIAS = {
  home: ["inbox"], approvals: ["inbox", ""], calendar: ["planner"], queue: ["planner"],
  studio: ["agent", "quick"], radar: ["news", "feed"], sources: ["news", "scan"],
  brandstudio: ["brand", "kit"], ledger: ["brand", "ledger"], templates: ["brand", "templates"],
  connect: ["apps"], agents: ["apps"], ai: ["settings", ""],
};
const SUB_DEFAULT = { agent: "agent", news: "feed", brand: "kit" };
document.querySelectorAll(".rail button[data-tab]").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
// phone: a bottom bar with the four everyday screens and a Menu sheet for the rest (Esc or Close returns focus).
// On a desktop the sheet is just the lower part of the rail, so nothing is listed twice.
function moreSheet(open) {
  const btn = $("#moreBtn"), rail = $("#rail");
  rail.classList.toggle("more-open", open); btn.setAttribute("aria-expanded", open); $("#railScrim").hidden = !open;
  if (open) $("#railMore [data-tab]").focus(); else if (document.activeElement && $("#railMore").contains(document.activeElement)) btn.focus();
}
$("#moreBtn").onclick = () => moreSheet(!$("#rail").classList.contains("more-open"));
$("#moreClose").onclick = () => moreSheet(false);
$("#railScrim").onclick = () => moreSheet(false);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#rail").classList.contains("more-open")) moreSheet(false); });
// one generic "go to" for links between screens: a button with data-go="planner" or data-go="brand:ledger"
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-go]"); if (!g || g.dataset.act || !g.dataset.go) return;
  e.preventDefault(); ibGo(g.dataset.go);
});
function subTab(tab, sub) {
  const root = document.getElementById("tab-" + tab); if (!root) return "";
  const subs = [...root.querySelectorAll(":scope > .sub[data-sub]")]; if (!subs.length) return "";
  if (!subs.some((x) => x.dataset.sub === sub)) sub = SUB_DEFAULT[tab] || subs[0].dataset.sub;
  subs.forEach((x) => (x.hidden = x.dataset.sub !== sub));
  root.querySelectorAll(":scope > .pg-tabs [data-sub]").forEach((b) => { const on = b.dataset.sub === sub; b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
  root.dataset.sub = sub;
  (window.HS_SUBS || {})[tab + ":" + sub]?.();
  return sub;
}
document.querySelectorAll(".pg-tabs").forEach((seg) => {
  const tab = seg.closest(".tab").id.slice(4);
  seg.querySelectorAll("[data-sub]").forEach((b) => (b.onclick = () => { const sub = subTab(tab, b.dataset.sub); urlFor(tab, sub); }));
  seg.addEventListener("keydown", (e) => { // arrow keys move between sub-tabs (WAI-ARIA tabs)
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const bs = [...seg.querySelectorAll("[data-sub]")], i = bs.indexOf(document.activeElement); if (i < 0) return;
    const n = bs[(i + (e.key === "ArrowRight" ? 1 : bs.length - 1)) % bs.length]; n.click(); n.focus(); e.preventDefault();
  });
});
function urlFor(name, sub) {
  if (name === "planner") return;
  const demo = S.session?.demo ? "demo=1&" : "";
  const q = name === "inbox" ? "" : `tab=${name}${sub && sub !== SUB_DEFAULT[name] ? "&sub=" + sub : ""}`;
  history.replaceState(null, "", q || demo ? "/?" + (demo + q).replace(/&$/, "") : "/");
}
function showTab(name, sub = "") {
  if (TAB_ALIAS[name]) {
    if (name === "queue") { try { const s = JSON.parse(localStorage.getItem("hq_planner") || "{}"); s.view = "list"; localStorage.setItem("hq_planner", JSON.stringify(s)); } catch (_) {} }
    const [t, sb] = TAB_ALIAS[name]; if (name === "approvals") setTimeout(() => $("#ibApprSec")?.scrollIntoView({ block: "start" }), 300);
    name = t; sub = sub || sb || "";
  }
  if (!document.getElementById("tab-" + name)) name = "inbox";
  if ($("#rail").classList.contains("more-open")) moreSheet(false);
  const primary = !!document.querySelector(`.rail [data-tab="${name}"][data-primary]`);
  $("#moreBtn").classList.toggle("on", !primary);
  document.querySelectorAll(".rail button[data-tab]").forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("on", on); on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"); });
  window.scrollTo({ top: 0 });
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  sub = subTab(name, sub);
  urlFor(name, sub);
  ({ inbox: loadInbox, news: loadNews, apps: loadApps, settings: loadSettings }[name] || (window.HS_TABS || {})[name] || (() => {}))();
}
window.showTab = showTab;

// ---------------------------------------------------------------- Inbox: a dashboard of your day
// Stats tiles, the agent posts waiting for approval (as cards with each channel's media), today's queue, what needs
// attention (/api/inbox, the same as MCP get_inbox), agent activity with each app's mark, and posts recommended for you.
const IB_URG = { now: "Now", soon: "Soon", fyi: "This week" };
const IB_ICON = {
  approvals: '<path d="M5 12l5 5 9-10"/>', channels: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M4 4l16 16"/>',
  failed: '<circle cx="12" cy="12" r="8"/><path d="M12 8v5M12 16h.01"/>', preflight: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  undated: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>', open_slots: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M12 13v4M10 15h4"/>',
  plan: '<path d="M4 19h16M7 16V9M12 16V5M17 16v-4"/>' };
const ibWhen = (iso) => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }); };
const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
function ibItem(g, it) {
  const acts = (it.actions || []).map((a) => a.href
    ? `<a class="btn2 sm" data-testid="inbox-action" data-act="${esc(a.id)}" href="${esc(a.href)}" target="_blank" rel="noopener">${esc(a.label)}</a>`
    : `<button type="button" class="${a.id === "approve" || a.id === "fix" ? "btn accent sm" : "btn2 sm"}" data-testid="inbox-action" data-act="${esc(a.id)}" data-go="${esc(a.go || "")}" data-id="${esc(it.id ?? "")}">${esc(a.label)}</button>`).join("");
  if (g.key === "open_slots") return `<li class="ib-slot" data-testid="inbox-item" data-group="${g.key}" title="next ${esc(ibWhen(it.next))}">${netBadge(it.service)}<b>${it.count}</b><span class="sr"> open on ${esc(it.channel)}</span></li>`;
  if (g.key === "plan") return "";
  const meta = [it.channel, ibWhen(it.when)].filter(Boolean).map(esc).join(" · ");
  return `<li class="ib-item" data-testid="inbox-item" data-group="${g.key}" data-ref="${esc(it.ref || "")}">${netBadge(it.service)}
    <div class="ib-what"><small class="ib-meta">${it.agent ? aiWho(it.agent, 14) + " · " : ""}${meta}</small>${it.text ? `<span class="ib-text">${esc(it.text)}</span>` : ""}
      ${it.reason ? `<span class="ib-reason${it.status === "warning" ? " warn" : ""}" data-testid="inbox-reason"><b class="sr">${it.status === "warning" ? "Warning" : "Problem"}: </b>${esc(it.reason)}</span>` : ""}</div>
    ${acts ? `<div class="ib-acts">${acts}</div>` : ""}</li>`;
}
// network marks live in net-logos.js (simple-icons); `label` names the network for screen readers when no text says it
function netBadge(svc, label) { return window.netIcon ? netIcon(svc, label) : `<span class="net svc-${esc(svc || "")}" aria-hidden="true">${esc((svc || "?").slice(0, 2))}</span>`; }
function ibGroup(g) {
  const acts = (g.actions || []).map((a) => a.href
    ? `<a class="btn2 sm" data-testid="inbox-group-action" data-act="${esc(a.id)}" href="${esc(a.href)}" target="_blank" rel="noopener">${esc(a.label)}</a>`
    : `<button type="button" class="btn2 sm" data-testid="inbox-group-action" data-act="${esc(a.id)}" data-go="${esc(a.go || "")}">${esc(a.label)}</button>`).join("");
  const more = g.count > (g.items || []).length && g.key !== "plan" && g.key !== "open_slots" ? `<p class="muted sm ib-more">and ${g.count - g.items.length} more</p>` : "";
  return `<section class="ib-group u-${g.urgency}" id="ib-${g.key}" data-testid="inbox-group" data-group="${g.key}" data-urgency="${g.urgency}" data-count="${g.count}" aria-labelledby="ibh-${g.key}">
    <header class="ib-gh"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${IB_ICON[g.key] || ""}</svg>
      <h3 id="ibh-${g.key}" tabindex="-1">${esc(g.title)}</h3><span class="ib-count" data-testid="inbox-count" aria-label="${g.count} item${g.count === 1 ? "" : "s"}">${g.count}</span>
      <span class="ib-urg">${IB_URG[g.urgency]}</span></header>
    <p class="ib-sum" data-testid="inbox-group-summary">${esc(g.summary)}</p>
    ${g.key === "plan" ? `<div class="plan" id="planBox" data-testid="plan-box"></div>` : ""}
    ${(g.items || []).length && g.key !== "plan" ? `<ul class="${g.key === "open_slots" ? "ib-slots" : "ib-items"}">${g.items.map((it) => ibItem(g, it)).join("")}</ul>` : ""}${more}
    ${acts ? `<div class="ib-gacts">${acts}</div>` : ""}</section>`;
}
async function ibGo(go) {
  if (!go) return;
  if (go.startsWith("planner:")) { if (typeof plOpenRef === "function") return plOpenRef(go.slice(8)); return showTab("planner"); }
  const [t, sub] = go.split(":"); showTab(t, sub || "");
}
function ibTile(key, label, value, sub, tone = "", go = "") {
  return `<${go ? `button type="button" data-go="${go}"` : "div"} class="ib-tile ${tone}" data-testid="inbox-stat" data-stat="${key}"><span class="ib-tl">${esc(label)}</span><b class="ib-tv">${esc(value)}</b><span class="ib-ts">${esc(sub)}</span></${go ? "button" : "div"}>`;
}
async function loadInbox() {
  const box = $("#inboxGroups"), demo = !!S.session?.demo;
  const [d, sched, lim] = await Promise.all([api("/api/inbox").catch((e) => ({ error: e.message })), api("/api/queue?status=scheduled").catch(() => []), api("/api/limits").catch(() => null)]);
  box.removeAttribute("aria-busy");
  if (d.error) { box.innerHTML = `<div class="st err" data-testid="inbox-error">${esc(d.error)}</div>`; return; }
  ibBadge(d.waiting);
  const G = Object.fromEntries(d.groups.map((g) => [g.key, g]));
  // the dashboard shows approvals as cards above, and the Buffer plan only where it is real (never in the sample)
  const groups = d.groups.filter((g) => g.key !== "approvals" && !(demo && g.key === "plan"));
  const live = groups.filter((g) => g.count > 0), clear = groups.filter((g) => !g.count);
  const waiting = d.waiting - (demo && G.plan && G.plan.urgency === "now" ? 1 : 0);
  $("#inboxTitle").textContent = "Inbox";
  $("#inboxSummary").textContent = waiting ? `${waiting} thing${waiting === 1 ? "" : "s"} need${waiting === 1 ? "s" : ""} you, most urgent first.` : "You're all caught up. Nothing is waiting on you right now.";
  $("#inboxKill").hidden = !d.kill_switch;
  // stats tiles
  const today = new Date(), endDay = new Date(today); endDay.setHours(23, 59, 59, 999);
  const w = lim && (lim.buffer_api.windows || [])[0];
  $("#ibStats").innerHTML =
    ibTile("scheduled", "Scheduled", String(sched.length), sched.length ? `next ${hhmm(sched.map((p) => p.dueAt).filter(Boolean).sort()[0] || today.toISOString())}` : "nothing queued", "", "planner") +
    ibTile("approval", "Awaiting approval", String(G.approvals?.count || 0), G.approvals?.count ? "from your agents" : "none waiting", G.approvals?.count ? "hot" : "") +
    ibTile("failed", "Failed", String(G.failed?.count || 0), G.failed?.count ? "fix and reschedule" : "all delivered", G.failed?.count ? "bad" : "ok") +
    ibTile("budget", "Buffer requests left", demo ? "Sample" : w ? `${w.remaining}` : "OK", demo ? "0 used: nothing reaches Buffer" : w ? `of ${w.quota ?? "?"} this ${w.window}` : "plenty left");
  // today's queue: what Buffer will send in the next 24 hours
  const next = sched.filter((p) => p.dueAt && new Date(p.dueAt) - today < 864e5).sort((a, b) => a.dueAt.localeCompare(b.dueAt));
  const soon = next.length ? next : sched.filter((p) => p.dueAt).sort((a, b) => a.dueAt.localeCompare(b.dueAt)).slice(0, 4);
  $("#ibTodayH").textContent = next.length ? "Today's queue" : "Coming up";
  $("#ibToday").innerHTML = soon.length ? soon.slice(0, 6).map(todayRow).join("")
    : `<li class="ib-none">Nothing scheduled. <button class="link" type="button" data-go="agent" data-testid="inbox-empty-make">Make a post</button> or <button class="link" type="button" data-go="take" data-testid="inbox-empty-take">turn one video into a week</button>.</li>`;
  // needs attention
  $("#inboxJump").innerHTML = live.length > 2 ? live.map((g) => `<a class="ib-chip u-${g.urgency}" href="#ib-${g.key}" data-testid="inbox-jump-chip" data-group="${g.key}"><span>${esc(g.title)}</span><b>${g.count}</b></a>`).join("") : "";
  box.innerHTML = (live.length ? live.map(ibGroup).join("") : "")
    + (!live.some((g) => g.urgency !== "fyi") ? `<div class="ib-empty" data-testid="inbox-empty"><span class="calm-dot" aria-hidden="true"></span><div><b>All caught up</b><span>No bounces, warnings or undated drafts.</span></div></div>` : "")
    + (clear.length ? `<details class="ib-clear" data-testid="inbox-clear"><summary data-testid="inbox-clear-toggle">All clear: ${clear.map((g) => esc(g.title.toLowerCase())).join(", ")}</summary><ul>${clear.map((g) => `<li data-testid="inbox-clear-item" data-group="${g.key}"><span class="calm-dot" aria-hidden="true"></span>${esc(g.summary)}</li>`).join("")}</ul></details>` : "");
  $("#inboxJump").querySelectorAll("a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); const h = $("#ibh-" + a.dataset.group); h.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); h.focus({ preventScroll: true }); }));
  box.querySelectorAll("button[data-act]").forEach((b) => (b.onclick = async () => {
    const act = b.dataset.act;
    if (act === "refresh") return loadChannels(true).then(loadInbox);
    ibGo(b.dataset.go);
  }));
  if (G.plan && !demo) loadPlan(); else $("#limitBar").hidden = true;
  loadApprovals(); ibAgents(); ibRecs();
}
function todayRow(p) {
  const ch = chOf(p.channelId), a = (p.assets || [])[0], vid = a && /video/.test((a.type || "") + (a.mimeType || ""));
  const th = a && (a.thumbnail || (!vid && a.source));
  return `<li class="ib-trow svc-${esc(ch.service)}" data-testid="inbox-today-item"><time datetime="${esc(p.dueAt)}">${esc(hhmm(p.dueAt))}<small>${esc(new Date(p.dueAt).toLocaleDateString([], { weekday: "short" }))}</small></time>
    ${window.chMark ? chMark(ch, true) : netBadge(ch.service, true)}<span class="ib-ttext"><b>${esc(ch.displayName || ch.name)}</b><span>${esc(p.text)}</span></span>
    ${th ? `<img class="ib-tth" src="${esc(th)}" alt="" loading="lazy">` : vid ? `<span class="ib-tth vid" aria-label="Video"></span>` : ""}</li>`;
}
async function ibAgents() {
  const box = $("#ibAgents");
  // group=run: a Make a post run's AI calls come back as one row ("HuhQueue agent made a post · 11 AI calls · $0.13"),
  // with each call kept for the expandable detail
  let r; try { r = await api("/api/activity?tab=agent&limit=100&group=run"); } catch (_) { box.innerHTML = `<li class="ib-none">Agent activity shows here once an app connects.</li>`; return; }
  box.innerHTML = r.items.length ? r.items.slice(0, 6).map((x) => (x.group === "run" ? ibRunRow(x) : ibAgentRow(x))).join("")
    : `<li class="ib-none">No agent has acted yet. <button class="link" type="button" data-go="apps" data-testid="inbox-empty-apps">Connect Claude, ChatGPT or Cursor</button>.</li>`;
  box.querySelectorAll("[data-open-run]").forEach((b) => (b.onclick = async () => {
    showTab("agent", "agent");
    try { const run = await api(`/api/agent/runs/${b.dataset.openRun}`); window.showRun && showRun(run); } catch (e) { toast(e.message); }
  }));
}
function ibAgentRow(x) {
  const who = x.app_label || x.token_label || "Agent";
  return `<li class="ib-arow${x.ok ? "" : " bad"}" data-testid="inbox-agent-row">${aiMark(x.app || who, 28)}<span><b>${esc(who)}</b> ${esc(actionWords(x.action))}${x.model ? `<small>${aiMark(x.model, 12)} ${esc(x.model)}</small>` : ""}</span><time>${esc(timeAgo(x.ts * 1000))}</time></li>`;
}
const usd = (v) => (v == null ? "" : v < 0.01 ? "<$0.01" : "$" + v.toFixed(2));
function ibRunRow(x) {
  const n = x.n_calls || (x.calls || []).length, cost = usd(x.cost_usd);
  const line = [`${n} AI call${n === 1 ? "" : "s"}`, cost].filter(Boolean).join(" · ");
  const calls = (x.calls || []).map((c) => { const d = c.detail || {}, tok = c.tokens_in != null ? `${(c.tokens_in || 0) + (c.tokens_out || 0)} tokens` : "";
    return `<li data-testid="inbox-agent-call" title="${esc([c.model, tok].filter(Boolean).join(" · "))}">${aiMark(c.model || "huhqueue", 12)}<span>${esc(d.for || d.task || c.model || "AI call")}</span>`
      + `<span class="muted">${esc(c.cost_usd != null ? usd(c.cost_usd) : (c.model || ""))}</span><time>${esc(timeAgo(c.ts * 1000))}</time></li>`; }).join("");
  return `<li class="ib-arow ib-arun${x.ok ? "" : " bad"}" data-testid="inbox-agent-row" data-run="${esc(x.run)}">${aiMark("huhqueue", 28)}
    <details class="ib-arun-d"><summary data-testid="inbox-agent-run"><span><b>${esc(x.app_label || "HuhQueue agent")}</b> ${esc(x.words || "made a post")} <span class="ib-arun-n">· ${esc(line)}</span>${x.model ? `<small>${aiMark(x.model, 12)} ${esc(x.model)}</small>` : ""}</span></summary>
      <ul class="ib-acalls" data-testid="inbox-agent-calls">${calls}</ul>
      ${x.can_open ? `<button class="link sm" type="button" data-open-run="${esc(x.run)}" data-testid="inbox-open-run">Open this run</button>` : ""}</details>
    <time>${esc(timeAgo(x.ts * 1000))}</time></li>`;
}
const ACTION_WORDS = { submit_post: "wrote a post", approve_post: "had a post approved", preview_post: "checked a post", draft: "drafted posts", list_channels: "read your channels",
  get_inbox: "read your Inbox", agent_run: "ran the agent", sources_scan: "scanned sources", sources_draft: "drafted from sources", render: "rendered media", get_calendar: "read your Planner", ai_call: "made an AI call", preflight: "checked a post", get_posts: "read your posts", create_idea: "saved an idea",
  approve: "approved a post", reject: "rejected a post", create_token: "got a token", schedule_package: "scheduled a package" };
const actionWords = (a) => ACTION_WORDS[a] || String(a || "").replace(/_/g, " ");
// Recommended for you: GET /api/recommendations (backend, docs/ONBOARDING.md). Until that ships, the newest stories from
// your feeds stand in, clearly labelled.
async function getRecs() {
  try {
    const r = await api("/api/recommendations");
    const ready = (r.recommended || []).map((x) => ({ id: x.cluster_id, title: x.headline, url: x.source_url, image: x.cover, branded: true,
      why: ((r.feed || []).find((c) => c.id === x.cluster_id) || {}).why || "Rendered in your brand, ready to review" }));
    const feed = (r.feed || []).filter((c) => !ready.some((x) => x.id === c.id)).map((c) => ({ id: c.id, title: c.story?.headline || c.items?.[0]?.title,
      why: c.why || c.story?.line || "", url: c.items?.[0]?.url || "", source: c.items?.[0]?.source }));
    const items = Array.isArray(r.items) ? r.items : [...ready, ...feed];
    if (items.length) return { items, live: true };
  } catch (_) {}
  try { const r = await api("/api/radar"); return { items: r.stories.slice(0, 4).map((s) => ({ id: s.url, title: s.title, why: whyLine(s), source: s.source, url: s.url, image: s.image, published: s.published })), live: false }; }
  catch (_) { return { items: [], live: false }; }
}
async function ibRecs() {
  const box = $("#ibRecs"), r = await getRecs();
  box.innerHTML = r.items.length ? r.items.slice(0, 4).map((x) => recCard(x)).join("") + (r.live ? "" : `<p class="muted xs">From your feeds. Personal picks arrive after setup.</p>`)
    : `<p class="ib-none">Follow a few feeds in <button class="link" type="button" data-go="news:sources" data-testid="inbox-empty-sources">News</button> and posts worth making show up here.</p>`;
  wireMake(box); favicons(box);
}
function recCard(x) {
  const host = hostOf(x.url);
  return `<article class="rec${x.branded ? " branded" : ""}" data-testid="rec-card">${x.image ? `<img class="rec-img" src="${esc(x.image)}" alt="${x.branded ? "Cover rendered in your brand" : ""}" loading="lazy">` : ""}
    <div class="rec-b"><span class="nf-src">${fav(host)}${esc(host || x.source)}</span><b class="rec-t">${esc(x.title)}</b>${x.why ? `<span class="rec-why">${esc(x.why)}</span>` : ""}
    <span class="row tight">${x.run_id ? `<button class="btn sm" type="button" data-run="${esc(x.run_id)}" data-testid="rec-review">Review</button>` : `<button class="btn sm" type="button" data-make="${esc(x.url)}" data-testid="rec-make">Make post</button>`}</span></div></article>`;
}
function ibBadge(n) { const b = $("#inboxBadge"); b.textContent = n; b.hidden = !n; }
$("#inboxRefresh").onclick = () => loadInbox();
// ---------------------------------------------------------------- plan limits: warn before Buffer says no (never in the sample)
async function loadPlan() {
  let p; try { p = await api("/api/plan"); } catch (_) { return; }
  const box = $("#planBox"); if (!box) return;
  const meter = (m) => { const lvl = m.pct >= 100 ? "over" : m.pct >= 80 ? "near" : "";
    return `<div class="pm" data-testid="plan-meter"><div class="row"><span>${esc(m.label)}</span><span class="${lvl}">${m.used.toLocaleString()} of ${m.limit.toLocaleString()}</span></div><div class="bar ${lvl}" aria-hidden="true"><i style="width:${Math.min(100, m.pct)}%"></i></div></div>`; };
  box.innerHTML = p.meters.length ? p.meters.map(meter).join("") + (p.locked_channels.length ? `<div class="st err">Locked in Buffer: ${esc(p.locked_channels.join(", "))}. Posts to these channels won't go out until you unlock them.</div>` : "")
    : `<div class="empty"><b>No plan limits to show</b><span>Buffer didn't share limits for this organization, so HuhQueue checks each channel's daily limit when you send.</span></div>`;
  const api_ = (p.buffer_api.windows || []).find((w) => w.quota && w.remaining / w.quota < 0.1);
  const worst = p.near_limit.sort((x, y) => y.pct - x.pct)[0];
  let msg = "";
  if (worst && worst.pct >= 100) msg = `You've used all ${worst.limit} ${worst.label.toLowerCase()} your Buffer plan allows. New posts won't be accepted until some go out.`;
  else if (p.locked_channels.length) msg = `${p.locked_channels.length} channel${p.locked_channels.length > 1 ? "s are" : " is"} locked in Buffer because of your plan.`;
  const bar = $("#limitBar"), key = "hq_limit_dismissed:" + msg;
  let dismissed = false; try { dismissed = sessionStorage.getItem(key) === "1"; } catch (_) {}
  if (S.session?.demo) bar.hidden = true;
  else if (msg && !dismissed) { $("#limitText").textContent = msg; $("#limitUpgrade").hidden = false; bar.hidden = false; }
  else if (api_ && !dismissed) { $("#limitText").textContent = `HuhQueue has ${api_.remaining} Buffer API requests left for the next ${api_.window}. Reading may pause briefly; sending still works.`; $("#limitUpgrade").hidden = true; bar.hidden = false; }
  else bar.hidden = true;
  $("#limitUpgrade").href = p.upgrade_url;
  $("#limitDismiss").onclick = () => { try { sessionStorage.setItem(key, "1"); } catch (_) {} bar.hidden = true; };
}

// ---------------------------------------------------------------- News: scannable story cards
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (_) { return ""; } };
// The site's own favicon (no third-party favicon service); a monogram until it loads, and if it never does.
const fav = (host) => `<span class="fav" data-host="${esc(host)}" aria-hidden="true"><i>${esc((host || "?")[0].toUpperCase())}</i></span>`;
function favicons(box) {
  box.querySelectorAll(".fav[data-host]:not([data-done])").forEach((f) => {
    f.dataset.done = "1"; const h = f.dataset.host; if (!h) return;
    const img = new Image(); img.alt = ""; img.referrerPolicy = "no-referrer"; img.width = 16; img.height = 16;
    img.onload = () => { if (img.naturalWidth > 1) { f.innerHTML = ""; f.append(img); f.classList.add("ok"); } };
    img.src = `https://${h}/favicon.ico`;
  });
}
// one plain line on why a story matters: the feed's own summary minus link-aggregator boilerplate ("Article URL: …")
function whyLine(st) {
  let s = String(st.summary || "").replace(/(Article URL|Comments URL|Points|# Comments):\s*\S*/gi, " ").replace(/https?:\/\/\S+/g, " ").replace(/\s+/g, " ").trim();
  if (s.length < 25) return "";
  const m = s.match(/^.{25,140}?[.!?](\s|$)/); s = m ? m[0].trim() : s.slice(0, 140).replace(/\s\S*$/, "") + "…";
  return s;
}
function storyItem(st) {
  const host = hostOf(st.url), ago = st.published ? timeAgo(st.published * 1000) : "", why = whyLine(st);
  return `<article class="nf-card" data-testid="story-item">
    ${st.image ? `<img class="nf-img" src="${esc(st.image)}" alt="" loading="lazy">` : ""}
    <div class="nf-b"><div class="nf-meta">${fav(host)}<span class="nf-src" title="via ${esc(st.source || "")}">${esc(host || st.source)}</span>${ago ? `<time>${esc(ago)}</time>` : ""}</div>
      <a class="nf-t" href="${esc(st.url)}" target="_blank" rel="noopener">${esc(st.title)}</a>
      ${why ? `<p class="nf-why">${esc(why)}</p>` : ""}
      <div class="nf-act"><button class="btn sm" type="button" data-make="${esc(st.url)}" data-testid="story-write">Make post</button></div></div></article>`;
}
function timeAgo(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 24) return `${h} h ago`;
  return new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });
}
// "Make post" opens Make a post with the story's link in the one prompt box (Prompt or link); the person picks the
// format and presses Make it.
function wireMake(box) {
  box.querySelectorAll("[data-make]").forEach((b) => (b.onclick = () => {
    showTab("agent", "agent");
    document.querySelector("#agentCompose [data-src=topic]")?.click();
    const q = $("#agQuery");
    if (q) { q.value = ("Post this on all my channels " + (b.dataset.make || "")).trim(); q.dispatchEvent(new Event("input")); q.focus(); }
    toast("Link added. Pick a format and press Make it.");
  }));
  box.querySelectorAll("[data-run]").forEach((b) => (b.onclick = async () => { showTab("agent", "agent"); try { const r = await api(`/api/agent/runs/${b.dataset.run}`); window.showRun && showRun(r); } catch (e) { toast(e.message); } }));
}
const NF = { stories: [], shown: 12 };
async function loadNews() { if ((document.getElementById("tab-news").dataset.sub || "feed") === "feed") loadFeed(); }
async function loadFeed() {
  const box = $("#stories");
  if (!NF.stories.length) box.innerHTML = `<div class="nf-skel" aria-busy="true"><span></span><span></span><span></span></div>`;
  try {
    const r = await api("/api/radar"); NF.stories = r.stories; NF.feeds = r.feeds;
    box.innerHTML = (r.errors || []).map((e) => `<div class="st err">${esc(hostOf(e.feed))}: ${esc(e.error)}</div>`).join("")
      + (r.stories.length ? r.stories.slice(0, NF.shown).map(storyItem).join("") : `<div class="empty"><b>No stories yet</b><span>Follow a site in <button class="link" type="button" data-go="news:sources" data-testid="news-empty-sources">Sources</button>. Most news sites and blogs have a feed at /feed or /rss.</span></div>`);
    $("#nfMore").hidden = r.stories.length <= NF.shown;
    wireMake(box); favicons(box);
  } catch (e) { box.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
}
$("#nfMore").onclick = () => { NF.shown += 12; loadFeed(); };
window.HS_SUBS = Object.assign(window.HS_SUBS || {}, { "news:feed": loadFeed, "news:sources": loadFeeds });
// One list of followed feeds: the For you feed (news radar) and the story scan (sources agent) read the same list.
async function loadFeeds() {
  const fl = $("#feeds"); window.loadSources?.();
  let r, sa; try { [r, sa] = await Promise.all([api("/api/radar"), api("/api/sources").catch(() => null)]); } catch (e) { fl.innerHTML = `<li class="st err">${esc(e.message)}</li>`; return; }
  const scan = (sa && sa.settings && sa.settings.rss) || [];
  const rows = r.feeds.map((f) => ({ id: f.id, url: f.url, title: f.title }));
  for (const u of scan) if (!rows.some((x) => x.url === u)) rows.push({ id: null, url: u, title: hostOf(u) });
  fl.innerHTML = rows.length ? rows.map((f) => `<li class="feed" data-testid="feed-row">${fav(hostOf(f.url))}<span><b>${esc(f.title || hostOf(f.url))}</b>${f.title && f.title !== hostOf(f.url) ? `<small>${esc(hostOf(f.url))}</small>` : ""}</span><button class="link danger" type="button" data-testid="feed-remove" data-id="${f.id ?? ""}" data-url="${esc(f.url)}" aria-label="Stop following ${esc(f.url)}">Remove</button></li>`).join("")
    : `<li class="muted sm">No feeds yet.</li>`;
  favicons(fl);
  fl.querySelectorAll("[data-testid=feed-remove]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { if (b.dataset.id) await api(`/api/radar/feeds/${b.dataset.id}`, { method: "DELETE" });
      if (scan.includes(b.dataset.url)) await api("/api/sources/settings", { method: "POST", body: { rss: scan.filter((u) => u !== b.dataset.url) } });
      NF.stories = []; loadFeeds(); } catch (e) { toast(e.message, 5000); b.disabled = false; }
  }));
}
$("#feedForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const b = $("[data-testid=feed-add]"), url = $("#feedUrl").value.trim(); b.disabled = true;
  try {
    const f = await api("/api/radar/feeds", { method: "POST", body: { url } });
    try { const sa = await api("/api/sources"); const rss = sa.settings.rss || []; if (!rss.includes(url) && rss.length < 8) await api("/api/sources/settings", { method: "POST", body: { rss: [...rss, url] } }); } catch (_) {}
    toast(`Following ${f.title}`); $("#feedUrl").value = ""; NF.stories = []; loadFeeds();
  } catch (e) { toast(e.message, 5000); }
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

// ---------------------------------------------------------------- approvals (cards on the Inbox)
async function refreshBadge() {
  try {
    const r = await api("/api/approvals");
    const n = $("#ibApprN"); if (n) { n.textContent = r.length; n.classList.toggle("hot", !!r.length); }
    return r;
  } catch (_) { return []; }
}
const isVidUrl = (u) => /\.(mp4|mov|m4v|webm)(\?|$)/i.test(u || "");
async function loadApprovals() {
  const box = $("#approvals"), rows = await refreshBadge();
  $("#ibApprSec").classList.toggle("is-empty", !rows.length);
  if (!rows.length) { box.innerHTML = `<div class="ib-none" data-testid="approvals-empty">Nothing waiting. When an agent writes a post, it shows up here before anything reaches Buffer.</div>`; return; }
  box.innerHTML = "";
  for (const r of rows) {
    const ch = chOf(r.payload.channel_id), media = (r.payload.media_urls && r.payload.media_urls.length ? r.payload.media_urls : [r.payload.media_url]).filter(Boolean);
    const el = document.createElement("article"); el.className = `appr svc-${ch.service}`; el.setAttribute("data-testid", "approval-item");
    const thumb = media.length ? `<div class="appr-media${media.length > 1 ? " multi" : ""}">${isVidUrl(media[0]) ? `<video src="${esc(media[0])}" muted playsinline preload="metadata"></video><span class="play" aria-hidden="true"></span>` : `<img src="${esc(media[0])}" alt="" loading="lazy">`}${media.length > 1 ? `<span class="more">+${media.length - 1}</span>` : ""}</div>` : "";
    el.innerHTML = `<div class="appr-head">${aiMark(r.agent_name, 22)}<span><b>${esc(r.agent_name)}</b> for ${netBadge(ch.service)} <b>${esc(ch.displayName || ch.name)}</b></span><small>${esc(timeAgo(r.created * 1000))}</small></div>
      <div class="appr-body">${thumb}<textarea data-testid="approval-text" aria-label="Post text" rows="4">${esc(r.payload.text)}</textarea></div>
      <div class="appr-act"><button class="btn2 sm danger" data-testid="reject">Reject</button>
      <span class="right"><select data-testid="approval-mode" aria-label="How to send">${modeOptions(r.payload.mode === "schedule" ? "queue" : r.payload.mode)}</select>
      <button class="btn accent sm" data-testid="approve">Approve</button></span></div><div data-testid="approval-status"></div>`;
    $("[data-testid=approve]", el).onclick = async () => {
      const b = $("[data-testid=approve]", el); b.disabled = true;
      try { const res = await api(`/api/approvals/${r.id}/approve`, { method: "POST", body: { text: $("textarea", el).value, mode: $("select", el).value } });
        $("[data-testid=approval-status]", el).innerHTML = `<div class="st ok">Sent to Buffer (${esc(res.mode)})</div>`; el.classList.add("sent"); setTimeout(loadInbox, 1400); }
      catch (e) { $("[data-testid=approval-status]", el).innerHTML = `<div class="st err">${esc(e.message)}</div>`; b.disabled = false; }
    };
    $("[data-testid=reject]", el).onclick = async () => { await api(`/api/approvals/${r.id}/reject`, { method: "POST" }); toast("Rejected. Nothing was sent."); loadInbox(); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- AI apps: who has access, what they did, connect more
const POL = { review: "Needs my approval", draft: "Buffer drafts only", trusted: "Can schedule" };
async function loadTokens() {
  const r = await api("/api/safety"); setKill(r.kill_switch);
  const box = $("#tokens"), toks = r.tokens.filter((t) => !t.revoked);
  box.innerHTML = toks.length ? `<ul class="ax-list">${toks.map((t) => `<li class="ax-row" data-testid="token-row">${aiMark(t.name, 32)}<span class="ax-name"><b>${esc(t.name)}</b><small>${esc(POL[t.policy] || t.policy)} · ${t.daily_cap} posts a day</small></span>
      <span class="ax-used ${t.last_used ? "on" : ""}">${t.last_used ? "Last used " + esc(timeAgo(t.last_used * 1000)) : "Not used yet"}</span>
      <button class="link danger" type="button" data-testid="revoke-token" data-id="${t.id}">Revoke</button></li>`).join("")}</ul>`
    : `<p class="muted ib-none">No app has a token yet. Claude and ChatGPT sign in with OAuth and appear in the activity below once they act.</p>`;
  box.querySelectorAll("[data-testid=revoke-token]").forEach((b) => (b.onclick = async () => { await api(`/api/agent-tokens/${b.dataset.id}`, { method: "DELETE" }); loadTokens(); }));
}
async function loadAppUsage() {
  const box = $("#axUsage");
  try {
    const r = await api("/api/activity?tab=agent&limit=25");
    box.innerHTML = r.agents && r.agents.length ? `<h2 class="sec">What each app did</h2><div class="ax-usage">${r.agents.map((g) => `<button type="button" class="ax-u" data-go="activity" data-testid="apps-usage-card">
      <span class="ax-uh">${aiMark(g.app || g.label, 24)}<b>${esc(g.label)}</b></span><span class="ax-un"><b>${g.actions.toLocaleString()}</b> action${g.actions === 1 ? "" : "s"}${g.failed ? ` · <em>${g.failed} failed</em>` : ""}</span>
      <span class="ax-um">${g.models.length ? g.models.slice(0, 3).map((m) => `${aiMark(m.model || m.provider, 12)} ${esc(m.model || "unknown model")} ×${m.calls}`).join("<br>") : "model not reported by the app"}</span></button>`).join("")}</div>` : "";
  } catch (_) { box.innerHTML = ""; }
}
function loadApps() { loadTokens(); loadAppUsage(); (window.HS_TABS || {}).connect?.(); }
$("#tokenForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const t = await api("/api/agent-tokens", { method: "POST", body: { name: $("#tokName").value, policy: $("#tokPolicy").value, daily_cap: +$("#tokCap").value } });
    const rv = $("#tokenReveal"); rv.hidden = false;
    rv.innerHTML = `<b>Copy this token now. It is shown once.</b>${t.sample ? `<span class="sample-note" data-testid="token-sample">Sample token</span><span class="muted sm">${esc(t.note)}</span>` : ""}<code data-testid="token-value">${esc(t.token)}</code><span><button class="btn2 sm" data-testid="copy-token">Copy token</button></span>`;
    $("[data-testid=copy-token]", rv).onclick = () => navigator.clipboard.writeText(t.token).then(() => toast("Copied"));
    loadTokens();
  } catch (e) { toast(e.message, 5000); }
});
async function toggleKill(on) { const r = await api("/api/safety/kill", { method: "POST", body: { on } }); setKill(r.kill_switch); toast(on ? "Kill switch on. No agent can draft or post until you turn it off." : "Agents can act again"); }
$("#killToggle").onchange = (e) => toggleKill(e.target.checked);
$("#killPill").onclick = () => toggleKill(!$("#killToggle").checked);

// ---------------------------------------------------------------- Settings
function loadSettings() {
  const s = S.session || {};
  $("#setWho").textContent = `Signed in with Buffer as ${s.user?.name || s.user?.email || "you"}${s.user?.organization ? ", organization " + s.user.organization : ""}.`;
  $("#setChannels").innerHTML = S.channels.length ? S.channels.map((c) => `<li data-testid="settings-channel">${avatar(c)}<span><b>${esc(c.displayName || c.name)}</b><small>${esc(netName(c.service))}${c.isDisconnected ? " · reconnect in Buffer" : ""}</small></span></li>`).join("")
    : `<li class="muted sm">No channels in this Buffer organization yet.</li>`;
  if (window.onbState) onbState().then((st) => ($("#setOnbState").textContent = st.done ? "Setup is done. Open it again to change what you post, your channels or your brand." : st.skipped ? "You skipped setup. It takes about a minute." : "Setup isn't finished yet."));
  (window.HS_TABS || {}).ai?.();
}
$("#setOnbOpen").onclick = () => window.onbOpen && onbOpen();
$("#refreshCh").onclick = () => loadChannels(true).then(() => { loadSettings(); toast("Channels refreshed from Buffer"); });

// ---------------------------------------------------------------- audit: see activity.js (HS_TABS.activity)

// ---------------------------------------------------------------- account
$("#acctBtn").onclick = () => { const m = $("#acctMenu"); m.hidden = !m.hidden; $("#acctBtn").setAttribute("aria-expanded", !m.hidden); };
document.addEventListener("click", (e) => { if (!e.target.closest(".acct")) { $("#acctMenu").hidden = true; $("#acctBtn").setAttribute("aria-expanded", "false"); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#acctMenu").hidden) { $("#acctMenu").hidden = true; $("#acctBtn").focus(); } });
async function deviceLink() {
  $("#acctMenu").hidden = true;
  try { const r = await api("/api/device-link", { method: "POST" }); await navigator.clipboard.writeText(r.url).catch(() => {}); toast("One-time link, valid 10 min (copied): " + r.url, 20000); }
  catch (e) { toast(e.message, 5000); }
}
async function logout() { await api("/api/logout", { method: "POST" }); location.href = "/"; }
async function disconnect() { if (!confirm("Disconnect Buffer and delete everything HuhQueue stored for you?")) return; await api("/api/disconnect", { method: "POST" }); location.href = "/"; }
$("#deviceBtn").onclick = deviceLink; $("#setDevice").onclick = deviceLink;
$("#logoutBtn").onclick = logout; $("#setLogout").onclick = logout;
$("#disconnectBtn").onclick = disconnect; $("#setDisconnect").onclick = disconnect;
document.querySelectorAll("#acctMenu [data-go]").forEach((b) => b.addEventListener("click", () => ($("#acctMenu").hidden = true)));

boot().catch((e) => toast(e.message, 6000));
