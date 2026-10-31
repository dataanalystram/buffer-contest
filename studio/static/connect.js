// Connect AI apps: per-app setup for Claude, ChatGPT, Grok, Claude Code, Codex, Cursor and any MCP client. One MCP
// address for all of them; OAuth apps sign in themselves, the rest get an agent token made here (the same
// /api/agent-tokens action as the Agents screen, so the same policy, daily cap and kill switch apply). "Last used" comes
// from the agent tokens list (/api/safety), matched to an app by the token's name.
"use strict";
(() => {
const CX = [
  { id: "claude", name: "Claude", mark: "C", match: /claude(?!.*code)/i, auth: "oauth", where: "claude.ai, desktop and mobile",
    steps: ["Open Settings, then Connectors.", "Choose Add custom connector and paste your MCP address.", "Sign in with Buffer when HuhQueue asks, pick what Claude may do, and allow."] },
  { id: "chatgpt", name: "ChatGPT", mark: "G", match: /chatgpt|openai/i, auth: "oauth", where: "chatgpt.com, on plans with custom connectors",
    steps: ["Open Settings, then Apps and Connectors, and turn on developer mode under Advanced.", "Create a connector, paste your MCP address and choose OAuth.", "Sign in with Buffer when HuhQueue asks, pick what ChatGPT may do, and allow."] },
  { id: "grok", name: "Grok", mark: "X", match: /grok|xai/i, auth: "token", where: "the xAI API (remote MCP tools)",
    steps: ["Create a token for Grok below.", "Add a remote MCP tool to your request with your MCP address as the server URL.", "Send the token as the Authorization header: Bearer hq_…"],
    snip: (u, t) => `{"type": "mcp", "server_url": "${u}",\n "server_label": "huhqueue",\n "headers": {"Authorization": "Bearer ${t}"}}` },
  { id: "claude-code", name: "Claude Code", mark: ">_", match: /claude.?code/i, auth: "token", where: "your terminal",
    steps: ["Create a token for Claude Code below.", "Run the command in your project.", "Ask Claude Code to list your HuhQueue tools to check it works."],
    snip: (u, t) => `claude mcp add --transport http huhqueue \\\n  ${u} \\\n  --header "Authorization: Bearer ${t}"` },
  { id: "codex", name: "Codex", mark: "{}", match: /codex/i, auth: "token", where: "Codex CLI and IDE extension",
    steps: ["Create a token for Codex below and export it as HUHQUEUE_TOKEN.", "Add the server to ~/.codex/config.toml.", "Restart Codex and run /mcp to see the HuhQueue tools."],
    snip: (u) => `[mcp_servers.huhqueue]\nurl = "${u}"\nbearer_token_env_var = "HUHQUEUE_TOKEN"` },
  { id: "cursor", name: "Cursor", mark: "Cu", match: /cursor/i, auth: "token", where: "Cursor settings, MCP",
    steps: ["Create a token for Cursor below.", "Open Settings, then MCP, and add a new server, or edit .cursor/mcp.json.", "Paste the entry and save. The HuhQueue tools appear in Agent mode."],
    snip: (u, t) => `{"mcpServers": {"huhqueue": {\n  "url": "${u}",\n  "headers": {"Authorization": "Bearer ${t}"}\n}}}` },
  { id: "other", name: "Any MCP client", mark: "+", match: null, auth: "either", where: "Streamable HTTP, OAuth 2.1 with PKCE or a bearer token",
    steps: ["If the client supports OAuth, paste the address and sign in with Buffer.", "Otherwise create a token below and send it as Authorization: Bearer hq_….", "The same tools are plain REST too: see the API reference."],
    snip: (u, t) => `POST ${u}\nAuthorization: Bearer ${t}\nContent-Type: application/json\n\n{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}` },
];
const made = {}; // tokens created on this visit, shown once
const ago = (ts) => { const s = Date.now() / 1000 - ts; if (s < 90) return "just now"; if (s < 3600) return `${Math.round(s / 60)} min ago`; if (s < 86400) return `${Math.round(s / 3600)} h ago`; return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" }); };
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch (_) { /* clipboard blocked: the text stays selectable */ }
  const was = btn.textContent; btn.textContent = "Copied"; toast("Copied"); setTimeout(() => (btn.textContent = was), 1600);
}
async function loadConnect() {
  const url = (S.session && S.session.mcp_url) || location.origin + "/mcp";
  $("#cxUrl").textContent = url;
  $("#cxCopy").onclick = () => copy(url, $("#cxCopy"));
  let toks = [];
  try { toks = (await api("/api/safety")).tokens || []; } catch (e) { $("#cxApps").innerHTML = `<div class="st err">${esc(e.message)}</div>`; return; }
  const box = $("#cxApps");
  box.innerHTML = CX.map((a) => {
    const mine = toks.filter((t) => !t.revoked && (a.match ? a.match.test(t.name) : !CX.some((b) => b.match && b.match.test(t.name))));
    const used = mine.map((t) => t.last_used || 0).sort((x, y) => y - x)[0];
    const status = mine.length ? (used ? `Last used ${ago(used)}` : "Connected, not used yet") : "Not connected";
    const tok = made[a.id] || "hq_YOUR_TOKEN";
    const snip = a.snip ? a.snip(url, tok) : "";
    return `<article class="cx-card" data-testid="connect-app" data-app="${a.id}">
      <header><span class="cx-mark" aria-hidden="true">${esc(a.mark)}</span><div><h2>${esc(a.name)}</h2><p class="muted xs">${esc(a.where)}</p></div>
        <span class="cx-status${mine.length ? " on" : ""}" data-testid="connect-last-used">${esc(status)}</span></header>
      <ol class="cx-steps">${a.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
      ${snip ? `<div class="cx-snip"><pre class="code" data-testid="connect-snippet">${esc(snip)}</pre><button class="btn2 sm" type="button" data-copy="${a.id}">Copy</button></div>` : ""}
      <div class="cx-acts">${a.auth === "oauth" ? `<button class="btn sm" type="button" data-copy-url>Copy MCP address</button>`
        : `<button class="btn sm" type="button" data-make="${a.id}" data-testid="connect-make-token">${made[a.id] ? "Token created" : `Create a token for ${esc(a.name)}`}</button>`}
        ${made[a.id] ? `<span class="muted xs">Shown once. It needs your approval for every post.</span>` : `<span class="muted xs">${a.auth === "oauth" ? "No token to paste: the app signs in." : "Approval by default, 10 posts a day."}</span>`}</div>
    </article>`;
  }).join("");
  box.removeAttribute("aria-busy");
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => copy(b.previousElementSibling.textContent, b)));
  box.querySelectorAll("[data-copy-url]").forEach((b) => (b.onclick = () => copy(url, b)));
  box.querySelectorAll("[data-make]").forEach((b) => (b.onclick = async () => {
    const a = CX.find((x) => x.id === b.dataset.make);
    if (made[a.id]) return;
    b.disabled = true;
    try { const t = await api("/api/agent-tokens", { method: "POST", body: { name: a.id === "other" ? "MCP client" : a.name, policy: "review", daily_cap: 10 } }); made[a.id] = t.token; loadConnect(); }
    catch (e) { toast(e.message, 5000); b.disabled = false; }
  }));
}
window.HS_TABS = Object.assign(window.HS_TABS || {}, { connect: loadConnect });
})();
