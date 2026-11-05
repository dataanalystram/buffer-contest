"""Free speech-to-text chain for One take: Groq's free tier first, then faster-whisper on this machine.

    words, language, provider = asr.free_first(path) or <local faster-whisper>

* Groq ``whisper-large-v3-turbo`` (OpenAI-compatible ``/audio/transcriptions``, word timestamps) runs only when
  ``GROQ_API_KEY`` is set. Its free plan limits (https://console.groq.com/docs/rate-limits, checked 4 Nov 2026) are
  enforced HERE, before any request, with a safety margin: 20 requests a minute, 2,000 a day, 7,200 audio-seconds an
  hour and 28,800 a day (each request bills at least 10 s). Near a limit, or on any 429 / error, the chunk (and every
  chunk after it) is transcribed locally instead. Nothing ever retries into a paid tier: a 429 is never retried.
* Audio only (16 kHz mono FLAC, chunks of at most 10 minutes, well under the 25 MB free-tier file cap); no video
  frames leave the machine.
* Usage is counted in a small JSON file (``HQ_ASR_USAGE_FILE``, default in the temp dir) so several jobs on one
  machine share one budget, AND on the video worker every request is first reserved on the app's shared ledger
  (studio/free_quota.py), because each GitHub Actions run is a fresh machine whose own counter starts at zero.
* On the video worker (InstaBox or GitHub Actions) there is no Groq key: each chunk goes to the APP
  (``free_quota.PROXY``, ``POST /api/worker/jobs/{id}/asr``), which calls Groq with its own key under the ledger
  (studio/free_proxy.py). The key never leaves the app; a refusal or any error means local for the rest.
* The local fallback is faster-whisper ``base.en`` (CPU int8; ``HQ_ASR_LOCAL_MODEL`` picks another, e.g. ``small`` for
  other languages).

Data: Groq says it doesn't retain inference inputs by default, may keep them up to 30 days for reliability or abuse
investigations, and offers Zero Data Retention in its data controls (https://console.groq.com/docs/your-data).
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
LIMITS = {"rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800}   # Groq free plan, whisper-large-v3-turbo
MARGIN = 0.9                                                   # stop at 90% of every limit
MIN_BILLED_S = 10.0
CHUNK_S = 600.0
TIMEOUT_S = 120.0
LOCAL_DEFAULT = "base.en"
LANG = {"english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it", "portuguese": "pt", "hindi": "hi",
        "japanese": "ja", "chinese": "zh", "korean": "ko", "dutch": "nl", "russian": "ru", "arabic": "ar", "turkish": "tr"}
_lock = threading.Lock()


@contextlib.contextmanager
def _file_lock(path: Path):
    """An exclusive lock shared by every process on this machine (check + count is one step, so two jobs can't both
    take the last free request). No lock available: the in-process lock still serialises this process."""
    try:
        import fcntl
    except ImportError:  # not POSIX
        fcntl = None
    f = None
    try:
        if fcntl is not None:
            try:
                f = open(str(path) + ".lock", "a+")
                fcntl.flock(f, fcntl.LOCK_EX)
            except OSError:
                f = None
        yield
    finally:
        if f is not None:
            f.close()  # closing releases the flock


class GroqUnavailable(RuntimeError):
    """Groq can't take this chunk (no key, a free limit is near, a 429, an error): transcribe it locally."""


class GroqRateLimited(GroqUnavailable):
    """Groq answered 429; ``wait`` is its retry-after in seconds. Never retried."""

    def __init__(self, wait: float):
        super().__init__("Groq rate limit (429)")
        self.wait = wait


def local_model(requested: str | None = None) -> str:
    return os.environ.get("HQ_ASR_LOCAL_MODEL") or LOCAL_DEFAULT


# ---------------------------------------------------------------------- the free-tier budget
class Budget:
    """Requests and billed audio-seconds in rolling windows, persisted to a JSON file."""

    def __init__(self, path: str | None = None, now=time.time):
        self.path = Path(path or os.environ.get("HQ_ASR_USAGE_FILE") or (Path(tempfile.gettempdir()) / "hq_groq_usage.json"))
        self.now = now

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        try:
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(d))
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _events(self, d: dict) -> list[list[float]]:
        t = self.now()
        return [e for e in d.get("events", []) if isinstance(e, list) and len(e) == 2 and t - float(e[0]) < 86400]

    def usage(self) -> dict:
        t = self.now()
        ev = self._events(self._load())
        return {"rpm": sum(1 for a, _ in ev if t - a < 60), "rpd": len(ev),
                "ash": sum(s for a, s in ev if t - a < 3600), "asd": sum(s for _, s in ev),
                "blocked_until": float(self._load().get("blocked_until") or 0)}

    def allow(self, secs: float) -> tuple[bool, str]:
        """Whether one more request of `secs` audio stays inside MARGIN of every free limit."""
        billed = max(MIN_BILLED_S, float(secs))
        u = self.usage()
        if u["blocked_until"] > self.now():
            return False, "Groq asked us to wait (429)"
        if u["rpm"] + 1 > LIMITS["rpm"] * MARGIN:
            return False, "near Groq's 20 requests/minute"
        if u["rpd"] + 1 > LIMITS["rpd"] * MARGIN:
            return False, "near Groq's 2,000 requests/day"
        if u["ash"] + billed > LIMITS["ash"] * MARGIN:
            return False, "near Groq's 7,200 audio-seconds/hour"
        if u["asd"] + billed > LIMITS["asd"] * MARGIN:
            return False, "near Groq's 28,800 audio-seconds/day"
        return True, ""

    def reserve(self, secs: float) -> tuple[bool, str]:
        """allow + record as ONE step under a machine-wide file lock (concurrent jobs share one budget)."""
        with _file_lock(self.path):
            ok, why = self.allow(secs)
            if ok:
                self.record(secs)
            return ok, why

    def record(self, secs: float) -> None:
        with _lock:
            d = self._load()
            ev = self._events(d)
            ev.append([self.now(), max(MIN_BILLED_S, float(secs))])
            d["events"] = ev
            self._save(d)

    def block(self, seconds: float) -> None:
        with _lock:
            d = self._load()
            d["blocked_until"] = self.now() + max(60.0, float(seconds))
            self._save(d)


# ---------------------------------------------------------------------- audio
def audio_chunks(path: str, tmp: Path, chunk_s: float = CHUNK_S) -> list[tuple[float, float, Path]]:
    """[(offset, seconds, flac)] 16 kHz mono FLAC chunks of the source's audio (audio only: no frames leave)."""
    from .. import mediasafe
    from ..take import probe
    dur = float(probe(path)["duration"] or 0)
    out, k, off = [], 0, 0.0
    while off < dur - 0.05:
        n = min(chunk_s, dur - off)
        f = tmp / f"a{k}.flac"
        r = mediasafe.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{off:.3f}", "-t", f"{n:.3f}", "-i", path, "-vn", "-map", "0:a:0",
                           "-ac", "1", "-ar", "16000", "-c:a", "flac", str(f)], timeout=600)
        if r.returncode != 0:
            raise GroqUnavailable("couldn't extract the audio")
        out.append((off, n, f))
        off += n
        k += 1
    if not out:  # no duration in the container (browser WebM): the caller's local path transcribes it
        raise GroqUnavailable("couldn't determine the audio duration")
    return out


# ---------------------------------------------------------------------- Groq
def groq_post(data: bytes, name: str, key: str, http=None) -> tuple[list[dict], str]:
    """ONE request to Groq (no budget here: callers reserve first). Words in chunk seconds, language. Raises
    GroqUnavailable (GroqRateLimited on a 429) instead of ever retrying. Used by the worker with its own key and by
    the app's proxy (studio/free_proxy.py)."""
    import httpx
    client = http or httpx.Client(timeout=TIMEOUT_S, trust_env=False, follow_redirects=False)
    try:
        r = client.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"},
                        data={"model": GROQ_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word",
                              "temperature": "0"},
                        files={"file": (name, data, "audio/flac")})
    except Exception as e:  # noqa: BLE001 - network, closed client, anything: local takes over
        raise GroqUnavailable(f"Groq unreachable ({e.__class__.__name__})")
    finally:
        if http is None:
            client.close()
    if r.status_code == 429:
        try:
            wait = float(r.headers.get("retry-after") or 60)
        except ValueError:
            wait = 60.0
        raise GroqRateLimited(wait)
    if r.status_code != 200:
        raise GroqUnavailable(f"Groq answered {r.status_code}")
    try:
        d = r.json()
        words = clean_words(d.get("words") or [], groq=True)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GroqUnavailable("Groq's answer couldn't be read")
    lang = str(d.get("language") or "").lower()
    return words, LANG.get(lang, lang[:2] if len(lang) == 2 else lang)


def clean_words(raw, groq: bool = False) -> list[dict]:
    """[{w, s, e, p}] from Groq's words (``groq``: {word, start, end}) or from the app's proxy answer ({w, s, e, p}).
    TypeError / ValueError on anything malformed."""
    if not isinstance(raw, list) or len(raw) > 200_000:
        raise TypeError("words must be a list")
    out = []
    for w in raw:
        if not isinstance(w, dict):
            raise TypeError("a word must be an object")
        text = str(w["word"] if groq else w["w"]).strip()
        if not text:
            continue
        s, e = float(w["start"] if groq else w["s"]), float(w["end"] if groq else w["e"])
        p = 1.0 if groq else float(w.get("p", 1.0))
        out.append({"w": text[:200], "s": round(s, 2), "e": round(e, 2), "p": round(min(1.0, max(0.0, p)), 2)})
    return out


def groq_chunk(flac: Path, secs: float, key: str, budget: Budget, http=None) -> tuple[list[dict], str]:
    """One chunk through Groq with THIS machine's key (a self-hoster's worker). Raises GroqUnavailable."""
    ok, why = budget.reserve(secs)  # counted before the call: a timeout still used the request
    if not ok:
        raise GroqUnavailable(why)
    from .. import free_quota  # the app's shared ledger: a fresh worker machine's own counter starts at zero
    ok, why = free_quota.remote_ok("groq", secs)
    if not ok:
        raise GroqUnavailable(why)
    try:
        return groq_post(flac.read_bytes(), flac.name, key, http)
    except GroqRateLimited as e:
        budget.block(e.wait)
        raise


def proxy_chunk(flac: Path, secs: float, proxy) -> tuple[list[dict], str]:
    """One chunk through the APP (it holds the key and the ledger). Raises GroqUnavailable on a refusal or any error."""
    data = flac.read_bytes()
    from .. import free_quota
    if len(data) > free_quota.ASR_MAX_BYTES:
        raise GroqUnavailable("this part is over Groq's 25 MB free-tier file cap")
    d = proxy.asr(data, secs)
    if d.get("ok") is not True:
        raise GroqUnavailable(str(d.get("why") or "the app couldn't transcribe this part"))
    try:
        words = clean_words(d.get("words"))
    except (TypeError, ValueError, KeyError):
        raise GroqUnavailable("the app's transcript couldn't be read")
    lang = str(d.get("language") or "")[:8]
    return words, lang


def _local_chunk(flac: Path, model: str) -> tuple[list[dict], str]:
    try:
        from faster_whisper import WhisperModel  # MIT
    except ImportError:
        raise RuntimeError("faster-whisper isn't installed on this worker")
    wm = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    segs, info = wm.transcribe(str(flac), word_timestamps=True, vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                               beam_size=1, condition_on_previous_text=False)
    words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)}
             for seg in segs for w in seg.words or []]
    return words, info.language


def free_first(path: str, key: str | None = None, budget: Budget | None = None, http=None, local=None):
    """(words, language, provider) with Groq doing every chunk it can inside the free limits and the rest local, or
    None when Groq can't do even the first chunk (no key, a limit, an error): the caller's local path runs then.
    On the video worker (``free_quota.PROXY`` set, no explicit ``key``) the app makes each Groq call with its key."""
    from .. import free_quota
    proxy = free_quota.PROXY if key is None else None
    key = key if key is not None else os.environ.get("GROQ_API_KEY", "")
    if proxy is None and not key:
        return None
    budget = budget or Budget()
    tmp = Path(tempfile.mkdtemp(prefix="hq_asr_"))
    try:
        try:
            chunks = audio_chunks(path, tmp)
        except GroqUnavailable as e:
            print(f"asr: {e}; transcribing locally", flush=True)
            return None
        words, lang, groq_n, local_n, why = [], "", 0, 0, ""
        for off, n, f in chunks:
            got = None
            if not why:
                try:
                    got = proxy_chunk(f, n, proxy) if proxy is not None else groq_chunk(f, n, key, budget, http)
                    groq_n += 1
                except GroqUnavailable as e:
                    why = str(e)
                    print(f"asr: {why}; the rest is transcribed locally", flush=True)
                    if groq_n == 0:
                        return None
            if got is None:
                got = (local or _local_chunk)(f, local_model())
                local_n += 1
            ws, lg = got
            lang = lang or lg
            words += [{**w, "s": round(w["s"] + off, 2), "e": round(w["e"] + off, 2)} for w in ws]
        label = f"Groq {GROQ_MODEL} (free tier{', via the app' if proxy is not None else ''})"
        if local_n:
            label += f" + faster-whisper {local_model()} for {local_n} of {len(chunks)} parts ({why})"
        return words, lang, label
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
