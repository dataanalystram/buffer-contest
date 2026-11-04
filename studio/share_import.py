"""Import a photo or video from a share link: Google Drive, Dropbox, OneDrive / SharePoint, iCloud Drive, or any direct
https link (owner bug, 2 Nov 2026: Claude.ai's sandbox can't reach our one-time PUT URL, and 2 MB base64 chunks of a
27 MB video are too much for a chat, so the file has to come in some other way).

``direct(url)`` turns a share page into the URL that returns the file itself:

* Google Drive  ``drive.google.com/file/d/<id>/view``, ``open?id=``, ``uc?id=``, ``docs.google.com/uc?id=``
                -> ``drive.usercontent.google.com/download?id=<id>&export=download&confirm=t``. Drive answers a large file
                (it can't virus-scan it) with a "Download anyway" page; ``Importer`` reads that page's form (id, export,
                confirm, uuid) and follows it once. A page that asks for a sign-in means the file isn't shared publicly.
* Dropbox       ``dropbox.com/s/...``, ``/scl/fi/...?rlkey=...`` -> the same link with ``dl=1`` (keeps ``rlkey``).
* OneDrive      ``1drv.ms/...``, ``onedrive.live.com/...`` -> the shares API ``api.onedrive.com/v1.0/shares/u!<b64>/root/content``;
  SharePoint    ``<tenant>.sharepoint.com/:v:/...`` -> the link with ``download=1``.
* iCloud Drive  ``icloud.com/iclouddrive/<id>`` -> CloudKit's public ``records/resolve`` answer gives the download URL.

Folders are refused with a plain message. Everything streams through netguard (every redirect hop's host is checked,
private addresses refused) into the Library's own writer (MIME sniffed from the bytes, the per-file cap and the person's
quota enforced while it streams), so a link import is exactly as safe as an upload and lands in the same Library.
"""
from __future__ import annotations

import base64
import html as htmlmod
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import httpx
from fastapi import Body, Request

from . import demo_guard, netguard
from .actions import ActionError, Actor

UA = {"User-Agent": "Mozilla/5.0 (compatible; HuhQueue/1.0; +https://huhqueue-studio.onrender.com)"}
TIMEOUT = 30            # seconds per read; a 100 MB file from Drive takes ~5-20 s on Render
PAGE_MAX = 2 * 1024 * 1024

FOLDER = "That's a folder link. Share the video file itself (open the file, then Share > Copy link)."
NOT_PUBLIC = {
    "drive": "Google Drive asked for a sign-in, so this file isn't public. In Drive: Share > General access > "
             "\"Anyone with the link\" (Viewer), then send the link again.",
    "dropbox": "Dropbox asked for a sign-in, so this link isn't public. In Dropbox: Share > Copy link (\"Anyone with the link\").",
    "onedrive": "OneDrive asked for a sign-in, so this link isn't public. In OneDrive: Share > \"Anyone with the link can view\" > Copy.",
    "icloud": "iCloud didn't share this file publicly. In iCloud Drive: Share > Share File > \"Anyone with the link\" > Copy Link.",
    "": "The link answered with a web page (probably a sign-in or preview page), not the file. Use a direct or public share link.",
}


def _host(u: str) -> str:
    return (urlparse(u).hostname or "").lower()


def provider(url: str) -> str:
    h = _host(url)
    if h in ("drive.google.com", "docs.google.com", "drive.usercontent.google.com"):
        return "drive"
    if h == "dropbox.com" or h.endswith(".dropbox.com") or h == "dl.dropboxusercontent.com":
        return "dropbox"
    if h in ("1drv.ms", "onedrive.live.com") or h.endswith(".sharepoint.com"):
        return "onedrive"
    if h in ("icloud.com", "www.icloud.com"):
        return "icloud"
    return ""


def drive_id(url: str) -> str:
    p = urlparse(url)
    m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", p.path) or re.search(r"/d/([A-Za-z0-9_-]{10,})", p.path)
    if m:
        return m.group(1)
    ids = parse_qs(p.query).get("id")
    return ids[0] if ids and re.fullmatch(r"[A-Za-z0-9_-]{10,}", ids[0]) else ""


def _onedrive_share_url(url: str) -> str:
    """Microsoft's documented share-id encoding: "u!" + base64url(url) without padding."""
    b = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://api.onedrive.com/v1.0/shares/u!{b}/root/content"


def direct(url: str) -> tuple[str, str]:
    """(the URL that should return the file, provider) for a share link; (url, "") for anything else."""
    url = (url or "").strip()
    prov = provider(url)
    p = urlparse(url)
    if prov == "drive":
        if "/folders/" in p.path:
            raise ActionError(FOLDER, 400)
        fid = drive_id(url)
        if not fid:
            raise ActionError("That Google Drive link has no file id. Open the file in Drive, then Share > Copy link.", 400)
        return f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t", prov
    if prov == "dropbox":
        if p.path.startswith(("/sh/", "/scl/fo/")):
            raise ActionError(FOLDER, 400)
        if _host(url) == "dl.dropboxusercontent.com":
            return url, prov
        q = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items() if k not in ("dl", "raw", "preview")}
        q["dl"] = ["1"]
        return urlunparse(p._replace(scheme="https", query=urlencode(q, doseq=True))), prov
    if prov == "onedrive":
        if _host(url).endswith(".sharepoint.com"):
            if "/:f:/" in p.path:
                raise ActionError(FOLDER, 400)
            q = parse_qs(p.query, keep_blank_values=True)
            q["download"] = ["1"]
            return urlunparse(p._replace(query=urlencode(q, doseq=True))), prov
        return _onedrive_share_url(url), prov
    if prov == "icloud":
        if not re.search(r"/iclouddrive/([A-Za-z0-9_-]{10,})", p.path):
            raise ActionError("That iCloud link isn't an iCloud Drive file link (icloud.com/iclouddrive/...).", 400)
        return url, prov
    return url, ""


def _drive_confirm(page: str) -> str:
    """Drive's "can't scan this file for viruses" page: the Download anyway form, as a URL ('' when it isn't that page)."""
    m = re.search(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"(.*?)</form>', page, re.S)
    if not m:
        return ""
    action = htmlmod.unescape(m.group(1))
    if _host(action) not in ("drive.usercontent.google.com", "drive.google.com", "docs.google.com"):
        return ""
    fields = {k: htmlmod.unescape(v) for k, v in re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', m.group(2))}
    return action + "?" + urlencode(fields) if fields.get("id") else ""


def _page_says(page: str) -> str:
    low = page[:200000].lower()
    if "accounts.google.com" in low or "servicelogin" in low or ("sign in" in low and "google" in low):
        return "signin"
    if "quota exceeded" in low or "too many users have viewed or downloaded" in low:
        return "quota"
    return ""


class Importer:
    def __init__(self, house):
        self.h, self.lib = house, house.library
        self.client = lambda: netguard.client(TIMEOUT, UA)  # tests swap in a MockTransport client

    def _icloud(self, url: str, http) -> tuple[str, str]:
        """CloudKit's public resolve call for an iCloud Drive share: (download URL, file name)."""
        sid = re.search(r"/iclouddrive/([A-Za-z0-9_-]{10,})", urlparse(url).path).group(1)
        r = netguard.safe_post("https://ckdatabasews.icloud.com/database/1/com.apple.cloudkit/production/public/records/resolve",
                               http=http, json={"shortGUIDs": [{"value": sid}]}, headers=UA,
                               allowed_hosts={"ckdatabasews.icloud.com"}, timeout=TIMEOUT)
        try:
            res = (r.json().get("results") or [{}])[0]
            rec = res.get("rootRecord") or {}
            dl = rec["fields"]["fileContent"]["value"]["downloadURL"]
            name = (res.get("share") or {}).get("fields", {}).get("cloudkit.title", {}).get("value") or "icloud"
            ext = (rec.get("fields", {}).get("extension") or {}).get("value") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ActionError(NOT_PUBLIC["icloud"], 403)
        fname = f"{name}.{ext}" if ext else name
        return dl.replace("${f}", quote(fname)), fname

    def import_link(self, a: Actor, url: str, name: str = "") -> dict:
        """Stream the file behind a share or direct link into the person's Library. Returns the Library item."""
        self.h.studio._guard_write(a, "library_upload")
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace's Library is read-only. Connect Buffer to import your own files.", 403)
        url = (url or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            raise ActionError("Give the full link, starting with https://", 400)
        self.lib.check_room(a, None)
        target, prov = direct(url)
        http = self.client()
        try:
            if prov == "icloud":
                target, guessed = self._icloud(url, http)
                name = name or guessed
            return self._stream(a, target, prov, name or self._name_from(url, prov), http)
        except netguard.FetchError as e:
            msg = str(e)
            raise ActionError("That link points to a private address, so it can't be read." if "private" in msg
                              else f"Couldn't download that link: {msg}", 400)
        except httpx.TimeoutException:
            raise ActionError("The link stopped answering while downloading. Try again, or upload the file instead.", 504)
        except httpx.HTTPError as e:
            raise ActionError(f"Couldn't reach that link ({e.__class__.__name__}). Check it opens in a browser.", 502)
        finally:
            http.close()

    @staticmethod
    def _name_from(url: str, prov: str) -> str:
        last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        if prov in ("drive", "onedrive", "icloud") or "." not in last:
            return {"drive": "drive-import", "onedrive": "onedrive-import", "icloud": "icloud-import"}.get(prov, "link-import")
        return last[:80]

    def _stream(self, a: Actor, target: str, prov: str, name: str, http, hop: int = 0) -> dict:
        from .library import FILE_MAX, MB
        with netguard.safe_stream(target, http=http, headers=UA, timeout=TIMEOUT, max_hops=6) as r:
            if r.status_code in (401, 403):
                raise ActionError(NOT_PUBLIC.get(prov, NOT_PUBLIC[""]), 403)
            if r.status_code in (404, 410):
                raise ActionError("That link answered 404 (not found). Check the link, or that the file wasn't moved or deleted.", 404)
            if r.status_code >= 400:
                raise ActionError(f"Couldn't download that link: it answered HTTP {r.status_code}.", 502)
            cl = r.headers.get("content-length")
            declared = int(cl) if cl and cl.isdigit() else None
            if declared is not None and declared > FILE_MAX:
                raise ActionError(f"That file is {declared / MB:.0f} MB; imports take files up to {FILE_MAX // MB} MB. "
                                  "Trim or compress the video, then share it again.", 413)
            ctype = (r.headers.get("content-type") or "").lower()
            disp = r.headers.get("content-disposition") or ""
            it = r.iter_bytes()
            first = b""
            for piece in it:
                first += piece
                if len(first) >= 64:
                    break
            if "text/html" in ctype or first.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
                page = first
                for piece in it:
                    page += piece
                    if len(page) > PAGE_MAX:
                        break
                text = page.decode("utf-8", "replace")
                nxt = _drive_confirm(text) if prov == "drive" else ""
                if nxt and hop == 0:
                    r.close()
                    return self._stream(a, nxt, prov, name, http, hop + 1)
                why = _page_says(text)
                if why == "quota":
                    raise ActionError("Google Drive says this file was downloaded too often today (its download quota). "
                                      "Try again tomorrow, or upload it with the Send a file page.", 429)
                raise ActionError(NOT_PUBLIC.get(prov, NOT_PUBLIC[""]), 403 if why == "signin" or prov else 415)
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp)
            if m and name.endswith("-import"):
                name = m.group(1).strip()[:80]

            def pieces():
                if first:
                    yield first
                yield from it
            return self.lib.save_iter(a, name, pieces(), declared, "link")


def item_out(house, it: dict, prov: str = "") -> dict:
    tok = it.get("media_token")
    return {"library_id": it.get("id"), "media_token": tok, "url": house.media_url(tok) if tok else it.get("url"),
            "kind": it.get("kind"), "type": "video" if it.get("kind") == "video" else "photo", "name": it.get("name"),
            "bytes": it.get("size"), "from": prov or "link",
            "next": "Use media_token with edit_video / render_reel / split_video, or attach it to a post."}


TOOLS = [
    {"name": "import_from_link", "description": "Add a photo or video to the user's Library from a share link: Google Drive, "
     "Dropbox, OneDrive / SharePoint or iCloud Drive (the link must be shared with \"anyone with the link\"), or any direct "
     "https link to the file. Converts the share page to the file's download URL (Drive's large-file confirm page "
     "included) and streams it in, up to the Library's per-file limit (see max_mb in create_upload / create_send_code). "
     "Use this when the user's file is in a cloud drive, or when your sandbox can't reach create_upload's URL. Returns "
     "library_id and media_token.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string", "description": "The share link, e.g. https://drive.google.com/file/d/<id>/view?usp=sharing"},
         "name": {"type": "string", "description": "File name to keep, e.g. launch.mp4 (optional)"},
         "run_id": {"type": "integer", "description": "The post this file is for (start_post): the import is kept with it"}}}},
]
NAMES = {t["name"] for t in TOOLS}


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    url = str((args or {}).get("url") or "")
    it = studio.house.share_import.import_link(actor, url, str((args or {}).get("name") or ""))
    rid = (args or {}).get("run_id")
    if isinstance(rid, int) and rid > 0 and it.get("id"):
        from . import run_discipline
        run_discipline.own_run(studio.store, actor, rid)  # 404 for someone else's run
        studio.house.library.attach_run(actor, it["id"], rid)
    out = item_out(studio.house, it, provider(url))
    return {**out, "deduped": True} if it.get("deduped") else out


def add_routes(app, house, actor) -> None:
    @app.post("/api/library/import")
    def library_import(request: Request, body: dict = Body(default={})):
        """import_from_link: a share link (Drive, Dropbox, OneDrive, iCloud) or a direct https link into the Library."""
        return call_tool(house.studio, actor(request, write=True), "import_from_link", body or {})
