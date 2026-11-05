// Settings → Privacy & data → "Auto-approve video links" (studio/link_auto.py, 5 Nov 2026). Off by default. Turning it on
// shows the rights statement and the app scope first; nothing changes until the person ticks the statement and confirms.
// Turning it off is one click and takes effect at once. Every load aborts the one before it (a late answer never
// overwrites a newer screen).
(function () {
  const L = { ctl: null, data: null, form: false };
  const box = () => document.getElementById("setAutoBody");
  const tog = () => document.getElementById("setAutoToggle");
  const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }) : "");
  const appName = (id) => ((L.data && L.data.available_apps) || []).find((a) => a.id === id)?.name || `App ${id}`;

  async function load() {
    if (!box()) return;
    if (L.ctl) L.ctl.abort();
    const ctl = (L.ctl = new AbortController());
    try {
      const d = await api("/api/settings/link-auto-approve", { signal: ctl.signal });
      if (ctl.signal.aborted) return;
      L.data = d; L.form = false; draw();
    } catch (e) {
      if (e.name === "AbortError" || ctl.signal.aborted) return;
      box().innerHTML = `<p class="st err">${esc(e.message)}</p>`;
    }
  }

  function draw() {
    const d = L.data || {}, b = box();
    tog().checked = !!d.on;
    if (L.form) return drawForm();
    if (!d.on) {
      b.innerHTML = d.statement_changed
        ? `<p class="muted sm" data-testid="link-auto-changed">The rights statement changed, so auto-approve is paused. Turn it on again to agree to the new text.</p>`
        : `<p class="muted sm" data-testid="link-auto-off">Off. Each video link an AI app sends waits in your Inbox for you to approve.</p>`;
      return;
    }
    const who = d.scope === "apps" ? (d.apps || []).map(appName).join(", ") : "all your connected AI apps";
    b.innerHTML = `<div class="set-auto-sum" data-testid="link-auto-on"><span class="pill sm auto-pill">On</span>
      <span>For ${esc(who)}. You agreed to the rights statement on ${esc(when(d.attested_at))}.</span></div>
      <div class="row tight"><button class="btn2 sm" type="button" data-testid="link-auto-edit">Change apps</button></div>`;
    b.querySelector("[data-testid=link-auto-edit]").onclick = () => { L.form = true; drawForm(); };
  }

  function drawForm() {
    const d = L.data || {}, b = box(), apps = d.available_apps || [], editing = !!d.on;
    const picked = new Set(d.scope === "apps" ? d.apps || [] : []);
    b.innerHTML = `<form class="set-auto-form" data-testid="link-auto-form" novalidate>
      ${editing ? "" : `<label class="appr-attest" data-testid="link-auto-attest-row"><input type="checkbox" data-testid="link-auto-attest" required><span>${esc(d.attestation || "")}</span></label>
      <p class="muted sm">This applies to every video link the apps below send while it is on. You can turn it off at any time.</p>`}
      <fieldset><legend>Which AI apps</legend>
        <label class="chk"><input type="radio" name="laScope" value="all" data-testid="link-auto-scope-all" ${d.scope !== "apps" ? "checked" : ""}> All connected apps</label>
        <label class="chk"><input type="radio" name="laScope" value="apps" data-testid="link-auto-scope-apps" ${d.scope === "apps" ? "checked" : ""} ${apps.length ? "" : "disabled"}> Only the apps I pick</label>
        <div class="set-auto-apps" data-testid="link-auto-apps">${apps.map((a) => `<label class="chk"><input type="checkbox" value="${Number(a.id)}" data-testid="link-auto-app" ${picked.has(a.id) ? "checked" : ""}> ${esc(a.name)}</label>`).join("") || `<p class="muted sm">No AI apps connected yet.</p>`}</div>
      </fieldset>
      <p class="st err" data-testid="link-auto-error" role="alert" hidden></p>
      <div class="row tight"><button class="btn2 sm" type="button" data-testid="link-auto-cancel">Cancel</button>
        <button class="btn sm" type="submit" data-testid="link-auto-save" disabled>${editing ? "Save" : "Turn on"}</button></div></form>`;
    const f = b.querySelector("form"), save = f.querySelector("[data-testid=link-auto-save]");
    const scope = () => f.querySelector("input[name=laScope]:checked")?.value || "all";
    const chosen = () => [...f.querySelectorAll("[data-testid=link-auto-app]:checked")].map((x) => Number(x.value));
    const sync = () => {
      const appsBox = f.querySelector("[data-testid=link-auto-apps]");
      appsBox.hidden = scope() !== "apps";
      const attested = editing || f.querySelector("[data-testid=link-auto-attest]")?.checked;
      save.disabled = !attested || (scope() === "apps" && !chosen().length);
    };
    f.addEventListener("change", sync); sync();
    f.querySelector("[data-testid=link-auto-cancel]").onclick = () => { L.form = false; draw(); };
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      if (save.disabled) return;
      if (typeof demoSays === "function" && demoSays("turn on auto-approve for video links")) return;
      save.disabled = true;
      const err = f.querySelector("[data-testid=link-auto-error]");
      try {
        L.data = await api("/api/settings/link-auto-approve", { method: "PUT", body: { on: true, scope: scope(), apps: scope() === "apps" ? chosen() : [],
          rights_attested: !editing, attestation_version: d.attestation_version || "" } });
        L.form = false; draw();
        toast(editing ? "Saved." : "Auto-approve is on. Video links from those apps start without asking; Activity marks them auto-approved.");
      } catch (e) { err.textContent = e.message; err.hidden = false; sync(); }
    };
    const first = f.querySelector("input"); if (first) first.focus();
  }

  async function onToggle(e) {
    const want = e.target.checked;
    if (want) {  // nothing changes until the statement is ticked and confirmed
      e.target.checked = !!(L.data && L.data.on);
      if (!(L.data && L.data.on)) { L.form = true; drawForm(); }
      return;
    }
    if (typeof demoSays === "function" && demoSays("turn off auto-approve")) { e.target.checked = true; return; }
    try {
      L.data = await api("/api/settings/link-auto-approve", { method: "PUT", body: { on: false } });
      L.form = false; draw();
      toast("Auto-approve is off. Video links from your AI apps wait for you again.");
    } catch (err) { e.target.checked = true; toast(err.message, 5000); }
  }

  document.addEventListener("DOMContentLoaded", () => { const t = tog(); if (t) t.addEventListener("change", onToggle); });
  if (document.readyState !== "loading") { const t = tog(); if (t && !t.dataset.bound) { t.dataset.bound = "1"; t.addEventListener("change", onToggle); } }
  window.HS_TABS = window.HS_TABS || {};
  window.HS_TABS.linkauto = load;
})();
