"""Live smoke of the free X reader on one public post: each layer's status only (no post text, no keys).

    python scripts/x_reader_smoke.py [https://x.com/<handle>/status/<id>] [--ytdlp]

Exit 0 when the cascade reads the post (words + at least one media URL), 1 otherwise.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from studio import xreader  # noqa: E402
from studio.reader import x_status  # noqa: E402

DEFAULT = "https://x.com/AarushYadav47/status/2116198463847408066"


def main(argv: list[str]) -> int:
    url = next((a for a in argv if a.startswith("http")), DEFAULT)
    handle, sid = x_status(url)
    out = {}
    for name, fn in (("oembed", lambda: xreader.layer_oembed(handle, sid)), ("syndication", lambda: xreader.layer_syndication(sid)),
                     ("fxtwitter", lambda: xreader.layer_fxtwitter(sid))):
        try:
            got = fn()
            out[name] = f"ok (text={bool(got.get('text'))}, videos={len(got.get('videos') or [])}, photos={len(got.get('photos') or [])})"
        except xreader.XReadError as e:
            out[name] = f"FAILED: {e}"
    if "--ytdlp" in argv:
        try:
            import yt_dlp
            opts = {"quiet": True, "skip_download": True, "allowed_extractors": ["Twitter"], "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=False)
            out["yt-dlp"] = f"ok (formats={len(info.get('formats') or [])}, duration={info.get('duration')})"
        except Exception as e:  # noqa: BLE001 - status only
            out["yt-dlp"] = f"FAILED: {e.__class__.__name__}"
    cascade = xreader.read(handle, sid)
    out["cascade"] = f"text={bool(cascade['text'])}, videos={len(cascade['videos'])}, media_via={cascade['media_via'] or '-'}"
    for k, v in out.items():
        print(f"{k:12} {v}")
    return 0 if cascade["text"] and (cascade["videos"] or cascade["photos"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
