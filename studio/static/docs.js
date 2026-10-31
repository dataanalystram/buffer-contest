// Docs search: one fetch of /docs/search.json (built from the same data as the pages), then instant results as you type.
// Arrow keys move, Enter opens, Esc clears. Press / anywhere on a docs page to search.
"use strict";
(() => {
const q = document.getElementById("dq"), res = document.getElementById("dres");
if (!q) return;
let idx = null, items = [], sel = 0;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const load = () => idx || (idx = fetch("/docs/search.json").then((r) => r.json()).catch(() => []));
function score(e, words) { const t = (e.title + " " + e.page).toLowerCase(), b = e.text.toLowerCase(); let s = 0;
  for (const w of words) { if (t.includes(w)) s += 5; else if (b.includes(w)) s += 1; else return 0; } return s; }
async function run() {
  const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) { res.hidden = true; q.setAttribute("aria-expanded", "false"); return; }
  const all = await load();
  items = all.map((e) => ({ e, s: score(e, words) })).filter((x) => x.s).sort((a, b) => b.s - a.s).slice(0, 8).map((x) => x.e);
  sel = 0; res.hidden = false; q.setAttribute("aria-expanded", "true");
  res.innerHTML = items.length ? items.map((e, i) => `<li role="option" id="dr${i}" aria-selected="${i === sel}"><a href="${esc(e.url)}" data-testid="docs-result"><b>${esc(e.title)}</b><span>${esc(e.page)}</span><small>${esc(e.text.slice(0, 110))}</small></a></li>`).join("")
    : `<li class="none" role="presentation">No results. Try “planner”, “token” or “delete”.</li>`;
}
function mark() { [...res.querySelectorAll("[role=option]")].forEach((li, i) => li.setAttribute("aria-selected", i === sel)); q.setAttribute("aria-activedescendant", items.length ? "dr" + sel : ""); }
q.addEventListener("input", run);
q.addEventListener("focus", load);
q.form.addEventListener("submit", (e) => { e.preventDefault(); if (items[sel]) location.href = items[sel].url; });
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
  else if (e.key === "Escape") { q.value = ""; run(); }
});
addEventListener("keydown", (e) => { if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) { e.preventDefault(); q.focus(); } });
})();
