// Make a post as a chat (owner, 3 Nov 2026): "a proper chat interface like ChatGPT / open-webui". Discover stays the start
// screen; a thread is a conversation with a thread sidebar (past runs, search, rename), a centered message column, the
// steps folded into one "Working… N steps" row, message actions (copy, retry, edit and resend), the post cards inline
// with a side panel for full-size preview, a "Ready to send" bar, a sticky composer with Upload video and the model, and
// a context drawer (what the agent knows: source, kit, template, channels, rights, rules, the compacted conversation).
// UX patterns studied from open-webui (https://github.com/open-webui/open-webui): no code copied; see NOTICE.
// house.js still owns the run (submit, polling, packages) and makepost.js the start screen; this file only arranges and
// decorates. Every piece of outside text goes through esc() / textContent, never raw innerHTML.
"use strict";
(() => {
  const root = document.getElementById("tab-agent"), rv = document.getElementById("runView");
  if (!root || !rv) return;
  const KEEP = 6;  // turns the server keeps word for word (studio/chat_context.py KEEP_LAST)
  const HC = { rows: [], q: "", trace: {}, showOld: {}, ctxFor: 0, ctxKey: "", panelFor: "", lastFocus: null, shown: 25, dirty: true };
  const PAGE = 25;  // the sidebar draws 25 threads, then 25 more as you scroll (the rows come from the Recent fetch, no extra call)
  const ic = (d) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const I = {
    plus: '<path d="M12 5v14M5 12h14"/>', menu: '<path d="M4 7h16M4 12h16M4 17h16"/>', search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>', copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
    retry: '<path d="M4 12a8 8 0 1 0 2.3-5.6L4 8.7"/><path d="M4 4v4.7h4.7"/>', edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>', chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    fold: '<path d="M4 6h16M4 12h10M4 18h7"/>',
  };
  const safeUrl = (u) => (typeof u === "string" && (/^https?:\/\//i.test(u) || /^\/(?!\/)/.test(u)) ? u : "");
  const busyRun = (r) => !!r && ["running", "rendering"].includes(r.status);
  const run = () => window.H_STATE && window.H_STATE.run;
  const el = (tag, cls, attrs) => { const n = document.createElement(tag); if (cls) n.className = cls; for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; };
  const sel = (v) => (window.CSS && CSS.escape ? CSS.escape(String(v)) : String(v).replace(/["\\]/g, "\\$&"));
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch (_) { const t = el("textarea", "sr"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch (__) { /* stays selectable */ } t.remove(); }
    if (window.toast) toast("Copied");
  }
  const top = () => document.documentElement.style.setProperty("--hc-top", ((document.querySelector("header.top") || {}).offsetHeight || 64) + "px");
  top(); window.addEventListener("resize", top);

  // ------------------------------------------------------------ the shell: sidebar | main column | preview panel
  rv.classList.add("hc");
  const side = el("aside", "hc-side", { id: "hcSide", "data-testid": "chat-sidebar", "aria-label": "Post threads" });
  side.innerHTML = `<div class="hc-side-head"><b class="hc-side-t">Threads</b>
      <button type="button" class="btn2 sm hc-newb" id="hcNew" data-testid="chat-new">${ic(I.plus)}<span>New post</span></button>
      <button type="button" class="btn2 sm icon hc-side-x" id="hcSideX" data-testid="chat-sidebar-close" aria-label="Close threads">${ic(I.x)}</button></div>
    <div class="hc-search"><label class="sr" for="hcQ">Search threads</label>${ic(I.search)}<input id="hcQ" type="search" data-testid="chat-search" placeholder="Search threads" autocomplete="off" maxlength="80"></div>
    <nav class="hc-list" id="hcList" data-testid="chat-thread-list" aria-label="Threads"></nav>`;
  const main = el("div", "hc-main", { id: "hcMain" });
  const panel = el("aside", "hc-panel", { id: "hcPanel", "data-testid": "chat-preview-panel", "aria-labelledby": "hcPanelT", tabindex: "-1" });
  panel.hidden = true;
  panel.innerHTML = `<div class="hc-panel-head"><b id="hcPanelT">Preview</b><button type="button" class="btn2 sm icon" id="hcPanelX" data-testid="chat-preview-close" aria-label="Close the preview">${ic(I.x)}</button></div><div class="hc-panel-body" id="hcPanelBody"></div>`;
  const drawer = el("div", "hc-ctx", { id: "hcCtx", role: "dialog", "aria-modal": "false", "aria-labelledby": "hcCtxT", "data-testid": "chat-context-drawer", tabindex: "-1" });
  drawer.hidden = true;
  drawer.innerHTML = `<div class="hc-panel-head"><b id="hcCtxT">What the agent knows</b><button type="button" class="btn2 sm icon" id="hcCtxX" data-testid="chat-context-close" aria-label="Close">${ic(I.x)}</button></div><div class="hc-ctx-body" id="hcCtxBody" aria-live="polite"></div>`;
  const scrim = el("div", "hc-scrim", { id: "hcScrim", "data-testid": "chat-scrim" }); scrim.hidden = true;
  // move house.js's thread nodes into the centered column (their ids and test ids stay)
  [...rv.children].forEach((n) => main.appendChild(n));
  // chat order: the request, the agent's answer, then what's ready (house.js drew the ready summary first)
  const readyBox = document.getElementById("mpReady"), agentMsg = main.querySelector(".mp-msg.agent");
  if (readyBox && agentMsg) agentMsg.after(readyBox);
  rv.append(side, main, panel);
  document.body.append(drawer, scrim);

  // the bar: threads (phone), title, model, context, new post
  const bar = main.querySelector(".mp-thread-bar");
  const mpNew = document.getElementById("mpNew");
  mpNew.innerHTML = `${ic(I.plus)}<span>New post</span>`; mpNew.setAttribute("aria-label", "New post");
  const menuB = el("button", "btn2 sm icon hc-menu", { type: "button", id: "hcMenu", "data-testid": "chat-sidebar-open", "aria-label": "Threads", "aria-controls": "hcSide", "aria-expanded": "false" });
  menuB.innerHTML = ic(I.menu);
  const head = el("div", "hc-head");
  head.innerHTML = `<h2 class="hc-title" id="hcTitle" data-testid="chat-title">Post</h2>`;
  head.appendChild(document.getElementById("mpStatus"));
  const model = el("button", "hc-chip hc-model", { type: "button", id: "hcModel", "data-testid": "chat-model", "data-go": "settings:ai", title: "The model that runs this agent. Change it in Settings > AI engine." });
  const ctxB = el("button", "hc-chip hc-ctxb", { type: "button", id: "hcCtxB", "data-testid": "chat-context", "aria-controls": "hcCtx", "aria-expanded": "false" });
  ctxB.innerHTML = `${ic(I.info)}<span>Context</span>`;
  bar.prepend(menuB, head);
  bar.append(model, ctxB);
  bar.appendChild(mpNew);
  // the composer gets a footer with the model; the actions row becomes the Ready to send bar
  const reply = document.getElementById("replyForm"), acts = document.getElementById("mpActs");
  const cfoot = el("div", "hc-cfoot");
  cfoot.innerHTML = `<span class="muted xs">Enter to send · Shift+Enter for a new line</span><button type="button" class="link xs hc-cmodel" id="hcCModel" data-testid="chat-composer-model" data-go="settings:ai"></button>`;
  reply.appendChild(cfoot);
  acts.classList.add("hc-ready"); acts.setAttribute("role", "region"); acts.setAttribute("aria-label", "Ready to send");
  const readyTxt = el("span", "hc-ready-t", { id: "hcReadyT", "data-testid": "chat-ready-text" });
  acts.prepend(readyTxt);
  reply.parentNode.insertBefore(acts, reply);  // right above the composer, sticky with it
  // a phone gets the short placeholder (the long one wrapped onto a second, cut-off line)
  const ri0 = document.getElementById("replyIn"), longPh = ri0.placeholder, mq = matchMedia("(max-width:600px)");
  const ph = () => { ri0.placeholder = mq.matches ? "Ask for a change…" : longPh; };
  ph(); if (mq.addEventListener) mq.addEventListener("change", ph);
  if (window.ResizeObserver) new ResizeObserver(() => rv.style.setProperty("--hc-composer", reply.offsetHeight + "px")).observe(reply);

  // ------------------------------------------------------------ the thread sidebar (past runs = conversations)
  const day = 86400000;
  function group(ts) {
    const d = new Date(ts * 1000), now = new Date(), start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= start) return "Today";
    if (d.getTime() >= start - day) return "Yesterday";
    if (d.getTime() >= start - 7 * day) return "Previous 7 days";
    return "Older";
  }
  const STATE = { ready: "Ready", draft: "In Buffer", scheduled: "Scheduled", running: "Working", failed: "Stopped", interrupted: "Interrupted" };
  // lazy: nothing is drawn until the sidebar can be seen (a thread is open, or the phone drawer), then a page at a time
  const sideVisible = () => root.classList.contains("mp-in-thread") && (rv.classList.contains("hc-side-open") || !matchMedia("(max-width:1100px)").matches);
  function drawList(force) {
    if (!force && !sideVisible()) { HC.dirty = true; return; }
    HC.dirty = false;
    const box = document.getElementById("hcList"), q = HC.q.trim().toLowerCase(), cur = run() && run().id;
    const all = HC.rows.filter((x) => !q || String(x.title || "").toLowerCase().includes(q));
    const rows = all.slice(0, HC.shown);
    if (!rows.length) { box.innerHTML = `<p class="muted sm hc-empty">${HC.rows.length ? "No thread matches." : "Your posts show up here as threads."}</p>`; return; }
    let html = "", g = "";
    for (const x of rows) {
      const gg = group(x.created || 0);
      if (gg !== g) { if (g) html += "</ul>"; g = gg; html += `<h3 class="hc-grp">${esc(g)}</h3><ul class="hc-ul">`; }
      const on = String(x.id) === String(cur);
      html += `<li class="hc-li${on ? " on" : ""}" data-id="${esc(x.id)}"><button type="button" class="hc-th" data-open="${esc(x.id)}" data-kind="${esc(x.kind)}" data-testid="chat-thread"${on ? ' aria-current="true"' : ""}>
          <span class="hc-th-t">${esc(x.title || "Your post")}</span><small class="hc-th-s st-${esc(x.state || x.status)}">${esc(STATE[x.state] || x.state || x.status || "")}${x.summarized ? " · summarized" : ""}</small></button>
        ${x.kind === "take" ? "" : `<button type="button" class="hc-th-r" data-rename="${esc(x.id)}" data-testid="chat-thread-rename" aria-label="Rename “${esc(String(x.title || "").slice(0, 60))}”">${ic(I.pen)}</button>`}</li>`;
    }
    box.innerHTML = html + "</ul>" + (all.length > rows.length
      ? `<button type="button" class="link sm hc-more" data-testid="chat-thread-more">Show ${Math.min(PAGE, all.length - rows.length)} more</button>` : "");
    const more = box.querySelector(".hc-more");
    if (more) {
      more.onclick = () => { HC.shown += PAGE; drawList(true); };
      if (window.IntersectionObserver) { const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); HC.shown += PAGE; drawList(true); } }, { root: box }); io.observe(more); }
    }
  }
  document.getElementById("hcQ").addEventListener("input", (e) => { HC.q = e.target.value; HC.shown = PAGE; drawList(true); });
  document.getElementById("hcList").addEventListener("click", async (e) => {
    const r = e.target.closest("[data-rename]"); if (r) return rename(r.dataset.rename);
    const o = e.target.closest("[data-open]"); if (!o) return;
    sideDrawer(false);
    if (o.dataset.kind === "take") return window.openTake && window.openTake(o.dataset.open);
    try { const x = await api(`/api/agent/runs/${o.dataset.open}`); window.showRun(x); if (busyRun(x)) window.watchRun(x.id); window.scrollTo({ top: 0 }); }
    catch (err) { toast(err.message, 5000); }
  });
  function rename(id) {
    const li = document.querySelector(`#hcList .hc-li[data-id="${sel(id)}"]`); if (!li) return;
    const row = HC.rows.find((x) => String(x.id) === String(id)) || {};
    const f = el("form", "hc-ren", { "data-testid": "chat-rename-form" });
    f.innerHTML = `<label class="sr" for="hcRen">Thread name</label><input id="hcRen" data-testid="chat-rename-input" maxlength="80" autocomplete="off">
      <button type="submit" class="btn sm" data-testid="chat-rename-save">Save</button>`;
    const inp = f.querySelector("input"); inp.value = row.title || "";
    li.replaceChildren(f); inp.focus(); inp.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save && inp.value.trim() !== (row.title || "")) {
        try { const out = await api(`/api/agent/runs/${id}/title`, { method: "POST", body: { title: inp.value.trim() } });
          row.title = out.title || row.auto_title || row.title; toast(out.custom ? "Renamed" : "Name reset"); }
        catch (err) { toast(err.message, 5000); }
      }
      drawList(); drawTitle();
      if (window.loadRuns) window.loadRuns();
      const b = document.querySelector(`#hcList [data-rename="${sel(id)}"]`); if (b) b.focus();
    };
    f.onsubmit = (e) => { e.preventDefault(); finish(true); };
    inp.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); } });
    inp.addEventListener("blur", () => setTimeout(() => finish(true), 150));
  }
  // house.js loadRuns -> makepost.js mpRecent(rows) -> here too
  const recent = window.mpRecent;
  window.mpRecent = (rows) => { if (recent) recent(rows); HC.rows = rows || []; drawList(); drawTitle(); };
  function sideDrawer(open) {
    rv.classList.toggle("hc-side-open", open); menuB.setAttribute("aria-expanded", String(open)); scrimSync();
    if (open && HC.dirty) drawList(true);
    if (open) document.getElementById("hcQ").focus(); else if (side.contains(document.activeElement)) menuB.focus();
  }
  menuB.onclick = () => sideDrawer(!rv.classList.contains("hc-side-open"));
  document.getElementById("hcSideX").onclick = () => sideDrawer(false);
  document.getElementById("hcNew").onclick = () => { sideDrawer(false); mpNew.click(); };

  // ------------------------------------------------------------ the bar: title, model
  function drawTitle() {
    const r = run(); if (!r) return;
    const row = HC.rows.find((x) => String(x.id) === String(r.id));
    const st = (r.result || {}).story || {};
    document.getElementById("hcTitle").textContent = (row && row.title) || st.headline || "Your post";
    document.querySelectorAll("#hcList .hc-li").forEach((li) => { const on = li.dataset.id === String(r.id); li.classList.toggle("on", on);
      const b = li.querySelector(".hc-th"); if (b) { if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current"); } });
  }
  // the run's own brain (what wrote it); a run an AI app made over MCP says so ("Claude · via MCP"), never our engine
  function runBrain() { const r = run(), br = r && (r.result || {}).brain; return br && typeof br === "object" && (br.model || br.provider || br.source) ? br : null; }
  function modelName() {
    if (typeof brainName !== "function") return "the built-in writer";
    return brainName(runBrain() || (window.H_STATE || {}).brain);
  }
  function drawModel() {
    const n = modelName(), br = runBrain(), mcp = !!(br && br.mode === "mcp");
    const nxt = !mcp && br && window.H_STATE && H_STATE.brain && typeof brainName === "function" && brainName(H_STATE.brain) !== n ? brainName(H_STATE.brain) : "";
    model.innerHTML = `${ic(I.chip)}<span>${esc(n)}</span>`;
    model.title = mcp ? br.hint : "The model that runs this agent. Change it in Settings > AI engine.";
    model.setAttribute("aria-label", mcp ? `Made in ${n}. ${br.hint}` : `Model: ${n}. Open Settings, AI engine`);
    const f = document.getElementById("hcCModel");
    f.textContent = mcp ? `${n} — ${br.hint}` : "Model: " + n + (nxt ? ` · next reply: ${nxt}` : "");
  }
  window.hcDrawModel = () => { if (run()) drawModel(); };

  // ------------------------------------------------------------ after every render of the run: decorate
  function actions(kind, text, extra) {
    const box = el("div", "hc-acts", { role: "group", "aria-label": "Message actions", "data-testid": "chat-msg-actions" });
    const add = (label, icon, tid, fn) => { const b = el("button", "hc-act", { type: "button", "data-testid": tid, "aria-label": label, title: label });
      b.innerHTML = ic(icon); b.onclick = fn; box.appendChild(b); };
    add("Copy", I.copy, "chat-copy", () => copy(text));
    if (extra.edit) add(kind === "first" ? "Edit and resend as a new post" : "Edit and resend", I.edit, "chat-edit", extra.edit);
    if (extra.retry) add("Retry", I.retry, "chat-retry", extra.retry);
    return box;
  }
  const say = (t) => { const ri = document.getElementById("replyIn"); ri.value = t; ri.dispatchEvent(new Event("input")); document.getElementById("replyForm").requestSubmit(); };
  function editInComposer(t) { const ri = document.getElementById("replyIn"); ri.value = t; ri.dispatchEvent(new Event("input")); ri.focus(); ri.setSelectionRange(t.length, t.length); }
  const msgText = (m) => (m.querySelector(":scope > span") || m).textContent.trim();
  function decorate() {
    const r = run(); if (!r) return;
    const res = r.result || {}, busy = busyRun(r), thread = res.thread || [];
    // the first message (the request): copy, and edit-and-resend as a new post
    const u = document.getElementById("mpUser"), bub = u.querySelector(".mp-bubble");
    u.querySelectorAll(":scope > .hc-acts").forEach((x) => x.remove());
    if (bub) {
      const said = (bub.querySelector("p") || bub).textContent;
      u.appendChild(actions("first", said, { edit: () => { mpNew.click(); const q = document.getElementById("agQuery"); q.value = said; q.dispatchEvent(new Event("input")); q.focus(); } }));
    }
    // the agent's first answer: copy (plan + status), retry (regenerate)
    const am = rv.querySelector(".mp-msg.agent");
    am.querySelectorAll(":scope > .hc-acts").forEach((x) => x.remove());
    const plan = [...document.querySelectorAll("#agentPlan li")].map((li, i) => `${i + 1}. ${li.textContent}`).join("\n");
    am.appendChild(actions("agent", [document.getElementById("agentBrain").textContent, plan, document.getElementById("mpStatus").textContent].filter(Boolean).join("\n"),
      { retry: busy || ["media", "news"].includes(r.kind) ? null : () => document.getElementById("mpRegen").click() }));
    // the conversation: every later message gets its actions; older turns are marked as summarized for the agent
    const msgs = [...document.querySelectorAll("#agentThread .msg[data-testid=thread-msg]")];
    msgs.forEach((m, i) => {
      if (m.querySelector(":scope > .hc-acts")) return;
      const txt = msgText(m), user = m.classList.contains("user");
      let prevUser = "";
      for (let j = i - 1; j >= 0 && !prevUser; j--) if (msgs[j].classList.contains("user")) prevUser = msgText(msgs[j]);
      m.appendChild(actions(user ? "user" : "agent", txt, user ? { edit: () => editInComposer(txt) } : { retry: busy || !prevUser ? null : () => say(prevUser) }));
    });
    const summ = res.thread_summary || {}, folded = summ.summarized || (thread.length > 10 ? thread.length - KEEP : 0);
    const at = document.getElementById("agentThread");
    at.querySelectorAll(":scope > .hc-fold").forEach((x) => x.remove());
    msgs.forEach((m) => m.classList.remove("hc-old"));
    if (folded > 0 && msgs.length > KEEP) {
      msgs.slice(0, msgs.length - KEEP).forEach((m) => m.classList.add("hc-old"));
      const hide = HC.showOld[r.id] === false;
      at.classList.toggle("hc-hide-old", hide);
      const f = el("div", "hc-fold", { "data-testid": "chat-summarized", role: "note" });
      f.innerHTML = `${ic(I.fold)}<span><b>Earlier messages summarized</b><small>The agent reads ${esc(folded)} earlier message${folded === 1 ? "" : "s"} as a short summary and the last ${KEEP} word for word. The source, rights, kit and template are always kept.</small></span>
        <button type="button" class="link sm" data-testid="chat-summarized-toggle" aria-expanded="${hide ? "false" : "true"}">${hide ? "Show them" : "Hide them"}</button>`;
      f.querySelector("button").onclick = () => { HC.showOld[r.id] = hide; decorate(); };
      at.insertBefore(f, msgs[msgs.length - KEEP]);
      at.hidden = false;
    } else at.classList.remove("hc-hide-old");
    // the steps: one "Working… N steps" row, folded unless you opened it (a failure opens it)
    const tr = document.getElementById("mpTrace"), steps = r.steps || [], cur = steps.find((s) => s.status === "running");
    const sum = document.getElementById("mpTraceSum");
    const t = busy ? `Working… ${steps.length} step${steps.length === 1 ? "" : "s"}${cur ? " · " + cur.label : ""}` :
      `${r.status === "failed" ? "Stopped after" : "Worked through"} ${steps.length} step${steps.length === 1 ? "" : "s"}`;
    if (sum.textContent !== t) sum.textContent = t;
    tr.hidden = !busy && !steps.length;  // never "Worked through 0 steps": no steps, no row
    tr.classList.toggle("hc-working", busy);
    const want = HC.trace[r.id] ?? ["failed", "interrupted"].includes(r.status);
    if (tr.open !== want) { HC.syncing = true; tr.open = want; setTimeout(() => { HC.syncing = false; }, 0); }
    previews();
    // Ready to send
    const pk = res.packages || [], ready = pk.filter((p) => p.status === "ready").length;
    readyTxt.textContent = ready ? `${ready} post${ready === 1 ? "" : "s"} ready to send` : "";
    ctxB.querySelector("span").textContent = summ.summarized ? "Context · summarized" : "Context";
    if (HC.dirty) drawList();
    drawTitle(); drawModel();
    if (HC.panelFor && !document.querySelector(`#packages .pkg[data-ch="${sel(HC.panelFor)}"]`)) closePanel();
    else if (HC.panelFor) openPanel(HC.panelFor, false);
    // the open drawer refreshes only when the thread changed (a new turn, a new status), never on every poll tick
    const key = `${r.id}:${thread.length}:${r.status}:${(res.packages || []).length}`;
    if (!drawer.hidden && HC.ctxFor === r.id && key !== HC.ctxKey) { HC.ctxKey = key; loadCtx(false); }
  }
  document.getElementById("mpTrace").addEventListener("toggle", (e) => { const r = run(); if (r && !HC.syncing) HC.trace[r.id] = e.target.open; });
  // the post cards: a Preview button opens the side panel
  function previews() {
    document.querySelectorAll("#packages .pkg").forEach((card) => {
      const hd = card.querySelector(":scope > header"); if (!hd || hd.querySelector("[data-testid=pkg-preview]")) return;
      const b = el("button", "btn2 sm hc-prev", { type: "button", "data-testid": "pkg-preview", "aria-controls": "hcPanel" });
      b.setAttribute("aria-label", `Preview the ${(card.querySelector("header b") || {}).textContent || ""} post`);
      b.innerHTML = `${ic(I.expand)}<span>Preview</span>`;
      b.onclick = () => { HC.lastFocus = b; openPanel(card.dataset.ch, true); };
      hd.appendChild(b);
    });
  }
  new MutationObserver(() => previews()).observe(document.getElementById("packages"), { childList: true });
  const onRun = window.mpOnRun;
  window.mpOnRun = (r) => { if (onRun) onRun(r); rv.classList.toggle("hc-busy", busyRun(r)); setTimeout(decorate, 0); };

  // ------------------------------------------------------------ side panel: one post full size
  function openPanel(ch, focus) {
    const r = run(); if (!r) return;
    const p = ((r.result || {}).packages || []).find((x) => x.channel_id === ch); if (!p) { closePanel(); return; }
    HC.panelFor = ch;
    const card = document.querySelector(`#packages .pkg[data-ch="${sel(ch)}"]`);
    const media = (p.media && p.media.length ? p.media : (p.media_urls || []).map((u) => ({ url: u, type: /\.(mp4|mov|m4v|webm)(\?|$)/i.test(u) ? "video" : "photo" })));
    document.getElementById("hcPanelT").textContent = `${p.channel_name || ""} · ${typeof netName === "function" ? netName(p.service) : p.service}`;
    const wrap = el("div", "hc-pv");
    const fmt = el("p", "muted sm hc-pv-fmt"); fmt.textContent = window.HQ_MEDIA_LABEL ? window.HQ_MEDIA_LABEL(p) : (p.format || ""); wrap.appendChild(fmt);
    if (!media.length) { const n = el("p", "muted sm"); n.textContent = "Text only: no image or video on this post."; wrap.appendChild(n); }
    media.forEach((m, i) => {
      const u = safeUrl(m.url); if (!u) return;
      if (m.type === "video") { const v = el("video", "hc-pv-m", { controls: "", playsinline: "", preload: "metadata", "data-testid": "chat-preview-video" }); v.src = u; wrap.appendChild(v); }
      else { const im = el("img", "hc-pv-m", { "data-testid": "chat-preview-img", loading: "lazy" }); im.alt = `${p.channel_name || ""} ${p.format || "image"}, ${i + 1} of ${media.length}`; im.src = u; wrap.appendChild(im); }
    });
    const cap = el("div", "hc-pv-cap", { "data-testid": "chat-preview-caption" });
    const ta = card && card.querySelector("textarea");
    cap.textContent = ta ? ta.value : (p.text || "");
    wrap.appendChild(cap);
    if (media.length && window.openMedia) {
      const fs = el("button", "btn2 sm", { type: "button", "data-testid": "chat-preview-full" }); fs.textContent = "Open full screen";
      fs.onclick = () => window.openMedia(media, 0, `${p.channel_name || ""}`); wrap.appendChild(fs);
    }
    document.getElementById("hcPanelBody").replaceChildren(wrap);
    panel.hidden = false; rv.classList.add("hc-panel-open"); scrimSync();
    if (focus) document.getElementById("hcPanelX").focus();
  }
  function closePanel() {
    if (panel.hidden) return;
    panel.hidden = true; HC.panelFor = ""; rv.classList.remove("hc-panel-open"); scrimSync();
    if (HC.lastFocus && document.contains(HC.lastFocus)) HC.lastFocus.focus();
  }
  document.getElementById("hcPanelX").onclick = closePanel;

  // ------------------------------------------------------------ context drawer: what the agent knows for this thread
  async function loadCtx(focus) {
    const r = run(); if (!r) return;
    HC.ctxFor = r.id;
    const body = document.getElementById("hcCtxBody");
    if (focus) body.innerHTML = `<p class="muted sm" aria-busy="true"><span class="spin" aria-hidden="true"></span>Reading this thread…</p>`;
    let c;
    try { c = await api(`/api/agent/runs/${r.id}/context`); } catch (e) { body.innerHTML = `<p class="st err">${esc(e.message)}</p>`; return; }
    if (HC.ctxFor !== r.id) return;
    const f = c.facts || {}, cp = c.compaction || {};
    const RIGHTS = { news_credit: "You confirmed the rights: the video is used with credit", owned: "Your own video", slides: "Slides instead of the video", asked: "Asked, waiting for your answer" };
    const row = (k, v, tid) => `<div class="hc-kv" data-testid="${tid}"><dt>${esc(k)}</dt><dd>${v}</dd></div>`;
    const host = (u) => (typeof hostOf === "function" ? hostOf(u) : "");
    const src = f.source && f.source.url ? `<a href="${escUrl(f.source.url)}" target="_blank" rel="noopener">${esc(f.source.title || f.source.site || host(f.source.url))}</a><small>${esc(f.source.site || host(f.source.url) || "")}</small>` : `<span class="muted">No source yet</span>`;
    const net = (s) => (typeof netName === "function" ? netName(s) : s);
    body.innerHTML = `<dl class="hc-dl">
        ${row("Source", src, "ctx-source")}
        ${row("Rights", esc(RIGHTS[f.rights] || f.rights || "Not needed"), "ctx-rights")}
        ${row("Brand kit", esc([f.kit && f.kit.name, f.kit && f.kit.handle].filter(Boolean).join(" · ") || "Your brand kit"), "ctx-kit")}
        ${row("Template", f.template && f.template.name ? `${esc(f.template.name)}${f.template.why ? `<small>${esc(f.template.why)}</small>` : ""}` : `<span class="muted">Picked when the post is made</span>`, "ctx-template")}
        ${row("Channels", (f.channels || []).map((ch) => `<span class="hc-tag">${esc(ch.name || ch.service)}<small>${esc(net(ch.service))}</small></span>`).join("") || "None", "ctx-channels")}
        ${row("Format and look", esc(`${f.format || "auto"} · ${f.theme || "auto"}`), "ctx-format")}
        ${row("Rules applied", (f.rules || []).length ? `<ul class="hc-rules">${f.rules.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<span class="muted">None yet</span>`, "ctx-rules")}
      </dl>
      <section class="hc-conv" data-testid="ctx-compaction"><h3 class="hc-h3">Conversation</h3>
        <p class="sm">${cp.summarized ? `${esc(cp.summarized)} earlier message${cp.summarized === 1 ? "" : "s"} summarized · the last ${esc(cp.kept)} kept word for word.` : `All ${esc(cp.total || 0)} message${cp.total === 1 ? "" : "s"} kept word for word. Summarizing starts after ${esc(cp.threshold || 10)}.`}</p>
        ${cp.summary ? `<details><summary>The summary the agent reads</summary><pre class="hc-sum" data-testid="ctx-summary">${esc(cp.summary)}</pre></details>` : ""}
        <p class="muted xs">The facts above are always sent in full, however long the thread gets.</p></section>`;
  }
  function openCtx() {
    const r = run(); if (r) HC.ctxKey = `${r.id}:${((r.result || {}).thread || []).length}:${r.status}:${((r.result || {}).packages || []).length}`;
    drawer.hidden = false; ctxB.setAttribute("aria-expanded", "true"); scrimSync();
    document.getElementById("hcCtxX").focus(); loadCtx(true);
  }
  function closeCtx() {
    if (drawer.hidden) return;
    drawer.hidden = true; ctxB.setAttribute("aria-expanded", "false"); scrimSync(); ctxB.focus();
  }
  ctxB.onclick = () => (drawer.hidden ? openCtx() : closeCtx());
  document.getElementById("hcCtxX").onclick = closeCtx;

  function scrimSync() {
    const phone = matchMedia("(max-width:860px)").matches;
    scrim.hidden = !(!drawer.hidden || (phone && !panel.hidden));
  }
  scrim.onclick = () => { closeCtx(); closePanel(); };
  // Esc closes the newest layer: the context drawer, then the preview, then the threads drawer (dialogs keep their own)
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || document.querySelector("dialog[open]") || root.hidden) return;
    if (!drawer.hidden) { e.preventDefault(); closeCtx(); }
    else if (!panel.hidden) { e.preventDefault(); closePanel(); }
    else if (rv.classList.contains("hc-side-open")) { e.preventDefault(); sideDrawer(false); }
  });
  // a click outside the threads drawer (phone) closes it
  document.addEventListener("click", (e) => { if (rv.classList.contains("hc-side-open") && !side.contains(e.target) && !menuB.contains(e.target)) sideDrawer(false); });
  // leaving the thread (New post, Discover) closes its panels
  mpNew.addEventListener("click", () => { closePanel(); closeCtx(); rv.classList.remove("hc-side-open"); menuB.setAttribute("aria-expanded", "false"); scrimSync(); });

  // the start screen's prompt sends on Enter too (Shift+Enter: new line), like the thread composer
  const aq = document.getElementById("agQuery");
  if (aq) aq.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && aq.value.trim()) { e.preventDefault(); document.getElementById("agentForm").requestSubmit(); } });
  window.HQ_CHAT = { decorate, openPanel, closePanel, openCtx, closeCtx };
})();
