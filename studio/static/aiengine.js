// The AI engine switch (studio/ai_engines.py): Settings > AI engine shows every model with its credit in plain words
// ("Claude: $0.34 of your $1 used", "Gemini (Oracle): free during the contest, $0.00 of your $3 used") and the app-wide
// pools; the Make a post composer has a compact "Model" switch. Both read GET /api/ai/engine and save with PUT.
// A model that is out of credit or erroring hands over to the next one automatically; the note says so.
"use strict";
(() => {
const E = { st: null, busy: false };
const q = (s) => document.querySelector(s);
const escH = (s) => (window.esc ? window.esc(s) : String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
const AUTO = { id: "auto", label: "Automatic", short: "Auto", about: "Claude first, then Gemini on Oracle Cloud, your key, then the free model." };

function bar(c) {
  if (!c || c.kind === "own" || c.kind === "free") return "";
  const pct = Math.max(0, Math.min(100, c.used_pct || 0));
  return `<span class="ai-eng-bar ${c.level || "ok"}" role="meter" aria-label="Credit used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></span>`;
}
function warn(c) {
  if (!c || !c.level || c.level === "ok") return "";
  return c.level === "out" ? `<span class="ai-eng-w out">Out of credit: the next model takes over.</span>`
    : `<span class="ai-eng-w low">${c.used_pct}% used.</span>`;
}
function optHtml(o, sel) {
  const on = sel === o.id;
  const dis = o.id === "own" && !o.available;  // no key saved: nothing to switch to
  const badge = on ? `<span class="ai-eng-b on">Selected</span>` : !o.available ? `<span class="ai-eng-b off">Unavailable</span>` : "";
  return `<button type="button" role="radio" class="ai-eng-o${on ? " on" : ""}${o.available ? "" : " na"}" data-eng="${o.id}" aria-checked="${on}"
      data-testid="ai-engine-${o.id}" ${dis ? "disabled" : ""} tabindex="${on ? 0 : -1}">
    <span class="ai-eng-top"><b>${escH(o.label)}</b>${badge}</span>
    <span class="ai-eng-ab">${escH(o.about)}</span>
    ${o.credit ? `<span class="ai-eng-cr" data-testid="ai-engine-credit-${o.id}">${escH(o.credit.label)}</span>${bar(o.credit)}${warn(o.credit)}` : ""}
    ${!o.available && o.reason ? `<span class="ai-eng-why">${escH(o.reason)}</span>` : ""}</button>`;
}
function paintSettings(st) {
  const box = q("#aiEngines"); if (!box) return;
  const sel = st.selected || "auto";
  box.innerHTML = optHtml({ ...AUTO, available: true, credit: null }, sel) + st.engines.map((o) => optHtml(o, sel)).join("");
  const pools = [st.claude_pool && st.claude_pool.label, st.oracle_pool && st.oracle_pool.label].filter(Boolean);
  q("#aiEngPools").textContent = pools.join(" · ");
  const note = q("#aiEngNote");
  note.hidden = !st.note; note.textContent = st.note || "";
  const now = q("#aiNow"), runs = st.engines.find((o) => o.id === st.runs);
  if (now) now.innerHTML = runs ? `Your agent runs on <b>${escH(runs.label)}</b>${st.selected === "auto" ? " (automatic order)" : ""}.` +
      (runs.credit && runs.credit.label ? ` ${escH(runs.credit.label)}.` : "")
    : "Your agent writes with the <b>built-in writer</b> (no AI). Pick a model above, add a key, or connect Claude/ChatGPT.";
  const btns = [...box.querySelectorAll(".ai-eng-o")];
  btns.forEach((b) => (b.onclick = () => choose(b.dataset.eng)));
  box.onkeydown = (e) => {
    const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!k) return;
    e.preventDefault();
    const live = btns.filter((b) => !b.disabled);
    const i = live.indexOf(document.activeElement);
    const n = live[(i + k + live.length) % live.length];
    n.focus(); choose(n.dataset.eng);
  };
}
function paintComposer(st) {
  const s = q("#agEngineSel"); if (!s) return;
  const sel = st.selected || "auto";
  const left = (o) => !o.credit ? "" : o.credit.kind === "allowance" ? ` · $${Number(o.credit.left_usd).toFixed(2)} left`
    : o.credit.kind === "oracle" ? " · free" : o.credit.kind === "free" ? " · free" : "";
  s.innerHTML = `<option value="auto">Auto</option>` + st.engines.filter((o) => o.id !== "own" || o.available).map((o) =>
    `<option value="${o.id}"${o.available ? "" : " disabled"}>${escH(o.short)}${o.available ? left(o) : " (unavailable)"}</option>`).join("");
  s.value = [...s.options].some((x) => x.value === sel) ? sel : "auto";
  const runs = st.engines.find((o) => o.id === st.runs);
  const note = q("#agEngNote");
  if (note) { note.hidden = !st.note; note.textContent = st.note || ""; }
  if (runs && q("#agEngine")) q("#agEngine").textContent = `Writing with: ${runs.short}`;
}
function paint(st) { E.st = st; paintSettings(st); paintComposer(st); }
async function refresh() {
  try { paint(await api("/api/ai/engine")); } catch (_) { const b = q("#aiEngines"); if (b && !E.st) b.innerHTML = `<span class="muted sm">Models didn't load. The agent keeps its default order.</span>`; }
}
async function choose(id) {
  if (E.busy || !E.st || E.st.selected === id) return;
  E.busy = true;
  try {
    const st = await api("/api/ai/engine", { method: "PUT", body: { engine: id } });
    paint(st);
    const o = id === "auto" ? AUTO : st.engines.find((x) => x.id === id);
    if (window.toast) toast(st.note || `Your agent now uses ${o ? o.label : id}`, st.note ? 6000 : 3000);
    const f = q(`[data-eng="${id}"]`); if (f && document.activeElement && document.activeElement.closest && document.activeElement.closest("#aiEngines")) f.focus();
    if (window.HQ_BRAIN_SWAP) window.HQ_BRAIN_SWAP.refresh();
  } catch (e) { if (window.toast) toast(e.message || "Couldn't change the model", 5000); refresh(); }
  finally { E.busy = false; }
}
const sel = q("#agEngineSel");
if (sel) sel.addEventListener("change", () => choose(sel.value));
const TABS = (window.HS_TABS = window.HS_TABS || {});
for (const t of ["ai", "agent", "settings"]) {
  const prev = TABS[t];
  TABS[t] = (...a) => { refresh(); return prev && prev(...a); };
}
window.HQ_AI_ENGINE = { refresh, get state() { return E.st; } };
setTimeout(() => { if (window.S && S.session && S.session.connected !== false) refresh(); }, 1200);
})();
