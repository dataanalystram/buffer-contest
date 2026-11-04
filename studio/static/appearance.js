// Light / dark / system (owner, 1 Nov 2026: "no visible dark/light mode toggle"). Loaded in <head> before the CSS paints,
// so a saved choice never flashes the other theme. "system" (the default) follows prefers-color-scheme; "light" and
// "dark" set html[data-theme], which every stylesheet's dark block respects (:root[data-theme=dark] / :not([data-theme=light])).
// Saved in localStorage (hq_mode) and, when signed in, in the account (PUT /api/prefs/appearance) so it follows you.
"use strict";
(() => {
  // the sidebars as you left them (sidebars.js, chat.js), set before first paint so a reload never jumps; motion starts
  // only after the first frame, so the restored state never animates in
  try { if (localStorage.getItem("hq_rail") === "collapsed") document.documentElement.classList.add("rail-collapsed");
    if (localStorage.getItem("hq_threads") === "hidden") document.documentElement.classList.add("threads-hidden"); } catch (_) { /* private mode */ }
  addEventListener("load", () => requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.add("hq-anim"))));
  const KEY = "hq_mode", root = document.documentElement, mq = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  const read = () => { try { return localStorage.getItem(KEY) || "system"; } catch (_) { return "system"; } };
  const effective = (m) => (m === "light" || m === "dark" ? m : mq && mq.matches ? "dark" : "light");
  function apply(m) {
    if (m === "light" || m === "dark") root.setAttribute("data-theme", m); else root.removeAttribute("data-theme");
    root.dataset.mode = m;
    document.querySelectorAll("[data-theme-toggle]").forEach(paintBtn);
    document.querySelectorAll("[data-theme-choice]").forEach((b) => { const on = b.dataset.themeChoice === m; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
  }
  const SUN = '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const MOON = '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>';
  function paintBtn(b) {
    const dark = effective(read()) === "dark";
    b.innerHTML = dark ? SUN : MOON;  // the icon shows what a click switches to
    const next = dark ? "light" : "dark";
    b.setAttribute("aria-label", `Switch to ${next} mode`); b.title = `Switch to ${next} mode`;
    b.setAttribute("aria-pressed", String(dark));
  }
  function set(m, sync = true) {
    try { localStorage.setItem(KEY, m); } catch (_) {}
    apply(m);
    // eslint-disable-next-line no-undef
    if (sync && typeof api === "function" && typeof S !== "undefined" && S.session && S.session.connected && !S.session.demo) {
      api("/api/prefs/appearance", { method: "PUT", body: { mode: m } }).catch(() => {});  // eslint-disable-line no-undef
    }
  }
  apply(read());
  if (mq && mq.addEventListener) mq.addEventListener("change", () => read() === "system" && apply("system"));
  window.HQMode = {
    get: read, effective: () => effective(read()), set,
    toggle: () => set(effective(read()) === "dark" ? "light" : "dark"),
    // the account's saved choice wins on a new device (called once the session is known)
    fromAccount: async () => { try { const r = await api("/api/prefs/appearance");  // eslint-disable-line no-undef
 if (r && r.mode && r.mode !== read() && !sessionStorage.getItem("hq_mode_local")) set(r.mode, false); } catch (_) {} },
    wire: () => {
      document.querySelectorAll("[data-theme-toggle]").forEach((b) => { if (b.dataset.wired) return; b.dataset.wired = "1"; b.onclick = () => { sessionStorage.setItem("hq_mode_local", "1"); window.HQMode.toggle(); }; paintBtn(b); });
      document.querySelectorAll("[data-theme-choice]").forEach((b) => { if (b.dataset.wired) return; b.dataset.wired = "1"; b.onclick = () => { sessionStorage.setItem("hq_mode_local", "1"); set(b.dataset.themeChoice); }; });
      apply(read());
    },
  };
  document.addEventListener("DOMContentLoaded", () => window.HQMode.wire());
})();
