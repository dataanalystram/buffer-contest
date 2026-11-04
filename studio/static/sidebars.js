// The main sidebar (the section rail) collapses to a 64 px icon rail with tooltips (owner, 3 Nov 2026). Desktop only:
// under 861 px the rail is the bottom bar and this does nothing. The choice is kept in localStorage (hq_rail) and
// applied before first paint by the inline line in index.html, so a reload never jumps. Vanilla, no dependencies.
"use strict";
(() => {
  const rail = document.getElementById("rail"); if (!rail) return;
  const KEY = "hq_rail", root = document.documentElement, desk = matchMedia("(min-width:861px)");
  const get = () => { try { return localStorage.getItem(KEY) === "collapsed"; } catch (_) { return false; } };
  const ic = '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/></svg>';
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "rail-toggle"; btn.id = "railToggle";
  btn.dataset.testid = "nav-collapse"; btn.setAttribute("aria-controls", "rail"); btn.style.order = "0";
  btn.innerHTML = ic;
  rail.prepend(btn);
  const tip = document.createElement("div");
  tip.className = "hq-tip"; tip.id = "hqTip"; tip.setAttribute("role", "tooltip"); tip.dataset.testid = "nav-tooltip"; tip.hidden = true;
  document.body.appendChild(tip);

  function apply(collapsed, persist) {
    root.classList.toggle("rail-collapsed", collapsed);
    btn.setAttribute("aria-expanded", String(!collapsed));
    const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
    btn.setAttribute("aria-label", label); btn.dataset.tip = label;
    if (persist) { try { localStorage.setItem(KEY, collapsed ? "collapsed" : "open"); } catch (_) { /* private mode: this tab only */ } }
    hideTip();
  }
  btn.addEventListener("click", () => apply(!root.classList.contains("rail-collapsed"), true));
  apply(get(), false);

  // tooltips: only while collapsed (the label is on screen otherwise), on hover and keyboard focus, never on touch
  const label = (b) => b.dataset.tip || ((b.querySelector("span:not(.badge)") || {}).textContent || "").trim();
  function showTip(b) {
    if (!desk.matches || (!root.classList.contains("rail-collapsed") && b !== btn)) return;
    const t = label(b); if (!t) return;
    const badge = b.querySelector(".badge:not([hidden])");
    tip.textContent = t + (badge && badge.textContent.trim() ? ` (${badge.textContent.trim()})` : "");
    tip.hidden = false;
    const r = b.getBoundingClientRect();
    tip.style.top = Math.round(r.top + r.height / 2 - tip.offsetHeight / 2) + "px";
    tip.style.left = Math.round(r.right + 10) + "px";
    b.setAttribute("aria-describedby", "hqTip");
  }
  function hideTip() { tip.hidden = true; rail.querySelectorAll("[aria-describedby=hqTip]").forEach((b) => b.removeAttribute("aria-describedby")); }
  rail.addEventListener("mouseover", (e) => { const b = e.target.closest("button"); if (b && rail.contains(b)) showTip(b); });
  rail.addEventListener("mouseout", (e) => { const b = e.target.closest("button"); if (b && !b.contains(e.relatedTarget)) hideTip(); });
  rail.addEventListener("focusin", (e) => { const b = e.target.closest("button"); if (b && b.matches(":focus-visible")) showTip(b); });
  rail.addEventListener("focusout", hideTip);
  rail.addEventListener("click", hideTip);
  rail.addEventListener("scroll", hideTip, { passive: true });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !tip.hidden) hideTip(); });
  window.HQ_RAIL = { set: (c) => apply(!!c, true), collapsed: () => root.classList.contains("rail-collapsed") };
})();
