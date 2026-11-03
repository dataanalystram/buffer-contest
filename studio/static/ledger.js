// Brand > Style Ledger (v2): rules grouped by what they govern, where each came from, its scope, examples and the edits
// it was learned from; suggestions from repeated edits (Accept / Dismiss); and the per-draft "rules applied" + sources
// block and review links on Make a post. Server: studio/ledger.py (UI = REST = MCP).
(function () {
  const L = { data: null, filter: "all", editing: null, open: new Set() };
  const NET = { instagram: "Instagram", linkedin: "LinkedIn", twitter: "X", facebook: "Facebook", threads: "Threads", bluesky: "Bluesky",
    tiktok: "TikTok", youtube: "YouTube", mastodon: "Mastodon", pinterest: "Pinterest", googlebusiness: "Google Business" };
  const net = (s) => NET[s] || s;
  const scopeText = (sc) => (sc && sc.length ? sc.map(net).join(", ") + " only" : "All channels");
  const origin = (r) => r.origin === "learned" ? `Learned from ${r.learned_from || "your"} edit${r.learned_from === 1 ? "" : "s"}`
    : r.origin === "agent" ? "Added by an AI app" : r.origin === "kit" ? "From your brand kit" : "Taught by you";

  window.loadLedger = async function () {
    const box = $("#ledger");
    if (!box) return;
    try { L.data = await api("/api/ledger"); draw(); }
    catch (e) { box.innerHTML = `<div class="st err">${esc(e.message)}</div>`; }
  };

  function scopePicker(sel, name) {
    const services = (L.data && L.data.services) || Object.keys(NET);
    return `<fieldset class="lg-scope" data-testid="${name}"><legend class="sr">Channels</legend>
      <label class="lg-pill"><input type="checkbox" value="all" ${!sel || !sel.length ? "checked" : ""}><span>All channels</span></label>
      ${services.map((s) => `<label class="lg-pill"><input type="checkbox" value="${esc(s)}" ${sel && sel.includes(s) ? "checked" : ""}><span>${esc(net(s))}</span></label>`).join("")}</fieldset>`;
  }
  function readScope(fs) {
    if (!fs) return [];
    const on = [...fs.querySelectorAll("input:checked")].map((i) => i.value);
    return on.includes("all") ? [] : on;
  }
  function wireScope(fs) {
    if (!fs) return;
    fs.addEventListener("change", (ev) => {
      const all = fs.querySelector('input[value="all"]');
      if (ev.target === all && all.checked) fs.querySelectorAll('input:not([value="all"])').forEach((i) => (i.checked = false));
      else if (ev.target !== all) all.checked = !fs.querySelector('input:not([value="all"]):checked');
    });
  }

  function exampleList(ex) {
    if (!ex || !ex.length) return "";
    return `<ul class="lg-ex" data-testid="rule-examples">${ex.map((x) => `<li><span class="lg-b"><s>${esc(x.before)}</s></span><span class="lg-arrow" aria-hidden="true">→</span><span class="lg-a">${esc(x.after)}</span></li>`).join("")}</ul>`;
  }
  function editList(ed) {
    if (!ed || !ed.length) return "";
    return `<ol class="lg-ex lg-edits" data-testid="rule-edits">${ed.map((x) => `<li><small class="muted">${esc(net(x.service || ""))}${x.at ? " · " + esc(new Date(x.at * 1000).toLocaleDateString()) : ""}</small>
      <span class="lg-b"><s>${esc(x.before)}</s></span><span class="lg-arrow" aria-hidden="true">→</span><span class="lg-a">${esc(x.after)}</span></li>`).join("")}</ol>`;
  }

  function ruleRow(r) {
    const ro = !!r.readonly;
    const hits = r.hits ? ` · caught ${r.hits}×` : "";
    const isOpen = L.open.has(String(r.id));
    const editing = L.editing === r.id;
    const more = !ro && (r.examples?.length || r.learned_from || r.source);
    return `<li class="lg-rule${r.active ? "" : " off"}${ro ? " ro" : ""}" data-testid="rule-row" data-rule="${esc(r.id)}" data-group="${esc(r.group)}">
      <div class="lg-main">
        <div class="lg-txt"><b data-testid="rule-text">${esc(r.rule)}</b>
          <span class="lg-meta"><span class="lg-src ${esc(r.origin)}" data-testid="rule-origin">${esc(origin(r))}</span>
            <span class="lg-sc" data-testid="rule-scope">${esc(scopeText(r.scope))}</span>${hits ? `<span>${esc(hits.slice(3))}</span>` : ""}</span></div>
        ${ro ? `<button type="button" class="link sm" data-go="brand:kit" data-testid="rule-kit-edit">Edit in Kit</button>`
          : `<label class="switch sm lg-sw"><input type="checkbox" role="switch" data-act="${r.id}" ${r.active ? "checked" : ""} aria-label="${esc(r.rule)}: on"><span></span></label>`}
      </div>
      ${ro ? "" : `<div class="lg-acts">
        ${more ? `<button type="button" class="link sm" data-more="${r.id}" aria-expanded="${isOpen}" data-testid="rule-more">${isOpen ? "Hide details" : r.learned_from ? "Show the edits" : r.examples?.length ? "Show examples" : "Details"}</button>` : ""}
        <button type="button" class="link sm" data-edit="${r.id}" data-testid="rule-edit">Edit</button>
        <button type="button" class="link sm danger" data-del="${r.id}" data-testid="rule-delete">Delete</button></div>`}
      ${editing ? `<form class="lg-edit" data-testid="rule-edit-form" data-id="${r.id}"><label class="sr" for="lgE${r.id}">Rule in plain words</label>
          <input id="lgE${r.id}" value="${esc(r.source && !/^Learned from/.test(r.source) ? r.source : r.rule)}" data-testid="rule-edit-input">
          ${r.kind === "text" ? scopePicker(r.scope, "rule-edit-scope") : `<small class="muted">Visual rules apply to every channel.</small>`}
          <div class="row tight"><button class="btn sm" type="submit" data-testid="rule-edit-save">Save</button><button class="btn2 sm" type="button" data-cancel>Cancel</button></div></form>` : ""}
      ${isOpen && !editing ? `<div class="lg-detail" data-testid="rule-detail">
          ${r.source && !/^Learned from/.test(r.source) ? `<p class="sm"><span class="muted">Your words:</span> “${esc(r.source)}”</p>` : ""}
          ${r.examples?.length ? `<p class="sm muted">What it changed</p>${exampleList(r.examples)}` : ""}
          ${r.learned_from ? `<p class="sm muted">Learned from these edits</p><div data-edits="${r.id}"><small class="muted">Loading…</small></div>` : ""}
        </div>` : ""}
    </li>`;
  }

  function suggestionCard(s) {
    return `<li class="lg-sugg" data-testid="rule-suggestion" data-sid="${s.id}">
      <div class="lg-txt"><b data-testid="suggestion-text">Suggested: ${esc(s.rule)}</b><span class="lg-meta"><span class="lg-src learned">From ${s.edit_count} of your edits</span>
        ${s.services?.length ? `<span>${esc(s.services.map(net).join(", "))}</span>` : ""}</span></div>
      <details class="lg-why"><summary>See the edits</summary>${editList(s.edits)}</details>
      ${s.kind === "text" ? scopePicker(s.scope, "suggestion-scope") : ""}
      <div class="row tight"><button type="button" class="btn sm" data-accept="${s.id}" data-testid="suggestion-accept">Accept</button>
        <button type="button" class="btn2 sm" data-dismiss="${s.id}" data-testid="suggestion-dismiss">Dismiss</button></div>
      <small class="muted sm">Nothing changes until you accept.</small></li>`;
  }

  function draw() {
    const box = $("#ledger");
    const d = L.data;
    const rules = d.rules || [], kit = d.kit_rules || [], sug = d.suggestions || [];
    const all = rules.concat(kit);
    const scoped = rules.filter((r) => r.scope && r.scope.length);
    const sm = d.summary || {};
    const chips = [["all", "All", all.length], ...d.groups.map((g) => [g.id, g.label, g.count]), ["channel", "Per channel", scoped.length]];
    const shown = L.filter === "all" ? all : L.filter === "channel" ? scoped : all.filter((r) => r.group === L.filter);
    const groups = (L.filter === "all" ? d.groups : L.filter === "channel" ? [{ id: "channel", label: "Per channel", hint: "Rules that apply on some networks only." }] : d.groups.filter((g) => g.id === L.filter));
    const section = (g) => {
      const rs = g.id === "channel" ? scoped : shown.filter((r) => r.group === g.id);
      if (!rs.length) return "";
      return `<section class="lg-group" data-testid="ledger-group" data-group="${esc(g.id)}"><h3 class="lg-h">${esc(g.label)} <small>${rs.length}</small></h3>
        <p class="muted sm">${esc(g.hint)}</p><ul class="lg-rules">${rs.map(ruleRow).join("")}</ul></section>`;
    };
    const body = groups.map(section).join("");
    box.innerHTML = `
      <div class="lg-stats" data-testid="ledger-stats" aria-live="polite">
        <span><b>${sm.active || 0}</b> rule${sm.active === 1 ? "" : "s"} on</span>
        <span><b>${sm.learned || 0}</b> learned from your edits</span>
        <span><b>${sm.scoped || 0}</b> per channel</span>
        ${kit.length ? `<span><b>${kit.length}</b> from your kit</span>` : ""}</div>
      ${sug.length ? `<section class="lg-sugs" data-testid="ledger-suggestions" aria-label="Suggested rules"><h3 class="lg-h">Suggested from your edits <small>${sug.length}</small></h3>
        <p class="muted sm">You made the same kind of change ${d.suggest_after} or more times. Accept to make it a rule; nothing is applied until you do.</p>
        <ul>${sug.map(suggestionCard).join("")}</ul></section>` : ""}
      <div class="seg lg-filter" role="tablist" aria-label="Rule groups" data-testid="ledger-filter">${chips.map(([id, label, n]) => `<button type="button" role="tab" data-f="${esc(id)}" aria-selected="${L.filter === id}" class="${L.filter === id ? "on" : ""}">${esc(label)}${n ? ` <small>${n}</small>` : ""}</button>`).join("")}</div>
      ${body || (all.length ? `<div class="empty"><b>No rules in this group</b><span>Teach one above, or pick another group.</span></div>`
        : `<div class="empty" data-testid="ledger-empty"><b>No rules yet</b><span>Teach one above (“never say game-changer”), reply to a post in plain words, or just edit captions: when you make the same change three times, a suggested rule appears here.</span></div>`)}`;
    wire(box);
  }

  function wire(box) {
    box.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { L.filter = b.dataset.f; draw(); }));
    box.querySelectorAll("[data-act]").forEach((c) => (c.onchange = async () => {
      try { await api(`/api/ledger/rules/${c.dataset.act}`, { method: "PATCH", body: { active: c.checked } }); loadLedger(); } catch (e) { toast(e.message); c.checked = !c.checked; } }));
    box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      const r = (L.data.rules || []).find((x) => String(x.id) === b.dataset.del);
      if (window.confirmDialog ? !(await window.confirmDialog(`Delete “${r ? r.rule : "this rule"}”?`, "Delete")) : !confirm(`Delete “${r ? r.rule : "this rule"}”?`)) return;
      try { await api(`/api/ledger/${b.dataset.del}`, { method: "DELETE" }); toast("Rule deleted"); loadLedger(); } catch (e) { toast(e.message); } }));
    box.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => { L.editing = +b.dataset.edit; draw(); const i = box.querySelector(".lg-edit input"); i && i.focus(); }));
    box.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = () => { L.editing = null; draw(); }));
    box.querySelectorAll(".lg-scope").forEach(wireScope);
    box.querySelectorAll(".lg-edit").forEach((f) => (f.onsubmit = async (ev) => {
      ev.preventDefault();
      const r = (L.data.rules || []).find((x) => x.id === +f.dataset.id);
      const v = f.querySelector("input").value.trim();
      const body = { scope: readScope(f.querySelector(".lg-scope")) };
      if (r && v && v !== r.rule && v !== r.source) body.rule = v;
      try { await api(`/api/ledger/rules/${f.dataset.id}`, { method: "PATCH", body }); L.editing = null; toast("Rule saved"); loadLedger(); } catch (e) { toast(e.message, 5000); }
    }));
    box.querySelectorAll("[data-more]").forEach((b) => (b.onclick = async () => {
      const id = b.dataset.more;
      L.open.has(id) ? L.open.delete(id) : L.open.add(id);
      draw();
      const slot = box.querySelector(`[data-edits="${id}"]`);
      if (slot) { try { const r = await api(`/api/ledger/rules/${id}`); slot.innerHTML = editList(r.edits) || `<small class="muted">The edits are no longer stored.</small>`; } catch (_) { slot.innerHTML = ""; } }
    }));
    box.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = async () => {
      const card = b.closest(".lg-sugg");
      try { const r = await api(`/api/ledger/suggestions/${b.dataset.accept}`, { method: "POST", body: { decision: "accept", scope: readScope(card.querySelector(".lg-scope")) } });
        toast(`Rule added: ${r.rule.rule}`, 4000); loadLedger(); } catch (e) { toast(e.message); } }));
    box.querySelectorAll("[data-dismiss]").forEach((b) => (b.onclick = async () => {
      try { await api(`/api/ledger/suggestions/${b.dataset.dismiss}`, { method: "POST", body: { decision: "dismiss" } }); toast("Dismissed. It won't be suggested again."); loadLedger(); } catch (e) { toast(e.message); } }));
  }

  // teach a rule (with an optional channel scope)
  const form = $("#ruleForm");
  if (form) {
    const fs = $("#ruleScope");
    if (fs) { fs.innerHTML = scopePicker([], "rule-scope").replace(/^<fieldset[^>]*>|<\/fieldset>$/g, ""); wireScope(fs); }
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const v = $("#ruleIn").value.trim();
      if (!v) return;
      try {
        const r = await api("/api/ledger", { method: "POST", body: { correction: v, scope: readScope(fs) } });
        toast(r.added.length ? "Learned: " + r.added.map((x) => x.rule).join(" · ") : r.message, 5000);
        if (r.added.length) { $("#ruleIn").value = ""; if (fs) fs.querySelectorAll("input").forEach((i) => (i.checked = i.value === "all")); }
        loadLedger();
      } catch (e) { toast(e.message, 5000); }
    });
  }

  // ---------------------------------------------------------------- Make a post: per draft
  window.ledgerDraft = function (p) {
    const rules = p.applied_rules || [], ev = p.evidence || [];
    if (!rules.length && !ev.length) return "";
    const miss = ev.filter((x) => !x.found).length;
    return `<div class="lg-draft" data-testid="pkg-ledger">
      ${rules.length ? `<details data-testid="pkg-applied"><summary>${rules.length} brand rule${rules.length === 1 ? "" : "s"} applied</summary>
        <ul>${rules.map((r) => `<li data-testid="pkg-applied-rule"><span>${esc(r.rule)}</span><small class="muted">${esc(r.scope && r.scope.length ? scopeText(r.scope) : r.origin === "learned" ? "learned" : "")}</small></li>`).join("")}</ul></details>` : ""}
      ${ev.length ? `<details data-testid="pkg-evidence"><summary>Sources: ${ev.length - miss} of ${ev.length} fact${ev.length === 1 ? "" : "s"} found in ${esc(ev[0].site || "the source")}${miss ? ` · ${miss} not found` : ""}</summary>
        <ul class="lg-evl">${ev.map((x) => `<li class="${x.found ? "ok" : "warn"}" data-testid="pkg-evidence-item"><b>${esc(x.claim)}</b>${x.quote ? `<q>${esc(x.quote)}</q>` : `<span class="sm">${x.found ? "" : "Not found in the source text: check it before posting."}</span>`}
          ${x.url ? `<a class="link sm" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.site || "Source")}</a>` : ""}</li>`).join("")}</ul></details>` : ""}
      <button type="button" class="link sm" data-review="${esc(p.channel_id)}" data-testid="pkg-review-link">Share for review</button>
    </div>`;
  };
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-review]");
    if (!b) return;
    const run = typeof H !== "undefined" && H.run ? H.run : null;
    if (!run) return;
    b.disabled = true;
    try {
      const r = await api(`/api/agent/runs/${run.id}/review-links`, { method: "POST", body: { channel_id: b.dataset.review } });
      const box = document.createElement("div");
      box.className = "lg-share"; box.setAttribute("data-testid", "review-link-box");
      box.innerHTML = `<label class="sr" for="rv${run.id}">Review link</label><input id="rv${run.id}" readonly value="${esc(r.url)}" data-testid="review-link-url">
        <button type="button" class="btn2 sm" data-copy>Copy</button><small class="muted sm">Read-only. Expires in ${Math.round(r.hours)} hours. They can approve or ask for changes; you see it in this thread.</small>`;
      b.replaceWith(box);
      box.querySelector("[data-copy]").onclick = async () => { try { await navigator.clipboard.writeText(r.url); toast("Link copied"); } catch (_) { box.querySelector("input").select(); } };
    } catch (e) { toast(e.message); b.disabled = false; }
  });
  window.ledgerSuggested = function (list) {
    if (!list || !list.length) return;
    toast(`Suggested rule: ${list[0].rule} (${list[0].why}). Review it in Brand › Style Ledger.`, 6000);
  };
})();
