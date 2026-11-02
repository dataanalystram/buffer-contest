// HuhQueue: "Create your brand kit", the builder on Brand > Kit (owner, 2 Nov 2026).
// Build a kit from scratch or from a website: name, handle, logo upload, colours (pickers + a palette read from the logo),
// heading and body fonts, caption style and the end card. As you edit, the whole gallery redraws on the server: every
// theme in the Video style gallery, the carousel cover, the thumbnail and the end card. Save makes it the ACTIVE kit,
// which every template renders from. Several kits sit in the bar on top; one click switches the active kit and the
// gallery restyles. The sample workspace opens with the "Nothing" sample brand (not affiliated) filled in, next to
// HuhByte AI; Linear and Notion can be added from the bar; "Start your own" clears the form.
// Every button is one REST call that is also an MCP tool (list_brand_kits, create_brand_kit, update_brand_kit,
// set_active_brand_kit, preview_brand_kit). Own scope: the page's scripts share one global scope.
"use strict";
(() => {
  const K = { kits: [], active: null, id: null, form: null, logo: "", logoUrl: "", meta: null, seq: 0, t: null, dirty: false,
              busy: false, loaded: false, opened: 0 };
  const COLS = [["background", "Background"], ["primary", "Primary"], ["accent", "Accent"], ["text", "Text"]];
  const CAPS = { opus: "Opus", news: "News", minimal: "Minimal", hype: "Hype" };
  const BLANK = () => ({ name: "", handle: "", colors: { background: "#111114", primary: "#5B8DEF", accent: "#9AA4B2", text: "#FFFFFF" },
                         head_font: "inter", body_font: "inter", caption_style: "opus", end_text: "", end_cta: "", website: "", voice: "" });

  const HTML = `
  <section class="panel bk" id="bkBuilder" data-testid="bk-builder" aria-labelledby="bkH">
    <header class="bk-head">
      <div><h2 id="bkH" class="h2">Create your brand kit</h2>
        <p class="muted sm">Your logo, colours, fonts, captions and end card. Every theme, carousel, thumbnail and end card below redraws as you edit; <b>Save</b> makes it the kit every template uses.</p></div>
      <button class="btn2" type="button" id="bkNew" data-testid="bk-start-own">Start your own</button>
    </header>
    <div class="bk-bar" id="bkBar" role="radiogroup" aria-label="Your brand kits" data-testid="bk-kits"></div>
    <form class="bk-url" id="bkUrlForm" autocomplete="off" data-testid="bk-url-form">
      <label class="sr" for="bkUrl">Start from a website</label>
      <input id="bkUrl" data-testid="bk-url" inputmode="url" maxlength="300" placeholder="Start from a website: yourbrand.com">
      <button class="btn2" type="submit" id="bkUrlGo" data-testid="bk-url-go">Fill from website</button>
    </form>
    <p class="bk-label" id="bkLabel" data-testid="bk-label" hidden></p>
    <div class="bk-grid">
      <form class="bk-form" id="bkForm" data-testid="bk-form" autocomplete="off">
        <div class="bk-row2">
          <div class="field"><label class="label" for="bkName">Brand name</label><input id="bkName" data-f="name" data-testid="bk-name" maxlength="60" placeholder="Your brand"></div>
          <div class="field"><label class="label" for="bkHandle">Handle</label><div class="bk-at"><span aria-hidden="true">@</span><input id="bkHandle" data-f="handle" data-testid="bk-handle" maxlength="40" placeholder="yourbrand"></div></div>
        </div>
        <div class="field"><span class="label" id="bkLogoL">Logo</span>
          <div class="bk-logo-row">
            <span class="bk-logo" id="bkLogoBox" data-testid="bk-logo-box"><img id="bkLogoImg" alt="" hidden><i id="bkLogoNone">No logo</i></span>
            <label class="btn2 sm bk-up">Upload logo<input type="file" id="bkLogo" data-testid="bk-logo" accept="image/png,image/jpeg,image/webp" aria-labelledby="bkLogoL" hidden></label>
            <button class="btn2 sm" type="button" id="bkPal" data-testid="bk-palette">Colours from logo</button>
            <button class="link sm" type="button" id="bkLogoClear" data-testid="bk-logo-clear">Remove</button>
          </div></div>
        <div class="field"><span class="label">Colours</span>
          <div class="bk-cols" id="bkCols">${COLS.map(([k, l]) => `
            <label class="bk-col"><input type="color" data-col="${k}" data-testid="bk-color-${k}" aria-label="${l} colour">
              <span><b>${l}</b><input class="bk-hex" data-hex="${k}" data-testid="bk-hex-${k}" maxlength="7" spellcheck="false" aria-label="${l} hex"></span></label>`).join("")}
          </div>
          <p class="muted xs bk-hint">Background is the ground of every card, Primary the highlight, Accent the second colour, Text sits on the background.</p></div>
        <div class="bk-row2">
          <div class="field"><label class="label" for="bkHead">Heading font</label><select id="bkHead" data-f="head_font" data-testid="bk-head-font"></select></div>
          <div class="field"><label class="label" for="bkBody">Body font</label><select id="bkBody" data-f="body_font" data-testid="bk-body-font"></select></div>
        </div>
        <div class="field"><span class="label" id="bkCapL">Caption style</span>
          <div class="seg bk-caps" role="radiogroup" aria-labelledby="bkCapL" id="bkCaps" data-testid="bk-caption-style">${Object.entries(CAPS).map(([v, l]) =>
            `<button type="button" role="radio" data-cap="${v}" data-testid="bk-cap-${v}" aria-checked="false">${l}</button>`).join("")}</div>
          <p class="muted xs" id="bkCapAbout"></p></div>
        <div class="field"><span class="label">End card</span>
          <div class="bk-row2">
            <input id="bkEndText" data-f="end_text" data-testid="bk-end-text" maxlength="70" placeholder="One line, e.g. New drops every Friday" aria-label="End card text">
            <input id="bkEndCta" data-f="end_cta" data-testid="bk-end-cta" maxlength="28" placeholder="Follow for more" aria-label="End card call to action">
          </div></div>
        <div class="bk-save">
          <button class="btn accent" type="submit" id="bkSave" data-testid="bk-save">Save and use this kit</button>
          <span class="muted sm" id="bkState" role="status" aria-live="polite" data-testid="bk-state"></span>
        </div>
      </form>
      <div class="bk-prev" data-testid="bk-preview">
        <div class="bk-prev-head"><h3 id="bkPrevH">Live preview</h3><span class="muted xs" id="bkMs" data-testid="bk-ms" aria-live="polite"></span></div>
        <div class="bk-kitcards" id="bkKitCards" data-testid="bk-kit-cards" aria-busy="true"></div>
        <div class="bk-prev-head"><h3>Video style gallery</h3><span class="muted xs" id="bkThemeN" data-testid="bk-theme-count"></span></div>
        <div class="bk-themes" id="bkThemes" data-testid="bk-themes" aria-busy="true"></div>
      </div>
    </div>
  </section>`;

  // ------------------------------------------------------------ kit <-> form
  const fromKit = (k) => {
    const c = k.builder_colors || {}, ec = k.end_card || {};
    return { name: k.name || "", handle: k.handle || "", website: k.website || "", voice: k.voice || "",
             colors: { background: c.background || "#111114", primary: c.primary || "#5B8DEF", accent: c.accent || c.primary || "#9AA4B2", text: c.text || "#FFFFFF" },
             head_font: k.font || "inter", body_font: k.body_font || "inter", caption_style: k.caption_style || "opus",
             end_text: ec.line || "", end_cta: ec.cta || "" };
  };
  const body = () => ({ ...K.form, logo: K.logo });
  const hex = (v) => (/^#?[0-9a-f]{6}$/i.test(v || "") ? "#" + v.replace("#", "").toUpperCase() : null);
  const slug = (s) => String(s || "kit").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const state = (t) => { const el = $("#bkState"); if (el) el.textContent = t; };

  function fill() {
    const f = K.form;
    document.querySelectorAll("#bkForm [data-f]").forEach((el) => (el.value = f[el.dataset.f] ?? ""));
    COLS.forEach(([k]) => { const v = f.colors[k] || "#888888"; $(`[data-col=${k}]`).value = v.toLowerCase(); $(`[data-hex=${k}]`).value = v; });
    document.querySelectorAll("#bkCaps [data-cap]").forEach((b) => { const on = b.dataset.cap === f.caption_style; b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
    $("#bkCapAbout").textContent = (K.meta?.caption_styles || {})[f.caption_style] || "";
    const img = $("#bkLogoImg");
    img.hidden = !K.logoUrl; $("#bkLogoNone").hidden = !!K.logoUrl;
    if (K.logoUrl) { img.src = K.logoUrl; img.alt = `${f.name || "Brand"} logo`; }
    $("#bkLogoBox").style.background = f.colors.background;
    $("#bkPal").disabled = !K.logo; $("#bkLogoClear").hidden = !K.logo;
    const kit = K.kits.find((x) => x.id === K.id);
    const lab = $("#bkLabel");
    lab.hidden = !(kit && kit.kit.label);
    lab.textContent = kit && kit.kit.label ? `${kit.name}: ${kit.kit.label}` : "";
    $("#bkSave").textContent = kit && kit.active ? "Save changes" : "Save and use this kit";
  }
  function bar() {
    const have = new Set(K.kits.map((k) => String(k.kit.logo || "").replace("sample:", "")));
    const chip = (k) => `<button type="button" role="radio" class="bk-chip${k.id === K.id ? " on" : ""}" data-kit="${k.id}" data-testid="bk-kit-${slug(k.name)}"
        aria-checked="${k.id === K.id}" style="--g:${esc(k.kit.builder_colors.background)};--a:${esc(k.kit.builder_colors.primary)}">
        <span class="bk-chip-logo">${k.logo_url ? `<img src="${esc(k.logo_url)}" alt="">` : `<b>${esc((k.name || "?").slice(0, 1))}</b>`}</span>
        <span class="bk-chip-t"><b>${esc(k.name)}</b>${k.active ? `<small class="bk-on" data-testid="bk-active-badge">Active</small>` : ""}${k.kit.label ? `<small>${esc(k.kit.label)}</small>` : ""}</span></button>`;
    const extra = ((K.meta && K.meta.samples) || []).filter((s) => !have.has(s.id)).map((s) =>
      `<button type="button" class="bk-chip add" data-sample="${s.id}" data-testid="bk-add-${s.id}" title="${esc(s.label)}"><span class="bk-chip-t"><b>+ ${esc(s.name)}</b><small>Sample brand</small></span></button>`).join("");
    const draft = K.id === null ? `<span class="bk-chip on draft" data-testid="bk-kit-draft"><span class="bk-chip-t"><b>${esc(K.form.name || "New kit")}</b><small>Not saved yet</small></span></span>` : "";
    $("#bkBar").innerHTML = K.kits.map(chip).join("") + draft + extra;
    $("#bkBar").querySelectorAll("[data-kit]").forEach((b) => (b.onclick = () => pick(+b.dataset.kit, true)));
    $("#bkBar").querySelectorAll("[data-sample]").forEach((b) => (b.onclick = () => addSample(b.dataset.sample)));
  }

  // ------------------------------------------------------------ the live gallery
  function redraw(now) { clearTimeout(K.t); K.t = setTimeout(draw, now ? 0 : 450); }
  async function draw() {
    const seq = ++K.seq, t0 = performance.now();
    const kc = $("#bkKitCards"), th = $("#bkThemes");
    kc.setAttribute("aria-busy", "true"); th.setAttribute("aria-busy", "true");
    kc.classList.add("stale"); th.classList.add("stale");
    const kit = body();
    try {
      const a = await api("/api/brand-kits/preview", { method: "POST", body: { kit, logo: K.logo, part: "kit", width: 420 } });
      if (seq !== K.seq) return;
      kc.innerHTML = a.cards.map(card).join(""); kc.classList.remove("stale"); kc.setAttribute("aria-busy", "false");
      const b = await api("/api/brand-kits/preview", { method: "POST", body: { kit, logo: K.logo, part: "themes", width: 240 } });
      if (seq !== K.seq) return;
      th.innerHTML = b.cards.map(card).join(""); th.classList.remove("stale"); th.setAttribute("aria-busy", "false");
      $("#bkThemeN").textContent = `${b.cards.length} themes in ${kit.name || "this kit"}`;
      $("#bkMs").textContent = `Redrawn in ${((performance.now() - t0) / 1000).toFixed(1)} s${a.cached && b.cached ? " (cached)" : ""}`;
    } catch (e) {
      if (seq !== K.seq) return;
      $("#bkMs").textContent = e.message;
      kc.classList.remove("stale"); th.classList.remove("stale");
    }
  }
  const card = (c) => c.url
    ? `<figure class="bk-card ${c.group === "kit" ? "k-" + c.id : "theme"}" data-testid="bk-card-${c.id}" data-card="${c.id}"><img src="${c.url}" alt="${esc(c.label)} in ${esc(K.form.name || "your kit")}"><figcaption>${esc(c.label)}</figcaption></figure>`
    : `<figure class="bk-card theme bad" data-testid="bk-card-${c.id}"><span>Couldn't draw</span><figcaption>${esc(c.label)}</figcaption></figure>`;

  // ------------------------------------------------------------ actions
  async function load(pickId) {
    const r = await api("/api/brand-kits");
    K.meta = r; K.metaAt = Date.now(); K.kits = r.kits; K.active = r.active_id;
    if (!K.loaded) {
      K.loaded = true;
      $("#bkHead").innerHTML = Object.entries(r.fonts || {}).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
      $("#bkBody").innerHTML = Object.entries(r.body_fonts || { inter: "Inter" }).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
    }
    const id = pickId ?? K.id ?? r.active_id ?? (r.kits[0] || {}).id ?? null;
    if (id != null && r.kits.some((k) => k.id === id)) pick(id, false);
    else { if (!K.form) { K.form = BLANK(); K.id = null; } bar(); fill(); redraw(true); }
    settingsCard();
  }
  function pick(id, activate) {
    const k = K.kits.find((x) => x.id === id); if (!k) return;
    K.id = id; K.form = fromKit(k.kit); K.logo = k.kit.logo || ""; K.logoUrl = k.logo_url || ""; K.dirty = false;
    bar(); fill(); redraw(true); state("");
    if (activate && !k.active) setActive(id);
  }
  async function setActive(id) {
    try {
      await api(`/api/brand-kits/${id}/activate`, { method: "POST", body: {} });
      K.kits.forEach((k) => (k.active = k.id === id)); K.active = id; bar(); fill();
      const k = K.kits.find((x) => x.id === id);
      state(`${k.name} is the active kit. Every template and theme now renders in it.`);
      refreshOthers();
    } catch (e) { toast(e.message); }
  }
  function refreshOthers() {
    window.dispatchEvent(new CustomEvent("hq:brand-kit", { detail: { id: K.active } }));
    refreshLegacy(); settingsCard();
  }
  async function addSample(sid) {
    try {
      const r = await api("/api/brand-kits", { method: "POST", body: { sample: sid } });
      await load(r.id); await setActive(r.id);
    } catch (e) { toast(e.message); }
  }
  function startOwn() {
    K.id = null; K.form = BLANK(); K.logo = ""; K.logoUrl = ""; K.dirty = false;
    bar(); fill(); redraw(true); state("A blank kit. Name it, add your logo and colours, then save.");
    $("#bkName").focus();
  }
  async function save(e) {
    if (e) e.preventDefault();
    if (!K.form.name.trim()) { state("Give your brand a name first."); $("#bkName").focus(); return; }
    if (K.busy) return; K.busy = true; $("#bkSave").disabled = true;
    try {
      const r = K.id === null
        ? await api("/api/brand-kits", { method: "POST", body: { ...body(), activate: true } })
        : await api(`/api/brand-kits/${K.id}`, { method: "PATCH", body: { ...body(), activate: true } });
      K.dirty = false;
      await load(r.id);
      state(`Saved. ${r.name} is the active kit: every template, theme, carousel, thumbnail and end card uses it.`);
      toast("Brand kit saved"); refreshOthers();
    } catch (err) { state(err.message); }
    finally { K.busy = false; $("#bkSave").disabled = false; }
  }
  async function upload(file) {
    const data = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(file); });
    return api("/api/media", { method: "POST", body: { name: file.name, data } });
  }
  async function palette() {
    if (!K.logo) return;
    try {
      const p = await api("/api/brand-kits/palette", { method: "POST", body: { logo: K.logo } });
      K.form.colors = { background: p.background, primary: p.primary, accent: p.accent, text: p.text };
      fill(); edited(); state("Colours picked from your logo. Adjust any of them.");
    } catch (e) { state(e.message); }
  }
  async function fromUrl(e) {
    e.preventDefault();
    const url = $("#bkUrl").value.trim(); if (!url) return;
    $("#bkUrlGo").disabled = true; state(`Reading ${url}: logo, colours, fonts and voice…`);
    try {
      const r = await api("/api/brand/extract", { method: "POST", body: { url } });
      const d = r.draft || {}, c = d.colors || {}, ec = d.end_card || {};
      K.id = null;
      K.form = { name: d.name || "", handle: d.handle || "", website: d.website || url, voice: d.voice || "",
                 colors: { background: c.primary || "#111114", primary: c.accent || "#5B8DEF", accent: c.secondary || c.accent || "#9AA4B2", text: c.text || "#FFFFFF" },
                 head_font: d.font || "inter", body_font: d.body_font || "inter", caption_style: "opus", end_text: ec.line || "", end_cta: ec.cta || "" };
      K.logo = r.logo || ""; K.logoUrl = r.logo_url || "";
      bar(); fill(); redraw(true);
      state(`${r.source === "sample" ? "Filled in from the cached sample of " : "Read "}${url}. ${r.label ? r.label + ". " : ""}Check it, then save.`);
    } catch (err) { state(err.message); }
    finally { $("#bkUrlGo").disabled = false; }
  }
  function edited() {
    K.dirty = true;
    if (K.id === null) bar();
    state(K.id === null ? "Not saved yet." : "Unsaved changes. Save to use them everywhere.");
    redraw();
  }

  // ------------------------------------------------------------ Settings > Brand kit: the active kit at a glance
  async function settingsCard() {
    const el = document.getElementById("setBrandKit"); if (!el) return;
    try {
      const r = K.meta && Date.now() - (K.metaAt || 0) < 1500 ? K.meta : await api("/api/brand-kits");
      const k = r.kits.find((x) => x.active);
      if (!k) { el.innerHTML = `<p class="muted sm">No brand kit yet. Create one and every template uses it.</p>`; return; }
      const c = k.kit.builder_colors || {};
      el.innerHTML = `<div class="set-bk"><span class="bk-chip-logo" style="--g:${esc(c.background)};--a:${esc(c.primary)}">${k.logo_url ? `<img src="${esc(k.logo_url)}" alt="${esc(k.name)} logo">` : `<b>${esc(k.name.slice(0, 1))}</b>`}</span>
        <span><b data-testid="settings-brand-name">${esc(k.name)}</b> <small class="muted">active${k.kit.label ? " · " + esc(k.kit.label) : ""}</small><br>
        <span class="set-bk-sw">${["background", "primary", "accent", "text"].map((x) => `<i style="background:${esc(c[x])}" title="${x} ${esc(c[x])}"></i>`).join("")}</span>
        <small class="muted">${esc((r.fonts || {})[k.kit.font] || k.kit.font)} · ${esc(k.kit.caption_style || "opus")} captions · ${r.kits.length} kit${r.kits.length === 1 ? "" : "s"}</small></span></div>`;
    } catch (_) { /* the card keeps its intro line */ }
  }

  // ------------------------------------------------------------ wiring
  function wire() {
    $("#bkNew").onclick = startOwn;
    $("#bkForm").onsubmit = save;
    $("#bkUrlForm").onsubmit = fromUrl;
    $("#bkPal").onclick = palette;
    $("#bkLogoClear").onclick = () => { K.logo = ""; K.logoUrl = ""; fill(); edited(); };
    document.querySelectorAll("#bkForm [data-f]").forEach((el) => (el.oninput = () => { K.form[el.dataset.f] = el.value; edited(); }));
    document.querySelectorAll("#bkForm [data-col]").forEach((el) => (el.oninput = () => {
      const v = el.value.toUpperCase(); K.form.colors[el.dataset.col] = v; $(`[data-hex=${el.dataset.col}]`).value = v;
      if (el.dataset.col === "background") $("#bkLogoBox").style.background = v;
      edited(); }));
    document.querySelectorAll("#bkForm [data-hex]").forEach((el) => (el.onchange = () => {
      const v = hex(el.value); if (!v) { el.value = K.form.colors[el.dataset.hex]; return; }
      K.form.colors[el.dataset.hex] = v; fill(); edited(); }));
    document.querySelectorAll("#bkCaps [data-cap]").forEach((b) => (b.onclick = () => { K.form.caption_style = b.dataset.cap; fill(); edited(); }));
    $("#bkCaps").addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const bs = [...document.querySelectorAll("#bkCaps [data-cap]")], i = bs.findIndex((b) => b.classList.contains("on"));
      const n = bs[(i + (e.key === "ArrowRight" ? 1 : bs.length - 1)) % bs.length]; n.click(); n.focus(); e.preventDefault();
    });
    $("#bkLogo").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      state("Uploading the logo…");
      try { const up = await upload(f); K.logo = up.token; K.logoUrl = up.url; fill(); await palette(); }
      catch (err) { state(err.message); }
      e.target.value = "";
    };
  }
  function mount() {
    const host = document.getElementById("bkMount");
    if (!host || document.getElementById("bkBuilder")) return false;
    host.innerHTML = HTML; wire();
    return true;
  }
  function open() {
    if (!document.getElementById("bkBuilder") && !mount()) return;
    if (Date.now() - K.opened < 800) return;  // showTab and the boot both ask; one load is enough
    K.opened = Date.now();
    load().catch((e) => { $("#bkMs").textContent = e.message; });
  }
  // Brand > Kit opens the builder. brandstudio.js sets HS_SUBS["brand:kit"] / HS_TABS.brand when it mounts, so wrap
  // whatever is there (idempotent) instead of replacing it; the older kit editor below still loads as before.
  function install() {
    window.HS_SUBS = window.HS_SUBS || {};
    const cur = window.HS_SUBS["brand:kit"];
    if (!cur || !cur._bk) {
      const w = () => { open(); if (cur) cur(); };
      w._bk = true; window.HS_SUBS["brand:kit"] = w;
    }
  }
  function refreshLegacy() { try { const t = window.HS_TABS && window.HS_TABS.brand; if (t) t(); } catch (_) { /* optional */ } }
  window.HQBrandKit = { open, reload: () => load(), settingsCard };
  const boot = () => {
    mount(); install();
    const t = document.getElementById("tab-brand");
    if (t && !t.hidden && (t.dataset.sub || "kit") === "kit") open();
    if (!document.getElementById("tab-settings")?.hidden) settingsCard();
  };
  install();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  setTimeout(boot, 0);
  window.addEventListener("hq:session", boot);
  document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest('[data-tab="settings"],[data-go="settings"]')) setTimeout(settingsCard, 60); });
})();
