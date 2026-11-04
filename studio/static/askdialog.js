// In-app confirm and prompt (replaces window.confirm / window.prompt). Native dialogs block the page's event loop, can be
// suppressed by the browser ("prevent this page from creating additional dialogs"), are invisible to automation and
// look nothing like the app. These are native <dialog>s: focus moves in, Esc or Cancel answers "no", Enter confirms.
//   await hqConfirm("Delete this draft?", { ok: "Delete draft", danger: true })  -> true | false
//   await hqAsk("Actions a day (1 to 100)", "10", { type: "number", min: 1, max: 100 })  -> string | null
"use strict";
(() => {
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
function open(message, opts, field) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "pl-dialog ask-dialog"; d.dataset.testid = "ask-dialog";
    d.setAttribute("aria-labelledby", "askMsg");
    const input = field ? `<input class="ask-input" id="askInput" data-testid="ask-input" type="${esc(field.type || "text")}" value="${esc(field.value)}"${field.min !== undefined ? ` min="${esc(field.min)}"` : ""}${field.max !== undefined ? ` max="${esc(field.max)}"` : ""} aria-labelledby="askMsg">` : "";
    d.innerHTML = `<form method="dialog">
      <p class="ask-msg" id="askMsg" data-testid="ask-message">${esc(message).replace(/\n/g, "<br>")}</p>${input}
      <div class="pl-dlg-act"><span></span><span class="row">
        <button class="btn2 sm" type="button" data-testid="ask-cancel">${esc(opts.cancel || "Cancel")}</button>
        <button class="btn sm${opts.danger ? " danger-fill" : ""}" type="submit" value="ok" data-testid="ask-ok">${esc(opts.ok || "OK")}</button></span></div></form>`;
    let answered = false;
    const done = (v) => { if (answered) return; answered = true; try { d.close(); } catch (_) {} d.remove(); resolve(v); };
    d.querySelector("[data-testid=ask-cancel]").onclick = () => done(field ? null : false);
    d.querySelector("form").onsubmit = (e) => { e.preventDefault(); done(field ? d.querySelector("#askInput").value : true); };
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(field ? null : false); });  // Esc
    document.body.append(d);
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
    const f = field ? d.querySelector("#askInput") : d.querySelector(opts.danger ? "[data-testid=ask-cancel]" : "[data-testid=ask-ok]");
    f.focus(); if (field && f.select) f.select();
  });
}
// A choice of actions: await hqChoose("Delete this thread?", [{ value: "keep", label: "Delete thread only" },
//   { value: "delete", label: "Delete thread and its take", danger: true }], { note: "..." })  -> value | null (Cancel / Esc)
function choose(message, choices, opts) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "pl-dialog ask-dialog ask-choose"; d.dataset.testid = "choose-dialog";
    d.setAttribute("aria-labelledby", "askMsg");
    d.innerHTML = `<form method="dialog">
      <p class="ask-msg" id="askMsg" data-testid="ask-message">${esc(message).replace(/\n/g, "<br>")}</p>
      ${opts.note ? `<p class="muted sm" data-testid="choose-note">${esc(opts.note)}</p>` : ""}
      <div class="ask-choices" role="group" aria-label="${esc(opts.label || "Choose")}">${choices.map((c) => `<button type="button" class="${c.danger ? "btn sm danger-fill" : "btn2 sm"}" data-value="${esc(c.value)}" data-testid="choose-${esc(c.value)}">${esc(c.label)}${c.hint ? `<small>${esc(c.hint)}</small>` : ""}</button>`).join("")}</div>
      <div class="pl-dlg-act"><span></span><span class="row"><button class="btn2 sm" type="button" data-testid="ask-cancel">${esc(opts.cancel || "Cancel")}</button></span></div></form>`;
    let answered = false;
    const done = (v) => { if (answered) return; answered = true; try { d.close(); } catch (_) {} d.remove(); resolve(v); };
    d.querySelector("[data-testid=ask-cancel]").onclick = () => done(null);
    d.querySelectorAll("[data-value]").forEach((b) => (b.onclick = () => done(b.dataset.value)));
    d.querySelector("form").onsubmit = (e) => { e.preventDefault(); };
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });  // Esc
    document.body.append(d);
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
    d.querySelector("[data-testid=ask-cancel]").focus();  // a destructive choice is never the default
  });
}
window.hqConfirm = (message, opts = {}) => open(message, opts, null);
window.hqChoose = (message, choices, opts = {}) => choose(message, choices || [], opts);
window.hqAsk = (message, value = "", opts = {}) => open(message, opts, { value, type: opts.type, min: opts.min, max: opts.max });
})();
