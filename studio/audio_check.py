"""Buffer's own check of an Instagram track before it is saved or sent (studio/reel_audio.py, Studio._validate).

Kept free of FastAPI: actions.py (which the render worker imports) calls it.
"""
from __future__ import annotations

import os
import re

from .actions import ActionError, Actor
from .buffer_api import BufferError

NEEDS_FACEBOOK = "Connect Instagram through a Facebook Page in Buffer to add music."
# Buffer error codes that say nothing about the track itself (sign-in, scope, rate limits, outages): never "unloadable"
NOT_ABOUT_TRACK = ("FORBIDDEN", "UNAUTH", "RATE_LIMIT", "TOO_MANY", "INTERNAL", "TIMEOUT", "UNAVAILABLE", "VALIDATION_FAILED")
CHECK_TTL = 600  # one Buffer check per track and channel every 10 minutes, whoever asks
UNLOADABLE = ("Buffer can't load “{title}” for this Instagram account (Buffer shows it as “Couldn't load track” and "
              "Instagram won't attach it), so it isn't used. Pick another track: licensed music usually works best.")


def check_track(studio, a: Actor, channel_id: str, au: dict, fresh: bool = False) -> dict:
    """Buffer's own check of a picked track (instagramAudio), owner 9 Nov 2026: "Gifts to your future self" (an original
    sound from the trending list) went to Buffer, which then showed "Couldn't load track" and couldn't post it.
    {"state": "ok", "audio": refreshed} | {"state": "unavailable", "why"} | {"state": "needs_facebook"} |
    {"state": "unknown"} (Buffer busy or unreachable now: nothing is decided, the track stays). fresh: ask Buffer now, not
    the 10-minute answer (the person asked about the music)."""
    buf = studio.buffer
    fn = getattr(buf, "instagram_audio", None)
    aid = str((au or {}).get("audio_id") or "")
    if not fn or not aid or os.environ.get("HQ_AUDIO_CHECK", "1") == "0":  # kill switch: send tracks unchecked, as before
        return {"state": "unknown"}

    def ask():
        try:
            return {"state": "ok", **fn(a.uid, channel_id, aid)}
        except BufferError as e:
            # only Buffer's own GraphQL answer about this track counts (gql raises those as "Buffer could not complete the
            # request (CODE: message)"); rate limits, HTTP 5xx, sign-in and scope problems say nothing about the track
            m = str(e)
            code = (re.match(r"Buffer could not complete the request \(([^:]*):", m) or [None, ""])[1].strip().upper()
            if type(e) is not BufferError or not code or code.isdigit() or "Cannot query field" in m \
                    or any(w in code for w in NOT_ABOUT_TRACK):
                raise  # never cached
            return {"state": "unavailable", "why": m[:300]}
    key = ("audio_ok", a.uid, channel_id, aid)
    if fresh and hasattr(buf, "_cache"):
        buf._cache.pop(key, None)
    try:
        out = buf._cached(key, CHECK_TTL, ask) if hasattr(buf, "_cached") else ask()
    except Exception:  # noqa: BLE001 - rate limited, Buffer down, network: say nothing about the track
        return {"state": "unknown"}
    if out.get("needs_facebook"):
        return {"state": "needs_facebook"}
    return out


def ensure_playable(studio, a: Actor, channel_id: str, au: dict) -> dict:
    """Refuse a track Buffer can't load before anything is sent (a draft with "Couldn't load track" is what the person saw).
    Returns the track as picked (Buffer's answer decides whether it can be used; only its fresh preview link is taken)."""
    chk = check_track(studio, a, channel_id, au)
    if chk["state"] == "unavailable":
        raise ActionError(UNLOADABLE.format(title=au.get("title") or "that track"), 409)
    if chk["state"] == "needs_facebook":
        raise ActionError("Buffer can't add music to this Instagram channel. " + NEEDS_FACEBOOK, 409)
    pv = str((chk.get("audio") or {}).get("previewUrl") or "")
    if pv.startswith("https://") and len(pv) <= 2000 and au.get("preview_url") != pv:
        return {**au, "preview_url": pv}  # Buffer's fresh preview link, for the card's "hear it with the video"
    return au
