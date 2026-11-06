// News › Sources: the source library. Free feeds by category, newest first, the same story merged across sources
// ("also on 3 sources"), a status dot per source, Refresh (once a minute) and a per-source on/off switch.
// UI = REST = MCP: GET /api/sources/library = list_sources, POST /api/sources/refresh = refresh_sources,
// POST /api/sources/enabled = set_source_enabled.
"use strict";
(function () {
  const L = { v: null, cat: "", hours: 48, page: 1, busy: false, wait: 0, timer: 0 };
  const PER = 12;
  const CATS = ["Launches", "AI labs", "Tech news", "Research", "Videos", "Open source"];
  const DOT = { ok: "Working", slow: "Slow", failing: "Failing", unchecked: "Not checked yet" };
  const ago = (t) => (t ? timeAgo(t * 1000) : "never");
  const HTML = `
    <section class="panel lib" aria-labelledby="libH" data-testid="lib-panel">
      <div class="lib-head">
        <div><h2 class="h2" id="libH">Source library</h2>
          <p class="muted sm" id="libSub">Free sources by category. Duplicates are merged, so a story on several sites shows up once.</p></div>
        <div class="lib-go">
          <span class="muted xs" id="libUpd" data-testid="lib-updated" aria-live="polite"></span>
          <button class="btn2 sm lib-refresh" type="button" id="libRefresh" data-testid="lib-refresh">
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg><span>Refresh</span></button>
        </div>
      </div>
      <p class="lib-msg" id="libMsg" role="status" data-testid="lib-msg" hidden></p>
      <div class="lib-bar">
        <div class="lib-chips" id="libChips" role="group" aria-label="Category" data-testid="lib-chips"></div>
        <div class="seg lib-age" role="group" aria-label="How recent" data-testid="lib-age">
          <button type="button" data-h="48" class="on" aria-pressed="true">48 h</button><button type="button" data-h="168" aria-pressed="false">7 days</button></div>
      </div>
      <ul class="lib-items" id="libItems" data-testid="lib-items"><li class="lib-skel" aria-busy="true"><span></span><span></span><span></span></li></ul>
      <div id="libPager" class="pg-wrap" data-testid="lib-pager" hidden></div>
      <details class="lib-set" id="libSet" data-testid="lib-settings">
        <summary><span><b>Sources</b> <small class="muted" id="libSetSum"></small></span><span class="muted xs">Turn a source off to hide it here, in Story scan and in the agent's research.</span></summary>
        <div id="libSrcs" class="lib-srcs"></div>
      </details>
    </section>`;

  function mount() {
    const host = document.getElementById("libMount");
    if (!host || host.dataset.on) return;
    host.dataset.on = "1"; host.innerHTML = HTML;
    $("#libRefresh").onclick = refresh;
    document.querySelectorAll("#libMount .lib-age [data-h]").forEach((b) => (b.onclick = () => {
      L.hours = +b.dataset.h; L.page = 1;
      document.querySelectorAll("#libMount .lib-age [data-h]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
      load();
    }));
    setInterval(stamp, 30000);
    const prev = (window.HS_SUBS || {})["news:sources"];
    window.HS_SUBS = Object.assign(window.HS_SUBS || {}, { "news:sources": () => { if (prev) prev(); load(); } });
    if (!document.getElementById("tab-news")?.hidden && document.getElementById("tab-news")?.dataset.sub === "sources") load();
  }

  async function load() {
    try { show(await api(`/api/sources/library?limit=200&fresh_hours=${L.hours}`)); }
    catch (e) { msg(e.message, "bad"); $("#libItems").innerHTML = ""; }
  }
  function msg(text, kind) {
    const m = $("#libMsg"); if (!m) return;
    m.hidden = !text; m.textContent = text || ""; m.className = `lib-msg ${kind || ""}`;
  }
  function stamp() {
    if (!L.v) return;
    $("#libUpd").textContent = L.v.updated ? `Updated ${ago(L.v.updated)}` : "Not refreshed yet";
  }
  function button() {
    const b = $("#libRefresh"); if (!b || !L.v) return;
    const label = b.querySelector("span");
    b.classList.toggle("busy", L.busy);
    if (L.busy) { b.disabled = true; b.setAttribute("aria-busy", "true"); label.textContent = "Refreshing…"; return; }
    b.removeAttribute("aria-busy");
    if (L.v.read_only) { b.disabled = true; b.title = "Sign in to refresh"; label.textContent = "Refresh"; return; }
    b.disabled = L.wait > 0; b.title = L.wait > 0 ? `You can refresh again in ${L.wait} s` : "Re-read every source you keep on";
    label.textContent = L.wait > 0 ? `Refresh in ${L.wait} s` : "Refresh";
  }
  function cooldown(s) {
    L.wait = Math.max(0, Math.ceil(s || 0)); clearInterval(L.timer); button();
    if (L.wait) L.timer = setInterval(() => { L.wait -= 1; if (L.wait <= 0) { L.wait = 0; clearInterval(L.timer); } button(); }, 1000);
  }
  async function refresh() {
    if (L.busy || L.wait) return;
    L.busy = true; button(); msg("");
    try {
      const v = await api("/api/sources/refresh", { method: "POST", body: {} });
      L.v = null; show(v);
      const live = v.counts.with_items;
      msg(v.failing.length ? `Updated. ${live} sources have stories; ${v.failing.length} didn't answer (${v.failing.slice(0, 3).join(", ")}${v.failing.length > 3 ? "…" : ""}), so their last copy is shown.`
        : `Updated. ${live} sources have stories from the last ${L.hours === 48 ? "48 hours" : "7 days"}.`, v.failing.length ? "warn" : "ok");
      cooldown(v.refresh_every || 60);
    } catch (e) {
      msg(e.message, e.status === 429 ? "warn" : "bad");
      const m = /in (\d+) s/.exec(e.message || ""); if (m) cooldown(+m[1]);
    } finally { L.busy = false; button(); }
  }

  function show(v) {
    L.v = v; if (!L.wait && v.refresh_in) cooldown(v.refresh_in);
    $("#libSub").textContent = v.read_only ? v.note
      : `${v.counts.sources} free sources by category. Duplicates are merged, so a story on several sites shows up once.`;
    stamp(); button(); chips(); list(); settings();
  }
  function chips() {
    const v = L.v, n = Object.fromEntries(v.categories.map((c) => [c.name, c.items]));
    const all = v.categories.reduce((a, c) => a + c.items, 0);
    const box = $("#libChips");
    box.innerHTML = [["", "All", all], ...CATS.map((c) => [c, c, n[c] || 0])].map(([k, label, cnt]) =>
      `<button type="button" class="chip mini${L.cat === k ? " on" : ""}" data-cat="${esc(k)}" aria-pressed="${L.cat === k}" data-testid="lib-chip">${esc(label)} <small>${cnt}</small></button>`).join("");
    box.querySelectorAll("[data-cat]").forEach((b) => (b.onclick = () => { L.cat = b.dataset.cat; L.page = 1; chips(); list(); }));
  }
  function card(it) {
    const host = hostOf(it.url), vid = it.video, paper = it.type === "paper";
    const when = it.evergreen ? "HN favourite" : it.published ? ago(it.published) : "";
    const also = it.also_n ? `<span class="lib-also" title="Also on ${esc(it.also.join(", "))}" data-testid="lib-also">also on ${it.also_n} source${it.also_n > 1 ? "s" : ""}</span>` : "";
    const pic = it.image ? `<span class="lib-pic${vid ? " vid" : ""}"><img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">${vid ? `<span class="play" aria-hidden="true"></span>` : ""}</span>`
      : `<span class="lib-pic nopic" aria-hidden="true">${fav(host)}</span>`;
    return `<li class="lib-item${paper ? " paper" : ""}" data-testid="lib-item" data-type="${esc(it.type)}">
      ${pic}
      <div class="lib-b">
        <div class="lib-meta">${fav(host)}<span class="lib-src">${esc(it.source)}</span>${when ? `<time>${esc(when)}</time>` : ""}<span class="lib-type t-${esc(it.type)}" data-testid="lib-type">${esc(it.type_label)}</span>${also}</div>
        <a class="lib-t" href="${escUrl(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
        ${it.summary ? `<p class="lib-sum">${esc(it.summary)}</p>` : ""}
        <div class="lib-act">${vid ? `<a class="btn2 sm" href="${escUrl(vid.url)}" target="_blank" rel="noopener" data-testid="lib-watch">Watch on YouTube</a><span class="muted xs" title="${esc(vid.note)}">Link or embed only · reuse: ${esc(vid.reuse)}</span>`
          : `<button class="btn sm" type="button" data-make="${esc(it.url)}" data-title="${esc(it.title || "")}" data-testid="lib-make">Make post</button>`}${it.discussion ? `<a class="link sm" href="${escUrl(it.discussion)}" target="_blank" rel="noopener">HN discussion</a>` : ""}</div>
      </div></li>`;
  }
  function list() {
    const rows = L.v.items.filter((it) => !L.cat || it.category === L.cat);
    const box = $("#libItems");
    const pages = Math.max(1, Math.ceil(rows.length / PER)); L.page = Math.min(L.page, pages);
    if (!rows.length) {
      const none = !L.v.updated ? (L.v.read_only ? "No saved copy yet. Sign in to read the sources live." : "Nothing read yet. Press Refresh to read every source.")
        : `No stories in ${L.cat || "these sources"} from the last ${L.hours === 48 ? "48 hours" : "7 days"}.${L.hours === 48 ? " Try 7 days." : ""}`;
      box.innerHTML = `<li class="lib-none" data-testid="lib-empty">${esc(none)}</li>`;
    } else box.innerHTML = rows.slice((L.page - 1) * PER, L.page * PER).map(card).join("");
    box.querySelectorAll(".lib-pic img").forEach((im) => (im.onerror = () => { const p = im.closest(".lib-pic"); p.classList.add("nopic"); im.remove(); }));
    if (typeof wireMake === "function") wireMake(box);
    if (typeof favicons === "function") favicons(box);
    window.hqPager($("#libPager"), { total: rows.length, page: L.page, per: PER, label: "Source library pages",
      onPage: (n) => { L.page = n; list(); box.scrollIntoView({ behavior: "smooth", block: "start" }); } });
  }
  function settings() {
    const v = L.v, ro = v.read_only;
    const on = v.sources.filter((s) => s.enabled).length, bad = v.sources.filter((s) => s.enabled && s.status === "failing").length;
    $("#libSetSum").textContent = `${on} of ${v.sources.length} on${bad ? ` · ${bad} failing` : ""}`;
    $("#libSrcs").innerHTML = CATS.map((c) => `<div class="lib-grp"><h3>${esc(c)}</h3><ul>${v.sources.filter((s) => s.category === c).map((s) => `
      <li class="lib-row${s.enabled ? "" : " off"}" data-testid="lib-source" data-id="${esc(s.id)}">
        <span class="dot ${esc(s.status)}" role="img" aria-label="${esc(DOT[s.status])}" title="${esc(DOT[s.status] + (s.error ? ": " + s.error : ""))}"></span>
        <span class="lib-rn"><b>${esc(s.label)}</b><small>${esc(s.type_label)} · ${s.last_checked ? `checked ${esc(ago(s.last_checked))}` : "not checked yet"}${s.status === "slow" ? ` · slow (${(s.ms / 1000).toFixed(1)} s)` : ""}${s.status === "failing" ? ` · ${esc(s.error || "didn't answer")}` : ""}</small></span>
        <span class="lib-n muted xs">${s.items}</span>
        <label class="switch sm" title="${ro ? "Sign in to choose sources" : s.enabled ? "On" : "Off"}"><input type="checkbox" role="switch" data-src="${esc(s.id)}" ${s.enabled ? "checked" : ""} ${ro ? "disabled" : ""} aria-label="${esc(s.label)}" data-testid="lib-toggle"><span></span></label>
      </li>`).join("")}</ul></div>`).join("");
    $("#libSrcs").querySelectorAll("[data-src]").forEach((i) => (i.onchange = async () => {
      i.disabled = true;
      try { await api("/api/sources/enabled", { method: "POST", body: { source_id: i.dataset.src, enabled: i.checked } }); await load(); }
      catch (e) { i.checked = !i.checked; toast(e.message); }
      finally { i.disabled = false; }
    }));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
