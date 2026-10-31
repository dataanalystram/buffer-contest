// Activity log: server-side, cursor-paginated, categorised (sign-ins / AI agents / system & Buffer API), searchable, CSV.
// Uses app.js helpers ($, esc, api, toast). Same data as GET /api/activity and the MCP tool get_activity.
"use strict";
(() => {
  const A = { tab: "all", cursors: [""], at: 0, app: "", next: null };
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();
  const money = (v) => (v == null ? "" : v === 0 ? "free" : "$" + Number(v).toFixed(v < 0.01 ? 4 : 2));
  const num = (v) => (v == null ? "" : Number(v).toLocaleString());

  function filters() {
    const f = { tab: A.tab, q: $("#actQ").value.trim(), from: $("#actFrom").value, to: $("#actTo").value,
                limit: $("#actLimit").value, failed: $("#actFailed").value ? "true" : "" };
    if (A.app && A.tab === "agent") f.app = A.app;
    return f;
  }
  const qs = (f) => Object.entries(f).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

  function who(r) {
    if (r.category === "agent" || r.token_label) {
      const app = r.app_label || "Agent";
      const sub = [r.token_label, r.client_id ? `client ${r.client_id}` : "", r.surface].filter(Boolean).join(" · ");
      return `<td class="act-who"><b>${esc(app)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</td>`;
    }
    if (r.category === "signin") {
      const sub = [r.ip, (r.ua || "").slice(0, 60)].filter(Boolean).join(" · ");
      return `<td class="act-who">${esc(r.actor === "system" ? "sign-in attempt" : r.actor)}${sub ? `<small>${esc(sub)}</small>` : ""}</td>`;
    }
    return `<td class="act-who">${esc(r.actor)}${r.surface ? `<small>${esc(r.surface)}</small>` : ""}</td>`;
  }
  function model(r) {
    if (!r.model && !r.provider) return "<td></td>";
    const usage = [r.tokens_in != null ? `${num(r.tokens_in)} in` : "", r.tokens_out != null ? `${num(r.tokens_out)} out` : "", money(r.cost_usd)].filter(Boolean).join(" · ");
    return `<td class="act-model">${esc(r.model || "")}<small>${esc(r.provider || "")}${usage ? " · " + esc(usage) : ""}</small></td>`;
  }

  function groups(list) {
    if (!list || !list.length) return "";
    return `<div class="act-groups" data-testid="act-groups">${list.map((g) => `<button type="button" class="act-group${A.app === g.app ? " on" : ""}" data-app="${esc(g.app)}" data-testid="act-group">
      <h3>${esc(g.label)}</h3><p>${num(g.actions)} actions${g.failed ? ` · ${num(g.failed)} failed` : ""}</p>
      ${g.models.length ? `<p>${g.models.map((m) => esc(`${m.model || "unknown model"}${m.provider ? ` (${m.provider})` : ""} ×${m.calls}`)).join("<br>")}</p><p class="act-note" data-testid="act-model-note">model reported by the client</p>` : `<p>model not reported by the client</p>`}
      ${g.tokens_in || g.tokens_out ? `<p>${num(g.tokens_in)} tokens in · ${num(g.tokens_out)} out${g.cost_usd != null ? " · " + money(g.cost_usd) : ""}</p>` : ""}
      ${g.agents.length ? `<p>${g.agents.map((t) => esc(t.label || `token ${t.token_id}`)).join(", ")}</p>` : ""}</button>`).join("")}</div>`;
  }
  function meter(b) {
    if (!b) return "";
    const rem = (x) => (x == null ? "" : `, ${num(x)} left for the app`);
    return `<div class="act-meter" data-testid="act-buffer">Buffer API: <b>${num(b.you_15min)}</b> calls by you in 15 min (limit ${num(b.limit_15min)}${rem(b.remaining_15min)}) · <b>${num(b.you_24h)}</b> in 24 h (limit ${num(b.limit_24h)}${rem(b.remaining_24h)}). <span class="muted sm">${esc(b.note)}</span></div>`;
  }

  async function load() {
    const f = filters();
    $("#actCsv").href = "/api/activity/export.csv?" + qs({ ...f, limit: "", cursor: "" });
    let r;
    try { r = await api("/api/activity?" + qs({ ...f, cursor: A.cursors[A.at] })); }
    catch (e) { $("#audit").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    for (const [k, v] of Object.entries(r.counts || {})) { const el = document.querySelector(`[data-count="${k}"]`); if (el) el.textContent = v ? String(v) : ""; }
    $("#actRetention").textContent = r.retention_days;
    $("#actSummary").innerHTML = (A.tab === "agent" ? groups(r.agents) : "") + (A.tab === "system" ? meter(r.buffer) : "");
    document.querySelectorAll("[data-testid=act-group]").forEach((b) => (b.onclick = () => { A.app = A.app === b.dataset.app ? "" : b.dataset.app; reset(); }));
    const box = $("#audit");
    if (!r.items.length) box.innerHTML = `<p class="muted" data-testid="act-empty">No activity matches.</p>`;
    else box.innerHTML = `<div class="wrap-x"><table class="data"><thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Model</th><th scope="col">Result</th><th scope="col">Detail</th></tr></thead><tbody>${r.items.map((x) => `<tr data-testid="audit-row" data-category="${esc(x.category)}"><td>${fmtTime(x.ts)}</td>${who(x)}<td>${esc(x.action)}</td>${model(x)}<td><span class="tag ${x.ok ? esc(x.risk) : "fail"}">${x.ok ? esc(x.risk) : "failed"}</span></td><td><code>${esc(JSON.stringify(x.detail)).slice(0, 160)}</code></td></tr>`).join("")}</tbody></table></div>`;
    A.next = r.next_cursor;
    $("#actNext").disabled = !A.next;
    $("#actPrev").disabled = A.at === 0;
    $("#actPage").textContent = `Page ${A.at + 1} · ${r.items.length} of ${r.counts[A.tab] ?? r.counts.all}`;
  }
  function reset() { A.cursors = [""]; A.at = 0; load(); }

  document.querySelectorAll("[data-act-tab]").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("[data-act-tab]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b ? "true" : "false"); });
    A.tab = b.dataset.actTab; A.app = ""; reset();
  }));
  $("#actFilters").onsubmit = (e) => { e.preventDefault(); reset(); };
  $("#actLimit").onchange = reset;
  $("#actFailed").onchange = reset;
  $("#actNext").onclick = () => { if (!A.next) return; A.cursors = A.cursors.slice(0, A.at + 1).concat(A.next); A.at += 1; load(); };
  $("#actPrev").onclick = () => { if (A.at > 0) { A.at -= 1; load(); } };

  window.HS_TABS = Object.assign(window.HS_TABS || {}, { activity: reset });
})();
