// Keyframes from the person's own video, taken in the browser at upload time (owner P0, 1 Nov 2026).
// The hosted app has no FFmpeg, so the agent could only hear a video, never see it, and wrote captions about the request
// instead of the story. Here a hidden <video> seeks to 4 points, each frame is drawn on a canvas and sent as a small JPEG
// (max 768 px) with the run; the server re-encodes them before a vision model looks (studio/agent_brain._keyframes).
"use strict";
(function () {
  const once = (el, ev, ms) => new Promise((res, rej) => {
    const t = setTimeout(() => { el.removeEventListener(ev, ok); rej(new Error(ev + " timed out")); }, ms);
    const ok = () => { clearTimeout(t); el.removeEventListener(ev, ok); res(); };
    el.addEventListener(ev, ok);
  });
  // src: a File/Blob (an upload) or a same-origin URL (a Library item). Never throws: no frames is a valid answer.
  window.hqKeyframes = async function (src, n = 4, max = 768) {
    const blobUrl = src instanceof Blob ? URL.createObjectURL(src) : "";
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous";
    v.src = blobUrl || String(src || "");
    const out = { frames: [], duration: 0, width: 0, height: 0 };
    try {
      if (v.readyState < 1) await once(v, "loadedmetadata", 10000);
      out.duration = isFinite(v.duration) ? v.duration : 0; out.width = v.videoWidth; out.height = v.videoHeight;
      if (!out.duration || !out.width) return out;
      const s = Math.min(1, max / Math.max(out.width, out.height));
      const c = document.createElement("canvas");
      c.width = Math.round(out.width * s); c.height = Math.round(out.height * s);
      const g = c.getContext("2d");
      for (let i = 0; i < n; i++) {
        v.currentTime = Math.min(out.duration - 0.05, out.duration * (0.1 + (0.8 * i) / Math.max(1, n - 1)));
        await once(v, "seeked", 6000);
        g.drawImage(v, 0, 0, c.width, c.height);
        const url = c.toDataURL("image/jpeg", 0.72);
        if (url.length < 400000) out.frames.push(url);
      }
    } catch (_) { /* a codec the browser can't decode, a timeout: the transcript and the person's words still count */ }
    finally { if (blobUrl) URL.revokeObjectURL(blobUrl); v.removeAttribute("src"); }
    return out;
  };
})();
