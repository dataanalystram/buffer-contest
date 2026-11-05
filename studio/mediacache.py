"""Local disk read-through cache for media bytes (Egress guard, 5 Nov 2026; docs/STORAGE.md).

Where media bytes live is three layers, each behind one small interface:

* the row in Postgres/SQLite keeps the reference and metadata (token, owner, MIME, size, sha256, storage_key);
* ``blobstore`` reads and writes the bytes from their backend: ``db`` (the ``data`` BYTEA column, today's default) or
  ``r2`` (an S3-compatible bucket, off until R2 is set up);
* this module keeps a copy of each file on the instance's own disk after the first read, so a file is read from the
  backend at most once per instance lifetime instead of once per request (Supabase's free plan counts every byte the
  database sends as egress: 5 GB a month).

A cache entry is keyed by the file's token AND its ETag (the content hash when the row has one), so a token whose bytes
changed can never be answered with the old copy. The cache sits BELOW every ownership check: callers resolve the row
(and refuse foreign or missing ids with 404) before they ask for bytes, so a cached file is never a way in.

Writes are atomic (a temp file in the same directory, then ``os.replace``), one fill per key at a time, LRU eviction to
``HQ_MEDIA_CACHE_MB`` (default 1024; 0 turns the cache off). Render's disk is ephemeral: a deploy starts empty.
The cache object lives on the Store (never module-global): two stores in one process never share entries.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

MB = 1024 * 1024


def _cap_bytes() -> int:
    try:
        return max(0, int(float(os.environ.get("HQ_MEDIA_CACHE_MB", "1024")) * MB))
    except ValueError:
        return 1024 * MB


def _tok_part(token: str) -> str:
    return hashlib.sha256(("tok:" + str(token or "")).encode()).hexdigest()[:24]


class MediaCache:
    """One cache directory for one Store, in a per-process subdirectory of HQ_MEDIA_CACHE_DIR (default: the temp dir)."""

    def __init__(self, base: str | None = None, cap: int | None = None):
        root = Path(base or os.environ.get("HQ_MEDIA_CACHE_DIR") or (Path(tempfile.gettempdir()) / "hq-media-cache"))
        self.cap = _cap_bytes() if cap is None else int(cap)
        self.dir = root / f"p{os.getpid()}-{secrets.token_hex(4)}"
        self._lock = threading.Lock()
        self._fills: dict[str, threading.Lock] = {}
        self._lru: OrderedDict[str, int] = OrderedDict()
        self._gen = 0                    # forget() bumps it: a fill that overlapped a forget never keeps its copy
        self._doomed: set[str] = set()   # forgotten copies whose unlink failed: retried on every later call, never served
        self.hits = self.misses = self.fills = self.evictions = self.bypass = 0
        if self.cap <= 0:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.chmod(root, 0o700)
            for old in root.iterdir():  # a previous process's copies (same instance, restarted): never served again
                if old.is_dir() and not old.name.startswith(f"p{os.getpid()}-"):
                    shutil.rmtree(old, ignore_errors=True)
            self.dir.mkdir(mode=0o700)
        except OSError as e:
            print(f"media cache: off ({e.__class__.__name__})", flush=True)
            self.cap = 0

    @property
    def on(self) -> bool:
        return self.cap > 0

    def _name(self, token: str, etag: str) -> str:
        return f"{_tok_part(token)}-{hashlib.sha256(str(etag or '').encode()).hexdigest()[:24]}"

    def stats(self) -> dict:
        with self._lock:
            return {"on": self.on, "cap_bytes": self.cap, "bytes": sum(self._lru.values()), "files": len(self._lru),
                    "hits": self.hits, "misses": self.misses, "fills": self.fills, "evictions": self.evictions,
                    "bypass": self.bypass, "pending_removals": len(self._doomed)}

    # ------------------------------------------------------------------ fill / read
    def path_for(self, token: str, etag: str, size: int, fill) -> Path | None:
        """The local copy of this file, filled once from ``fill()`` (an iterator of byte pieces) when missing. None when
        the cache is off, the file is bigger than a quarter of the cap, or the fill came out the wrong length (the
        caller then reads the backend directly). Errors from ``fill`` (storage down: ActionError 503) propagate."""
        if not self.on or size <= 0 or size > self.cap // 4:
            with self._lock:
                self.bypass += 1
            return None
        name = self._name(token, etag)
        p = self.dir / name
        with self._lock:
            self._retry_doomed()
            if name in self._lru and p.exists():
                self._lru.move_to_end(name)
                self.hits += 1
                return p
            lk = self._fills.setdefault(name, threading.Lock())
        with lk:  # one fill per file: a second request for the same file waits and then reads the copy
            with self._lock:
                if name in self._lru and p.exists():
                    self._lru.move_to_end(name)
                    self.hits += 1
                    return p
                self.misses += 1
                gen = self._gen
            n, tmp = 0, None
            try:
                fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".fill-")
                with os.fdopen(fd, "wb") as fh:
                    for piece in fill():
                        if not piece:
                            continue
                        fh.write(piece)
                        n += len(piece)
                        if n > size:
                            break
                if n != size:  # a short or long read: never keep a copy that isn't the whole file
                    os.unlink(tmp)
                    print(f"media cache: fill length {n} != {size}; not cached", flush=True)
                    return None
                os.replace(tmp, p)
            except OSError as e:  # the disk (full, gone): serve from the backend instead, never a 500
                _unlink(tmp)
                print(f"media cache: disk error ({e.__class__.__name__}); serving without a copy", flush=True)
                with self._lock:
                    self.bypass += 1
                return None
            except BaseException:  # storage down (ActionError 503) and the like: the caller's error, no partial copy
                _unlink(tmp)
                raise
            finally:
                with self._lock:
                    self._fills.pop(name, None)
            with self._lock:
                if self._gen != gen:  # a permanent delete ran while this copy was filling: it must not stay on disk
                    self._drop(name)
                    return None
                self._lru[name] = n
                self._lru.move_to_end(name)
                self.fills += 1
                self._evict()
            return p

    def _evict(self) -> None:
        """Drop least recently used copies until the cache fits its cap (caller holds the lock)."""
        total = sum(self._lru.values())
        while total > self.cap and len(self._lru) > 1:
            name, n = self._lru.popitem(last=False)
            total -= n
            self.evictions += 1
            self._drop(name)

    def forget(self, tokens) -> int:
        """Remove every copy of these tokens (a permanent delete): their bytes don't stay on disk until eviction."""
        if not self.on:
            return 0
        parts = {_tok_part(t) for t in tokens if t}
        gone = 0
        with self._lock:
            self._gen += 1
            on_disk = set()
            try:  # every copy on disk, not only the ones the LRU knows (CodeRabbit, PR #119)
                on_disk = {f.name for f in self.dir.iterdir() if not f.name.startswith(".")}
            except OSError:
                pass
            for name in {k for k in set(self._lru) | on_disk if k.split("-", 1)[0] in parts}:
                self._lru.pop(name, None)
                self._drop(name)
                gone += 1
        return gone

    def _drop(self, name: str) -> None:
        """Unlink one copy (caller holds the lock). A failed unlink keeps the name in ``_doomed``, retried on later
        calls, so an erased person's file is never forgotten on disk (CodeRabbit, PR #119)."""
        try:
            (self.dir / name).unlink()
            self._doomed.discard(name)
        except FileNotFoundError:
            self._doomed.discard(name)
        except OSError as e:
            self._doomed.add(name)
            print(f"media cache: couldn't remove a forgotten copy ({e.__class__.__name__}); will retry", flush=True)

    def _retry_doomed(self) -> None:
        """Retry every failed removal (caller holds the lock)."""
        for name in list(self._doomed):
            self._lru.pop(name, None)
            self._drop(name)

    def pending_removals(self) -> int:
        """Forgotten copies still waiting for a successful unlink."""
        with self._lock:
            return len(self._doomed)


def _unlink(p) -> None:
    if p:
        try:
            os.unlink(p)
        except OSError:
            pass


def read_file(p: Path, s: int, e: int) -> bytes:
    with open(p, "rb") as fh:
        fh.seek(s)
        return fh.read(e - s + 1)


def iter_file(p: Path, piece: int = MB):
    with open(p, "rb") as fh:
        while True:
            b = fh.read(piece)
            if not b:
                return
            yield b


def of(store) -> MediaCache:
    """The Store's cache (made on first use; lives on the Store object, never in a module-level map)."""
    c = getattr(store, "_media_cache", None)
    if c is None:
        c = MediaCache()
        try:
            store._media_cache = c
        except AttributeError:
            pass
    return c


def wrap(store, f: dict, token: str, meter_kind: str = "media") -> dict:
    """A ranged reader (``{"mime","size","read","iter","etag"}``) served from the local copy. The first read of any
    range fills the copy once from the backend (counted in the egress meter as DB bytes); every later read, whole or
    ranged, comes from disk. With the cache off or the file too big, the backend reader is kept and still metered."""
    from . import egress
    size = int(f.get("size") or 0)
    src_read, src_iter = f["read"], f.get("iter")
    cache = of(store)
    etag = f.get("etag") or ""

    def metered_iter():
        for b in (src_iter() if src_iter else _pieces(src_read, size)):
            egress.db_read(len(b), meter_kind)
            yield b

    def local():
        return cache.path_for(token, etag, size, metered_iter)

    def read(s: int, e: int | None) -> bytes:
        e = size - 1 if e is None else min(int(e), size - 1)
        if size <= 0 or s > e:
            return b""
        p = local()
        if p is not None:
            try:
                return read_file(p, s, e)
            except OSError:  # evicted or removed between the lookup and the open: read the backend this once
                pass
        b = src_read(s, e)
        egress.db_read(len(b), meter_kind)
        return b

    def it():
        p = local()
        if p is not None:
            try:
                fh = open(p, "rb")  # opened before the first byte: a later eviction can't cut the answer short
            except OSError:
                fh = None
            if fh is not None:
                with fh:
                    while True:
                        b = fh.read(MB)
                        if not b:
                            return
                        yield b
        yield from metered_iter()

    return {**f, "read": read, "iter": it}


def _pieces(read, size: int, piece: int = MB):
    for s in range(0, size, piece):
        yield read(s, min(s + piece, size) - 1)
