// AI app and model marks, used wherever an agent or a model is named (Inbox, Approvals, Activity, AI apps).
// aiMark(nameOrId, size) returns a small tile with the official mark (ai-logos.js) or, for anything we have no mark
// for, a monogram. aiId(text) maps free text ("Claude Code", "gpt-5", "xai/grok-4", "OpenRouter") to a mark id.
"use strict";
(() => {
const D = () => window.HQ_LOGO_DATA || {};
const ALIAS = [ // order matters: the more specific first
  [/claude.?code|claudecode/i, "claude-code"], [/codex/i, "codex"], [/chat.?gpt|openai|\bgpt|\bo[134](-|\b)|dall.?e|whisper/i, "chatgpt"],
  [/claude|anthropic|sonnet|opus|haiku/i, "claude"], [/grok/i, "grok"], [/\bxai\b|x\.ai/i, "xai"], [/cursor/i, "cursor"],
  [/gemini|google|gemma/i, "gemini"], [/groq/i, "groq"], [/mistral|mixtral|codestral/i, "mistral"], [/llama|\bmeta\b/i, "meta"],
  [/perplexity|sonar/i, "perplexity"], [/openrouter/i, "openrouter"], [/deepseek/i, "deepseek"], [/qwen/i, "qwen"],
  [/hugging/i, "huggingface"], [/ollama/i, "ollama"], [/copilot/i, "copilot"], [/huhqueue|sources agent|built-in|rules/i, "huhqueue"],
];
// Products that use a company mark: ChatGPT and Codex are OpenAI's Blossom, Grok has its own, Groq has no mark here.
const USE = { chatgpt: "openai", codex: "openai" };
const LABEL = { chatgpt: "ChatGPT", codex: "Codex", groq: "Groq", huhqueue: "HuhQueue", mcp: "MCP client", api: "API token" };
const MONO = { groq: ["Gq", "#F55036"], mcp: ["M", "#5B5B66"], api: ["</>", "#5B5B66"] };
function aiId(text) {
  const t = String(text || "").trim(); if (!t) return "";
  if (D()[t] || LABEL[t]) return t;
  for (const [re, id] of ALIAS) if (re.test(t)) return id;
  return "";
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function aiLabel(text) { const id = aiId(text); return LABEL[id] || (D()[id] && D()[id].label) || String(text || "Agent"); }
function aiMark(text, size = 20) {
  const id = aiId(text), m = D()[USE[id] || id];
  const st = `width:${size}px;height:${size}px`;
  if (id === "huhqueue") return `<span class="aim" style="${st}" data-mark="huhqueue" aria-hidden="true"><img src="/static/brand/hb-mark-96.png" alt=""></span>`;
  if (m) return `<span class="aim" style="${st}" data-mark="${esc(id)}" title="${esc(aiLabel(text))}" aria-hidden="true"><svg viewBox="${esc(m.vb)}" fill="${esc(m.hex === "#000000" || m.hex === "#191919" ? "currentColor" : m.hex)}">${m.svg}</svg></span>`;
  const [mono, bg] = MONO[id] || [(String(text || "?").replace(/[^\p{L}\p{N}]/gu, "").slice(0, 2) || "?"), "#6B6B78"];
  return `<span class="aim mono" style="${st};--aim:${bg}" data-mark="${esc(id || "mono")}" aria-hidden="true">${esc(mono.length > 2 ? mono : mono[0].toUpperCase() + (mono[1] || "").toLowerCase())}</span>`;
}
// A name with its mark: "<mark> Claude Code"
function aiWho(text, size = 18) { return `<span class="aiw">${aiMark(text, size)}<span>${esc(text)}</span></span>`; }
Object.assign(window, { aiId, aiMark, aiLabel, aiWho });
})();
