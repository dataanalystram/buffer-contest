// Horizontal strips that actually scroll (owner, 1 Nov 2026: "the video theme gallery doesn't scroll to the right with my
// trackpad / wheel"). Every strip that overflows sideways gets, without changing its markup:
//   wheel        a vertical wheel turns into sideways scroll while the strip can still move that way (then the page scrolls)
//   shift+wheel  and trackpad sideways swipes: native, and never snapped back (no mandatory snap fights the gesture)
//   drag         press and drag with a mouse (a drag of 6 px or more never counts as a click)
//   keyboard     ArrowLeft / ArrowRight / Home / End when the strip itself has focus (it is made focusable)
//   buttons      ‹ › buttons at the edges, shown only while there is more to see that way
// window.hqStrip(el) wires one element; data-strip on any element opts it in; the list below covers the app's strips.
"use strict";
(() => {
  const SEL = ".pm-strip,.bs-strip,.strip,.mp-filters,.pl-views,.mp-disc-tabs,.wrap-x,.rail,.mp-chans,[data-strip]";
  const overflows = (el) => el.scrollWidth - el.clientWidth > 2;
  function edges(el) {
    const w = el.closest(".hq-strip-wrap"); if (!w) return;
    const max = el.scrollWidth - el.clientWidth;
    w.classList.toggle("can-l", el.scrollLeft > 2); w.classList.toggle("can-r", el.scrollLeft < max - 2);
  }
  function wire(el) {
    if (!el || el.dataset.stripWired) return;
    el.dataset.stripWired = "1";
    el.addEventListener("wheel", (e) => {
      if (!overflows(el) || e.ctrlKey) return;
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : (e.shiftKey || el.clientHeight < 460 ? e.deltaY : 0);
      if (!dx) return;
      const max = el.scrollWidth - el.clientWidth;
      if ((dx < 0 && el.scrollLeft <= 0) || (dx > 0 && el.scrollLeft >= max - 1)) return;  // at the edge: let the page move
      e.preventDefault();
      el.scrollLeft = Math.max(0, Math.min(max, el.scrollLeft + dx * (e.deltaMode === 1 ? 32 : 1)));
    }, { passive: false });
    let down = null, moved = false;
    el.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse" || e.button !== 0 || !overflows(el)) return;
      down = { x: e.clientX, left: el.scrollLeft }; moved = false;
    });
    window.addEventListener("pointermove", (e) => {
      if (!down) return;
      const d = e.clientX - down.x;
      if (!moved && Math.abs(d) < 6) return;
      moved = true; el.classList.add("hq-dragging"); el.scrollLeft = down.left - d;
    });
    window.addEventListener("pointerup", () => { if (down) setTimeout(() => el.classList.remove("hq-dragging"), 0); down = null; });
    el.addEventListener("click", (e) => { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);
    el.addEventListener("dragstart", (e) => { if (down) e.preventDefault(); });
    const BTN = ".pm-strip,.bs-strip,.strip,.mp-filters,[data-strip-buttons]";
    if (el.matches(BTN) && !el.hasAttribute("tabindex") && !el.querySelector("[role=radio],[role=tab],button,a,input")) el.tabIndex = 0;
    el.addEventListener("keydown", (e) => {
      if (e.target !== el) return;
      const step = Math.max(120, el.clientWidth * 0.8);
      const go = { ArrowRight: step, ArrowLeft: -step, End: 1e9, Home: -1e9 }[e.key];
      if (go === undefined || !overflows(el)) return;
      e.preventDefault(); el.scrollBy({ left: go, behavior: "smooth" });
    });
    if (el.matches(BTN) && el.parentElement && !el.parentElement.classList.contains("hq-strip-wrap")) {
      const w = document.createElement("div"); w.className = "hq-strip-wrap";
      el.parentElement.insertBefore(w, el); w.appendChild(el);
      for (const [cls, dir, lab] of [["l", -1, "Scroll left"], ["r", 1, "Scroll right"]]) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "hq-strip-btn " + cls; b.setAttribute("aria-label", lab); b.tabIndex = -1;
        b.dataset.testid = "strip-" + (cls === "l" ? "prev" : "next"); b.setAttribute("data-testid", b.dataset.testid);
        b.innerHTML = cls === "l" ? "‹" : "›";
        b.onclick = () => el.scrollBy({ left: dir * Math.max(120, el.clientWidth * 0.8), behavior: "smooth" });
        w.appendChild(b);
      }
      el.addEventListener("scroll", () => edges(el), { passive: true });
      new ResizeObserver(() => edges(el)).observe(el);
      edges(el);
    }
  }
  function scan(root = document) { root.querySelectorAll(SEL).forEach(wire); }
  window.hqStrip = wire; window.hqStripScan = scan;
  document.addEventListener("DOMContentLoaded", () => {
    scan();
    new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1) { if (n.matches(SEL)) wire(n); scan(n); } })
      .observe(document.body, { childList: true, subtree: true });
  });
})();
