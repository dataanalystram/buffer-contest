// Brain swap (work/plans BRAIN_SWAP_PLAN): the AI allowance meter ("$0.62 of $1 left") and, from 70% used and at 100%,
// the free route: the person's own Claude / ChatGPT does the thinking over MCP, HuhQueue does the editing. Also the
// Settings -> Integrations card "Use HuhQueue in Claude / ChatGPT" (MCP address + copy + 3 steps).
// The numbers come from GET /api/agent/brain -> meter (studio/budget.py meter()); no meter without the operator key.
"use strict";
(() => {
const BS = { meter: null };
const mcpUrl = () => (window.S && S.session && S.session.mcp_url) || location.origin + "/mcp";
window.hqMcpUrl = mcpUrl;
async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch (_) { /* clipboard blocked: the address stays selectable */ }
  const was = btn.textContent; btn.textContent = "Copied"; if (window.toast) toast("Copied"); setTimeout(() => (btn.textContent = was), 1600);
}
function goConnect() {
  if (window.showTab) showTab("settings");
  setTimeout(() => { const c = document.getElementById("useInClaude"); if (!c) return; c.scrollIntoView({ block: "start", behavior: "smooth" });
    c.classList.add("integ-flash"); setTimeout(() => c.classList.remove("integ-flash"), 1600); }, 120);
}
function meterHtml(m, where) {
  const pct = Math.max(0, Math.min(100, m.used_pct || 0));
  const lvl = m.out ? "out" : m.nudge ? "low" : "ok";
  return `<div class="bsw-meter ${lvl}" data-testid="${where}-meter-box" data-level="${lvl}">
    <div class="bsw-row"><span class="bsw-label">AI allowance</span><b class="bsw-left" data-testid="${where}-meter-left">${esc(m.label)}</b></div>
    <div class="bsw-bar" role="meter" aria-label="AI allowance used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
    ${m.nudge ? `<p class="bsw-nudge" data-testid="${where}-meter-nudge">${m.out ? "<b>Out of credit?</b> " : ""}Keep going free in Claude: <button class="link bsw-link" type="button" data-bs-connect data-testid="${where}-meter-connect">connect HuhQueue</button></p>` : ""}
  </div>`;
}
function paint(m) {
  BS.meter = m;
  for (const [id, where] of [["aiMeter", "ai"], ["agMeter", "agent"]]) {
    const box = document.getElementById(id); if (!box) continue;
    // the agent screen only carries it once it matters (70%+); Settings always shows the number
    const show = !!m && (where === "ai" || m.nudge);
    box.hidden = !show; box.innerHTML = show ? meterHtml(m, where) : "";
    box.querySelectorAll("[data-bs-connect]").forEach((b) => (b.onclick = goConnect));
  }
}
async function refresh() {
  try { const r = await api("/api/agent/brain"); paint(r.meter || null); return r.meter || null; } catch (_) { paint(null); return null; }
}
function card() {
  const box = document.getElementById("useInClaude"); if (!box) return;
  const url = mcpUrl();
  box.innerHTML = `<div class="integ-top"><h3 class="card-title" id="useInClaudeH">Use HuhQueue in Claude / ChatGPT</h3><span class="integ-st ok">Free</span></div>
    <p class="bsw-tag" data-testid="use-in-claude-tag">Your model does the thinking, HuhQueue does the editing. Free.</p>
    <div class="bsw-addr"><span class="label">MCP address</span><code data-testid="use-in-claude-url">${esc(url)}</code>
      <button class="btn sm" type="button" data-testid="use-in-claude-copy">Copy</button></div>
    <ol class="bsw-steps" data-testid="use-in-claude-steps">
      <li><b>Copy</b> the MCP address above.</li>
      <li><b>Add it</b> in Claude (Settings, Connectors, Add custom connector) or ChatGPT (Plugins, Add custom MCP server).</li>
      <li><b>Sign in with Buffer</b> when HuhQueue asks, then ask: “make a carousel from this link” or “cut this video and add captions”.</li>
    </ol>
    <p class="muted sm">Carousels, reels, cuts, captions and splits run on HuhQueue at no AI cost to your allowance; every post still waits for your approval. Grok, Cursor, Codex and more: <button class="link bsw-link" type="button" data-go="apps" data-testid="use-in-claude-apps">AI apps</button></p>`;
  const b = box.querySelector("[data-testid=use-in-claude-copy]"); b.onclick = () => copyText(url, b);
  const g = box.querySelector("[data-go=apps]"); if (g) g.onclick = () => window.showTab && showTab("apps");
}
window.HQ_BRAIN_SWAP = { refresh, paint, card, goConnect, get meter() { return BS.meter; } };
const TABS = (window.HS_TABS = window.HS_TABS || {});
const prevInteg = TABS.integrations;
TABS.integrations = (...a) => { card(); refresh(); return prevInteg && prevInteg(...a); };
card();
// the agent screen: refresh the meter when the tab opens (house.js registers HS_TABS.agent first; wrap it, keep it)
const prevAgent = TABS.agent;
if (prevAgent) TABS.agent = (...a) => { refresh(); return prevAgent(...a); };
setTimeout(() => { if (window.S && S.session && S.session.connected !== false) refresh(); }, 1500);
})();
