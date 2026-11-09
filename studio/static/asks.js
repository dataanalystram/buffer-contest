// "What you asked" (owner, 9 Nov 2026): the run view shows, per network, what the person asked for and what exists now,
// so a missing channel (Instagram) or a changed format (a LinkedIn carousel that became a video) is seen at a glance,
// not found by reading the replies. Plus a dashed tab for a channel they asked for that has no post yet, and the hook /
// credit / source as chips above each caption. Nothing here changes a post: the buttons write a normal reply in the
// composer and send it (the same path as typing it), and the hook chip only fills the composer for editing.
// Data: GET /api/agent/runs/{id} -> asks {formats: {service: carousel|reel}, hook, credit, channels}.
"use strict";
(() => {
  const NAME = { carousel: "Carousel", reel: "Reel" };
  const VID = { youtube: "Short", tiktok: "Video", linkedin: "Video", facebook: "Reel", instagram: "Reel" };
  const madeAs = (p) => (!p ? "" : p.format === "reel" ? "reel" : p.format === "carousel" ? "carousel" : p.format || "");
  const label = (svc, f) => (f === "reel" ? VID[svc] || "Reel" : NAME[f] || f);
  const connected = (svc) => (S.channels || []).find((c) => c.service === svc && !c.isDisconnected);

  function send(text) {
    const box = $("#replyIn"), form = $("#replyForm");
    if (!box || !form) return;
    box.value = text; box.dispatchEvent(new Event("input"));
    form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
  }
  function draft(text) {
    const box = $("#replyIn"); if (!box) return;
    box.value = text; box.dispatchEvent(new Event("input")); box.focus();
    const i = text.lastIndexOf('"'); box.setSelectionRange(i > 0 ? i : text.length, i > 0 ? i : text.length);
  }
  const askFor = (svc, f) => `Make the ${label(svc, f || "reel")} for my ${netName(svc)} too, with the same video, credit and theme.`;

  function rows(r) {
    const a = r.asks || {}, pk = (r.result || {}).packages || [], f = a.formats || {};
    const svcs = [...new Set([...Object.keys(f), ...pk.map((p) => p.service)])];
    return svcs.map((svc) => {
      const p = pk.find((x) => x.service === svc), want = f[svc] || "", got = madeAs(p);
      const state = !p ? (connected(svc) ? "missing" : "offline") : want && got && want !== got ? "changed" : "made";
      return { svc, want, got, p, state };
    });
  }

  function card(r) {
    const a = r.asks || {}, rs = rows(r);
    if (!Object.keys(a.formats || {}).length && !a.hook && !a.credit) return "";
    const pill = (x) => {
      const what = esc(x.want ? label(x.svc, x.want) : x.p ? label(x.svc, x.got) : "");
      const st = x.state === "made" ? `<b class="ask-ok">✓ made</b>`
        : x.state === "changed" ? `<b class="ask-need">made as ${esc(label(x.svc, x.got))}</b>`
        : x.state === "missing" ? `<b class="ask-need">not made yet</b>` : `<b class="ask-off">not connected in Buffer</b>`;
      const btn = x.state === "missing" ? `<button type="button" class="btn sm" data-ask-add="${esc(x.svc)}" data-ask-fmt="${esc(x.want)}" data-testid="ask-add">Add ${esc(netName(x.svc))}</button>`
        : x.state === "changed" ? `<button type="button" class="btn2 sm" data-ask-fix="${esc(x.svc)}" data-ask-fmt="${esc(x.want)}" data-testid="ask-fix">Make it a ${esc(label(x.svc, x.want))}</button>` : "";
      return `<li class="ask-pill ${x.state === "made" ? "" : "need"}" data-testid="ask-row" data-svc="${esc(x.svc)}">${esc(netName(x.svc))}${what ? " · " + what : ""} · ${st}${btn}</li>`;
    };
    const meta = [a.hook ? `<span>Hook <b>“${esc(a.hook)}”</b></span>` : "", a.credit ? `<span>Credit <b>${esc(a.credit)}</b></span>` : ""].filter(Boolean).join("");
    return `<section class="ask-card" data-testid="ask-card" aria-label="What you asked"><h3>What you asked <small>from all your messages · later words win</small></h3>
      <ul class="ask-list">${rs.map(pill).join("")}</ul>${meta ? `<div class="ask-meta" data-testid="ask-meta">${meta}</div>` : ""}</section>`;
  }

  function missingTabs(r) {
    return rows(r).filter((x) => x.state === "missing").map((x) => {
      const ch = connected(x.svc);
      return `<span class="ask-miss" data-testid="ask-missing-tab">${window.chMark ? window.chMark(ch, true) : ""}<span><b>${esc(ch.displayName || ch.name || netName(x.svc))}</b>
        <small>${esc(netName(x.svc))} · you asked for ${x.want ? "a " + esc(label(x.svc, x.want)) : "a post"}</small></span>
        <button type="button" class="btn sm" data-ask-add="${esc(x.svc)}" data-ask-fmt="${esc(x.want)}" data-testid="ask-make">Make it</button></span>`;
    }).join("");
  }

  function chips(r) {
    const a = r.asks || {}, src = ((r.result || {}).source || {}).url || "";
    let host = ""; try { host = src ? new URL(src).hostname.replace(/^www\./, "") + new URL(src).pathname.split("/").slice(0, 2).join("/") : ""; } catch (_) {}
    document.querySelectorAll("#packages .pkg").forEach((el) => {
      if (el.querySelector(".ask-chips")) return;
      const ta = el.querySelector("textarea[data-testid=pkg-text]"); if (!ta || ta.disabled) return;
      const p = ((r.result || {}).packages || []).find((x) => x.channel_id === el.dataset.ch) || {};
      const first = (ta.value || "").split("\n")[0].trim();
      const hook = a.hook || first;
      const vid = p.format === "reel";
      const c = document.createElement("div");
      c.className = "ask-chips"; c.dataset.testid = "ask-chips";
      c.innerHTML = [hook ? `<button type="button" class="ask-chip" data-testid="ask-hook" title="Change the hook word for word">Hook ✎ <span>${esc(hook)}</span></button>` : "",
        vid && a.credit ? `<span class="ask-chip" data-testid="ask-credit">${esc(a.credit)}</span>` : "",
        host ? `<span class="ask-chip" data-testid="ask-source">Source ${esc(host)}</span>` : ""].join("");
      ta.parentNode.insertBefore(c, ta);
      const hb = c.querySelector("[data-testid=ask-hook]");
      if (hb) hb.onclick = () => draft(`Use this hook word for word on ${netName(p.service || "")}: "${hook}"`);
    });
  }

  window.hqAsks = (r) => {
    const box = $("#packages"); if (!box) return;
    box.querySelectorAll(".ask-card,.ask-miss-solo").forEach((n) => n.remove());
    if (!r || !r.asks || !box.innerHTML.trim()) return;
    const html = card(r);
    if (html) box.insertAdjacentHTML("afterbegin", html);
    const miss = missingTabs(r);
    if (miss) {
      const tabs = box.querySelector("[data-testid=pkg-tabs]");
      if (tabs) tabs.insertAdjacentHTML("beforeend", miss);
      else box.querySelector(".ask-card")?.insertAdjacentHTML("afterend", `<div class="ask-miss-solo">${miss}</div>`);
    }
    chips(r);
    box.querySelectorAll("[data-ask-add]").forEach((b) => (b.onclick = () => { b.disabled = true; send(askFor(b.dataset.askAdd, b.dataset.askFmt)); }));
    box.querySelectorAll("[data-ask-fix]").forEach((b) => (b.onclick = () => { b.disabled = true;
      send(`Make my ${netName(b.dataset.askFix)} post a ${label(b.dataset.askFix, b.dataset.askFmt)} as I asked. Leave the other channels as they are.`); }));
  };
})();
