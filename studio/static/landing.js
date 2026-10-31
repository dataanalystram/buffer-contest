// HuhQueue landing (Hero C). Vanilla, no dependencies, no network calls except this server's /api/session and /api/stats.
// Motion is CSS (scroll timelines); this file only measures, fills the product pieces and wires four small controls.
"use strict";
(() => {
document.documentElement.classList.add("js");
const $ = (s) => document.querySelector(s);
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const vt = (fn) => (document.startViewTransition && !reduce()) ? document.startViewTransition(fn) : fn();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toast(msg, ms = 6000) {
  let t = $("#ltoast");
  if (!t) { t = document.createElement("div"); t.id = "ltoast"; t.className = "toast-l"; t.setAttribute("role", "status"); t.dataset.testid = "toast"; document.body.append(t); }
  t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms);
}

// ---- product pieces: caption rows counted Buffer's way (X counts a URL as 23, LinkedIn as 24) and next week's strip
const CAPS = [
  ["X", "X", "OpenAI, Google, Microsoft and Anthropic will honour one opt-out rule for AI scraping. What it means for your blog, in 3 steps. https://brightdesk.news/opt-out", 280],
  ["in", "LinkedIn", "Publishers finally get one switch for AI training. Here is what the new opt-out standard covers, and the one line to add to your site this week. https://brightdesk.news/opt-out", 3000],
  ["IG", "Instagram", "One rule, every big AI crawler. Swipe for the 3 steps. Link in bio.", 2200],
];
const cnt = (t, net) => { const u = net === "X" ? 23 : net === "LinkedIn" ? 24 : 0; return u ? t.replace(/https?:\/\/\S+/g, "x".repeat(u)).length : [...t].length; };
document.querySelectorAll("[data-caps]").forEach((ul) => {
  ul.innerHTML = CAPS.map(([m, net, t, lim]) => `<li><span class="cav" aria-hidden="true">B<i>${m}</i></span><p>${esc(t)}</p><span class="n">${cnt(t, net)} of ${lim.toLocaleString("en")} <b>fits</b></span></li>`).join("");
  ul.setAttribute("aria-label", "Captions per channel: " + CAPS.map(([, net, t, lim]) => `${net} ${cnt(t, net)} of ${lim}`).join(", "));
});
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const PIECES = { 1: ["sq kit", "Opt out <i>in 3 steps</i>", "Tue 08:15", "Instagram carousel"], 3: ["tall kit", "opt out of every crawler", "Thu 17:00", "Instagram Reel"], 5: ["txt", "OpenAI, Google, Microsoft and Anthropic will honour one opt-out rule", "Sat 09:00", "X thread"] };
const OPEN = { 0: "17:00", 2: "08:15", 4: "11:00", 6: "08:15" };
const nextMon = (() => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7)); return d; })();
document.querySelectorAll("[data-week]").forEach((wk) => {
  let i = 0;
  wk.innerHTML = DAYS.map((d, k) => { const p = PIECES[k], day = new Date(nextMon); day.setDate(nextMon.getDate() + k);
    return `<div class="wd"><span class="h">${d}</span><b class="dn">${day.getDate()}</b>${p ? `<article class="piece" data-i="${i++}"><div class="m ${p[0]}">${p[1]}</div><span class="when">${p[2]}<br>${p[3]}</span><span class="st approval">Needs approval</span></article>` : `<span class="open">${OPEN[k]} open</span>`}</div>`; }).join("");
});

// ---- the thread: from the paste field's right edge to the page edge and down off the hero. Pure SVG.
function thread() {
  const svg = $("#thread"), hero = svg.parentElement, f = $("#paste"), path = $("#threadP");
  const H = hero.getBoundingClientRect(), r = f.getBoundingClientRect();
  const x0 = r.right - H.left + 2, y0 = r.top - H.top + r.height / 2, xe = document.documentElement.clientWidth - H.left - 40, ye = H.height + 40;
  svg.setAttribute("width", Math.max(1, xe + 4)); svg.setAttribute("height", ye + 4);
  if (xe - x0 < 60) { path.setAttribute("d", ""); return; }
  path.setAttribute("d", `M${x0} ${y0} H${xe - 24} Q${xe} ${y0} ${xe} ${y0 + 24} V${ye}`);
  const b = document.querySelectorAll(".bead"), run = xe - 24 - x0;
  [0.2, 0.5, 0.8].forEach((p, i) => { b[i].style.left = `${x0 + run * p}px`; b[i].style.top = `${y0}px`; });
  b[3].style.left = `${xe}px`; b[3].style.top = `${y0 + (ye - y0) * 0.62}px`;
}

// ---- the chapter: measure each station and write the stop that shows it whole (the 75% clipping bug: a linear slide
// left the carousel half off the left edge and the week half off the right; now the track holds on one station at a time)
function stops() {
  const track = $("#track"); if (!track) return;
  const vw = document.documentElement.clientWidth, tw = track.scrollWidth, min = Math.min(0, vw - tw);
  track.querySelectorAll(".station").forEach((s, i) => {
    const c = s.offsetLeft + s.offsetWidth / 2;
    let x = i === 0 ? 0 : vw / 2 - c;
    x = Math.max(min, Math.min(0, x));
    track.style.setProperty(`--x${i + 1}`, `${Math.round(x)}px`);
  });
}
function layout() { thread(); stops(); }
layout(); addEventListener("resize", layout); document.fonts?.ready.then(layout);

// ---- reveal once (content is visible without JS and under reduced motion)
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("seen"); io.unobserve(e.target); } }), { rootMargin: "0px 0px -15%" });
  document.querySelectorAll("[data-reveal]").forEach((el) => io.observe(el));
} else document.querySelectorAll("[data-reveal]").forEach((el) => el.classList.add("seen"));

// ---- beat 5: a live preflight card. The count is computed here, X's way (any URL counts 23).
const POST = "OpenAI, Google, Microsoft and Anthropic will honour one opt-out rule for AI scraping. Publishers add one line to robots.txt and every big crawler stays away from training on their pages. Here is what it covers, what it does not, and the three steps to take on your blog this week before the crawlers come back around. https://brightdesk.news/opt-out";
const xc = (t) => t.replace(/https?:\/\/\S+/g, "x".repeat(23)).length;
const split = (t) => { const s = t.match(/[^.]+\.(\s|$)|\S+$/g).map((x) => x.trim()); const out = [""]; for (const p of s) { const c = out[out.length - 1]; if (xc(c ? c + " " + p : p) <= 270) out[out.length - 1] = c ? c + " " + p : p; else out.push(p); } return out.map((p, i, a) => `${p} ${i + 1}/${a.length}`); };
let fixed = false;
function xdraw() {
  const parts = fixed ? split(POST) : [POST];
  $("#xparts").innerHTML = parts.map((p) => { const n = xc(p), bad = n > 280;
    return `<div class="part">${esc(p).replace(/(https?:\/\/\S+)/, '<span class="lnk">$1</span>')}</div><div class="meter ${bad ? "bad" : "good"}"><span class="bar"><i style="--p:${Math.min(1, n / 280).toFixed(3)}"></i></span><b>${n} of 280</b><span>${bad ? "too long" : "fits"}</span></div>`; }).join("");
  $("#xnote").textContent = fixed ? `Split into a thread of ${parts.length}, on sentence ends.` : "X counts this link as 23 characters.";
  const b = $("#xfix"); b.textContent = fixed ? "Undo fix" : "Fix it"; b.className = fixed ? "btn2 sm" : "btn sm";
}
$("#xfix").onclick = () => { fixed = !fixed; vt(xdraw); };
xdraw();

// ---- beat 6: the Planner view switch swaps real captures (radiogroup, roving focus, one sliding indicator)
const CAP = { week: "<b>Week.</b> Every post a card with its channel, media and status. Open posting times show as dashed slots.", month: "<b>Month.</b> The whole month at a glance, sent posts and the plan ahead.", list: "<b>List.</b> Grouped by day, with every action on the card.", board: "<b>Board.</b> One column per status, from draft to sent. Failed posts carry Buffer’s reason.", grid: "<b>Grid.</b> How the Instagram feed will look, planned posts dashed with their times." };
const ALT = { week: "The Planner week view", month: "The Planner month view", list: "The Planner list view, grouped by day", board: "The Planner board view with one column per status", grid: "The Planner grid view: an Instagram feed preview with planned tiles dashed" };
function seg(el, onChange) {
  el.classList.add("js");
  const ind = document.createElement("span"); ind.className = "ind"; ind.setAttribute("aria-hidden", "true"); el.prepend(ind);
  const btns = [...el.querySelectorAll("button")];
  const place = () => { const b = btns.find((x) => x.getAttribute("aria-checked") === "true") || btns[0]; ind.style.setProperty("--x", `${b.offsetLeft}px`); ind.style.setProperty("--w", `${b.offsetWidth}px`); };
  const pick = (b, focus) => { btns.forEach((x) => { const on = x === b; x.setAttribute("aria-checked", on); x.tabIndex = on ? 0 : -1; }); place(); if (focus) b.focus(); onChange(b.dataset.v); };
  btns.forEach((b) => { b.addEventListener("click", () => pick(b)); b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1; });
  el.addEventListener("keydown", (e) => { const i = btns.indexOf(document.activeElement); if (i < 0) return;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); pick(btns[(i + 1) % btns.length], true); }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); pick(btns[(i - 1 + btns.length) % btns.length], true); } });
  addEventListener("resize", place); document.fonts?.ready.then(place); place();
}
seg($("#plseg"), (v) => vt(() => {
  $("#plpic source").srcset = `/static/land/planner-${v}-dark.webp`;
  const img = $("#plimg"); img.src = `/static/land/planner-${v}-light.webp`; img.alt = ALT[v]; $("#plcap").innerHTML = CAP[v];
}));

// ---- beat 8: copy the MCP command
$("#copyB").onclick = async () => { try { await navigator.clipboard.writeText($("#mcp").textContent); } catch (_) { /* clipboard blocked: the text stays selectable */ }
  const b = $("#copyB"); b.textContent = "Copied"; $("#copyS").textContent = "Command copied"; setTimeout(() => { b.textContent = "Copy"; }, 1600); };

// ---- server state: an error from Buffer's sign-in, an unconfigured server, live counters (real rows only)
const p = new URLSearchParams(location.search);
if (p.get("error")) { toast("Buffer: " + p.get("error")); history.replaceState({}, "", "/"); }
fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json()).then((s) => {
  if (s.repo) $("#repoLink").href = s.repo;
  if (s.connected) { location.replace("/"); return; }
  if (!s.configured) { $("#configNote").hidden = false; $("#connectBtn").classList.add("off"); $("#connectBtn").setAttribute("aria-disabled", "true"); }
}).catch(() => {});
fetch("/api/stats").then((r) => r.json()).then((st) => {
  const n = st.buffer_accounts, m = st.posts_sent_to_buffer; if (!n) return;
  const el = $("#landStats");
  el.innerHTML = `<span><b>${n.toLocaleString()}</b> Buffer account${n === 1 ? "" : "s"} connected</span><span><b>${m.toLocaleString()}</b> post${m === 1 ? "" : "s"} sent to Buffer</span><span><b>${(st.sample_workspaces_open || 0).toLocaleString()}</b> trying the sample today</span>`;
  el.hidden = false;
}).catch(() => {});
})();
