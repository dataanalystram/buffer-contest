// HuhQueue admin panel (docs/ADMIN.md). Served only to HQ_ADMIN_IDS; every action sends the session's CSRF token.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const PER = 50;
  let csrf = "", offset = 0, total = 0, ctl = null, me = "";

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

  function row(u) {
    const self = u.id === me;
    const block = u.status === "blocked"
      ? `<button type="button" data-act="unblock" data-testid="admin-unblock" data-id="${esc(u.id)}">Unblock</button>`
      : `<button type="button" data-act="block" data-testid="admin-block" data-id="${esc(u.id)}"${self ? " disabled title=\"You can't block yourself\"" : ""}>Block</button>`;
    return `<tr data-testid="admin-user"><td><b>${esc(u.name || "(no name)")}</b><small>${esc(u.email)}</small><small><code>${esc(u.id)}</code></small></td>
      <td>${esc(when(u.created))}</td>
      <td>${esc(when(u.last_signin))}<small>${esc(u.last_signin_ip)}${u.last_signin_ua ? " · " + esc(u.last_signin_ua) : ""}</small></td>
      <td>${esc(when(u.last_seen))}</td><td>${esc(u.runs)}</td><td>${esc(u.posts)}</td><td>${esc(size(u.storage_bytes))}</td>
      <td><span class="pill ${esc(u.status)}">${esc(u.status)}</span></td>
      <td><div class="adm-actions">${block}<button type="button" class="danger" data-act="delete" data-testid="admin-delete" data-id="${esc(u.id)}"${self ? " disabled" : ""}>Delete</button></div></td></tr>`;
  }

  async function load() {
    if (ctl) ctl.abort();               // a newer search wins: a late answer never overwrites it
    ctl = new AbortController();
    const q = new URLSearchParams({ q: $("q").value.trim(), limit: PER, offset, demo: $("demo").checked ? "1" : "0" });
    try {
      const d = await api(`/api/admin/users?${q}`, { signal: ctl.signal });
      total = d.total; overview(d.overview);
      $("rows").innerHTML = d.users.map(row).join("") || `<tr><td colspan="9">No users.</td></tr>`;
      $("page").textContent = total ? `${offset + 1}–${Math.min(offset + PER, total)} of ${total}` : "";
      $("prev").disabled = offset === 0; $("next").disabled = offset + PER >= total;
      const a = await api("/api/admin/audit?limit=50", { signal: ctl.signal });
      $("audit").innerHTML = a.items.map((x) => `<tr><td>${esc(when(x.ts))}</td><td><code>${esc(x.admin)}</code></td><td>${esc(x.action)}</td>
        <td><code>${esc((x.subject_hash || "").slice(0, 12))}</code></td></tr>`).join("") || `<tr><td colspan="4">Nothing yet.</td></tr>`;
    } catch (e) { if (e.name !== "AbortError") say(e.message); }
  }

  async function act(kind, id) {
    try {
      if (kind === "block") {               // an in-app confirm dialog (CLAUDE.md: never window.confirm / prompt)
        $("blkReason").value = ""; $("blk").dataset.id = id; $("blk").showModal(); return;
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
  $("blkCancel").addEventListener("click", () => $("blk").close());
  $("blkForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = $("blk").dataset.id;
    try {
      await api(`/api/admin/users/${encodeURIComponent(id)}/block`, { method: "POST", body: JSON.stringify({ reason: $("blkReason").value }) });
      say("Blocked.");
    } catch (e) { say(e.message); }
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

  fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json()).then((s) => {
    csrf = s.csrf || ""; me = (s.user && (s.user.id || s.user.account_id)) || ""; load();
  });
})();
