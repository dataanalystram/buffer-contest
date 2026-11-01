"""Original, royalty-free background music synthesised in code (numpy), so no track ever needs a licence check.

* ``cinematic``: the owner's HuhByte score (organ pad + ticking pulse + piano ostinato + slow swell, A minor), ported
  from dataanalystram/Huhbyte.ai code/music/make_cinematic.py (same owner; credited in NOTICE). Calm, for explainers.
* ``pulse``: a 120 BPM electronic bed (kick, off-beat hats, bass, plucked arpeggio) for the Hype and Opus styles.

Both write 44.1 kHz stereo 16-bit WAV, fade in/out, peak-normalised; the compositor ducks them under real speech.
No third-party music is bundled (CC0 / library tracks would each need their own licence record).
"""
from __future__ import annotations

import wave

import numpy as np

SR = 44100


def _note(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _write(path: str, mix: np.ndarray, fade_in: float = 1.2, fade_out: float = 2.0, peak: float = 0.75) -> str:
    n = len(mix)
    fi, fo = min(n, int(fade_in * SR)), min(n, int(fade_out * SR))
    if fi:
        mix[:fi] *= np.linspace(0, 1, fi)[:, None]
    if fo:
        mix[-fo:] *= np.linspace(1, 0, fo)[:, None]
    mix = np.tanh(mix * 1.1)
    m = float(np.max(np.abs(mix))) or 1.0
    mix = mix / m * peak
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    return path


def cinematic(path: str, secs: float, seed: int = 0) -> str:
    """Owner's make_cinematic.py, as a function (same notes, levels and reverb)."""
    n = int(secs * SR)
    L, R = np.zeros(n), np.zeros(n)

    def add(sig, start, pan=0.0):
        s = int(start * SR)
        e = min(n, s + len(sig))
        if s >= n:
            return
        L[s:e] += sig[:e - s] * (1 - pan)
        R[s:e] += sig[:e - s] * (1 + pan)

    def organ(f, dur):
        x = np.arange(int(dur * SR)) / SR
        harm = [(1, 1), (2, .5), (3, .25), (4, .18), (6, .08), (8, .05), (.5, .35)]
        sig = sum(a * (np.sin(2 * np.pi * f * h * x) + np.sin(2 * np.pi * f * h * 1.002 * x)) for h, a in harm) / 6
        trem = 1 + 0.04 * np.sin(2 * np.pi * 5.2 * x)
        return sig * trem * np.minimum(1, x / 1.6) * np.clip(np.minimum(1, (dur - x) / 1.2), 0, 1)

    def piano(f, dur=1.6):
        x = np.arange(int(dur * SR)) / SR
        sig = np.sin(2 * np.pi * f * x) + .4 * np.sin(4 * np.pi * f * x) * np.exp(-x * 3) + .15 * np.sin(6 * np.pi * f * x) * np.exp(-x * 5)
        return sig * np.exp(-x * 2.2) * np.minimum(1, x / 0.004)

    def tick():
        r = np.random.RandomState(7).randn(int(0.04 * SR))
        r = r - np.convolve(r, np.ones(8) / 8, "same")
        return r * np.exp(-np.arange(len(r)) / SR * 180) * 0.07

    prog = [[45, 57, 60, 64], [41, 57, 60, 65], [48, 55, 60, 64], [43, 55, 59, 62]]
    ost = [[69, 72, 76, 72], [69, 72, 77, 72], [67, 72, 76, 72], [67, 71, 74, 71]]
    chord = 4.0
    nb = int(np.ceil(secs / chord))
    for b in range(nb):
        t0, c = b * chord, prog[(b + seed) % 4]
        lvl = min(1, 0.35 + b / max(1, nb - 1) * 0.65)
        for i, m in enumerate(c):
            add(organ(_note(m), chord + 1.4) * 0.11 * (1.3 if i == 0 else 1), t0, pan=(i - 1.5) * .15)
        if b >= 1:
            for k in range(8):
                m = ost[(b + seed) % 4][k % 4] + (12 if (b >= 4 and k % 2) else 0)
                add(piano(_note(m)) * 0.10 * lvl, t0 + k * 0.5, pan=0.3 if k % 2 else -0.3)
        for k in range(4):
            add(tick() * (0.6 + 0.4 * lvl), t0 + k * 1.0 + 0.5)
    mix = np.stack([L, R], 1)
    for d, g in [(0.071, .35), (0.113, .3), (0.173, .25), (0.29, .18)]:
        k = int(d * SR)
        tmp = mix.copy()
        tmp[k:] += mix[:-k] * g
        mix = tmp
    return _write(path, mix, 1.2, 2.5)


def pulse(path: str, secs: float, bpm: float = 120, seed: int = 0) -> str:
    """Driving electronic bed: four-on-the-floor kick, off-beat hats, eighth-note bass, plucked arpeggio."""
    n = int(secs * SR)
    mix = np.zeros((n, 2))
    beat = 60.0 / bpm
    rng = np.random.RandomState(seed)

    def put(sig, t, pan=0.0, g=1.0):
        s = int(t * SR)
        e = min(n, s + len(sig))
        if s < n:
            mix[s:e, 0] += sig[:e - s] * g * (1 - pan)
            mix[s:e, 1] += sig[:e - s] * g * (1 + pan)

    x = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-x * 30)) * x) * np.exp(-x * 9)
    hx = np.arange(int(0.06 * SR)) / SR
    hat = rng.randn(len(hx)) * np.exp(-hx * 70) * 0.25
    hat = hat - np.convolve(hat, np.ones(6) / 6, "same")
    roots = [45, 41, 48, 43]  # Am F C G
    nbeats = int(secs / beat) + 1
    for b in range(nbeats):
        t = b * beat
        put(kick, t, g=0.9)
        put(hat, t + beat / 2, pan=0.2, g=0.6)
        bar = (b // 4) % 4
        for h in (0, 1):
            f = _note(roots[bar] - 12 + (12 if h else 0))
            bx = np.arange(int(beat / 2 * SR)) / SR
            bass = np.sign(np.sin(2 * np.pi * f * bx)) * 0.18 * np.exp(-bx * 6)
            put(bass, t + h * beat / 2, g=0.7)
        if b >= 4:
            arp = [0, 7, 12, 15][(b % 4)]
            f = _note(roots[bar] + 24 + arp)
            px = np.arange(int(0.3 * SR)) / SR
            pl = (np.sin(2 * np.pi * f * px) + 0.3 * np.sin(4 * np.pi * f * px)) * np.exp(-px * 10) * 0.25
            put(pl, t + beat / 4, pan=-0.3 if b % 2 else 0.3)
    return _write(path, mix, 0.4, 1.5, peak=0.7)


def make(path: str, secs: float, mood: str = "cinematic", seed: int = 0) -> str:
    return pulse(path, secs, seed=seed) if mood in ("pulse", "hype", "energetic") else cinematic(path, secs, seed=seed)
