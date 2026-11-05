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
* The local fallback is faster-whisper ``base.en`` (CPU int8; ``HQ_ASR_LOCAL_MODEL`` picks another, e.g. ``small`` for
  other languages).

Data: Groq says it doesn't retain inference inputs by default, may keep them up to 30 days for reliability or abuse
investigations, and offers Zero Data Retention in its data controls (https://console.groq.com/docs/your-data).
"""
from __future__ import annotations

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


class GroqUnavailable(RuntimeError):
    """Groq can't take this chunk (no key, a free limit is near, a 429, an error): transcribe it locally."""


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
    return out


# ---------------------------------------------------------------------- Groq
def groq_chunk(flac: Path, secs: float, key: str, budget: Budget, http=None) -> tuple[list[dict], str]:
    """One chunk through Groq (words in chunk seconds, language). Raises GroqUnavailable instead of ever retrying."""
    ok, why = budget.allow(secs)
    if not ok:
        raise GroqUnavailable(why)
    from .. import free_quota  # the app's shared ledger: a fresh worker machine's own counter starts at zero
    ok, why = free_quota.remote_ok("groq", secs)
    if not ok:
        raise GroqUnavailable(why)
    import httpx
    client = http or httpx.Client(timeout=TIMEOUT_S, trust_env=False, follow_redirects=False)
    try:
        budget.record(secs)  # counted before the call: a timeout still used the request
        r = client.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"},
                        data={"model": GROQ_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word",
                              "temperature": "0"},
                        files={"file": (flac.name, flac.read_bytes(), "audio/flac")})
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
        budget.block(wait)
        raise GroqUnavailable("Groq rate limit (429)")
    if r.status_code != 200:
        raise GroqUnavailable(f"Groq answered {r.status_code}")
    try:
        d = r.json()
        words = [{"w": str(w["word"]).strip(), "s": round(float(w["start"]), 2), "e": round(float(w["end"]), 2), "p": 1.0}
                 for w in d.get("words") or [] if str(w.get("word", "")).strip()]
    except (ValueError, KeyError, TypeError):
        raise GroqUnavailable("Groq's answer couldn't be read")
    lang = str(d.get("language") or "").lower()
    return words, LANG.get(lang, lang[:2] if len(lang) == 2 else lang)


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
    None when Groq can't do even the first chunk (no key, a limit, an error): the caller's local path runs then."""
    key = key if key is not None else os.environ.get("GROQ_API_KEY", "")
    if not key:
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
                    got = groq_chunk(f, n, key, budget, http)
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
        label = f"Groq {GROQ_MODEL} (free tier)"
        if local_n:
            label += f" + faster-whisper {local_model()} for {local_n} of {len(chunks)} parts ({why})"
        return words, lang, label
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
