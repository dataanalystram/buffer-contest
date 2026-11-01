"""Word-level transcript for captions: faster-whisper (MIT, CTranslate2 int8 on CPU) with Silero VAD.

``transcribe(path)`` -> {"words": [{"w", "s", "e"}], "text", "language", "model"}. The audio is first decoded by our
hardened FFmpeg to 16 kHz mono WAV and handed over as a numpy array, so faster-whisper's own PyAV decoder never opens the untrusted container
(it also sidesteps a PyAV 19 / faster-whisper 1.2.1 incompatibility seen 1 Nov 2026: open() got 'metadata_errors').
Model size: ``tiny`` (~75 MB download, fits Render's 512 MB) by default for the hosted worker; ``base``/``small`` are
noticeably better on names and numbers and are the default on GitHub Actions / bigger plans (HQ_WHISPER_MODEL).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import ff

DEFAULT_MODEL = os.environ.get("HQ_WHISPER_MODEL", "tiny")


def extract_wav(path: str, out: str) -> str:
    ff.run(["-y", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out], timeout=600)
    return out


def load_wav(path: str):
    """16 kHz mono s16le WAV -> float32 numpy array in [-1, 1]."""
    import wave

    import numpy as np
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(path: str, model: str | None = None, language: str | None = None, cpu_threads: int = 2,
               names: list[str] | None = None) -> dict:
    """names: product/company/person names from the story. Passed as Whisper's initial prompt so small models spell
    them right (tiny heard \"Subvocal\" as \"So Volko\" without it, 1 Nov 2026 test)."""
    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # noqa: BLE001
        raise ff.MediaError("faster-whisper isn't installed on this worker (pip install faster-whisper).") from e
    name = model or DEFAULT_MODEL
    with tempfile.TemporaryDirectory() as td:
        wav = extract_wav(path, str(Path(td) / "a.wav"))
        audio = load_wav(wav)  # a numpy array: faster-whisper never opens a file itself (no PyAV decode)
        wm = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        prompt = ("Names: " + ", ".join(n for n in names if n) + ".") if names else None
        segs, info = wm.transcribe(audio, word_timestamps=True, vad_filter=True, language=language, beam_size=1,
                                   initial_prompt=prompt)
        words = []
        for s in segs:
            for w in s.words or []:
                t = (w.word or "").strip()
                if t:
                    words.append({"w": t, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
    return {"words": words, "text": " ".join(w["w"] for w in words), "language": info.language, "model": name}
