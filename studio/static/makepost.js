// Make a post v2 (owner, 1 Nov 2026): stepper, mode cards, quick-action chips, the Discover panel, Recent content and the
// chat-style agent thread. Vanilla JS on top of house.js (which still owns the run itself: submit, polling, packages).
// Patterns borrowed (not code): assistant-ui's thread / composer, shadcn AI Elements' tool + sources + status parts,
// prompt-kit's prompt input and suggestion chips (all MIT). See docs/UI_CREDITS.md.
"use strict";
(() => {
  const root = document.getElementById("tab-agent");
  if (!root) return;
  const MP = { disc: "foryou", stories: [], at: 0, recent: [], filter: "all", page: 1 };
  const ic = (d) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const I = {
    rocket: '<path d="M5 15c-1.5 1.5-2 4-2 6 2 0 4.5-.5 6-2"/><path d="M9 15l-3-3c2-6 7-9 14-9 0 7-3 12-9 14z"/><circle cx="15" cy="9" r="1.5"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>', box: '<rect x="3" y="7" width="13" height="10" rx="2"/><path d="M16 11l5-3v8l-5-3"/>',
    reel: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 8h16M9 3l2 5M15 3l2 5"/><path d="M10 12l5 3-5 3z"/>',
    cal: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>', cut: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.5 8.5L20 20M8.5 15.5L20 4"/>',
    dots: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
    out: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>', play: '<path d="M8 5l11 7-11 7z"/>',
    slides: '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="M21 7v10"/>', img: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    text: '<path d="M5 6h14M5 12h14M5 18h9"/>',
  };
  const EXAMPLES = [
    "Find the latest AI startup launches from Y Combinator and create a 60-second Instagram Reel with key highlights and a catchy caption.",
    "Post this on all my channels as a short punchy explainer: https://x.com/AarushYadav47/status/2116198463847408066",
    "Make a carousel for LinkedIn and Instagram on the three biggest robotics stories this week, and end every video with Follow @huhbyte.ai",
  ];
  const CHIPS = [
    ["Find trending AI news", I.rocket, "Find the most talked-about AI news from the last 24 hours and make a punchy carousel for all my channels.", "carousel"],
    ["YC startup launches", I.layers, "Find today's Y Combinator startup launches and make a 45-second Reel with the three best, with a catchy caption.", "reel"],
    ["Explain a product", I.box, "Explain this product in plain words as a short punchy explainer: ", ""],
    ["Create a Reel", I.reel, "Make a 30-second Reel about ", "reel"],
    ["Weekly recap", I.cal, "Make a weekly recap carousel of the biggest AI and startup stories this week.", "carousel"],
    ["Split a long video", I.cut, "@take", ""],
  ];
  const MORE = [["Announce a launch", "Announce our launch on every channel: "], ["Fact-check a claim", "Check this claim against other outlets before posting: "],
    ["Thread to carousel", "Turn this X thread into a 6-slide carousel: "], ["Text-only update", "Write a short text-only update for all my channels about ", "text"]];
  const DISC = [["foryou", "For you", null], ["ai", "AI launches", /\b(AI|A\.I\.|model|GPT|Claude|Gemini|Llama|LLM|agent|OpenAI|Anthropic|DeepMind|Mistral)\b/i],
    ["startups", "Startups", /\b(YC|Y Combinator|startup|raises|raised|funding|seed|Series [A-D]|launch(es|ed)?|founder)\b/i],
    ["robotics", "Robotics", /\b(robot\w*|humanoid|drone|autonomous|self-driving|Figure|Boston Dynamics|Optimus)\b/i]];
  const FILTERS = [["all", "All"], ["reel", "Reels"], ["carousel", "Carousels"], ["image", "Images"], ["draft", "Drafts"]];
  const STATE = { ready: "Ready", draft: "Draft", scheduled: "Scheduled", running: "Working", failed: "Failed", interrupted: "Interrupted" };
  const q = () => $("#agQuery");
  const fill = (text, fmt) => { const t = q(); t.value = text; t.dispatchEvent(new Event("input")); t.focus(); t.setSelectionRange(t.value.length, t.value.length);
    if (fmt) { $("#agFormat").value = fmt; window.mpThemePicker && window.mpThemePicker.refresh(); } };

  // ------------------------------------------------------------ stepper
  function setStep(name) {
    const order = ["discover", "create", "review"], at = order.indexOf(name);
    $("#mpSteps").querySelectorAll("li").forEach((li, i) => { li.classList.toggle("done", i < at);
      if (i === at) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current"); });
  }
  $("#mpSteps").querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => {
    const g = b.dataset.goto;
    if (g === "review") { if (H_STATE.run) { root.classList.add("mp-in-thread"); $("#runView").hidden = false; $("#packages").scrollIntoView({ behavior: "smooth" }); } else toast("Create a post first, then review it here."); return; }
    leaveThread();
    if (g === "discover") { setStep("discover"); $("#mpDiscover").scrollIntoView({ behavior: "smooth", block: "start" }); $("#mpDiscover").focus?.(); }
    else { setStep("create"); q().focus(); }
  }));

  // ------------------------------------------------------------ mode cards: "From a link" is a real mode (owner, 1 Nov 2026:
  // the card did nothing visible). It selects like the other two, shows a URL field (focused), previews the pasted link
  // (POST /api/link/preview: the X reader for posts, the page reader for articles, server-side through netguard) and
  // Create runs the agent with that link as THE source.
  const linkCard = root.querySelector("[data-mode=link]"), LK = { url: "", t: null, seq: 0, ok: false };
  const isUrl = (u) => /^https?:\/\/[^\s/$.?#][^\s]*$/i.test(u || "");
  function linkMode(on) {
    root.classList.toggle("mp-link-on", on);
    $("#mpLink").hidden = !on;
    linkCard.classList.toggle("on", on); linkCard.setAttribute("aria-selected", String(on)); linkCard.tabIndex = on ? 0 : -1;
    if (on) root.querySelectorAll(".mp-modes [data-sub]").forEach((b) => { b.classList.remove("on"); b.setAttribute("aria-selected", "false"); b.tabIndex = -1; });
    $("#agQueryLbl").textContent = on ? "What should the post say? (optional)" : "What should we post?";
    q().placeholder = on ? "E.g. “a short punchy explainer for all my channels”. Leave it empty and the agent picks the format."
      : "E.g. “Find the latest AI startup launches from Y Combinator and create a 60-second Instagram Reel with key highlights and a catchy caption.”";
    $("#agGo").lastChild.textContent = on ? "Create from link" : "Create with agent";
  }
  linkCard.onclick = () => {
    root.querySelector("[data-testid=agent-mode-agent]").click();  // the agent pane, then mark the link mode
    leaveThread(); linkMode(true);
    if (/^Turn this into a post for all my channels:\s*$/.test(q().value)) { q().value = ""; q().dispatchEvent(new Event("input")); }
    $("#mpLinkUrl").focus(); setStep("create");
  };
  root.querySelectorAll(".mp-modes [data-sub]").forEach((b) => b.addEventListener("click", () => linkMode(false)));
  async function preview() {
    const u = $("#mpLinkUrl").value.trim(), box = $("#mpLinkPreview"), n = ++LK.seq;
    $("#mpLinkClear").hidden = !u; LK.ok = false;
    if (!u) { box.hidden = true; box.innerHTML = ""; return; }
    if (!isUrl(u)) { box.hidden = false; box.innerHTML = `<p class="mp-lp-err" data-testid="mp-link-error">That doesn't look like a link yet. It should start with https://</p>`; return; }
    box.hidden = false; box.innerHTML = `<div class="mp-lp-load" aria-busy="true"><span class="spin" aria-hidden="true"></span>Reading ${esc(hostOf(u) || "the link")}…</div>`;
    try {
      const r = await api("/api/link/preview", { method: "POST", body: { url: u } });
      if (n !== LK.seq) return;
      LK.url = r.url || u; LK.ok = true;
      const pic = r.image || r.url ? window.hqThumb(r.url || u, r.image || "") : "";
      box.innerHTML = `<div class="mp-lp-card" data-testid="mp-link-card">${pic ? `<span class="mp-lp-pic"><img src="${esc(pic)}" alt="" loading="lazy"></span>` : ""}
        <span class="mp-lp-b"><small class="mp-lp-site">${fav(hostOf(r.url || u))}${esc(r.site || hostOf(u))}${r.author ? " · " + esc(r.author) : ""}</small>
        <b class="mp-lp-t" data-testid="mp-link-title">${esc(r.title || u)}</b>${r.description ? `<span class="mp-lp-d">${esc(r.description.slice(0, 220))}</span>` : ""}
        ${r.media && r.media.length ? `<small class="mp-lp-m">${esc(r.media.join(" + "))} in the post</small>` : ""}</span></div>`;
      const im = box.querySelector("img"); if (im) im.onerror = () => im.closest(".mp-lp-pic").remove();
      if (typeof favicons === "function") favicons(box);
    } catch (e) {
      if (n !== LK.seq) return;
      LK.url = u; LK.ok = true;  // a page we can't preview can still be the source: the agent reads it with its fallbacks
      box.innerHTML = `<p class="mp-lp-err" data-testid="mp-link-error">${esc(e.message || "Couldn't preview that link")}. You can still press Create: the agent tries again with its other readers.</p>`;
    }
  }
  $("#mpLinkUrl").addEventListener("input", () => { clearTimeout(LK.t); LK.t = setTimeout(preview, 350); });
  $("#mpLinkUrl").addEventListener("paste", () => { clearTimeout(LK.t); setTimeout(preview, 0); });
  $("#mpLinkUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#agentForm").requestSubmit(); } });
  $("#mpLinkClear").onclick = () => { $("#mpLinkUrl").value = ""; preview(); $("#mpLinkUrl").focus(); };
  // Create in link mode: the link joins the prompt before house.js reads it (capture phase runs first)
  document.addEventListener("submit", (e) => {
    if (e.target.id !== "agentForm" || !root.classList.contains("mp-link-on")) return;
    const u = $("#mpLinkUrl").value.trim();
    if (!isUrl(u)) { e.preventDefault(); e.stopImmediatePropagation(); toast("Paste a link first (https://…)."); $("#mpLinkUrl").focus(); return; }
    const said = q().value.replace(u, "").trim();
    q().value = `${said || "Turn this into a post for all my channels:"} ${u}`;
  }, true);
  window.mpLinkMode = linkMode;

  // ------------------------------------------------------------ prompt box: example, quick actions, theme
  $("#mpExample").onclick = () => { fill(EXAMPLES[MP.ex = ((MP.ex ?? -1) + 1) % EXAMPLES.length]); };
  function drawChips(open) {
    const list = open ? [...CHIPS, ...MORE.map(([l, t, f]) => [l, I.text, t, f || ""])] : CHIPS;
    $("#mpChips").innerHTML = list.map(([l, icn], i) => `<button type="button" class="mp-chip" data-i="${i}" data-testid="mp-chip">${ic(icn)}${esc(l)}</button>`).join("") +
      `<button type="button" class="mp-chip mp-more" data-testid="mp-chip-more" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? "Fewer" : "More"} quick actions">${open ? "Less" : ic(I.dots) + "<span>More…</span>"}</button>`;
    $("#mpChips").querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => {
      const [, , text, fmt] = list[+b.dataset.i];
      if (text === "@take") return window.showTab && showTab("take");
      fill(text, fmt);
    }));
    $("#mpChips").querySelector("[data-testid=mp-chip-more]").onclick = () => drawChips(!open);
  }
  drawChips(false);
  function mountTheme() {
    const el = $("#mpTheme");
    if (!window.HQThemes || el.dataset.mounted) return;
    el.hidden = false; el.dataset.mounted = "1";
    let v = ""; try { v = localStorage.getItem("hq_theme") || ""; } catch (_) {}
    H_STATE.theme = v;
    const FMT = { auto: "Auto format", reel: "Reel 9:16", carousel: "Carousel 4:5", photo: "Image", text: "Text" };
    const picker = window.HQThemes.mount(el, { value: v, formatLabel: () => FMT[$("#agFormat").value] || "",
      onChange: (x) => { H_STATE.theme = x; try { localStorage.setItem("hq_theme", x || ""); } catch (_) {} } });
    $("#agFormat").addEventListener("change", () => picker.refresh());
    window.mpThemePicker = picker;
  }

  // ------------------------------------------------------------ Discover (the feeds you follow, /api/radar)
  function drawDiscTabs() {
    $("#mpDiscover .mp-disc-tabs").innerHTML = DISC.map(([k, l]) => `<button type="button" role="tab" class="mp-dt${MP.disc === k ? " on" : ""}" data-k="${k}" data-testid="mp-disc-tab" aria-selected="${MP.disc === k}">${esc(l)}</button>`).join("")
      + `<span class="mp-dmore"><button type="button" class="mp-dt" data-testid="mp-disc-tab-more" aria-expanded="false" aria-haspopup="true">More ▾</button>
         <span class="mp-dmenu" data-testid="mp-disc-more-menu" hidden><button type="button" data-go="news:scan" data-testid="mp-disc-go-scan">Story scan</button><button type="button" data-go="news" data-testid="mp-disc-go-news">All news</button><button type="button" data-go="news:sources" data-testid="mp-disc-go-sources">Your sources</button></span></span>`;
    $("#mpDiscover").querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => { MP.disc = b.dataset.k; drawDiscTabs(); drawDisc(); }));
    const mb = $("#mpDiscover [data-testid=mp-disc-tab-more]"), menu = $("#mpDiscover .mp-dmenu");
    mb.onclick = () => { menu.hidden = !menu.hidden; mb.setAttribute("aria-expanded", String(!menu.hidden)); };
  }
  function tagOf(st) {
    for (const [k, l, re] of DISC.slice(1)) if (re.test(st.title + " " + (st.summary || ""))) return l.replace(/s$/, "").replace("AI launche", "AI launch");
    return (st.source || "News").slice(0, 22);
  }
  function drawDisc() {
    const box = $("#mpDiscList"), re = (DISC.find((d) => d[0] === MP.disc) || [])[2];
    const rows = MP.stories.filter((s) => !re || re.test(s.title + " " + (s.summary || ""))).slice(0, 4);
    if (!MP.stories.length && MP.loading) { box.innerHTML = `<li class="mp-skel" aria-busy="true"><span></span><span></span><span></span></li>`; return; }
    box.innerHTML = rows.map((s, i) => { const host = hostOf(s.url), ago = s.published ? timeAgo(s.published * 1000) : "";
      return `<li class="mp-ds" data-testid="mp-disc-item"><div class="mp-ds-pic"><img src="${esc(window.hqThumb(s.url, s.image || ""))}" alt="" loading="lazy">${fav(host)}</div>
        <div class="mp-ds-b"><span class="mp-ds-tag">${esc(tagOf(s))}</span><a class="mp-ds-t" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
          <small>${esc(host || s.source || "")}${ago ? " · " + esc(ago) : ""}</small></div>
        <div class="mp-ds-a"><button type="button" class="mp-ib" data-use="${i}" data-testid="mp-disc-use" aria-label="Use “${esc(s.title.slice(0, 60))}” in a post">Use</button>
          <a class="mp-ib" href="${esc(s.url)}" target="_blank" rel="noopener" data-testid="mp-disc-open" aria-label="Open the story">${ic(I.out)}</a></div></li>`; }).join("")
      || `<li class="mp-ds-empty muted sm">${MP.stories.length ? "Nothing on this topic right now." : "No stories yet: follow a few sites in News → Sources."}</li>`;
    box.querySelectorAll(".mp-ds-pic img").forEach((im) => (im.onerror = () => im.closest(".mp-ds-pic").classList.add("nopic")));
    box.querySelectorAll("[data-use]").forEach((b) => (b.onclick = () => { const s = rows[+b.dataset.use]; leaveThread(); fill(`Post this on all my channels: ${s.url}`); setStep("create"); }));
    if (typeof favicons === "function") favicons(box);
  }
  async function loadDisc() {
    if (Date.now() - MP.at < 10 * 60000 && MP.stories.length) return drawDisc();
    MP.loading = true; drawDisc();
    try { const r = await api("/api/radar"); MP.stories = r.stories || []; MP.at = Date.now(); } catch (_) { MP.stories = []; }
    MP.loading = false; drawDisc();
  }

  // ------------------------------------------------------------ Recent content (house.js loadRuns hands the rows here)
  const fmtDur = (s) => (window.HQ_FMT ? window.HQ_FMT.fmtDur(s) : "");
  function badge(x) {
    if (x.format === "reel") return `${ic(I.play)}Reel${x.duration ? " " + fmtDur(x.duration) : ""}`;
    if (x.format === "carousel") return `${ic(I.slides)}Carousel${x.slides ? ` · ${x.slides} slides` : ""}`;
    if (x.format === "image") return `${ic(I.img)}Image`;
    if (x.format === "text") return `${ic(I.text)}Text`;
    return x.kind === "take" ? `${ic(I.cut)}One take` : "Post";
  }
  // a still for every card (owner, 1 Nov 2026: video-only runs showed burned captions or a blank intro frame, and a One take
  // showed nothing): the run's own picture, else the story's picture (GET /api/thumb, read server-side), else a video frame
  const isVid = (u) => /\.(mp4|mov|webm|m4v)(\?|#|$)/i.test(u || "");
  function cardPic(x) {
    const img = x.thumb || (x.cover && !isVid(x.cover) ? x.cover : "");
    if (img) return `<img src="${esc(img)}" alt="" loading="lazy" data-testid="mp-rc-img">`;
    const src = x.thumb_src && x.thumb_src.url && window.hqThumb ? window.hqThumb(x.thumb_src.url, x.thumb_src.img || "") : "";
    if (src) return `<img src="${esc(src)}" alt="" loading="lazy" data-testid="mp-rc-img"${x.cover ? ` data-fallback="${esc(x.cover)}"` : ""}>`;
    if (x.cover) return `<video src="${esc(x.cover)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true" data-testid="mp-rc-video"></video>`;
    return `<span class="mp-rc-ph" aria-hidden="true">${ic(x.kind === "take" ? I.cut : I.text)}</span>`;
  }
  function wirePics(box) {  // a story picture that 204s / fails falls back to the video's frame, then to the placeholder
    box.querySelectorAll(".mp-rc-pic img").forEach((im) => (im.onerror = () => {
      const v = im.dataset.fallback;
      im.outerHTML = v ? `<video src="${esc(v)}#t=1.5" muted playsinline preload="metadata" aria-hidden="true" data-testid="mp-rc-video"></video>` : `<span class="mp-rc-ph" aria-hidden="true">${ic(I.text)}</span>`;
    }));
  }
  function drawRecent() {
    const per = 8, rows = MP.recent.filter((x) => MP.filter === "all" || (MP.filter === "draft" ? x.state === "draft" : x.format === MP.filter));
    const pages = Math.max(1, Math.ceil(rows.length / per)); MP.page = Math.min(MP.page, pages);
    $("#mpRecentH").parentElement.querySelector(".mp-filters").innerHTML = FILTERS.map(([k, l]) => `<button type="button" class="mp-f${MP.filter === k ? " on" : ""}" data-f="${k}" data-testid="mp-filter" aria-pressed="${MP.filter === k}">${l}</button>`).join("");
    root.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { MP.filter = b.dataset.f; MP.page = 1; drawRecent(); }));
    const box = $("#runList");
    box.innerHTML = rows.slice((MP.page - 1) * per, MP.page * per).map((x) => `<button type="button" class="mp-rc runrow" data-testid="run-row" data-id="${x.id}" data-kind="${esc(x.kind)}">
        <span class="mp-rc-pic">${cardPic(x)}<span class="mp-badge" data-testid="mp-badge">${badge(x)}</span></span>
        <span class="mp-rc-b"><b>${esc(x.title)}</b><span class="mp-pill st-${esc(x.state || x.status)}" data-testid="mp-pill">${esc(STATE[x.state] || x.status)}</span></span></button>`).join("")
      || `<div class="empty"><b>${MP.recent.length ? "Nothing here yet" : "No posts yet"}</b><span>Ask the agent above, or pick a story from Discover.</span></div>`;
    wirePics(box);
    box.querySelectorAll("[data-id]").forEach((b) => (b.onclick = async () => {
      if (b.dataset.kind === "take") return window.openTake(b.dataset.id);
      const r = await api(`/api/agent/runs/${b.dataset.id}`); showRun(r); if (["running", "rendering"].includes(r.status)) watchRun(r.id); window.scrollTo({ top: 0, behavior: "smooth" });
    }));
    window.hqPager($("#mpRecentPager"), { total: rows.length, page: MP.page, per, label: "Recent content pages", onPage: (n) => { MP.page = n; drawRecent(); } });
  }
  window.mpRecent = (rows) => { MP.recent = rows; drawRecent(); };
  // "View all →": the Library with everything (runs' renders are registered there server-side), on the same filter
  const LIB_KIND = { all: "", reel: "video", carousel: "carousel", image: "image", draft: "draft" };
  const va = root.querySelector("[data-testid=mp-view-all]");
  if (va) { va.removeAttribute("data-go"); va.onclick = () => { window.LIB_PRESET = { kind: LIB_KIND[MP.filter] ?? "" }; showTab("library"); }; }

  // ------------------------------------------------------------ the chat thread
  function leaveThread() {
    root.classList.remove("mp-in-thread"); $("#runView").hidden = true;
    clearTimeout(H_STATE.poll); H_STATE.run = null; setStep("create");
  }
  $("#mpNew").onclick = () => { leaveThread(); q().focus(); };
  const hostIcon = (u) => { const h = hostOf(u); return h ? `<a class="mp-src" href="${esc(u)}" target="_blank" rel="noopener" data-testid="mp-source">${fav(h)}<span>${esc(h)}</span></a>` : ""; };
  function sources(r) {
    const res = r.result || {}, urls = [];
    const add = (u) => { if (u && /^https?:/.test(u) && !urls.includes(u)) urls.push(u); };
    add((res.source || {}).url);
    for (const s of r.steps || []) { const d = s.data || {};
      if (s.tool === "read_url" || s.tool === "x_oembed") add(d.url); (d.leads || []).forEach((l) => add(l.url)); if (d.best_video) add(d.best_video.page || d.best_video.url); }
    (res.other_outlets || []).forEach((o) => add(o.url));
    (((res.recap || {}).plan || {}).segments || []).forEach((sg) => add(sg.url));  // the weekly recap's stories
    return urls.slice(0, 8);
  }
  window.mpOnRun = (r) => {
    const res = r.result || {}, busy = ["running", "rendering"].includes(r.status), pk = res.packages || [];
    root.classList.add("mp-in-thread");
    setStep(busy || !pk.length ? "create" : "review");
    const inp = r.input || {}, up = res.upload;
    const said = inp.prompt || inp.brief || inp.query || inp.url || "(your file)";
    $("#mpUser").innerHTML = `<div class="mp-bubble"><p>${esc(said)}</p>${up ? `<span class="mp-att">${up.type === "video" ? `<video src="${esc(up.url)}#t=0.5" muted playsinline preload="metadata" aria-hidden="true"></video>` : `<img src="${esc(up.url)}" alt="">`}<small>${esc(up.name || "Your " + up.type)}${up.duration ? " · " + fmtDur(up.duration) : ""}</small></span>` : ""}</div>`;
    const steps = r.steps || [], cur = steps.find((s) => s.status === "running"), done = steps.filter((s) => ["done", "warn"].includes(s.status)).length;
    $("#mpTraceSum").textContent = busy ? `Working · ${done} of ${Math.max(steps.length, done + 1)} steps` : `${steps.length} step${steps.length === 1 ? "" : "s"} · ${r.status === "failed" ? "stopped" : "done"}`;
    $("#mpTrace").open = busy || ["failed", "interrupted"].includes(r.status);  // collapsed once it's done
    $("#mpStatus").innerHTML = busy ? `<span class="spin" aria-hidden="true"></span>${esc(cur ? cur.label + (cur.detail ? ": " + cur.detail.slice(0, 80) : "") + "…" : "Thinking…")}`
      : r.status === "failed" ? `<span class="mp-dot bad" aria-hidden="true"></span>Stopped. Reply below to try again.`
      : r.status === "interrupted" ? `<span class="mp-dot bad" aria-hidden="true"></span>Interrupted by a server restart. Press Resume below.`
      : res.rights_question && !res.recap && !(res.rights || {}).mode ? `<span class="mp-dot" aria-hidden="true"></span>Needs your answer below`
      : res.recap && res.recap.awaiting_rights ? `<span class="mp-dot" aria-hidden="true"></span>Needs your answer below` : pk.length ? `<span class="mp-dot ok" aria-hidden="true"></span>Ready for review` : "";
    const src = sources(r);
    $("#mpSources").hidden = !src.length;
    $("#mpSources").innerHTML = src.length ? `<span class="mp-src-h">Sources</span>${src.map(hostIcon).join("")}` : "";
    if (typeof favicons === "function") favicons($("#mpSources"));
    const legacy = ["media", "news"].includes(r.kind);
    $("#mpActs").hidden = busy || !pk.length || legacy || !pk.some((p) => p.status === "ready");
    mountTheme();
  };
  // "split this into 5 shorts": the request and the agent's answer stay in this chat thread; the clips themselves are
  // picked in One take (scores, reasons, hook titles), opened from here
  window.mpOnTake = (t, said) => {
    root.classList.add("mp-in-thread"); $("#runView").hidden = false; setStep("create");
    const inp = t.input || {}, n = inp.count;
    $("#mpUser").innerHTML = `<div class="mp-bubble"><p>${esc(said || "(your video)")}</p></div>`;
    $("#mpStatus").innerHTML = `<span class="spin" aria-hidden="true"></span>Splitting the video into clips…`;
    ["#agentPlan", "#mpSources", "#pkgHead", "#pkgHelp", "#mpActs", "#replyForm"].forEach((k) => { const el = $(k); if (el) el.hidden = true; });
    $("#timeline").innerHTML = ""; $("#mediaBox").innerHTML = ""; $("#packages").innerHTML = "";
    $("#mpTraceSum").textContent = "One take: transcript (captions reused when the video has them), best moments, reframing";
    $("#agentThread").hidden = false;
    $("#agentThread").innerHTML = `<div class="msg agent" data-testid="thread-msg"><b>Agent</b><span>I'm splitting it into ${n ? n + " clips" : "its best moments"}, each with a score, why it was picked and a hook title.
      <button type="button" class="btn2 sm" data-testid="mp-open-take">Open the clips</button></span></div>`;
    $("[data-testid=mp-open-take]").onclick = () => window.openTake && window.openTake(t.id);
  };
  $("#mpApprove").onclick = () => { const b = $("[data-testid=save-drafts]"); if (b) b.click(); else toast("Nothing left to approve in this post."); };
  $("#mpEdit").onclick = () => { $("#replyIn").focus(); $("#replyForm").scrollIntoView({ behavior: "smooth", block: "end" }); };
  $("#mpRegen").onclick = async () => {
    if (!H_STATE.run) return;
    const b = $("#mpRegen"); b.disabled = true;
    try { const r = await api(`/api/agent/runs/${H_STATE.run.id}/messages`, { method: "POST", body: { text: (H_STATE.run.result || {}).recap ? "redo the recap" : "Regenerate the media and captions with a fresh angle" } }); showRun(r); watchRun(r.id); }
    catch (e) { toast(e.message, 5000); } finally { b.disabled = false; }
  };
  const ri = $("#replyIn");  // the sticky composer grows with its text and sends on Enter (Shift+Enter: new line)
  ri.addEventListener("input", () => { ri.style.height = "auto"; ri.style.height = Math.min(160, ri.scrollHeight) + "px"; });
  ri.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#replyForm").requestSubmit(); } });

  window.HS_TABS = Object.assign(window.HS_TABS || {}, { agent: ((orig) => async function () { await orig?.(); drawDiscTabs(); loadDisc(); mountTheme(); })(window.HS_TABS?.agent) });
  setStep("create");
})();
