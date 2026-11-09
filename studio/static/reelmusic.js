// Instagram music on Reels (studio/reel_audio.py; UI = REST = MCP: search_instagram_audio, set_reel_audio, adjust_clip music).
// One picker, two homes: HQMusic.mount() draws it inside a container (the Music tab of One take's Trim & caption window)
// and openMusicPicker() wraps the same picker in a dialog for an agent-run package card. Search Instagram's catalog
// through Buffer (blank = trending; the server caches each search for 5 minutes), play a 30-second preview, pick a track
// (shown as a chip: title · artist, Change / Remove), and set the Music and Video sound levels. Instagram picks which part
// of the track plays: Buffer's API has no start time. Channels connected with Instagram Login can't take music: the
// picker says how to fix that.
"use strict";
(() => {
const H_ESC = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mmss = (ms) => { const s = Math.max(0, Math.round((+ms || 0) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const VID = /\.(mp4|mov|m4v|webm)(\?|$)/i;
const who = (t) => t.display_artist || (t.creator_username ? "@" + t.creator_username : "");
const kind = (t) => (t.type === "originalSound" ? "Original sound" : "Music");
const NOTE_ICON = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M9 18.5a3 3 0 1 1-2-2.83V5.5l12-2.5v12a3 3 0 1 1-2-2.83V7.4l-8 1.67V18.5z"/></svg>`;
const HELP = "https://support.buffer.com/en-us/articles/getting-instagram-locations-facebook-authorization-required-acaC8V633l";
const PICKS_NOTE = "Instagram picks which part of the track plays.";
let CUR = null;  // the one preview that may play at a time

function art(t) { return t.cover_url ? `<img src="${H_ESC(t.cover_url)}" alt="" loading="lazy">` : `<span class="rm-art">${NOTE_ICON}</span>`; }
// Meta's artwork links are temporary: a cover that doesn't load becomes the note mark instead of a broken image
function noBroken(box) { box.querySelectorAll("img").forEach((i) => (i.onerror = () => { const s = document.createElement("span"); s.className = "rm-art"; s.innerHTML = NOTE_ICON; i.replaceWith(s); })); }
window.HQ_MUSIC_NOBROKEN = noBroken;
const levels = (au) => `Music ${au.music_volume ?? 100}% · Video sound ${au.original_volume ?? 100}%`;
// the chosen track, the same chip everywhere (package card, Trim & caption, the clip row)
function chip(au, { actions = true, compact = false } = {}) {
  return `<div class="rm-chosen${compact ? " sm" : ""}" data-testid="music-chosen">${art(au)}
    <div class="rm-txt"><b>${H_ESC(au.title)}${who(au) ? ` <span class="rm-who">· ${H_ESC(who(au))}</span>` : ""}</b>
      <small>${H_ESC([kind(au), mmss(au.duration_ms)].filter(Boolean).join(" · "))}</small>
      <small data-testid="music-levels">${levels(au)}</small></div>
    ${actions ? `<span class="row"><button type="button" class="btn2 sm" data-testid="music-change">Change</button><button type="button" class="link sm" data-testid="music-remove">Remove</button></span>` : ""}</div>`;
}
window.HQMusicChip = chip;

// mount(box, { channelId, value, onChange }) -> { get(), search(q), stop() }. value / get(): the track with music_volume and
// original_volume (0-100), or null. Nothing is saved here: the caller saves (set_reel_audio, adjust_clip).
function mount(box, opts) {
  const M = { tracks: [], pick: opts.value ? { ...opts.value } : null, audio: null, playing: -1, searched: false };
  box.classList.add("rm-picker");
  box.innerHTML = `
    <p class="muted sm rm-about">Instagram's licensed music and original sounds, through Buffer. <b>${PICKS_NOTE}</b></p>
    <div class="rm-sel" data-testid="music-selected"></div>
    <div class="rm-search" role="search"><label class="sr" for="rmQ_${opts.channelId}">Search music</label>
      <input id="rmQ_${opts.channelId}" type="search" placeholder="Search songs, artists or sounds" autocomplete="off" data-testid="music-q">
      <button type="button" class="btn2 sm" data-testid="music-search">Search</button></div>
    <div class="rm-status" role="status" aria-live="polite" data-testid="music-status"></div>
    <ul class="rm-list" data-testid="music-results" aria-label="Tracks"></ul>
    <fieldset class="rm-mix" data-testid="music-mix">
      <legend>Mix</legend>
      <label><span class="rm-lab">Music <output data-testid="music-vol-out"></output></span>
        <input type="range" min="0" max="100" step="5" data-testid="music-vol" aria-label="Music volume"></label>
      <label><span class="rm-lab">Video sound <output data-testid="music-orig-out"></output></span>
        <input type="range" min="0" max="100" step="5" data-testid="music-orig" aria-label="The video's own sound volume"></label>
    </fieldset>`;
  const $b = (sel) => box.querySelector(sel);
  const vol = $b("[data-testid=music-vol]"), orig = $b("[data-testid=music-orig]");
  vol.value = M.pick?.music_volume ?? 100; orig.value = M.pick?.original_volume ?? 100;
  const changed = () => opts.onChange && opts.onChange(get());
  function paintLevels() {
    $b("[data-testid=music-vol-out]").textContent = vol.value + "%"; $b("[data-testid=music-orig-out]").textContent = orig.value + "%";
    $b("[data-testid=music-mix]").disabled = !M.pick;
  }
  function paintSel() {
    const sel = $b("[data-testid=music-selected]");
    sel.innerHTML = M.pick ? chip({ ...M.pick, music_volume: +vol.value, original_volume: +orig.value }) : `<p class="muted sm" data-testid="music-none">No music on this Reel yet. Pick a track below.</p>`;
    noBroken(sel);
    const ch = sel.querySelector("[data-testid=music-change]"), rm = sel.querySelector("[data-testid=music-remove]");
    if (ch) ch.onclick = () => { $b("[data-testid=music-q]").focus(); if (!M.searched) search(""); };
    if (rm) rm.onclick = () => { M.pick = null; stop(); paintSel(); paintLevels(); drawTracks(); changed(); };
    paintLevels();
  }
  function status(html, cls = "") { const s = $b("[data-testid=music-status]"); s.className = "rm-status " + cls; s.innerHTML = html; }
  function drawTracks() {
    const ul = $b("[data-testid=music-results]");
    ul.innerHTML = M.tracks.map((t, i) => { const on = !!(M.pick && M.pick.audio_id === t.audio_id);
      return `<li class="rm-track${on ? " on" : ""}" data-testid="music-track">${art(t)}
        <div class="rm-txt"><b>${H_ESC(t.title)}</b><small>${H_ESC([who(t), mmss(t.duration_ms)].filter(Boolean).join(" · "))}</small></div>
        <span class="rm-kind ${t.type === "originalSound" ? "orig" : ""}">${kind(t)}</span>
        ${t.preview_url ? `<button type="button" class="btn2 sm rm-play" data-play="${i}" data-testid="music-preview" aria-label="Play a 30-second preview of ${H_ESC(t.title)}">${M.playing === i ? "Stop" : "Preview"}</button>` : `<span class="muted xs rm-nopv">No preview</span>`}
        <button type="button" class="btn2 sm" data-pick="${i}" data-testid="music-pick" aria-pressed="${on}">${on ? "Picked" : "Pick"}</button></li>`; }).join("");
    noBroken(ul);
    ul.querySelectorAll("[data-play]").forEach((b) => (b.onclick = () => play(+b.dataset.play)));
    ul.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => { M.pick = { ...M.tracks[+b.dataset.pick] }; paintSel(); drawTracks(); changed(); }));
  }
  function stop() { if (M.audio) { M.audio.pause(); M.audio = null; } M.playing = -1; box.querySelectorAll("[data-play]").forEach((b) => (b.textContent = "Preview")); }
  function play(i) {
    const t = M.tracks[i];
    if (M.audio && M.playing === i) { stop(); return; }
    if (CUR && CUR !== api_) CUR.stop();
    stop(); CUR = api_;
    const a = new Audio(t.preview_url);  // Buffer's search returns Instagram's own 30-second preview link
    a.volume = (+vol.value || 0) / 100;
    M.audio = a; M.playing = i; drawTracks();
    a.onended = () => stop();
    a.play().catch(() => { stop(); status("That preview didn't play (Instagram's preview links expire). Search again for a fresh one.", "warn"); });
  }
  async function search(q) {
    M.searched = true;
    status(`<span class="spin" aria-hidden="true"></span> ${q ? "Searching Instagram music…" : "Loading what's trending…"}`);
    try {
      const out = await api(`/api/instagram/audio?channel_id=${encodeURIComponent(opts.channelId)}&q=${encodeURIComponent(q)}`);
      if (out.needs_facebook) {
        M.tracks = []; drawTracks();
        status(`<b data-testid="music-needs-fb">${H_ESC(out.message)}</b> <a href="${escUrl(out.help_url)}" target="_blank" rel="noopener" data-testid="music-help">How to connect it in Buffer</a>`, "warn");
        return;
      }
      M.tracks = out.tracks || [];
      if (M.pick && !M.tracks.some((t) => t.audio_id === M.pick.audio_id)) M.tracks.unshift(M.pick);
      drawTracks();
      status(M.tracks.length ? `${q ? "Results for “" + H_ESC(q) + "”" : "Trending on Instagram"} · ${M.tracks.length} track${M.tracks.length > 1 ? "s" : ""}${out.note ? " · " + H_ESC(out.note) : ""}` : "Nothing found. Try another song or artist.");
    } catch (e) {
      M.tracks = []; drawTracks();
      status(H_ESC(e.message) + (e.status === 409 ? ` <a href="${HELP}" target="_blank" rel="noopener">How to connect it in Buffer</a>` : ""), "warn");
    }
  }
  function get() { return M.pick ? { ...M.pick, music_volume: +vol.value, original_volume: +orig.value } : null; }
  vol.oninput = () => { paintLevels(); paintSel(); if (M.audio) M.audio.volume = vol.value / 100; changed(); };
  orig.oninput = () => { paintLevels(); paintSel(); changed(); };
  // a div, not a form: the picker also sits inside the Trim & caption form, and forms can't nest
  $b("[data-testid=music-search]").onclick = () => search($b("[data-testid=music-q]").value.trim());
  $b("[data-testid=music-q]").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(e.target.value.trim()); } });
  paintSel();
  const api_ = { get, search, stop, get searched() { return M.searched; } };
  return api_;
}
window.HQMusic = { mount, chip, levels, PICKS_NOTE };

// ---------------------------------------------------------------- agent-run package cards
function canMusic(p) { return p.service === "instagram" && (p.media_urls || []).some((u) => VID.test(u)); }
window.pkgMusic = function (p, sent) {
  if (!canMusic(p)) return "";
  const au = (p.options || {}).ig_audio;
  if (!au) {
    return `<div class="pkg-music" data-testid="pkg-music"><small class="muted sm">Music</small>
      ${sent ? `<span class="muted sm">No music</span>` : `<button type="button" class="btn2 sm rm-add" data-testid="music-add">${NOTE_ICON}<span>Add Instagram music</span></button>`}</div>`;
  }
  return `<div class="pkg-music" data-testid="pkg-music"><small class="muted sm">Music</small>${chip(au, { actions: !sent })}</div>`;
};

let D = null;  // the open package picker: { dlg, picker }
function close() { if (D) { D.picker.stop(); try { D.dlg.close(); } catch (_) {} D.dlg.remove(); D = null; } }
window.openMusicPicker = function (r, pk) {
  close();
  const dlg = document.createElement("dialog");
  dlg.className = "pl-dialog rm-dialog"; dlg.dataset.testid = "music-dialog"; dlg.setAttribute("aria-labelledby", "rmTitle");
  const au = (pk.options || {}).ig_audio;
  dlg.innerHTML = `<div class="rm-form">
    <div class="rm-head"><div><h2 id="rmTitle">Instagram music</h2><p class="muted sm">For the Reel on ${H_ESC(pk.channel_name || "Instagram")}.</p></div>
      <button type="button" class="icon-btn" data-testid="music-close" aria-label="Close"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <div data-testid="music-picker"></div>
    <div class="pl-dlg-act"><span class="muted sm" data-testid="music-picked">${au ? "Picked: " + H_ESC(au.title) : "Pick a track"}</span><span class="row">
      <button type="button" class="btn2 sm" data-testid="music-cancel">Cancel</button>
      <button type="button" class="btn sm" data-testid="music-save" ${au ? "" : "disabled"}>Use this music</button></span></div></div>`;
  document.body.append(dlg);
  const save = dlg.querySelector("[data-testid=music-save]");
  const picker = mount(dlg.querySelector("[data-testid=music-picker]"), { channelId: pk.channel_id, value: au ? { ...au } : null,
    onChange: (v) => { save.disabled = !v && !au; dlg.querySelector("[data-testid=music-picked]").textContent = v ? "Picked: " + v.title : (au ? "Music will be removed" : "Pick a track"); } });
  D = { dlg, picker };
  dlg.querySelector("[data-testid=music-close]").onclick = close;
  dlg.querySelector("[data-testid=music-cancel]").onclick = close;
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  save.onclick = async () => {
    const v = picker.get(); save.disabled = true;
    try {
      await api(`/api/agent/runs/${r.id}/packages/${encodeURIComponent(pk.channel_id)}/audio`, { method: "POST",
        body: v ? { audio: v, music_volume: v.music_volume, original_volume: v.original_volume } : { audio: null } });
      close(); toast(v ? "Music added. Instagram adds it when the Reel is posted; play the Reel here to hear it with the track." : "Music removed", 5000);
      showRun(await api(`/api/agent/runs/${r.id}`));
    } catch (e) { toast(e.message, 6000); save.disabled = false; }
  };
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  dlg.querySelector("[data-testid=music-q]").focus();
  picker.search("");
};

// "I selected the music but it didn't get attached to the video" (owner, 9 Nov 2026): Instagram music is never inside the
// file (Buffer sends the track id and Instagram adds it when it posts), so the card plays Instagram's preview of the track
// along with the Reel, at the chosen levels, to show what the post will sound like. The file itself stays as it is.
function withVideo(el, au) {
  const v = el.querySelector("[data-testid=pkg-video]"), tg = el.querySelector("[data-testid=music-with-video]");
  if (!v || !au || !au.preview_url) return;
  const a = new Audio(au.preview_url); a.preload = "none"; a.loop = true;
  let ok = true;
  const on = () => !tg || tg.checked;
  const mix = () => { a.volume = Math.max(0, Math.min(1, (au.music_volume ?? 100) / 100)); v.volume = on() ? Math.max(0, Math.min(1, (au.original_volume ?? 100) / 100)) : 1; };
  const sync = () => { if (isFinite(a.duration) && a.duration > 0) a.currentTime = v.currentTime % a.duration; };
  const play = () => { if (!ok || !on()) return; if (CUR && CUR !== a) CUR.pause(); CUR = a; mix(); sync(); a.play().catch(() => {}); };
  a.onerror = () => { ok = false; const n = el.querySelector("[data-testid=music-how]"); if (n) n.textContent = "Instagram's preview of this track has expired here, but the track still goes to Buffer with the Reel."; };
  v.addEventListener("play", play);
  v.addEventListener("pause", () => a.pause());
  v.addEventListener("ended", () => a.pause());
  v.addEventListener("seeked", () => { sync(); });
  if (tg) tg.onchange = () => { mix(); if (on() && !v.paused) play(); else a.pause(); };
}

window.wireMusic = function (el, r, pk) {
  const add = el.querySelector("[data-testid=music-add]"), ch = el.querySelector("[data-testid=music-change]"), rm = el.querySelector("[data-testid=music-remove]");
  if (add) add.onclick = () => window.openMusicPicker(r, pk, el);
  if (ch) ch.onclick = () => window.openMusicPicker(r, pk, el);
  const box = el.querySelector("[data-testid=pkg-music]"); if (box) noBroken(box);
  withVideo(el, (pk.options || {}).ig_audio);
  if (rm) rm.onclick = async () => {
    rm.disabled = true;
    try { await api(`/api/agent/runs/${r.id}/packages/${encodeURIComponent(pk.channel_id)}/audio`, { method: "POST", body: { audio: null } });
      toast("Music removed"); showRun(await api(`/api/agent/runs/${r.id}`)); }
    catch (e) { toast(e.message, 6000); rm.disabled = false; }
  };
};
})();
