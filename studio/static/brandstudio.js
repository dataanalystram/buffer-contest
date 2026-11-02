// HuhQueue: Brand studio (kit, front/end cards, templates with a live preview, example brands, recurring slots) and the
// Sources agent (scan -> clusters -> per-channel drafts -> Buffer DRAFTS). Every button is one REST call that is also an
// MCP tool. This file adds its own two tabs, so the page shell stays untouched.
"use strict";
(function () {
  const B = { kit: null, tpls: [], styles: {}, examples: [], slots: [], tpl: "weekly_recap", svc: "instagram", over: {}, t: null,
              run: null, picks: new Set(), pkgs: [] };
  const FONTS = [["anton", "Anton"], ["poppins", "Poppins"], ["inter", "Inter"], ["figtree", "Figtree"], ["roboto", "Roboto"],
                 ["sourceserif", "Source Serif 4"], ["garamond", "EB Garamond"], ["serif", "DM Serif Display"], ["dot", "Doto (dot matrix)"]];
  const BODY_ONLY = new Set(["anton", "garamond", "serif", "dot"]);  // display faces never set body copy
  const LAYOUT_LABEL = { bold_news: "Bold news", editorial: "Editorial", minimal: "Minimal", playful: "Playful" };
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const ICON = {
    brandstudio: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 13h8M8 16h5"/>',
    sources: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8 11h6M11 8v6"/>' };

  // ------------------------------------------------------------ mount: the Brand screen's Kit and Templates, and News'
  // Story scan and source settings (the shells are in index.html; one kit, one source list, no second copy anywhere)
  function mount() {
    const k = document.getElementById("bsKitMount"); if (!k || document.getElementById("bsName")) return;
    k.innerHTML = KIT_HTML; document.getElementById("bsTplMount").innerHTML = TPL_HTML;
    document.getElementById("saScanMount").innerHTML = SCAN_HTML; document.getElementById("saSetMount").innerHTML = SRC_HTML;
    window.HS_TABS = Object.assign(window.HS_TABS || {}, { brand: () => { loadBrand(); if ((document.getElementById("tab-brand").dataset.sub || "kit") === "ledger") window.loadLedger?.(); } });
    window.HS_SUBS = Object.assign(window.HS_SUBS || {}, { "brand:ledger": () => window.loadLedger?.(), "brand:kit": () => B.kit || loadBrand(), "brand:templates": async () => { if (!B.kit) await loadBrand(); loadKits(); }, "news:scan": loadSources });
    window.loadSources = loadSources;
    wireBrand(); wireSources();
  }
  const cost = (r) => r && r.buffer_cost ? (r.buffer_cost.live ? `Buffer API: ${r.buffer_cost.requests} request${r.buffer_cost.requests === 1 ? "" : "s"}`
    : "Sample: 0 Buffer requests (connect Buffer to see real calls)") : "";
  function showCost(id, r) { const el = $(id); if (el) { el.textContent = cost(r); el.hidden = !r || !r.buffer_cost; } }

  // ============================================================ Brand studio
  const KIT_HTML = `
    <div class="bs-grid">
      <section class="panel bs-kit" aria-labelledby="bsKitH"><h2 id="bsKitH" class="h2">Your kit</h2>
        <div class="field"><label class="label" for="kitSite">Website</label><div class="row bs-site"><input id="kitSite" data-testid="kit-site" placeholder="yourbrand.com" autocomplete="url"><button class="btn2" type="button" id="kitImport" data-testid="kit-import">Import logo and colours</button></div></div>
        <div class="field"><label class="label" for="bsLogo">Logo</label><div class="row"><img id="bsLogoImg" class="bs-logo" alt="" hidden><input id="bsLogo" data-testid="bs-logo" type="file" accept="image/png,image/jpeg,image/webp"></div></div>
        <div class="row2"><div class="field"><label class="label" for="bsName">Brand name</label><input id="bsName" data-testid="bs-name" maxlength="60"></div>
          <div class="field"><label class="label" for="bsHandle">Handle</label><input id="bsHandle" data-testid="bs-handle" maxlength="40"></div></div>
        <div class="field"><span class="label">Colours</span><div class="bs-colors">${["primary:Ground", "accent:Accent", "text:Text", "secondary:Second"].map((x) => { const [k, l] = x.split(":"); return `<label class="bs-col"><input type="color" data-col="${k}" data-testid="bs-color-${k}"><span>${l}</span></label>`; }).join("")}</div></div>
        <div class="row3"><div class="field"><label class="label" for="bsFont">Headlines</label><select id="bsFont" data-testid="bs-font">${FONTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
          <div class="field"><label class="label" for="bsBody">Body</label><select id="bsBody" data-testid="bs-body">${FONTS.filter((f) => !BODY_ONLY.has(f[0])).map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
          <div class="field"><label class="label" for="bsCase">Case</label><select id="bsCase"><option value="upper">CAPS</option><option value="title">Sentence</option></select></div></div>
        <div class="field"><label class="label" for="kitVoice">Voice (how you write)</label><textarea id="kitVoice" data-testid="kit-voice" rows="2" maxlength="300" placeholder="Plain, specific, a little dry. No hype."></textarea></div>
        <div class="field"><span class="label">Front card</span><div class="seg bs-styles" id="bsFront" data-testid="bs-front-styles" role="group" aria-label="Front card style"></div>
          <label class="muted sm bs-up">or upload one <input type="file" id="bsFrontImg" data-testid="bs-front-upload" accept="image/png,image/jpeg,image/webp"></label></div>
        <div class="field"><span class="label">End card</span><div class="seg bs-styles" id="bsEnd" role="group" aria-label="End card style"></div>
          <div class="row2"><input id="bsCta" data-testid="bs-cta" placeholder="Follow for more" maxlength="28" aria-label="End card call to action"><input id="bsLine" placeholder="One line under it" maxlength="70" aria-label="End card line"></div>
          <label class="chk"><input type="checkbox" id="ecOn" data-testid="kit-endcard"> End every video with the end card</label>
          <label class="muted sm bs-up">or upload one <input type="file" id="bsEndImg" accept="image/png,image/jpeg,image/webp"></label></div>
        <div class="field"><label class="label" for="bsTags">Hashtags</label><input id="bsTags" data-testid="bs-tags" placeholder="#AI #YourBrand"></div>
        <div class="row bs-save"><button class="btn" id="bsSave" data-testid="bs-save" type="button">Save kit</button><span class="muted sm" id="bsSaved" role="status"></span></div>
      </section>
      <section class="panel bs-prev" aria-labelledby="bsPrevH"><h2 id="bsPrevH" class="h2">Live preview</h2>
        <div class="row2"><div class="field"><label class="label" for="bsTpl">Template</label><select id="bsTpl" data-testid="bs-template"></select></div>
          <div class="field"><label class="label" for="bsSvc">Channel</label><select id="bsSvc" data-testid="bs-service"></select></div></div>
        <p class="muted sm" id="bsTplAbout"></p>
        <div class="bs-strip" id="bsStrip" data-testid="bs-preview" aria-live="polite"></div>
        <div id="kitCheck" data-testid="kit-check"></div>
      </section>
    </div>`;
  const TPL_HTML = `
    <section class="panel bs-kits" aria-labelledby="bsKitsH" data-testid="tpl-kits-panel"><div class="bs-kits-head"><div><h2 id="bsKitsH" class="h2">Brand kits</h2>
      <p class="muted sm">Every kit you have, plus the samples. Pick one and every template below redraws in it at once.</p></div>
      <span class="muted xs" id="bsKitsMs" aria-live="polite" data-testid="tpl-kits-ms"></span></div>
      <div class="bs-kit-grid" id="bsKits" role="radiogroup" aria-label="Brand kit used by every template" data-testid="tpl-kits"></div></section>
    <section class="panel bs-tplprev" aria-labelledby="bsTplPrevH"><h2 id="bsTplPrevH" class="h2">Every template in <span id="bsTplKitName" data-testid="tpl-kit-name">your kit</span></h2>
      <div class="bs-tplprev-grid" id="bsTplPrev" data-testid="tpl-previews" aria-live="polite"></div></section>
    <section class="panel bs-ex" aria-labelledby="bsExH"><h2 id="bsExH" class="h2">Example brands</h2>
      <p class="muted sm">Two finished kits with real renders. Use one to start, then make it yours on the Kit tab.</p>
      <div class="bs-ex-row" id="bsExamples" data-testid="bs-examples"></div>
      <div id="bsGallery" class="bs-gallery" data-testid="bs-gallery" hidden></div></section>
    <div class="bs-grid">
      <section class="panel" aria-labelledby="bsRenH"><h2 id="bsRenH" class="h2">Render a template</h2>
        <div class="field"><label class="label" for="bsTplR">Template</label><select id="bsTplR" data-testid="bs-render-template"></select></div>
        <div class="row"><label class="chk"><input type="checkbox" id="bsVideo"> Reel too</label>
          <button class="btn2" id="bsRender" data-testid="bs-render" type="button">Render for my channels</button></div>
        <div class="muted sm bs-cost" id="bsCost" data-testid="bs-cost" hidden></div>
        <div id="bsRendered" data-testid="bs-rendered"></div></section>
      <section class="panel" aria-labelledby="bsSlotH"><h2 id="bsSlotH" class="h2">Recurring slots</h2>
        <p class="muted sm">A slot renders the template from that week's posts and puts a Buffer <b>draft</b> on each channel at the time. You approve it in Buffer.</p>
        <form class="row bs-slotform" id="bsSlotForm"><select id="bsSlotTpl" aria-label="Template"></select><select id="bsSlotDay" aria-label="Day">${DAYS.map((d) => `<option>${d}</option>`).join("")}</select>
          <input id="bsSlotTime" type="time" value="20:00" aria-label="Time"><button class="btn2 sm" data-testid="bs-slot-add" type="submit">Add slot</button></form>
        <ul class="bs-slots" id="bsSlots" data-testid="bs-slots"></ul>
        <div class="muted sm bs-cost" id="bsSlotCost" hidden></div></section>
    </div>`;

  function kitPatch() {
    const cols = {};
    document.querySelectorAll("#tab-brand [data-col]").forEach((i) => (cols[i.dataset.col] = i.value.toUpperCase()));
    return { name: $("#bsName").value, handle: $("#bsHandle").value, colors: cols, font: $("#bsFont").value, body_font: $("#bsBody").value,
             headline_case: $("#bsCase").value, front_card: { style: B.front },
             hashtags: $("#bsTags").value.split(/[\s,]+/).filter(Boolean), website: $("#kitSite").value.trim(), voice: $("#kitVoice").value,
             end_card: { style: B.end, cta: $("#bsCta").value, line: $("#bsLine").value, on: $("#ecOn").checked } };
  }
  function styleChips(box, cur, set) {
    box.innerHTML = Object.entries(B.styles).map(([k, d]) => `<button type="button" class="${k === cur ? "on" : ""}" data-style="${k}" title="${esc(d)}" aria-pressed="${k === cur}">${k}</button>`).join("");
    box.querySelectorAll("button").forEach((b) => (b.onclick = () => { set(b.dataset.style); styleChips(box, b.dataset.style, set); preview(); }));
  }
  function fillKit(k) {
    B.kit = k; B.front = k.front_card.style; B.end = k.end_card.style;
    $("#bsName").value = k.name || ""; $("#bsHandle").value = k.handle ? "@" + k.handle : "";
    for (const c of ["primary", "accent", "text", "secondary"]) { const i = $(`#tab-brand [data-col=${c}]`); i.value = (k.colors[c] || "#888888").slice(0, 7); }
    $("#bsFont").value = k.font; $("#bsBody").value = k.body_font === "anton" ? "inter" : k.body_font; $("#bsCase").value = k.headline_case;
    $("#bsCta").value = k.end_card.cta || ""; $("#bsLine").value = k.end_card.line || ""; $("#bsTags").value = (k.hashtags || []).join(" ");
    $("#kitSite").value = k.website || ""; $("#kitVoice").value = k.voice || ""; $("#ecOn").checked = !!k.end_card.on;
    const img = $("#bsLogoImg"); img.hidden = !k.logo_url; if (k.logo_url) img.src = k.logo_url;
    styleChips($("#bsFront"), B.front, (v) => (B.front = v)); styleChips($("#bsEnd"), B.end, (v) => (B.end = v));
    steps();
  }
  function steps() {
    const k = B.kit, f = k.first_run || {};
    const items = [["logo", "Upload your logo", f.logo], ["style", "Pick a style", f.style], ["examples", "See examples", f.examples_seen],
                   ["buffer", "Connect Buffer", f.buffer]];
    $("#tab-brand .bs-steps").innerHTML = items.map(([key, l, done], i) => `<li class="${done ? "done" : ""}" data-step="${key}"><b>${done ? "✓" : i + 1}</b>
      ${key === "buffer" && !done ? `<a href="/login" data-testid="bs-connect">${l}</a>` : `<span>${l}</span>`}</li>`).join("");
  }
  async function loadBrand() {
    try {
      const d = await api("/api/brand-studio");
      B.tpls = d.templates; B.styles = d.styles; B.examples = d.examples; B.slots = d.slots; B.channels = d.channels;
      $("#bsTpl").innerHTML = B.tpls.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
      $("#bsTplR").innerHTML = $("#bsTpl").innerHTML;
      $("#bsSlotTpl").innerHTML = B.tpls.filter((t) => t.kind !== "single").map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
      $("#bsSvc").innerHTML = Object.entries(d.channels).filter(([, c]) => !c.video_only).map(([k, c]) => `<option value="${k}">${esc(c.label)} (${c.ratio})</option>`).join("");
      $("#bsTpl").value = B.tpl; $("#bsSvc").value = B.svc;
      fillKit(d.kit); examples(d.active); slots();
      afterBuilder(() => { preview(); window.brandCheck?.($("#kitCheck")); });
    } catch (e) { toast(e.message); }
  }
  // The brand kit builder above paints first; this editor's server-drawn previews wait for it (or 2.5 s at most), so the
  // two never compete for a small instance on landing.
  function afterBuilder(fn) {
    if (window.__hqBkFirst || !document.getElementById("bkBuilder")) { fn(); return; }
    let done = false;
    const go = () => { if (!done) { done = true; fn(); } };
    window.addEventListener("hq:bk-first", go, { once: true });
    setTimeout(go, 2500);
  }
  function examples(active) {
    $("#bsExamples").innerHTML = B.examples.map((e) => `<article class="bs-excard ${e.id === active ? "on" : ""}" data-testid="bs-example" data-id="${e.id}">
      <div class="bs-swatch">${["primary", "accent", "text", "secondary"].map((c) => `<i style="background:${esc(e.colors[c] || "#888")}"></i>`).join("")}</div>
      <h3>${esc(e.title)}</h3><p class="muted sm">${esc(e.about)}</p>${e.label ? `<p class="bs-label">${esc(e.label)}</p>` : ""}
      <div class="row"><button class="btn sm" type="button" data-use="${e.id}" data-testid="bs-use-${e.id}">${e.id === active ? "In use" : "Use this brand"}</button>
      <button class="btn2 sm" type="button" data-gal="${e.id}" data-testid="bs-gallery-${e.id}">See examples</button></div></article>`).join("");
    document.querySelectorAll("#bsExamples [data-use]").forEach((b) => (b.onclick = async () => {
      b.disabled = true;
      try { const r = await api(`/api/brand-studio/examples/${b.dataset.use}`, { method: "POST" }); fillKit(r.kit); examples(b.dataset.use); preview(); toast(`${r.kit.name} is your brand now`); loadKits(); }
      catch (e) { toast(e.message); } finally { b.disabled = false; }
    }));
    document.querySelectorAll("#bsExamples [data-gal]").forEach((b) => (b.onclick = () => gallery(b.dataset.gal)));
  }
  async function gallery(id) {
    const box = $("#bsGallery");
    try {
      const r = await fetch(`/static/examples/${id}/index.json`); const g = await r.json();
      const groups = [["Covers per channel", (x) => x.name.startsWith("cover-")], ["Weekly recap carousel", (x) => /^recap-\d/.test(x.name)],
                      ["Launch", (x) => x.name.startsWith("launch-")], ["Tutorial", (x) => x.name.startsWith("tutorial-")], ["Quote", (x) => x.name.startsWith("quote-")]];
      box.innerHTML = `<div class="row"><h3 class="h3">${esc(g.title)}</h3>${g.label ? `<span class="bs-label">${esc(g.label)}</span>` : ""}<button class="link" type="button" id="bsGalClose">Close</button></div>` +
        groups.map(([t, f]) => `<h4>${t}</h4><div class="bs-strip">${g.images.filter(f).map((x) => `<a href="/static/examples/${id}/${x.file}" target="_blank" rel="noopener"><img loading="lazy" src="/static/examples/${id}/${x.file}" alt="${esc(x.name)}"></a>`).join("")}</div>`).join("") +
        `<h4>Reels (front card first, end card last)</h4><div class="bs-strip">${g.videos.map((v) => `<video controls muted playsinline preload="metadata" src="/static/examples/${id}/${v.file}" data-testid="bs-video"></video>`).join("")}</div>` +
        `<h4>Captions per channel</h4><div class="bs-caps">${Object.entries(g.captions).map(([s, t]) => `<div><b>${esc(netName(s))}</b><pre>${esc(t)}</pre></div>`).join("")}</div>`;
      box.hidden = false; $("#bsGalClose").onclick = () => (box.hidden = true);
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      if (!B.kit.first_run.examples_seen) { api("/api/brand-studio", { method: "POST", body: { seen_examples: true } }).then(fillKit).catch(() => {}); }
    } catch (e) { toast("Couldn't open the examples"); }
  }
  // ------------------------------------------------------------ Templates > Brand kits (brand kit v2, owner 2 Nov 2026)
  // All kits (the samples Nothing, Linear and Notion, HuhByte AI and every kit you saved) as cards; the active one is
  // marked. A click activates it (or adds the sample first) and every template preview redraws in it.
  const kslug = (s) => String(s || "kit").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const thumbs = new Map();  // kit id + updated -> cover URL
  async function loadKits() {
    const box = $("#bsKits"); if (!box) return;
    let r;
    try { r = await api("/api/brand-kits"); } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    B.kitsMeta = r;
    const haveSample = new Set(r.kits.map((k) => String(k.kit.logo || "").replace("sample:", "")));
    const names = new Set(r.kits.map((k) => (k.name || "").toLowerCase()));
    const own = r.kits.map((k) => {
      const c = k.kit.builder_colors || {}, th = k.kit.theme || {}, d = k.kit.design;
      return `<button type="button" role="radio" class="bs-kitcard${k.active ? " on" : ""}" aria-checked="${k.active}" data-kitid="${k.id}"
          data-testid="tpl-kit-${kslug(k.name)}" data-active="${k.active}" style="--g:${esc(c.background)};--a:${esc(c.primary)};--t:${esc(c.text)}">
        <span class="bs-kitthumb" data-thumb="${k.id}" data-u="${k.updated}"><i class="bk-sk" aria-hidden="true"></i></span>
        <span class="bs-kitmeta"><span class="bs-kitname">${k.logo_url ? `<img src="${esc(k.logo_url)}" alt="">` : ""}<b>${esc(k.name)}</b></span>
          <span class="bs-kitsw">${["background", "primary", "accent", "text"].map((x) => `<i style="background:${esc(c[x])}"></i>`).join("")}</span>
          <small>${esc(LAYOUT_LABEL[th.layout] || "Classic")} · ${esc((r.fonts || {})[k.kit.font] || k.kit.font)}${d && d.site ? ` · style from ${esc(d.site)}` : ""}</small>
          ${k.kit.label ? `<small class="muted">${esc(k.kit.label)}</small>` : ""}
          <span class="bs-kitstate">${k.active ? `<b class="bk-on" data-testid="tpl-kit-active">In use</b>` : "Use this kit"}</span></span></button>`;
    });
    const add = (r.samples || []).filter((s) => !haveSample.has(s.id) && !names.has(String(s.name || "").toLowerCase())).map((s) =>
      `<button type="button" class="bs-kitcard add" data-sample="${s.id}" data-testid="tpl-kit-add-${s.id}"><span class="bs-kitthumb add">+</span>
        <span class="bs-kitmeta"><b>${esc(s.name)}</b><small>${esc(s.label)}</small><span class="bs-kitstate">Add and use</span></span></button>`);
    const ex = (r.examples || []).filter((e) => !names.has(String(e.name || "").toLowerCase())).map((e) =>
      `<button type="button" class="bs-kitcard add" data-example="${e.id}" data-testid="tpl-kit-add-${e.id}"><span class="bs-kitthumb add"><img src="${esc(e.logo_url)}" alt=""></span>
        <span class="bs-kitmeta"><b>${esc(e.name)}</b><small>Example brand</small><span class="bs-kitstate">Add and use</span></span></button>`);
    box.innerHTML = own.concat(ex, add).join("");
    box.querySelectorAll("[data-kitid]").forEach((b) => (b.onclick = () => useKit({ id: +b.dataset.kitid }, b)));
    box.querySelectorAll("[data-sample]").forEach((b) => (b.onclick = () => useKit({ sample: b.dataset.sample }, b)));
    box.querySelectorAll("[data-example]").forEach((b) => (b.onclick = () => useKit({ example: b.dataset.example }, b)));
    const act = r.kits.find((k) => k.active);
    $("#bsTplKitName").textContent = act ? act.name : "your kit";
    kitThumbs(r.kits);
    templatePreviews();
  }
  async function kitThumbs(kits) {
    for (const k of kits) {  // one at a time: each kit's cards are cached on the server after the first draw
      const el = document.querySelector(`#bsKits [data-thumb="${k.id}"]`); if (!el) continue;
      const key = k.id + ":" + k.updated + ":" + JSON.stringify(k.kit.theme || {});
      let url = thumbs.get(key);
      if (!url) {
        try {
          const p = await api("/api/brand-kits/preview", { method: "POST", body: { kit_id: k.id, part: "kit", width: 240 } });
          const c = p.cards.find((x) => x.id === "carousel_cover"); url = c && c.url; if (url) thumbs.set(key, url);
        } catch (_) { url = null; }
      }
      const cur = document.querySelector(`#bsKits [data-thumb="${k.id}"]`);
      if (cur && url) cur.innerHTML = `<img src="${esc(url)}" alt="${esc(k.name)} carousel cover" data-testid="tpl-kit-thumb">`;
    }
  }
  async function useKit(what, btn) {
    if (btn.classList.contains("on")) { templatePreviews(); return; }
    btn.disabled = true; btn.setAttribute("aria-busy", "true");
    const t0 = performance.now();
    try {
      const r = what.id ? await api(`/api/brand-kits/${what.id}/activate`, { method: "POST", body: {} })
        : await api("/api/brand-kits", { method: "POST", body: { ...what, activate: true } });
      toast(`${r.name} is the active kit. Every template now renders in it.`);
      B.kit = null;
      await loadBrand();
      window.HQBrandKit?.reload?.();
      window.dispatchEvent(new CustomEvent("hq:brand-kit", { detail: { id: r.id, from: "templates" } }));
      await loadKits();
      $("#bsKitsMs").textContent = `Switched in ${((performance.now() - t0) / 1000).toFixed(1)} s`;
    } catch (e) { toast(e.message); btn.disabled = false; btn.removeAttribute("aria-busy"); }
  }
  // "Every template in <kit>": each template's first slide drawn in the ACTIVE kit (by its id, so it never falls back to
  // another kit's style), one request per template, all in flight at once, each card filled the moment it arrives.
  // The server caches every card (kit render fields + logo + template), so a kit seen before shows at once; this tab
  // also keeps the URLs it got per kit version, so switching back costs no request at all.
  const tplCache = new Map();  // kit id:updated:theme -> {template id: url}
  async function templatePreviews() {
    const box = $("#bsTplPrev"); if (!box || !B.tpls || !B.tpls.length) return;
    const seq = (B.tplSeq = (B.tplSeq || 0) + 1);
    const act = ((B.kitsMeta || {}).kits || []).find((k) => k.active);
    const ver = act ? `${act.id}:${act.updated}:${JSON.stringify(act.kit.theme || {})}` : "house";
    const have = tplCache.get(ver) || {};
    const c = (act && act.kit.builder_colors) || {};
    box.style.setProperty("--kit-g", c.background || "");
    box.dataset.kit = act ? String(act.id) : "";
    box.innerHTML = B.tpls.map((t) => have[t.id]
      ? `<figure class="bs-tplcard" data-tpl="${t.id}"><img src="${esc(have[t.id])}" alt="${esc(t.name)} in ${esc(act ? act.name : "your kit")}" data-testid="tpl-preview"><figcaption>${esc(t.name)}</figcaption></figure>`
      : `<figure class="bs-tplcard" data-tpl="${t.id}"><span class="bk-sk bs-tplsk" data-testid="tpl-preview-ph"></span><figcaption>${esc(t.name)}</figcaption></figure>`).join("");
    const todo = B.tpls.filter((t) => !have[t.id]);
    if (!todo.length) { box.setAttribute("aria-busy", "false"); return; }
    box.setAttribute("aria-busy", "true");
    const t0 = performance.now();
    const one = async (t) => {
      let url = null;
      try {
        const r = act
          ? await api("/api/brand-kits/preview", { method: "POST", body: { kit_id: act.id, part: "templates", ids: [t.id], width: 240 } })
          : null;
        const card = r && r.cards && r.cards[0];
        url = card && card.url;
        if (!r) {  // no saved kit yet: the house kit (older path)
          const p = await api("/api/brand-studio/preview", { method: "POST", body: { template: t.id, service: "instagram", width: 240, max: 1 } });
          url = p.slides && p.slides[0];
        }
      } catch (_) { url = null; }
      if (seq !== B.tplSeq) return;
      const f = box.querySelector(`[data-tpl="${t.id}"]`);
      if (!f) return;
      if (url) {
        have[t.id] = url; tplCache.set(ver, have);
        f.innerHTML = `<img src="${esc(url)}" alt="${esc(t.name)} in ${esc(act ? act.name : "your kit")}" data-testid="tpl-preview"><figcaption>${esc(t.name)}</figcaption>`;
        if (!box.dataset.first) box.dataset.first = String(Math.round(performance.now() - t0));
      } else {
        f.innerHTML = `<span class="bk-sk bs-tplsk off" data-testid="tpl-preview-failed"></span><figcaption>${esc(t.name)} <button type="button" class="linkish" data-retry="${t.id}">Retry</button></figcaption>`;
        const rb = f.querySelector("[data-retry]"); if (rb) rb.onclick = () => one(t);
      }
    };
    delete box.dataset.first;
    await Promise.all(todo.map(one));
    if (seq === B.tplSeq) box.setAttribute("aria-busy", "false");
  }
  window.addEventListener("hq:brand-kit", (e) => {  // the builder saved or switched a kit: the Templates tab follows at once
    if (e.detail && e.detail.from === "templates") return;
    const t = document.getElementById("tab-brand");
    if (t && (t.dataset.sub || "kit") === "templates") loadKits(); else B.kitsMeta = null;
  });
  window.HQTemplatesKits = { reload: loadKits };

  function preview() {
    clearTimeout(B.t);
    B.t = setTimeout(async () => {
      const t = B.tpls.find((x) => x.id === $("#bsTpl").value); $("#bsTplAbout").textContent = t ? t.about : "";
      try {
        const p = kitPatch();
        const r = await api("/api/brand-studio/preview", { method: "POST", body: { template: $("#bsTpl").value, service: $("#bsSvc").value, width: 300,
          kit: { colors: p.colors, font: p.font, body_font: p.body_font, headline_case: p.headline_case, handle: p.handle.replace(/^@/, ""), name: p.name,
                 front_card: p.front_card, end_card: p.end_card } } });
        $("#bsStrip").innerHTML = r.slides.map((s, i) => `<img src="${s}" alt="Slide ${i + 1} of ${r.count}" data-testid="bs-slide">`).join("");
      } catch (e) { $("#bsStrip").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    }, 300);
  }
  async function upload(file) {
    const data = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(file); });
    return api("/api/media", { method: "POST", body: { name: file.name, data } });
  }
  function slots() {
    $("#bsSlots").innerHTML = B.slots.length ? B.slots.map((s) => `<li data-testid="bs-slot"><b>${esc(s.name)}</b> every ${esc(s.weekday)} ${esc(s.at)} <span class="muted sm">${esc(s.tz)}</span>
      ${s.last_result ? `<span class="muted sm">last: ${s.last_result.results.length} drafts</span>` : ""}
      <button class="link" type="button" data-run="${s.id}" data-testid="bs-slot-run">Run now</button><button class="link danger" type="button" data-del="${s.id}">Remove</button></li>`).join("")
      : `<li class="muted sm">No slots yet. Try the weekly recap on Monday at 20:00.</li>`;
    document.querySelectorAll("#bsSlots [data-run]").forEach((b) => (b.onclick = async () => {
      b.disabled = true;
      try { const r = await api(`/api/templates/slots/${b.dataset.run}/run`, { method: "POST" }); showCost("#bsSlotCost", r);
            toast(`${r.drafts.filter((d) => d.status === "sent_to_buffer" || d.status === "pending_approval").length} Buffer drafts for ${new Date(r.due).toLocaleString()}`);
            B.slots = await api("/api/templates/slots"); slots(); }
      catch (e) { toast(e.message); } finally { b.disabled = false; }
    }));
    document.querySelectorAll("#bsSlots [data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/templates/slots/${b.dataset.del}`, { method: "DELETE" }); B.slots = await api("/api/templates/slots"); slots(); }));
  }
  function wireBrand() {
    document.querySelectorAll("#tab-brand input, #tab-brand select").forEach((i) => {
      if (i.type === "file" || i.id === "bsVideo" || i.closest("#bsSlotForm")) return;
      i.addEventListener("input", preview);
    });
    $("#bsSave").onclick = async () => {
      try { const k = await api("/api/brand-studio", { method: "POST", body: kitPatch() }); fillKit(k); $("#bsSaved").textContent = "Saved"; setTimeout(() => ($("#bsSaved").textContent = ""), 2000); window.brandCheck?.($("#kitCheck")); }
      catch (e) { toast(e.message); }
    };
    $("#bsLogo").onchange = async (ev) => {
      const f = ev.target.files[0]; if (!f) return;
      try { const up = await upload(f); fillKit(await api("/api/brand-studio", { method: "POST", body: { logo: up.token } })); preview(); toast("Logo saved"); } catch (e) { toast(e.message); }
    };
    for (const [id, key] of [["#bsFrontImg", "front_card"], ["#bsEndImg", "end_card"]]) {
      $(id).onchange = async (ev) => {
        const f = ev.target.files[0]; if (!f) return;
        try { const up = await upload(f); fillKit(await api("/api/brand-studio", { method: "POST", body: { [key]: { image: up.token } } })); preview(); } catch (e) { toast(e.message); }
      };
    }
    $("#kitImport").onclick = async () => {
      const b = $("#kitImport"), url = $("#kitSite").value.trim(); if (!url) return toast("Type your website first");
      b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Reading…`;
      try { const r = await api("/api/brand/from-website", { method: "POST", body: { url } });
        if (r.sample) toast(r.note, 6000);
        else toast(`Imported ${r.kit.name}${r.found.logo ? ", logo" : ""}${r.found.palette.length ? ", colours" : ""}${r.found.handle ? ", @" + r.found.handle : ""}`, 5000);
        B.kit = null; loadBrand(); }
      catch (e) { toast(e.message, 5000); } finally { b.disabled = false; b.textContent = "Import logo and colours"; }
    };
    $("#bsRender").onclick = async () => {
      const b = $("#bsRender"); b.disabled = true; b.textContent = "Rendering…";
      try {
        const r = await api("/api/templates/render", { method: "POST", body: { template: $("#bsTplR").value, video: $("#bsVideo").checked } });
        showCost("#bsCost", r);
        $("#bsRendered").innerHTML = `<p class="muted sm">Content: ${esc(r.content)}</p>` + r.channels.map((c) => `<div class="bs-chan"><b>${esc(c.label)}</b> <span class="muted sm">${esc(c.ratio)} · ${c.slides.length} slides</span>
          <div class="bs-strip sm">${c.slides.map((u) => `<img loading="lazy" src="${esc(u)}" alt="">`).join("")}${c.note ? `<span class="muted sm">${esc(c.note)}</span>` : ""}</div></div>`).join("") +
          (r.video ? `<video controls muted playsinline src="${esc(r.video.url)}"></video>${r.video.note ? `<p class="muted sm">${esc(r.video.note)}</p>` : ""}` : "");
      } catch (e) { toast(e.message); } finally { b.disabled = false; b.textContent = "Render for my channels"; }
    };
    $("#bsTpl").onchange = () => { B.tpl = $("#bsTpl").value; preview(); };
    $("#bsSvc").onchange = () => { B.svc = $("#bsSvc").value; preview(); };
    $("#bsSlotForm").onsubmit = async (ev) => {
      ev.preventDefault();
      try { await api("/api/templates/slots", { method: "POST", body: { template: $("#bsSlotTpl").value, weekday: $("#bsSlotDay").value, time: $("#bsSlotTime").value,
              tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Dublin" } });
            B.slots = await api("/api/templates/slots"); slots(); toast("Slot added"); } catch (e) { toast(e.message); }
    };
  }

  // ============================================================ Sources agent
  const SCAN_HTML = `
    <section class="panel sa-set" aria-labelledby="saScanH"><div class="sa-head"><div><h2 class="h2" id="saScanH">Story scan</h2>
      <p class="muted sm">Reads YC launches, Hacker News, your feeds, and Product Hunt or X with your own key. The same story from several places becomes one card. Pick stories and it writes a post per channel in your brand, saved to Buffer as <b>drafts</b>.</p></div>
      <div class="sa-go"><button class="btn big" type="button" id="saScan" data-testid="sa-scan">Scan now</button><span class="muted xs" id="saMode"></span></div></div>
      <p class="muted xs">Sources and keys: <button class="link" type="button" data-go="news:sources">Sources</button></p></section>
    <div id="saStatus" class="sa-status" data-testid="sa-status"></div>
    <div id="saTopics" class="chips" data-testid="sa-topics"></div>
    <ul id="saClusters" class="sa-clusters" data-testid="sa-clusters"><li class="ib-none" data-testid="sa-empty">No scan yet. Scan now reads every source you turned on and groups the same story into one card.</li></ul>
    <div id="saPager" class="pg-wrap" data-testid="sa-pager" hidden></div>
    <div class="sa-bar" id="saBar" hidden><span id="saPicked">0 stories</span><button class="btn" type="button" id="saDraft" data-testid="sa-draft">Write posts</button></div>
    <section id="saPkgs" class="panel" data-testid="sa-packages" hidden></section>`;
  const SRC_HTML = `
    <section class="panel sa-set" aria-labelledby="saSetH"><h2 class="h2" id="saSetH">What the story scan reads</h2>
      <div class="sa-srcs" id="saSrcs" data-testid="sa-sources"></div>
      <p class="muted sm" data-testid="sa-keys-note">Product Hunt and X read with your own key. Add, test or remove keys in <button class="link" type="button" data-integ="integrations" data-testid="sa-keys-link">Settings → Integrations</button>.</p>
      <div class="field"><label class="label" for="saXq">X search</label><input id="saXq" placeholder="(AI OR LLM) -is:retweet lang:en"></div>
      <p class="muted sm" id="saAi" data-testid="sa-ai"></p>
      <div class="row"><button class="btn2 sm" type="button" id="saSaveSet" data-testid="sa-save">Save sources</button><button class="link" type="button" id="saAiKey">Use your own AI key</button></div>
    </section>`;
  const LANE = { postable: "Post-worthy", idea_bank: "Idea bank", low: "Low", hidden: "Off topic" };
  const AGENT = { agent_ok: "", verify_first: "Verify first", person_only: "Needs a person" };
  async function loadSources() {
    try {
      const d = await api("/api/sources"); B.sset = d.settings;
      const s = d.settings;
      $("#saSrcs").innerHTML = s.adapters.map((a) => `<label class="sa-src" title="${esc(a.about)}"><input type="checkbox" data-src="${a.key}" ${s.on[a.key] ? "checked" : ""} data-testid="sa-src-${a.key}">
        <span class="sa-src-t"><b>${esc(a.label)}</b>${a.needs_key ? ((a.key === "x" ? s.x_key : s.ph_key) ? "<small>key added</small>" : `<small><button class="link xs" type="button" data-integ="integ-${a.key}" data-testid="sa-add-key-${a.key}">Add your key</button></small>`) : "<small>free</small>"}</span></label>`).join("");
      $("#saXq").value = s.x_query || "";
      $("#saMode").textContent = s.sample ? "Sample workspace: recorded sources, no live calls." : "Live: reads the sources now and drafts into your Buffer.";
      const cls = s.classifier || { order: ["rules"] };
      api("/api/ai").then((ai) => { $("#saAi").textContent = `Writer: ${ai.engine}. Story judges: ${cls.order.join(" → ")}${cls.configured ? ` (capped at ${cls.cap_per_day} a day)` : ""}.`; }).catch(() => {});
      if (d.latest && !B.run) render(d.latest);
    } catch (e) { toast(e.message); }
  }
  function render(run) {
    B.run = run; B.picks = new Set(); B.topic = "";
    $("#saStatus").innerHTML = run.status.map((s) => `<span class="sa-chip ${s.ok ? "ok" : "bad"}" data-testid="sa-src-status">${esc(s.label)}: ${s.ok ? `${s.items} · ${esc(s.mode)}` : esc(s.error)}</span>`).join("") +
      `<span class="muted sm">${run.counts.fetched} items → ${run.counts.unique} unique → ${run.counts.clusters} stories</span>`;
    $("#saTopics").innerHTML = `<button type="button" class="chip on" data-topic="">All</button>` + run.topics.map((t) => `<button type="button" class="chip" data-topic="${esc(t.topic)}">${esc(t.topic)} <small>${t.n}</small></button>`).join("");
    $("#saTopics").querySelectorAll("[data-topic]").forEach((b) => (b.onclick = () => { B.topic = b.dataset.topic; $("#saTopics").querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === b)); list(); }));
    list(); bar();
  }
  // Story scan cards are visual (owner, 1 Nov 2026): picture (og:image via /api/thumb, else the favicon), source, time,
  // headline, why it matters, tags, Make post; 12 per page with numbered pages instead of one endless list.
  const PER = 12;
  function card(c) {
    const it = c.items[0] || {}, url = c.story.url || it.url || "", host = hostOf(url) || it.source || "";
    const ago = it.published ? timeAgo(it.published * 1000) : "";
    const why = c.story.why || c.story.line || "";
    const tags = [c.topic, LANE[c.lane], ...(c.story.also || []).slice(0, 2).map((x) => "also " + x)].filter(Boolean);
    return `<li class="sa-card sa-vis lane-${c.lane}" data-testid="sa-cluster">
      <div class="sa-pic">${url ? `<img src="${esc(window.hqThumb(url, c.story.image || ""))}" alt="" loading="lazy" data-testid="sa-thumb" data-host="${esc(host)}">` : ""}
        <span class="sa-pic-fb" aria-hidden="true">${fav(host)}<b>${esc(host)}</b></span>
        <label class="chk sa-pick"><input type="checkbox" data-pick="${c.id}" data-testid="sa-pick" ${B.picks.has(c.id) ? "checked" : ""} aria-label="Pick ${esc(c.story.headline)}"></label></div>
      <div class="sa-b"><div class="sa-src">${fav(host)}<span>${esc(it.source || host)}</span>${ago ? `<time>${esc(ago)}</time>` : ""}${AGENT[c.agent] ? `<span class="sa-warn">${AGENT[c.agent]}</span>` : ""}</div>
        <h3><a href="${esc(url)}" target="_blank" rel="noopener">${esc(c.story.headline)}</a></h3>
        ${why ? `<p class="sa-why" data-testid="sa-why"><b>Why it matters:</b> ${esc(why)}</p>` : ""}
        <div class="sa-tags" data-testid="sa-tags">${tags.map((t) => `<span class="sa-tag">${esc(t)}</span>`).join("")}<span class="sa-score muted">score ${c.score}</span></div>
        <div class="sa-act"><button class="btn sm" type="button" data-make="${esc(url)}" data-testid="sa-make">Make post</button>
          <a class="link sm" href="${esc(url)}" target="_blank" rel="noopener" data-testid="sa-open">Open source</a></div></div></li>`;
  }
  function list(page) {
    B.page = page || 1;
    const rows = B.run.clusters.filter((c) => c.lane !== "hidden" && (!B.topic || c.topic === B.topic));
    const shown = rows.slice((B.page - 1) * PER, B.page * PER);
    const box = $("#saClusters");
    box.innerHTML = shown.map(card).join("") || `<li class="muted">Nothing in this topic.</li>`;
    box.querySelectorAll("[data-testid=sa-thumb]").forEach((im) => (im.onerror = () => im.closest(".sa-pic").classList.add("nopic")));
    box.querySelectorAll("[data-pick]").forEach((i) => (i.onchange = () => { i.checked ? B.picks.add(i.dataset.pick) : B.picks.delete(i.dataset.pick); bar(); }));
    if (typeof wireMake === "function") wireMake(box);
    if (typeof favicons === "function") favicons(box);
    window.hqPager($("#saPager"), { total: rows.length, page: B.page, per: PER, label: "Story scan pages",
      onPage: (n) => { list(n); $("#saClusters").scrollIntoView({ behavior: "smooth", block: "start" }); } });
  }
  function bar() { $("#saBar").hidden = !B.picks.size; $("#saPicked").textContent = `${B.picks.size} stor${B.picks.size === 1 ? "y" : "ies"}`; }
  function pkgs(r, sent) {
    B.pkgs = r.packages;
    const box = $("#saPkgs"); box.hidden = false;
    box.innerHTML = `<div class="row"><h2 class="h2">${sent ? "Saved to Buffer as drafts" : "Posts per channel"}</h2><span class="muted sm">Writer: ${esc(r.engine)}</span><span class="muted sm bs-cost" data-testid="sa-cost">${esc(cost(r))}</span></div>` +
      r.packages.map((p, i) => `<article class="sa-pkg" data-testid="sa-package">
        <img src="${esc(p.media_url)}" alt="Cover for ${esc(netName(p.service))}" class="r-${esc(p.ratio.replace(":", "x"))}">
        <div><div class="sa-meta"><b>${esc(netName(p.service))}</b><span>${esc(p.channel_name || "")}</span><span class="muted">${esc(p.format)} · ${esc(p.ratio)}</span>${p.agent !== "agent_ok" ? `<span class="sa-warn">${AGENT[p.agent]}</span>` : ""}</div>
        <textarea data-i="${i}" rows="6" ${sent ? "readonly" : ""} aria-label="Caption for ${esc(netName(p.service))}">${esc(p.text)}</textarea>
        <div class="muted sm"><span data-len="${i}">${p.length}</span> / ${p.limit} · ${p.hashtags} hashtags · ${esc(p.writer || "")}
        ${p.result ? ` · <b class="${p.result.status === "sent_to_buffer" || p.result.status === "pending_approval" ? "ok" : "bad"}" data-testid="sa-result">${esc(p.result.status === "sent_to_buffer" ? "Buffer draft " + (p.result.post_id || "") : p.result.status === "pending_approval" ? "Waiting in Approvals" : p.result.error || p.result.status)}</b>` : ""}</div></div></article>`).join("") +
      (sent ? "" : `<div class="row"><button class="btn big" type="button" id="saSend" data-testid="sa-send">Save as Buffer drafts</button><span class="muted sm">Drafts only. Nothing is scheduled or published.</span></div>`);
    box.querySelectorAll("textarea[data-i]").forEach((t) => (t.oninput = () => { const p = B.pkgs[t.dataset.i]; box.querySelector(`[data-len="${t.dataset.i}"]`).textContent = count(p.service, t.value); }));
    const send = $("#saSend");
    if (send) send.onclick = async () => {
      const texts = {};
      box.querySelectorAll("textarea[data-i]").forEach((t) => { const p = B.pkgs[t.dataset.i]; if (t.value !== p.text) (texts[p.cluster_id] = texts[p.cluster_id] || {})[p.channel_id] = t.value; });
      send.disabled = true; send.textContent = "Saving…";
      try { pkgs(await api("/api/sources/draft", { method: "POST", body: { run_id: B.run.run_id, cluster_ids: [...B.picks], dry_run: false, texts } }), true); toast("Saved as Buffer drafts"); }
      catch (e) { toast(e.message); send.disabled = false; send.textContent = "Save as Buffer drafts"; }
    };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function wireSources() {
    $("#saScan").onclick = async () => {
      const b = $("#saScan"); b.disabled = true; b.textContent = "Scanning…";
      try { const on = {}; document.querySelectorAll("#saSrcs [data-src]").forEach((i) => (on[i.dataset.src] = i.checked));
            render(await api("/api/sources/scan", { method: "POST", body: { sources: Object.keys(on).filter((k) => on[k]) } })); $("#saPkgs").hidden = true; }
      catch (e) { toast(e.message); } finally { b.disabled = false; b.textContent = "Scan now"; }
    };
    $("#saDraft").onclick = async () => {
      const b = $("#saDraft"); b.disabled = true; b.textContent = "Writing…";
      try { pkgs(await api("/api/sources/draft", { method: "POST", body: { run_id: B.run.run_id, cluster_ids: [...B.picks], dry_run: true } }), false); }
      catch (e) { toast(e.message); } finally { b.disabled = false; b.textContent = "Write posts"; }
    };
    $("#saSaveSet").onclick = async () => {
      const on = {}; document.querySelectorAll("#saSrcs [data-src]").forEach((i) => (on[i.dataset.src] = i.checked));
      const body = { on, x_query: $("#saXq").value }; // the feed list is managed above (one list for the feed and the scan)
      try { await api("/api/sources/settings", { method: "POST", body }); toast("Sources saved"); loadSources(); } catch (e) { toast(e.message); }
    };
    $("#saAiKey").onclick = () => showTab("settings");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
