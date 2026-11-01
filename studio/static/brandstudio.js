// HuhQueue: Brand studio (kit, front/end cards, templates with a live preview, example brands, recurring slots) and the
// Sources agent (scan -> clusters -> per-channel drafts -> Buffer DRAFTS). Every button is one REST call that is also an
// MCP tool. This file adds its own two tabs, so the page shell stays untouched.
"use strict";
(function () {
  const B = { kit: null, tpls: [], styles: {}, examples: [], slots: [], tpl: "weekly_recap", svc: "instagram", over: {}, t: null,
              run: null, picks: new Set(), pkgs: [] };
  const FONTS = [["anton", "Anton"], ["poppins", "Poppins"], ["inter", "Inter"], ["figtree", "Figtree"]];
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
    window.HS_SUBS = Object.assign(window.HS_SUBS || {}, { "brand:ledger": () => window.loadLedger?.(), "brand:kit": () => B.kit || loadBrand(), "brand:templates": () => B.kit || loadBrand(), "news:scan": loadSources });
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
          <div class="field"><label class="label" for="bsBody">Body</label><select id="bsBody" data-testid="bs-body">${FONTS.filter((f) => f[0] !== "anton").map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
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
      fillKit(d.kit); examples(d.active); slots(); preview(); window.brandCheck?.($("#kitCheck"));
    } catch (e) { toast(e.message); }
  }
  function examples(active) {
    $("#bsExamples").innerHTML = B.examples.map((e) => `<article class="bs-excard ${e.id === active ? "on" : ""}" data-testid="bs-example" data-id="${e.id}">
      <div class="bs-swatch">${["primary", "accent", "text", "secondary"].map((c) => `<i style="background:${esc(e.colors[c] || "#888")}"></i>`).join("")}</div>
      <h3>${esc(e.title)}</h3><p class="muted sm">${esc(e.about)}</p>${e.label ? `<p class="bs-label">${esc(e.label)}</p>` : ""}
      <div class="row"><button class="btn sm" type="button" data-use="${e.id}" data-testid="bs-use-${e.id}">${e.id === active ? "In use" : "Use this brand"}</button>
      <button class="btn2 sm" type="button" data-gal="${e.id}" data-testid="bs-gallery-${e.id}">See examples</button></div></article>`).join("");
    document.querySelectorAll("#bsExamples [data-use]").forEach((b) => (b.onclick = async () => {
      b.disabled = true;
      try { const r = await api(`/api/brand-studio/examples/${b.dataset.use}`, { method: "POST" }); fillKit(r.kit); examples(b.dataset.use); preview(); toast(`${r.kit.name} is your brand now`); }
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
    <ul id="saClusters" class="sa-clusters" data-testid="sa-clusters"></ul>
    <div class="sa-bar" id="saBar" hidden><span id="saPicked">0 stories</span><button class="btn" type="button" id="saDraft" data-testid="sa-draft">Write posts</button></div>
    <section id="saPkgs" class="panel" data-testid="sa-packages" hidden></section>`;
  const SRC_HTML = `
    <section class="panel sa-set" aria-labelledby="saSetH"><h2 class="h2" id="saSetH">What the story scan reads</h2>
      <div class="sa-srcs" id="saSrcs" data-testid="sa-sources"></div>
      <div class="row2"><div class="field"><label class="label" for="saPh">Product Hunt developer token (free)</label><input id="saPh" type="password" autocomplete="off" placeholder="paste to add" data-testid="sa-ph-key"></div>
        <div class="field"><label class="label" for="saX">X API bearer token (yours)</label><input id="saX" type="password" autocomplete="off" placeholder="paste to add" data-testid="sa-x-key"></div></div>
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
        <span class="sa-src-t"><b>${esc(a.label)}</b>${a.needs_key ? `<small>${(a.key === "x" ? s.x_key : s.ph_key) ? "key added" : "needs your key"}</small>` : "<small>free</small>"}</span></label>`).join("");
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
  function list() {
    const rows = B.run.clusters.filter((c) => c.lane !== "hidden" && (!B.topic || c.topic === B.topic)).slice(0, 30);
    $("#saClusters").innerHTML = rows.map((c) => `<li class="sa-card lane-${c.lane}" data-testid="sa-cluster">
      <label class="chk"><input type="checkbox" data-pick="${c.id}" ${B.picks.has(c.id) ? "checked" : ""} aria-label="Pick ${esc(c.story.headline)}"></label>
      <div><div class="sa-meta"><span class="sa-topic">${esc(c.topic)}</span><span>${LANE[c.lane]}</span>${AGENT[c.agent] ? `<span class="sa-warn">${AGENT[c.agent]}</span>` : ""}<span class="muted">score ${c.score}</span><span class="muted">${esc(c.judgments.provider || "rules")}</span></div>
      <h3><a href="${esc(c.items[0].url)}" target="_blank" rel="noopener">${esc(c.story.headline)}</a></h3>
      ${c.story.line ? `<p class="muted sm">${esc(c.story.line)}</p>` : ""}
      <p class="sm">${c.items.map((i) => esc(i.source)).join(" · ")}${c.story.also.length ? ` · also ${esc(c.story.also.join(", "))}` : ""}</p></div></li>`).join("") || `<li class="muted">Nothing in this topic.</li>`;
    $("#saClusters").querySelectorAll("[data-pick]").forEach((i) => (i.onchange = () => { i.checked ? B.picks.add(i.dataset.pick) : B.picks.delete(i.dataset.pick); bar(); }));
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
      if ($("#saPh").value) body.ph_key = $("#saPh").value;
      if ($("#saX").value) body.x_key = $("#saX").value;
      try { await api("/api/sources/settings", { method: "POST", body }); $("#saPh").value = ""; $("#saX").value = ""; toast("Sources saved"); loadSources(); } catch (e) { toast(e.message); }
    };
    $("#saAiKey").onclick = () => showTab("settings");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
