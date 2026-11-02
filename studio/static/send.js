// Send a file page (/send, /send/<code>). Signed in: make a short code + QR for another device (POST /api/send-codes).
// With a code: drop photos or videos; each streams to PUT /send/<code>/file (the raw file, with upload progress) into the
// person's Library. Without either: a field to type the code. Same caps as every upload (GET /api/uploads/limits).
"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const norm = (s) => [...String(s || "").toUpperCase()].filter((c) => ALPHA.includes(c)).join("").slice(0, 8);
  const pretty = (c) => (c.length > 4 ? c.slice(0, 4) + "-" + c.slice(4) : c);
  const mb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(n >= 10485760 ? 0 : 1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");
  const S = { code: norm(location.pathname.split("/")[2] || ""), max: 100 * 1048576, csrf: "", poll: null, busy: 0, link: "" };

  async function json(url, opts) {
    const r = await fetch(url, Object.assign({ credentials: "same-origin" }, opts || {}));
    let d = {}; try { d = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) { const e = new Error(d.error || `Something went wrong (HTTP ${r.status}).`); e.status = r.status; throw e; }
    return d;
  }
  function setMax(bytes) {
    S.max = bytes || S.max;
    const t = Math.round(S.max / 1048576) + " MB";
    $("#sdMax").textContent = t;
    document.querySelectorAll(".sd-max").forEach((e) => (e.textContent = t));
  }
  function err(el, msg) { el.textContent = msg || ""; el.hidden = !msg; }

  // ---- a code: the drop zone
  async function status() {
    if (!S.code) return;
    try {
      const d = await json(`/send/${S.code}/status`);
      setMax(d.max_bytes);
      const mins = Math.max(0, Math.round(d.expires_in / 60));
      $("#sdCodeMeta").textContent = `Code ${pretty(S.code)} · ${d.files_left} more file${d.files_left === 1 ? "" : "s"} · expires in ${mins} min`;
      if (!S.busy) $("#sdList").innerHTML = (d.received || []).map((f) => `<li class="ok"><b>${esc(f.name)}</b><span class="sd-st">✓ In your Library · ${esc(mb(f.bytes))}</span></li>`).join("");
      err($("#sdErr"), "");
    } catch (e) {
      err($("#sdErr"), e.message);
      if ((e.status === 404 || e.status === 410) && $("#sdMine").hidden) { stopPoll(); showEnter(e.message); }
    }
  }
  function startPoll() { stopPoll(); S.poll = setInterval(() => { if (!document.hidden) status(); }, 5000); }
  function stopPoll() { if (S.poll) clearInterval(S.poll); S.poll = null; }

  function send(file) {
    const li = document.createElement("li");
    li.innerHTML = `<b>${esc(file.name)}</b><span class="sd-st">Sending · ${esc(mb(file.size))}</span><progress max="100" value="0" aria-label="Sending ${esc(file.name)}"></progress>`;
    $("#sdList").prepend(li);
    const st = li.querySelector(".sd-st"), bar = li.querySelector("progress");
    if (file.size > S.max) {
      li.className = "bad"; bar.remove();
      st.textContent = `Too big: ${mb(file.size)}. Files up to ${Math.round(S.max / 1048576)} MB.`;
      return Promise.resolve();
    }
    S.busy++;
    return new Promise((done) => {
      const x = new XMLHttpRequest();
      x.open("PUT", `/send/${S.code}/file?name=${encodeURIComponent(file.name)}`);
      x.upload.onprogress = (e) => { if (e.lengthComputable) { bar.value = Math.round((e.loaded / e.total) * 100); st.textContent = `Sending · ${bar.value}%`; } };
      x.onload = () => {
        let d = {}; try { d = JSON.parse(x.responseText || "{}"); } catch (_) { /* empty */ }
        bar.remove();
        if (x.status >= 200 && x.status < 300) { li.className = "ok"; st.textContent = `✓ In your Library · ${mb(d.bytes || file.size)}`; }
        else { li.className = "bad"; st.textContent = d.error || `Didn't arrive (HTTP ${x.status}).`; }
        S.busy--; done(); if (!S.busy) status();
      };
      x.onerror = () => { bar.remove(); li.className = "bad"; st.textContent = "The connection dropped. Try again."; S.busy--; done(); };
      x.send(file);
    });
  }
  async function sendAll(files) { for (const f of files) await send(f); }  // one at a time: a phone on mobile data stays steady

  function showDrop() {
    $("#sdDropWrap").hidden = false;
    const drop = $("#sdDrop");
    $("#sdFile").onchange = (e) => { sendAll([...e.target.files]); e.target.value = ""; };
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
    drop.addEventListener("drop", (e) => { e.preventDefault(); sendAll([...((e.dataTransfer && e.dataTransfer.files) || [])]); });
    status(); startPoll();
  }

  // ---- no code: type it
  function showEnter(msg) {
    $("#sdEnter").hidden = false; $("#sdDropWrap").hidden = true;
    err($("#sdEnterErr"), msg || "");
    $("#sdIn").addEventListener("input", (e) => { e.target.value = pretty(norm(e.target.value)); });
    $("#sdEnter").onsubmit = (e) => {
      e.preventDefault();
      const c = norm($("#sdIn").value);
      if (c.length !== 8) return err($("#sdEnterErr"), "The code has 8 letters and numbers, like ABCD-EFGH.");
      location.href = "/send/" + c;
    };
    $("#sdIn").focus();
  }

  // ---- signed in: a code + QR for another device
  async function makeCode() {
    const d = await json("/api/send-codes", { method: "POST", headers: { "Content-Type": "application/json", "X-HQ-CSRF": S.csrf }, body: "{}" });
    S.code = norm(d.code); S.link = d.url;
    $("#sdMine").hidden = false;
    $("#sdCode").textContent = d.code;
    $("#sdPage").textContent = d.page.replace(/^https?:\/\//, "");
    $("#sdFiles").textContent = `up to ${d.max_files} files`;
    $("#sdExpires").textContent = `Valid ${Math.round(d.expires_in / 60)} minutes`;
    const qr = $("#sdQr");
    if (d.qr) { qr.src = d.qr; qr.hidden = false; } else qr.closest("figure").hidden = true;
    setMax(d.max_mb * 1048576);
  }

  async function boot() {
    json("/api/uploads/limits").then((d) => setMax(d.file_max_bytes)).catch(() => {});
    if (S.code) return showDrop();
    let sess = {};
    try { sess = await json("/api/session"); } catch (_) { /* signed out */ }
    S.csrf = sess.csrf || "";
    if (sess.connected && !sess.demo) {
      try { await makeCode(); showDrop(); } catch (e) { return showEnter(e.message); }
      $("#sdNew").onclick = async () => { try { await makeCode(); $("#sdList").innerHTML = ""; status(); } catch (e) { err($("#sdErr"), e.message); } };
      $("#sdCopy").onclick = async () => {
        try { await navigator.clipboard.writeText(S.link); $("#sdCopy").textContent = "Copied"; setTimeout(() => ($("#sdCopy").textContent = "Copy link"), 1500); }
        catch (_) { window.prompt("Copy this link", S.link); }
      };
      return;
    }
    showEnter(sess.demo ? "The sample workspace's Library is read-only. Connect Buffer to send your own files." : "");
  }
  boot();
})();
