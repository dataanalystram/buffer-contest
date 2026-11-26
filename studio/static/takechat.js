// The One take agent's chat (studio/take_agent.py): one conversation per take, shown on the take page and in Clip Studio.
// It answers and makes real edits to the clips' drafts; it renders only when the message asks. Outside text goes through
// textContent / esc, never raw innerHTML.
"use strict";
(() => {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SUGG = ["Make clip 1's opening stronger", "Add b-roll of a busy kitchen to clip 1", "Use cinematic captions on every clip", "Fewer effects on every clip", "What can you change?"];
  function mount(host, o) {
    const p = o.prefix || "tk";
    host.innerHTML = `<div class="tkc" data-testid="${p}-chat">
      <ol class="tkc-log" id="${p}ChatLog" data-testid="${p}-chat-log" aria-live="polite"></ol>
      <div class="tkc-sugg" data-testid="${p}-chat-suggestions">${SUGG.map((s, k) => `<button type="button" class="cst-chip" data-s="${esc(s)}" data-testid="${p}-chat-suggestion" id="${p}-chat-sugg-${k}">${esc(s)}</button>`).join("")}</div>
      <form class="tkc-form" id="${p}ChatForm" data-testid="${p}-chat-form">
        <textarea id="${p}ChatIn" data-testid="${p}-chat-input" rows="2" maxlength="600" placeholder="Ask about your clips, or tell me what to change…" aria-label="Message the One take agent"></textarea>
        <button type="submit" class="btn sm" id="${p}ChatSend" data-testid="${p}-chat-send">Send</button>
      </form></div>`;
    const log = host.querySelector(".tkc-log"), form = host.querySelector("form"), input = host.querySelector("textarea"), send = host.querySelector("[type=submit]");
    let loaded = null, busy = false;
    const draw = (msgs) => {
      log.innerHTML = msgs.length ? msgs.map((m) => `<li class="tkc-m ${m.role === "user" ? "me" : "agent"}" data-testid="${p}-chat-msg" data-role="${m.role}"><p>${esc(m.text)}</p>${m.by !== "AI" ? "" : (m.did || []).filter((d) => d.ops && d.ops.length).map((d) =>
        `<small class="tkc-did" data-testid="${p}-chat-did">${esc(d.clip)}: ${d.ops.map(esc).join("; ")}</small>`).join("")}</li>`).join("")
        : `<li class="tkc-empty muted sm">I'm the agent for this take. Ask me about your clips, or tell me what to change: openings, captions, graphics, B-roll, where things sit. My edits are drafts you can open in Clip Studio and undo.</li>`;
      log.scrollTop = log.scrollHeight;
    };
    const load = async () => {
      const t = o.take(); if (!t || loaded === t.id) return;
      try { const r = await api(`/api/takes/${encodeURIComponent(t.id)}/chat`); loaded = t.id; draw(r.messages || []); } catch (e) { draw([]); }
    };
    const say = async (text) => {
      text = String(text || "").trim(); const t = o.take(); if (!text || busy || !t) return;
      busy = true; send.disabled = true; input.value = "";
      const cur = host.querySelector(".tkc-log"); const li = document.createElement("li"); li.className = "tkc-m me"; li.innerHTML = `<p>${esc(text)}</p>`; cur.appendChild(li);
      const w = document.createElement("li"); w.className = "tkc-m agent pending"; w.innerHTML = `<p class="muted"><span class="spin" aria-hidden="true"></span> Working on it…</p>`; cur.appendChild(w); cur.scrollTop = cur.scrollHeight;
      try {
        const out = await api(`/api/takes/${encodeURIComponent(t.id)}/chat`, { method: "POST", body: { message: text, clip_id: o.clip ? o.clip() : null } });
        loaded = t.id; draw(out.messages || []);
        if (o.onDone) o.onDone(out);
      } catch (e) { w.remove(); const f = document.createElement("li"); f.className = "tkc-m agent err"; f.setAttribute("role", "alert"); f.innerHTML = `<p>${esc(e.message || "That didn't work.")}</p>`; cur.appendChild(f); }
      finally { busy = false; send.disabled = false; }
    };
    form.onsubmit = (e) => { e.preventDefault(); say(input.value); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); say(input.value); } });
    host.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => say(b.dataset.s)));
    load();
    return { load, reset() { loaded = null; load(); }, say };
  }
  window.HQTakeChat = { mount };
})();
