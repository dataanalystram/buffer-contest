"""Set up the private R2 media bucket: CORS for our origin only, and lifecycle rules (docs/STORAGE.md#r2).

    python scripts/r2_bucket_setup.py                      # dry run: prints the CORS and lifecycle documents, sends nothing
    python scripts/r2_bucket_setup.py --apply              # PUT ?cors and ?lifecycle on R2_BUCKET (needs R2_* and PUBLIC_URL)

What it sets (nothing else; it never creates, deletes or lists objects):

* CORS: only ``PUBLIC_URL``'s origin may GET/HEAD (previews, seeking through a presigned URL) and PUT (direct uploads
  through a presigned URL); ``ETag`` is exposed so a multipart upload can complete. No wildcard origin.
* Lifecycle: every incomplete multipart upload is aborted after 1 day; objects under ``tmp/`` (render intermediates)
  expire after ``--tmp-days`` (default 7). No rule touches ``media/``, ``lib/`` or ``takes/``: user originals are
  never deleted by the bucket.

Keep the bucket private: no public ``r2.dev`` URL and no custom domain on it. Every read goes through HuhQueue's own
routes (ownership checked) or a short-lived presigned URL HuhQueue signs after that check.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio import r2  # noqa: E402


def origin(public_url: str) -> str:
    u = urlparse(public_url or "")
    if u.scheme != "https" or not u.hostname:
        raise SystemExit("PUBLIC_URL must be the app's https URL (it is the only CORS origin).")
    port = f":{u.port}" if u.port and u.port != 443 else ""
    return f"https://{u.hostname}{port}"


def cors_xml(org: str) -> str:
    return ("<CORSConfiguration><CORSRule>"
            f"<AllowedOrigin>{org}</AllowedOrigin>"
            "<AllowedMethod>GET</AllowedMethod><AllowedMethod>HEAD</AllowedMethod><AllowedMethod>PUT</AllowedMethod>"
            "<AllowedHeader>content-type</AllowedHeader><AllowedHeader>range</AllowedHeader>"
            "<ExposeHeader>ETag</ExposeHeader><ExposeHeader>Content-Range</ExposeHeader><ExposeHeader>Accept-Ranges</ExposeHeader>"
            "<MaxAgeSeconds>3600</MaxAgeSeconds>"
            "</CORSRule></CORSConfiguration>")


def lifecycle_xml(tmp_days: int) -> str:
    return ("<LifecycleConfiguration>"
            "<Rule><ID>abort-incomplete-multipart-1d</ID><Status>Enabled</Status><Filter><Prefix></Prefix></Filter>"
            "<AbortIncompleteMultipartUpload><DaysAfterInitiation>1</DaysAfterInitiation></AbortIncompleteMultipartUpload></Rule>"
            f"<Rule><ID>expire-tmp-{int(tmp_days)}d</ID><Status>Enabled</Status><Filter><Prefix>tmp/</Prefix></Filter>"
            f"<Expiration><Days>{int(tmp_days)}</Days></Expiration></Rule>"
            "</LifecycleConfiguration>")


def _put(cfg: dict, sub: str, body: str, http=None) -> int:
    import httpx
    data = body.encode()
    url = r2.presign(cfg, "PUT", "", {sub: ""}, 300, length=len(data))
    c = http or httpx.Client(timeout=30, trust_env=False)
    r = c.put(url, content=data, headers={"content-type": "application/xml",
                                          "content-md5": base64.b64encode(hashlib.md5(data).digest()).decode()})
    return r.status_code


def main(argv: list[str] | None = None, http=None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/r2_bucket_setup.py", description="CORS + lifecycle for the private R2 media bucket")
    ap.add_argument("--apply", action="store_true", help="send the two documents (default: print them only)")
    ap.add_argument("--tmp-days", type=int, default=7, help="days before objects under tmp/ expire (1-365)")
    a = ap.parse_args(argv)
    if not 1 <= a.tmp_days <= 365:
        print("--tmp-days must be between 1 and 365", file=sys.stderr)
        return 2
    org = origin(os.environ.get("PUBLIC_URL", ""))
    cors, life = cors_xml(org), lifecycle_xml(a.tmp_days)
    print("CORS:", cors, "\nLifecycle:", life, sep="\n")
    if not a.apply:
        print("\nDry run: nothing sent. Add --apply to set them.")
        return 0
    cfg = r2.config()
    if not cfg:
        print("R2 is not configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET).", file=sys.stderr)
        return 2
    codes = {"cors": _put(cfg, "cors", cors, http), "lifecycle": _put(cfg, "lifecycle", life, http)}
    print(codes)  # status codes only: never a signed URL
    return 0 if all(c < 300 for c in codes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
