// The sample workspace's guided tour: seven short steps over the real screens, skippable at any point.
// Keyboard: Enter or the right arrow for Next, the left arrow for Back, Esc to skip. Focus moves to each step's card and
// returns to where it was when the tour ends. It opens by itself once per browser on /?demo=1, and again from
// "Take the tour" in the sample bar. Every step runs on sample data; nothing reaches Buffer.
// Wrapped in its own scope: every script on the page shares one global scope, and a top-level `const T` here collided
// with take.js's `const T`, which made the browser reject this whole file (no auto-open, a dead "Take the tour").
(() => {
const FIRST_URL_DEMO = new URLSearchParams(location.search).get("demo") === "1"; // read before boot rewrites the URL
const TOUR_KEY = "hq_tour_done";
const TOUR = [
  { tab: "inbox", target: "#inboxGroups", title: "Your Inbox",
    body: "Everything waiting for you, most urgent first: an agent post to approve, a post that bounced, a preflight warning, drafts with no date and empty slots this week." },
  { tab: "agent", target: "#agentCompose", title: "Make a post",
    body: "Give the agent a topic or a link. It finds and checks the story, renders it in your brand, writes a caption per channel and checks your Style Ledger. In the sample it can research a few topics a day, then it uses the sample story." },
  { tab: "take", target: "[data-testid=take-sample]", title: "One take → a week",
    body: "Try the sample take: one talk becomes three captioned clips. The Spread drops them into the open posting times of your week as drafts." },
  { tab: "planner", target: "#plMain", title: "Planner",
    body: "List, Week and Month in one place. Drag a post to move it, or a draft from No date onto the week. Preflight checks every change, and Fix it repairs what it can." },
  { tab: "brand", target: "#kitForm", title: "Brand and Style Ledger",
    body: "Import your colors and logo from your website, or set them here. Every correction you give becomes a rule the agent never forgets." },
  { tab: "agents", target: "#tokenForm", title: "Agents and MCP",
    body: "Give Claude, ChatGPT or any agent a token. Agent posts wait in Approvals for you. A token made here is a sample token: it only reaches this sample workspace." },
  { tab: null, target: "[data-testid=demo-connect]", title: "Use it for real",
    body: "Everything you just saw works the same on your own Buffer channels. Connect Buffer when you're ready; nothing is posted until you say so.", last: true },
];
const TR = { i: 0, el: null, ring: null, back: null };

function tourStart() {
  if (TR.el) return;
  TR.back = document.activeElement;
  TR.el = document.createElement("section");
  TR.el.className = "tour"; TR.el.setAttribute("role", "dialog"); TR.el.setAttribute("aria-modal", "false");
  TR.el.setAttribute("aria-labelledby", "tourTitle"); TR.el.setAttribute("aria-describedby", "tourBody"); TR.el.dataset.testid = "tour";
  TR.ring = document.createElement("div"); TR.ring.className = "tour-ring"; TR.ring.setAttribute("aria-hidden", "true");
  document.body.append(TR.ring, TR.el);
  TR.el.addEventListener("keydown", tourKeys);
  addEventListener("resize", tourPlace); addEventListener("scroll", tourPlace, { passive: true });
  tourShow(0);
}
function tourKeys(e) {
  if (e.key === "Escape") { e.preventDefault(); tourEnd(); }
  else if (e.key === "ArrowRight" || (e.key === "Enter" && e.target === TR.el)) { e.preventDefault(); tourNext(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); if (TR.i) tourShow(TR.i - 1); }
}
function tourNext() { if (TR.i < TOUR.length - 1) tourShow(TR.i + 1); else tourEnd(); }
function tourShow(i) {
  TR.i = i;
  const st = TOUR[i];
  if (st.tab) showTab(st.tab);
  TR.el.dataset.step = String(i + 1);
  TR.el.innerHTML = `<span class="tour-step" data-testid="tour-step">Step ${i + 1} of ${TOUR.length}</span>
    <h2 id="tourTitle" data-testid="tour-title">${esc(st.title)}</h2><p id="tourBody">${esc(st.body)}</p>
    <div class="tour-acts"><span class="tour-dots" aria-hidden="true">${TOUR.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("")}</span>
      <span class="row">
        ${st.last ? "" : `<button type="button" class="link" data-testid="tour-skip">Skip tour</button>`}
        ${i ? `<button type="button" class="btn2 sm" data-testid="tour-back">Back</button>` : ""}
        ${st.last ? `<button type="button" class="btn2 sm" data-testid="tour-done">Keep exploring</button><a class="btn accent sm" href="/login" data-testid="tour-connect">Connect Buffer</a>`
          : `<button type="button" class="btn sm" data-testid="tour-next">Next</button>`}
      </span></div>`;
  const on = (id, fn) => { const b = TR.el.querySelector(`[data-testid=${id}]`); if (b) b.onclick = fn; };
  on("tour-skip", tourEnd); on("tour-done", tourEnd); on("tour-next", tourNext); on("tour-back", () => tourShow(TR.i - 1));
  TR.el.tabIndex = -1;
  setTimeout(() => { tourPlace(true); (TR.el.querySelector("[data-testid=tour-next]") || TR.el.querySelector("[data-testid=tour-done]")).focus({ preventScroll: true }); }, 60);
}
function tourTarget() {
  const st = TOUR[TR.i];
  const el = st && document.querySelector(st.target);
  return el && el.getClientRects().length ? el : null;
}
function tourPlace(scroll) {
  if (!TR.el) return;
  const el = tourTarget();
  if (el && scroll === true) {
    const r0 = el.getBoundingClientRect();
    if (r0.top < 80 || r0.top > innerHeight * 0.55) window.scrollTo({ top: Math.max(0, scrollY + r0.top - 110), behavior: "auto" });
  }
  const r = el ? el.getBoundingClientRect() : null;
  if (r) {
    const pad = 6, top = Math.max(4, r.top - pad), h = Math.min(r.height + pad * 2, innerHeight - top - 4);
    Object.assign(TR.ring.style, { display: "", top: top + "px", left: Math.max(4, r.left - pad) + "px", width: Math.min(r.width + pad * 2, innerWidth - 8) + "px", height: Math.max(24, h) + "px" });
  } else TR.ring.style.display = "none";
  if (matchMedia("(max-width:860px)").matches) return; // the card is pinned above the bottom bar on small screens
  const w = TR.el.offsetWidth, h = TR.el.offsetHeight;
  let top = r ? r.bottom + 14 : innerHeight / 2 - h / 2, left = r ? r.left : innerWidth / 2 - w / 2;
  if (r && top + h > innerHeight - 12) top = Math.max(12, r.top - h - 14);
  if (r && top < 12) { top = 12; left = Math.min(r.right + 14, innerWidth - w - 12); }
  TR.el.style.top = Math.max(12, Math.min(top, innerHeight - h - 12)) + "px";
  TR.el.style.left = Math.max(12, Math.min(left, innerWidth - w - 12)) + "px";
}
function tourEnd() {
  try { localStorage.setItem(TOUR_KEY, "1"); } catch (_) {}
  removeEventListener("resize", tourPlace); removeEventListener("scroll", tourPlace);
  if (TR.el) TR.el.remove(); if (TR.ring) TR.ring.remove();
  TR.el = TR.ring = null;
  const back = TR.back && document.contains(TR.back) ? TR.back : $("#tourOpen");
  if (back && back.focus) back.focus();
}
window.tourStart = tourStart; window.tourEnd = tourEnd;
$("#tourOpen").onclick = tourStart;

// Label every screen of the sample as sample data, and open the tour once on the first visit.
function sampleLabels() {
  document.querySelectorAll(".tab .page-title").forEach((h) => {
    if (h.nextElementSibling && h.nextElementSibling.matches("[data-testid=sample-note]")) return;
    h.insertAdjacentHTML("afterend", `<span class="sample-note" data-testid="sample-note">Sample data · nothing reaches Buffer</span>`);
  });
}
// Wait until boot has shown the app and its first tab, so the tour's first step isn't overridden by boot's own showTab.
(function waitForApp() {
  const ready = typeof S !== "undefined" && S.session && (!S.session.connected || !S.session.demo ||
    (!$("#app").hidden && document.querySelector("#app .tab:not([hidden])")));
  if (!ready) return setTimeout(waitForApp, 80);
  if (!S.session.demo) return;
  sampleLabels();
  let done = false; try { done = localStorage.getItem(TOUR_KEY) === "1"; } catch (_) {}
  if (!done && FIRST_URL_DEMO) setTimeout(tourStart, 300);
})();
})();
