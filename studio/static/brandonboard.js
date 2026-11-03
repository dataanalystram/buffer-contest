// HuhQueue: brand onboarding ("Your brand": paste a website -> the kit is read from it -> three stills in that style ->
// tweak -> save) and the sample workspace's guided "Try it" (pick a sample brand -> the same story in each brand side by
// side -> into the sample Buffer queue). Every button is one REST call that is also an MCP tool (extract_brand,
// preview_brand_themes, list_sample_brands, schedule_sample_brand_story). Wrapped in its own scope: page scripts share one.
"use strict";
(() => {
  const O = { draft: null, logo: "", mark: "", res: null, t: null, samples: null, story: null, pick: "", busy: false };
  const LABEL = { primary: "Ground", accent: "Accent", text: "Text", secondary: "Second" };
  const FONT_NAMES = { inter: "Inter", poppins: "Poppins", figtree: "Figtree", anton: "Anton", serif: "DM Serif Display" };
  const isDemo = () => !!(window.S && S.session && S.session.demo);

  // ============================================================ "Your brand" card on Brand -> Kit
  const CARD = `
    <section class="panel bob" aria-labelledby="bobH" data-testid="brand-onboard">
      <div class="bob-head"><div><h2 id="bobH" class="h2">Your brand, from your website</h2>
        <p class="muted sm">Paste your site. HuhQueue reads your logo, colours, fonts and voice from it (no AI, nothing saved yet) and shows your posts in your style.</p></div>
        <button class="btn2 sm" type="button" id="bobDemo" data-testid="bob-demo">See the two-brand demo</button></div>
      <form class="bob-form" id="bobForm" autocomplete="off">
        <label class="sr-only" for="bobUrl">Your website</label>
        <input id="bobUrl" data-testid="bob-url" inputmode="url" placeholder="yourbrand.com" maxlength="300">
        <button class="btn" type="submit" id="bobGo" data-testid="bob-extract">Read my brand</button>
        <span class="bob-try muted sm">Try <button class="link" type="button" data-try="linear.app" data-testid="bob-try-linear">linear.app</button>
          or <button class="link" type="button" data-try="notion.com" data-testid="bob-try-notion">notion.com</button></span>
      </form>
      <p class="muted sm" id="bobStatus" role="status" aria-live="polite"></p>
      <div class="bob-result" id="bobResult" hidden>
        <div class="bob-found" id="bobFound" data-testid="bob-found"></div>
        <div class="bob-stills" id="bobStills" data-testid="bob-stills" aria-live="polite"></div>
        <div class="bob-tweak">
          <div class="bs-colors" id="bobCols"></div>
          <div class="row"><label class="label" for="bobFont">Headlines</label>
            <select id="bobFont" data-testid="bob-font">${Object.entries(FONT_NAMES).map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
            <label class="btn2 sm bob-up">Change logo<input type="file" id="bobLogo" data-testid="bob-logo" accept="image/png,image/jpeg,image/webp" hidden></label></div>
          <div class="row bob-save"><button class="btn accent" type="button" id="bobSave" data-testid="bob-save">Save as my brand</button>
            <span class="muted sm" id="bobSaved" role="status"></span></div>
        </div>
      </div>
    </section>`;

  function mountCard() {
    const host = document.getElementById("bobMount");
    if (!host || document.getElementById("bobForm")) return;
    host.innerHTML = CARD;
    $("#bobForm").onsubmit = (e) => { e.preventDefault(); extract($("#bobUrl").value); };
    host.querySelectorAll("[data-try]").forEach((b) => (b.onclick = () => { $("#bobUrl").value = b.dataset.try; extract(b.dataset.try); }));
    $("#bobDemo").onclick = () => openDemo();
    $("#bobFont").onchange = () => { if (O.draft) { O.draft.font = $("#bobFont").value; O.draft.headline_case = O.draft.font === "anton" ? "upper" : "title"; redraw(); } };
    $("#bobLogo").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try { const up = await upload(f); O.logo = up.token; O.res.logo_url = up.url; found(); redraw(); } catch (err) { toast(err.message); }
    };
    $("#bobSave").onclick = save;
  }
  async function upload(file) {
    const data = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(file); });
    return api("/api/media", { method: "POST", body: { name: file.name, data } });
  }
  const status = (t) => { const el = $("#bobStatus"); if (el) el.textContent = t; };

  async function extract(url) {
    url = (url || "").trim(); if (!url || O.busy) return;
    O.busy = true; $("#bobGo").disabled = true; status(`Reading ${url}: logo, colours, fonts, voice…`);
    const t0 = performance.now();
    try {
      const r = await api("/api/brand/extract", { method: "POST", body: { url } });
      O.res = r; O.draft = JSON.parse(JSON.stringify(r.draft)); O.logo = r.logo || ""; O.mark = r.mark || "";
      $("#bobResult").hidden = false; found(); cols(); $("#bobFont").value = O.draft.font || "inter";
      await redraw(true);
      status(`Read ${r.source === "sample" ? "(cached sample) " : ""}${url} and rendered three posts in ${((performance.now() - t0) / 1000).toFixed(1)} s.`);
    } catch (e) { status(e.message); }
    finally { O.busy = false; $("#bobGo").disabled = false; }
  }
  function sw(k, hex) {
    return `<span class="bob-sw" title="${esc((O.res.provenance || {})[k] || "")}"><i style="background:${esc(hex)}"></i><b>${LABEL[k]}</b><code>${esc(hex)}</code></span>`;
  }
  function found() {
    const r = O.res, d = O.draft, p = r.provenance || {}, f = r.fonts || {};
    const font = (x) => x && x.family ? `${esc(x.family)} → ${FONT_NAMES[x.bundled] || x.bundled}${x.match === "exact" ? "" : " (closest bundled)"}` : `${FONT_NAMES[(x || {}).bundled] || "Inter"} (default)`;
    $("#bobFound").innerHTML = `
      <div class="bob-id" style="--g:${esc(d.colors.primary)};--t:${esc(d.colors.text)}">${r.logo_url ? `<img src="${esc(r.logo_url)}" alt="${esc(d.name)} logo" data-testid="bob-logo-img">` : `<b>${esc(d.name)}</b>`}</div>
      <dl class="bob-dl">
        <div><dt>Name</dt><dd data-testid="bob-name">${esc(d.name)}<small>${esc(p.name || "")}</small></dd></div>
        <div><dt>Handle</dt><dd>${d.handle ? "@" + esc(d.handle) : "<span class=muted>none found</span>"}<small>${esc(p.handle || "")}</small></dd></div>
        <div><dt>Colours</dt><dd class="bob-sws" data-testid="bob-colors">${["primary", "accent", "text", "secondary"].filter((k) => d.colors[k]).map((k) => sw(k, d.colors[k])).join("")}</dd></div>
        <div><dt>Fonts</dt><dd>Headlines: ${font(f.head)}<br>Body: ${font(f.body)}</dd></div>
        <div><dt>Voice</dt><dd class="bob-voice">“${esc((d.voice || "").slice(0, 180))}${(d.voice || "").length > 180 ? "…" : ""}”<small>${esc((d.voice_notes || []).join(" · "))}</small></dd></div>
        <div><dt>Logo</dt><dd><small>${esc(p.logo || "")}</small></dd></div>
      </dl>${r.label ? `<p class="bs-label">${esc(r.label)}</p>` : ""}`;
  }
  function cols() {
    $("#bobCols").innerHTML = ["primary", "accent", "text"].map((k) => `<label class="bs-col"><input type="color" data-bcol="${k}" data-testid="bob-color-${k}" value="${esc((O.draft.colors[k] || "#888888").slice(0, 7))}"><span>${LABEL[k]}</span></label>`).join("");
    document.querySelectorAll("[data-bcol]").forEach((i) => (i.oninput = () => { O.draft.colors[i.dataset.bcol] = i.value.toUpperCase(); found(); redraw(); }));
  }
  function redraw(now) {
    clearTimeout(O.t);
    return new Promise((resolve) => {
      O.t = setTimeout(async () => {
        try {
          const r = await api("/api/brand/themes-preview", { method: "POST", body: { kit: O.draft, logo: O.logo, width: 420 } });
          $("#bobStills").innerHTML = r.stills.map((s) => `<figure class="bob-still r${s.ratio.replace(":", "x")}"><img src="${esc(s.url)}" alt="${esc(s.label)} in ${esc(O.draft.name)}'s style" data-testid="bob-still"><figcaption>${esc(s.label)} · ${esc(s.ratio)}</figcaption></figure>`).join("")
            + `<p class="muted sm bob-ms">Rendered on this server in ${r.ms} ms, nothing stored.</p>`;
        } catch (e) { $("#bobStills").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
        resolve();
      }, now ? 0 : 280);
    });
  }
  async function save() {
    if (!O.draft) return;
    $("#bobSave").disabled = true;
    try {
      await api("/api/brand/onboard", { method: "POST", body: { kit: O.draft, logo: O.logo } });
      $("#bobSaved").textContent = "Saved. Every template and theme now renders in this brand.";
      if (window.HS_TABS && window.HS_TABS.brand) window.HS_TABS.brand();
      toast("Brand saved");
    } catch (e) { toast(e.message); }
    finally { $("#bobSave").disabled = false; }
  }

  // ============================================================ the sample workspace's guided "Try it"
  function dialog() {
    let d = document.getElementById("bdemo");
    if (d) return d;
    d = document.createElement("dialog");
    d.id = "bdemo"; d.className = "bdemo"; d.dataset.testid = "brand-demo"; d.setAttribute("aria-labelledby", "bdemoH");
    d.innerHTML = `<div class="bd-wrap">
      <header class="bd-top"><div><p class="bd-kick">Try it · ${isDemo() ? "sample workspace" : "demo"}</p><h2 id="bdemoH">One story, every brand</h2></div>
        <button class="btn2 sm" type="button" id="bdClose" data-testid="brand-demo-close" aria-label="Close">Close</button></header>
      <ol class="bd-steps" aria-label="Steps"><li data-s="1" class="on">Pick a brand</li><li data-s="2">See it in each style</li><li data-s="3">Schedule it</li></ol>
      <div class="bd-body" id="bdBody"></div></div>`;
    document.body.append(d);
    d.querySelector("#bdClose").onclick = () => d.close();
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    return d;
  }
  function step(n) { document.querySelectorAll("#bdemo .bd-steps li").forEach((li) => li.classList.toggle("on", +li.dataset.s <= n)); }
  async function openDemo() {
    const d = dialog();
    if (!d.open) d.showModal();
    step(1);
    const body = $("#bdBody");
    body.innerHTML = `<p class="muted">Loading the sample brands…</p>`;
    try {
      if (!O.samples) { const r = await api("/api/brand/samples"); O.samples = r.samples; O.story = r.story; }
    } catch (e) { body.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    body.innerHTML = `<p class="bd-lead">Two real brands, read from their own homepages by HuhQueue's extractor: logo, colours, fonts and voice. Pick one.</p>
      <div class="bd-picks">${O.samples.map((s) => `
        <button type="button" class="bd-pick" data-pick="${s.id}" data-testid="brand-demo-pick-${s.id}" style="--g:${esc(s.kit.colors.primary)};--t:${esc(s.kit.colors.text)};--a:${esc(s.kit.colors.accent)}">
          <span class="bd-logo"><img src="${esc(s.logo_url)}" alt="${esc(s.title)} logo"></span>
          <span class="bd-sw">${["primary", "accent", "text", "secondary"].filter((k) => s.kit.colors[k]).map((k) => `<i style="background:${esc(s.kit.colors[k])}" title="${LABEL[k]} ${esc(s.kit.colors[k])}"></i>`).join("")}</span>
          <span class="bd-meta">Read from ${esc((s.hosts || [])[0] || "")} · ${esc(FONT_NAMES[s.kit.font] || s.kit.font)} · @${esc(s.kit.handle)}</span>
          <span class="bd-aff">${esc(s.label)}</span></button>`).join("")}</div>`;
    body.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => compare(b.dataset.pick)));
  }
  function column(s, on) {
    const prov = s.provenance || {};
    return `<section class="bd-col${on ? " on" : ""}" data-testid="brand-demo-col-${s.id}" aria-label="${esc(s.title)}">
      <h3><img src="${esc(s.logo_url)}" alt="" class="bd-mini" style="background:${esc(s.kit.colors.primary)}">${esc(s.title)}${on ? ` <span class="bd-you">your pick</span>` : ""}</h3>
      <div class="bd-row"><figure class="bd-reel"><img src="${esc(s.renders.reel_cover)}" alt="${esc(s.title)} Reel cover" data-testid="brand-demo-reel"><figcaption>Reel cover 9:16</figcaption></figure>
        <figure class="bd-cover"><img src="${esc(s.renders.cover)}" alt="${esc(s.title)} cover" data-testid="brand-demo-cover"><figcaption>Cover 4:5</figcaption></figure></div>
      <div class="bd-car">${s.carousel.map((u, i) => `<img src="${esc(u)}" alt="${esc(s.title)} carousel slide ${i + 1}" data-testid="brand-demo-slide">`).join("")}</div>
      <details class="bd-why"${on ? " open" : ""}><summary>What we read from ${esc((s.hosts || [])[0] || "")}</summary>
        <ul><li><b>Accent ${esc(s.kit.colors.accent)}</b>: ${esc(prov.accent || "")}</li><li><b>Ground ${esc(s.kit.colors.primary)}</b>: ${esc(prov.primary || "")}</li>
          <li><b>Font</b>: ${esc(((s.fonts || {}).head || {}).family || "")} → ${esc(FONT_NAMES[s.kit.font] || s.kit.font)}</li><li><b>Logo</b>: ${esc(prov.logo || "")}</li>
          <li><b>Voice</b>: “${esc((s.kit.voice || "").slice(0, 110))}…”</li></ul></details></section>`;
  }
  function compare(id) {
    O.pick = id; step(2);
    const pick = O.samples.find((s) => s.id === id), rest = O.samples.filter((s) => s.id !== id);
    $("#bdBody").innerHTML = `<p class="bd-lead">The same story, “${esc(O.story.title)}”, drawn in each brand from its kit alone: no per-brand templates, no AI.</p>
      <div class="bd-cols">${[pick, ...rest].map((s) => column(s, s.id === id)).join("")}</div>
      <div class="bd-next"><button class="btn2" type="button" id="bdBack" data-testid="brand-demo-back">Back</button>
        <button class="btn accent" type="button" id="bdSched" data-testid="brand-demo-schedule">Schedule the ${esc(pick.title)} version to the sample queue</button></div>`;
    $("#bdBack").onclick = openDemo;
    $("#bdSched").onclick = () => schedule(id);
  }
  async function schedule(id) {
    const s = O.samples.find((x) => x.id === id);
    const btn = $("#bdSched"); btn.disabled = true;
    if (!isDemo()) {
      $("#bdBody").insertAdjacentHTML("beforeend", `<p class="bd-note" data-testid="brand-demo-note">Scheduling the sample story runs in the free sample workspace (it never touches a real Buffer queue). <a href="/demo?try=brand">Open the sample</a>.</p>`);
      return;
    }
    try {
      const r = await api(`/api/brand/samples/${id}/schedule`, { method: "POST", body: {} });
      step(3);
      const when = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "next free slot";
      $("#bdBody").innerHTML = `<div class="bd-done" data-testid="brand-demo-done">
        <p class="bd-lead"><b>In the sample Buffer queue.</b> ${esc(r.note)}</p>
        <ul class="bd-q">${r.queued.map((q) => `<li data-testid="brand-demo-queued"><span class="bd-svc">${esc(q.service)}</span> ${esc(q.what === "carousel" ? "3-slide carousel" : "cover post")} · ${q.status === "failed" ? `<span class="bad">${esc(q.error || "failed")}</span>` : `queued for ${esc(when(q.due_at))}`}</li>`).join("")}</ul>
        <div class="bd-car big">${s.carousel.map((u, i) => `<img src="${esc(u)}" alt="${esc(s.title)} slide ${i + 1}">`).join("")}</div>
        <div class="bd-next"><button class="btn2" type="button" id="bdUse" data-testid="brand-demo-use">Use ${esc(s.title)} as this sample's brand kit</button>
          <button class="btn" type="button" id="bdPlanner" data-testid="brand-demo-planner">Open the Planner</button></div></div>`;
      $("#bdPlanner").onclick = () => { $("#bdemo").close(); window.showTab && showTab("planner"); };
      $("#bdUse").onclick = async () => {
        try { await api("/api/brand/onboard", { method: "POST", body: { sample_id: id, logo: `sample:${id}` } }); toast(`${s.title} is now the sample's brand kit`); $("#bdemo").close(); window.showTab && showTab("brand"); }
        catch (e) { toast(e.message); }
      };
    } catch (e) { toast(e.message); btn.disabled = false; }
  }
  window.openBrandDemo = openDemo;

  // ============================================================ wiring
  function wireDemoBar() {
    const acts = document.querySelector("#demoBar .demo-acts");
    if (acts && !document.getElementById("bdemoOpen")) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "link"; b.id = "bdemoOpen"; b.dataset.testid = "brand-demo-open"; b.textContent = "Try it with a brand";
      b.onclick = openDemo; acts.prepend(b);
    }
  }
  function boot() {
    mountCard(); wireDemoBar();
    const q = new URLSearchParams(location.search);
    if (q.get("try") === "brand" || location.hash.includes("try=brand")) setTimeout(openDemo, 400);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  window.addEventListener("hq:session", wireDemoBar);
  setTimeout(boot, 1500);
})();
