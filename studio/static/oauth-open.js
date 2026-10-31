// Connect Buffer from anywhere, painless on phones. Buffer's sign-in can fail inside in-app browsers (Instagram, Facebook,
// TikTok, X, LinkedIn, Line, Snapchat, Android WebView), so a tap on any /login link there opens a short sheet that hands
// the sign-in to the system browser (Chrome by intent on Android, Safari on iOS) or copies the link. OAuth is always a
// top-level page, never an iframe (the app also sends X-Frame-Options: DENY and frame-ancestors 'none').
"use strict";
(() => {
const ua = navigator.userAgent || "";
const inApp = /FBAN|FBAV|FB_IAB|Instagram|Line\/|Twitter|TwitterAndroid|TikTok|musical_ly|BytedanceWebview|LinkedInApp|Snapchat|Pinterest\/|; wv\)/i.test(ua);
const android = /Android/i.test(ua), ios = /iPhone|iPad|iPod/i.test(ua);
window.HQ_IN_APP = inApp;
function sheet(href) {
  const abs = new URL(href, location.origin).href;
  let d = document.getElementById("oauthSheet");
  if (!d) {
    d = document.createElement("dialog"); d.id = "oauthSheet"; d.className = "sheet oauth-sheet"; d.dataset.testid = "oauth-sheet";
    d.setAttribute("aria-labelledby", "oauthH"); document.body.append(d);
  }
  const open = android ? `intent://${abs.replace(/^https?:\/\//, "")}#Intent;scheme=https;package=com.android.chrome;S.browser_fallback_url=${encodeURIComponent(abs)};end`
    : ios ? "x-safari-" + abs : abs;
  d.innerHTML = `<h2 id="oauthH">Open Buffer's sign-in in your browser</h2>
    <p>You're inside another app's browser, where Buffer's sign-in can fail. Open it in ${android ? "Chrome" : ios ? "Safari" : "your browser"} and you'll come straight back here, connected.</p>
    <div class="oauth-acts"><a class="btn" href="${open}" data-testid="oauth-open-browser">Open in ${android ? "Chrome" : ios ? "Safari" : "browser"}</a>
    <button class="btn2" type="button" data-testid="oauth-copy">Copy the link</button>
    <a class="link" href="${abs}" data-testid="oauth-here">Try here anyway</a></div>
    <p class="muted oauth-hint">${ios ? "If nothing opens, tap the ••• or share menu and choose Open in Safari." : "If nothing opens, use the app's menu and choose Open in browser."}</p>
    <form method="dialog"><button class="btn2 sm" data-testid="oauth-close">Close</button></form>`;
  d.querySelector("[data-testid=oauth-copy]").onclick = async (e) => { try { await navigator.clipboard.writeText(abs); e.target.textContent = "Copied. Paste it in your browser"; } catch (_) { e.target.textContent = abs; } };
  d.showModal();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest('a[href^="/login"]');
  if (!a || !inApp || e.defaultPrevented) return;
  e.preventDefault(); sheet(a.getAttribute("href"));
});
// signed in from another tab or the system browser: pick it up when this page comes back into view
addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !document.querySelector('[data-testid=landing]')) return;
  fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json()).then((s) => { if (s.connected) location.replace("/"); }).catch(() => {});
});
})();
