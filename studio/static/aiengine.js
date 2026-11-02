// Settings > AI engine (studio/ai_engines.py, byo_keys.py, ai_logs.py) and the Make a post composer's "Model" switch.
//  - Engine cards: logo, name, Free/Paid/Your key badge, status dot, usage bar ("3 of 150 calls today", "$0.34 of $1"),
//    a selected state; "Automatic" first, explained in one line with an (i) popover (GET/PUT /api/ai/engine).
//  - Your keys: one row per provider, masked hint, add (tested on save) / replace / delete (GET/POST/DELETE /api/ai/keys).
//  - Logs: recent model calls, filterable and paged (GET /api/ai/logs; MCP get_ai_logs is the same data).
"use strict";
(() => {
const E = { st: null, busy: false, keys: null, logs: { page: 1, limit: 25 } };
const q = (s) => document.querySelector(s);
const escH = (s) => (window.esc ? window.esc(s) : String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
const mark = (id, label) => (window.brandMark ? window.brandMark(id, label ? { label } : {}) : "");
const AUTO = { id: "auto", label: "Automatic", short: "Auto", logo: "spark", tier: "free", available: true, credit: null };
const TIER = { free: "Free", paid: "Paid", "your key": "Your key" };

function usage(o) {
  const u = o.usage;
  if (!u) return `<span class="ai-u-none">${escH(o.credit && o.credit.kind === "own" ? "Billed by your provider" : o.id === "free" ? "No charge" : "")}</span>`;
  const pct = Math.max(0, Math.min(100, u.pct || 0));
  return `<span class="ai-u" data-testid="ai-engine-usage-${o.id}"><span class="ai-u-l">${escH(u.label)}</span>
    <span class="ai-eng-bar ${u.level || "ok"}" role="meter" aria-label="${escH(o.short)} usage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></span></span>`;
}
function card(o, sel) {
  const on = sel === o.id;
  const dis = o.id === "own" && !o.available;  // no key saved: nothing to switch to
  const dot = on && o.available ? "sel" : o.available ? "ok" : "off";
  const st = on && o.available ? "Selected" : on ? "Selected, unavailable" : o.available ? "Ready" : "Unavailable";
  const sub = o.id === "auto" ? (E.st ? E.st.order_labels.slice(0, 3).join(" → ") + (E.st.order_labels.length > 3 ? " → …" : "") : "")
    : (o.model || "");
  return `<button type="button" role="radio" class="ai-c${on ? " on" : ""}${o.available ? "" : " na"}" data-eng="${o.id}" aria-checked="${on}"
      data-testid="ai-engine-${o.id}" ${dis ? "disabled" : ""} tabindex="${on ? 0 : -1}" title="${escH(o.about || "")}">
    <span class="ai-c-top">${mark(o.logo)}<span class="ai-c-name"><b>${escH(o.id === "auto" ? "Automatic" : o.short)}</b><small>${escH(sub)}</small></span>
      <span class="ai-c-tier t-${(o.tier || "").replace(" ", "-")}">${escH(TIER[o.tier] || "")}</span></span>
    <span class="ai-c-st"><i class="ai-dot ${dot}" aria-hidden="true"></i><span data-testid="ai-engine-status-${o.id}">${escH(st)}</span></span>
    ${o.id === "auto" ? `<span class="ai-u-none">Falls back on its own, with a note in the run</span>` : usage(o)}
    ${o.credit && o.credit.label ? `<span class="sr" data-testid="ai-engine-credit-${o.id}">${escH(o.credit.label)}</span>` : ""}
    ${!o.available && o.reason ? `<span class="ai-c-why">${escH(o.reason)}</span>` : ""}</button>`;
}
function paintSettings(st) {
  const box = q("#aiEngines"); if (!box) return;
  const sel = st.selected || "auto";
  box.innerHTML = card(AUTO, sel) + st.engines.map((o) => card(o, sel)).join("");
  const tip = q("#aiAutoTip");
  if (tip) tip.innerHTML = `<b>Automatic</b><p>${escH(st.auto_line || "")}</p><p>Pick an engine to put it first; if it runs out or doesn't answer, the next one takes over.</p>`;
  const runs = st.engines.find((o) => o.id === st.runs);
  const sub = q("#aiEngSub");
  if (sub) sub.innerHTML = runs ? `Now running: <b data-testid="ai-engine-runs">${escH(runs.short)}</b>${sel === "auto" ? " (Automatic)" : ""}.`
    : "No engine is ready: the built-in writer runs.";
  const pools = [...(st.free_pools || []).map((p) => p && p.label), st.claude_pool && st.claude_pool.label].filter(Boolean);
  q("#aiEngPools").textContent = pools.length ? "App-wide today: " + pools.map((x) => x.replace(/ today:/, ":").replace(/^App-wide /, "")).join(" · ") : "";
  let keys = q("#aiEngKeys");
  if (!keys) { keys = document.createElement("p"); keys.id = "aiEngKeys"; keys.className = "muted xs ai-eng-keys"; keys.dataset.testid = "ai-engine-keys"; q("#aiEngPools").after(keys); }
  const links = st.engines.filter((o) => o.link && !o.available);
  keys.hidden = !links.length;
  keys.innerHTML = links.map((o) => `${escH(o.short)}: <a class="link" href="${escH(o.link.url)}" target="_blank" rel="noopener" data-testid="ai-engine-key-link-${o.id}">${escH(o.reason)}</a> (or add your own key below).`).join(" ");
  const note = q("#aiEngNote");
  note.hidden = !st.note; note.textContent = st.note || "";
  const btns = [...box.querySelectorAll(".ai-c")];
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
    : o.credit.kind === "free-credit" ? ` · free · ${o.credit.left_calls} left today`
    : o.credit.kind === "own" ? " · your key" : o.credit.kind === "free" ? " · free" : "";
  s.innerHTML = `<option value="auto">Auto</option>` + st.engines.filter((o) => o.id !== "own" || o.available).map((o) =>
    `<option value="${o.id}"${o.available ? "" : " disabled"}>${escH(o.short)}${o.available ? left(o) : " (unavailable)"}</option>`).join("");
  s.value = [...s.options].some((x) => x.value === sel) ? sel : "auto";
  const runs = st.engines.find((o) => o.id === st.runs);
  const note = q("#agEngNote");
  if (note) { note.hidden = !st.note; note.textContent = st.note || ""; }
  if (runs && q("#agEngine")) q("#agEngine").textContent = `Writing with: ${runs.short}`;
}
function paint(st) { E.st = st; paintSettings(st); paintComposer(st); if (window.HQ_INTEGRATIONS) window.HQ_INTEGRATIONS.redraw(); }
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
    if (window.toast) toast(st.note || `Your agent now uses ${o ? (o.short || o.label) : id}`, st.note ? 6000 : 3000);
    const f = q(`[data-eng="${id}"]`); if (f && document.activeElement && document.activeElement.closest && document.activeElement.closest("#aiEngines")) f.focus();
    if (window.HQ_BRAIN_SWAP) window.HQ_BRAIN_SWAP.refresh();
  } catch (e) { if (window.toast) toast(e.message || "Couldn't change the model", 5000); refresh(); }
  finally { E.busy = false; }
}

// ------------------------------------------------------------------ Your keys
const ENGINE_NAME = { claude: "Claude", "gemini-studio": "Gemini (AI Studio)", nvidia: "NVIDIA", hf: "Hugging Face", own: "Your key" };
function keyRow(k) {
  const id = `aik-${k.provider}`;
  return `<li class="ai-k${k.saved ? " saved" : ""}" data-testid="ai-key-${k.provider}">
    <div class="ai-k-main">${mark(k.logo)}<span class="ai-k-name"><b>${escH(k.label)}</b>
      <small>Runs ${escH(ENGINE_NAME[k.engine] || k.engine)}${k.saved && k.model ? " · " + escH(k.model) : ""}</small></span>
      <span class="ai-k-st" data-testid="ai-key-status-${k.provider}">${k.saved ? `<i class="ai-dot ok" aria-hidden="true"></i>Saved <code>${escH(k.hint)}</code>` : `<i class="ai-dot off" aria-hidden="true"></i>Not added`}</span>
      <span class="ai-k-btns"><button class="btn2 sm" type="button" data-k-open="${k.provider}" aria-expanded="false" aria-controls="${id}" data-testid="ai-key-open-${k.provider}">${k.saved ? "Replace" : "Add"}</button>
      ${k.saved ? `<button class="btn2 sm danger" type="button" data-k-del="${k.provider}" data-testid="ai-key-delete-${k.provider}">Delete</button>` : ""}</span></div>
    <form class="ai-k-form" id="${id}" data-provider="${k.provider}" hidden autocomplete="off">
      <div class="ai-k-fields">
        <label class="field"><span class="label">API key</span><input type="password" name="api_key" autocomplete="off" spellcheck="false" required data-testid="ai-key-input-${k.provider}" placeholder="${k.saved ? "paste a new key to replace " + escH(k.hint) : "paste your key"}"></label>
        <label class="field"><span class="label">Model <small class="muted">(optional)</small></span><input name="model" placeholder="${escH(k.default_model || "")}" value="${escH(k.model || "")}" data-testid="ai-key-model-${k.provider}"></label>
        ${k.needs_base_url ? `<label class="field"><span class="label">Base URL</span><input name="base_url" placeholder="https://…/v1" value="${escH(k.base_url || "")}" data-testid="ai-key-base-${k.provider}"></label>` : ""}
      </div>
      <div class="row tight"><button class="btn sm" type="submit" data-testid="ai-key-save-${k.provider}">Test and save</button>
        ${k.get_key_url ? `<a class="link sm" href="${escH(k.get_key_url)}" target="_blank" rel="noopener">Get a key ↗</a>` : ""}
        <span class="ai-k-msg sm" role="status" data-testid="ai-key-msg-${k.provider}"></span></div>
    </form></li>`;
}
function paintKeys(v) {
  E.keys = v;
  const box = q("#aiKeys"); if (!box) return;
  box.innerHTML = v.keys.map(keyRow).join("");
  box.querySelectorAll("[data-k-open]").forEach((b) => (b.onclick = () => {
    const f = document.getElementById(b.getAttribute("aria-controls"));
    f.hidden = !f.hidden; b.setAttribute("aria-expanded", String(!f.hidden));
    if (!f.hidden) f.querySelector("input").focus();
  }));
  box.querySelectorAll("[data-k-del]").forEach((b) => (b.onclick = async () => {
    const p = b.dataset.kDel;
    if (!(await hqConfirm("Delete this key? HuhQueue's own key (if any) runs that engine again.", { ok: "Delete key", danger: true }))) return;
    try { const r = await api(`/api/ai/keys/${p}`, { method: "DELETE" }); paintKeys(r); if (r.engine) paint(r.engine); toast("Key deleted"); }
    catch (e) { toast(e.message, 5000); }
  }));
  box.querySelectorAll(".ai-k-form").forEach((f) => f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const p = f.dataset.provider, msg = f.querySelector(".ai-k-msg"), btn = f.querySelector("[type=submit]");
    const body = Object.fromEntries(new FormData(f).entries());
    btn.disabled = true; btn.textContent = "Testing…"; msg.textContent = ""; msg.className = "ai-k-msg sm";
    try {
      const r = await api(`/api/ai/keys/${p}`, { method: "POST", body });
      paintKeys(r); if (r.engine) paint(r.engine);
      toast("Key works and is saved");
    } catch (e) { msg.textContent = e.message; msg.className = "ai-k-msg sm bad"; btn.disabled = false; btn.textContent = "Test and save"; }
  }));
}
async function loadKeys() {
  try { paintKeys(await api("/api/ai/keys")); } catch (e) { const b = q("#aiKeys"); if (b) b.innerHTML = `<li class="muted sm">${escH(e.message || "Keys didn't load.")}</li>`; }
}

// ------------------------------------------------------------------ Logs
const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
const ms = (v) => (typeof v === "number" ? (v >= 1000 ? (v / 1000).toFixed(1) + " s" : v + " ms") : "–");
const usd = (v) => (typeof v === "number" ? (v === 0 ? "free" : "$" + (v < 0.01 ? v.toFixed(4) : v.toFixed(2))) : "–");
const tok = (a, b) => (a == null && b == null ? "–" : `${a ?? 0} / ${b ?? 0}`);
function logRow(r) {
  const why = [r.fallback && `After: ${r.fallback}`, r.error && `Error: ${r.error}`].filter(Boolean).join(" · ");
  return `<tr data-testid="ai-log-row" class="${r.ok ? "" : "err"}">
    <td data-l="Time"><time datetime="${new Date(r.ts * 1000).toISOString()}" title="${escH(new Date(r.ts * 1000).toLocaleString())}">${escH(ago(r.ts))}</time></td>
    <td data-l="Engine"><span class="ai-log-eng">${mark(({ "gemini-studio": "gemini", hf: "huggingface", claude: "claude", nvidia: "nvidia", own: "key", free: "spark", client: "mcp" })[r.engine] || "spark")}${escH(r.engine_label)}</span></td>
    <td data-l="Model"><code>${escH(r.model || "–")}</code></td>
    <td data-l="Task">${escH(r.task || "–")}</td>
    <td data-l="Latency" class="num">${escH(ms(r.latency_ms))}</td>
    <td data-l="Tokens" class="num">${escH(tok(r.tokens_in, r.tokens_out))}</td>
    <td data-l="Cost" class="num">${escH(usd(r.cost_usd))}</td>
    <td data-l="Status"><span class="ai-log-st ${r.ok ? "ok" : "bad"}">${r.ok ? "OK" : "Error"}</span>${why ? `<span class="ai-log-why">${escH(why)}</span>` : ""}</td></tr>`;
}
function paintLogs(d) {
  const box = q("#aiLogs"); if (!box) return;
  const eng = q("#aiLogEng"), task = q("#aiLogTask");
  const keep = (sel, opts, first) => { const v = sel.value; sel.innerHTML = `<option value="">${first}</option>` + opts.map(([k, l]) => `<option value="${escH(k)}">${escH(l)}</option>`).join(""); sel.value = [...sel.options].some((o) => o.value === v) ? v : ""; };
  keep(eng, d.facets.engines.map((x) => [x.id, x.label]), "All engines");
  keep(task, d.facets.tasks.map((x) => [x, x]), "All tasks");
  const s = d.summary;
  q("#aiLogSum").textContent = s.calls ? `${s.calls} call${s.calls === 1 ? "" : "s"} · ${s.errors} error${s.errors === 1 ? "" : "s"}${s.avg_latency_ms != null ? " · avg " + ms(s.avg_latency_ms) : ""}${s.cost_usd ? " · $" + s.cost_usd.toFixed(2) : ""}` : "";
  box.innerHTML = d.items.length ? `<table class="ai-log-t"><thead><tr><th>Time</th><th>Engine</th><th>Model</th><th>Task</th><th class="num">Latency</th><th class="num">Tokens in/out</th><th class="num">Cost</th><th>Status</th></tr></thead>
    <tbody>${d.items.map(logRow).join("")}</tbody></table>`
    : `<p class="muted sm ai-logs-empty" data-testid="ai-logs-empty">${d.total === 0 && !Object.values(d.filters).some(Boolean) ? "No AI calls yet. Make a post and each model call shows up here." : "No calls match these filters."}</p>`;
  q("#aiLogPage").textContent = d.total ? `Page ${d.page} of ${d.pages}` : "";
  q("#aiLogPrev").disabled = d.page <= 1; q("#aiLogNext").disabled = d.page >= d.pages;
}
async function loadLogs(page) {
  if (!q("#aiLogs")) return;
  E.logs.page = page || 1;
  const p = new URLSearchParams({ page: E.logs.page, limit: E.logs.limit, engine: q("#aiLogEng").value, status: q("#aiLogSt").value, task: q("#aiLogTask").value, q: q("#aiLogQ").value.trim() });
  for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
  try { paintLogs(await api("/api/ai/logs?" + p.toString())); } catch (e) { q("#aiLogs").innerHTML = `<p class="muted sm">${escH(e.message || "Logs didn't load.")}</p>`; }
}
const lf = q("#aiLogF");
if (lf) {
  lf.addEventListener("submit", (ev) => { ev.preventDefault(); loadLogs(1); });
  ["#aiLogEng", "#aiLogSt", "#aiLogTask"].forEach((s) => q(s).addEventListener("change", () => loadLogs(1)));
  q("#aiLogPrev").onclick = () => loadLogs(E.logs.page - 1);
  q("#aiLogNext").onclick = () => loadLogs(E.logs.page + 1);
}

const sel = q("#agEngineSel");
if (sel) sel.addEventListener("change", () => choose(sel.value));
const TABS = (window.HS_TABS = window.HS_TABS || {});
let lastAi = 0;
function loadAll() { lastAi = Date.now(); refresh(); loadKeys(); loadLogs(E.logs.page); }
for (const t of ["ai", "agent"]) {
  const prev = TABS[t];
  TABS[t] = (...a) => { if (t === "ai") loadAll(); else refresh(); return prev && prev(...a); };
}
// Settings can open before this script has wrapped HS_TABS.ai (a deep link, a fast boot): load when the tab is shown
const setTab = document.getElementById("tab-settings");
if (setTab) {
  const seen = () => { if (!setTab.hidden && Date.now() - lastAi > 1500) loadAll(); };
  new MutationObserver(seen).observe(setTab, { attributes: true, attributeFilter: ["hidden"] });
  seen();
}
window.HQ_AI_ENGINE = { refresh, loadKeys, loadLogs, get state() { return E.st; } };
})();
