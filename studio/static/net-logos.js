// Social network marks for posts and channels: one helper for the Inbox, Planner (week, month, day, list, board, grid),
// onboarding and approvals, so every screen shows the same LinkedIn/Instagram/X/... icon. Marks come from
// net-logos-data.js (simple-icons 16.36.0, CC0; built by scripts/build_net_logos.py). LinkedIn is not in simple-icons, so
// it is an "in" monogram on LinkedIn blue; anything unknown is a two-letter monogram. Loaded before app.js and planner.js.
(function () {
  const MONO = { linkedin: ["in", "#0A66C2", "LinkedIn"] };
  const NAMES = { twitter: "X", linkedin: "LinkedIn", instagram: "Instagram", bluesky: "Bluesky", threads: "Threads", mastodon: "Mastodon",
    facebook: "Facebook", tiktok: "TikTok", youtube: "YouTube", pinterest: "Pinterest", googlebusiness: "Google Business" };
  const e = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeSvc = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  // A square tile in the network's colour with the mark in white. `label` gives it a name for screen readers; without it
  // the tile is decorative (the channel name or "on LinkedIn" text next to it already says it).
  function netIcon(svc, label) {
    const k = safeSvc(svc), d = (window.HQ_NET_DATA || {})[k], m = MONO[k];
    const name = (d && d.label) || (m && m[2]) || NAMES[k] || svc || "Unknown network";
    const a11y = label ? `role="img" aria-label="${e(name)}"` : `aria-hidden="true"`;
    if (d) return `<span class="net net-logo svc-${k}" data-net="${k}" ${a11y} title="${e(name)}" style="--net:${e(d.hex)}"><svg viewBox="${e(d.vb)}" focusable="false" aria-hidden="true">${d.svg}</svg></span>`;
    const txt = m ? m[0] : (k || "?").slice(0, 2);
    return `<span class="net net-mono svc-${k}" data-net="${k}" ${a11y} title="${e(name)}"${m ? ` style="--net:${m[1]}"` : ""}>${e(txt)}</span>`;
  }
  // The channel's avatar with its network mark on the corner when Buffer gave us an avatar; otherwise just the mark.
  function chMark(ch, label) {
    const c = ch || {};
    if (!c.avatar || !/^https:\/\//.test(c.avatar)) return netIcon(c.service, label);
    return `<span class="chmark" data-testid="channel-mark"><img class="chmark-av" src="${e(c.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer">${netIcon(c.service, label)}</span>`;
  }
  window.netIcon = netIcon;
  window.chMark = chMark;
})();
