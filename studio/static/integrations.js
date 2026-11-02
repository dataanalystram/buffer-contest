// HuhQueue front end: Settings -> Integrations. One card per key-based integration (Product Hunt, X, YouTube Data API):
// what it unlocks, how to get a key, a password field, Test (live check, status only), Save, Remove and a masked status.
// The server never sends a key back: the card only ever shows "…abcd".
"use strict";
(function () {
  const I = { data: null };
  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  };
  function status(c) {
    if (!c.connected) {
      const app = c.app || {};
      return app.label ? `<span class="integ-st ok app" data-testid="integ-status">${esc(app.label)}</span>` : `<span class="integ-st none" data-testid="integ-status">Not connected</span>`;
    }
    const tested = c.tested ? ` · ${c.tested_ok ? "tested" : "test failed"} ${ago(c.tested)}` : " · not tested yet";
    return `<span class="integ-st ${c.tested && !c.tested_ok ? "bad" : "ok"}" data-testid="integ-status">Your key · ${esc(c.hint)}${esc(tested)}</span>`;
  }
  function card(c) {
    return `<section class="panel integ-card" id="integ-${c.key}" data-key="${c.key}" data-testid="integ-card-${c.key}" aria-labelledby="integH-${c.key}">
      <div class="integ-top"><h3 class="card-title" id="integH-${c.key}">${esc(c.name)}</h3>${status(c)}</div>
      ${c.app && c.app.detail ? `<p class="sm integ-app" data-testid="integ-app-${c.key}">${esc(c.app.detail)}</p>` : ""}
      <p class="muted sm">${esc(c.unlocks)}</p>
      <details class="integ-how"${c.connected || (c.app && c.app.label) ? "" : " open"}><summary>${c.app && c.app.label ? "Optional: use your own key" : "Get a key"}</summary>
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
          ${c.oauth ? `<a class="link sm" href="/integrations/producthunt/connect" data-testid="integ-oauth-${c.key}">Or connect with Product Hunt</a>` : ""}
        </div>
        <p class="integ-msg sm" role="status" data-testid="integ-msg-${c.key}">${c.connected && c.test_message ? esc(c.test_message) : ""}</p>
      </form></section>`;
  }
  function draw(d) {
    I.data = d;
    const box = document.getElementById("integCards"); if (!box) return;
    box.innerHTML = d.integrations.map(card).join("");
    const rd = document.getElementById("integRedirects");
    if (rd) rd.innerHTML = `<h3 class="card-title">Callback and redirect URLs</h3><p class="muted sm">For the developer apps behind HuhQueue. Paste these exactly where each service asks.</p>
      <ul class="integ-urls">${(d.redirects || []).map((r) => `<li data-testid="integ-url"><span class="sm"><b>${esc(r.name)}</b></span><code class="integ-code">${esc(r.url)}</code><button class="btn2 sm" type="button" data-copy="${esc(r.url)}" data-testid="integ-copy">Copy</button></li>`).join("")}</ul>`;
    document.querySelectorAll("#integRedirects [data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"), () => toast("Copy failed; select the text instead"))));
    box.querySelectorAll(".integ-form").forEach(wire);
    box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"), () => {})));
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
    try { draw(await api("/api/integrations")); } catch (e) { const box = document.getElementById("integCards"); if (box) box.innerHTML = `<p class="st err">${esc(e.message)}</p>`; }
  }
  // open Settings scrolled to one card: data-go="settings:integ-ph" (from News -> Sources) or /?tab=settings&sub=integrations
  window.integOpen = function (id) {
    showTab("settings");
    setTimeout(() => { const el = document.getElementById(id || "integrations"); if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); el.classList.add("integ-flash"); setTimeout(() => el.classList.remove("integ-flash"), 1600); } }, 350);
  };
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-integ]"); if (!g) return;
    e.preventDefault(); e.stopPropagation(); window.integOpen(g.dataset.integ);
  }, true);
  const ai = document.getElementById("integAiLink");
  if (ai) ai.onclick = () => document.getElementById("aiH")?.scrollIntoView({ behavior: "smooth", block: "start" });
  window.HS_TABS = Object.assign(window.HS_TABS || {}, { integrations: load });
  const p = new URLSearchParams(location.search);
  if (p.get("tab") === "settings" && /^(integrations|integ-[a-z]+)$/.test(p.get("sub") || "")) {
    const want = p.get("sub"); setTimeout(() => window.integOpen(want), 1200);
  }
})();
