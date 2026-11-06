"""What each network calls a video post (owner, 6 Nov 2026: every channel chip said "Reel").

One source for the words people see on a video post, per channel. `studio/static/house.js` keeps the same table
(VIDEO_NAMES / videoName); tests/studio/test_video_names.py keeps the two in step.

- Instagram publishes every feed video as a Reel (Buffer's InstagramPostMetadataInput.type = reel); story = Story.
- Facebook: a 9:16 video goes as a Reel (fb_type reel -> PostTypeFacebook reel); any other ratio goes to the Page feed
  as a video (type post).
- YouTube: a vertical or square video up to 3 minutes becomes a Short; anything else is a video. Buffer has no
  YouTube post type: YouTube decides from the file, so this label only describes what will happen.
- LinkedIn, TikTok, X, Threads, Bluesky, Mastodon, Google Business: a video. Pinterest: a video Pin.
"""
from __future__ import annotations

VIDEO_NAMES = {"instagram": "Reel", "facebook": "Video", "youtube": "Video", "tiktok": "Video", "linkedin": "Video",
               "twitter": "Video", "x": "Video", "threads": "Video", "bluesky": "Video", "mastodon": "Video",
               "pinterest": "Video Pin", "googlebusiness": "Video"}
SHORT_MAX_S = 180  # YouTube Shorts: up to 3 minutes, vertical or square


def _vertical(ratio: str) -> bool:
    try:
        w, h = (float(x) for x in str(ratio or "").split(":"))
        return w / h <= 1.0
    except (ValueError, ZeroDivisionError):
        return False


def video_name(service: str, ratio: str = "", options: dict | None = None, duration: float | None = None) -> str:
    """The network's own word for a video post on this channel ("Reel", "Short", "Video", "Video Pin", "Story")."""
    svc, o = (service or "").lower(), options or {}
    if svc == "instagram":
        return "Story" if o.get("ig_type") == "story" else "Reel"
    if svc == "facebook":
        t = o.get("fb_type")  # what build_post_input sends: no fb_type = a feed video (type post)
        return {"story": "Story", "reel": "Reel"}.get(t, "Video")
    if svc == "youtube":
        try:
            secs = float(duration) if duration else 0.0
        except (TypeError, ValueError):
            secs = 0.0  # an unreadable length doesn't decide it; the ratio does
        short = (_vertical(ratio) if ratio else True) and (not secs or secs <= SHORT_MAX_S)
        return "Short" if short else "Video"
    return VIDEO_NAMES.get(svc, "Video")


def facebook_reel(ratio: str) -> bool:
    """Facebook takes a Reel in 9:16 only; a 4:5 or 16:9 video is a feed video."""
    return (ratio or "9:16") == "9:16"


NETWORK_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube", "tiktok": "TikTok",
                 "linkedin": "LinkedIn", "twitter": "X", "x": "X", "threads": "Threads", "bluesky": "Bluesky",
                 "mastodon": "Mastodon", "pinterest": "Pinterest", "googlebusiness": "Google Business"}


def phrase(service: str, name: str | None = None) -> str:
    """'Instagram Reel', 'YouTube Short', 'LinkedIn video', 'Pinterest video Pin' for a sentence."""
    name = name or video_name(service)
    word = name.replace("Video", "video")
    return f"{NETWORK_NAMES.get((service or '').lower(), (service or '').title())} {word}"
