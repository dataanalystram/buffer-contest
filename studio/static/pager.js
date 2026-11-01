// Numbered pagination for long lists (Story scan, News feed, Library, Recent content): 12 per page by default, instead of
// endless scroll. hqPager(el, {total, page, per, label, onPage}) renders ‹ 1 2 3 … 9 › into el; page is 1-based.
"use strict";
window.hqPager = function (el, o) {
  if (!el) return;
  const per = o.per || 12, pages = Math.max(1, Math.ceil((o.total || 0) / per)), cur = Math.min(Math.max(1, o.page || 1), pages);
  el.hidden = pages <= 1;
  if (pages <= 1) { el.innerHTML = ""; return; }
  const list = [...new Set([1, pages, cur - 1, cur, cur + 1])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  let html = `<button type="button" class="pg-b" data-pg="${cur - 1}" data-testid="pager-prev" aria-label="Previous page" ${cur === 1 ? "disabled" : ""}>‹</button>`;
  let last = 0;
  for (const n of list) {
    if (n - last > 1) html += `<span class="pg-gap" aria-hidden="true">…</span>`;
    html += `<button type="button" class="pg-b${n === cur ? " on" : ""}" data-pg="${n}" data-testid="pager-page" ${n === cur ? 'aria-current="page"' : ""} aria-label="Page ${n}">${n}</button>`;
    last = n;
  }
  html += `<button type="button" class="pg-b" data-pg="${cur + 1}" data-testid="pager-next" aria-label="Next page" ${cur === pages ? "disabled" : ""}>›</button>`;
  el.innerHTML = `<nav class="pager" aria-label="${o.label || "Pages"}" data-testid="pager">${html}<span class="pg-of">${(cur - 1) * per + 1}–${Math.min(cur * per, o.total)} of ${o.total}</span></nav>`;
  el.querySelectorAll("[data-pg]").forEach((b) => (b.onclick = () => { const n = +b.dataset.pg; if (n >= 1 && n <= pages && n !== cur) o.onPage(n); }));
};
// a story picture through our own server (studio/thumbs.py); the caller shows the favicon monogram on error
// The free sample never makes the server fetch anything (SECURITY_RULES 3): it shows the feed's own picture, if any.
window.hqThumb = (url, img) => (typeof S !== "undefined" && S.session && S.session.demo ? (img || "data:,")
  : `/api/thumb?url=${encodeURIComponent(url || "")}${img ? "&img=" + encodeURIComponent(img) : ""}`);
