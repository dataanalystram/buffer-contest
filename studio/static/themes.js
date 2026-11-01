// HuhQueue: the video theme picker, a self-contained component.
//   window.HQThemes.mount(el, { value: "auto", onChange(id) {} }) -> { get(), set(id), destroy() }
// Reads GET /api/themes once (cached), shows a gallery of stills that play a short loop on hover/focus, with
// "Let the agent choose" first (the default). Keyboard: arrow keys move, Enter/Space picks. No dependencies.
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
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function card(t, on) {
    const auto = t.id === "auto";
    return `<button type="button" role="radio" class="hqt-card${on ? " on" : ""}" data-theme="${esc(t.id)}" aria-checked="${on}"
      data-testid="theme-${esc(t.id)}" title="${esc(t.about)}">
      <span class="hqt-thumb">${auto ? `<span class="hqt-auto" aria-hidden="true">✦</span>` :
        `<img loading="lazy" alt="" src="${esc(t.thumb)}"><video muted loop playsinline preload="none" data-src="${esc(t.loop)}"></video>`}</span>
      <span class="hqt-name">${esc(t.label)}</span>
      <span class="hqt-for">${esc(auto ? "Picks from your story and words" : (t.best_for || [])[0] || "")}</span></button>`;
  }

  function mount(el, opts = {}) {
    let value = opts.value || "auto";
    let items = [];
    el.classList.add("hqt");
    // collapsed by default (owner, 1 Nov 2026): one "Theme: Let the agent choose ▾" row with the chosen theme's still;
    // opening it shows every theme as a wrapped grid (no sideways scrolling needed at any width)
    const uid = "hqt" + Math.random().toString(36).slice(2, 8);
    el.innerHTML = `<button type="button" class="hqt-sum" aria-expanded="false" aria-controls="${uid}" data-testid="theme-summary">
        <span class="hqt-sum-pic" aria-hidden="true"></span><span class="hqt-sum-t"><span class="hqt-sum-k" id="${uid}L">Theme</span>
        <b class="hqt-now" aria-live="polite">Let the agent choose</b></span><span class="hqt-caret" aria-hidden="true">▾</span></button>
      <div class="hqt-panel" id="${uid}" hidden>
      <div class="hqt-grid" role="radiogroup" aria-labelledby="${uid}L" data-testid="theme-picker"><span class="muted sm">Loading themes…</span></div></div>`;
    const grid = el.querySelector(".hqt-grid");
    const now = el.querySelector(".hqt-now");
    const sum = el.querySelector(".hqt-sum"), panel = el.querySelector(".hqt-panel");
    const open = (on) => { panel.hidden = !on; sum.setAttribute("aria-expanded", String(on)); el.classList.toggle("open", on);
      if (on) (grid.querySelector(".hqt-card.on") || grid.querySelector(".hqt-card"))?.focus({ preventScroll: true }); };
    sum.addEventListener("click", () => open(panel.hidden));
    el.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { e.stopPropagation(); open(false); sum.focus(); } });
    const label = () => (items.find((t) => t.id === value) || { label: "Let the agent choose" }).label;
    function paintSum() {
      const t = items.find((x) => x.id === value);
      now.textContent = label();
      el.querySelector(".hqt-sum-pic").innerHTML = t && t.thumb ? `<img alt="" src="${esc(t.thumb)}">` : `<span class="hqt-auto">✦</span>`;
    }
    function render() {
      grid.innerHTML = items.map((t) => card(t, t.id === value)).join("");
      paintSum();
      grid.querySelectorAll(".hqt-card").forEach((b, i) => {
        b.tabIndex = items[i].id === value ? 0 : -1;
        b.addEventListener("click", () => { pick(b.dataset.theme, true); open(false); sum.focus(); });
        const v = b.querySelector("video");
        if (v) {
          const play = () => { if (!v.src) v.src = v.dataset.src; v.play().catch(() => {}); b.classList.add("playing"); };
          const stop = () => { v.pause(); b.classList.remove("playing"); };
          b.addEventListener("mouseenter", play); b.addEventListener("focus", play);
          b.addEventListener("mouseleave", stop); b.addEventListener("blur", stop);
        }
      });
    }
    function pick(id, user) {
      if (!items.some((t) => t.id === id)) return;
      value = id;
      grid.querySelectorAll(".hqt-card").forEach((b) => {
        const on = b.dataset.theme === id;
        b.classList.toggle("on", on); b.setAttribute("aria-checked", on); b.tabIndex = on ? 0 : -1;
      });
      paintSum();
      if (user && typeof opts.onChange === "function") opts.onChange(id);
    }
    grid.addEventListener("keydown", (e) => {
      const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      const i = items.findIndex((t) => t.id === value);
      const n = items[(i + keys[e.key] + items.length) % items.length];
      pick(n.id, true);
      grid.querySelector(`[data-theme="${n.id}"]`)?.focus();
    });
    load().then((list) => {
      items = [{ id: "auto", label: "Let the agent choose", about: "The agent picks a theme from your story and your words, and says why in the plan." }, ...list];
      if (!items.some((t) => t.id === value)) value = "auto";
      render();
    }).catch(() => { grid.innerHTML = `<span class="muted sm">Themes didn't load; the agent will choose.</span>`; });
    return { get: () => value, set: (id) => pick(id, false), open, destroy: () => { el.innerHTML = ""; el.classList.remove("hqt"); } };
  }
  window.HQThemes = { mount, load };
})();
