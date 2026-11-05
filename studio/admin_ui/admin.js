// HuhQueue admin panel (docs/ADMIN.md). Served only to HQ_ADMIN_IDS; every action sends the session's CSRF token.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const PER = 50;
  let csrf = "", offset = 0, total = 0, ctl = null, me = "", rows = [], busy = false;
  const BLOCK_TIMEOUT_MS = 15000;          // a block request that stalls is aborted; Cancel/Esc always close the dialog
  let blkCtl = null, blkSeq = 0;           // the open dialog's request; a late answer for an older dialog is ignored

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");
  const size = (n) => {
    if (n == null) return "—";
    const u = ["B", "KB", "MB", "GB"]; let i = 0; let v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
  };
  const say = (m) => { $("msg").textContent = m || ""; };

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: "same-origin", ...opts,
      headers: { "Content-Type": "application/json", "X-HQ-CSRF": csrf, ...(opts.headers || {}) } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || body.detail || `HTTP ${r.status}`);
    return body;
  }

  function overview(o) {
    const cards = [["Database", size(o.db_bytes)], ["Media in database", size(o.db_media_bytes)], ["Media in R2", size(o.r2_media_bytes)],
      ["New media goes to", o.storage_backend === "r2" ? "R2" : "the database"], ["Users", o.users], ["Blocked", o.blocked],
      ["Objects waiting to delete", o.objects_pending_delete]];
    $("overview").innerHTML = cards.map(([k, v]) => `<div><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  }

  // Media bandwidth (studio/egress.py): today's bytes against the daily budgets, the 80% warning, the last 14 days
  function egressDraw(d) {
    const t = d.today || {}, c = d.cache || {};
    const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
    const cards = [["Read from database", `${size(t.db_bytes)} of ${size(t.db_budget)}`], ["Sent to clients", `${size(t.served_bytes)} of ${size(t.served_budget)}`],
      ["Disk cache", `${c.files || 0} files · ${size(c.bytes)}`], ["Cache hits / misses", `${c.hits || 0} / ${c.misses || 0}`]];
    const r = d.r2 || {};   // R2 against the free-tier caps (studio/r2usage.py): stored, writes (Class A), reads (Class B)
    if (r.configured) cards.push(["R2 stored", `${size(r.stored_bytes)} of ${size(r.max_bytes)}`],
      ["R2 writes this month", `${(r.class_a || 0).toLocaleString()} of ${(r.max_class_a || 0).toLocaleString()}`],
      ["R2 reads this month", `${(r.class_b || 0).toLocaleString()} of ${(r.max_class_b || 0).toLocaleString()}`]);
    $("egressToday").innerHTML = cards.map(([k, v]) => `<div><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
    const hi = Math.max(pct(t.db_bytes, t.db_budget), pct(t.served_bytes, t.served_budget));
    const w = $("egressWarn");
    const capMsg = [r.write_blocked ? "new files stay in the database" : "",
      r.read_blocked ? "files with a database copy are served from it; files kept only in R2 answer 503 to everyone but their owner until next month (UTC)" : ""].filter(Boolean).join("; ");
    w.hidden = hi < 80 && !capMsg && !t.degraded;
    w.textContent = [capMsg ? `R2 free-tier cap reached: ${capMsg}.` : "",
      t.degraded ? "Over today's budget: other people's media previews are paused until midnight UTC. Your own files and Buffer are unaffected."
        : hi >= 80 ? `${hi}% of today's media budget used.` : ""].filter(Boolean).join(" ");
    $("egressDays").innerHTML = (d.days || []).map((x) => `<tr data-testid="admin-egress-day"><td>${esc(x.day)}</td>
      <td>${esc(size(x.db_bytes))} <small>of ${esc(size(x.db_budget))}</small></td><td>${esc(size(x.served_bytes))} <small>of ${esc(size(x.served_budget))}</small></td>
      <td>${esc((x.refused_budget || 0) + (x.refused_repeat || 0))}</td></tr>`).join("") || `<tr><td colspan="4">No full day yet.</td></tr>`;
  }

  function row(u) {
    const self = u.id === me;
    const block = u.status === "blocked"
      ? `<button type="button" data-act="unblock" data-testid="admin-unblock" data-id="${esc(u.id)}">Unblock</button>`
      : `<button type="button" data-act="block" data-testid="admin-block" data-id="${esc(u.id)}"${self ? " disabled title=\"You can't block yourself\"" : ""}>Block</button>`;
    return `<tr data-testid="admin-user"><td><b>${esc(u.name || "(no name)")}</b><small>${esc(u.email)}</small>${u.org ? `<small>${esc(u.org)}</small>` : ""}<small><code>${esc(u.id)}</code></small></td>
      <td>${esc(when(u.first_signin))}</td>
      <td>${esc(when(u.last_signin))}<small>${esc(u.last_signin_ip)}${u.last_signin_ua ? " · " + esc(u.last_signin_ua) : ""}</small></td>
      <td>${esc(when(u.last_seen))}</td><td>${esc(u.runs)}</td><td>${esc(u.posts)}</td><td>${esc(size(u.storage_bytes))}</td>
      <td><span class="pill ${esc(u.status)}">${esc(u.status)}</span></td>
      <td><div class="adm-actions">${block}<button type="button" class="danger" data-act="delete" data-testid="admin-delete" data-id="${esc(u.id)}"${self ? " disabled" : ""}>Delete</button></div></td></tr>`;
  }

  // View transitions (5 Nov 2026, studio/static/viewswap.js): a new page or search shows the HuhQueue loader in the table at
  // once, so the rows being left never stay on view as if they were the answer; at least 150 ms so it doesn't flicker
  const LOAD_MIN_MS = 150;
  function loadingRows() {
    $("rows").innerHTML = `<tr data-testid="admin-loading"><td colspan="9" class="adm-loading">${window.HQView ? HQView.markup("Loading users") : "Loading users"}</td></tr>`;
  }
  function failedRows(msg) {
    $("rows").innerHTML = `<tr data-testid="admin-error"><td colspan="9" class="adm-loading"><span role="alert">${esc(msg)}</span>
      <button type="button" id="admRetry" data-testid="admin-retry">Retry</button></td></tr>`;
    $("admRetry").onclick = () => load();
  }
  async function load() {
    if (ctl) ctl.abort();               // a newer search wins: a late answer never overwrites it
    ctl = new AbortController();
    const mine = ctl, t0 = Date.now();
    loadingRows();
    const q = new URLSearchParams({ q: $("q").value.trim(), limit: PER, offset, demo: $("demo").checked ? "1" : "0" });
    try {
      const d = await api(`/api/admin/users?${q}`, { signal: ctl.signal });
      const left = LOAD_MIN_MS - (Date.now() - t0); if (left > 0) await new Promise((r) => setTimeout(r, left));
      if (mine !== ctl || mine.signal.aborted) return;
      total = d.total; rows = d.users; overview(d.overview);
      $("rows").innerHTML = d.users.map(row).join("") || `<tr><td colspan="9">No users.</td></tr>`;
      $("page").textContent = total ? `${offset + 1}–${Math.min(offset + PER, total)} of ${total}` : "";
      $("prev").disabled = offset === 0; $("next").disabled = offset + PER >= total;
      try { egressDraw(await api("/api/admin/egress", { signal: ctl.signal })); }
      catch (e) { if (e.name !== "AbortError") $("egressToday").textContent = `Couldn't load media bandwidth: ${e.message}`; }
      const a = await api("/api/admin/audit?limit=50", { signal: ctl.signal });
      $("audit").innerHTML = a.items.map((x) => `<tr><td>${esc(when(x.ts))}</td><td><code>${esc(x.admin)}</code></td><td>${esc(x.action)}</td>
        <td><code>${esc((x.subject_hash || "").slice(0, 12))}</code></td></tr>`).join("") || `<tr><td colspan="4">Nothing yet.</td></tr>`;
    } catch (e) { if (e.name !== "AbortError" && mine === ctl) { say(e.message); if ($("rows").querySelector("[data-testid=admin-loading]")) failedRows(e.message); } }
  }

  async function act(kind, id) {
    try {
      if (kind === "block") {               // an in-app confirm dialog (CLAUDE.md: never window.confirm / prompt)
        if (busy) return;
        const u = rows.find((x) => x.id === id) || { id };
        $("blkH").textContent = `Block ${u.name || u.email || u.id}?`;   // the account it will block, before anything is sent
        $("blkWho").textContent = `${u.name || "(no name)"}${u.email ? " · " + u.email : ""} · ${u.id}`;
        $("blkReason").value = ""; $("blk").dataset.id = id; $("blkOk").disabled = false; $("blk").showModal(); return;
      } else if (kind === "unblock") {
        await api(`/api/admin/users/${encodeURIComponent(id)}/unblock`, { method: "POST", body: "{}" });
        say("Unblocked.");
      } else if (kind === "delete") {
        $("delId").textContent = id; $("delInput").value = ""; $("delOk").disabled = true; $("del").dataset.id = id;
        $("del").showModal(); return;
      }
    } catch (e) { say(e.message); }
    load();
  }

  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-act]");
    if (b && !b.disabled) act(b.dataset.act, b.dataset.id);
  });
  function closeBlock() {                  // Cancel / Esc: always works, even while a request is pending
    const pending = busy;
    blkSeq++;                               // the pending request's answer no longer touches any dialog
    if (blkCtl) blkCtl.abort();
    blkCtl = null; busy = false; $("blkOk").disabled = false;
    if ($("blk").open) $("blk").close();
    if (pending) { say("Block request stopped. The list is reloaded so you can see whether it went through."); load(); }
  }
  $("blkCancel").addEventListener("click", closeBlock);
  $("blk").addEventListener("cancel", (ev) => { ev.preventDefault(); closeBlock(); });
  $("blkForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (busy) return;                     // one request per dialog: no double submit, no duplicate audit rows
    busy = true; $("blkOk").disabled = true;
    const id = $("blk").dataset.id, seq = ++blkSeq, c = new AbortController();
    blkCtl = c;
    const timer = setTimeout(() => c.abort(), BLOCK_TIMEOUT_MS);
    let msg = "Blocked.";
    try {
      await api(`/api/admin/users/${encodeURIComponent(id)}/block`, { method: "POST", signal: c.signal,
        body: JSON.stringify({ reason: $("blkReason").value }) });
    } catch (e) {
      msg = e.name === "AbortError" ? "The block request didn't finish in time. The list is reloaded so you can see whether it went through." : e.message;
    } finally { clearTimeout(timer); }
    if (seq !== blkSeq) return;           // closed (or reopened) meanwhile: closeBlock already reconciled
    blkCtl = null; busy = false; say(msg);
    $("blk").close(); load();
  });
  $("delInput").addEventListener("input", () => { $("delOk").disabled = $("delInput").value !== $("del").dataset.id; });
  $("delCancel").addEventListener("click", () => $("del").close());
  $("delForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = $("del").dataset.id;
    try {
      await api(`/api/admin/users/${encodeURIComponent(id)}/delete`, { method: "POST", body: JSON.stringify({ confirm: $("delInput").value }) });
      say("Deleted. Only a hashed id remains in the admin log.");
    } catch (e) { say(e.message); }
    $("del").close(); load();
  });
  let t = 0;
  $("q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { offset = 0; load(); }, 250); });
  $("demo").addEventListener("change", () => { offset = 0; load(); });
  $("prev").addEventListener("click", () => { offset = Math.max(0, offset - PER); load(); });
  $("next").addEventListener("click", () => { offset += PER; load(); });

  function start() {                       // the session (CSRF token, who I am), then the list; a failure says so
    fetch("/api/session", { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((s) => { csrf = s.csrf || ""; me = (s.user && (s.user.id || s.user.account_id)) || ""; say(""); load(); })
      .catch(() => {
        $("msg").innerHTML = 'Couldn\'t load your session. <button type="button" id="retry" data-testid="admin-retry">Try again</button>';
        $("retry").addEventListener("click", start);
      });
  }
  start();
})();
