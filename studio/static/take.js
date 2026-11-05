// HuhQueue: One take -> a week. Upload or link one long video you own; the worker transcribes it, the best moments become
// captioned 9:16 clips with a cover and end card, each is preflighted, and "The Spread" flies them into the open posting
// slots of your week in the Planner. Nothing is created until you press "Save as Buffer drafts", and only drafts.
// Same actions as REST and MCP: clips_from_video, list_clips, adjust_clip, place_clips_in_week.
"use strict";
const T = { src: "upload", file: null, take: null, poll: null, sel: new Set(), plan: null, edit: null, sort: "score" };
const TK_ATTEST_V = "2026-11-04.1"; // link_ingest.ATTESTATION_VERSION: a newer statement on the server answers 409 (reload)
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
    el.onclick = () => { T.sel.has(c.id) ? T.sel.delete(c.id) : T.sel.add(c.id); el.classList.toggle("on"); el.setAttribute("aria-pressed", T.sel.has(c.id)); if (T.take) takeTargets(T.take); };
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
// Uploads (4 Nov 2026): with a bucket on the server, the file goes straight to it in 16 MiB parts (resumable, up to
// max_bytes); without one, as a raw body to the Library upload (100 MB). Never base64 through /api/media any more.
let TK_UP = null;
// The size limit as text, synchronously (form errors show at once, never after a round trip): the fallback's 100 MB
// until the server's config has arrived.
function tkLim(cfg) {
  const mb = Math.round(((cfg && cfg.max_bytes) || 100 * 1048576) / 1048576);
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 ? 1 : 0)} GB` : `${mb} MB`;
}
async function tkUpCfg() {
  let cfg = TK_UP;
  if (!cfg) {  // a failed lookup (signed out yet, offline) is not cached: the next call asks again
    try { cfg = TK_UP = await api("/api/takes/uploads/config"); } catch (_) { cfg = { multipart: false, max_bytes: 100 * 1048576 }; }
  }
  const lim = tkLim(cfg);
  if (!T.file) $("#tkDropTxt").textContent = `Drop an MP4 or MOV here, or choose a file (up to ${lim}${cfg.multipart ? ", resumes if the connection drops" : "; use a link for longer talks"})`;
  return { ...cfg, lim };
}
tkUpCfg().catch(() => {});  // prefetch, so the drop zone and the form errors name the real limit
async function tkPick(f) {
  if (!/^video\//.test(f.type) && !/\.(mp4|mov|m4v|webm)$/i.test(f.name)) return toast("Choose a video file (MP4, MOV or WebM)");
  const c = await tkUpCfg();
  if (f.size > c.max_bytes) return toast(`Uploads here go up to ${c.lim}; this one is ${(f.size / 1048576).toFixed(0)} MB. Export a shorter cut, or paste a direct link to the file.`, 7000);
  T.file = f; $("#tkDropTxt").textContent = `${f.name} · ${(f.size / 1048576).toFixed(1)} MB`;
}
function tkProg(pct, text) {
  $("#tkProgBox").hidden = pct == null; if (pct == null) return;
  $("#tkProg").value = Math.round(pct); $("#tkProgTxt").textContent = text || `${Math.round(pct)}%`;
}
const tkSleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tkOnline = () => (navigator.onLine === false ? new Promise((r) => window.addEventListener("online", r, { once: true })) : Promise.resolve());
function tkXhr(method, url, body, headers, onProg) {
  return new Promise((ok, bad) => {
    const x = new XMLHttpRequest(); x.open(method, url);
    Object.entries(headers || {}).forEach(([k, v]) => x.setRequestHeader(k, v));
    x.upload.onprogress = (e) => onProg && e.lengthComputable && onProg(e.loaded);
    x.onload = () => (x.status < 300 ? ok(x) : bad(Object.assign(new Error(((() => { try { return JSON.parse(x.responseText).error; } catch (_) { return ""; } })()) || `Upload failed (HTTP ${x.status}).`), { status: x.status })));
    x.onerror = () => bad(Object.assign(new Error("The connection dropped."), { net: true }));
    x.send(body);
  });
}
async function tkUploadMultipart(f) {
  const up = await api("/api/takes/uploads", { method: "POST", body: { name: f.name, size: f.size, content_type: f.type || "video/mp4", fingerprint: `${f.name}|${f.size}|${f.lastModified || 0}` } });
  if (up.status === "joined" || up.status === "ready") {  // the parts were joined before: only the checks are left
    tkProg(100, "Checking the upload…");
    await api(`/api/takes/uploads/${up.upload_id}/complete`, { method: "POST", body: {} });
    return up.upload_id;
  }
  const done = new Map((up.received || []).map((p) => [p.part_number, { part_number: p.part_number, etag: p.etag, size: p.size }]));
  const ps = up.part_size, todo = [];
  for (let n = 1; n <= up.parts; n++) if (!done.has(n)) todo.push(n);
  let sent = [...done.values()].reduce((s, p) => s + p.size, 0);
  const show = (extra = 0) => tkProg(((sent + extra) / f.size) * 100, `${up.resumed ? "Resumed · " : ""}${((sent + extra) / 1048576).toFixed(0)} of ${(f.size / 1048576).toFixed(0)} MB`);
  show();
  for (let i = 0; i < todo.length; i += 20) {
    const { urls } = await api(`/api/takes/uploads/${up.upload_id}/parts`, { method: "POST", body: { parts: todo.slice(i, i + 20) } });
    for (const u0 of urls) {
      let u = u0, fresh = false;
      const blob = f.slice((u.part_number - 1) * ps, (u.part_number - 1) * ps + u.size);
      for (let tries = 1; ; tries++) {
        try {
          const x = await tkXhr("PUT", u.url, blob, {}, (n) => show(n));
          const etag = x.getResponseHeader("ETag");
          // a hard error, never retried as a dropped connection: no retry can read a header the bucket's CORS hides
          if (!etag) throw Object.assign(new Error("The storage bucket doesn't let the browser read the ETag header, so the upload can't be finished. Its CORS rules need ExposeHeaders: ETag."), { hard: true });
          done.set(u.part_number, { part_number: u.part_number, etag, size: u.size }); sent += u.size; show(); break;
        } catch (e) {
          if (e.hard) throw new Error(`Part ${u.part_number} didn't upload: ${e.message}`);
          if (e.status === 403 && !fresh) {  // the signed URL ran out (an hour): ask for a new one and try again
            fresh = true; tries--;
            u = (await api(`/api/takes/uploads/${up.upload_id}/parts`, { method: "POST", body: { parts: [u.part_number] } })).urls[0];
            continue;
          }
          if (tries >= 6 || (!e.net && e.status && e.status < 500 && e.status !== 408)) throw new Error(`Part ${u.part_number} didn't upload: ${e.message} Press Make clips again to resume.`);
          tkProg((sent / f.size) * 100, navigator.onLine === false ? "Offline. Waiting for the connection, then resuming…" : `Connection dropped. Retrying part ${u.part_number}…`);
          await tkOnline(); await tkSleep(Math.min(16000, 1000 * 2 ** tries));
        }
      }
    }
  }
  tkProg(100, "Checking the upload…");
  await api(`/api/takes/uploads/${up.upload_id}/complete`, { method: "POST", body: { parts: [...done.values()].map(({ part_number, etag }) => ({ part_number, etag })) } });
  return up.upload_id;
}
async function tkUploadLibrary(f) {
  tkProg(0, "Uploading…");
  const x = await tkXhr("POST", `/api/library/upload?source=take&name=${encodeURIComponent(f.name)}`, f, { "X-HQ-CSRF": S.csrf, "Content-Type": "application/octet-stream" },
    (n) => tkProg((n / f.size) * 100, `${(n / 1048576).toFixed(0)} of ${(f.size / 1048576).toFixed(0)} MB`));
  const it = JSON.parse(x.responseText);
  return it.media_token || it.token;
}

$("#takeForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  // errors stay on the page under the button (a toast alone was easy to miss: live test 2 Nov 2026)
  const say = (m) => { $("#tkErr").textContent = m; if (m) toast(m, 6000); };
  say("");
  if (!$("#tkOwns").checked) return say("Tick “I own this video or have permission to use it” first. One take only clips videos you have the rights to.");
  if (!T.sel.size) return say("Pick at least one channel for the clips (below).");
  if (T.src === "upload" && !T.file) return say(`Choose a video first (MP4, MOV or WebM up to ${tkLim(TK_UP)}), or try the sample take.`);
  if (T.src === "link") {
    const u = $("#tkUrl").value.trim();
    if (!/^https:\/\/[^\s/]+\.[^\s/]+\/\S+/.test(u)) return say("Paste an https:// link to the video: a YouTube, Vimeo or X post, or the video file.");
  }
  const body = { owns: true, channel_ids: [...T.sel] };
  const n = $("#tkCount").value; if (n) body.count = +n;
  const b = $("#tkGo"); b.disabled = true; b.innerHTML = `<span class="spin" aria-hidden="true"></span>Starting…`;
  try {
    if (T.src === "upload") {
      if (!T.file) throw new Error("Choose a video first, or try the sample take");
      b.innerHTML = `<span class="spin" aria-hidden="true"></span>Uploading…`;
      const cfg = await tkUpCfg();
      if (cfg.multipart) body.upload_id = await tkUploadMultipart(T.file);
      else body.media_token = await tkUploadLibrary(T.file);
      body.name = T.file.name.replace(/\.[^.]+$/, "");
      tkProg(null);
    } else {
      body.url = $("#tkUrl").value.trim();
      if (!body.url) throw new Error("Paste a link to the video");
      // the rights statement the person ticked (studio/link_ingest.py); the server keeps a consent record before any fetch
      body.rights_attested = true; body.attestation_version = TK_ATTEST_V;
    }
    const t = await api("/api/takes", { method: "POST", body });
    showTake(t); watchTake(t.id);
  } catch (e) { say(e.message); if ($("#tkProg").value < 100) $("#tkProgTxt").textContent = e.message; }
  finally { b.disabled = false; b.textContent = "Make clips"; }
});
$("#tkSample").onclick = async () => {
  const b = $("#tkSample"); b.disabled = true;
  try { const t = await api("/api/takes/sample", { method: "POST", body: { channel_ids: [...T.sel] } }); showTake(t); loadTakes(); }
  catch (e) { toast(e.message, 6000); }
  finally { b.disabled = false; }
};

function watchTake(id) {
  clearTimeout(T.poll); T.watching = id;
  let unwake = () => {};
  // a hidden tab asks at most every 30 s and catches up the moment it is shown again (loading.js pollMs / wake)
  const later = (ms) => {
    unwake(); const d = window.HQLoad && HQLoad.pollMs ? HQLoad.pollMs(ms) : ms;
    T.poll = setTimeout(tick, d);
    unwake = d > ms ? HQLoad.wake(() => { if (T.watching !== id) return; clearTimeout(T.poll); tick(); }) : () => {};
  };
  const tick = async () => {
    unwake(); unwake = () => {};
    if (T.watching !== id) return;  // another take is watched now
    try {
      const t = await api(`/api/takes/${id}`);
      if (T.watching !== id) return;
      showTake(t);
      if (["running", "rendering"].includes(t.status)) later(2000);
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
  $("#tkClipsN").textContent = clips.length ? `(${clips.length})` : "";
  if (!clips.length) {
    grid.innerHTML = ["running", "rendering"].includes(t.status) ? `<li class="clip-ph" aria-hidden="true"><span class="spin"></span> Finding the best moments…</li>` : "";
  } else {
    grid.innerHTML = sortedClips(t).map(({ c, i }, k) => clipRow(c, i, k)).join("");
    grid.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => clipAct(b.dataset.act, b.dataset.clip)));
    if (window.HQ_MUSIC_NOBROKEN) window.HQ_MUSIC_NOBROKEN(grid);
  }
  takeTargets(t);
  const ready = clips.filter((c) => c.status === "ready").length;
  $("#spreadBar").hidden = !(t.status === "ready" && ready);
  // the sample's clips only spread in the sample workspace (the server refuses them in a real account): say so up front
  const sampleOnly = !!t.sample && !(S.session && S.session.demo);
  $("#spreadGo").hidden = sampleOnly; $("#spreadWeek").hidden = sampleOnly; $("#spreadHelp").hidden = sampleOnly;
  $("#spreadSampleNote").hidden = !sampleOnly;
  $("#spreadErr").textContent = "";
  $("#spreadCount").textContent = `${ready} clip${ready === 1 ? "" : "s"} ready`;
  if (window.ceRefresh) window.ceRefresh(t);
}
// clips in the chosen order; i is the clip's number in the take (c1 = 1), shown on the row and in the editor
function sortedClips(t) {
  const rows = (t.clips || []).map((c, i) => ({ c, i: i + 1 }));
  return T.sort === "time" ? rows.sort((a, b) => a.c.start - b.c.start) : rows.sort((a, b) => (b.c.score ?? -1) - (a.c.score ?? -1) || a.i - b.i);
}
$("#tkSort").onchange = () => { T.sort = $("#tkSort").value; if (T.take) showTake(T.take); };
// Channel targets: what each channel of the take gets (the ratio it's rendered in, its caption limit, how many clips pass
// its preflight) and whether the Spread uses it (the same selection as the channel chips above).
const T_RATIO = { instagram: "9:16 Reel", tiktok: "9:16", youtube: "9:16 Short", facebook: "9:16 Reel", linkedin: "4:5", threads: "4:5", twitter: "1:1", bluesky: "1:1" };
function takeTargets(t) {
  const box = $("#tkTgList"); if (!box) return;
  const chans = (t.input && t.input.channels) || [];
  const lim = t.caption_limits || {};
  box.innerHTML = chans.map((ch) => {
    const pf = (t.clips || []).map((c) => (c.preflight || {})[ch.id]).filter(Boolean);
    const ok = pf.filter((r) => r.status === "ok").length, bad = pf.filter((r) => r.status === "error").length;
    const ratio = t.style === "opus" ? T_RATIO[ch.service] || "9:16" : "9:16";
    const on = T.sel.has(ch.id);
    return `<li class="tk-tg-row" data-testid="take-target" data-ch="${esc(ch.id)}">${netMark(ch.service)}
      <span class="tk-tg-t"><b>${esc(ch.name)}</b><small>${esc(netName(ch.service))} · ${esc(ratio)}${lim[ch.service] ? ` · captions up to ${lim[ch.service].toLocaleString()}` : ""}</small>
        ${pf.length ? `<small class="${bad ? "tk-bad" : ""}" data-testid="take-target-preflight">${ok} of ${pf.length} clips pass preflight${bad ? ` · ${bad} need a fix` : ""}</small>` : ""}</span>
      <label class="sw"><input type="checkbox" role="switch" data-tg="${esc(ch.id)}" data-testid="take-target-switch" ${on ? "checked" : ""} aria-label="Spread to ${esc(ch.name)}"><span aria-hidden="true"></span></label></li>`;
  }).join("") || `<li class="muted sm">No channels on this take.</li>`;
  box.querySelectorAll("[data-tg]").forEach((i) => (i.onchange = () => {
    i.checked ? T.sel.add(i.dataset.tg) : T.sel.delete(i.dataset.tg);
    const chip = document.querySelector(`#takeChips [data-ch="${CSS.escape(i.dataset.tg)}"]`);
    if (chip) { chip.classList.toggle("on", i.checked); chip.setAttribute("aria-pressed", i.checked); }
  }));
}
// the row shows one small chip per channel (network + pass/warn/fix); the full check is in its tooltip
function pfCompact(c) {
  const pf = c.preflight || {};
  return Object.entries(pf).map(([cid, r]) => `<span class="pf-chip s-${esc(r.status)}" data-testid="clip-preflight" data-status="${esc(r.status)}" title="${esc(r.chip + "\n" + (r.findings || []).map((f) => f.message).join("\n"))}"><b aria-hidden="true">${PF_ICON[r.status]}</b><span class="sr">${PF_WORD[r.status]}: </span>${esc(netName(r.service || ""))}</span>`).join("");
}
function pfMini(c) {
  const pf = c.preflight || {};
  return Object.entries(pf).map(([cid, r]) => `<span class="pf-chip s-${esc(r.status)}" data-testid="clip-preflight" data-status="${esc(r.status)}" title="${esc((r.findings || []).map((f) => f.message).join("\n"))}"><b aria-hidden="true">${PF_ICON[r.status]}</b><span class="sr">${PF_WORD[r.status]}: </span>${esc(r.chip)}</span>`).join("");
}
const LAYOUT_IC = `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M9 21V9"/></svg>`;  // Lucide layout-template
const SCISSORS = `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/></svg>`;
function clipRow(c, n, k) {
  const placed = (c.placements || []).filter((p) => ["draft", "pending_approval"].includes(p.status));
  const failed = (c.placements || []).filter((p) => p.status === "failed");
  const st = c.status === "ready" ? "" : c.status === "failed" ? `<span class="st err" data-testid="clip-error">${esc(c.error || "Render failed")}</span>` : `<span class="muted xs" data-testid="clip-rendering"><span class="spin" aria-hidden="true"></span> Rendering…</span>`;
  return `<li class="clip-row deal" style="--i:${k}" data-testid="clip-card" data-clip="${esc(c.id)}" data-status="${esc(c.status)}">
    <button type="button" class="clip-thumb" data-act="edit" data-clip="${esc(c.id)}" aria-label="Open clip ${n} in Trim and caption" ${c.url ? "" : "disabled"}>${c.cover_url ? `<img src="${esc(c.cover_url)}${c.rendered_at ? "?v=" + Math.round(c.rendered_at) : ""}" alt="" loading="lazy">` : `<span class="spin" aria-hidden="true"></span>`}</button>
    <span class="clip-n" aria-hidden="true">${n}</span>
    <div class="clip-body">
      <p class="clip-hook" data-testid="clip-hook">${esc(c.hook)}</p>
      <p class="muted xs clip-meta"><span data-testid="clip-dur">${((c.end || 0) - (c.start || 0)).toFixed(1)} s</span> · <span data-testid="clip-span">${secs(c.start)}–${secs(c.end)} in the take</span>${c.score != null ? ` · <span class="pill sm clip-score" data-testid="clip-score">Score ${Math.round(c.score)}</span>` : ""}</p>
      ${c.why ? `<p class="xs clip-why" data-testid="clip-why" title="${esc(c.why)}">${c.rank ? `<b>#${c.rank}</b> · ` : ""}Why this moment: ${esc(c.why)}</p>` : ""}
      ${Object.keys(c.variants || {}).length ? `<p class="muted xs" data-testid="clip-variants">Also in ${Object.entries(c.variants).map(([r, u]) => `<a class="link" href="${escUrl(u)}" target="_blank" rel="noopener">${esc(r)}</a>`).join(" · ")} for the channels that need it</p>` : ""}
      ${c.music ? `<p class="xs clip-music" data-testid="clip-music-chip">Music: ${esc(c.music.title)}${c.music.display_artist ? " · " + esc(c.music.display_artist) : ""}</p>` : ""}
      ${st}${c.rerender_error ? `<span class="st err xs" data-testid="clip-rerender-error">Re-render failed: ${esc(c.rerender_error)} The previous video is kept.</span>` : ""}
      <div class="clip-pf">${pfCompact(c)}</div>
      ${placed.length ? `<p class="xs clip-placed" data-testid="clip-placed">${placed.map((p) => `${netMark(p.service)} ${esc(p.local)} · ${p.status === "draft" ? "Buffer draft" : "waiting for approval"}`).join("<br>")}</p>` : ""}
      ${failed.length ? `<p class="st err xs">${esc(failed[0].error)}</p>` : ""}
    </div>
    <div class="clip-act">${c.url ? `<button type="button" class="btn2 sm" data-act="edit" data-clip="${esc(c.id)}" data-testid="clip-edit">${SCISSORS}<span>Trim &amp; caption</span></button>${T.take && T.take.style === "take" && !T.take.sample ? `<button type="button" class="btn2 sm" data-act="layout" data-clip="${esc(c.id)}" data-testid="clip-layout">${LAYOUT_IC}<span>Layout</span></button>` : ""}<a class="link xs" href="${escUrl(c.url)}" target="_blank" rel="noopener" data-testid="clip-open">Open video</a>` : ""}${T.take && !T.take.sample ? `<button type="button" class="link xs danger" data-act="delete" data-clip="${esc(c.id)}" data-testid="clip-delete" aria-label="Delete clip ${n}">Delete</button>` : ""}</div>
  </li>`;
}

// ---------------------------------------------------------------- per-clip editor: Trim & caption (takeedit.js)
function clipAct(act, id) {
  if (act === "edit" && window.openClipEditor) window.openClipEditor(id);
  if (act === "layout" && window.openClipLayout) window.openClipLayout(id);  // takelayout.js
  if (act === "delete") deleteClip(id);
}
async function deleteClip(id) {
  if (!T.take || !(await hqConfirm("Delete this clip? It moves to the Bin (Library › Bin) for 7 days, then it's removed for good. A Buffer draft already made from it stays in Buffer.", { ok: "Delete clip", danger: true }))) return;
  const rid = T.take.id;
  try {
    const r = await api(`/api/takes/${encodeURIComponent(rid)}/clips/${encodeURIComponent(id)}`, { method: "DELETE" });
    tkUndoToast(`Clip moved to the Bin for ${(r && r.in_bin_days) || 7} days.`, { take_id: rid, clip_id: id });
    tkRefresh();  // the list refreshes whether or not the open take's own refresh below succeeds
    tkShowIfOpen(rid).catch((e) => toast(e.message, 6000));
  } catch (e) { toast(e.message, 6000); }
}
async function deleteTake(id) {
  if (!(await hqConfirm("Delete this take and all its clips? It moves to the Bin (Library › Bin) for 7 days, then it and its files are removed for good. A render still running is stopped.", { ok: "Delete take", danger: true }))) return;
  try {
    const r = await api(`/api/takes/${encodeURIComponent(id)}`, { method: "DELETE" });
    tkUndoToast(`Take moved to the Bin for ${(r && r.in_bin_days) || 7} days.`, { take_id: id });
    if (T.take && String(T.take.id) === String(id)) { clearTimeout(T.poll); T.watching = null; T.take = null; T.plan = null; $("#takeView").hidden = true; }
    tkRefresh();
  } catch (e) { toast(e.message, 6000); }
}
// Undo right after a delete: the toast's Undo restores it from the Bin (the same restore the Library Bin's button calls)
function tkUndoToast(msg, ref) {
  const undo = () => tkRestore(ref).catch((e) => toast(e.message, 6000));
  if (window.toastUndo) window.toastUndo(msg, undo, 8000); else toast(msg, 6000);
}
async function tkRestore(ref) {
  const path = ref.clip_id ? `/api/takes/${encodeURIComponent(ref.take_id)}/clips/${encodeURIComponent(ref.clip_id)}/restore`
    : `/api/takes/${encodeURIComponent(ref.take_id)}/restore`;
  await api(path, { method: "POST", body: {} });
  toast(ref.clip_id ? "Clip restored." : "Take restored.");
  if (ref.clip_id) { try { await tkShowIfOpen(ref.take_id); } catch (e) { toast(e.message, 6000); } }
  tkRefresh();
}
// redraw the open take, only if it is still the one open once its answer is back (the user may have opened another)
async function tkShowIfOpen(rid) {
  const open = () => T.take && String(T.take.id) === String(rid);
  if (!open()) return;
  const t = await api(`/api/takes/${encodeURIComponent(rid)}`);
  if (open()) showTake(t);
}
// one Bin (owner ask, 5 Nov 2026): deleted takes and clips are listed in the Library's Bin, so only Your takes reloads here
function tkRefresh() { loadTakes(); }

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
  document.querySelectorAll("#clipGrid [data-testid=clip-card]").forEach((el) => { const m = el.querySelector(".clip-thumb"); from[el.dataset.clip] = (m || el).getBoundingClientRect(); });
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

// ---------------------------------------------------------------- Your takes (5 Nov 2026)
// One Bin (owner ask, 5 Nov 2026): One take has no Bin of its own any more. Deleting a take or a clip moves it to the
// Library's Bin (Library › Bin lists binned takes and clips with Restore and Delete permanently, the same 7 days), and the
// toast right after offers Undo. The list is one live request (HQLoad.start, an AbortController): leaving One take aborts
// it, so a late answer never draws over another screen; a failed load says so with Retry.
// The cards (owner bug, 5 Nov 2026): the old rows put an absolutely positioned "Delete" on top of the title and long titles
// pushed the grid items wider than their track, so cards overlapped. Each card now has a fixed track (min-width 0),
// one-line titles with an ellipsis and a ⋯ menu in its own column (the Library's menus: popover, or a bottom sheet on a phone).
const TK_KEY = "takes";
const TK_IC = {  // Lucide (ISC): more-horizontal, trash-2
  more: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/></svg>',
  trash: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><path d="M10 11v6M14 11v6"/></svg>',
};
/** Cancel the list request in flight and a take still opening (One take was left). */
function tkAbort() { HQLoad.start(TK_KEY).done(); HQLoad.start("takeopen").done(); }
/** Draw a load error with a Retry button in box (role=alert, never a silent empty list). */
function tkErr(box, msg, retry, id) {
  box.setAttribute("aria-busy", "false");
  box.innerHTML = `<div class="tk-err" role="alert" data-testid="${id}-error"><span>${esc(msg || "Something went wrong.")}</span><button type="button" class="btn2 sm" data-testid="${id}-retry">Retry</button></div>`;
  box.querySelector(`[data-testid=${id}-retry]`).onclick = retry;
}
// leaving One take (any screen change hides #tab-take) cancels the list's request and closes an open ⋯ menu
new MutationObserver(() => { if ($("#tab-take").hidden) { tkAbort(); if (window.hqMenu) window.hqMenu.closeIn($("#takeList")); } })
  .observe($("#tab-take"), { attributes: true, attributeFilter: ["hidden"] });
const tkWhen = (ts) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
/** One card under Your takes: open button (thumb, one-line title, status) and a ⋯ menu with Delete (not on the sample). */
function tkCard(x) {
  const title = x.title || "Your take";
  const meta = `${x.status} · ${x.clips} clip${x.clips === 1 ? "" : "s"}${x.placed ? ` · ${x.placed} placed` : ""} · ${tkWhen(x.created)}`;
  const menu = x.sample ? "" : `<span class="lp-mw tk-mw"><button type="button" class="lp-more tk-more" data-testid="take-more" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for ${esc(title)}">${TK_IC.more}</button>
    <div class="lp-menu r" role="menu" aria-label="${esc(title)}" hidden><button type="button" class="lp-mi danger" role="menuitem" data-testid="take-delete" data-del="${esc(x.id)}">${TK_IC.trash}<span>Delete take…</span></button></div></span>`;
  return `<article class="tk-card" data-testid="take-card" data-take="${esc(x.id)}">
    <button type="button" class="tk-open" data-testid="take-row" data-id="${esc(x.id)}" title="${esc(title)}">${x.cover ? `<img src="${esc(x.cover)}" alt="" loading="lazy">` : `<span class="vt" aria-hidden="true">▶</span>`}
      <span class="rt"><b data-testid="take-title">${esc(title)}</b><small>${esc(meta)}</small></span></button>${menu}
  </article>`;
}
/** Load Your takes into #takeList through one live request (aborted when One take is left); Retry on a failure. */
async function loadTakes() {
  const box = $("#takeList"), tk = HQLoad.start(TK_KEY), sig = { signal: tk.signal };
  if (!box.querySelector(".tk-card, .empty")) { box.innerHTML = HQLoad.rows(3, "Loading your takes"); box.setAttribute("aria-busy", "true"); }
  try {
    const rows = await api("/api/takes", sig);
    if (!tk.live()) return;
    box.setAttribute("aria-busy", "false");
    if (window.hqMenu) window.hqMenu.closeIn(box);  // the redraw replaces an open ⋯ menu: close it first
    box.innerHTML = rows.length ? rows.map(tkCard).join("")
      : `<div class="empty"><b>No takes yet</b><span>Drop a talk, a podcast episode or a tutorial you recorded, or try the sample take.</span></div>`;
    box.querySelectorAll("[data-testid=take-more]").forEach((b) => (b.onclick = (ev) => { ev.stopPropagation(); window.hqMenu.toggle(b, b.nextElementSibling); }));
    box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = (ev) => { ev.stopPropagation(); window.hqMenu.close(); deleteTake(b.dataset.del); }));
    box.querySelectorAll("[data-testid=take-row]").forEach((b) => (b.onclick = async () => {
      const op = HQLoad.start("takeopen");  // opening another take aborts this one: only the latest pick is drawn
      try {
        const t = await api(`/api/takes/${encodeURIComponent(b.dataset.id)}`, { signal: op.signal });
        if (!op.live() || $("#tab-take").hidden) return;  // a take opened, then One take was left: never drawn elsewhere
        showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id); window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
      } catch (e) { if (!HQLoad.aborted(e) && op.live() && !$("#tab-take").hidden) toast(e.message, 6000); }
      finally { op.done(); }
    }));
  } catch (e) {
    if (HQLoad.aborted(e) || !tk.live()) return;
    tkErr(box, `Your takes didn't load: ${e.message}`, () => loadTakes(), "take-list");
  } finally { tk.done(); }
}
async function loadTake() {
  takeChips(); loadTakes();
  $("#tkSample").hidden = false;
  const u = new URLSearchParams(location.search);
  if (u.get("take") && !T.take) { try { const t = await api(`/api/takes/${u.get("take")}`); showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id); } catch (_) {} }
}
// Open a take from anywhere (View run, a thread, Make a post): the take is fetched FIRST and One take is shown once with it,
// so no other screen (or an older take) flashes in between and Back returns to where the person was (owner bug, 5 Nov 2026).
window.openTake = async (id) => {
  const op = HQLoad.start("takeopen");
  try {
    const t = await api(`/api/takes/${encodeURIComponent(id)}`, { signal: op.signal });
    if (!op.live()) return;
    showTab("take"); showTake(t); if (["running", "rendering"].includes(t.status)) watchTake(t.id);
  } catch (e) { if (!HQLoad.aborted(e) && op.live()) toast(e.message, 6000); }
  finally { op.done(); }
};
window.HS_TABS = Object.assign(window.HS_TABS || {}, { take: loadTake });
