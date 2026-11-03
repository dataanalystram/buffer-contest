// HuhQueue: One take -> a week. Upload or link one long video you own; the worker transcribes it, the best moments become
// captioned 9:16 clips with a cover and end card, each is preflighted, and "The Spread" flies them into the open posting
// slots of your week in the Planner. Nothing is created until you press "Save as Buffer drafts", and only drafts.
// Same actions as REST and MCP: clips_from_video, list_clips, adjust_clip, place_clips_in_week.
"use strict";
const T = { src: "upload", file: null, take: null, poll: null, sel: new Set(), plan: null, edit: null };
const T_VIDEO = new Set(["instagram", "tiktok", "youtube", "twitter", "linkedin", "threads", "facebook", "bluesky"]);
const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const secs = (s) => { s = Math.max(0, +s || 0); const m = Math.floor(s / 60); return `${m}:${String((s % 60).toFixed(1)).padStart(4, "0")}`; };
const LAYOUT = { track: "Face-tracked", mixed: "Face-tracked + full frame", pad: "Full frame, blurred sides", fit: "Already vertical" };

function takeChips() {
  const chans = S.channels.filter((c) => !c.isDisconnected && T_VIDEO.has(c.service));
  // the same default as Make a post (your setup's channels, else every connected one), minus channels without video
  if (!T.sel.size) defaultChannels().filter((c) => T_VIDEO.has(c.service)).forEach((c) => T.sel.add(c.id));
  const box = $("#takeChips");
  box.innerHTML = chans.length ? "" : `<p class="muted sm">Connect a channel that takes video (Instagram, TikTok, YouTube, X, LinkedIn, Threads) in Buffer.</p>`;
  for (const c of chans) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "chip svc-" + c.service + (T.sel.has(c.id) ? " on" : "");
    el.setAttribute("data-testid", "take-chip"); el.setAttribute("aria-pressed", T.sel.has(c.id)); el.dataset.ch = c.id;
    el.innerHTML = `${avatar(c)}<span>${esc(c.displayName || c.name)}</span><small>${esc(netName(c.service))}</small>`;
    el.onclick = () => { T.sel.has(c.id) ? T.sel.delete(c.id) : T.sel.add(c.id); el.classList.toggle("on"); el.setAttribute("aria-pressed", T.sel.has(c.id)); };
    box.appendChild(el);
  }
}
function setTakeSrc(src) {
  T.src = src;
  document.querySelectorAll("[data-tsrc]").forEach((b) => { const on = b.dataset.tsrc === src; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  $("#tkUploadF").hidden = src !== "upload"; $("#tkLinkF").hidden = src !== "link";
}
document.querySelectorAll("[data-tsrc]").forEach((b) => (b.onclick = () => setTakeSrc(b.dataset.tsrc)));
const tdrop = $("#tkDrop");
["dragover", "dragenter"].forEach((e) => tdrop.addEventListener(e, (ev) => { ev.preventDefault(); tdrop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => tdrop.addEventListener(e, () => tdrop.classList.remove("over")));
tdrop.addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) tkPick(ev.dataTransfer.files[0]); });
$("#tkFile").onchange = (e) => e.target.files[0] && tkPick(e.target.files[0]);
function tkPick(f) {
  if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm)$/i.test(f.name)) return toast("Choose a video file (MP4 or MOV)");
  if (f.size > 40 * 1024 * 1024) return toast("Uploads here go up to 40 MB. For a longer talk, paste a direct link to the file instead.", 6000);
  T.file = f; $("#tkDropTxt").textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
}

$("#takeForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  // errors stay on the page under the button (a toast alone was easy to miss: live test 2 Nov 2026)
  const say = (m) => { $("#tkErr").textContent = m; if (m) toast(m, 6000); };
  say("");
  if (!$("#tkOwns").checked) return say("Tick “This is my video, or I have the rights to it” first. One take only clips videos you own.");
  if (!T.sel.size) return say("Pick at least one channel for the clips (below).");
  if (T.src === "upload" && !T.file) return say("Choose a video first (MP4, MOV or WebM up to 40 MB), or try the sample take.");
  if (T.src === "link") {
    const u = $("#tkUrl").value.trim();
    if (!/^https:\/\/[^\s/]+\.[^\s/]+\/\S+/.test(u)) return say("Paste a direct https:// link to your video file, e.g. https://your-storage.example.com/talk.mp4.");
  }
  const body = { owns: true, channel_ids: [...T.sel] };
  const n = $("#tkCount").value; if (n) body.count = +n;
  const b = $("#tkGo"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Starting…`;
  try {
    if (T.src === "upload") {
      if (!T.file) throw new Error("Choose a video first, or try the sample take");
      b.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
      const up = await api("/api/media", { method: "POST", body: { name: T.file.name, data: await readB64(T.file) } });
      body.media_token = up.token; body.name = T.file.name.replace(/\.[^.]+$/, "");
    } else {
      body.url = $("#tkUrl").value.trim();
      if (!body.url) throw new Error("Paste a direct link to your video file");
    }
    const t = await api("/api/takes", { method: "POST", body });
    showTake(t); watchTake(t.id);
  } catch (e) { say(e.message); }
  finally { b.disabled = false; b.textContent = "Make clips"; }
});
$("#tkSample").onclick = async () => {
  const b = $("#tkSample"); b.disabled = true;
  try { const t = await api("/api/takes/sample", { method: "POST", body: { channel_ids: [...T.sel] } }); showTake(t); loadTakes(); }
  catch (e) { toast(e.message, 6000); }
  finally { b.disabled = false; }
};

function watchTake(id) {
  clearTimeout(T.poll);
  const tick = async () => {
    try {
      const t = await api(`/api/takes/${id}`);
      showTake(t);
      if (["running", "rendering"].includes(t.status)) T.poll = setTimeout(tick, 2000);
      else loadTakes();
    } catch (e) { toast(e.message); }
  };
  T.poll = setTimeout(tick, 1200);
}

// ---------------------------------------------------------------- the take: timeline + clips
function showTake(t) {
  T.take = t; $("#takeView").hidden = false;
  $("#tkTimeline").innerHTML = t.steps.map((s) => `<li class="step ${s.status}" data-testid="take-step" data-key="${s.key}" data-status="${s.status}">
      <span class="dot" aria-hidden="true">${STATUS_ICON[s.status] || ""}</span>
      <div><b>${esc(s.label)}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}</div></li>`).join("");
  const src = t.source || {};
  $("#tkHead").innerHTML = `<b>${esc(src.title || t.input.name || "Your take")}</b>`
    + (src.duration ? `<span class="muted sm">${secs(src.duration)} long</span>` : "")
    + (t.sample ? `<span class="pill sm" data-testid="take-sample-badge">Sample · ${esc(src.credit || "")}</span>` : "")
    + (t.picked_by ? `<span class="muted sm">Moments picked by ${esc(t.picked_by)}</span>` : "")
    + (t.transcript_source && t.transcript_source !== "whisper" ? `<span class="pill sm" data-testid="take-captions-reused">Reused existing captions (no transcription)</span>` : "");
  const grid = $("#clipGrid");
  const clips = t.clips || [];
  if (!clips.length) {
    grid.innerHTML = ["running", "rendering"].includes(t.status) ? `<div class="clip-ph" aria-hidden="true"><span class="spin"></span></div>` : "";
  } else {
    const playing = new Set([...grid.querySelectorAll("video")].filter((v) => !v.paused).map((v) => v.dataset.clip));
    grid.innerHTML = clips.map(clipCard).join("");
    grid.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => clipAct(b.dataset.act, b.dataset.clip)));
    grid.querySelectorAll("video").forEach((v) => {
      if (playing.has(v.dataset.clip)) v.play().catch(() => {});
      v.onmouseenter = () => { if (!reducedMotion()) { v.muted = true; v.play().catch(() => {}); } };
      v.onmouseleave = () => { if (v.muted) v.pause(); };
    });
  }
  const ready = clips.filter((c) => c.status === "ready").length;
  $("#spreadBar").hidden = !(t.status === "ready" && ready);
  // the sample's clips only spread in the sample workspace (the server refuses them in a real account): say so up front
  const sampleOnly = !!t.sample && !(S.session && S.session.demo);
  $("#spreadGo").hidden = sampleOnly; $("#spreadWeek").hidden = sampleOnly; $("#spreadHelp").hidden = sampleOnly;
  $("#spreadSampleNote").hidden = !sampleOnly;
  $("#spreadErr").textContent = "";
  $("#spreadCount").textContent = `${ready} clip${ready === 1 ? "" : "s"} ready`;
  if (T.edit) openEditor(T.edit);
}
function pfMini(c) {
  const pf = c.preflight || {};
  return Object.entries(pf).map(([cid, r]) => `<span class="pf-chip s-${esc(r.status)}" data-testid="clip-preflight" data-status="${esc(r.status)}" title="${esc((r.findings || []).map((f) => f.message).join("\n"))}"><b aria-hidden="true">${PF_ICON[r.status]}</b><span class="sr">${PF_WORD[r.status]}: </span>${esc(r.chip)}</span>`).join("");
}
function clipCard(c, i) {
  const placed = (c.placements || []).filter((p) => ["draft", "pending_approval"].includes(p.status));
  const failed = (c.placements || []).filter((p) => p.status === "failed");
  const st = c.status === "ready" ? "" : c.status === "failed" ? `<span class="st err" data-testid="clip-error">${esc(c.error || "Render failed")}</span>` : `<span class="muted sm"><span class="spin" aria-hidden="true"></span> Rendering…</span>`;
  return `<article class="clip deal" style="--i:${i}" data-testid="clip-card" data-clip="${esc(c.id)}" data-status="${esc(c.status)}">
    <div class="clip-media">${c.url ? `<video src="${esc(c.url)}" poster="${esc(c.cover_url || "")}" controls playsinline preload="none" muted loop data-clip="${esc(c.id)}" data-testid="clip-video" aria-label="Clip ${i + 1}: ${esc(c.hook)}"></video>` : c.cover_url ? `<img src="${esc(c.cover_url)}" alt="">` : `<span class="spin" aria-hidden="true"></span>`}</div>
    <div class="clip-body">
      <p class="clip-hook" data-testid="clip-hook">“${esc(c.hook)}”</p>
      <p class="muted xs clip-meta"><span data-testid="clip-span">${secs(c.start)}–${secs(c.end)} in the take</span> · ${Math.round(c.duration || 0)} s${c.layout ? ` · ${esc(LAYOUT[c.layout] || c.layout)}` : ""}${c.score != null ? ` · score ${Math.round(c.score)}` : ""}</p>
      ${c.why ? `<p class="xs clip-why" data-testid="clip-why">${c.rank ? `<b>#${c.rank}</b> · ` : ""}Why this moment: ${esc(c.why)}</p>` : ""}
      ${Object.keys(c.variants || {}).length ? `<p class="muted xs" data-testid="clip-variants">Also in ${Object.entries(c.variants).map(([r, u]) => `<a class="link" href="${escUrl(u)}" target="_blank" rel="noopener">${esc(r)}</a>`).join(" · ")} for the channels that need it</p>` : ""}
      ${st}
      <div class="clip-pf">${pfMini(c)}</div>
      ${placed.length ? `<p class="xs clip-placed" data-testid="clip-placed">${placed.map((p) => `${netMark(p.service)} ${esc(p.local)} · ${p.status === "draft" ? "Buffer draft" : "waiting for approval"}`).join("<br>")}</p>` : ""}
      ${failed.length ? `<p class="st err xs">${esc(failed[0].error)}</p>` : ""}
      <div class="clip-act">${c.url ? `<button type="button" class="link" data-act="edit" data-clip="${esc(c.id)}" data-testid="clip-edit">Trim &amp; caption</button><a class="link" href="${escUrl(c.url)}" target="_blank" rel="noopener" data-testid="clip-open">Open video</a>` : ""}</div>
    </div></article>`;
}

// ---------------------------------------------------------------- per-clip editor: trim + hook + caption
function clipAct(act, id) { if (act === "edit") openEditor(id); }
function openEditor(id) {
  const c = (T.take?.clips || []).find((x) => x.id === id); if (!c) return;
  T.edit = id;
  const dlg = $("#clipDialog");
  if (dlg.open && dlg.dataset.clip === id) return;
  dlg.dataset.clip = id;
  $("#ceTitle").textContent = `Clip ${id.replace(/^c/, "")}: trim and caption`;
  const canCut = T.take.can_rerender;
  T.trim = { s: c.start, e: c.end };
  const words = c.words || [];
  $("#ceWords").innerHTML = words.map((w, i) => `<button type="button" class="w" data-i="${i}" data-s="${w.s}" data-e="${w.e}">${esc(w.w)}</button>`).join(" ");
  $("#ceWords").querySelectorAll(".w").forEach((b) => (b.onclick = (ev) => {
    const s = +b.dataset.s, e = +b.dataset.e;
    if (ev.shiftKey || Math.abs(s - T.trim.e) < Math.abs(s - T.trim.s)) T.trim.e = e + 0.2; else T.trim.s = Math.max(0, s - 0.1);
    paintTrim();
  }));
  $("#ceTrimNote").textContent = canCut ? "Click a word to start there (Shift+click to end there), or nudge with the buttons. Trimming re-renders the clip." : (T.take.sample ? "The sample workspace has no video worker, so trimming is shown but not re-rendered here. Caption edits work." : "This server has no video worker, so only captions can change.");
  const svcs = [...new Set(T.take.input.channels.map((x) => x.service))];
  $("#ceSvc").innerHTML = svcs.map((s) => `<option value="${esc(s)}">${esc(netName(s))}</option>`).join("");
  $("#ceCaption").value = (c.post_captions || {})[svcs[0]] || "";
  $("#ceSvc").onchange = () => { const cc = (T.take.clips.find((x) => x.id === id) || {}).post_captions || {}; $("#ceCaption").value = cc[$("#ceSvc").value] || ""; };
  $("#ceHook").value = c.hook || "";
  $("#ceErr").textContent = "";
  paintTrim();
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  $("#ceCaption").focus();
}
function paintTrim() {
  const { s, e } = T.trim;
  $("#ceIn").textContent = secs(s); $("#ceOut").textContent = secs(e); $("#ceLen").textContent = `${(e - s).toFixed(1)} s`;
  $("#ceWords").querySelectorAll(".w").forEach((b) => b.classList.toggle("out", +b.dataset.e < s || +b.dataset.s > e));
}
document.querySelectorAll("[data-nudge]").forEach((b) => (b.onclick = () => {
  const [edge, d] = b.dataset.nudge.split(":"); T.trim[edge] = Math.max(0, +(T.trim[edge] + +d).toFixed(2)); paintTrim();
}));
function closeEditor() { const d = $("#clipDialog"); T.edit = null; if (typeof d.close === "function" && d.open) d.close(); else d.removeAttribute("open"); }
$("#ceCancel").onclick = closeEditor;
$("#clipDialog").addEventListener("close", () => (T.edit = null));
$("#ceForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const id = T.edit, c = T.take.clips.find((x) => x.id === id); if (!c) return;
  const body = {};
  if ($("#ceCaption").value !== ((c.post_captions || {})[$("#ceSvc").value] || "")) { body.caption = $("#ceCaption").value; body.service = $("#ceSvc").value; }
  if ($("#ceHook").value.trim() !== c.hook) body.hook = $("#ceHook").value.trim();
  if (Math.abs(T.trim.s - c.start) > 0.04 || Math.abs(T.trim.e - c.end) > 0.04) { body.start_ms = Math.round(T.trim.s * 1000); body.end_ms = Math.round(T.trim.e * 1000); }
  if (!Object.keys(body).length) return closeEditor();
  const btn = $("#ceSave"); btn.disabled = true; $("#ceErr").textContent = "";
  try {
    const t = await api(`/api/takes/${T.take.id}/clips/${encodeURIComponent(id)}`, { method: "PATCH", body });
    closeEditor(); showTake(t);
    toast(body.start_ms != null || body.hook ? "Re-rendering that clip…" : "Caption saved");
    if (["running", "rendering"].includes(t.status)) watchTake(t.id);
  } catch (e) { $("#ceErr").textContent = e.message; }
  finally { btn.disabled = false; }
});

// ---------------------------------------------------------------- The Spread
$("#spreadGo").onclick = async () => {
  const b = $("#spreadGo"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Finding open slots…`;
  try {
    // only the channels selected in the chips (owner bug 2 Nov 2026: the Spread planned every channel of the take)
    const takeCh = new Set((T.take.input.channels || []).map((x) => x.id));
    const chosen = [...T.sel].filter((id) => takeCh.has(id));
    if (!chosen.length) { const m = "Select at least one of this take's channels above to spread it."; $("#spreadErr").textContent = m; toast(m, 6000); return; }
    const out = await api(`/api/takes/${T.take.id}/place`, { method: "POST", body: { dry_run: true, week_of: $("#spreadWeek").value, channel_ids: chosen } });
    T.plan = out;
    $("#spreadErr").textContent = "";
    if (!out.plan.placements.length) { const m = out.plan.unplaced[0]?.why || "No open posting slots that week."; $("#spreadErr").textContent = m; toast(m, 6000); return; }
    await spread(out);
  } catch (e) { $("#spreadErr").textContent = e.message; toast(e.message, 6000); }
  finally { b.disabled = false; b.textContent = "Spread into my week"; }
};
async function spread(out) {
  const plan = out.plan, clips = Object.fromEntries((T.take.clips || []).map((c) => [c.id, c]));
  // where each card flies from: its clip card on this page
  const from = {};
  document.querySelectorAll("#clipGrid [data-testid=clip-card]").forEach((el) => { const m = el.querySelector(".clip-media"); from[el.dataset.clip] = (m || el).getBoundingClientRect(); });
  // ghost items in the Planner, one per placement, in the planned week
  PL.extra = plan.placements.map((p, i) => ({ kind: "ghost", state: "ghost", ref: `ghost:${i}`, when: p.at, text: p.text, service: p.service,
    channel: p.channel_name, channel_id: p.channel_id, id: `ghost:${i}`, thumb: p.cover_url || "", type: "reel", draggable: false, clip_id: p.clip_id, preflight: p.preflight }));
  if (!PL.tz) plLoadState();
  PL.view = "week"; PL.date = plan.week_of;
  showTab("planner");   // loads the Planner (same request path as opening the tab)
  await loadPlanner();
  // keep the earliest landing hour in view
  const zs = PL.extra.map((g) => zoned(new Date(g.when), PL.tz));
  const sc = $(".pl-week-scroll");
  if (sc && zs.length) sc.scrollTop = Math.max(0, (Math.min(...zs.map((z) => z.h)) - 1) * ROW_H);
  showSpreadBar(out);
  const targets = [...document.querySelectorAll("[data-ref^='ghost:']")];
  if (reducedMotion()) return;
  targets.forEach((el) => el.classList.add("landing"));
  const layer = document.createElement("div"); layer.className = "spread-layer"; layer.setAttribute("aria-hidden", "true"); document.body.appendChild(layer);
  const anims = targets.map((el, i) => {
    const g = PL.extra[+el.dataset.ref.split(":")[1]], c = clips[g.clip_id] || {};
    const a = from[g.clip_id] || { left: window.innerWidth / 2 - 40, top: window.innerHeight - 160, width: 80, height: 142 };
    const b = el.getBoundingClientRect();
    const card = document.createElement("div"); card.className = "spread-card";
    card.style.cssText = `left:${a.left}px;top:${a.top}px;width:${a.width}px;height:${a.height}px;transform-origin:0 0;background-image:url("${(c.cover_url || "").replace(/"/g, "")}")`;
    layer.appendChild(card);
    const dx = b.left - a.left, dy = b.top - a.top, sx = Math.max(0.08, b.width / a.width), sy = Math.max(0.05, b.height / a.height);
    const lift = Math.min(160, Math.abs(dx) * 0.25 + 60);
    const anim = card.animate([
      { transform: "translate(0,0) scale(1)", opacity: 1 },
      { transform: `translate(${dx * 0.5}px, ${dy * 0.5 - lift}px) scale(${(1 + sx) / 2.4})`, opacity: 1, offset: 0.55 },
      { transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`, opacity: 0.2 },
    ], { duration: 320, delay: i * 70, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
    return anim.finished.then(() => { el.classList.remove("landing"); el.classList.add("landed"); });
  });
  await Promise.allSettled(anims);
  layer.remove();
}
function showSpreadBar(out) {
  const bar = $("#plSpread"), plan = out.plan;
  const days = new Set(plan.placements.map((p) => p.day)).size;
  bar.hidden = false;
  $("#plSpreadText").innerHTML = `<b>${plan.placements.length} clip post${plan.placements.length === 1 ? "" : "s"} planned</b> across ${days} day${days === 1 ? "" : "s"} in your open posting times`
    + (plan.suggested_times ? " (suggested times where a channel has none in Buffer)" : "")
    + (plan.unplaced.length ? ` · ${plan.unplaced.length} without a free slot` : "")
    + `. ${esc(out.on_confirm[0].toUpperCase() + out.on_confirm.slice(1))}; nothing is published.`;
}
$("#plSpreadSave").onclick = async () => {
  if (!T.plan) return;
  const b = $("#plSpreadSave"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Saving drafts…`;
  try {
    const out = await api(`/api/takes/${T.take.id}/place`, { method: "POST", body: { confirm_token: T.plan.confirm_token } });
    T.plan = null; PL.extra = []; $("#plSpread").hidden = true;
    toast(out.failed ? `${out.placed} saved as Buffer drafts, ${out.failed} failed: ${(out.results.find((r) => r.status === "failed") || {}).error || ""}` : out.message, 7000);
    await loadPlanner(); budget();
    api(`/api/takes/${T.take.id}`).then((t) => (T.take = t)).catch(() => {});
  } catch (e) { toast(e.message, 7000); }
  finally { b.disabled = false; b.textContent = "Save as Buffer drafts"; }
};
$("#plSpreadCancel").onclick = () => { T.plan = null; PL.extra = []; $("#plSpread").hidden = true; plDraw(); toast("Plan discarded. Nothing was saved."); };

// ---------------------------------------------------------------- list of takes
async function loadTakes() {
  const box = $("#takeList");
  try {
    const rows = await api("/api/takes");
    box.innerHTML = rows.length ? rows.map((x) => `<button class="runrow" data-testid="take-row" data-id="${esc(x.id)}">${x.cover ? `<img src="${esc(x.cover)}" alt="">` : `<span class="vt">▶</span>`}
      <span class="rt"><b>${esc(x.title)}</b><small>${esc(x.status)} · ${esc(x.clips)} clip${x.clips === 1 ? "" : "s"}${x.placed ? ` · ${x.placed} placed` : ""} · ${new Date(x.created * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</small></span></button>`).join("")
      : `<div class="empty"><b>No takes yet</b><span>Drop a talk, a podcast episode or a tutorial you recorded, or try the sample take.</span></div>`;
    box.querySelectorAll("[data-id]").forEach((b) => (b.onclick = async () => { const t = await api(`/api/takes/${b.dataset.id}`); showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  } catch (_) {}
}
async function loadTake() {
  takeChips(); loadTakes();
  $("#tkSample").hidden = false;
  const u = new URLSearchParams(location.search);
  if (u.get("take") && !T.take) { try { const t = await api(`/api/takes/${u.get("take")}`); showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id); } catch (_) {} }
}
window.openTake = async (id) => { showTab("take"); const t = await api(`/api/takes/${id}`); showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id); };
window.HS_TABS = Object.assign(window.HS_TABS || {}, { take: loadTake });
