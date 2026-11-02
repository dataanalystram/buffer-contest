// HuhQueue Planner: one place for the Buffer queue and the calendar. Week | Month | Day | List | Board | Grid on the same
// data (one /api/calendar read), filters, a timezone, "No date"
// drafts, Buffer posting times, click an empty slot to schedule, drag to reschedule. Every move goes through the same
// actions as REST and MCP: PATCH-style reschedule of Buffer posts (studio.update_post), approvals get a time, ready
// packages go through preview -> submit. Ported from the HuhQueue calendar (dataanalystram/bufferopensource, MIT).
"use strict";
const PL = { view: "week", date: "", ch: new Set(), type: "", status: "", q: "", tz: "", nodate: false, slots: true,
             data: null, loading: false, req: 0, edit: null, extra: [], open: "" };
const plPhone = () => matchMedia("(max-width:600px)").matches; // week and day become an agenda on a phone
const PL_VIEWS = ["week", "month", "day", "list", "board", "grid"];
const PL_BOARD = [["draft", "Draft"], ["approval", "Needs approval"], ["ready", "Ready to post"], ["scheduled", "Scheduled in Buffer"], ["sent", "Sent"], ["failed", "Failed in Buffer"]];
const PL_KEY = "hq_planner";
const PL_STATES = { scheduled: "Scheduled in Buffer", sent: "Sent", approval: "Needs approval", ready: "Ready to post", draft: "Draft", failed: "Failed in Buffer", ghost: "Planned clip (not saved yet)" };
const PL_TYPES = { text: "Text", image: "Image", carousel: "Carousel", video: "Video or Reel" };
const NET_MARK = { twitter: "X", linkedin: "in", instagram: "IG", bluesky: "bs", threads: "@", mastodon: "M", facebook: "f", tiktok: "TT", youtube: "YT", pinterest: "P", googlebusiness: "G" };
const DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const ROW_H = 52;
const pad2 = (n) => String(n).padStart(2, "0");
const localTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (_) { return "UTC"; } };

// ---------------------------------------------------------------- dates in a chosen timezone
const _fmt = {};
function zoned(date, tz) {
  const f = _fmt[tz] || (_fmt[tz] = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }));
  const o = {}; for (const p of f.formatToParts(date)) o[p.type] = p.value;
  const z = { y: +o.year, mo: +o.month, d: +o.day, h: +o.hour % 24, mi: +o.minute };
  z.ymd = `${z.y}-${pad2(z.mo)}-${pad2(z.d)}`;
  return z;
}
function fromZoned(ymd, hh, mm, tz) { // the instant whose wall clock in tz reads ymd hh:mm
  const [y, mo, d] = ymd.split("-").map(Number);
  let guess = Date.UTC(y, mo - 1, d, hh, mm);
  for (let i = 0; i < 2; i++) { const z = zoned(new Date(guess), tz); guess -= Date.UTC(z.y, z.mo - 1, z.d, z.h, z.mi) - Date.UTC(y, mo - 1, d, hh, mm); }
  return new Date(guess);
}
const ymdUTC = (t) => new Date(t).toISOString().slice(0, 10);
const addDays = (ymd, n) => { const [y, m, d] = ymd.split("-").map(Number); return ymdUTC(Date.UTC(y, m - 1, d + n)); };
const dowOf = (ymd) => (new Date(ymd + "T12:00:00Z").getUTCDay() + 6) % 7; // 0 = Monday
const mondayOf = (ymd) => addDays(ymd, -dowOf(ymd));
const dayLabel = (ymd, o) => new Date(ymd + "T12:00:00Z").toLocaleDateString([], { timeZone: "UTC", ...o });
const timeLabel = (h, mi) => new Date(Date.UTC(2000, 0, 1, h, mi)).toLocaleTimeString([], { timeZone: "UTC", hour: "numeric", minute: "2-digit" });
const todayYmd = () => zoned(new Date(), PL.tz).ymd;

// ---------------------------------------------------------------- state <-> URL and localStorage
function plLoadState() {
  let saved = {}; try { saved = JSON.parse(localStorage.getItem(PL_KEY) || "{}"); } catch (_) {}
  const u = new URLSearchParams(location.search), g = (k) => (u.has(k) ? u.get(k) : saved[k]);
  PL.view = PL_VIEWS.includes(g("view")) ? g("view") : "week";
  PL.date = /^\d{4}-\d{2}-\d{2}$/.test(g("date") || "") ? g("date") : "";
  PL.ch = new Set(String(g("ch") || "").split(",").filter(Boolean));
  PL.type = PL_TYPES[g("type")] ? g("type") : "";
  PL.status = PL_STATES[g("status")] ? g("status") : "";
  PL.q = g("q") || "";
  PL.tz = g("tz") || localTz();
  PL.nodate = String(g("nodate")) === "1";
  PL.slots = String(g("slots")) !== "0";
}
function plSaveState() {
  const s = { view: PL.view, date: PL.date, ch: [...PL.ch].join(","), type: PL.type, status: PL.status, q: PL.q, tz: PL.tz,
              nodate: PL.nodate ? "1" : "", slots: PL.slots ? "" : "0" };
  try { localStorage.setItem(PL_KEY, JSON.stringify(s)); } catch (_) {}
  const u = new URLSearchParams({ tab: "planner" });
  for (const [k, v] of Object.entries(s)) if (v && !(k === "tz" && v === localTz())) u.set(k, v);
  history.replaceState(null, "", "/?" + u.toString());
}
const plFiltersActive = () => PL.ch.size || PL.type || PL.status || PL.q;

// ---------------------------------------------------------------- range for the current view
function plRange() {
  const anchor = PL.date || todayYmd();
  if (PL.view === "week") { const s = mondayOf(anchor); return { days: Array.from({ length: 7 }, (_, i) => addDays(s, i)), anchor }; }
  if (PL.view === "list" || PL.view === "board") { const s = PL.date || todayYmd(); return { days: Array.from({ length: 28 }, (_, i) => addDays(s, i)), anchor }; }
  if (PL.view === "day") return { days: [anchor], anchor };
  if (PL.view === "grid") return { days: [], anchor, all: true };
  const first = anchor.slice(0, 8) + "01", s = mondayOf(first);
  return { days: Array.from({ length: 42 }, (_, i) => addDays(s, i)), anchor, month: first.slice(0, 7) };
}
function plTitle(r) {
  if (PL.view === "month") return dayLabel(r.month + "-15", { month: "long", year: "numeric" });
  if (PL.view === "day") return dayLabel(r.days[0], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  if (PL.view === "grid") return "Feed preview";
  const a = r.days[0], b = r.days[r.days.length - 1];
  return `${dayLabel(a, { day: "numeric", month: "short" })} – ${dayLabel(b, { day: "numeric", month: "short", year: "numeric" })}`;
}

// ---------------------------------------------------------------- data
async function loadPlanner() {
  if (!PL.tz) plLoadState();
  plSaveState(); plControls();
  const my = ++PL.req;
  if (!PL.data) $("#plMain").innerHTML = `<div class="pl-skel" data-testid="planner-loading" aria-busy="true"><span></span><span></span><span></span></div>`;
  try {
    const d = await api("/api/calendar");
    if (my !== PL.req) return;
    PL.data = d;
    for (const it of d.items) it.z = it.when ? zoned(new Date(it.when), PL.tz) : null;
    if (typeof readyBadgeFrom === "function") readyBadgeFrom(d);
  } catch (e) { $("#plMain").innerHTML = `<div class="st err">${esc(e.message)}</div>`; return; }
  plControls(); plDraw();
  if (PL.open) { // opened from the Inbox: jump to the post's week and open it
    const i = plFind(PL.open); PL.open = "";
    if (i) { if (i.z) { PL.date = i.z.ymd; plChanged(); } plOpenItem(i); }
  }
}
// the Inbox's way in: "planner:nodate", "planner:week" or "planner:buffer:<id>"
function plOpenRef(ref) {
  if (!PL.tz) plLoadState();
  if (ref === "nodate") { PL.nodate = true; PL.view = PL.view === "month" ? "week" : PL.view; }
  else if (ref === "week") { PL.view = "week"; PL.date = ""; }
  else PL.open = ref;
  plSaveState();
  showTab("planner");
}
function plVisible() {
  const q = PL.q.trim().toLowerCase();
  for (const g of PL.extra) g.z = g.when ? zoned(new Date(g.when), PL.tz) : null; // One take: planned clips (The Spread)
  return [...(PL.data?.items || []), ...PL.extra].filter((i) => (!PL.ch.size || PL.ch.has(i.channel_id))
    && (!PL.type || (PL.type === "video" ? ["video", "reel"].includes(i.type) : i.type === PL.type))
    && (!PL.status || i.state === PL.status)
    && (!q || (i.text || "").toLowerCase().includes(q) || (i.channel || "").toLowerCase().includes(q)));
}

// ---------------------------------------------------------------- controls (header, filters, toggles)
function plControls() {
  document.querySelectorAll("[data-view]").forEach((b) => { const on = b.dataset.view === PL.view; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  $("#plType").value = PL.type; $("#plStatus").value = PL.status;
  if (document.activeElement !== $("#plSearch")) $("#plSearch").value = PL.q;
  $("#plNoDateT").checked = PL.nodate; $("#plSlotsT").checked = PL.slots;
  const tzSel = $("#plTz");
  if (!tzSel.options.length || tzSel.dataset.for !== PL.tz) {
    let all = []; try { all = Intl.supportedValuesOf("timeZone"); } catch (_) { all = ["UTC", "Europe/Dublin", "Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Kolkata"]; }
    const top = [...new Set([localTz(), PL.tz, ...((PL.data?.slots || []).map((s) => s.timezone))])].filter(Boolean);
    tzSel.innerHTML = `<optgroup label="Suggested">${top.map((z) => `<option value="${esc(z)}">${esc(z.replace(/_/g, " "))}${z === localTz() ? " (this device)" : ""}</option>`).join("")}</optgroup>`
      + `<optgroup label="All timezones">${all.filter((z) => !top.includes(z)).map((z) => `<option value="${esc(z)}">${esc(z.replace(/_/g, " "))}</option>`).join("")}</optgroup>`;
    tzSel.dataset.for = PL.tz;
  }
  tzSel.value = PL.tz;
  const box = $("#plChannels");
  box.innerHTML = S.channels.map((c) => `<button type="button" class="pl-ch${PL.ch.has(c.id) ? " on" : ""}" data-testid="planner-channel" data-ch="${esc(c.id)}" aria-pressed="${PL.ch.has(c.id)}" title="${esc((c.displayName || c.name) + " on " + netName(c.service))}">${netMark(c.service, c.id)}<span>${esc(c.displayName || c.name)}</span></button>`).join("");
  box.querySelectorAll("[data-ch]").forEach((b) => (b.onclick = () => { const id = b.dataset.ch; PL.ch.has(id) ? PL.ch.delete(id) : PL.ch.add(id); plChanged(); }));
  $("#plClear").hidden = !plFiltersActive();
}
function plChanged() { plSaveState(); plControls(); plDraw(); }
// the network's mark (net-logos.js, simple-icons); with a channel id, the channel's avatar too when Buffer has one
function netMark(svc, chId, label) {
  if (chId && window.chMark) { const c = (S.channels || []).find((x) => x.id === chId); if (c) return chMark({ ...c, service: svc || c.service }, label); }
  return window.netIcon ? netIcon(svc, label) : `<span class="net svc-${esc(svc)}" aria-hidden="true">${esc(NET_MARK[svc] || (svc || "?").slice(0, 2))}</span>`;
}
function stateChip(st) { return `<span class="pl-state s-${esc(st)}" data-testid="planner-status">${esc(PL_STATES[st] || st)}</span>`; }

// ---------------------------------------------------------------- draw
function plDraw() {
  if (!PL.data) return;
  const r = plRange(), vis = plVisible();
  const inRange = new Set(r.days), dated = vis.filter((i) => i.z), undated = vis.filter((i) => !i.z);
  const byDay = {};
  for (const i of dated) (byDay[i.z.ymd] ||= []).push(i);
  for (const k in byDay) byDay[k].sort((a, b) => new Date(a.when) - new Date(b.when));
  const count = PL.view === "grid" ? plGridItems(vis).length
    : dated.filter((i) => i.kind !== "ghost" && inRange.has(i.z.ymd) && (PL.view !== "month" || i.z.ymd.startsWith(r.month))).length
      + (PL.view === "board" ? undated.filter((i) => i.kind !== "ghost").length : 0);
  $("#plTitle").textContent = plTitle(r);
  $("#plCount").textContent = `${count} post${count === 1 ? "" : "s"}`;
  $("#plTzNote").textContent = PL.tz.replace(/_/g, " ");
  const main = $("#plMain"), old = $(".pl-week-scroll"), key = PL.view + "|" + r.days[0];
  // keep the user's scroll only on a redraw of the same visible week/day; a hidden scroller reads 0 (it opened at 00:00)
  const keep = old && old.offsetParent !== null && PL.drawKey === key ? old.scrollTop : undefined;
  PL.drawKey = key;
  main.dataset.view = PL.view;
  if (PL.view === "list") main.innerHTML = plList(r, byDay);
  else if (PL.view === "week") main.innerHTML = plPhone() ? plAgenda(r, byDay) : plWeek(r, byDay);
  else if (PL.view === "day") main.innerHTML = plPhone() ? plAgenda(r, byDay) : plDay(r, byDay);
  else if (PL.view === "board") main.innerHTML = plBoard(r, dated, undated);
  else if (PL.view === "grid") main.innerHTML = plGrid(vis);
  else main.innerHTML = plMonth(r, byDay);
  $("#tab-planner .pl-nav").classList.toggle("off", PL.view === "grid");
  main.dataset.phone = plPhone() ? "1" : "";
  // No date sidebar
  const side = $("#plNoDate");
  side.hidden = !PL.nodate; $("#plBody").classList.toggle("with-side", PL.nodate);
  if (PL.nodate) {
    side.innerHTML = `<div class="pl-side-head"><b>No date</b><span class="pill sm" data-testid="planner-nodate-count">${undated.length}</span></div>
      <p class="muted xs">Drag a draft onto the calendar to give it a time.</p>
      ${undated.length ? undated.map((i) => plCard(i, "side")).join("") : `<p class="pl-side-empty" data-testid="planner-nodate-empty">Nothing without a date. Drafts you save without a time land here.</p>`}`;
  }
  plWire();
  const sc = $(".pl-week-scroll");
  if (sc) {
    const days = PL.view === "day" ? [r.days[0]] : r.days;  // Day and Week open at the first post of the range
    const first = Math.min(...days.flatMap((d) => (byDay[d] || []).filter((i) => i.kind !== "ghost").map((i) => i.z.h)), 24);
    const top = keep ?? (first < 24 ? Math.max(0, first - 1) : 7) * ROW_H;
    sc.scrollTop = top;
    if (sc.offsetParent === null && "IntersectionObserver" in window) { // drawn while hidden: scroll once it shows
      const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { sc.scrollTop = top; io.disconnect(); } });
      io.observe(sc);
    }
  }
}
function plEmpty() {
  return `<div class="pl-empty" data-testid="planner-empty"><svg class="ic lg" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>
    <b>No posts match these filters</b><span>Try another channel, type or date range, or schedule something new.</span>
    <div class="row"><button class="btn sm" type="button" data-testid="planner-empty-create" data-act="new">Create post</button>${plFiltersActive() ? `<button class="btn2 sm" type="button" data-testid="planner-empty-clear" data-act="clear">Clear filters</button>` : ""}</div></div>`;
}
function plThumb(i) {
  if (i.thumb) return `<img src="${esc(i.thumb)}" alt="" loading="lazy">`;
  const g = { video: '<path d="M8 6l10 6-10 6z"/>', reel: '<path d="M8 6l10 6-10 6z"/>', carousel: '<rect x="4" y="6" width="12" height="12" rx="2"/><path d="M19 8v8"/>', image: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 16l5-5 4 4 3-3 4 4"/>' }[i.type] || '<path d="M6 7h12M6 12h12M6 17h7"/>';
  return `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${g}</svg>`;
}
function plWhen(i) { return i.z ? timeLabel(i.z.h, i.z.mi) : "No date"; }
// a compact chip for the week and month grids
function plChip(i) {
  return `<button type="button" class="pl-chip s-${esc(i.state)}${i.draggable ? "" : " locked"}" data-testid="planner-item" data-ref="${esc(i.ref)}" data-state="${esc(i.state)}" data-ch="${esc(i.channel_id)}" ${i.draggable ? ' draggable="true"' : ''}
    title="${esc(PL_STATES[i.state] + " · " + (i.channel || "") + " · " + netName(i.service) + "\n" + i.text)}">${netMark(i.service, i.channel_id, true)}<span class="t">${esc(plWhen(i))}</span><span class="c">${esc(i.text || "(media)")}</span></button>`;
}
// a full card for the list view and the No date sidebar
function plCard(i, where) {
  const acts = [];
  if (i.kind === "buffer" && i.state !== "sent") acts.push(`<button type="button" class="link" data-testid="planner-edit" data-act="edit" data-ref="${esc(i.ref)}">Edit</button>`,
    `<button type="button" class="link" data-testid="planner-reschedule" data-act="reschedule" data-ref="${esc(i.ref)}">Reschedule</button>`);
  if (i.kind === "approval") acts.push(`<button type="button" class="link" data-testid="planner-reschedule" data-act="reschedule" data-ref="${esc(i.ref)}">${i.when ? "Reschedule" : "Set time"}</button>`,
    `<button type="button" class="btn accent xs" data-testid="planner-approve" data-act="approve" data-ref="${esc(i.ref)}">Approve</button>`);
  if (i.kind === "ready") acts.push(`<button type="button" class="link" data-testid="planner-open-run" data-act="run" data-ref="${esc(i.ref)}">Open</button>`,
    `<button type="button" class="link" data-testid="planner-reschedule" data-act="reschedule" data-ref="${esc(i.ref)}">Schedule</button>`);
  if (i.state === "draft") acts.push(`<button type="button" class="link danger" data-testid="planner-delete" data-act="delete" data-ref="${esc(i.ref)}">Delete draft</button>`);
  if (i.state === "sent" && i.link) acts.push(`<a class="link" href="${esc(i.link)}" target="_blank" rel="noopener" data-testid="planner-view-post">View post</a>`);
  return `<article class="pl-card s-${esc(i.state)}${where === "side" ? " in-side" : ""}" data-testid="${where === "side" ? "planner-nodate-item" : "planner-card"}" data-ref="${esc(i.ref)}" data-ch="${esc(i.channel_id)}" data-state="${esc(i.state)}" ${i.draggable ? 'draggable="true"' : ""}>
    <span class="pl-thumb">${plThumb(i)}</span>
    <div class="pl-card-body"><div class="pl-meta">${netMark(i.service, i.channel_id)}<b>${esc(i.channel || netName(i.service))}</b><span class="muted nw">${esc(netName(i.service))}</span><span class="pl-time">${esc(plWhen(i))}</span></div>
      <p class="pl-cap">${esc(i.text || "(media only)")}</p>${i.error ? `<p class="pl-err" data-testid="planner-error">${esc(i.error)}</p>` : ""}
      <div class="pl-foot">${stateChip(i.state)}${i.agent ? `<span class="muted xs">by ${esc(i.agent)}</span>` : ""}<span class="pl-acts">${acts.join("")}</span></div></div></article>`;
}
function plList(r, byDay) {
  const days = r.days.filter((d) => (byDay[d] || []).length);
  if (!days.length) return plEmpty();
  const t = todayYmd();
  return `<div class="pl-list" data-testid="planner-list">${days.map((d) => `<section class="pl-day" data-day="${d}"><h3><span>${d === t ? "Today" : esc(dayLabel(d, { weekday: "long" }))}</span><span class="muted">${esc(dayLabel(d, { day: "numeric", month: "long" }))}</span><span class="pill sm" data-testid="planner-day-count">${byDay[d].length}</span></h3>
    <div class="pl-cards" data-testid="planner-list-day" data-day="${d}">${byDay[d].map((i) => plCard(i)).join("")}</div></section>`).join("")}</div>`;
}
// phone: the week as an agenda, every day listed (empty ones too) with its open posting times as tap-to-create buttons
function plAgenda(r, byDay) {
  const t = todayYmd(), slots = plSlotMap(r.days);
  return `<div class="pl-list pl-agenda" data-testid="planner-agenda">${r.days.map((d) => {
    const items = byDay[d] || [];
    const open = Object.entries(slots).filter(([k]) => k.startsWith(d + "|")).flatMap(([, v]) => v)
      .filter((s) => !items.some((i) => i.channel_id === s.ch && i.z.h === s.h && i.z.mi === s.mi)).sort((a, b) => a.h - b.h || a.mi - b.mi).slice(0, 4);
    return `<section class="pl-day" data-day="${d}"><h3><span>${d === t ? "Today" : esc(dayLabel(d, { weekday: "long" }))}</span><span class="muted">${esc(dayLabel(d, { day: "numeric", month: "long" }))}</span><span class="pill sm" data-testid="planner-day-count">${items.length}</span></h3>
      <div class="pl-cards" data-testid="planner-list-day" data-day="${d}">${items.map((i) => plCard(i)).join("")}${items.length ? "" : `<p class="muted sm pl-agenda-empty">Nothing planned.</p>`}</div>
      ${open.length ? `<div class="pl-open">${open.map((s) => `<button type="button" class="pl-slotmark" data-testid="planner-posting-time" data-day="${d}" data-hour="${s.h}" data-min="${s.mi}" data-ch="${esc(s.ch)}" aria-label="New post at ${esc(timeLabel(s.h, s.mi))}">${netMark(s.service)}${esc(timeLabel(s.h, s.mi))}</button>`).join("")}</div>` : ""}</section>`;
  }).join("")}</div>`;
}
function plSlotMap(days) { // Buffer posting slots per channel, shown in the planner's timezone: "ymd|hour" -> [{time, service}]
  const m = {};
  if (!PL.slots) return m;
  const chans = Object.fromEntries(S.channels.map((c) => [c.id, c]));
  for (const s of PL.data.slots || []) {
    if (PL.ch.size && !PL.ch.has(s.channel_id)) continue;
    for (const day of [addDays(days[0], -1), ...days, addDays(days[days.length - 1], 1)]) {
      const sch = (s.schedule || []).find((x) => x.day === DOW[dowOf(day)]);
      if (!sch || sch.paused) continue;
      for (const tm of sch.times || []) {
        const [hh, mm] = tm.split(":").map(Number), inst = fromZoned(day, hh, mm, s.timezone);
        if (inst < Date.now()) continue; // only upcoming posting times
        const z = zoned(inst, PL.tz);
        (m[`${z.ymd}|${z.h}`] ||= []).push({ h: z.h, mi: z.mi, service: chans[s.channel_id]?.service || "", ch: s.channel_id });
      }
    }
  }
  return m;
}
function plWeek(r, byDay) {
  const t = todayYmd(), now = zoned(new Date(), PL.tz), slots = plSlotMap(r.days);
  let h = `<div class="pl-week-scroll" data-testid="planner-week"><div class="pl-week" role="grid" aria-label="Week">
    <div class="pl-corner" aria-hidden="true">${esc(PL.tz.split("/").pop().replace(/_/g, " "))}</div>`;
  for (const d of r.days) {
    const n = (byDay[d] || []).length;
    h += `<div class="pl-wh${d === t ? " today" : ""}" role="columnheader"><span class="dw">${esc(dayLabel(d, { weekday: "short" }))}</span><span class="dn">${+d.slice(8)}</span><span class="dc" data-testid="planner-day-count">${n ? `${n} post${n === 1 ? "" : "s"}` : "&nbsp;"}</span></div>`;
  }
  for (let hr = 0; hr < 24; hr++) {
    h += `<div class="pl-hr" aria-hidden="true">${esc(timeLabel(hr, 0))}</div>`;
    for (const d of r.days) {
      const items = (byDay[d] || []).filter((i) => i.z.h === hr), sl = slots[`${d}|${hr}`] || [];
      const nowLine = d === t && now.h === hr ? `<span class="pl-now" style="top:${(now.mi / 60) * 100}%" aria-hidden="true"></span>` : "";
      h += `<div class="pl-cell${d === t ? " today" : ""}" role="gridcell" data-testid="planner-slot" data-day="${d}" data-hour="${hr}" aria-label="${esc(dayLabel(d, { weekday: "long", day: "numeric", month: "long" }) + " " + timeLabel(hr, 0))}">${nowLine}`
        + items.map(plChip).join("")
        + (items.length ? "" : sl.slice(0, 2).map((s) => `<span class="pl-slotmark" data-testid="planner-posting-time" data-day="${d}" data-hour="${s.h}" data-min="${s.mi}" data-ch="${esc(s.ch)}">${netMark(s.service)}${esc(timeLabel(s.h, s.mi))}</span>`).join(""))
        + `</div>`;
    }
  }
  return h + `</div></div>`;
}
function plMonth(r, byDay) {
  const t = todayYmd(), MAX = 3;
  let h = `<div class="pl-month" data-testid="planner-month" role="grid" aria-label="Month"><div class="pl-dow">${DOW.map((d) => `<span>${esc(dayLabel(addDays(mondayOf("2026-01-05"), DOW.indexOf(d)), { weekday: "short" }))}</span>`).join("")}</div><div class="pl-days">`;
  for (const d of r.days) {
    const items = byDay[d] || [];
    h += `<div class="pl-mday${d.startsWith(r.month) ? "" : " out"}${d === t ? " today" : ""}" role="gridcell" data-testid="planner-day" data-day="${d}" aria-label="${esc(dayLabel(d, { weekday: "long", day: "numeric", month: "long" }))}">
      <span class="dn">${+d.slice(8)}</span>${items.slice(0, MAX).map(plChip).join("")}${items.length > MAX ? `<button type="button" class="pl-more" data-testid="planner-more" data-act="more" data-day="${d}">+${items.length - MAX} more</button>` : ""}</div>`;
  }
  return h + `</div></div>`;
}

// Day: one lane per channel, the same hour rows as the week (drop on a lane cell to move, click an empty cell to create)
function plDay(r, byDay) {
  const d = r.days[0], t = todayYmd(), now = zoned(new Date(), PL.tz), slots = plSlotMap(r.days);
  const chans = S.channels.filter((c) => !c.isDisconnected && (!PL.ch.size || PL.ch.has(c.id)));
  if (!chans.length) return plEmpty();
  const items = byDay[d] || [];
  let h = `<div class="pl-week-scroll pl-day-scroll" data-testid="planner-day-view"><div class="pl-week pl-dayv" role="grid" aria-label="${esc(dayLabel(d, { weekday: "long", day: "numeric", month: "long" }))}" style="--lanes:${chans.length}">
    <div class="pl-corner" aria-hidden="true">${esc(PL.tz.split("/").pop().replace(/_/g, " "))}</div>`;
  for (const c of chans) {
    const n = items.filter((i) => i.channel_id === c.id).length;
    h += `<div class="pl-wh${d === t ? " today" : ""}" role="columnheader">${netMark(c.service, c.id, true)}<span class="dw">${esc(c.displayName || c.name)}</span><span class="dc" data-testid="planner-day-count">${n ? `${n} post${n === 1 ? "" : "s"}` : "&nbsp;"}</span></div>`;
  }
  for (let hr = 0; hr < 24; hr++) {
    h += `<div class="pl-hr" aria-hidden="true">${esc(timeLabel(hr, 0))}</div>`;
    for (const c of chans) {
      const its = items.filter((i) => i.channel_id === c.id && i.z.h === hr), sl = (slots[`${d}|${hr}`] || []).filter((x) => x.ch === c.id);
      const nowLine = d === t && now.h === hr ? `<span class="pl-now" style="top:${(now.mi / 60) * 100}%" aria-hidden="true"></span>` : "";
      h += `<div class="pl-cell${d === t ? " today" : ""}" role="gridcell" data-testid="planner-slot" data-day="${d}" data-hour="${hr}" data-lane="${esc(c.id)}" aria-label="${esc((c.displayName || c.name) + ", " + timeLabel(hr, 0))}">${nowLine}`
        + its.map(plChip).join("")
        + (its.length ? "" : sl.slice(0, 1).map((x) => `<span class="pl-slotmark" data-testid="planner-posting-time" data-day="${d}" data-hour="${x.h}" data-min="${x.mi}" data-ch="${esc(x.ch)}">${esc(timeLabel(x.h, x.mi))}</span>`).join(""))
        + `</div>`;
    }
  }
  return h + `</div></div>`;
}
// Board: one column per status. Status is Buffer's, so only a draft, a ready package or a post waiting for approval can be
// dropped on "Scheduled in Buffer" (it opens the schedule dialog); Sent and Failed are set by Buffer alone.
function plBoard(r, dated, undated) {
  const inRange = new Set(r.days), cards = [...dated.filter((i) => inRange.has(i.z.ymd)), ...undated].filter((i) => i.kind !== "ghost");
  if (!cards.length) return plEmpty();
  return `<div class="pl-board" data-testid="planner-board">${PL_BOARD.map(([st, name]) => {
    const col = cards.filter((i) => i.state === st).sort((a, b) => (a.when ? new Date(a.when) : 9e15) - (b.when ? new Date(b.when) : 9e15));
    return `<section class="pl-col s-${st}" data-testid="planner-board-col" data-col="${st}" aria-label="${esc(name)}, ${col.length}">
      <h3><span class="pl-dot s-${st}" aria-hidden="true"></span>${esc(name)}<span class="pill sm">${col.length}</span></h3>
      <div class="pl-col-body" data-drop="${st}">${col.map((i) => plCard(i)).join("") || `<p class="muted xs pl-col-empty">Nothing here.</p>`}</div></section>`;
  }).join("")}</div>`;
}
// Grid: how the Instagram feed will look. Newest first, planned posts dashed with their time, sent posts solid.
function plGridItems(vis) {
  const ig = new Set(S.channels.filter((c) => c.service === "instagram").map((c) => c.id));
  return vis.filter((i) => ig.has(i.channel_id) && i.kind !== "ghost" && i.state !== "failed").sort((a, b) => (b.when ? new Date(b.when) : 9e15) - (a.when ? new Date(a.when) : 9e15));
}
function plGrid(vis) {
  const its = plGridItems(vis);
  if (!S.channels.some((c) => c.service === "instagram")) return `<div class="pl-empty" data-testid="planner-empty"><b>No Instagram channel</b><span>The grid previews an Instagram feed. Connect one in Buffer, then refresh channels.</span></div>`;
  if (!its.length) return plEmpty();
  return `<div class="pl-grid-wrap"><div class="pl-grid" data-testid="planner-grid" role="list" aria-label="Instagram feed preview, newest first">${its.map((i) => `<button type="button" role="listitem" class="pl-tile${i.state === "sent" ? "" : " planned"} s-${esc(i.state)}" data-testid="planner-grid-tile" data-ref="${esc(i.ref)}" aria-label="${esc((PL_STATES[i.state] || i.state) + ", " + plWhenFull(i) + ": " + (i.text || "media"))}">
      <span class="pl-tile-txt">${esc((i.text || "").slice(0, 90))}</span>${i.thumb ? `<img src="${esc(i.thumb)}" alt="" loading="lazy">` : ""}
      <span class="pl-tile-meta">${i.state === "sent" ? "Sent" : esc(plWhenFull(i))}</span></button>`).join("")}</div>
    <p class="muted xs">Dashed tiles are planned; solid tiles are already on the feed. Click a tile to open it.</p></div>`;
}
function plWhenFull(i) { return i.z ? `${dayLabel(i.z.ymd, { weekday: "short", day: "numeric", month: "short" })}, ${timeLabel(i.z.h, i.z.mi)}` : "No date"; }
// for the palette and the 1-6 keys
function plSetView(v) {
  if (!PL_VIEWS.includes(v)) return;
  if (!PL.tz) plLoadState();
  PL.view = v;
  if ($("#tab-planner").hidden) { plSaveState(); showTab("planner"); } else plChanged();
}
// plSetView and plAct are top-level functions, so the palette and keys already reach them as window globals

// ---------------------------------------------------------------- interactions
function plFind(ref) { return [...(PL.data?.items || []), ...PL.extra].find((i) => i.ref === ref); }
function plWire() {
  const root = $("#tab-planner");
  root.querySelectorAll("#plMain [data-act], #plNoDate [data-act]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); plAct(b.dataset.act, b.dataset.ref, b); }));
  root.querySelectorAll(".pl-chip,.pl-tile").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); plOpenItem(plFind(b.dataset.ref)); }));
  root.querySelectorAll("[data-drop]").forEach((col) => {
    col.ondragover = (e) => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = (e) => { e.preventDefault(); col.classList.remove("over"); plBoardDrop(e.dataTransfer.getData("text/plain"), col.dataset.drop); };
  });
  root.querySelectorAll("[draggable=true]").forEach((el) => {
    el.ondragstart = (e) => { e.dataTransfer.setData("text/plain", el.dataset.ref); e.dataTransfer.effectAllowed = "move"; el.classList.add("dragging"); document.body.classList.add("pl-dragging"); };
    el.ondragend = () => { el.classList.remove("dragging"); document.body.classList.remove("pl-dragging"); };
  });
  root.querySelectorAll(".pl-agenda .pl-slotmark").forEach((b) => (b.onclick = () => plCompose(b.dataset.day, +b.dataset.hour, +b.dataset.min, b.dataset.ch)));
  root.querySelectorAll("[data-testid=planner-slot],[data-testid=planner-day]").forEach((cell) => {
    cell.ondragover = (e) => { e.preventDefault(); cell.classList.add("over"); };
    cell.ondragleave = () => cell.classList.remove("over");
    cell.ondrop = (e) => { e.preventDefault(); cell.classList.remove("over"); plMove(e.dataTransfer.getData("text/plain"), cell.dataset.day, cell.dataset.hour); };
    cell.onclick = (e) => {
      const mark = e.target.closest(".pl-slotmark");
      if (mark) return plCompose(mark.dataset.day, +mark.dataset.hour, +mark.dataset.min, mark.dataset.ch);
      if (plPhone() && cell.dataset.testid === "planner-day") return plPopover(cell.dataset.day, cell);
      if (e.target === cell || e.target.classList.contains("dn")) plCompose(cell.dataset.day, cell.dataset.hour !== undefined ? +cell.dataset.hour : 9, 0, cell.dataset.lane);
    };
  });
}
function plOpenItem(i) {
  if (!i) return;
  if (i.kind === "ghost") return toast("A planned clip. Save the plan below to create it as a Buffer draft.");
  if (i.kind === "ready") return plAct("run", i.ref);
  if (i.kind === "approval") return plDialog("reschedule", i);
  if (i.state === "sent") { if (i.link) window.open(i.link, "_blank", "noopener"); else toast("Sent by Buffer. Sent posts can't be moved."); return; }
  plDialog("edit", i);
}
async function plAct(act, ref, el) {
  const i = plFind(ref);
  if (act === "new") return plCompose(PL.date || todayYmd(), 9, 0);
  if (act === "clear") { PL.ch.clear(); PL.type = PL.status = PL.q = ""; $("#plSearch").value = ""; return plChanged(); }
  if (act === "more") return plPopover(el.dataset.day, el);
  if (!i) return;
  if (act === "edit") return plDialog("edit", i);
  if (act === "reschedule") return plDialog("reschedule", i);
  if (act === "run") { showTab("agent"); const r = await api(`/api/agent/runs/${i.run_id}`); showRun(r); return; }
  if (act === "approve") {
    try { await api(`/api/approvals/${i.id}/approve`, { method: "POST", body: {} }); toast(i.when ? "Approved. Scheduled in Buffer." : "Approved and added to your Buffer queue"); refreshBadge(); loadPlanner(); }
    catch (e) { toast(e.message, 6000); }
    return;
  }
  if (act === "delete") {
    if (!(await hqConfirm("Delete this draft from Buffer? This can't be undone.", { ok: "Delete draft", danger: true }))) return;
    try { await api(`/api/posts/${encodeURIComponent(i.id)}`, { method: "DELETE" }); toast("Draft deleted"); loadPlanner(); }
    catch (e) { toast(e.message, 6000); }
  }
}
// the one move path for drag-and-drop and the reschedule dialog
async function plReschedule(i, instant) {
  if (instant <= new Date()) { toast("Pick a time in the future"); return false; }
  const due_at = instant.toISOString();
  try {
    if (i.kind === "buffer") await api(`/api/posts/${encodeURIComponent(i.id)}/reschedule`, { method: "POST", body: { due_at } });
    else if (i.kind === "approval") await api(`/api/approvals/${i.id}/reschedule`, { method: "POST", body: { due_at } });
    else if (i.kind === "ready") {
      const out = await api(`/api/agent/runs/${i.run_id}/packages/${encodeURIComponent(i.channel_id)}/schedule`, { method: "POST", body: { mode: "schedule", due_at } });
      if (out.status === "pending_approval") { toast("Waiting for approval"); loadPlanner(); return true; }
    }
    const z = zoned(instant, PL.tz);
    toast(`${i.state === "draft" ? "Draft moved" : i.kind === "ready" ? "Scheduled in Buffer" : "Rescheduled"} to ${dayLabel(z.ymd, { weekday: "short", day: "numeric", month: "short" })}, ${timeLabel(z.h, z.mi)}`);
    loadPlanner(); return true;
  } catch (e) { toast(e.message, 6000); loadPlanner(); return false; }
}
function plMove(ref, day, hour) {
  const i = plFind(ref);
  if (!i || !day) return;
  if (!i.draggable) return toast("Sent posts can't be moved");
  const hh = hour !== undefined ? +hour : i.z ? i.z.h : 9, mm = i.z ? i.z.mi : 0;
  // optimistic: show it in the new place straight away
  const inst = fromZoned(day, hh, mm, PL.tz);
  if (inst <= new Date()) return toast("Pick a time in the future");
  i.when = inst.toISOString(); i.z = zoned(inst, PL.tz); plDraw();
  plReschedule(i, inst);
}
function plBoardDrop(ref, col) {
  const i = plFind(ref);
  if (!i || i.state === col) return;
  if (col === "sent" || col === "failed") return toast("Only Buffer marks a post sent or failed");
  if (col === "scheduled" && ["draft", "ready", "approval"].includes(i.state)) return plDialog(i.kind === "buffer" ? "edit" : "reschedule", i);
  if (col === "approval") return toast("Agent posts land here by themselves; a post you write goes straight to Buffer");
  toast("Change it from the card: approve, schedule or edit");
}
function plPopover(day, anchor) {
  const pop = $("#plPop"), items = plVisible().filter((i) => i.z && i.z.ymd === day).sort((a, b) => new Date(a.when) - new Date(b.when));
  pop.innerHTML = `<div class="pl-pop-head"><b>${esc(dayLabel(day, { weekday: "long", day: "numeric", month: "long" }))}</b><button type="button" class="link" data-testid="planner-popover-close" aria-label="Close">Close</button></div>
    <div class="pl-pop-list">${items.map(plChip).join("")}</div>`;
  const r = anchor.getBoundingClientRect();
  pop.hidden = false;
  pop.style.top = Math.min(window.innerHeight - pop.offsetHeight - 12, Math.max(12, r.top - 40)) + "px";
  pop.style.left = Math.min(window.innerWidth - pop.offsetWidth - 12, Math.max(12, r.left - 40)) + "px";
  pop.querySelector("[data-testid=planner-popover-close]").onclick = () => (pop.hidden = true);
  pop.querySelectorAll(".pl-chip").forEach((b) => (b.onclick = () => { pop.hidden = true; plOpenItem(plFind(b.dataset.ref)); }));
  pop.querySelectorAll("[draggable=true]").forEach((el) => (el.ondragstart = (e) => { e.dataTransfer.setData("text/plain", el.dataset.ref); setTimeout(() => (pop.hidden = true), 0); }));
  pop.querySelector("[data-testid=planner-popover-close]").focus();
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#plPop").hidden) $("#plPop").hidden = true; });
document.addEventListener("click", (e) => { const p = $("#plPop"); if (!p.hidden && !p.contains(e.target) && !e.target.closest("[data-act=more]")) p.hidden = true; });

// ---------------------------------------------------------------- dialogs: new post, edit, reschedule
const toLocalInput = (inst) => { const z = zoned(inst, PL.tz); return `${z.ymd}T${pad2(z.h)}:${pad2(z.mi)}`; };
const fromLocalInput = (v) => { const [d, t] = v.split("T"); const [h, m] = t.split(":").map(Number); return fromZoned(d, h, m, PL.tz); };
function plCompose(day, hh, mm, ch) {
  let inst = fromZoned(day, hh, mm || 0, PL.tz);
  if (inst <= new Date()) { const n = new Date(Date.now() + 3600e3); n.setMinutes(0, 0, 0); inst = n; }
  plDialog("new", { when: inst.toISOString(), channel_id: ch || [...PL.ch][0] || "" });
}
function plDialog(mode, i) {
  const dlg = $("#plDialog"); PL.edit = { mode, i };
  const isNew = mode === "new", resched = mode === "reschedule";
  $("#plDlgTitle").textContent = isNew ? "New post" : resched ? (i.kind === "ready" ? "Schedule in Buffer" : "Reschedule") : "Edit post";
  $("#plDlgCh").hidden = !isNew; $("#plDlgMediaF").hidden = !isNew; $("#plDlgAgent").hidden = !isNew;
  $("#plDlgTextF").hidden = resched;
  $("#plDlgChSel").innerHTML = S.channels.filter((c) => !c.isDisconnected).map((c) => `<option value="${esc(c.id)}">${esc((c.displayName || c.name) + " · " + netName(c.service))}</option>`).join("");
  if (isNew && i.channel_id) $("#plDlgChSel").value = i.channel_id;
  $("#plDlgText").value = isNew ? "" : i.text || "";
  $("#plDlgMedia").value = "";
  const start = i.when ? new Date(i.when) : (() => { const n = new Date(Date.now() + 3600e3); n.setMinutes(0, 0, 0); return n; })();
  $("#plDlgWhen").value = toLocalInput(start);
  $("#plDlgTz").textContent = PL.tz.replace(/_/g, " ");
  $("#plDlgSave").textContent = isNew ? "Schedule in Buffer" : resched ? (i.kind === "ready" ? "Schedule" : "Move") : "Save changes";
  $("#plDlgDraft").hidden = !isNew;
  $("#plDlgNote").textContent = i.kind === "approval" ? "It stays in Approvals until you approve it, then goes out at this time." : i.state === "draft" ? "It stays a draft in Buffer, with this time." : "";
  $("#plDlgErr").textContent = "";
  $("#plDlgPf").hidden = resched || !(isNew || i.kind === "buffer");
  if (!$("#plDlgPf").hidden) { PL.pf.run(true); } else { $("#plDlgPf").innerHTML = ""; }
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  (resched ? $("#plDlgWhen") : isNew ? $("#plDlgChSel") : $("#plDlgText")).focus();
}
// preflight in the dialog: the same server check as the composer, REST and MCP
PL.pf = pfPanel($("#plDlgPf"), () => {
  const { mode, i } = PL.edit || {};
  if (!mode || mode === "reschedule") return null;
  if (mode === "new") return { channel_id: $("#plDlgChSel").value, text: $("#plDlgText").value, media_url: $("#plDlgMedia").value.trim(), mode: "schedule" };
  return { channel_id: i.channel_id, text: $("#plDlgText").value, mode: i.state === "draft" ? "draft" : "queue", editing: true };
}, (r) => { $("#plDlgText").value = r.text; if (PL.edit?.mode === "new") $("#plDlgMedia").value = r.media_url || ""; });
["#plDlgText", "#plDlgMedia"].forEach((s) => $(s).addEventListener("input", () => PL.pf.run()));
$("#plDlgChSel").addEventListener("change", () => PL.pf.run(true));
async function plDialogSave(asDraft) {
  const { mode, i } = PL.edit || {}, err = $("#plDlgErr"), btn = $("#plDlgSave");
  if (mode === "new" || (mode === "edit" && $("#plDlgText").value !== i.text)) {
    await PL.pf.settle();
    const why = PL.pf.blocked();
    if (why) { err.textContent = why; return; }
  }
  const ov = PL.pf.override();
  const whenV = $("#plDlgWhen").value;
  if (!whenV && !asDraft) { err.textContent = "Pick a date and time"; return; }
  const inst = whenV ? fromLocalInput(whenV) : null;
  btn.disabled = true; err.textContent = "";
  try {
    if (mode === "new") {
      const body = { channel_id: $("#plDlgChSel").value, text: $("#plDlgText").value, media_url: $("#plDlgMedia").value.trim(), mode: asDraft ? "draft" : "schedule" };
      if (ov) body.override_warnings = true;
      if (!asDraft) body.due_at = inst.toISOString();
      const pv = await api("/api/posts/preview", { method: "POST", body });
      await api("/api/posts/submit", { method: "POST", body: { confirm_token: pv.confirm_token } });
      toast((asDraft ? "Saved as a draft in Buffer" : "Scheduled in Buffer") + (pv.warnings.length ? " · " + pv.warnings[0] : ""), 4500);
    } else if (mode === "edit") {
      const body = {};
      if ($("#plDlgText").value !== i.text) body.text = $("#plDlgText").value;
      if (whenV && (!i.when || Math.abs(inst - new Date(i.when)) >= 60000)) body.due_at = inst.toISOString();
      if (!Object.keys(body).length) { closeDlg(); return; }
      if (ov) body.override_warnings = true;
      await api(`/api/posts/${encodeURIComponent(i.id)}`, { method: "PATCH", body });
      toast("Saved in Buffer");
    } else {
      if (!(await plReschedule(i, inst))) { btn.disabled = false; return; }
    }
    closeDlg(); loadPlanner(); budget();
  } catch (e) { err.textContent = e.message; }
  finally { btn.disabled = false; }
}
function closeDlg() { const d = $("#plDialog"); if (typeof d.close === "function" && d.open) d.close(); else d.removeAttribute("open"); }

// ---------------------------------------------------------------- static wiring
document.querySelectorAll("#tab-planner [data-view]").forEach((b) => (b.onclick = () => { PL.view = b.dataset.view; plChanged(); }));
$("#plPrev").onclick = () => plShift(-1); $("#plNext").onclick = () => plShift(1);
$("#plToday").onclick = () => { PL.date = ""; plChanged(); };
function plShift(n) {
  const a = PL.date || todayYmd();
  if (PL.view === "grid") return;
  if (PL.view === "month") { const [y, m] = a.split("-").map(Number); PL.date = ymdUTC(Date.UTC(y, m - 1 + n, 1)); }
  else PL.date = addDays(a, (PL.view === "list" || PL.view === "board" ? 28 : PL.view === "day" ? 1 : 7) * n);
  plChanged();
}
$("#plType").onchange = (e) => { PL.type = e.target.value; plChanged(); };
$("#plStatus").onchange = (e) => { PL.status = e.target.value; plChanged(); };
let plQT; $("#plSearch").oninput = (e) => { clearTimeout(plQT); plQT = setTimeout(() => { PL.q = e.target.value; plChanged(); }, 160); };
$("#plTz").onchange = (e) => { PL.tz = e.target.value; for (const it of PL.data?.items || []) it.z = it.when ? zoned(new Date(it.when), PL.tz) : null; plChanged(); };
$("#plNoDateT").onchange = (e) => { PL.nodate = e.target.checked; plChanged(); };
$("#plSlotsT").onchange = (e) => { PL.slots = e.target.checked; plChanged(); };
$("#plClear").onclick = () => plAct("clear");
$("#plNew").onclick = () => plAct("new");
$("#plDlgForm").addEventListener("submit", (e) => { e.preventDefault(); plDialogSave(false); });
$("#plDlgDraft").onclick = () => plDialogSave(true);
$("#plDlgCancel").onclick = closeDlg;
$("#plDlgAgent").onclick = () => { closeDlg(); showTab("agent"); };
window.HS_TABS = Object.assign(window.HS_TABS || {}, { planner: loadPlanner });
let plWasPhone = plPhone();
addEventListener("resize", () => { const p = plPhone(); if (p !== plWasPhone) { plWasPhone = p; if (!$("#tab-planner").hidden) plDraw(); } });

// a feed tile whose image can't load shows its caption instead (no inline handlers under the CSP)
document.addEventListener("error", (e) => { const t = e.target; if (t.tagName === "IMG" && t.closest(".pl-tile")) t.remove(); }, true);
