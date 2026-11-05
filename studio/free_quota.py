"""Free-tier budgets that hold across every video-worker run (Groq Whisper, the vision layout picker).

The video worker runs on a fresh GitHub Actions machine each time, so a counter kept on that machine starts at zero on
every job. The worker therefore asks the app to reserve each free request BEFORE it makes it:

    POST /api/worker/jobs/{jid}/quota  {"provider": "groq" | "nvidia" | "openrouter", "units": <audio seconds or 1>}
    -> {"ok": true} or {"ok": false, "why": "..."}

The app keeps one ledger (table ``free_usage``: provider, time, units; no user data) and refuses a reservation that
would cross MARGIN of a provider's free limit. The worker treats a refusal, and ANY error reaching the app, as "no":
Groq then falls back to faster-whisper on the machine and the vision picker to the rules. Nothing ever retries into a
paid tier. The worker's own per-machine counters (studio/media/asr.py, studio/media/reframe/vision.py) still apply.

The keys never leave the app (5 Nov 2026). Production runs jobs on the InstaBox box and the repo holds no Actions
secrets, so a worker has no GROQ / NVIDIA / OPENROUTER key. The worker sets PROXY (WorkerProxy) and the APP makes the
free call with its own key, on the job's token, under the same ledger (the pattern #108 uses for Jev):

    POST /api/worker/jobs/{jid}/asr     body: one 16 kHz mono FLAC chunk (<= 25 MB), header x-audio-seconds
        -> {"ok": true, "words": [{w, s, e, p}], "language": "en", "model": ...} or {"ok": false, "why": "..."}
    POST /api/worker/jobs/{jid}/vision  {"frames": [<base64 JPEG>, ...]} (<= 12, each <= 400 KB, <= 1024 px)
        -> {"ok": true, "category": ..., "provider": ..., "model": ...} or {"ok": false, "why": "..."}

Server side: studio/free_proxy.py. A worker that has its own key and no PROXY (a self-hoster's machine) still calls
the provider itself, reserving on REMOTE first.

No FastAPI here: the worker imports this module.
"""
from __future__ import annotations

import threading
import time

# Free plans: Groq whisper-large-v3-turbo (https://console.groq.com/docs/rate-limits, checked 4 Nov 2026; each request
# bills at least 10 audio-seconds); OpenRouter ':free' models (about 50 requests a day on a key without credits);
# NVIDIA API catalog (a 20 requests/minute guard and a conservative day cap).
LIMITS: dict[str, dict] = {
    "groq": {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800, "min_units": 10.0, "margin": 0.9},
    "nvidia": {"rpm": 20, "rpd": 200, "min_units": 1.0, "margin": 1.0},
    "openrouter": {"rpm": 20, "rpd": 45, "min_units": 1.0, "margin": 1.0},
}
VISION = ("nvidia", "openrouter")
MAX_UNITS = 3600.0
ASR_MAX_BYTES = 25 * 1024 * 1024        # Groq's free-tier file cap; a 10-minute 16 kHz mono FLAC chunk is ~10-19 MB
ASR_MAX_SECONDS = 610.0                 # one chunk (studio/media/asr.py CHUNK_S = 600) plus rounding
VISION_MAX_FRAMES, VISION_FRAME_MAX_BYTES, VISION_MAX_PX = 12, 400_000, 1024
DAY = 86400.0
_lock = threading.Lock()

# The worker sets this to a callable (provider, units) -> (ok, why) that asks the app. None (local runs, tests, the web
# server itself): only the machine's own counters apply.
REMOTE = None
# The worker sets this to a WorkerProxy: the app makes Groq / vision calls with ITS key. None: no proxy (local, tests).
PROXY = None


def check(events: list[tuple[float, float]], provider: str, units: float, now: float) -> tuple[bool, str]:
    """Whether one more request of ``units`` stays inside MARGIN of every free limit, given [(time, units)] for that
    provider. Pure."""
    lim = LIMITS[provider]
    billed = max(lim["min_units"], float(units))
    m = lim["margin"]
    ev = [(t, u) for t, u in events if now - t < DAY]
    if sum(1 for t, _ in ev if now - t < 60) + 1 > lim["rpm"] * m:
        return False, f"{provider}: near {lim['rpm']} requests/minute"
    if len(ev) + 1 > lim["rpd"] * m:
        return False, f"{provider}: near {lim['rpd']} requests/day"
    if "ash" in lim and sum(u for t, u in ev if now - t < 3600) + billed > lim["ash"] * m:
        return False, f"{provider}: near {lim['ash']} audio-seconds/hour"
    if "asd" in lim and sum(u for _, u in ev) + billed > lim["asd"] * m:
        return False, f"{provider}: near {lim['asd']} audio-seconds/day"
    return True, ""


def clean(provider, units) -> tuple[str, float]:
    """Validated (provider, units) from a request body; ValueError on a bad type or value (the route answers 400)."""
    if not isinstance(provider, str) or provider not in LIMITS:
        raise ValueError(f"provider must be one of: {', '.join(LIMITS)}.")
    if isinstance(units, bool) or not isinstance(units, (int, float)) or not 0 < float(units) <= MAX_UNITS:
        raise ValueError(f"units must be a number above 0 and at most {int(MAX_UNITS)}.")
    return provider, float(units)


class Ledger:
    """The app's shared ledger (one web instance; the lock serialises reservations inside it)."""

    def __init__(self, store, now=time.time):
        self.store, self.now = store, now
        self._ready = False

    def _table(self) -> None:
        if not self._ready:
            pg = getattr(self.store, "pg", False)
            real = "DOUBLE PRECISION" if pg else "REAL"  # Postgres REAL is 4 bytes: a Unix time would round by minutes
            self.store.x(f"CREATE TABLE IF NOT EXISTS free_usage (provider TEXT, t {real}, units {real})")
            if pg:
                self.store.x("ALTER TABLE free_usage ENABLE ROW LEVEL SECURITY")
            self._ready = True

    def reserve(self, provider, units) -> tuple[bool, str]:
        provider, units = clean(provider, units)
        with _lock:
            self._table()
            t = self.now()
            self.store.x("DELETE FROM free_usage WHERE t < ?", (t - DAY,))
            ev = [(float(r["t"]), float(r["units"])) for r in
                  self.store.q("SELECT t, units FROM free_usage WHERE provider=? AND t >= ?", (provider, t - DAY))]
            ok, why = check(ev, provider, units, t)
            if ok:
                self.store.x("INSERT INTO free_usage(provider,t,units) VALUES(?,?,?)",
                             (provider, t, max(LIMITS[provider]["min_units"], units)))
            return ok, why


def remote_ok(provider: str, units: float) -> tuple[bool, str]:
    """Reserve on the shared ledger when the worker set one. Any error answers no: the free call is skipped, never paid."""
    fn = REMOTE
    if fn is None:
        return True, ""
    try:
        ok, why = fn(provider, units)
        return bool(ok), str(why or "")
    except Exception as e:  # noqa: BLE001 - unreachable app, closed client, bad JSON: no free request this time
        return False, f"{provider}: the shared free-tier ledger didn't answer ({e.__class__.__name__})"


def worker_remote(client, base: str, headers: dict, jid: int):
    """The worker's REMOTE: one small POST to the app per free request (10 s timeout)."""
    url = f"{base.rstrip('/')}/api/worker/jobs/{int(jid)}/quota"

    def ask(provider: str, units: float) -> tuple[bool, str]:
        r = client.post(url, headers=headers, json={"provider": provider, "units": float(units)}, timeout=10)
        if r.status_code != 200:
            return False, f"{provider}: the ledger answered {r.status_code}"
        d = r.json()
        if not isinstance(d, dict):
            return False, f"{provider}: the ledger's answer couldn't be read"
        return d.get("ok") is True, str(d.get("why") or "")
    return ask


class WorkerProxy:
    """The worker's side of the app's free-call proxy. Never raises: any error answers {"ok": False, "why": ...}, so the
    caller falls back to faster-whisper or to the rules. Holds the job token only (no provider key exists here)."""

    def __init__(self, client, base: str, headers: dict, jid: int):
        self.client, self.headers = client, dict(headers)
        self.url = f"{base.rstrip('/')}/api/worker/jobs/{int(jid)}"

    def _post(self, path: str, what: str, timeout: float, **kw) -> dict:
        try:
            r = self.client.post(self.url + path, timeout=timeout, **kw)
            if r.status_code != 200:
                return {"ok": False, "why": f"{what}: the app answered {r.status_code}"}
            d = r.json()
        except Exception as e:  # noqa: BLE001 - network, closed client, bad JSON: no free call this time
            return {"ok": False, "why": f"{what}: the app didn't answer ({e.__class__.__name__})"}
        if not isinstance(d, dict):
            return {"ok": False, "why": f"{what}: the app's answer couldn't be read"}
        return d

    def asr(self, flac: bytes, secs: float) -> dict:
        h = {**self.headers, "content-type": "audio/flac", "x-audio-seconds": f"{float(secs):.3f}"}
        return self._post("/asr", "groq", 180.0, content=flac, headers=h)

    def vision(self, frames: list[bytes]) -> dict:
        import base64
        body = {"frames": [base64.b64encode(f).decode() for f in frames[:VISION_MAX_FRAMES]]}
        return self._post("/vision", "vision", 90.0, json=body, headers=self.headers)
