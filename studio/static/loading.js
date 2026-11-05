// Loading states everywhere (owner ask, 4 Nov 2026): "changing a filter briefly shows the old results, then swaps; Library ->
// Bin shows the previous view's items". Two pieces, used by every screen that fetches:
//  - HQLoad.start(key): one live request per view. Starting a new one aborts the view's previous request (AbortController,
//    passed to api() as `signal`) and hands back live(), so an answer that lost the race is never drawn.
//  - skeletons and a busy bar drawn the moment a load starts, before any network.
// Loaders ported from 21st.dev to vanilla CSS with our tokens (see NOTICE): the Skeleton by shadcn (MIT; a muted block that
// pulses) and the Busy indicator by zweiundeins (MIT; an indeterminate bar that sweeps, a visually hidden role=status that
// reads the label out, a readable static state under prefers-reduced-motion). No gradients, no new fonts.
"use strict";
(() => {
  const live = {};
  function start(key) {
    if (live[key]) live[key].abort();
    const c = new AbortController(); live[key] = c;
    return { signal: c.signal, live: () => live[key] === c && !c.signal.aborted, done: () => { if (live[key] === c) delete live[key]; } };
  }
  const aborted = (e) => !!e && (e.name === "AbortError" || e.code === 20);
  // A poll's next delay: as asked while the tab is on view, at least HIDDEN_MS while it is hidden (a background tab kept
  // asking every 1.4 s for nothing; Render bandwidth quota, 5 Nov 2026). wake(fn) runs fn once when the tab is shown again,
  // so a hidden poll catches up at once instead of waiting out its long delay.
  const HIDDEN_MS = 30000;
  const pollMs = (ms) => (document.hidden ? Math.max(ms, HIDDEN_MS) : ms);
  function wake(fn) {
    if (!document.hidden) return () => {};
    const on = () => { if (!document.hidden) { document.removeEventListener("visibilitychange", on); fn(); } };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }
  const sk = (cls = "") => `<span class="hq-sk${cls ? " " + cls : ""}" aria-hidden="true"></span>`;
  // a status line for screen readers, then the shapes; aria-busy on the container while it shows
  const wrap = (cls, inner, label) => `<div class="hq-skw ${cls}" data-testid="loading-skeleton" aria-busy="true"><span class="hq-vh" role="status">${label || "Loading"}</span>${inner}</div>`;
  const rows = (n = 5, label) => wrap("hq-sk-rows", Array.from({ length: n }, (_, i) =>
    `<div class="hq-sk-row">${sk("hq-sk-th")}<div class="hq-sk-lines">${sk("hq-sk-l w" + (i % 3))}${sk("hq-sk-l sm")}</div></div>`).join(""), label);
  const cards = (n = 6, label) => wrap("hq-sk-cards", Array.from({ length: n }, () =>
    `<div class="hq-sk-card">${sk("hq-sk-media")}${sk("hq-sk-l w0")}${sk("hq-sk-l sm")}</div>`).join(""), label);
  const tiles = (n = 4, label) => wrap("hq-sk-tiles", Array.from({ length: n }, () => `<div class="hq-sk-tile">${sk("hq-sk-l sm")}${sk("hq-sk-l big")}</div>`).join(""), label);
  // Busy (bar variant) over a region that keeps its content while a refresh runs: a 2 px sweep along its top edge
  function busy(el, on, label = "Loading") {
    if (!el) return;
    el.setAttribute("aria-busy", on ? "true" : "false");
    let b = el.querySelector(":scope > .hq-busy");
    if (on && !b) { b = document.createElement("div"); b.className = "hq-busy"; b.setAttribute("data-testid", "loading-bar"); b.innerHTML = '<span class="hq-busy-fill" aria-hidden="true"></span><span class="hq-vh" role="status"></span>'; el.prepend(b); }
    if (b) { b.hidden = !on; b.lastChild.textContent = on ? label : ""; }
  }
  window.HQLoad = { start, aborted, sk, rows, cards, tiles, busy, pollMs, wake, HIDDEN_MS };
})();
