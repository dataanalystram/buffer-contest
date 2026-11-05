// View transitions (owner ask, 5 Nov 2026): "switching tabs or views flashes the wrong page, then it vanishes at once".
// One helper every screen change goes through (showTab, a sub-tab, View run, open a take, open a thread):
//  - the target screen is hidden (visibility, so it keeps its layout for the loaders that measure) and the HuhQueue logo
//    loader shows in its place at once: the old screen's content is never on view while the new one loads;
//  - the screen's loaders run, then it is shown once, after at least MIN_MS so a fast answer doesn't flicker;
//  - past SLOW_MS the loader adds list skeletons under the mark; past CAP_MS the screen is shown with its own skeletons;
//  - a newer transition aborts the older one (AbortController: HQView.signal, plus the left screen's HQLoad requests), so a
//    late answer never reveals or redraws over the screen picked now;
//  - a loader that fails shows an inline error with Retry instead of a blank or wrong page.
// The loader is our own code (vanilla SVG + CSS), after the idea of the Logo Trace Loader on 21st.dev (dqnamo, MIT): a short
// lavender dash loops around the mark's outline while work runs. No code copied (see NOTICE). Static under reduced motion.
"use strict";
(() => {
  const MIN_MS = 150, SLOW_MS = 300, CAP_MS = 8000;
  // HQLoad request keys that belong to a screen: leaving the screen aborts them (take.js aborts its own on leave)
  const KEYS = { inbox: ["inbox"], library: ["library", "openrun"], planner: ["planner"] };
  let seq = 0, ctl = null, curEl = null;
  const now = () => (window.performance ? performance.now() : Date.now());
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const aborted = (e) => !!e && (e.name === "AbortError" || e.code === 20);

  // the mark: the app icon inside a rounded-square outline (the icon's own shape) that a short dash traces
  const MARK = '<div class="hq-vt-mark"><svg class="hq-vt-ring" viewBox="0 0 64 64" aria-hidden="true" focusable="false">'
    + '<rect class="hq-vt-track" x="2" y="2" width="60" height="60" rx="17" pathLength="100"/>'
    + '<rect class="hq-vt-trace" x="2" y="2" width="60" height="60" rx="17" pathLength="100"/></svg>'
    + '<img class="hq-vt-logo" src="/static/brand/hb-mark-96.png" width="40" height="40" alt="" decoding="async"></div>';
  /** The loader markup alone (admin.js and any region that wants the mark inline use it too). */
  const markup = (label = "Loading") => `<div class="hq-vt-in">${MARK}<span class="hq-vh" role="status">${label}</span></div>`;

  function overlay(el, label) {
    let o = el.querySelector(":scope > .hq-vt");
    if (!o) { o = document.createElement("div"); o.className = "hq-vt"; o.setAttribute("data-testid", "view-loader"); el.prepend(o); }
    o.classList.remove("slow"); o.innerHTML = markup(label);
    el.classList.add("hq-vt-wait"); el.setAttribute("aria-busy", "true");
    return o;
  }
  function clear(el) {
    if (!el) return;
    el.classList.remove("hq-vt-wait"); el.removeAttribute("aria-busy");
    const o = el.querySelector(":scope > .hq-vt"); if (o) o.remove();
  }
  function slow(o) {
    if (!o.isConnected || o.querySelector(".hq-vt-err")) return;
    o.classList.add("slow");
    if (!o.querySelector(".hq-vt-sk") && window.HQLoad) { const d = document.createElement("div"); d.className = "hq-vt-sk"; d.innerHTML = HQLoad.rows(4); o.appendChild(d); }
  }
  // an inline error with Retry (and Close for a hold on the screen still open): textContent only, never the message as HTML
  function fail(o, el, err, retry, closable) {
    o.classList.remove("slow"); o.innerHTML = "";
    const box = document.createElement("div"); box.className = "hq-vt-err"; box.setAttribute("role", "alert"); box.setAttribute("data-testid", "view-error");
    const b = document.createElement("b"); b.textContent = "This didn't load.";
    const p = document.createElement("span"); p.textContent = (err && err.message) || "Something went wrong.";
    const acts = document.createElement("div"); acts.className = "hq-vt-acts";
    const r = document.createElement("button"); r.type = "button"; r.className = "btn2 sm"; r.textContent = "Retry"; r.setAttribute("data-testid", "view-retry");
    r.onclick = () => { if (retry) retry(); else clear(el); };
    acts.appendChild(r);
    if (closable) { const c = document.createElement("button"); c.type = "button"; c.className = "link"; c.textContent = "Close"; c.setAttribute("data-testid", "view-error-close"); c.onclick = () => clear(el); acts.appendChild(c); }
    box.append(b, p, acts); o.appendChild(box);
    el.removeAttribute("aria-busy");
    r.focus({ preventScroll: true });
  }
  // a new transition: the older one's signal aborts, its overlay goes, the screen being left drops its live requests
  function begin(el, leaving) {
    const my = ++seq;
    if (ctl) ctl.abort();
    ctl = new AbortController(); api.signal = ctl.signal;
    if (curEl && curEl !== el) clear(curEl);
    curEl = el;
    if (leaving && window.HQLoad && HQLoad.cancel) (KEYS[leaving] || []).forEach((k) => HQLoad.cancel(k));
    return my;
  }
  const run = (fn) => { try { return Promise.resolve(typeof fn === "function" ? fn() : fn); } catch (e) { return Promise.reject(e); } };

  /** Show `el` through the loader while `work` (functions or promises) runs; shown once, after MIN_MS. */
  function swap(el, work, opts = {}) {
    if (!el) return Promise.resolve();
    const my = begin(el, opts.leaving);
    const o = overlay(el, opts.label);
    const t0 = now(), st = setTimeout(() => { if (my === seq) slow(o); }, SLOW_MS);
    const jobs = (Array.isArray(work) ? work : [work]).map(run);
    const done = Promise.all(jobs).then(() => null, (e) => e);
    const cap = sleep(CAP_MS).then(() => null);
    return Promise.race([done, cap]).then(async (err) => {
      const left = MIN_MS - (now() - t0); if (left > 0) await sleep(left);
      clearTimeout(st);
      if (my !== seq) return;  // a newer screen was picked meanwhile: it owns the loader now
      if (err && !aborted(err)) { fail(o, el, err, opts.retry ? () => swap(el, [opts.retry], { label: opts.label, retry: opts.retry }) : null, false); return; }
      clear(el); curEl = null;
    });
  }
  /** Keep the screen still open under the loader while a deep link (View run, a take) fetches what it opens. */
  function hold(el, label) {
    if (!el) return { done() {}, fail() {}, live: () => false, signal: undefined };
    const my = begin(el), o = overlay(el, label), st = setTimeout(() => { if (my === seq) slow(o); }, SLOW_MS), sig = ctl.signal;
    const live = () => my === seq && !sig.aborted;
    return {
      signal: sig, live,
      done() { clearTimeout(st); if (my === seq) { clear(el); curEl = null; } },
      fail(err, retry) { clearTimeout(st); if (my !== seq) return; if (aborted(err)) { clear(el); curEl = null; } else fail(o, el, err, retry, true); },
    };
  }
  const api = { swap, hold, markup, clear, signal: undefined, MIN_MS, SLOW_MS, CAP_MS };
  window.HQView = api;
})();
