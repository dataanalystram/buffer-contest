// Setup after Connect Buffer: what you post -> your channels -> your brand -> a tailored feed and posts to approve.
// Skippable at every step and re-openable from Settings. State lives on the server (GET/PUT /api/onboarding and
// GET /api/recommendations, docs/ONBOARDING.md); where that route isn't there yet, the browser keeps it, so the flow
// never blocks on the backend. "Skipped" is the browser's own note (the server only knows done or not).
"use strict";
(() => {
const KEY = "hq_onb";
// the server sends its own list (options.content_types); this is the same list for when it can't
let TYPES = [["ai_news", "AI news"], ["product_launches", "Product launches"], ["yc_startups", "YC startups"], ["tutorials", "Tutorials and how-tos"],
  ["dev_tools", "Developer tools"], ["memes", "Memes and culture"], ["india_tech", "India tech"], ["research", "Research papers"], ["funding", "Funding and deals"], ["open_source", "Open source"]];
// a starting feed per content type: public RSS only, nothing that needs a key
const FEEDS = {
  ai_news: [["Hacker News front page", "https://hnrss.org/frontpage"], ["The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"]],
  product_launches: [["Hacker News Show HN", "https://hnrss.org/show"]], dev_tools: [["Hacker News Show HN", "https://hnrss.org/show"]],
  tutorials: [["Hacker News best", "https://hnrss.org/best"]], funding: [["TechCrunch", "https://techcrunch.com/feed/"]],
  research: [["Hugging Face blog", "https://huggingface.co/blog/feed.xml"]], open_source: [["Hugging Face blog", "https://huggingface.co/blog/feed.xml"]],
};
const O = { st: null, step: 0, el: null, back: null };
const local = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) { return {}; } };
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
  if (Object.keys(body).length) { try { await api("/api/onboarding", { method: "PUT", body }); } catch (_) {} }
}
const STEPS = ["What you post", "Your channels", "Your brand", "Your feed"];
function shell(inner, step) {
  return `<div class="onb-card" role="dialog" aria-modal="true" aria-labelledby="onbH" data-testid="onboarding" data-step="${step}">
    <div class="onb-top"><div class="onb-steps" aria-label="Step ${step + 1} of ${STEPS.length}">${STEPS.map((_, i) => `<i class="${i <= step ? "on" : ""}"></i>`).join("")}</div>
      <button class="link" type="button" data-onb="skip" data-testid="onboarding-skip">Skip setup</button></div>${inner}</div>`;
}
const chip = (id, label, on) => `<button type="button" data-v="${esc(id)}" aria-pressed="${on ? "true" : "false"}">${label}</button>`;
function nav(step, nextLabel = "Continue") {
  return `<div class="onb-act">${step ? `<button class="btn2" type="button" data-onb="back" data-testid="onboarding-back">Back</button>` : ""}<span class="grow"></span>
    <button class="btn" type="button" data-onb="next" data-testid="onboarding-next">${nextLabel}</button></div>`;
}
async function draw() {
  const st = O.st, s = O.step;
  let inner = "";
  if (s === 0) inner = `<h2 id="onbH">What do you post?</h2><p class="lead">Pick everything that fits. It tunes your news feed and the posts we suggest.</p>
    <div class="onb-chips" data-testid="onboarding-types" role="group" aria-label="Content types">${TYPES.map(([id, l]) => chip(id, esc(l), st.content_types.includes(id))).join("")}</div>${nav(s)}`;
  if (s === 1) {
    const ch = S.channels.filter((c) => !c.isDisconnected), pick = st.channels.length ? st.channels : ch.map((c) => c.id);
    inner = `<h2 id="onbH">Where should posts go?</h2><p class="lead">These are your Buffer channels. Every composer starts with the ones you pick; you can change it on any post.</p>
    <div class="onb-chips" data-testid="onboarding-channels" role="group" aria-label="Channels">${ch.map((c) => chip(c.id, `${netBadge(c.service)} ${esc(c.displayName || c.name)}`, pick.includes(c.id))).join("") || `<p class="muted">No channels yet. Add one in Buffer, then refresh from Settings.</p>`}</div>${nav(s)}`;
  }
  if (s === 2) {
    let kit = null; try { kit = await api("/api/brand-studio").then((d) => d.kit); } catch (_) {}
    inner = `<h2 id="onbH">Make it look like you</h2><p class="lead">Give us your website and we pull your logo and colours, or upload a logo. Fine-tune it later under Brand.</p>
    <div class="onb-brand"><div class="field"><label class="label" for="onbSite">Website</label><div class="row"><input id="onbSite" data-testid="onboarding-site" placeholder="yourbrand.com" value="${esc(st.website || (kit && kit.website) || "")}"><button class="btn2" type="button" data-onb="import" data-testid="onboarding-import">Import</button></div></div>
      <div class="field"><label class="label" for="onbLogo">or upload a logo</label><input id="onbLogo" type="file" data-testid="onboarding-logo" accept="image/png,image/jpeg,image/webp"></div></div>
    <div id="onbKit">${kit ? kitRow(kit) : ""}</div>${nav(s)}`;
  }
  if (s === 3) {
    const want = [...new Set(st.content_types.flatMap((t) => FEEDS[t] || []).map((f) => f[1]))].map((u) => Object.values(FEEDS).flat().find((f) => f[1] === u));
    const recs = (await getRecs()).items.slice(0, 4);
    inner = `<h2 id="onbH">Your feed is ready</h2><p class="lead">Posts worth making today, and a few sources to follow. Make post opens the agent with the story; nothing reaches Buffer until you approve it.</p>
    <div class="onb-feed" data-testid="onboarding-recs">${recs.map(recCard).join("") || `<p class="muted">Follow a source and stories land here.</p>`}</div>
    ${want.length ? `<h3 class="h3" style="margin-top:20px">Follow for ${esc(st.content_types.map((t) => (TYPES.find((x) => x[0] === t) || [, t])[1].toLowerCase()).slice(0, 2).join(" and "))}</h3>
      <div class="onb-chips" data-testid="onboarding-feeds">${want.map(([l, u]) => `<button type="button" data-follow="${esc(u)}" aria-pressed="false">${fav(hostOf(u))} ${esc(l)}</button>`).join("")}</div>` : ""}
    ${nav(s, "Go to my Inbox")}`;
  }
  O.el.innerHTML = shell(inner, s);
  favicons(O.el); wire();
  (O.el.querySelector("h2") || O.el).setAttribute("tabindex", "-1"); O.el.querySelector("h2")?.focus();
}
const kitRow = (k) => `<div class="onb-kit" data-testid="onboarding-kit">${k.logo_url ? `<img src="${esc(k.logo_url)}" alt="">` : ""}<div><b>${esc(k.name || "Your brand")}</b><div class="muted sm">${k.handle ? "@" + esc(k.handle) : ""}</div></div>
  <span class="sw">${["primary", "accent", "text"].map((c) => `<i style="background:${esc((k.colors || {})[c] || "#888")}"></i>`).join("")}</span></div>`;
function picked(sel) { return [...O.el.querySelectorAll(sel + " [aria-pressed=true]")].map((b) => b.dataset.v); }
function wire() {
  O.el.querySelectorAll(".onb-chips [data-v]").forEach((b) => (b.onclick = () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true")));
  O.el.querySelectorAll("[data-follow]").forEach((b) => (b.onclick = async () => {
    if (b.getAttribute("aria-pressed") === "true") return;
    b.disabled = true;
    try { await api("/api/radar/feeds", { method: "POST", body: { url: b.dataset.follow } }); b.setAttribute("aria-pressed", "true"); toast("Following it. New stories land in News."); }
    catch (e) { toast(e.message, 5000); } finally { b.disabled = false; }
  }));
  wireMake(O.el);
  O.el.querySelectorAll("[data-make],[data-run]").forEach((b) => b.addEventListener("click", () => finish(true), { capture: true }));
  const q = (a) => O.el.querySelector(`[data-onb=${a}]`);
  q("skip").onclick = async () => { await save({ skipped: true, step: O.step }); close(); toast("Setup skipped. Open it again any time from Settings."); };
  if (q("back")) q("back").onclick = () => { O.step--; draw(); };
  q("next").onclick = async () => {
    if (O.step === 0) await save({ content_types: picked("[data-testid=onboarding-types]") });
    if (O.step === 1) { await save({ channels: picked("[data-testid=onboarding-channels]") }); }
    if (O.step === 2) O.st.website = (O.el.querySelector("#onbSite")?.value || "").trim();
    if (O.step === 3) return finish(false);
    O.step++; await save({ step: O.step }); draw();
  };
  const imp = q("import");
  if (imp) imp.onclick = async () => {
    const url = O.el.querySelector("#onbSite").value.trim(); if (!url) return toast("Type your website first");
    imp.disabled = true; imp.innerHTML = `<span class="spin" aria-hidden="true"></span>Reading…`;
    try {
      let r = null;
      if (O.server) { try { const o = await api("/api/onboarding", { method: "PUT", body: { website: url } }); r = o.brand || { sample: false }; if (r.note) toast(r.note, 6000); } catch (e) { if (e.status !== 404) throw e; } }
      if (!r) { r = await api("/api/brand/from-website", { method: "POST", body: { url } }); if (r.sample) toast(r.note, 6000); }
      const k = await api("/api/brand-studio").then((d) => d.kit); O.el.querySelector("#onbKit").innerHTML = kitRow(k); O.st.website = url; }
    catch (e) { toast(e.message, 5000); } finally { imp.disabled = false; imp.textContent = "Import"; }
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
function close() { if (!O.el) return; O.el.hidden = true; O.el.innerHTML = ""; document.removeEventListener("keydown", keys, true); O.back?.focus?.(); }
function keys(e) { if (e.key === "Escape" && O.el && !O.el.hidden) { e.preventDefault(); O.el.querySelector("[data-onb=skip]")?.click(); } }
async function onbOpen(step) {
  await onbState();
  if (!O.el) { O.el = document.createElement("div"); O.el.className = "onb"; O.el.id = "onb"; document.body.append(O.el); }
  O.back = document.activeElement; O.step = Number.isInteger(step) ? step : 0; O.el.hidden = false;
  document.addEventListener("keydown", keys, true);
  draw();
}
// after Connect Buffer: once, unless it was finished or skipped. Never over the sample's guided tour.
async function onbMaybeOpen({ justConnected, demo }) {
  const st = await onbState();
  if (demo) return;
  if (!st.done && !st.skipped && (justConnected || !localStorage.getItem("hq_onb_seen"))) { try { localStorage.setItem("hq_onb_seen", "1"); } catch (_) {} onbOpen(st.step || 0); }
}
Object.assign(window, { onbOpen, onbState, onbMaybeOpen });
})();
