// Make a post › Product panel (studio/commerce.py; UI = REST = MCP: search_products, get_product, attach_product_to_post).
//  * search Channel3 for a product (1 free credit; the meter line shows what's left; it stops at the free allowance)
//  * pick one: a fresh price, then "Add to post" attaches a HuhQueue shop link and puts #Ad first + the disclosure line
//    in this channel's caption (the caption helper), and shows the text product card (name, price, CTA, link label)
//  * no catalogue images (not licensed for social posts): the card is text on the brand kit
//  * your own Channel3 key (optional): sealed on the server, only "…abcd" is ever shown
// Product text is untrusted: everything goes through E() (escaped) — never innerHTML of raw values.
// A newer search, a close or a re-render aborts the request in flight (AbortController), so a late answer can't
// overwrite the panel.
"use strict";
(() => {
const E = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const live = new WeakMap();   // panel element -> AbortController of its request in flight

function card(p, sent) {
  if (sent) return "";
  return `<details class="cm-panel" data-testid="product-panel"><summary class="cm-sum">Product <small class="muted xs">add a product and a shop link</small></summary>
    <div class="cm-body">
      <form class="cm-search" data-testid="product-search" role="search">
        <label class="sr" for="cmq-${E(p.channel_id)}">Search products</label>
        <input id="cmq-${E(p.channel_id)}" type="search" maxlength="200" placeholder="e.g. wireless headphones under 150" data-testid="product-q" autocomplete="off">
        <input type="number" min="0" step="1" inputmode="numeric" placeholder="Max €" aria-label="Most you'd pay" data-testid="product-max">
        <button type="submit" class="btn2 sm" data-testid="product-go">Search</button>
      </form>
      <small class="muted xs cm-meter" data-testid="product-meter" aria-live="polite">Product search is free: it stops at the free monthly credits.</small>
      <div class="cm-results" data-testid="product-results" aria-live="polite"></div>
      <div class="cm-pick" data-testid="product-pick" hidden></div>
      <p class="muted xs cm-disc" data-testid="product-disclosure">#Ad goes first in a post with a shop link (Irish rule). We may earn a commission when you buy through links in this post. Catalogue photos aren't licensed for social posts: the card is text on your brand kit, or add your own photo.</p>
      <details class="cm-key" data-testid="product-key"><summary class="muted xs">Use your own Channel3 key</summary>
        <form class="cm-keyform"><label class="sr" for="cmk-${E(p.channel_id)}">Your Channel3 API key</label>
          <input id="cmk-${E(p.channel_id)}" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your Channel3 key" data-testid="product-key-input">
          <button type="submit" class="btn2 sm" data-testid="product-key-save">Save</button>
          <button type="button" class="btn2 sm" data-testid="product-key-remove" hidden>Remove</button></form>
        <small class="muted xs" data-testid="product-key-status">Your key spends your own free credits; HuhQueue stops at the free limit. Shop links always use HuhQueue's key.</small>
      </details>
    </div></details>`;
}

function abort(el) { const c = live.get(el); if (c) c.abort(); const n = new AbortController(); live.set(el, n); return n.signal; }
function meter(el, u) { const m = el.querySelector("[data-testid=product-meter]"); if (m && u && u.line) m.textContent = u.line; }

function cardHtml(c) {
  return `<div class="cm-card" data-testid="product-card"><b>${E(c.name)}</b><span class="cm-price">${E(c.price)}</span>
    <span class="cm-cta">${E(c.cta)}</span><small class="muted xs">${E(c.link_label)}${c.retailer ? " · " + E(c.retailer) : ""}</small></div>`;
}

function wire(el, r, pk) {
  const panel = el.querySelector("[data-testid=product-panel]");
  if (!panel) return;
  const res = panel.querySelector("[data-testid=product-results]"), pick = panel.querySelector("[data-testid=product-pick]");
  const ta = el.querySelector("textarea[data-testid=pkg-text]");
  panel.addEventListener("toggle", async () => {
    if (!panel.open) { const c = live.get(panel); if (c) c.abort(); return; }
    try { const k = await api("/api/commerce/key", { signal: abort(panel) }); meter(panel, k.usage); keyState(k); } catch (_) { /* aborted or offline */ }
  });
  function keyState(k) {
    const st = panel.querySelector("[data-testid=product-key-status]"), rm = panel.querySelector("[data-testid=product-key-remove]");
    if (k.saved) { st.textContent = `Your Channel3 key ${k.hint} is in use for searches.`; rm.hidden = false; } else rm.hidden = true;
  }
  panel.querySelector("[data-testid=product-search]").onsubmit = async (ev) => {
    ev.preventDefault();
    const q = panel.querySelector("[data-testid=product-q]").value.trim(), mx = panel.querySelector("[data-testid=product-max]").value;
    if (!q) return;
    if (window.demoSays && demoSays("search products on Channel3")) return;
    const signal = abort(panel);
    pick.hidden = true; pick.innerHTML = "";
    res.innerHTML = `<p class="muted sm"><span class="spin" aria-hidden="true"></span> Searching…</p>`;
    const body = { query: q }; if (mx !== "" && Number(mx) >= 0) body.budget_max = Number(mx);
    try {
      const d = await api("/api/commerce/search", { method: "POST", body, signal });
      meter(panel, d.usage);
      res.innerHTML = (d.products || []).map((p) => `<button type="button" class="cm-item" data-pid="${E(p.id)}" data-testid="product-item">
          <b>${E(p.title)}</b><small class="muted">${E(p.brand)}${p.brand ? " · " : ""}${E(p.price_label || "No price")}${p.retailer ? " at " + E(p.retailer) : ""}${p.rating ? " · ★ " + E(p.rating.toFixed(1)) : ""}</small>
          ${p.affiliate ? `<span class="cm-ad" title="This shop pays a commission">Ad link</span>` : ""}</button>`).join("") ||
        `<p class="muted sm">Nothing found. Try other words.</p>`;
      res.querySelectorAll("[data-pid]").forEach((b) => (b.onclick = () => choose(b.dataset.pid)));
    } catch (e) { if (e.name !== "AbortError") res.innerHTML = `<p class="st warn">${E(e.message)}</p>`; }
  };
  async function choose(pid) {
    const signal = abort(panel);
    pick.hidden = false; pick.innerHTML = `<p class="muted sm"><span class="spin" aria-hidden="true"></span> Checking today's price…</p>`;
    try {
      const d = await api(`/api/commerce/products/${encodeURIComponent(pid)}`, { signal });
      const p = d.product;
      pick.innerHTML = `<div class="cm-detail"><b>${E(p.title)}</b><span>${E(p.price_label || "")}${p.retailer ? " at " + E(p.retailer) : ""}</span>
        ${(p.key_features || []).length ? `<ul>${p.key_features.map((f) => `<li>${E(f)}</li>`).join("")}</ul>` : ""}
        <div class="cm-row"><label>CTA <input data-testid="product-cta" maxlength="40" value="Shop now"></label>
        <label>Link label <input data-testid="product-label" maxlength="40" value="Link in bio"></label></div>
        <button type="button" class="btn sm" data-testid="product-attach">Add to post</button></div>`;
      pick.querySelector("[data-testid=product-attach]").onclick = () => attach(pid);
    } catch (e) { if (e.name !== "AbortError") pick.innerHTML = `<p class="st warn">${E(e.message)}</p>`; }
  }
  async function attach(pid) {
    const btn = pick.querySelector("[data-testid=product-attach]"), signal = abort(panel);
    btn.disabled = true; btn.textContent = "Adding…";
    try {
      const d = await api("/api/commerce/attach", { method: "POST", signal, body: { run_id: r.id, product_id: pid, caption: ta ? ta.value : "",
        cta: pick.querySelector("[data-testid=product-cta]").value, link_label: pick.querySelector("[data-testid=product-label]").value } });
      if (ta && !ta.disabled) { ta.value = d.caption; ta.dispatchEvent(new Event("input", { bubbles: true })); }
      pick.innerHTML = cardHtml(d.card) + `<small class="muted xs">Added: #Ad first, the shop link and the disclosure line are in the caption. The link finds a fresh offer when the post goes out.</small>`;
      toast("Product added to the caption with #Ad");
    } catch (e) { if (e.name !== "AbortError") { toast(e.message, 6000); btn.disabled = false; btn.textContent = "Add to post"; } }
  }
  panel.querySelector(".cm-keyform").onsubmit = async (ev) => {
    ev.preventDefault();
    const inp = panel.querySelector("[data-testid=product-key-input]"), v = inp.value.trim();
    if (!v) return;
    try { const k = await api("/api/commerce/key", { method: "POST", body: { value: v } }); inp.value = ""; keyState(k); meter(panel, k.usage); toast("Your Channel3 key is saved"); }
    catch (e) { inp.value = ""; toast(e.message, 6000); }
  };
  panel.querySelector("[data-testid=product-key-remove]").onclick = async () => {
    try { const k = await api("/api/commerce/key", { method: "DELETE" }); keyState(k); meter(panel, k.usage);
      panel.querySelector("[data-testid=product-key-status]").textContent = "Searches use HuhQueue's free credits again."; }
    catch (e) { toast(e.message, 6000); }
  };
}

window.hqProduct = { card, wire, cardHtml };
})();
