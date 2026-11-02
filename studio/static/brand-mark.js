// Brand marks and the (i) popover, shared by Settings > AI engine, Settings > Integrations and the landing page.
// Marks come from brand-logos-data.js (simple-icons 16.36.0, CC0; built by scripts/build_brand_logos.py), then
// ai-logos.js (OpenAI's Blossom etc.), then net-logos-data.js; LinkedIn and Groq (not in simple-icons) are monograms;
// "key" and "spark" are plain line icons for "your own key" and the free starter model. Loaded before app.js.
"use strict";
(function () {
  const e = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MONO = { linkedin: ["in", "#0A66C2", "LinkedIn"], groq: ["gq", "#F55036", "Groq"], chatgpt: ["G", "#10A37F", "ChatGPT"] };
  const LINE = {
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3M14 9l2 2"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  };
  function data(id) {
    const k = String(id || "").toLowerCase();
    return (window.HQ_BRAND_DATA || {})[k] || (window.HQ_LOGO_DATA || {})[k] || (window.HQ_NET_DATA || {})[k] || null;
  }
  // A square tile with the mark. opts.label gives screen readers a name (role=img); without it the tile is decorative.
  // opts.plain draws the mark in the current text colour (monochrome, coloured on hover by CSS via --brand).
  function brandMark(id, opts) {
    const o = opts || {}, k = String(id || "").toLowerCase(), d = data(k), m = MONO[k];
    const name = o.label || (d && d.label) || (m && m[2]) || k;
    const a11y = o.label ? `role="img" aria-label="${e(name)}"` : `aria-hidden="true"`;
    const cls = `bmark${o.plain ? " plain" : ""}${o.cls ? " " + o.cls : ""}`;
    if (d) return `<span class="${cls}" data-mark="${e(k)}" ${a11y} style="--brand:${e(d.hex)}"><svg viewBox="${e(d.vb)}" focusable="false" aria-hidden="true">${d.svg}</svg></span>`;
    if (LINE[k]) return `<span class="${cls} line" data-mark="${e(k)}" ${a11y}><svg viewBox="0 0 24 24" focusable="false" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${LINE[k]}</svg></span>`;
    const txt = m ? m[0] : (k || "?").slice(0, 2);
    return `<span class="${cls} mono" data-mark="${e(k)}" ${a11y}${m ? ` style="--brand:${m[1]}"` : ""}><b>${e(txt)}</b></span>`;
  }
  // (i) popovers: a button[data-tip-for] toggles the element it names; Escape or a click elsewhere closes it.
  let open = null;
  function closeTip() {
    if (!open) return;
    open.tip.hidden = true; open.btn.setAttribute("aria-expanded", "false"); open = null;
  }
  function toggleTip(btn) {
    const tip = document.getElementById(btn.getAttribute("aria-controls") || "");
    if (!tip) return;
    const was = open && open.btn === btn;
    closeTip();
    if (was) return;
    tip.hidden = false; btn.setAttribute("aria-expanded", "true"); open = { btn, tip };
    // keep it on screen: align to the button, flip left when it would spill past the right edge
    tip.style.left = ""; tip.style.right = "";
    const r = tip.getBoundingClientRect();
    if (r.right > window.innerWidth - 8) { tip.style.left = "auto"; tip.style.right = "0"; }
  }
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest(".info-i[aria-controls]");
    if (b) { ev.preventDefault(); ev.stopPropagation(); toggleTip(b); return; }
    if (open && !open.tip.contains(ev.target)) closeTip();
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && open) { const b = open.btn; closeTip(); b.focus(); } });
  window.brandMark = brandMark;
  window.hqTip = { close: closeTip, toggle: toggleTip };
})();
