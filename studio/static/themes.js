// HuhQueue: the video style picker, a self-contained component (owner, 2 Nov 2026: the inline gallery made Make a post
// scroll forever; build it like the reference: one compact "Video style" card + a "Change" modal).
//   window.HQThemes.mount(el, { value: "auto", onChange(id) {}, formatLabel() -> "Reel 9:16" })
//     -> { get(), set(id), open(), refresh(), destroy() }
// The card shows the chosen style's still, name, one-line description and the format chip. "Change" opens a modal
// (native <dialog>, focus trapped, Esc closes, the page behind never scrolls) with a category rail and counts, search,
// sort (Popular / A-Z), a 3-column grid (2 on phones) with "Let the agent choose" first, a live 9:16 preview, "Save to
// favourites" (the Saved category, kept in this browser) and "Apply theme". Arrow keys move through the grid.
// Reads GET /api/themes once (cached). No dependencies.
(function () {
  "use strict";
  let cache = null;
  async function load() {
    if (cache) return cache;
    const r = await fetch("/api/themes", { credentials: "same-origin" });
    if (!r.ok) throw new Error(`themes: HTTP ${r.status}`);
    cache = (await r.json()).themes || [];
    return cache;
  }
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const FAV_KEY = "hq_theme_favs";
  const favs = () => { try { const v = JSON.parse(localStorage.getItem(FAV_KEY) || "[]"); return Array.isArray(v) ? v : []; } catch (_) { return []; } };
  const setFavs = (list) => { try { localStorage.setItem(FAV_KEY, JSON.stringify(list)); } catch (_) {} };
  const reduced = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const AUTO = { id: "auto", label: "Let the agent choose", short: "Picks the best style from your story and your words.", category: "auto",
    duration: "Auto", popularity: 0, about: "The agent reads your story and your words, picks the style that fits, and says why in the plan.",
    best_for: ["Reads your story and your words", "Picks from every style in the gallery", "Says why in the plan, so you can switch"] };
  // "No style: post my video as it is" (owner, 2 Nov 2026): only trimmed / fitted to 9:16, the logo (toggle, on by default)
  // and the end card. The agent respects it; MCP set_theme takes "none".
  const NONE = { id: "none", label: "No style", short: "Post my video as it is: fitted to the frame, your logo, the end card.", category: "auto",
    duration: "As is", popularity: 0, about: "Your video exactly as you made it, only lightly finished: trimmed and fitted to 9:16 (blurred sides when the shape differs), your logo if you want it, and the Follow end card. No captions, no theme, no music.",
    best_for: ["Videos you already edited", "Keeps your own captions and sound", "Add my logo: on by default"] };
  const LOGO_KEY = "hq_add_logo";
  const logoOn = () => { try { return localStorage.getItem(LOGO_KEY) !== "0"; } catch (_) { return true; } };
  const CATS = [["all", "All styles"], ["news", "News"], ["startups", "Startups"], ["clips", "Clips"], ["explainers", "Explainers"],
    ["cinematic", "Cinematic"], ["saved", "Saved"]];
  const CHECK = `<svg class="hqt-ck" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7"/></svg>`;
  const STAR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>`;
  let dlgSeq = 0;

  const SPECIAL = (id) => id === "auto" || id === "none";
  const specialPic = (t) => `<span class="${t.id === "none" ? "hqt-auto-pic hqt-none-pic" : "hqt-auto-pic"}" aria-hidden="true"><span>${t.id === "none" ? "▢" : "✦"}</span></span>`;
  function thumb(t, cls) {
    if (SPECIAL(t.id)) return `<span class="${cls}">${specialPic(t)}</span>`;
    return `<span class="${cls}"><img loading="lazy" alt="" src="${esc(t.thumb)}"></span>`;
  }

  function mount(el, opts = {}) {
    let value = opts.value || "auto";
    let items = [AUTO, NONE];
    let ready = false;
    const uid = "hqt" + (++dlgSeq) + Math.random().toString(36).slice(2, 6);
    el.classList.add("hqt");
    el.innerHTML = `<div class="hqt-vs" data-testid="video-style">
        <span class="hqt-vs-pic" aria-hidden="true"></span>
        <span class="hqt-vs-t"><span class="hqt-vs-k" id="${uid}K">Video style</span>
          <b class="hqt-now" data-testid="video-style-name" aria-live="polite">Let the agent choose</b>
          <span class="hqt-vs-d" data-testid="video-style-about"></span></span>
        <span class="hqt-fmt" data-testid="video-style-format"></span>
        <label class="hqt-logo" data-testid="theme-none-logo-wrap" hidden><input type="checkbox" data-testid="theme-none-logo" checked> Add my logo</label>
        <button type="button" class="btn2 sm hqt-change" data-testid="theme-change" aria-haspopup="dialog" aria-describedby="${uid}K">Change</button>
      </div>`;
    const now = el.querySelector(".hqt-now"), about = el.querySelector(".hqt-vs-d"), pic = el.querySelector(".hqt-vs-pic");
    const fmt = el.querySelector(".hqt-fmt"), change = el.querySelector(".hqt-change");
    const logoWrap = el.querySelector(".hqt-logo"), logoBox = el.querySelector("[data-testid=theme-none-logo]");
    logoBox.checked = logoOn();
    logoBox.addEventListener("change", () => {
      try { localStorage.setItem(LOGO_KEY, logoBox.checked ? "1" : "0"); } catch (_) {}
      if (typeof opts.onLogo === "function") opts.onLogo(logoBox.checked);
    });

    function paintCard() {
      const t = items.find((x) => x.id === value) || AUTO;
      now.textContent = t.label;
      about.textContent = t.short || "";
      pic.innerHTML = SPECIAL(t.id) ? specialPic(t) : `<img alt="" src="${esc(t.thumb)}">`;
      logoWrap.hidden = t.id !== "none";
      const f = typeof opts.formatLabel === "function" ? opts.formatLabel() : "";
      fmt.textContent = f || ""; fmt.hidden = !f;
    }

    // ------------------------------------------------------------------ the modal
    const dlg = document.createElement("dialog");
    dlg.className = "hqt-dlg";
    dlg.id = uid + "D";
    dlg.setAttribute("aria-labelledby", uid + "H");
    dlg.setAttribute("data-testid", "theme-modal");
    dlg.innerHTML = `<div class="hqt-dlg-in">
      <header class="hqt-dlg-h"><div><h2 id="${uid}H">Choose a video style</h2><p class="hqt-dlg-sub">Pick the look for your video. Format (Reel, Carousel…) stays separate.</p></div>
        <button type="button" class="hqt-x" data-testid="theme-close" aria-label="Close">×</button></header>
      <div class="hqt-dlg-body">
        <nav class="hqt-rail" aria-label="Style categories" data-testid="theme-rail"></nav>
        <div class="hqt-main">
          <div class="hqt-tools">
            <label class="hqt-search"><span class="sr">Search styles</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>
              <input type="search" data-testid="theme-search" placeholder="Search styles" autocomplete="off" spellcheck="false"></label>
            <label class="hqt-sort"><span class="sr">Sort styles</span><select data-testid="theme-sort"><option value="popular">Popular</option><option value="az">A–Z</option></select></label>
          </div>
          <aside class="hqt-prev" data-testid="theme-preview" aria-live="polite"></aside>
          <div class="hqt-grid" role="radiogroup" aria-label="Video styles" data-testid="theme-picker"></div>
          <p class="hqt-empty" data-testid="theme-empty" hidden>No styles match. Try another word or category.</p>
        </div>
      </div>
      <footer class="hqt-dlg-f"><span class="hqt-count" data-testid="theme-count" aria-live="polite"></span>
        <span class="hqt-f-act"><button type="button" class="btn2 hqt-fav" data-testid="theme-fav" aria-pressed="false">${STAR}<span>Save to favourites</span></button>
        <button type="button" class="btn hqt-apply" data-testid="theme-apply">Apply theme</button></span></footer></div>`;
    document.body.appendChild(dlg);
    const rail = dlg.querySelector(".hqt-rail"), grid = dlg.querySelector(".hqt-grid"), prev = dlg.querySelector(".hqt-prev");
    const search = dlg.querySelector("[data-testid=theme-search]"), sort = dlg.querySelector("[data-testid=theme-sort]");
    const countEl = dlg.querySelector(".hqt-count"), empty = dlg.querySelector(".hqt-empty");
    const favBtn = dlg.querySelector(".hqt-fav"), apply = dlg.querySelector(".hqt-apply");
    const st = { cat: "all", q: "", sort: "popular", pending: value, shown: [], previewed: false };

    function inCat(t, cat) {
      if (cat === "all") return true;
      if (cat === "saved") return favs().includes(t.id);
      return t.category === cat;
    }
    function list() {
      const q = st.q.trim().toLowerCase();
      let out = items.filter((t) => !SPECIAL(t.id) && inCat(t, st.cat) &&
        (!q || [t.label, t.short, t.about, (t.best_for || []).join(" "), t.category_label].join(" ").toLowerCase().includes(q)));
      out = out.sort(st.sort === "az" ? (a, b) => a.label.localeCompare(b.label) : (a, b) => (a.popularity || 99) - (b.popularity || 99));
      const showAuto = st.cat === "all" && (!q || (AUTO.label + " " + AUTO.short + " auto agent").toLowerCase().includes(q));
      const showNone = st.cat === "all" && (!q || (NONE.label + " " + NONE.short + " none as is plain original").toLowerCase().includes(q));
      return [...(showAuto ? [AUTO] : []), ...(showNone ? [NONE] : []), ...out];
    }
    function drawRail() {
      const themes = items.filter((t) => !SPECIAL(t.id)), f = favs();
      rail.innerHTML = CATS.map(([k, l]) => {
        const n = k === "all" ? themes.length : k === "saved" ? themes.filter((t) => f.includes(t.id)).length : themes.filter((t) => t.category === k).length;
        return `<button type="button" class="hqt-cat${st.cat === k ? " on" : ""}" data-cat="${k}" data-testid="theme-cat-${k}" aria-pressed="${st.cat === k}">
          <span>${esc(l)}</span><span class="hqt-n" aria-label="${n} styles">${n}</span></button>`;
      }).join("");
      rail.querySelectorAll("[data-cat]").forEach((b) => (b.onclick = () => { st.cat = b.dataset.cat; drawRail(); drawGrid(); }));
    }
    function cardHtml(t) {
      const on = t.id === st.pending;
      return `<button type="button" role="radio" class="hqt-tc${on ? " on" : ""}" data-theme="${esc(t.id)}" aria-checked="${on}" tabindex="-1"
        data-testid="theme-${esc(t.id)}" aria-label="${esc(t.label)}. ${esc(t.short || "")}">
        <span class="hqt-tc-pic">${SPECIAL(t.id) ? specialPic(t) : `<img loading="lazy" alt="" src="${esc(t.thumb)}">`}
          <span class="hqt-dur" aria-hidden="true">${esc(t.duration || "")}</span>${on ? CHECK : ""}${favs().includes(t.id) ? `<span class="hqt-star" aria-hidden="true">${STAR}</span>` : ""}</span>
        <span class="hqt-tc-n">${esc(t.label)}</span><span class="hqt-tc-d">${esc(t.short || "")}</span></button>`;
    }
    function drawGrid() {
      st.shown = list();
      grid.innerHTML = st.shown.map(cardHtml).join("");
      empty.hidden = st.shown.length > 0;
      const n = st.shown.filter((t) => !SPECIAL(t.id)).length;
      countEl.textContent = `${n} ${n === 1 ? "style" : "styles"}${st.cat !== "all" || st.q ? " shown" : ""}`;
      const cur = grid.querySelector(".hqt-tc.on") || grid.querySelector(".hqt-tc");
      if (cur) cur.tabIndex = 0;
      grid.querySelectorAll(".hqt-tc").forEach((b) => {
        b.onclick = () => { choose(b.dataset.theme); st.previewed = true; dlg.classList.add("hqt-has-prev"); };
        b.ondblclick = () => { choose(b.dataset.theme); commit(); };
      });
    }
    function drawPreview() {
      const t = items.find((x) => x.id === st.pending) || AUTO;
      const vid = !SPECIAL(t.id) && t.loop && !reduced();
      prev.innerHTML = `<div class="hqt-pv-frame">${SPECIAL(t.id) ? specialPic(t) :
          `<img alt="" src="${esc(t.thumb)}">${vid ? `<video muted loop playsinline autoplay preload="none" src="${esc(t.loop)}" aria-hidden="true"></video>` : ""}`}
          <span class="hqt-pv-ratio" aria-hidden="true">9:16</span></div>
        <div class="hqt-pv-t"><h3 data-testid="theme-preview-name">${esc(t.label)}</h3>
          ${t.category_label ? `<span class="hqt-pv-cat">${esc(t.category_label)} · ${esc(t.duration || "")}</span>` : ""}
          <p>${esc(t.about || t.short || "")}</p>
          <ul class="hqt-pv-list">${(t.best_for || []).slice(0, 3).map((b) => `<li>${CHECK}<span>${SPECIAL(t.id) ? "" : "Great for "}${esc(b)}</span></li>`).join("")}</ul></div>`;
      const v = prev.querySelector("video");
      if (v) { v.addEventListener("canplay", () => v.classList.add("on"), { once: true }); v.play().catch(() => {}); }
      const fav = !SPECIAL(t.id) && favs().includes(t.id);
      favBtn.disabled = SPECIAL(t.id);
      favBtn.setAttribute("aria-pressed", String(fav));
      favBtn.querySelector("span").textContent = fav ? "Saved to favourites" : "Save to favourites";
      apply.textContent = t.id === value ? "Keep this style" : "Apply theme";
    }
    function choose(id) {
      if (!items.some((t) => t.id === id)) return;
      st.pending = id;
      grid.querySelectorAll(".hqt-tc").forEach((b) => {
        const on = b.dataset.theme === id;
        b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1;
        const pic = b.querySelector(".hqt-tc-pic"), ck = pic.querySelector(".hqt-ck");
        if (on && !ck) pic.insertAdjacentHTML("beforeend", CHECK); else if (!on && ck) ck.remove();
      });
      drawPreview();
    }
    function cols() {
      const c = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length;
      return Math.max(1, c || 1);
    }
    grid.addEventListener("keydown", (e) => {
      const btns = [...grid.querySelectorAll(".hqt-tc")];
      const i = btns.indexOf(document.activeElement);
      if (i < 0) return;
      const c = cols();
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: c, ArrowUp: -c, Home: -i, End: btns.length - 1 - i }[e.key];
      if (step === undefined) {
        if (e.key === "Enter" && !e.repeat) { e.preventDefault(); choose(btns[i].dataset.theme); commit(); }
        return;
      }
      e.preventDefault();
      const j = Math.max(0, Math.min(btns.length - 1, i + step));
      choose(btns[j].dataset.theme);
      btns[j].focus();
      btns[j].scrollIntoView({ block: "nearest" });
    });
    search.addEventListener("input", () => { st.q = search.value; drawGrid(); });
    search.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); (grid.querySelector(".hqt-tc.on") || grid.querySelector(".hqt-tc"))?.focus(); } });
    sort.addEventListener("change", () => { st.sort = sort.value; drawGrid(); });
    favBtn.addEventListener("click", () => {
      const id = st.pending; if (SPECIAL(id)) return;
      const f = favs(), has = f.includes(id);
      setFavs(has ? f.filter((x) => x !== id) : [...f, id]);
      drawRail(); drawGrid(); drawPreview();  // the button's own label says it (a toast would sit under the modal)
    });
    apply.addEventListener("click", commit);
    dlg.querySelector(".hqt-x").addEventListener("click", () => close());
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });  // Esc
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });  // the backdrop
    dlg.addEventListener("keydown", (e) => {  // focus trap (the dialog is modal; Tab never leaves it)
      if (e.key !== "Tab") return;
      const f = [...dlg.querySelectorAll("button:not([disabled]),input,select,[tabindex='0']")].filter((x) => x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    function commit() {
      const changed = st.pending !== value;
      pick(st.pending, true);
      close();
      if (changed && window.toast) toast(`Video style: ${(items.find((t) => t.id === value) || AUTO).label}`);
    }
    let lastFocus = null;
    function open() {
      if (dlg.open) return;
      lastFocus = document.activeElement;
      st.pending = value; st.q = ""; search.value = ""; st.previewed = false;
      dlg.classList.remove("hqt-has-prev");
      if (st.cat === "saved" && !favs().length) st.cat = "all";
      drawRail(); drawGrid(); drawPreview();
      document.documentElement.classList.add("hqt-lock");
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
      const cur = grid.querySelector(".hqt-tc.on") || grid.querySelector(".hqt-tc");
      (window.matchMedia && matchMedia("(max-width: 640px)").matches ? dlg.querySelector(".hqt-x") : cur || search)?.focus({ preventScroll: true });
      cur && cur.scrollIntoView({ block: "nearest" });
    }
    function close() {
      prev.querySelectorAll("video").forEach((v) => v.pause());
      if (dlg.open) { if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open"); }
      document.documentElement.classList.remove("hqt-lock");
      (lastFocus && document.contains(lastFocus) ? lastFocus : change).focus({ preventScroll: true });
    }
    function pick(id, user) {
      if (!items.some((t) => t.id === id)) return;
      value = id;
      paintCard();
      if (user && typeof opts.onChange === "function") opts.onChange(id);
    }
    change.addEventListener("click", open);
    paintCard();
    load().then((themes) => {
      items = [AUTO, NONE, ...themes];
      ready = true;
      if (!items.some((t) => t.id === value)) value = "auto";
      paintCard();
      if (dlg.open) { drawRail(); drawGrid(); drawPreview(); }
    }).catch(() => { about.textContent = "Styles didn't load; the agent will choose."; change.disabled = true; });
    return { get: () => value, logo: () => logoBox.checked, set: (id) => (ready ? pick(id, false) : (value = id || "auto")), open, close, refresh: paintCard,
      destroy: () => { close(); dlg.remove(); el.innerHTML = ""; el.classList.remove("hqt"); } };
  }
  window.HQThemes = { mount, load };
})();
