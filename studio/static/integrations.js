// HuhQueue front end: Settings -> Integrations. A logo grid: each tile is the service's mark, its name, a status
// (Connected / Connect), one line on what it does and an (i) popover with the details. Publishing networks come through
// Buffer (Connected when a channel of that network is in Buffer), research keys (Product Hunt, X, YouTube Data API) open
// a dialog with the key form (Test, Save, Remove, masked "…abcd" only), AI engines read GET /api/ai/engine, and AI apps
// point at the MCP connector. Marks: brand-logos-data.js (simple-icons, CC0) via brand-mark.js.
"use strict";
(function () {
  const I = { data: null };
  const mark = (id) => (window.brandMark ? window.brandMark(id) : "");
  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  };
  const NETS = [  // Buffer service id, mark, name, one line, details
    ["instagram", "instagram", "Instagram", "Reels, carousels and posts", "Publishes through your Buffer Instagram channel: Reels, carousels, single images and first comments. HuhQueue checks every Instagram rule (ratio, length, hashtags) before it sends."],
    ["youtube", "youtube", "YouTube", "Shorts with title and category", "Publishes Shorts through Buffer with the title and category YouTube needs. One take cuts a long video into Shorts."],
    ["tiktok", "tiktok", "TikTok", "Vertical videos", "Publishes vertical videos through your Buffer TikTok channel."],
    ["linkedin", "linkedin", "LinkedIn", "Posts and documents", "Publishes to your LinkedIn profile or page through Buffer, with LinkedIn's own length and link counting."],
    ["facebook", "facebook", "Facebook", "Page posts and Reels", "Publishes to your Facebook Page through Buffer."],
    ["twitter", "x", "X", "Posts and threads", "Publishes posts and threads to X through Buffer; X's weighted character count is checked first."],
    ["threads", "threads", "Threads", "Posts and topics", "Publishes to Threads through Buffer."],
    ["bluesky", "bluesky", "Bluesky", "Posts and threads", "Publishes to Bluesky through Buffer, counted in graphemes the way Bluesky counts."],
  ];
  const KEYS = { ph: ["producthunt", "Today's launches in News"], x: ["x", "X search source and post media"], yt: ["youtube", "YouTube search in Find media"] };
  const AIS = [  // engine id, mark, name, one line
    ["gemini-studio", "gemini", "Google Gemini", "Free engine on AI Studio"],
    ["nvidia", "nvidia", "NVIDIA NIM", "Free Nemotron engine"],
    ["hf", "huggingface", "Hugging Face", "Free open models"],
    ["claude", "claude", "Claude", "Best writing, included allowance"],
  ];
  const chip = (on, label) => `<span class="integ-st ${on ? "ok" : "none"}" data-testid="integ-tile-status">${on ? "Connected" : esc(label || "Connect")}</span>`;
  function tile(t) {
    const tipId = `integTip-${t.id}`;
    const act = t.action ? `<button class="btn2 sm integ-act" type="button" data-act="${esc(t.action)}" data-key="${esc(t.key || "")}" data-testid="integ-act-${t.id}">${esc(t.actLabel)}</button>`
      : t.href ? `<a class="btn2 sm integ-act" href="${esc(t.href)}" target="_blank" rel="noopener" data-testid="integ-act-${t.id}">${esc(t.actLabel)} <span aria-hidden="true">↗</span></a>` : "";
    return `<div class="integ-tile${t.on ? " on" : ""}" data-testid="integ-tile-${t.id}" data-on="${t.on ? 1 : 0}">
      <div class="integ-tile-top">${mark(t.mark)}${chip(t.on, t.offLabel)}</div>
      <div class="integ-tile-name"><h3>${esc(t.name)}</h3>
        <button type="button" class="info-i" aria-label="About ${esc(t.name)}" aria-expanded="false" aria-controls="${tipId}" data-testid="integ-info-${t.id}">i</button></div>
      <p class="integ-tile-v">${esc(t.value)}</p>
      <div class="hq-tip" id="${tipId}" role="tooltip" hidden>${t.tip}</div>
      ${act}</div>`;
  }
  function tiles(d) {
    const chans = (typeof S !== "undefined" && S.channels) || [];
    const has = (svc) => chans.some((c) => (c.service || "").toLowerCase() === svc);
    const signed = typeof S !== "undefined" && S.session && S.session.connected !== false;
    const out = { publish: [], research: [], ai: [], apps: [] };
    out.publish.push({ id: "buffer", mark: "buffer", name: "Buffer", on: !!signed, value: "Your channels, queue and drafts",
      tip: "<b>Buffer</b><p>HuhQueue signs in with Buffer and schedules through Buffer's API: your channels, queue, drafts and ideas stay in your Buffer account.</p>",
      href: "https://publish.buffer.com/channels", actLabel: "Open Buffer" });
    for (const [svc, m, name, v, det] of NETS) {
      const on = has(svc);
      out.publish.push({ id: svc, mark: m, name, on, value: v, tip: `<b>${esc(name)}</b><p>${esc(det)}</p>${on ? "" : "<p>Add the channel in Buffer, then Refresh from Buffer.</p>"}`,
        href: on ? "" : "https://publish.buffer.com/channels", actLabel: "Add in Buffer" });
    }
    for (const c of d.integrations || []) {
      const [m, v] = KEYS[c.key] || ["key", c.unlocks];
      const app = c.app || {};
      const on = !!c.connected || !!app.on;
      const status = c.connected ? `Your key ${esc(c.hint)}${c.tested ? ` · ${c.tested_ok ? "tested" : "test failed"} ${esc(ago(c.tested))}` : ""}` : app.label ? esc(app.label) : "Not connected";
      out.research.push({ id: c.key === "x" ? "x-api" : c.key === "yt" ? "yt-api" : c.key, key: c.key, mark: m, name: c.name, on, value: v,
        tip: `<b>${esc(c.name)}</b><p>${esc(c.unlocks)}</p><p class="muted">${status}</p>`, action: "key", actLabel: c.connected ? "Manage" : on ? "Use my key" : "Connect" });
    }
    const st = window.HQ_AI_ENGINE && window.HQ_AI_ENGINE.state;
    for (const [eid, m, name, v] of AIS) {
      const o = st && st.engines.find((x) => x.id === eid);
      const on = !!(o && o.available);
      out.ai.push({ id: "ai-" + eid, mark: m, name, on, value: v, offLabel: o && o.link ? "Add key" : "Connect",
        tip: `<b>${esc(name)}</b><p>${esc((o && o.about) || "")}</p>${o && !o.available && o.reason ? `<p class="muted">${esc(o.reason)}</p>` : ""}${o && o.byo ? "<p>Runs on your own key.</p>" : ""}`,
        action: "ai", actLabel: on ? "Settings" : "Add a key" });
    }
    const own = st && st.engines.find((x) => x.id === "own");
    out.ai.push({ id: "ai-openai", mark: "openai", name: "OpenAI & more", on: !!(own && own.available), value: "Your own key: OpenAI, Groq, OpenRouter",
      tip: "<b>Bring your own key</b><p>Add an OpenAI, Groq, OpenRouter or any OpenAI-compatible key. It's encrypted, tested on save and billed by your provider.</p>",
      action: "ai", actLabel: own && own.available ? "Manage" : "Add a key" });
    out.apps.push({ id: "mcp", mark: "mcp", name: "Claude, ChatGPT & MCP", on: false, offLabel: "Connect", value: "Use HuhQueue from your AI app",
      tip: "<b>MCP connector</b><p>Claude, ChatGPT, Cursor and Claude Code use HuhQueue's tools over MCP. Your app does the thinking; HuhQueue supplies the tools. Free.</p>",
      action: "apps", actLabel: "Connect an app" });
    return out;
  }
  function drawTiles() {
    const box = document.getElementById("integTiles"); if (!box || !I.data) return;
    const g = tiles(I.data);
    const sec = (key, title) => `<section class="integ-grp" aria-label="${esc(title)}" data-testid="integ-group-${key}"><h3 class="integ-grp-h">${esc(title)}</h3>
      <div class="integ-grid">${g[key].map(tile).join("")}</div></section>`;
    box.innerHTML = sec("publish", "Publish through Buffer") + sec("research", "Research") + sec("ai", "AI engines") + sec("apps", "AI apps");
    box.querySelectorAll(".integ-act[data-act]").forEach((b) => (b.onclick = () => {
      if (b.dataset.act === "key") openKey(b.dataset.key);
      else if (b.dataset.act === "ai") document.getElementById(b.textContent.includes("key") ? "aiKeysH" : "aiH")?.scrollIntoView({ behavior: "smooth", block: "start" });
      else if (b.dataset.act === "apps") showTab("apps");
    }));
  }
  function status(c) {
    if (!c.connected) {
      const app = c.app || {};
      return app.label ? `<span class="integ-st ok app" data-testid="integ-status">${esc(app.label)}</span>` : `<span class="integ-st none" data-testid="integ-status">Not connected</span>`;
    }
    const tested = c.tested ? ` · ${c.tested_ok ? "tested" : "test failed"} ${ago(c.tested)}` : " · not tested yet";
    return `<span class="integ-st ${c.tested && !c.tested_ok ? "bad" : "ok"}" data-testid="integ-status">Your key · ${esc(c.hint)}${esc(tested)}</span>`;
  }
  function card(c) {
    return `<section class="integ-card" id="integ-${c.key}" data-key="${c.key}" data-testid="integ-card-${c.key}" aria-labelledby="integH-${c.key}">
      <div class="integ-top">${mark((KEYS[c.key] || ["key"])[0])}<h3 class="card-title" id="integH-${c.key}">${esc(c.name)}</h3>${status(c)}</div>
      <p class="muted sm">${esc(c.unlocks)}</p>
      <details class="integ-how"${c.connected || (c.app && c.app.label) ? "" : " open"}><summary>${c.app && c.app.label ? "Use your own key" : "Get a key"}</summary>
        <ol class="sm">${c.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        <p class="sm"><a class="link" href="${esc(c.get_url)}" target="_blank" rel="noopener" data-testid="integ-get-${c.key}">Open ${esc(new URL(c.get_url).host)} ↗</a></p>
        ${c.redirect_uri ? `<p class="muted xs">Redirect URI: <code class="integ-code" data-testid="integ-redirect">${esc(c.redirect_uri)}</code> <button class="link xs" type="button" data-copy="${esc(c.redirect_uri)}">Copy</button></p>` : ""}
      </details>
      <form class="integ-form" data-key="${c.key}" autocomplete="off">
        <label class="label" for="integIn-${c.key}">${esc(c.name)} ${esc(c.what)}</label>
        <input id="integIn-${c.key}" type="password" autocomplete="off" spellcheck="false" placeholder="${c.connected ? "paste a new key to replace " + esc(c.hint) : "paste your " + esc(c.what)}" data-testid="integ-input-${c.key}">
        <div class="row tight integ-btns">
          <button class="btn2 sm" type="button" data-act="test" data-testid="integ-test-${c.key}">Test</button>
          <button class="btn sm" type="submit" data-testid="integ-save-${c.key}">Save</button>
          ${c.connected ? `<button class="btn2 sm danger" type="button" data-act="remove" data-testid="integ-remove-${c.key}">Remove</button>` : ""}
        </div>
        <p class="integ-msg sm" role="status" data-testid="integ-msg-${c.key}">${c.connected && c.test_message ? esc(c.test_message) : ""}</p>
      </form></section>`;
  }
  const dlg = () => document.getElementById("integDlg");
  function openKey(key) {
    const c = (I.data && I.data.integrations || []).find((x) => x.key === key); if (!c) return;
    I.open = key;
    document.getElementById("integDlgT").textContent = c.name;
    const box = document.getElementById("integCards");
    box.innerHTML = card(c);
    box.querySelectorAll(".integ-form").forEach(wire);
    box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"), () => {})));
    const d = dlg();
    if (d && !d.open) { if (d.showModal) d.showModal(); else d.setAttribute("open", ""); }
    setTimeout(() => box.querySelector("input")?.focus(), 50);
  }
  function draw(d) {
    I.data = d;
    drawTiles();
    if (I.open && dlg()?.open) openKey(I.open);  // refresh the open card in place
    const rd = document.getElementById("integRedirects");
    if (rd) rd.innerHTML = `<summary class="card-title">Developer setup: callback and redirect URLs</summary><p class="muted sm">For the developer apps behind HuhQueue. Paste these exactly where each service asks.</p>
      <ul class="integ-urls">${(d.redirects || []).map((r) => `<li data-testid="integ-url"><span class="sm"><b>${esc(r.name)}</b></span><code class="integ-code">${esc(r.url)}</code><button class="btn2 sm" type="button" data-copy="${esc(r.url)}" data-testid="integ-copy">Copy</button></li>`).join("")}</ul>`;
    document.querySelectorAll("#integRedirects [data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"), () => toast("Copy failed; select the text instead"))));
  }
  function wire(form) {
    const key = form.dataset.key, input = form.querySelector("input"), msg = form.querySelector(".integ-msg");
    const say = (text, ok) => { msg.textContent = text; msg.className = "integ-msg sm " + (ok === true ? "ok" : ok === false ? "bad" : ""); };
    form.querySelector("[data-act=test]").onclick = async (ev) => {
      const b = ev.currentTarget; b.disabled = true; b.textContent = "Testing…"; say("Checking with the service…");
      try { const r = await api(`/api/integrations/${key}/test`, { method: "POST", body: input.value ? { value: input.value } : {} });
        if (!input.value) { await load(); const m = document.querySelector(`[data-testid=integ-msg-${key}]`); if (m) { m.textContent = r.message; m.className = "integ-msg sm " + (r.ok ? "ok" : "bad"); } }
        else say(r.message, r.ok); }
      catch (e) { say(e.message, false); } finally { b.disabled = false; b.textContent = "Test"; }
    };
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!input.value) { say("Paste a key first.", false); input.focus(); return; }
      try { const d = await api(`/api/integrations/${key}`, { method: "POST", body: { value: input.value } }); input.value = ""; draw(d); toast("Key saved"); }
      catch (e) { say(e.message, false); }
    });
    const rm = form.querySelector("[data-act=remove]");
    if (rm) rm.onclick = async () => {
      if (!(await hqConfirm("Remove this key? Features that use it stop until you add one again.", { ok: "Remove key", danger: true }))) return;
      try { draw(await api(`/api/integrations/${key}`, { method: "DELETE" })); toast("Key removed"); } catch (e) { say(e.message, false); }
    };
  }
  async function load() {
    try { draw(await api("/api/integrations")); } catch (e) { const box = document.getElementById("integTiles"); if (box) box.innerHTML = `<p class="st err">${esc(e.message)}</p>`; }
  }
  const x = document.getElementById("integDlgX");
  if (x) x.onclick = () => { I.open = null; dlg().close(); };
  dlg()?.addEventListener("close", () => { I.open = null; });
  // open Settings at Integrations, or straight at one key's dialog: data-integ="integ-ph" (News -> Sources) or
  // /?tab=settings&sub=integ-ph
  window.integOpen = function (id) {
    showTab("settings");
    const key = /^integ-([a-z]+)$/.test(id || "") ? id.slice(6) : "";
    const go = (n) => {
      if (key && I.data) { openKey(key); return; }
      if (key && n < 30) { setTimeout(() => go(n + 1), 200); return; }
      const el = document.getElementById("integrations");
      if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); }
    };
    setTimeout(() => go(0), 350);
  };
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-integ]"); if (!g) return;
    e.preventDefault(); e.stopPropagation(); window.integOpen(g.dataset.integ);
  }, true);
  window.HS_TABS = Object.assign(window.HS_TABS || {}, { integrations: load });
  window.HQ_INTEGRATIONS = { redraw: drawTiles, open: openKey };
  const p = new URLSearchParams(location.search);
  if (p.get("tab") === "settings" && /^(integrations|integ-[a-z]+)$/.test(p.get("sub") || "")) {
    const want = p.get("sub"); setTimeout(() => window.integOpen(want), 1200);
  }
})();
