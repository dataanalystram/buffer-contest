// Setup after Connect Buffer: a welcome, then what you post -> your channels -> your brand -> your AI -> your feed.
// Skippable at every step and re-openable from Settings. State lives on the server (GET/PUT /api/onboarding, docs/ONBOARDING.md);
// where that route isn't there yet, the browser keeps it, so the flow never blocks on the backend. "Skipped" is the
// browser's own note (the server only knows done or not).
// News is never empty (owner, 9 Nov 2026): picking interests here makes the server follow matching starter feeds
// (studio/starter_feeds.py); skipping leaves the default tech-news feeds, followed on the first open of News.
// Feel (owner, 9 Nov 2026: "premium, Apple style"): one calm column, big light headings, each step slides in from the
// side it came from, chips tick, nothing waits on the network before the step is drawn. Motion is transform/opacity,
// ease-out, under 300 ms, and off under prefers-reduced-motion (DESIGN.md).
"use strict";
(() => {
const KEY = "hq_onb";
// the server sends its own list (options.content_types); this is the same list for when it can't
let TYPES = [["ai_news", "AI news"], ["product_launches", "Product launches"], ["yc_startups", "YC startups"], ["tutorials", "Tutorials and how-tos"],
  ["dev_tools", "Developer tools"], ["memes", "Memes and culture"], ["india_tech", "India tech"], ["research", "Research papers"], ["funding", "Funding and deals"], ["open_source", "Open source"]];
const O = { st: null, step: 0, dir: 1, el: null, back: null, intro: false, timer: 0 };
const still = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const local = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) { return {}; } };
const ICON = {
  check: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  news: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M8 9h5M8 13h5M8 17h3"/></svg>',
  brush: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 4.5l5 5L11 18H6v-5zM12.5 6.5l5 5"/></svg>',
  shield: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/></svg>',
};
async function onbState() {
  if (O.st) return O.st;
  let st = null;
  try {
    const r = await api("/api/onboarding"), p = r.profile || r;
    st = { content_types: p.content_types || [], channels: p.channel_ids || p.channels || [], website: p.website || "", done: !!p.done };
    if (r.options && Array.isArray(r.options.content_types) && r.options.content_types.length) TYPES = r.options.content_types.map((t) => [t.id, t.label]);
    O.server = true;
  } catch (_) { st = null; O.server = false; }
  O.st = Object.assign({ done: false, skipped: false, content_types: [], channels: [], website: "", step: 0 }, local(), st || {});
  S.onb = O.st;
  return O.st;
}
async function save(patch) {
  Object.assign(O.st, patch); S.onb = O.st;
  try { localStorage.setItem(KEY, JSON.stringify(O.st)); } catch (_) {}
  if (O.server === false) return; // no server route here: the browser copy stands in
  const body = {}; for (const k of Object.keys(patch)) if (k === "content_types" || k === "done") body[k] = O.st[k]; else if (k === "channels") body.channel_ids = O.st.channels;
  if (Object.keys(body).length) { try { return await api("/api/onboarding", { method: "PUT", body }); } catch (_) {} }
}
const STEPS = ["What you post", "Your channels", "Your brand", "Your AI", "Your feed"];
function shell(inner, step) {
  const top = step < 0 ? `<div class="onb-top"><span class="grow"></span><button class="link" type="button" data-onb="skip" data-testid="onboarding-skip">Skip setup</button></div>`
    : `<div class="onb-top"><div class="onb-prog"><span class="onb-count" data-testid="onboarding-progress">Step ${step + 1} of ${STEPS.length}<span class="muted"> · ${esc(STEPS[step])}</span></span>
        <div class="onb-steps" aria-hidden="true">${STEPS.map((_, i) => `<i class="${i < step ? "on" : i === step ? "on now" : ""}"></i>`).join("")}</div></div>
      <button class="link" type="button" data-onb="skip" data-testid="onboarding-skip">Skip setup</button></div>`;
  return `<div class="onb-card" role="dialog" aria-modal="true" aria-labelledby="onbH" data-testid="onboarding" data-step="${step}">${top}
    <div class="onb-pane ${O.dir < 0 ? "from-l" : "from-r"}">${inner}</div></div>`;
}
const chip = (id, label, on) => `<button type="button" data-v="${esc(id)}" aria-pressed="${on ? "true" : "false"}"><span class="onb-tick">${ICON.check}</span>${label}</button>`;
function nav(step, nextLabel = "Continue") {
  return `<div class="onb-act">${step > 0 ? `<button class="btn2 big" type="button" data-onb="back" data-testid="onboarding-back">Back</button>` : ""}<span class="grow"></span>
    <button class="btn big" type="button" data-onb="next" data-testid="onboarding-next">${nextLabel}</button></div>`;
}
const skel = (n, cls = "onb-sk") => `<div class="${cls}" aria-busy="true">${"<span></span>".repeat(n)}</div>`;
function draw() {
  const st = O.st, s = O.step;
  let inner = "";
  if (s < 0) inner = `<div class="onb-hello"><img class="onb-mark" src="/static/brand/hb-mark-96.png" alt="" width="56" height="56">
    <p class="onb-eyebrow">Welcome to HuhQueue</p><h2 id="onbH" class="onb-hero">Posts in your style,<br>ready when you are.</h2>
    <p class="lead">A minute of setup tunes your news, your brand and your agent. You can change any of it later.</p>
    <ul class="onb-why" data-testid="onboarding-why"><li>${ICON.news}<span><b>News that fits you.</b> Fresh stories on what you post about.</span></li>
      <li>${ICON.brush}<span><b>Your look on every post.</b> Logo and colours from your site.</span></li>
      <li>${ICON.shield}<span><b>You approve everything.</b> Nothing goes to Buffer without you.</span></li></ul>
    <div class="onb-act center"><button class="btn big" type="button" data-onb="next" data-testid="onboarding-start">Get started</button></div></div>`;
  if (s === 0) inner = `<h2 id="onbH">What do you post about?</h2><p class="lead">Pick a few. We follow sources for each one, so your News feed is ready the moment you finish.</p>
    <div class="onb-chips" data-testid="onboarding-types" role="group" aria-label="Content types">${TYPES.map(([id, l]) => chip(id, esc(l), st.content_types.includes(id))).join("")}</div>
    <p class="onb-note" id="onbPicked" data-testid="onboarding-picked" aria-live="polite"></p>${nav(s)}`;
  if (s === 1) {
    const ch = S.channels.filter((c) => !c.isDisconnected), pick = st.channels.length ? st.channels : ch.map((c) => c.id);
    inner = `<h2 id="onbH">Where should posts go?</h2><p class="lead">Your Buffer channels. Every new post starts with the ones you pick, and you can change it on any post.</p>
    <div class="onb-chips" data-testid="onboarding-channels" role="group" aria-label="Channels">${ch.map((c) => chip(c.id, `${netBadge(c.service)} ${esc(c.displayName || c.name)}`, pick.includes(c.id))).join("") || `<p class="muted">No channels yet. Add one in Buffer, then refresh from Settings.</p>`}</div>${nav(s)}`;
  }
  if (s === 2) {
    inner = `<h2 id="onbH">Make it look like you</h2><p class="lead">Give us your website and we bring in your logo and colours. Or upload a logo. Fine-tune it later under Brand.</p>
    <div class="onb-brand"><div class="field"><label class="label" for="onbSite">Website</label><div class="row"><input id="onbSite" data-testid="onboarding-site" placeholder="yourbrand.com" value="${esc(st.website || "")}" autocomplete="url"><button class="btn2" type="button" data-onb="import" data-testid="onboarding-import">Import</button></div></div>
      <div class="field"><span class="label">or upload a logo</span><input id="onbLogo" class="sr" type="file" data-testid="onboarding-logo" accept="image/png,image/jpeg,image/webp"><label class="btn2 onb-up" for="onbLogo">Choose a logo file</label></div></div>
    <div id="onbKit">${skel(1, "onb-sk kit")}</div>${nav(s)}`;
  }
  if (s === 3) {
    const url = window.hqMcpUrl ? hqMcpUrl() : location.origin + "/mcp";
    inner = `<h2 id="onbH">Bring your own brain</h2><p class="lead">HuhQueue's agent starts on Claude with included credit, then switches to NVIDIA when it runs out. Already pay for Claude or ChatGPT? Add HuhQueue there and keep going free: your model does the thinking, HuhQueue does the editing.</p>
    <div class="bsw-addr" data-testid="onboarding-mcp"><span class="label">MCP address</span><code>${esc(url)}</code><button class="btn2 sm" type="button" data-onb="copy" data-testid="onboarding-mcp-copy">Copy</button></div>
    <ol class="bsw-steps onb-ol"><li>Copy the address.</li><li>In Claude: Settings, Connectors, Add custom connector. In ChatGPT: Plugins, Add custom MCP server.</li><li>Sign in with Buffer when HuhQueue asks. Every post still waits for your approval.</li></ol>
    <p class="onb-note">Not now? It's under Settings, Integrations any time.</p>${nav(s)}`;
  }
  if (s === 4) {
    inner = `<h2 id="onbH">Your feed is ready</h2><p class="lead">Today's stories from sources picked for you. Make post opens the agent with the story; nothing reaches Buffer until you approve it.</p>
    <div class="onb-feed" id="onbFeed" data-testid="onboarding-recs">${skel(4, "onb-sk feed")}</div>
    <div class="onb-follow" id="onbFollow" data-testid="onboarding-feeds" hidden></div>
    <div class="onb-act"><button class="btn2 big" type="button" data-onb="back" data-testid="onboarding-back">Back</button><span class="grow"></span>
      <button class="link" type="button" data-onb="news" data-testid="onboarding-open-news">Open News</button>
      <button class="btn big" type="button" data-onb="next" data-testid="onboarding-next">Go to my Inbox</button></div>`;
  }
  O.el.innerHTML = shell(inner, s);
  favicons(O.el); wire();
  if (s === 0) picked0();
  if (s === 2) fillKit();
  if (s === 4) fillFeed();
  const h = O.el.querySelector("h2"); if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
}
function picked0() {
  const n = picked("[data-testid=onboarding-types]").length, note = O.el.querySelector("#onbPicked"), next = O.el.querySelector("[data-onb=next]");
  if (note) note.textContent = n ? `${n} picked. Your feed follows sources for ${n === 1 ? "it" : "each"}.` : "Nothing picked? You'll get the day's top tech news.";
  if (next) next.textContent = n ? "Continue" : "Continue with top tech news";
}
async function fillKit() {
  const box = O.el.querySelector("#onbKit"); let kit = null;
  try { kit = await api("/api/brand-studio").then((d) => d.kit); } catch (_) {}
  if (!box.isConnected) return;
  box.innerHTML = kit ? kitRow(kit) : "";
  const site = O.el.querySelector("#onbSite"); if (site && !site.value && kit && kit.website) site.value = kit.website;
}
async function fillFeed() {
  const box = O.el.querySelector("#onbFeed"), fol = O.el.querySelector("#onbFollow");
  let r = null, f = [];
  try { [r, f] = await Promise.all([api("/api/radar"), api("/api/onboarding").then((o) => o.following || []).catch(() => [])]); } catch (_) {}
  if (!box.isConnected) return;
  const items = ((r && r.stories) || []).slice(0, 4).map((s) => ({ id: s.url, title: s.title, url: s.url, image: s.image, source: s.source, why: s.why_for_you || "" }));
  box.innerHTML = items.length ? items.map((x, i) => recCard(x).replace('class="rec', `style="--i:${i}" class="deal rec`).replace('class="btn sm"', 'class="btn2 sm"')).join("")
    : `<div class="empty"><b>Stories are on their way</b><span>Your sources are being read. They show up in News within a minute.</span></div>`;
  if (f.length) {
    fol.hidden = false;
    fol.innerHTML = `<p class="onb-note">Following for you <span class="muted">· change them any time in News › Sources</span></p>
      <div class="onb-pills">${f.slice(0, 8).map((x) => `<span class="pill sm">${fav(hostOf(x.url))}${esc(x.label)}</span>`).join("")}${f.length > 8 ? `<span class="pill sm">+${f.length - 8} more</span>` : ""}</div>`;
  }
  favicons(O.el); wireMake(box);
  box.querySelectorAll("[data-make],[data-run]").forEach((b) => b.addEventListener("click", () => finish(true), { capture: true }));
}
const kitRow = (k) => `<div class="onb-kit" data-testid="onboarding-kit">${k.logo_url ? `<img src="${esc(k.logo_url)}" alt="">` : ""}<div><b>${esc(k.name || "Your brand")}</b><div class="muted sm">${k.handle ? "@" + esc(k.handle) : ""}</div></div>
  <span class="sw">${["primary", "accent", "text"].map((c) => `<i style="background:${esc((k.colors || {})[c] || "#888")}"></i>`).join("")}</span></div>`;
function picked(sel) { return [...O.el.querySelectorAll(sel + " [aria-pressed=true]")].map((b) => b.dataset.v); }
function go(step) { O.dir = step >= O.step ? 1 : -1; O.step = step; draw(); }
function wire() {
  O.el.querySelectorAll(".onb-chips [data-v]").forEach((b) => (b.onclick = () => {
    b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true");
    if (O.step === 0) picked0();
  }));
  const q = (a) => O.el.querySelector(`[data-onb=${a}]`);
  q("skip").onclick = async () => { await save({ skipped: true, step: Math.max(0, O.step) }); close(); toast("Setup skipped. Your News has today's top tech stories. Open setup again any time from Settings."); };
  if (q("back")) q("back").onclick = () => go(O.step - 1);
  q("next").onclick = async (ev) => {
    const btn = ev.currentTarget;
    if (O.step < 0) return go(0);
    if (O.step === 4) return finish(false);
    btn.disabled = true;
    try {
      if (O.step === 0) await save({ content_types: picked("[data-testid=onboarding-types]") });  // the server follows matching feeds now
      if (O.step === 1) await save({ channels: picked("[data-testid=onboarding-channels]") });
      if (O.step === 2) O.st.website = (O.el.querySelector("#onbSite")?.value || "").trim();
      await save({ step: O.step + 1 });
    } finally { btn.disabled = false; }
    go(O.step + 1);
  };
  const news = q("news");
  if (news) news.onclick = async () => { await finish(true); showTab("news", "feed"); };
  const cp = q("copy");
  if (cp) cp.onclick = async () => { const u = window.hqMcpUrl ? hqMcpUrl() : location.origin + "/mcp"; try { await navigator.clipboard.writeText(u); } catch (_) {} cp.textContent = "Copied"; toast("Copied"); };
  const imp = q("import");
  if (imp) imp.onclick = async () => {
    const url = O.el.querySelector("#onbSite").value.trim(); if (!url) return toast("Type your website first");
    imp.disabled = true; imp.innerHTML = `<span class="spin" aria-hidden="true"></span>Reading…`;
    O.el.querySelector("#onbKit").innerHTML = skel(1, "onb-sk kit");
    try {
      let r = null;
      if (O.server) { try { const o = await api("/api/onboarding", { method: "PUT", body: { website: url } }); r = o.brand || { sample: false }; if (r.note) toast(r.note, 6000); } catch (e) { if (e.status !== 404) throw e; } }
      if (!r) { r = await api("/api/brand/from-website", { method: "POST", body: { url } }); if (r.sample) toast(r.note, 6000); }
      const k = await api("/api/brand-studio").then((d) => d.kit); O.el.querySelector("#onbKit").innerHTML = kitRow(k); O.st.website = url; }
    catch (e) { toast(e.message, 5000); fillKit(); } finally { imp.disabled = false; imp.textContent = "Import"; }
  };
  const logo = O.el.querySelector("#onbLogo");
  if (logo) logo.onchange = async () => {
    const f = logo.files[0]; if (!f) return;
    try { const data = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(f); });
      const up = await api("/api/media", { method: "POST", body: { name: f.name, data } });
      const k = await api("/api/brand-studio", { method: "POST", body: { logo: up.token } }); O.el.querySelector("#onbKit").innerHTML = kitRow(k); toast("Logo saved"); }
    catch (e) { toast(e.message, 5000); }
  };
}
async function finish(toAgent) {
  await save({ done: true, skipped: false, step: STEPS.length - 1 });
  close();
  // the composers start from the channels picked here
  S.selected.clear(); defaultChannels().forEach((c) => S.selected.add(c.id));
  if (!toAgent) showTab("inbox");
}
function close() {
  if (!O.el || O.el.hidden) return;
  const el = O.el;
  document.removeEventListener("keydown", keys, true);
  el.classList.remove("in"); el.classList.add("out");
  clearTimeout(O.timer);
  O.timer = setTimeout(() => { el.hidden = true; el.classList.remove("out"); el.innerHTML = ""; }, still() ? 0 : 180);
  O.back?.focus?.();
  window.dispatchEvent(new CustomEvent("hq:interests"));
}
function keys(e) {
  if (!O.el || O.el.hidden) return;
  if (e.key === "Escape") { e.preventDefault(); O.el.querySelector("[data-onb=skip]")?.click(); return; }
  if (e.key === "Tab") {  // keep focus inside the dialog
    const f = [...O.el.querySelectorAll("button:not([disabled]),input,a[href],[tabindex='0']")].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  }
}
async function onbOpen(step, opts) {
  await onbState();
  if (!O.el) { O.el = document.createElement("div"); O.el.className = "onb"; O.el.id = "onb"; document.body.append(O.el); }
  clearTimeout(O.timer);
  O.back = document.activeElement; O.dir = 1;
  O.step = (opts && opts.intro) ? -1 : Number.isInteger(step) ? step : 0;
  O.el.hidden = false; O.el.classList.remove("out");
  requestAnimationFrame(() => O.el.classList.add("in"));
  document.addEventListener("keydown", keys, true);
  draw();
}
// after Connect Buffer: once, unless it was finished or skipped. Never over the sample's guided tour. A first visit opens
// on the welcome; a returning one picks up where it stopped.
async function onbMaybeOpen({ justConnected, demo }) {
  const st = await onbState();
  if (demo) return;
  if (!st.done && !st.skipped && (justConnected || !localStorage.getItem("hq_onb_seen"))) {
    try { localStorage.setItem("hq_onb_seen", "1"); } catch (_) {}
    onbOpen(st.step || 0, { intro: !st.step });
  }
}
Object.assign(window, { onbOpen, onbState, onbMaybeOpen });
})();
