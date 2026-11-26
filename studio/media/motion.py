"""Motion moments for One take clips (beta, HQ_MOTION=1): an animated statistic and a kinetic line, drawn by libass in
the caption band, only where the words earn them.

The editor (``plan``) reads the clip's own timed words and picks at most one of each kind:

* **Stat pop**: a number the speaker says with its unit ("2 days", "$5 million", "300%", "10x"), counted up from zero
  and held, large, in the accent colour. Only numbers written as digits by the recogniser: never invented, never a
  number word turned into a figure.
* **Kinetic line**: a short punchy sentence (at most 7 words, with a turn or stake word such as "never", "zero",
  "wrong", or ending on "!"), slammed in word by word, larger than the captions.

And it says no when an effect wouldn't help: in the first seconds (the hook and Words behind you own them), over a
screen recording or a split (text would cover what's shown), closer than MIN_GAP to another effect, or when the clip has
no captions of ours (the source keeps its own). Each choice and each refusal carries a reason for the clip's meta.

The effect replaces the caption line it covers (same words, same face-safe band), so nothing new can sit on a face.
No new dependency: plain ASS override tags (``\\t``, ``\\fscx``, ``\\fad``) on the captions' own style. Each effect gets a
quiet sound accent made by ffmpeg's own generators (``sfx_graph``), mixed well under the voice.
"""
from __future__ import annotations

import os
import re

MIN_START = 3.2        # s into the clip: the hook window belongs to the cover line and Words behind you
MIN_GAP = 6.0          # s between two effects
STAT_S, LINE_MAX_S = 1.8, 2.6
COUNT_MS, COUNT_STEPS = 520, 10
STAT_SCALE, LINE_SCALE = 2.2, 1.6
ROW_H, BOTTOM_SAFE = 104, 1540   # a caption row's height at 100% (1920 tall) and the lowest y text may reach
UNITS = {"percent", "%", "x", "times", "million", "billion", "thousand", "k", "m", "days", "day", "hours", "hour",
         "weeks", "week", "months", "month", "years", "year", "minutes", "minute", "seconds", "customers", "users",
         "people", "followers", "subscribers", "views", "dollars", "euros", "pounds", "downloads", "sales", "orders"}
TURN = {"never", "nobody", "wrong", "mistake", "secret", "stop", "nothing", "everyone", "zero", "lost", "won", "failed",
        "broke", "free", "always", "only", "best", "worst", "first", "truth", "biggest", "impossible"}
FILLER_OPEN = {"so", "and", "but", "um", "uh", "like", "well", "or", "because"}
NO_FX_LAYOUTS = {"screen", "split"}
NO_FX_RULES = {"R2"}   # a fit over a screen recording


def enabled() -> bool:
    return os.environ.get("HQ_MOTION", "").strip() == "1"


_NUM = re.compile(r"^([$€£]?)(\d[\d,]*(?:\.\d+)?)(%|x|k|m)?$", re.I)


def _clean(w: str) -> str:
    return re.sub(r"[^\w$€£%.,'-]", "", str(w or "")).strip(".,!?;:")


def _stat_at(words: list[dict], i: int) -> dict | None:
    """The statistic starting at word i: {text, prefix, value, decimals, suffix, s, e, n} or None."""
    tok, j = _clean(words[i]["w"]), i + 1
    # the recogniser sometimes splits thousands into their own word: "$2" ",000" or "$2," "000" is "$2,000"
    # (26 Nov Mac run: the card said "$2")
    while j < len(words) and re.fullmatch(r"[$€£]?\d[\d,]*", tok or ""):
        nxt = str(words[j]["w"]).strip()
        joined = re.match(r"^,?(\d{3})(?!\d)", nxt) if (nxt.startswith(",") or str(words[j - 1]["w"]).strip().endswith(",")) else None
        if not joined:
            break
        tok, j = tok + "," + joined.group(1) + _clean(nxt[joined.end():]), j + 1
    m = _NUM.match(tok)
    if not m:
        return None
    i0, i = i, j - 1   # the number's last word
    raw = m.group(2).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if value == 0 and not m.group(3):
        return None
    dec = len(raw.split(".")[1]) if "." in raw else 0
    suffix, n = (m.group(3) or "").upper(), 1
    if not suffix and i + 1 < len(words):
        nxt = _clean(words[i + 1]["w"]).lower()
        if nxt in UNITS:
            suffix, n = (" " + ("%" if nxt == "percent" else nxt.upper())).replace(" %", "%"), 2
    if not suffix and not m.group(1):
        return None   # a bare number ("in 2017", "step 3") isn't a statistic worth a card
    if 1900 <= value <= 2100 and not suffix.strip() and not m.group(1):
        return None
    return {"prefix": m.group(1), "value": value, "decimals": dec, "suffix": suffix,
            "s": float(words[i0]["s"]), "e": float(words[i + n - 1]["e"]), "n": i + n - i0}


COMPARE_HOLD = 1.6      # s the comparison card stays after the second figure is said
JOIN = {"to", "vs", "versus", "instead", "than", "compared", "against", "into"}


def templates_on() -> bool:
    return os.environ.get("HQ_TEMPLATES", "").strip() == "1"


def _label(st: dict) -> str:
    return f"{st['prefix']}{st['value']:,.{st['decimals']}f}{st['suffix']}"


def _compare_in(sent: list[dict]) -> dict | None:
    """Two statistics of the same kind (same currency, or same unit family) in one sentence with a joining word
    between them: {left, right, from_to, mark, s, e, stat}. The words are the speaker's own: never a label invented."""
    stats, i = [], 0
    while i < len(sent):
        st = _stat_at(sent, i)
        if st:
            stats.append((i, st))
            i += st["n"]
        else:
            i += 1
    for (i, a), (j, b) in zip(stats, stats[1:]):
        between = [re.sub(r"[^\w']", "", str(w["w"])).lower() for w in sent[i + a["n"]:j]]
        if not between or len(between) > 4 or not (set(between) & JOIN):
            continue
        same = (a["prefix"] and a["prefix"] == b["prefix"]) or (_unit(a) and _unit(a) == _unit(b))
        if not same or a["value"] == b["value"]:
            continue
        before = [re.sub(r"[^\w']", "", str(w["w"])).lower() for w in sent[max(0, i - 2):i]]
        from_to = "from" in before and "to" in between
        return {"left": _label(a), "right": _label(b), "from_to": from_to, "mark": None if from_to else "VS",
                "s": a["s"], "e": b["e"], "stat": {"kind": "stat", **{k: b[k] for k in ("prefix", "value", "decimals", "suffix")},
                                                     "text": _label(b)}}
    return None


def _unit(st: dict) -> str:
    u = st["suffix"].strip().lower()
    fam = {"minute": "time", "minutes": "time", "hour": "time", "hours": "time", "day": "time", "days": "time",
           "week": "time", "weeks": "time", "month": "time", "months": "time", "year": "time", "years": "time",
           "seconds": "time", "million": "count", "billion": "count", "thousand": "count", "k": "count", "m": "count"}
    return fam.get(u, u)


def _sentences(words: list[dict]) -> list[list[dict]]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if str(w["w"]).strip().endswith((".", "!", "?")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _layout_at(sidecar: list[dict] | None, t: float) -> dict:
    for s in sidecar or []:
        if float(s.get("start", 0)) <= t < float(s.get("end", 0)):
            return s
    return (sidecar or [{}])[-1] if sidecar else {}


def _norm(w) -> str:
    return re.sub(r"[^\w%$€£.']", "", str(w or "")).lower().strip(".")


def find_phrase(words: list[dict], text: str, nth: int = 0) -> int | None:
    """The index of the nth time the phrase `text` is said in `words` (word by word, punctuation ignored), or None."""
    want = [_norm(t) for t in str(text or "").split() if _norm(t)]
    if not want:
        return None
    toks = [_norm(w.get("w")) for w in words]
    hits = [i for i in range(len(toks) - len(want) + 1) if toks[i:i + len(want)] == want]
    return hits[nth] if 0 <= nth < len(hits) else (hits[-1] if hits else None)


def chosen(words: list[dict], clip_start: float, picks: list[dict]) -> dict:
    """The person's (or the director's) own picks, in place of the automatic plan (the edit room, 26 Nov 2026): each
    {kind: stat | line, text: the words said, nth: which time they're said}. A stat pick on a phrase that isn't a number
    becomes a kinetic line. Nothing is invented: a pick whose words aren't said in the clip is skipped, with why."""
    ws = [w for w in words or [] if str(w.get("w", "")).strip()]
    fx, skipped = [], []
    for p in picks[:4]:
        i = find_phrase(ws, p.get("text") or "", int(p.get("nth") or 0))
        if i is None:
            skipped.append(f"\"{str(p.get('text'))[:40]}\" isn't said in this clip any more, so it was left out.")
            continue
        n = max(1, len(str(p.get("text") or "").split()))
        st = _stat_at(ws, i) if p.get("kind") == "stat" else None
        t = float(ws[i]["s"]) - clip_start
        if st:
            label = _label(st)
            fx.append({"kind": "stat", "s": round(t, 3), "e": round(t + STAT_S, 3), "text": label,
                       **{k: st[k] for k in ("prefix", "value", "decimals", "suffix")}, "why": "Picked in the edit room.", "picked": True})
        else:
            sent = ws[i:i + n]
            e = float(sent[-1]["e"]) - clip_start
            fx.append({"kind": "line", "s": round(t, 3), "e": round(max(e, t + 0.6) + 0.35, 3), "text": " ".join(str(w["w"]).strip() for w in sent),
                       "words": [{"w": str(w["w"]).strip(), "s": round(float(w["s"]) - clip_start, 3)} for w in sent],
                       "why": "Picked in the edit room.", "picked": True})
    if not picks:
        skipped.append("No motion graphics on this clip (chosen in the edit room).")
    return {"on": True, "fx": sorted(fx, key=lambda f: f["s"]), "skipped": skipped, "chosen": True}


def plan(words: list[dict], clip_start: float, sidecar: list[dict] | None = None, shots: list[dict] | None = None,
         captions: bool = True, picks: list[dict] | None = None) -> dict:
    """{"fx": [{kind, s, e (clip seconds), text, ...why}], "skipped": [why]} for one clip. picks (a list, may be empty):
    the edit room's own choice replaces the automatic one (see chosen)."""
    if picks is not None and captions:
        return chosen(words, clip_start, picks)
    fx, skipped = [], []
    if not enabled():
        return {"on": False, "fx": [], "skipped": ["Motion moments aren't switched on on this server (HQ_MOTION)."]}
    if not captions:
        return {"on": True, "fx": [], "skipped": ["The video keeps its own burned-in captions, so no motion text was added."]}
    ws = [w for w in words or [] if str(w.get("w", "")).strip()]
    rules = {}
    for s in shots or []:
        rules[(round(float(s.get("start", 0)), 2), round(float(s.get("end", 0)), 2))] = s.get("rule")

    def blocked(t: float) -> str | None:
        if t < MIN_START:
            return "too early: the hook owns the opening seconds"
        seg = _layout_at(sidecar, t)
        if seg.get("layout") in NO_FX_LAYOUTS:
            return f"the {seg.get('layout')} layout is on screen: text would cover what's shown"
        for (a, b), r in rules.items():
            if a <= t < b and r in NO_FX_RULES:
                return "a screen recording is on screen: text would cover what's shown"
        if any(abs(t - f["s"]) < MIN_GAP for f in fx):
            return "too close to another effect"
        return None

    # a visual comparison: two figures of the same kind in one sentence, joined by "to" / "vs" / "instead of" ...
    # ("from 3 hours to 20 minutes"). Needs the template renderer (HQ_TEMPLATES=1); otherwise the stat pop below.
    if templates_on():
        for sent in _sentences(ws):
            cmp_ = _compare_in(sent)
            if not cmp_:
                continue
            t = cmp_["s"] - clip_start
            why = blocked(t)
            if why:
                skipped.append(f"Comparison \"{cmp_['left']} / {cmp_['right']}\" at {t:.1f} s left as a caption: {why}.")
                continue
            fx.append({"kind": "compare", "s": round(t, 3), "e": round(max(cmp_["e"] - clip_start, t) + COMPARE_HOLD, 3),
                       "text": f"{cmp_['left']} {cmp_['mark'] or '/'} {cmp_['right']}", **{k: cmp_[k] for k in ("left", "right", "from_to", "mark")},
                       "fallback": cmp_["stat"],
                       "why": f"Two figures side by side (\"{cmp_['left']}\" and \"{cmp_['right']}\"): a comparison card shows the change at a glance."})
            break
    # stat pops: the first number with a unit that has room
    for i in range(len(ws)):
        st = _stat_at(ws, i)
        if not st:
            continue
        t = st["s"] - clip_start
        if any(f["kind"] == "compare" and f["s"] - 0.2 <= t <= f["e"] for f in fx):
            continue   # already on the comparison card
        why = blocked(t)
        label = _label(st)
        if why:
            skipped.append(f"Stat \"{label}\" at {t:.1f} s left as a caption: {why}.")
            continue
        fx.append({"kind": "stat", "s": round(t, 3), "e": round(t + STAT_S, 3), "text": label, **{k: st[k] for k in ("prefix", "value", "decimals", "suffix")},
                   "why": f"\"{label}\" is a concrete number: counted up so it lands."})
        break
    # one kinetic line: a short sentence with a turn word or an exclamation
    for sent in _sentences(ws):
        toks = [str(w["w"]).strip() for w in sent]
        low = [re.sub(r"[^\w']", "", t).lower() for t in toks]
        if not (2 <= len(toks) <= 7) or not (set(low) & TURN or toks[-1].endswith("!")):
            continue
        if toks[-1].endswith("?") or low[0] in FILLER_OPEN:
            continue   # a question or a lead-in ("So when you broke in?") sets up the point; it isn't the point
        t, e = float(sent[0]["s"]) - clip_start, float(sent[-1]["e"]) - clip_start
        if e - t > LINE_MAX_S:
            continue
        why = blocked(t)
        if why:
            skipped.append(f"Line \"{' '.join(toks)}\" at {t:.1f} s left as a caption: {why}.")
            continue
        if any(f["s"] - 0.2 <= t <= f["e"] + 0.2 or t <= f["s"] <= e for f in fx):
            continue
        fx.append({"kind": "line", "s": round(t, 3), "e": round(e + 0.35, 3), "text": " ".join(toks),
                   "words": [{"w": str(w["w"]).strip(), "s": round(float(w["s"]) - clip_start, 3)} for w in sent],
                   "why": "A short, punchy line: slammed in word by word so it hits."})
        break
    if not fx and not skipped:
        skipped.append("Nothing in this clip needed it: no number with a unit and no short punchy line.")
    return {"on": True, "fx": sorted(fx, key=lambda f: f["s"]), "skipped": skipped[:6]}


def _time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(s: str) -> str:
    return str(s).replace("\\", "").replace("{", "(").replace("}", ")")


def events(fx: list[dict], y_at, W: int, accent_ass: str, text_ass: str, upper: bool = True, fit=None) -> list[str]:
    """ASS Dialogue lines (style Cap) for the planned effects. y_at(t) -> the caption band's y at clip time t;
    fit(text, scale) -> the scale (1.0 = caption size) at which text still fits the frame width."""
    fit = fit or (lambda text, scale: scale)
    out = []
    for f in fx:
        if f.get("template"):
            continue   # drawn by an OpenFilm template overlay instead (studio/media/openfilm, render.render)
        y = y_at(f["s"] + 0.01)
        if f["kind"] == "stat":
            big = int(100 * fit(f["text"].upper() if upper else f["text"], STAT_SCALE))
            steps = COUNT_STEPS
            for k in range(steps + 1):  # the count-up: one short event per step, then the held figure
                a = f["s"] + COUNT_MS / 1000 * k / steps
                b = f["s"] + COUNT_MS / 1000 * (k + 1) / steps if k < steps else f["e"]
                v = f["value"] * (1 - (1 - k / steps) ** 3)  # ease out: fast, then settles on the real figure
                txt = f"{f['prefix']}{v:,.{f['decimals']}f}{f['suffix']}" if k < steps else f["text"]
                grow = f"\\fscx{int(big * 0.85)}\\fscy{int(big * 0.85)}\\t(0,120,\\fscx{big}\\fscy{big})" if k == 0 else f"\\fscx{big}\\fscy{big}"
                fade = "\\fad(90,0)" if k == 0 else ("\\fad(0,180)" if k == steps else "")
                out.append(f"Dialogue: 1,{_time(a)},{_time(b)},Cap,,0,0,0,,{{\\an5\\pos({W // 2},{y}){fade}{grow}\\1c{accent_ass}}}"
                           f"{_esc(txt.upper() if upper else txt)}")
        elif f["kind"] == "line":
            ws = f.get("words") or []
            toks = [_esc(w["w"].upper() if upper else w["w"]) for w in ws]
            rows, cur = [], []
            for t in toks:  # stacked rows that fit the frame at LINE_SCALE (a long line no longer shrinks to caption size)
                if cur and fit(" ".join(cur + [t]), LINE_SCALE) < LINE_SCALE - 1e-3:
                    rows.append(cur)
                    cur = []
                cur.append(t)
            if cur:
                rows.append(cur)
            big = int(100 * min(fit(" ".join(r), LINE_SCALE) for r in rows))
            row_h = int(ROW_H * big / 100)
            y = min(y, BOTTOM_SAFE - row_h * len(rows) // 2)   # the whole stack stays above the platform UI
            body, k = [], 0
            for ri, r in enumerate(rows):
                for j, t in enumerate(r):  # each word flips in as it's said (a rotation keeps its width: no reflow)
                    w = ws[k]
                    k += 1
                    at = int(max(0.0, w["s"] - f["s"]) * 1000)
                    col = accent_ass if re.sub(r"[^\w']", "", w["w"]).lower() in TURN else text_ass
                    body.append(f"{{\\alpha&HFF&\\1c{col}\\frx75\\t({at},{at + 1},\\alpha&H00&)\\t({at},{at + 170},0.6,\\frx0)}}{t}"
                                + (" " if j < len(r) - 1 else ""))
                if ri < len(rows) - 1:
                    body.append("\\N")
            out.append(f"Dialogue: 1,{_time(f['s'])},{_time(f['e'])},Cap,,0,0,0,,{{\\an5\\pos({W // 2},{y})\\fad(60,200)"
                       f"\\fscx{big}\\fscy{big}}}{''.join(body)}")
    return out


# ---------------------------------------------------------------- sound accents
SFX_DB = -20.0          # under the voice: felt more than heard


def sfx_graph(fx: list[dict], src: str, out: str) -> list[str]:
    """ffmpeg filtergraph lines mixing a short accent under each motion moment into the audio `src` -> `out`. Made from
    lavfi generators (our own sound, nothing downloaded or licensed): a stat gets a soft rising whoosh into a tick, a
    kinetic line a low, short thump on its first word."""
    parts, labels = [], []
    g = 10 ** (SFX_DB / 20)
    for i, f in enumerate(fx[:4]):
        ms = max(0, int(float(f["s"]) * 1000))
        if f.get("kind") in ("stat", "compare"):
            parts.append(f"anoisesrc=d=0.32:c=pink:a=1,highpass=f=900,lowpass=f=6000,afade=t=in:d=0.24,afade=t=out:st=0.24:d=0.08,"
                         f"aformat=sample_rates=44100:channel_layouts=stereo,volume={g * 4.5:.4f},adelay={max(0, ms - 240)}|{max(0, ms - 240)}[sx{i}a]")
            parts.append(f"sine=f=1760:d=0.06,afade=t=out:d=0.06,aformat=sample_rates=44100:channel_layouts=stereo,"
                         f"volume={g * 5.0:.4f},adelay={ms + 80}|{ms + 80}[sx{i}b]")
            labels += [f"[sx{i}a]", f"[sx{i}b]"]
        else:
            parts.append(f"sine=f=70:d=0.22,afade=t=out:st=0.03:d=0.19,aformat=sample_rates=44100:channel_layouts=stereo,"
                         f"volume={g * 9.0:.4f},adelay={ms}|{ms}[sx{i}a]")
            labels.append(f"[sx{i}a]")
    if not labels:
        return [f"{src}anull{out}"]
    parts.append(f"{src}{''.join(labels)}amix=inputs={1 + len(labels)}:duration=first:normalize=0{out}")
    return parts
