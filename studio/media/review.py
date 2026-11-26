"""A short editorial review of one rendered One take clip: what works, what looks wrong, why, and the edit that fixes it.

Every point is read from what the render actually measured (the per-scene layout plan and its rules, face coverage, the
hook, the transcript, trims, Words behind you, the screen text OCR); nothing is guessed and no score is invented. A check
whose measurement is missing is skipped, not passed. The points follow the editorial skills (plans/editorial/skills:
hook-treatments, story-and-emotion, framing-and-visual-story, motion-and-typography) as plain rules of thumb.
"""
from __future__ import annotations

import re

SPEECH_WPS = 2.6        # spoken words per second, to place the hook line in time from its word offset
HOOK_LATE_S = 6.0       # a hook first heard later than this (take.HOOK_WINDOW) doesn't open the clip
COVERAGE_OK = 0.85      # a tracked face in frame less than this share of the time drifts out of the crop
SHORT_S, LONG_S = 15.0, 75.0
MAX_SWITCHES = 4        # layout changes in one clip before it reads as restless

_WEAK_END = ("and", "but", "so", "because", "or", "the", "a", "to", "of", "that", "which", "like")


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def _fmt(t: float) -> str:
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


def review(clip: dict, meta: dict | None = None) -> dict:
    """{"works": [str], "issues": [{"what", "why", "fix"}], "checked": [str]} for one clip view (takes.view's clip dict)."""
    meta = meta or {}
    works, issues, checked = [], [], []

    def issue(what: str, why: str, fix: str) -> None:
        issues.append({"what": what, "why": why, "fix": fix})

    text = str(clip.get("transcript") or clip.get("text") or "")
    co = clip.get("cold_open") if isinstance(clip.get("cold_open"), dict) else None
    if co and co.get("text") and (meta.get("cold_open") or not meta):   # the opening line was joined in front
        text = f"{co['text']} {text}"
    hook = str(clip.get("hook") or "")
    tw, hw = _words(text), _words(hook)

    # ---- rights first: another creator's watermark on screen
    wm = clip.get("watermarks") or []
    if wm:
        checked.append("watermarks")
        issue(f"Someone else's handle shows on screen ({', '.join(wm[:2])}).", "Posting another creator's watermark can break platform rules.",
              "Crop it out in Layout, or use footage you own.")

    # ---- the hook (hook-treatments): heard in the first seconds, and saying something on its own
    if tw and hw:
        checked.append("hook")
        at = next((i for i in range(len(tw) - len(hw) + 1) if tw[i:i + len(hw)] == hw), None)
        if at is None:
            issue("The cover line isn't in what's said.", "A hook that quotes words nobody says reads as clickbait.",
                  "Pick a line from the clip as the hook (Edit > Hook), or mark it as a headline.")
        elif at / SPEECH_WPS > HOOK_LATE_S:
            issue(f"The hook line is said about {at / SPEECH_WPS:.0f} s in, not at the start.",
                  "The viewer reads one line on the cover and hears another, so the opening feels off.",
                  f"Start the clip at the hook (Trim: start about {at / SPEECH_WPS:.0f} s later) or pick the opening line as the hook.")
        else:
            works.append("The hook is the line the viewer hears first.")
        if hw and hw[0] in ("it", "they", "that", "this", "he", "she", "them") and len(hw) < 8:
            issue(f"The hook opens on \"{hw[0]}\" without saying what it is.", "Out of context the cover line doesn't say what the clip is about.",
                  "Choose a hook line that names the subject.")

    # ---- the story (story-and-emotion): starts and ends on whole thoughts
    if tw:
        checked.append("story")
        last = text.strip()
        if last and not last.endswith((".", "!", "?")):
            issue("The clip stops mid-sentence.", "Ending mid-thought feels like a cut-off, not a payoff.",
                  "Extend the end to the end of the sentence (Trim), or end one sentence earlier.")
        elif last.endswith("?"):
            issue("The clip ends on a question.", "The viewer is left without the answer.", "Extend the end to include the answer.")
        elif tw[-1] in _WEAK_END:
            issue(f"The last word is \"{tw[-1]}\".", "It trails into the next thought.", "End one word earlier or finish the sentence.")
        else:
            works.append("It ends on a complete sentence.")
        if tw[0] in ("it", "they", "that", "this", "which") and len(tw) > 3:
            issue(f"The clip opens on \"{tw[0]}\", which points back to something before it.",
                  "Someone who didn't watch the full video won't know what it refers to.",
                  "Start a sentence or two earlier so the setup is included (Trim).")
    dur = clip.get("duration")
    if isinstance(dur, (int, float)):
        checked.append("length")
        if dur < SHORT_S:
            issue(f"It's only {dur:.0f} s long.", "Very short clips rarely carry a full idea.", "Extend it to the end of the thought.")
        elif dur > LONG_S:
            issue(f"It runs {dur:.0f} s.", "Past about a minute the drop-off on Shorts and Reels gets steep.",
                  "Trim the middle (pauses and fillers) or split it into two clips.")

    # ---- framing (framing-and-visual-story): the plan the render used, scene by scene
    plan = [s for s in clip.get("layout_plan") or [] if isinstance(s, dict)]
    if plan:
        checked.append("framing")
        span = lambda s: max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))  # noqa: E731
        total = sum(span(s) for s in plan) or 1.0
        fit = sum(span(s) for s in plan if s.get("layout") == "fit")
        two = [s for s in plan if s.get("layout") == "fit" and str(s.get("rule") or "").split("+")[0] == "R8"
               and span(s) >= 2.0]
        small = [s for s in plan if str(s.get("rule") or "").split("+")[0] in ("R4", "R3-upscale", "R5-upscale")]
        if two:
            # R8 is two (or more) people too far apart for one 9:16 crop, or faces too unsure to crop: pinning Track
            # would cut one of them out (26 Nov Mac run: the notes said "too small" on the Waid podcast)
            s = two[0]
            # R8 also covers a crowd and faces too unsure to crop (26 Nov Mac run: "two people" on a group of six)
            issue(f"The wide shot stays full frame ({_fmt(s['start'])}-{_fmt(s['end'])}), so the people in it look small.",
                  "More than one person is in it, too far apart or too unsure to crop to 9:16 without cutting someone out.",
                  "If two people are talking, switch that scene to Split in Layout; for a group, keep the wide shot.")
        elif small:
            s = small[0]
            issue(f"The speaker stays small in a full-frame shot ({_fmt(s['start'])}-{_fmt(s['end'])}).",
                  "The face was too small or too unsure to crop to 9:16 without going soft, so the frame shows the whole wide shot.",
                  "Pin that scene to Track in Layout if the face is clear enough, or accept the wide shot as context.")
        if not two and not small and fit / total > 0.8 and not any(s.get("rule") in ("R2", "R8-noface") for s in plan):
            issue("Most of the clip is the full wide frame.", "On a phone the people are small; the clip feels like a landscape video.",
                  "Pin the main scene to Track in Layout.")
        switches = sum(1 for a, b in zip(plan, plan[1:]) if a.get("layout") != b.get("layout"))
        if switches > MAX_SWITCHES:
            issue(f"The layout changes {switches} times.", "Frequent jumps between crops are tiring to watch.",
                  "Pin one layout for the busy stretch in Layout.")
        if any(s.get("layout") == "track" for s in plan):
            cov = clip.get("face_coverage")
            if isinstance(cov, (int, float)) and cov < COVERAGE_OK:
                issue(f"The face is in frame {cov * 100:.0f}% of the time.", "The crop loses the speaker when they move.",
                      "Switch the moving scene to full frame or Screen + face in Layout.")
            elif isinstance(cov, (int, float)):
                works.append(f"The speaker stays in frame ({cov * 100:.0f}% of the time).")

    if co and meta.get("cold_open"):
        checked.append("cold open")
        works.append(f"Opens on \"{co.get('text', '')[:70]}\", said earlier in the video, then cuts to the moment.")
    # ---- polish: trims, the hook effect, captions
    tr = clip.get("trim") or {}
    if isinstance(tr, dict) and tr.get("trimmed_s"):
        works.append(f"{tr['trimmed_s']:.1f} s of pauses and fillers were cut.")
    fx = meta.get("hook_fx") if isinstance(meta.get("hook_fx"), dict) else None
    if fx:
        checked.append("hook effect")
        if fx.get("on"):
            works.append(f"\"{fx.get('text', '')}\" sits behind the speaker in the opening seconds.")
        elif fx.get("why"):
            works.append(f"Words behind you was left off: {fx['why']}")
    mo = meta.get("motion") if isinstance(meta.get("motion"), dict) else None
    if mo and mo.get("on"):
        checked.append("motion")
        for f in mo.get("fx") or []:
            works.append(f"{'Animated stat' if f.get('kind') == 'stat' else 'Kinetic line'} \"{f.get('text', '')}\": {f.get('why', '')}")
    if clip.get("source_captions"):
        works.append("The video's own burned-in captions are kept, with no second layer on top.")
    return {"works": works[:6], "issues": issues[:6], "checked": checked}
