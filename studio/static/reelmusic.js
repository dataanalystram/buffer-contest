// Instagram music on Reels (studio/reel_audio.py; UI = REST = MCP: search_instagram_audio, set_reel_audio).
// A package card for an Instagram video shows "Music": add a track from Instagram's catalog (search, 30-second preview,
// pick), set the track's and the video's own volume and a start time, or remove it. The choice is saved on the package
// and sent to Buffer with it. Channels connected with Instagram Login can't take music: the dialog says how to fix that.
"use strict";
(() => {
const H_ESC = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mmss = (ms) => { const s = Math.max(0, Math.round((+ms || 0) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const VID = /\.(mp4|mov|m4v|webm)(\?|$)/i;
const who = (t) => t.display_artist || (t.creator_username ? "@" + t.creator_username : "");
const kind = (t) => (t.type === "originalSound" ? "Original sound" : "Music");
const NOTE_ICON = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M9 18.5a3 3 0 1 1-2-2.83V5.5l12-2.5v12a3 3 0 1 1-2-2.83V7.4l-8 1.67V18.5z"/></svg>`;

function canMusic(p) { return p.service === "instagram" && (p.media_urls || []).some((u) => VID.test(u)); }

window.pkgMusic = function (p, sent) {
  if (!canMusic(p)) return "";
  const au = (p.options || {}).ig_audio;
  if (!au) {
    return `<div class="pkg-music" data-testid="pkg-music"><small class="muted sm">Music</small>
      ${sent ? `<span class="muted sm">No music</span>` : `<button type="button" class="btn2 sm rm-add" data-testid="music-add">${NOTE_ICON}<span>Add Instagram music</span></button>`}</div>`;
  }
  return `<div class="pkg-music" data-testid="pkg-music"><small class="muted sm">Music</small>
    <div class="rm-chosen" data-testid="music-chosen">${au.cover_url ? `<img src="${H_ESC(au.cover_url)}" alt="" loading="lazy">` : `<span class="rm-art">${NOTE_ICON}</span>`}
      <div class="rm-txt"><b>${H_ESC(au.title)}</b><small>${H_ESC([who(au), kind(au), mmss(au.duration_ms)].filter(Boolean).join(" · "))}</small>
      <small data-testid="music-levels">Music ${au.music_volume}% · Video sound ${au.original_volume}%${au.start_ms ? " · from " + mmss(au.start_ms) : ""}</small></div>
      ${sent ? "" : `<span class="row"><button type="button" class="btn2 sm" data-testid="music-change">Change</button><button type="button" class="link sm" data-testid="music-remove">Remove</button></span>`}</div></div>`;
};

let D = null;  // the open picker: { dlg, run, pk, el, tracks, pick, audio }

function dialogHtml(pk) {
  const au = (pk.options || {}).ig_audio || {};
  return `<form method="dialog" class="rm-form">
    <div class="rm-head"><div><h2 id="rmTitle">Add Instagram music</h2><p class="muted sm">For the Reel on ${H_ESC(pk.channel_name || "Instagram")}. Instagram's licensed music and original sounds, through Buffer.</p></div>
      <button type="button" class="icon-btn" data-testid="music-close" aria-label="Close">✕</button></div>
    <div class="rm-search" role="search"><label class="sr" for="rmQ">Search music</label>
      <input id="rmQ" type="search" placeholder="Search songs, artists or sounds" autocomplete="off" data-testid="music-q">
      <button type="submit" class="btn sm" data-testid="music-search">Search</button></div>
    <div class="rm-status" role="status" aria-live="polite" data-testid="music-status"></div>
    <ul class="rm-list" data-testid="music-results" aria-label="Tracks"></ul>
    <fieldset class="rm-mix" data-testid="music-mix" disabled>
      <legend>Mix</legend>
      <label><span class="rm-lab">Music volume <output data-testid="music-vol-out">${au.music_volume ?? 100}%</output></span>
        <input type="range" min="0" max="100" step="5" value="${au.music_volume ?? 100}" data-testid="music-vol" aria-label="Music volume"></label>
      <label><span class="rm-lab">Video's own sound <output data-testid="music-orig-out">${au.original_volume ?? 100}%</output></span>
        <input type="range" min="0" max="100" step="5" value="${au.original_volume ?? 100}" data-testid="music-orig" aria-label="Video's own sound volume"></label>
      <label>Start at <span class="rm-start"><input type="number" min="0" step="1" value="${Math.round((au.start_ms || 0) / 1000)}" data-testid="music-start" aria-describedby="rmStartHelp"> s</span></label>
      <small class="muted sm" id="rmStartHelp">Saved with the post. Buffer can't send a start time yet, so Instagram plays the track from its default start.</small>
    </fieldset>
    <div class="pl-dlg-act"><span class="muted sm" data-testid="music-picked">${au.title ? "Picked: " + H_ESC(au.title) : "Pick a track"}</span><span class="row">
      <button type="button" class="btn2 sm" data-testid="music-cancel">Cancel</button>
      <button type="button" class="btn sm" data-testid="music-save" ${au.title ? "" : "disabled"}>Use this music</button></span></div>
  </form>`;
}

// Meta's artwork links are temporary: a cover that doesn't load becomes the note mark instead of a broken image
function noBroken(box) { box.querySelectorAll("img").forEach((i) => (i.onerror = () => { const s = document.createElement("span"); s.className = "rm-art"; s.innerHTML = NOTE_ICON; i.replaceWith(s); })); }
window.HQ_MUSIC_NOBROKEN = noBroken;

function status(html, cls = "") { const s = D.dlg.querySelector("[data-testid=music-status]"); s.className = "rm-status " + cls; s.innerHTML = html; }

function drawTracks() {
  const ul = D.dlg.querySelector("[data-testid=music-results]");
  ul.innerHTML = D.tracks.map((t, i) => `<li class="rm-track${D.pick && D.pick.audio_id === t.audio_id ? " on" : ""}" data-testid="music-track">
      ${t.cover_url ? `<img src="${H_ESC(t.cover_url)}" alt="" loading="lazy">` : `<span class="rm-art">${NOTE_ICON}</span>`}
      <div class="rm-txt"><b>${H_ESC(t.title)}</b><small>${H_ESC([who(t), mmss(t.duration_ms)].filter(Boolean).join(" · "))}</small></div>
      <span class="rm-kind ${t.type === "originalSound" ? "orig" : ""}">${kind(t)}</span>
      ${t.preview_url ? `<button type="button" class="btn2 sm rm-play" data-play="${i}" data-testid="music-preview" aria-label="Preview ${H_ESC(t.title)}">Play</button>` : `<span class="muted sm rm-nopv">No preview</span>`}
      <button type="button" class="btn2 sm" data-pick="${i}" data-testid="music-pick" aria-pressed="${!!(D.pick && D.pick.audio_id === t.audio_id)}">${D.pick && D.pick.audio_id === t.audio_id ? "Picked" : "Pick"}</button></li>`).join("");
  noBroken(ul);
  ul.querySelectorAll("[data-play]").forEach((b) => (b.onclick = () => play(+b.dataset.play, b)));
  ul.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => {
    D.pick = D.tracks[+b.dataset.pick];
    D.dlg.querySelector("[data-testid=music-mix]").disabled = false;
    D.dlg.querySelector("[data-testid=music-save]").disabled = false;
    D.dlg.querySelector("[data-testid=music-picked]").textContent = "Picked: " + D.pick.title;
    drawTracks();
  }));
}

function stop() { if (D && D.audio) { D.audio.pause(); D.audio = null; } D && D.dlg.querySelectorAll("[data-play]").forEach((b) => (b.textContent = "Play")); }

function play(i, btn) {
  const t = D.tracks[i];
  if (D.audio && D.playing === i) { stop(); return; }
  stop();
  const a = new Audio(t.preview_url);  // a short-lived Meta link straight from Buffer's search
  a.volume = (+D.dlg.querySelector("[data-testid=music-vol]").value || 100) / 100;
  D.audio = a; D.playing = i; btn.textContent = "Stop";
  a.onended = () => stop();
  a.play().catch(() => { stop(); status("That preview didn't play (Instagram's preview links expire). Search again for a fresh one.", "warn"); });
}

async function search(q) {
  status(`<span class="spin" aria-hidden="true"></span> ${q ? "Searching Instagram music…" : "Loading what's trending…"}`);
  try {
    const out = await api(`/api/instagram/audio?channel_id=${encodeURIComponent(D.pk.channel_id)}&q=${encodeURIComponent(q)}`);
    if (out.needs_facebook) {
      D.tracks = []; drawTracks();
      status(`<b data-testid="music-needs-fb">${H_ESC(out.message)}</b> <a href="${H_ESC(out.help_url)}" target="_blank" rel="noopener" data-testid="music-help">How to connect it in Buffer</a>`, "warn");
      return;
    }
    D.tracks = out.tracks || [];
    if (D.pick && !D.tracks.some((t) => t.audio_id === D.pick.audio_id)) D.tracks.unshift(D.pick);
    drawTracks();
    status(D.tracks.length ? `${q ? "Results for “" + H_ESC(q) + "”" : "Trending on Instagram"} · ${D.tracks.length} track${D.tracks.length > 1 ? "s" : ""}${out.note ? " · " + H_ESC(out.note) : ""}` : "Nothing found. Try another song or artist.");
  } catch (e) {
    D.tracks = []; drawTracks();
    status(H_ESC(e.message) + (e.status === 409 ? ` <a href="https://support.buffer.com/en-us/articles/getting-instagram-locations-facebook-authorization-required-acaC8V633l" target="_blank" rel="noopener">How to connect it in Buffer</a>` : ""), "warn");
  }
}

function close() { stop(); if (D) { try { D.dlg.close(); } catch (_) {} D.dlg.remove(); D = null; } }

window.openMusicPicker = function (r, pk, el) {
  close();
  const dlg = document.createElement("dialog");
  dlg.className = "pl-dialog rm-dialog"; dlg.dataset.testid = "music-dialog"; dlg.setAttribute("aria-labelledby", "rmTitle");
  dlg.innerHTML = dialogHtml(pk);
  document.body.append(dlg);
  const au = (pk.options || {}).ig_audio;
  D = { dlg, run: r, pk, el, tracks: [], pick: au ? { ...au, preview_url: "" } : null, audio: null };
  if (D.pick) dlg.querySelector("[data-testid=music-mix]").disabled = false;
  const vol = dlg.querySelector("[data-testid=music-vol]"), orig = dlg.querySelector("[data-testid=music-orig]");
  vol.oninput = () => { dlg.querySelector("[data-testid=music-vol-out]").textContent = vol.value + "%"; if (D.audio) D.audio.volume = vol.value / 100; };
  orig.oninput = () => { dlg.querySelector("[data-testid=music-orig-out]").textContent = orig.value + "%"; };
  dlg.querySelector("form").onsubmit = (e) => { e.preventDefault(); search(dlg.querySelector("#rmQ").value.trim()); };
  dlg.querySelector("[data-testid=music-close]").onclick = close;
  dlg.querySelector("[data-testid=music-cancel]").onclick = close;
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  dlg.querySelector("[data-testid=music-save]").onclick = async (ev) => {
    const b = ev.currentTarget; if (!D.pick) return;
    b.disabled = true;
    const start = Math.max(0, Math.round(+dlg.querySelector("[data-testid=music-start]").value || 0)) * 1000;
    try {
      await api(`/api/agent/runs/${r.id}/packages/${encodeURIComponent(pk.channel_id)}/audio`, { method: "POST",
        body: { audio: D.pick, music_volume: +vol.value, original_volume: +orig.value, start_ms: start } });
      close(); toast("Music added to the Reel. It goes to Buffer with the post.", 4000);
      showRun(await api(`/api/agent/runs/${r.id}`));
    } catch (e) { toast(e.message, 6000); b.disabled = false; }
  };
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  dlg.querySelector("#rmQ").focus();
  search("");
};

window.wireMusic = function (el, r, pk) {
  const add = el.querySelector("[data-testid=music-add]"), ch = el.querySelector("[data-testid=music-change]"), rm = el.querySelector("[data-testid=music-remove]");
  if (add) add.onclick = () => window.openMusicPicker(r, pk, el);
  if (ch) ch.onclick = () => window.openMusicPicker(r, pk, el);
  const box = el.querySelector("[data-testid=pkg-music]"); if (box) noBroken(box);
  if (rm) rm.onclick = async () => {
    rm.disabled = true;
    try { await api(`/api/agent/runs/${r.id}/packages/${encodeURIComponent(pk.channel_id)}/audio`, { method: "POST", body: { audio: null } });
      toast("Music removed"); showRun(await api(`/api/agent/runs/${r.id}`)); }
    catch (e) { toast(e.message, 6000); rm.disabled = false; }
  };
};
})();
