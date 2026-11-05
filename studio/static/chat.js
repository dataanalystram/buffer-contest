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
    panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>', clip: '<path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 7"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    // from Lucide (https://lucide.dev, ISC licence; see NOTICE): bot, user, square, arrow-down, check, key-round
    bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    stop: '<rect width="14" height="14" x="5" y="5" rx="2"/>', down: '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    key: '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>',
  };
  // the threads column: inline (and hideable, kept in localStorage hq_threads) on a wide screen, an overlay below 1101 px
  const TKEY = "hq_threads", overlayMq = matchMedia("(max-width:1100px)"), docEl = document.documentElement;
  const isOverlay = () => overlayMq.matches;
  const threadsHidden = () => docEl.classList.contains("threads-hidden");
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
  const sideScrim = el("div", "hc-side-scrim", { id: "hcSideScrim", "data-testid": "chat-sidebar-scrim" }); sideScrim.hidden = true;
  // move house.js's thread nodes into the centered column (their ids and test ids stay)
  [...rv.children].forEach((n) => main.appendChild(n));
  // chat order: the request, the agent's answer, then what's ready (house.js drew the ready summary first)
  const readyBox = document.getElementById("mpReady"), agentMsg = main.querySelector(".mp-msg.agent");
  if (readyBox && agentMsg) agentMsg.after(readyBox);
  rv.append(side, sideScrim, main, panel);
  document.body.append(drawer, scrim);

  // the bar: threads (phone), title, model, context, new post
  const bar = main.querySelector(".mp-thread-bar");
  const mpNew = document.getElementById("mpNew");
  mpNew.innerHTML = `${ic(I.plus)}<span>New post</span>`; mpNew.setAttribute("aria-label", "New post");
  const menuB = el("button", "btn2 sm icon hc-menu", { type: "button", id: "hcMenu", "data-testid": "chat-sidebar-open", "aria-label": "Threads", "aria-controls": "hcSide", "aria-expanded": "false" });
  menuB.innerHTML = `${ic(I.panel)}<span class="hc-menu-l">Hide threads</span>`;
  const head = el("div", "hc-head");
  head.innerHTML = `<h2 class="hc-title" id="hcTitle" data-testid="chat-title">Post</h2>`;
  head.appendChild(document.getElementById("mpStatus"));
  const model = el("button", "hc-chip hc-model", { type: "button", id: "hcModel", "data-testid": "chat-model", "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": "hcModelMenu", title: "The model that runs this agent. Click to change it." });
  const ctxB = el("button", "hc-chip hc-ctxb", { type: "button", id: "hcCtxB", "data-testid": "chat-context", "aria-controls": "hcCtx", "aria-expanded": "false" });
  ctxB.innerHTML = `${ic(I.info)}<span>Context</span>`;
  bar.prepend(menuB, head);
  bar.append(model, ctxB);
  bar.appendChild(mpNew);
  // the composer gets a footer with the model; the actions row becomes the Ready to send bar
  // the composer: one prompt box (text on top; attach, the model pill and Send in a row under it), the quick fixes as a
  // compact row above it, and the Ready to send bar above that, all in one sticky dock with a solid backing, so nothing in
  // the thread ever shows through or under it (patterns from assistant-ui / AI Elements prompt input, our own code)
  const reply = document.getElementById("replyForm"), acts = document.getElementById("mpActs");
  const row0 = reply.querySelector(".row"), up = document.getElementById("replyUpload"), go = reply.querySelector("[data-testid=reply-go]"), qf = document.getElementById("replyChips");
  const pbox = el("div", "hc-box", { "data-testid": "chat-prompt-box" }), tools = el("div", "hc-ctools");
  up.classList.add("hc-attach"); const upl = up.querySelector("span"); if (upl) upl.className = "hc-attach-l";
  const cmodel = el("button", "hc-pill hc-cmodel", { type: "button", id: "hcCModel", "data-testid": "chat-composer-model", "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": "hcModelMenu" });
  const hint = el("span", "hc-hint muted xs"); hint.textContent = "Enter to send · Shift+Enter for a new line";
  go.classList.add("hc-send"); go.innerHTML = `${ic(I.up)}<span class="hc-send-l">Send</span>`;
  tools.append(up, cmodel, el("span", "hc-sp", { "aria-hidden": "true" }), hint, go);
  pbox.append(document.getElementById("replyIn"), tools);
  reply.insertBefore(pbox, row0); row0.remove();
  reply.insertBefore(qf, pbox);
  acts.classList.add("hc-ready"); acts.setAttribute("role", "region"); acts.setAttribute("aria-label", "Ready to send");
  const readyTxt = el("span", "hc-ready-t", { id: "hcReadyT", "data-testid": "chat-ready-text" });
  acts.prepend(readyTxt);
  const dock = el("div", "hc-dock", { id: "hcDock", "data-testid": "chat-dock" });
  reply.parentNode.insertBefore(dock, reply); dock.appendChild(reply);
  // the Ready bar rides in the dock on a wide screen; on a phone it stays in the thread (the dock would cover half of it)
  const phoneMq = matchMedia("(max-width:600px)");
  const placeActs = () => { if (phoneMq.matches) dock.parentNode.insertBefore(acts, dock); else dock.insertBefore(acts, reply); };
  placeActs(); if (phoneMq.addEventListener) phoneMq.addEventListener("change", placeActs);
  // jumps inside the thread (scrollIntoView, keyboard focus) land clear of the sticky bar and the dock, never under them
  const pad = () => { const d = dock.offsetHeight, b = bar.offsetHeight, nav = matchMedia("(max-width:860px)").matches && d ? 68 : 0;
    docEl.style.scrollPaddingBottom = d ? `${d + nav + 8}px` : ""; docEl.style.scrollPaddingTop = d ? `${(document.querySelector("header.top") || {}).offsetHeight + b + 8 || 0}px` : ""; };
  if (window.ResizeObserver) new ResizeObserver(pad).observe(dock);
  // a phone gets the short placeholder (the long one wrapped onto a second, cut-off line)
  const ri0 = document.getElementById("replyIn"), longPh = ri0.placeholder, mq = matchMedia("(max-width:600px)");
  const ph = () => { ri0.placeholder = mq.matches ? "Ask for a change…" : longPh; };
  ph(); if (mq.addEventListener) mq.addEventListener("change", ph);
  // drafts: what you typed for a thread stays with that thread (switching threads, toggling sidebars, a reload)
  const DKEY = "hq_drafts";
  const drafts = () => { try { return JSON.parse(localStorage.getItem(DKEY) || "{}") || {}; } catch (_) { return {}; } };
  const saveDrafts = (d) => { const ks = Object.keys(d); ks.slice(0, Math.max(0, ks.length - 20)).forEach((k) => delete d[k]); try { localStorage.setItem(DKEY, JSON.stringify(d)); } catch (_) { /* full or private */ } };
  let draftT = 0;
  ri0.addEventListener("input", () => { clearTimeout(draftT); draftT = setTimeout(() => { const r = run(); if (!r) return; const d = drafts(); delete d[r.id];
    if (ri0.value.trim()) d[r.id] = ri0.value; saveDrafts(d); }, 250); });
  function syncDraft(r) {
    if (!r) return;
    if (HC.draftFor !== r.id) {
      HC.draftFor = r.id; const t = drafts()[r.id] || "";
      if (ri0.value !== t) { ri0.value = t; ri0.style.height = "auto"; if (t) ri0.style.height = Math.min(160, ri0.scrollHeight) + "px"; }
    } else if (!ri0.value) { const d = drafts(); if (d[r.id]) { delete d[r.id]; saveDrafts(d); } }
  }

  // ------------------------------------------------------------ the thread sidebar (past runs = conversations)
  // One heading per day bucket, newest first, whatever order the rows arrive in (4 Nov 2026: "Today" showed twice when a
  // backdated run sat between two of today's; the old loop opened a new heading on every change). Day edges are local
  // calendar midnights from new Date(y, m, d - n), never "midnight - 86400000": a daylight-saving day is 23 or 25 hours.
  const GROUPS = ["Today", "Yesterday", "Previous 7 days", "Older"];
  const byNewest = (a, b) => (Number(b.created) || 0) - (Number(a.created) || 0) || (Number(b.id) || 0) - (Number(a.id) || 0);
  function groupThreads(rows, now) {
    const n = new Date(now == null ? Date.now() : now), y = n.getFullYear(), m = n.getMonth(), d = n.getDate();
    const edges = [new Date(y, m, d).getTime(), new Date(y, m, d - 1).getTime(), new Date(y, m, d - 7).getTime()];
    const out = GROUPS.map((g) => [g, []]);
    for (const x of rows) {
      const t = (Number(x.created) || 0) * 1000, i = edges.findIndex((e) => t >= e);
      out[i < 0 ? 3 : i][1].push(x);
    }
    out.forEach(([, list]) => list.sort(byNewest));
    return out.filter(([, list]) => list.length);
  }
  const STATE = { ready: "Ready", draft: "In Buffer", scheduled: "Scheduled", running: "Working", failed: "Stopped", interrupted: "Interrupted" };
  // lazy: nothing is drawn until the sidebar can be seen (a thread is open, or the phone drawer), then a page at a time
  const sideVisible = () => root.classList.contains("mp-in-thread") && (isOverlay() ? rv.classList.contains("hc-side-open") : !threadsHidden());
  function drawList(force) {
    if (!force && !sideVisible()) { HC.dirty = true; return; }
    HC.dirty = false;
    const box = document.getElementById("hcList"), q = HC.q.trim().toLowerCase(), cur = run() && run().id;
    const all = HC.rows.filter((x) => !q || String(x.title || "").toLowerCase().includes(q)).sort(byNewest);
    const rows = all.slice(0, HC.shown);
    if (!HC.loaded) { box.innerHTML = `<div class="hc-sk" data-testid="chat-thread-skeleton" aria-busy="true" aria-label="Loading threads">${'<span class="sk sk-row"></span>'.repeat(5)}</div>`; return; }
    if (!rows.length) { box.innerHTML = `<p class="muted sm hc-empty">${HC.rows.length ? "No thread matches." : "Your posts show up here as threads."}</p>`; return; }
    let html = "";
    for (const [g, list] of groupThreads(rows)) {
      html += `<h3 class="hc-grp" data-testid="chat-thread-group">${esc(g)}</h3><ul class="hc-ul">`;
      for (const x of list) {
        const on = String(x.id) === String(cur);
        html += `<li class="hc-li${on ? " on" : ""}" data-id="${esc(x.id)}"><button type="button" class="hc-th" data-open="${esc(x.id)}" data-kind="${esc(x.kind)}" data-testid="chat-thread"${on ? ' aria-current="true"' : ""}>
            <span class="hc-th-t" title="${esc(x.title || "Your post")}">${esc(x.title || "Your post")}</span><small class="hc-th-s st-${esc(x.state || x.status)}">${esc(STATE[x.state] || x.state || x.status || "")}${x.summarized ? " · summarized" : ""}</small></button>
          ${x.kind === "take" ? "" : `<button type="button" class="hc-th-r" data-rename="${esc(x.id)}" data-testid="chat-thread-rename" aria-label="Rename “${esc(String(x.title || "").slice(0, 60))}”">${ic(I.pen)}</button>`}${x.thread_id ? `<button type="button" class="hc-th-r hc-th-del" data-del-thread="${esc(x.id)}" data-testid="chat-thread-delete" aria-label="Delete thread “${esc(String(x.title || "").slice(0, 60))}”">${ic(I.trash)}</button>` : ""}</li>`;
      }
      html += "</ul>";
    }
    box.innerHTML = html + (all.length > rows.length
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
    const dt = e.target.closest("[data-del-thread]"); if (dt) return deleteThread(dt.dataset.delThread);
    const o = e.target.closest("[data-open]"); if (!o) return;
    sideDrawer(false);
    if (o.dataset.kind === "take") return window.openTake && window.openTake(o.dataset.open);
    // the clicked thread is marked at once and the conversation shows a skeleton until its run arrives; a quicker second
    // click aborts this request, so the earlier thread never lands over the later one (loading.js)
    const tk = HQLoad.start("thread"), H = window.H_STATE || {};
    // the previous thread's poll stops here: a late answer for it can never redraw over the thread picked now (house.js watch)
    clearTimeout(H.poll); H.watching = null;
    markThread(o.dataset.open);
    threadLoading(true);
    try { const x = await api(`/api/agent/runs/${o.dataset.open}`, { signal: tk.signal }); if (!tk.live()) return; const mx = H.missed === String(x.id); H.missed = null; threadLoading(false); window.showRun(x); if (mx || busyRun(x)) window.watchRun(x.id); window.scrollTo({ top: 0 }); }
    catch (err) {
      if (HQLoad.aborted(err) || !tk.live()) return;
      toast(err.message, 5000);
      // the thread did not load: the list marks the thread still on screen again, and that thread is fetched again while the
      // skeleton and the inert dock stay up (a reply sent meanwhile or progress made shows up, and no caption or schedule
      // edit can start on a card that is about to be redrawn); if that fetch fails too, the copy on screen comes back as it was
      const cur = H.run; if (!cur) { threadLoading(false); return; }
      markThread(cur.id);
      // a reply answered while this loaded was not drawn (house.js sets H.missed): the thread is polled once more, so the
      // reply shows up even if the copy fetched here, or the copy on screen, predates it
      const missed = () => { const m = H.missed === String(cur.id); H.missed = null; return m; };
      try { const y = await api(`/api/agent/runs/${cur.id}`, { signal: tk.signal }); if (!tk.live()) return; threadLoading(false); window.showRun(y); if (missed() || busyRun(y)) window.watchRun(y.id); }
      catch (e2) { if (HQLoad.aborted(e2) || !tk.live()) return; threadLoading(false); if (missed() || busyRun(cur)) window.watchRun(cur.id); }
    }
    finally { if (tk.live()) tk.done(); }
  });
  // leaving the thread view (New post, a recent post, a Discover story): the thread request in flight is aborted, so its
  // answer (or its recovery fetch) never reopens a thread over the screen picked now, and the skeleton and inert dock go
  // (the open thread's poll stops too, so a late poll answer can't replace the screen picked now)
  window.hcCancelThread = () => { HQLoad.start("thread").done(); const W = window.H_STATE; if (W) { clearTimeout(W.poll); W.watching = null; } threadLoading(false); };
  function markThread(id) {
    document.querySelectorAll("#hcList .hc-li").forEach((li) => { const on = String(li.dataset.id) === String(id); li.classList.toggle("on", on); const b = li.querySelector(".hc-th"); if (!b) return; if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current"); });
  }
  // a skeleton over the conversation while another thread loads (the old thread's messages never stay up as if current)
  function threadLoading(on) {
    let sk = document.getElementById("hcLoading");
    rv.classList.toggle("hc-loading", on);
    if (on && !sk) { sk = el("div", "hc-loading-sk", { id: "hcLoading" }); sk.innerHTML = HQLoad.rows(4, "Loading the thread"); main.insertBefore(sk, main.children[1] || null); }
    if (sk) sk.hidden = !on;
    // the composer and the Ready bar belong to the thread still on screen: inert until the picked one arrives, so nothing
    // typed or approved meanwhile goes to the previous thread
    const dk = document.getElementById("hcDock"); if (dk) { dk.inert = on; dk.classList.toggle("hc-dock-wait", on); }
  }
  // delete a thread (studio/thread_bin.py), any kind or state: like deleting chat history, the conversation only. Media
  // is never deleted (takes, clips, uploads, posts stay). 7 days in the Bin. A foreign or missing id is a 404.
  async function deleteThread(id) {
    const row = HC.rows.find((x) => String(x.id) === String(id)); if (!row || !row.thread_id) return;
    const ok = await window.hqConfirm(`Delete “${String(row.title || "this thread").slice(0, 80)}”?\nOnly the conversation is deleted. ` +
      (row.kind === "take" ? "The take, its clips and the upload stay in One take." : "Your posts and media stay in the Library and Buffer.") +
      " It stays in the Bin for 7 days.", { ok: "Delete thread", danger: true });
    if (!ok) return;
    try {
      await api(`/api/threads/${encodeURIComponent(row.thread_id)}`, { method: "DELETE" });
      // the open conversation was deleted: leave it the way New post does, so nothing is sent to it afterwards
      const cur = run(); if (cur && String(cur.id) === String(id)) mpNew.click();
      HC.rows = HC.rows.filter((x) => String(x.id) !== String(id)); drawList(true);
      toast("Thread deleted. Your media stays (restorable for 7 days).", 5000);
    } catch (err) { toast(err.message, 5000); }
  }
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
  window.mpRecent = (rows) => { if (recent) recent(rows); HC.rows = rows || []; HC.loaded = true; drawList(); drawTitle(); };
  // the overlay (narrow screens): a modal panel with its own scrim; Esc, the scrim or a click outside closes it; Tab stays inside
  function sideDrawer(open) {
    if (!isOverlay()) open = false;
    const was = rv.classList.contains("hc-side-open");
    rv.classList.toggle("hc-side-open", open); sideScrim.hidden = !open;
    if (open) { side.setAttribute("role", "dialog"); side.setAttribute("aria-modal", "true"); } else { side.removeAttribute("role"); side.removeAttribute("aria-modal"); }
    syncToggle(); scrimSync();
    if (open && HC.dirty) drawList(true);
    if (open) document.getElementById("hcQ").focus(); else if (was && side.contains(document.activeElement)) menuB.focus();
  }
  // the inline column (wide screens): one button shows and hides it; the chat takes the room. Draft, scroll and the open
  // thread stay put: nothing is redrawn, and the message under the middle of the screen is kept in place while it moves.
  function anchorKeep(fn) {
    const kids = [...main.children].filter((n) => !n.hidden && n.offsetParent);
    const mid = innerHeight / 2, a = kids.find((n) => { const r = n.getBoundingClientRect(); return r.bottom > mid; });
    const before = a ? a.getBoundingClientRect().top : 0;
    fn();
    if (!a) return;
    const end = performance.now() + 260, keep = () => { const d = a.getBoundingClientRect().top - before; if (Math.abs(d) > 1) window.scrollBy(0, d); if (performance.now() < end) requestAnimationFrame(keep); };
    requestAnimationFrame(keep);
  }
  function setThreads(show, persist) {
    anchorKeep(() => docEl.classList.toggle("threads-hidden", !show));
    if (persist) { try { localStorage.setItem(TKEY, show ? "open" : "hidden"); } catch (_) { /* private mode */ } }
    syncToggle();
    if (show && HC.dirty) drawList(true);
    if (!show && side.contains(document.activeElement)) menuB.focus();
  }
  function syncToggle() {
    const open = isOverlay() ? rv.classList.contains("hc-side-open") : !threadsHidden();
    menuB.setAttribute("aria-expanded", String(open));
    menuB.setAttribute("aria-label", open ? "Hide threads" : "Show threads"); menuB.title = open ? "Hide threads" : "Show threads";
    menuB.querySelector(".hc-menu-l").textContent = open ? "Hide threads" : "Show threads";
    menuB.classList.toggle("on", open);
  }
  menuB.onclick = () => (isOverlay() ? sideDrawer(!rv.classList.contains("hc-side-open")) : setThreads(threadsHidden(), true));
  const onMq = () => { if (!isOverlay()) sideDrawer(false); syncToggle(); };
  if (overlayMq.addEventListener) overlayMq.addEventListener("change", onMq);
  side.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || !rv.classList.contains("hc-side-open")) return;
    const f = [...side.querySelectorAll("button,input,a[href],[tabindex]:not([tabindex='-1'])")].filter((n) => !n.disabled && n.offsetParent);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  sideScrim.onclick = () => sideDrawer(false);
  syncToggle();
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
    const label = mcp ? `Made in ${n}. ${br.hint}` : `Model: ${n}${nxt ? `, next reply: ${nxt}` : ""}. Open Settings, AI engine`;
    model.innerHTML = `${ic(I.chip)}<span>${esc(n)}</span>`;
    model.title = mcp ? br.hint : "The model that runs this agent. Change it in Settings > AI engine.";
    model.setAttribute("aria-label", label);
    // the composer pill (sidebars layout) names the model that writes the NEXT reply, short so the pill never squeezes
    // to "b…"; the run's own model and an MCP run's hint ride in its title and label
    const es = HC.eng, nx = !mcp && es && es.engines ? es.engines.find((o) => o.id === es.runs) : null;
    cmodel.innerHTML = `${ic(I.chip)}<span>${esc(nx ? nx.short : (nxt || n))}</span>`;
    cmodel.setAttribute("aria-label", label);
    cmodel.title = mcp ? `${n} — ${br.hint}` : nxt ? `Next reply: ${nxt} (this post was made with ${n})` : `Model: ${n}`;
    if (mcp) cmodel.dataset.hint = br.hint || ""; else delete cmodel.dataset.hint;
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
    // the steps: a live list while it works (pending, running, done), the time it has taken, folded into one row once done;
    // a run with no recorded steps shows "Thinking…" while it works and no row at all after (never "0 steps")
    const tr = document.getElementById("mpTrace"), steps = r.steps || [], cur = steps.find((s) => s.status === "running");
    const done = steps.filter((s) => ["done", "warn", "skipped"].includes(s.status)).length;
    tr.hidden = !busy && !steps.length;  // never "Worked through 0 steps": no steps, no row
    traceText(r);
    tr.classList.toggle("hc-working", busy);
    tr.dataset.state = busy ? "working" : r.status === "failed" ? "failed" : "done";
    tr.style.setProperty("--hc-prog", steps.length ? String(done / steps.length) : "0");
    clearInterval(HC.tick); if (busy) HC.tick = setInterval(() => { const x = run(); if (x && busyRun(x)) traceText(x); else clearInterval(HC.tick); }, 1000);
    const want = HC.trace[r.id] ?? (busy ? steps.length > 0 : ["failed", "interrupted"].includes(r.status));
    if (tr.open !== want) { HC.syncing = true; tr.open = want; setTimeout(() => { HC.syncing = false; }, 0); }
    previews();
    // Ready to send
    const pk = res.packages || [], ready = pk.filter((p) => p.status === "ready").length;
    readyTxt.textContent = ready ? `${ready} post${ready === 1 ? "" : "s"} ready to send` : "";
    ctxB.querySelector("span").textContent = summ.summarized ? "Context · summarized" : "Context";
    if (HC.dirty) drawList();
    drawTitle(); drawModel(); avatars(); working(r);
    if (HC.panelFor && !document.querySelector(`#packages .pkg[data-ch="${sel(HC.panelFor)}"]`)) closePanel();
    else if (HC.panelFor) openPanel(HC.panelFor, false);
    // the open drawer refreshes only when the thread changed (a new turn, a new status), never on every poll tick
    const key = `${r.id}:${thread.length}:${r.status}:${(res.packages || []).length}`;
    if (!drawer.hidden && HC.ctxFor === r.id && key !== HC.ctxKey) { HC.ctxKey = key; loadCtx(false); }
  }
  const secs = (n) => (n < 60 ? `${Math.max(0, Math.round(n))}s` : `${Math.floor(n / 60)}m ${String(Math.round(n % 60)).padStart(2, "0")}s`);
  function traceText(r) {
    const steps = r.steps || [], busy = busyRun(r), cur = steps.find((s) => s.status === "running"), n = steps.length;
    const t0 = Number(r.created) || 0, t1 = busy ? Date.now() / 1000 : Number(r.updated) || t0;
    const took = t0 && t1 >= t0 && (busy || t1 - t0 >= 1) ? secs(t1 - t0) : "";  // a run done within a second shows no time
    const t = busy ? (n ? `Working… ${n} step${n === 1 ? "" : "s"}${cur ? " · " + cur.label : ""}` : "Thinking…") :
      `${r.status === "failed" ? "Stopped after" : "Worked through"} ${n} step${n === 1 ? "" : "s"}`;
    const sum = document.getElementById("mpTraceSum");
    if (sum.textContent !== t) sum.textContent = t;
    let tm = document.getElementById("hcTraceTime");
    if (!tm) { tm = el("span", "hc-trace-time", { id: "hcTraceTime", "data-testid": "mp-trace-time" }); sum.after(tm); }
    const tt = took ? (busy ? took : `${took}`) : "";
    if (tm.textContent !== tt) tm.textContent = tt;
    tm.hidden = !tt;
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
  window.mpOnRun = (r) => { if (onRun) onRun(r); rv.classList.toggle("hc-busy", busyRun(r)); syncDraft(r); setTimeout(decorate, 0); };

  // ------------------------------------------------------------ side panel: one post full size
  function openPanel(ch, focus) {
    const r = run(); if (!r) return;
    const p = ((r.result || {}).packages || []).find((x) => x.channel_id === ch); if (!p) { closePanel(); return; }
    HC.panelFor = ch;
    const card = document.querySelector(`#packages .pkg[data-ch="${sel(ch)}"]`);
    const media = (p.media && p.media.length ? p.media : (p.media_urls || []).map((u) => ({ url: u, type: /\.(mp4|mov|m4v|webm)(\?|$)/i.test(u) ? "video" : "photo" })));
    document.getElementById("hcPanelT").textContent = `${p.channel_name || ""} · ${typeof netName === "function" ? netName(p.service) : p.service}`;
    const wrap = el("div", "hc-pv");
    const fmt = el("p", "muted sm hc-pv-fmt"); fmt.textContent = window.HQ_MEDIA_LABEL ? window.HQ_MEDIA_LABEL(p).split(" · ").filter((x) => x !== "…").join(" · ") : (p.format || ""); wrap.appendChild(fmt);
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
  // a click outside the threads drawer (phone) closes it; a dialog opened from the drawer (Delete thread's choice) is not outside
  document.addEventListener("click", (e) => { if (rv.classList.contains("hc-side-open") && !side.contains(e.target) && !menuB.contains(e.target) && !(e.target.closest && e.target.closest("dialog"))) sideDrawer(false); });
  // leaving the thread (New post, Discover) closes its panels
  mpNew.addEventListener("click", () => { closePanel(); closeCtx(); if (rv.classList.contains("hc-side-open")) sideDrawer(false); scrimSync(); });

  // the start screen's prompt sends on Enter too (Shift+Enter: new line), like the thread composer
  const aq = document.getElementById("agQuery");
  if (aq) aq.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && aq.value.trim()) { e.preventDefault(); document.getElementById("agentForm").requestSubmit(); } });
  // ------------------------------------------------------------ the model menu (4 Nov 2026)
  // The model pill used to be a data-go link to Settings > AI engine, so "Change model" left the chat. It opens a menu
  // here instead: every engine GET /api/ai/engine lists, the current one checked, the unavailable ones disabled with the
  // reason (and "Add a key in Settings" where a key would unlock one). A pick is saved with PUT /api/ai/engine (the
  // account's engine, so this chat's next reply uses it) and the pill redraws. WAI-ARIA menu: arrow keys, Home/End,
  // Enter/Space, Esc (focus back to the pill), Tab or a click outside closes. A bottom sheet at phone width.
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mm = el("div", "hc-mm", { id: "hcModelMenu", role: "menu", "aria-label": "Choose the model", "data-testid": "chat-model-menu", tabindex: "-1" });
  mm.hidden = true;
  document.body.appendChild(mm);
  const MM = { from: null, busy: false, seq: 0 };
  const KEYED = { own: true, "gemini-studio": true, nvidia: true, hf: true, claude: true };  // a saved key of yours unlocks these
  function mmItems() { return [...mm.querySelectorAll("[role=menuitemradio]")]; }
  function mmNav() { return [...mm.querySelectorAll("[role=menuitemradio],.hc-mm-key")]; }  // arrows also reach "Add a key"
  function mmDraw(st) {
    const sel = st.selected || "auto";
    const rows = [{ id: "auto", short: "Automatic", model: (st.order_labels || []).slice(0, 3).join(" → "), available: true, reason: "" }, ...(st.engines || [])];
    mm.innerHTML = `<div class="hc-mm-h"><b>Model</b><small>${esc(st.runs_label ? "Now running: " + st.runs_label : "")}</small></div>` +
      rows.map((o) => {
        const on = o.id === sel, dis = !o.available && o.id !== "auto";
        const keyLink = dis && KEYED[o.id] ? `<button type="button" class="link xs hc-mm-key" data-go="settings:ai" data-testid="chat-model-addkey" role="menuitem" tabindex="-1">${ic(I.key)}Add a key in Settings</button>` : "";
        return `<div class="hc-mm-row${on ? " on" : ""}${dis ? " na" : ""}"><button type="button" role="menuitemradio" class="hc-mm-i" data-eng="${esc(o.id)}"
            data-testid="chat-model-option" aria-checked="${on}" ${dis ? 'aria-disabled="true"' : ""} tabindex="-1">
          <span class="hc-mm-ck" aria-hidden="true">${on ? ic(I.check) : ""}</span>
          <span class="hc-mm-t"><b>${esc(o.short || o.label || o.id)}</b><small>${esc(dis ? o.reason || "Unavailable" : o.model || "")}</small></span></button>${keyLink}</div>`;
      }).join("") + (st.note ? `<p class="hc-mm-note xs muted" role="note">${esc(st.note)}</p>` : "");
    mm.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => mmClose(false)));
    mmItems().forEach((b) => (b.onclick = () => mmPick(b)));
  }
  function mmPlace() {
    const a = MM.from; if (!a || mm.hidden) return;
    if (matchMedia("(max-width:600px)").matches) { mm.classList.add("sheet"); mm.style.left = mm.style.top = mm.style.width = ""; return; }
    mm.classList.remove("sheet");
    const r = a.getBoundingClientRect(), w = Math.min(340, innerWidth - 16), h = mm.offsetHeight;
    mm.style.width = w + "px";
    mm.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    const up = r.top - h - 8 >= 8 || r.bottom + h + 8 > innerHeight;  // above the composer pill when it fits
    mm.style.top = (up ? Math.max(8, r.top - h - 8) : r.bottom + 8) + "px";
  }
  async function mmOpen(from) {
    MM.from = from; [model, cmodel].forEach((b) => b.setAttribute("aria-expanded", b === from ? "true" : "false"));
    mm.hidden = false; mm.setAttribute("aria-busy", "true");
    if (HC.eng) mmDraw(HC.eng); else mm.innerHTML = `<div class="hc-mm-h"><b>Model</b><small>Loading the models…</small></div>`;
    mmPlace();
    (mm.querySelector("[aria-checked=true]") || mm).focus();
    const seq = ++MM.seq;  // a pick (PUT) or another open makes this answer stale: never let it overwrite HC.eng
    try { const st = await api("/api/ai/engine"); if (seq !== MM.seq) return; HC.eng = st; if (mm.hidden || MM.from !== from) return; mmDraw(HC.eng); mmPlace(); (mm.querySelector("[aria-checked=true]") || mmItems()[0] || mm).focus(); }
    catch (e) { if (!mm.hidden) mm.innerHTML = `<div class="hc-mm-h"><b>Model</b><small>${esc(e.message || "The models didn't load.")}</small></div>`; }
    finally { mm.removeAttribute("aria-busy"); }
  }
  function mmClose(focusBack = true) {
    if (mm.hidden) return;
    mm.hidden = true; [model, cmodel].forEach((b) => b.setAttribute("aria-expanded", "false"));
    if (focusBack && MM.from) MM.from.focus();
  }
  async function mmPick(b) {
    if (b.getAttribute("aria-disabled") === "true" || MM.busy) return;
    const id = b.dataset.eng;
    if (HC.eng && (HC.eng.selected || "auto") === id) return mmClose();
    MM.busy = true; MM.seq++; b.setAttribute("aria-busy", "true");
    try {
      HC.eng = await api("/api/ai/engine", { method: "PUT", body: { engine: id } });
      try { const br = await api("/api/agent/brain"); if (window.H_STATE) window.H_STATE.brain = br.brain; } catch (_) { /* the pill still redraws */ }
      mmClose(); drawModel();
      if (window.HQ_AI_ENGINE) window.HQ_AI_ENGINE.refresh();  // Settings > AI engine shows the same pick
      const o = (HC.eng.engines || []).find((x) => x.id === id);
      if (window.toast) toast(HC.eng.note || `This chat now uses ${o ? o.short : "Automatic"}`, HC.eng.note ? 6000 : 3000);
    } catch (e) { if (window.toast) toast(e.message || "Couldn't change the model", 5000); }
    finally { MM.busy = false; b.removeAttribute("aria-busy"); }
  }
  [model, cmodel].forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (b.dataset.hint) { if (window.toast) toast(b.dataset.hint, 6000); return; }  // an AI app ran this post over MCP: its model is picked there
    if (!mm.hidden && MM.from === b) mmClose(); else mmOpen(b);
  }));
  mm.addEventListener("keydown", (e) => {
    const items = mmNav(), i = items.indexOf(document.activeElement);
    const go = (n) => { e.preventDefault(); if (items.length) items[(n + items.length) % items.length].focus(); };
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); mmClose(); }
    else if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i < 0 ? items.length - 1 : i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items.length - 1);
    else if (e.key === "Tab") mmClose(false);
    else if ((e.key === "Enter" || e.key === " ") && i >= 0) { e.preventDefault(); if (items[i].classList.contains("hc-mm-key")) items[i].click(); else mmPick(items[i]); }
  });
  document.addEventListener("click", (e) => { if (!mm.hidden && !mm.contains(e.target) && !model.contains(e.target) && !cmodel.contains(e.target)) mmClose(false); });
  window.addEventListener("resize", mmPlace);
  window.addEventListener("scroll", () => { if (!mm.hidden && !mm.classList.contains("sheet")) mmPlace(); }, { passive: true });
  window.addEventListener("popstate", () => mmClose(false));
  api("/api/ai/engine").then((st) => { HC.eng = st; if (run()) drawModel(); }).catch(() => {});

  // ------------------------------------------------------------ avatars: one icon each for the agent and you (Lucide)
  function avatars() {
    document.querySelectorAll("#agentThread .msg > b:first-child").forEach((n) => {
      if (n.querySelector(":scope > .hc-av")) return;
      const agent = !!n.closest(".agent");
      const av = el("span", "hc-av " + (agent ? "a" : "u"), { "aria-hidden": "true", "data-testid": "chat-avatar" });
      av.innerHTML = ic(agent ? I.bot : I.user);
      n.prepend(av);
    });
  }

  // ------------------------------------------------------------ the working message (4 Nov 2026)
  // The only sign of work used to be a small spinner under the title. While a run works, the last message in the chat is
  // the agent typing: dots, then "Step N of M · <the step>", the time it has taken and Stop. It follows the run poll
  // (house.js watch -> showRun -> decorate). The page follows it down unless you scrolled up; then "Jump to latest"
  // brings you back. No new step for 60 s: "Still working, this step can take a minute". Reduced motion: no animation.
  // A run the server cut off shows house.js's Resume card instead (status "interrupted").
  const wk = el("div", "msg agent hc-work", { id: "hcWork", "data-testid": "chat-working" });
  wk.hidden = true;
  wk.innerHTML = `<b><span class="hc-av a" aria-hidden="true">${ic(I.bot)}</span>Agent</b>
    <span class="hc-work-b"><span class="hc-dots" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="hc-work-t" data-testid="chat-working-step" role="status" aria-live="polite">Thinking…</span>
      <span class="hc-work-s muted xs" data-testid="chat-working-time" aria-hidden="true"></span>
      <button type="button" class="btn2 sm hc-work-stop" data-testid="chat-working-stop">${ic(I.stop)}<span>Stop</span></button></span>
    <span class="hc-work-slow muted xs" data-testid="chat-working-slow" role="status" hidden>Still working, this step can take a minute.</span>`;
  const wkDock = reply.closest(".mp-composer") || reply;
  (wkDock.parentNode || main).insertBefore(wk, wkDock);
  const jump = el("button", "hc-jump", { type: "button", "data-testid": "chat-jump-latest" });
  jump.innerHTML = `${ic(I.down)}<span>Jump to latest</span>`; jump.hidden = true;
  document.body.appendChild(jump);
  const W = { rid: null, key: "", at: 0, pinned: true, t: 0 };
  const nearEnd = () => innerHeight + scrollY >= document.documentElement.scrollHeight - 160;
  function toLatest(smooth) { wk.scrollIntoView({ block: "end", behavior: smooth && !reduced() ? "smooth" : "auto" }); }
  const chatOn = () => !root.hidden && !rv.hidden && !wk.hidden;  // the pill belongs to the chat screen only
  window.addEventListener("scroll", () => { if (!chatOn()) { jump.hidden = true; return; } W.pinned = nearEnd(); jump.hidden = W.pinned; }, { passive: true });
  new MutationObserver(() => { if (!chatOn()) jump.hidden = true; }).observe(root, { attributes: true, attributeFilter: ["hidden"] });
  jump.onclick = () => { W.pinned = true; jump.hidden = true; toLatest(true); };
  wk.querySelector(".hc-work-stop").onclick = async (e) => {
    const b = e.currentTarget, r = run(); if (!r) return;
    b.disabled = true; b.querySelector("span").textContent = "Stopping…";
    try { const n = await api(`/api/agent/runs/${r.id}/stop`, { method: "POST", body: {} }); if (!n.stopping) window.showRun(n); if (window.toast) toast(n.stopping ? "Stopping after this step" : "Stopped"); }
    catch (err) { b.disabled = false; b.querySelector("span").textContent = "Stop"; if (window.toast) toast(err.message || "Couldn't stop it", 5000); }
  };
  function wkText(r) {
    const steps = r.steps || [], cur = steps.find((x) => x.status === "running");
    const total = Math.max(steps.length, ((r.result || {}).plan || []).length);
    const t = cur ? `Step ${steps.indexOf(cur) + 1} of ${total} · ${cur.label}` : steps.length ? `Working… step ${steps.length} of ${total}` : "Thinking…";
    const th = (r.result || {}).thread || [], lastUser = [...th].reverse().find((m) => m.role === "user");
    const t0 = Number((lastUser || {}).at) || Number(r.created) || 0;
    const tt = wk.querySelector(".hc-work-t"), ts = wk.querySelector(".hc-work-s");
    if (tt.textContent !== t) tt.textContent = t;
    const took = t0 ? secs(Date.now() / 1000 - t0) : "";
    if (ts.textContent !== took) ts.textContent = took;
    wk.querySelector(".hc-work-slow").hidden = Date.now() - W.at < 60000;
  }
  function working(r) {
    const busy = busyRun(r);
    if (!busy) { if (!wk.hidden) { wk.hidden = true; jump.hidden = true; } clearInterval(W.t); W.rid = null; return; }
    const key = `${r.id}:${(r.steps || []).map((x) => x.status).join(",")}:${((r.result || {}).thread || []).length}`;
    if (W.rid !== r.id) { W.rid = r.id; W.pinned = true; W.at = Date.now(); const b = wk.querySelector(".hc-work-stop"); b.disabled = false; b.querySelector("span").textContent = "Stop"; }
    if (key !== W.key) { W.key = key; W.at = Date.now(); }
    const was = wk.hidden; wk.hidden = false;
    wkText(r);
    clearInterval(W.t); W.t = setInterval(() => { const x = run(); if (x && busyRun(x)) wkText(x); else clearInterval(W.t); }, 1000);
    if (!chatOn()) { jump.hidden = true; return; }
    if (W.pinned) { jump.hidden = true; requestAnimationFrame(() => toLatest(!was)); } else jump.hidden = false;
  }
  // tests: age the last progress past 60 s without waiting
  window.HQ_CHAT_WORK = { stale: () => { W.at = Date.now() - 61000; const r = run(); if (r) wkText(r); } };
  window.HQ_CHAT = { decorate, openPanel, closePanel, openCtx, closeCtx, openModelMenu: () => mmOpen(cmodel) };
})();
