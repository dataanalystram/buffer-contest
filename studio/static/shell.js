// HuhQueue app shell: the ⌘K / Ctrl K command palette, keyboard shortcuts, the shortcuts sheet, the Help menu (tour and
// docs from every screen) and toasts with Undo. Vanilla, no dependencies; every command calls the same functions the
// buttons call (showTab, plSetView, tourStart), so the palette can never do something the UI cannot.
"use strict";
(() => {
const ic = (d) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const I = {
  go: '<path d="M9 6l6 6-6 6"/>', cal: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', book: '<path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4z"/><path d="M5 16a4 4 0 0 1 4-4h10"/>',
  bolt: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>', key: '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 11h.01M11 11h.01M15 11h.01M8 14h8"/>',
  flag: '<path d="M5 21V4h11l-2 4 2 4H5"/>', link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
};
const kbd = (k) => (k || []).map((x) => `<kbd>${x}</kbd>`).join("");
const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const typing = (el) => /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable;
const TABS = [["inbox", "Inbox", ["G", "I"]], ["agent", "Make a post", ["G", "M"]], ["library", "Library", ["G", "L"]], ["take", "One take", ["G", "T"]], ["planner", "Planner", ["G", "P"]],
  ["news", "News", ["G", "N"]], ["brand", "Brand", ["G", "B"]], ["apps", "AI apps", ["G", "A"]], ["activity", "Activity", ["G", "Y"]], ["settings", "Settings", ["G", "S"]]];
// jobs that live inside a screen, findable by their old names too
const SUBS = [["inbox", "", "Approvals", "approve agent posts"], ["agent", "quick", "Quick text posts", "write from a link"], ["news", "scan", "Story scan", "sources agent"],
  ["news", "sources", "News sources", "follow a feed"], ["brand", "templates", "Brand templates", "examples, recurring slots"], ["brand", "ledger", "Style Ledger", "rules from corrections"],
  ["settings", "", "AI engine", "your own model key"]];
const VIEWS = ["week", "month", "day", "list", "board", "grid"];
const DOCS = [["getting-started", "Getting started"], ["connect-buffer", "Connect Buffer"], ["brand-kit", "Brand kit and templates"], ["sources-agent", "Sources agent"],
  ["inbox", "Inbox"], ["planner", "Planner views"], ["one-take", "One take"], ["agents", "Agents, MCP and API"], ["security", "Security and privacy"], ["faq", "FAQ"]];
const hasTab = (t) => !!document.getElementById("tab-" + t);

// ---- toasts with Undo, in the existing live region (#toast keeps its test id and role=status)
window.toastUndo = function (msg, undo, ms = 6000) {
  const t = document.getElementById("toast"); if (!t) return;
  t.innerHTML = ""; const p = document.createElement("span"); p.textContent = msg; t.append(p);
  if (undo) { const b = document.createElement("button"); b.type = "button"; b.className = "toast-undo"; b.textContent = "Undo"; b.dataset.testid = "toast-undo";
    b.onclick = () => { t.hidden = true; undo(); }; t.append(b); }
  t.hidden = false; clearTimeout(window.toast._t); window.toast._t = setTimeout(() => (t.hidden = true), ms);
};

// ---- the palette and the shortcuts sheet (native dialogs: focus trap, Esc and backdrop for free)
document.body.insertAdjacentHTML("beforeend", `
<dialog class="cmdk" id="cmdk" aria-label="Command palette" data-testid="cmdk">
  <div class="q">${ic('<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>')}<label class="sr" for="cmdkQ">Search commands, screens and docs</label>
    <input id="cmdkQ" type="text" role="combobox" aria-expanded="true" aria-controls="cmdkL" aria-autocomplete="list" placeholder="Type a command, a screen or a doc" autocomplete="off" spellcheck="false" data-testid="cmdk-input"><kbd>Esc</kbd></div>
  <ul id="cmdkL" role="listbox" aria-label="Results" data-testid="cmdk-list"></ul>
  <footer><span>${kbd(["↑", "↓"])} move</span><span>${kbd(["Enter"])} open</span><span>${kbd(["?"])} all shortcuts</span></footer>
</dialog>
<dialog class="sheet" id="keysSheet" aria-labelledby="keysH" data-testid="shortcuts">
  <h2 id="keysH">Keyboard shortcuts</h2>
  <dl class="keys">${[[[mac ? "⌘" : "Ctrl", "K"], "Command palette (also /)"], [["G", "I"], "Go to Inbox"], [["G", "P"], "Go to Planner"], [["G", "T"], "Go to One take"], [["G", "M"], "Make a post"],
    [["N"], "New post in the Planner"], [["1"], "Planner: Week (2 Month, 3 Day, 4 List, 5 Board, 6 Grid)"], [["T"], "Planner: today"], [["←", "→"], "Planner: previous or next"], [["?"], "This list"], [["Esc"], "Close"]]
    .map(([k, d]) => `<dt>${kbd(k)}</dt><dd>${d}</dd>`).join("")}</dl>
  <form method="dialog" class="sheet-act"><a class="link" href="/docs/getting-started">Read the guide</a><button class="btn2 sm" data-testid="shortcuts-close">Close</button></form>
</dialog>`);
const dlg = document.getElementById("cmdk"), q = document.getElementById("cmdkQ"), ul = document.getElementById("cmdkL");
function cmds() {
  const out = [];
  for (const [t, name, keys] of TABS) if (hasTab(t)) out.push({ g: "Go to", i: I.go, label: name, keys, run: () => showTab(t) });
  for (const [t, sub, name, hint] of SUBS) if (hasTab(t)) out.push({ g: "Go to", i: I.go, label: name, hint, run: () => showTab(t, sub) });
  VIEWS.forEach((v, n) => out.push({ g: "Planner views", i: I.cal, label: `Planner: ${v[0].toUpperCase() + v.slice(1)}`, keys: [String(n + 1)], run: () => window.plSetView ? plSetView(v) : showTab("planner") }));
  out.push({ g: "Do", i: I.plus, label: "New post", hint: "in the Planner", keys: ["N"], run: () => { showTab("planner"); setTimeout(() => window.plAct && plAct("new"), 50); } });
  out.push({ g: "Do", i: I.link, label: "Write posts from a link", hint: "paste any article", run: () => { showTab("agent", "quick"); setTimeout(() => document.getElementById("urlInput")?.focus(), 50); } });
  if (hasTab("take")) out.push({ g: "Do", i: I.go, label: "Try the sample take", hint: "one talk, three clips", run: () => { showTab("take"); setTimeout(() => document.getElementById("tkSample")?.click(), 50); } });
  out.push({ g: "Do", i: I.bolt, label: "Pause or resume every agent", hint: "kill switch", run: () => document.getElementById("killPill")?.click() });
  out.push({ g: "Help", i: I.flag, label: "Take the tour", run: () => window.tourStart && tourStart() });
  out.push({ g: "Help", i: I.key, label: "Keyboard shortcuts", keys: ["?"], run: () => document.getElementById("keysSheet").showModal() });
  for (const [slug, name] of DOCS) out.push({ g: "Docs", i: I.book, label: `Docs: ${name}`, run: () => (location.href = "/docs/" + slug) });
  return out;
}
const score = (needle, hay) => { // subsequence match, bonus for word starts and runs; 0 = no match
  needle = needle.toLowerCase(); hay = hay.toLowerCase(); if (!needle) return 1; let i = 0, sc = 0, prev = -2;
  for (let j = 0; j < hay.length && i < needle.length; j++) if (hay[j] === needle[i]) { sc += (j === prev + 1 ? 3 : 1) + (j === 0 || hay[j - 1] === " " ? 4 : 0); prev = j; i++; }
  return i === needle.length ? sc : 0;
};
let items = [], sel = 0;
function render() {
  const all = cmds();
  items = q.value ? all.map((c) => ({ c, s: score(q.value, c.label + " " + (c.hint || "")) })).filter((x) => x.s).sort((a, b) => b.s - a.s).slice(0, 14).map((x) => x.c) : all;
  sel = Math.min(sel, Math.max(0, items.length - 1));
  let g = "", h = "";
  items.forEach((c, n) => { if (c.g !== g && !q.value) { g = c.g; h += `<li class="g" role="presentation">${esc(g)}</li>`; }
    h += `<li role="option" id="co${n}" aria-selected="${n === sel}" data-n="${n}" data-testid="cmdk-item">${ic(c.i)}<span>${esc(c.label)}</span>${c.hint ? `<span class="h">${esc(c.hint)}</span>` : ""}<span class="k">${kbd(c.keys)}</span></li>`; });
  ul.innerHTML = h || `<li class="none" role="presentation">Nothing matches. Try “planner”, “tour” or “docs”.</li>`;
  q.setAttribute("aria-activedescendant", items.length ? `co${sel}` : "");
  ul.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}
function openPalette() { if (dlg.open) return; q.value = ""; sel = 0; render(); dlg.showModal(); q.focus(); }
window.openPalette = openPalette;
q.addEventListener("input", () => { sel = 0; render(); });
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % Math.max(1, items.length); render(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + items.length) % Math.max(1, items.length); render(); }
  else if (e.key === "Enter" && items[sel]) { e.preventDefault(); const c = items[sel]; dlg.close(); c.run(); }
});
ul.addEventListener("click", (e) => { const li = e.target.closest("[data-n]"); if (li) { const c = items[+li.dataset.n]; dlg.close(); c.run(); } });
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
document.getElementById("cmdkBtn")?.addEventListener("click", openPalette);
const kb = document.querySelector("#cmdkBtn .kb"); if (kb) kb.textContent = mac ? "⌘K" : "Ctrl K";

// ---- Help menu: the tour and the docs, reachable from every screen
const hb = document.getElementById("helpBtn"), hm = document.getElementById("helpMenu");
function helpOpen(open) { hm.hidden = !open; hb.setAttribute("aria-expanded", open); if (open) hm.querySelector("[role=menuitem]").focus(); }
hb?.addEventListener("click", (e) => { e.stopPropagation(); helpOpen(hm.hidden); });
document.addEventListener("click", (e) => { if (hm && !hm.hidden && !hm.contains(e.target)) helpOpen(false); });
hm?.addEventListener("keydown", (e) => {
  const its = [...hm.querySelectorAll("[role=menuitem]")], i = its.indexOf(document.activeElement);
  if (e.key === "Escape") { helpOpen(false); hb.focus(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); its[(i + 1) % its.length].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); its[(i - 1 + its.length) % its.length].focus(); }
});
document.getElementById("helpTour")?.addEventListener("click", () => { helpOpen(false); window.tourStart && tourStart(); });
document.getElementById("helpKeys")?.addEventListener("click", () => { helpOpen(false); document.getElementById("keysSheet").showModal(); });

// ---- global keys (ignored while typing, with a modifier, or under an open dialog)
let g = false, gT;
addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]") || document.querySelector(".tour")) return;
  if (document.getElementById("app")?.hidden) return;
  const k = e.key;
  if (k === "/") { e.preventDefault(); openPalette(); return; }
  if (k === "?") { e.preventDefault(); document.getElementById("keysSheet").showModal(); return; }
  if (k.toLowerCase() === "g" && !g) { g = true; clearTimeout(gT); gT = setTimeout(() => (g = false), 900); return; }
  if (g) { g = false; const hit = TABS.find((t) => t[2][1] === k.toUpperCase() && hasTab(t[0])); if (hit) { e.preventDefault(); showTab(hit[0]); } return; }
  const inPlanner = !document.getElementById("tab-planner")?.hidden;
  if (inPlanner && /^[1-6]$/.test(k) && window.plSetView) { e.preventDefault(); plSetView(VIEWS[+k - 1]); return; }
  if (inPlanner && k.toLowerCase() === "t") { e.preventDefault(); document.getElementById("plToday")?.click(); return; }
  if (inPlanner && (k === "ArrowLeft" || k === "ArrowRight") && !e.target.closest(".seg,[role=grid]")) { e.preventDefault(); document.getElementById(k === "ArrowLeft" ? "plPrev" : "plNext")?.click(); return; }
  if (k.toLowerCase() === "n") { e.preventDefault(); showTab("planner"); setTimeout(() => window.plAct && plAct("new"), 50); }
});
})();
