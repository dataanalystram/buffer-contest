"""Take location and camera metadata out of photos and videos before they are stored (and served at public /m/ URLs).

A photo straight off a phone carries EXIF with the GPS position it was taken at (often the owner's home), the phone's
make, model and serial, and XMP that repeats it. A phone video carries the same position as an ISO 6709 string
(``+53.3498-006.2603+012.000/``) in its ``moov`` box. HuhQueue serves Library files at public /m/<token> URLs (Buffer
fetches them from there), so all of that would be readable by anyone who sees the link. Security audit, 3 Nov 2026.

* JPEG: every APP1 (EXIF and XMP), APP13 (IPTC/Photoshop) and COM segment before the image data is dropped, losslessly
  (no re-encode). The one EXIF field the picture needs, Orientation, is written back in a 34-byte EXIF block, so the
  photo still shows the right way up. ICC colour profiles (APP2) and everything after the header are kept byte for byte.
* PNG: ``eXIf``, ``tEXt``, ``iTXt``, ``zTXt`` and ``tIME`` chunks before the image data are dropped.
* MP4 / MOV: ISO 6709 positions inside ``moov`` are overwritten in place with zeros (same length, so no offsets move
  and nothing is re-muxed).

Everything works on the file's first bytes (the header) or on the ``moov`` box alone, so a 100 MB upload is never held
in memory. A file this module can't parse is stored as it came (it is still MIME-sniffed and capped by the Library).
"""
from __future__ import annotations

import re
import struct

HEAD_MAX = 4 * 1024 * 1024   # the most header bytes held back while looking for where the image data starts
MOOV_MAX = 32 * 1024 * 1024  # a moov box bigger than this is left alone (real ones are a few MB at most)

_JPEG_DROP = {0xE1, 0xED, 0xFE}          # APP1 (EXIF, XMP), APP13 (IPTC), COM
_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}
_PNG_DROP = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"}
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
ISO6709 = re.compile(rb"[+-]\d{2}(?:\.\d+)?[+-]\d{3}(?:\.\d+)?(?:[+-]\d+(?:\.\d+)?)?(?:CRS[\w:]*)?/")
INCOMPLETE = None  # head() answer: need more bytes
UNPARSED = b""     # head() answer: not a layout we understand; keep the file as it is


def _orientation(seg: bytes) -> int:
    """EXIF Orientation (1-8) from an APP1 payload, 1 when absent or unreadable."""
    if not seg.startswith(b"Exif\x00\x00") or len(seg) < 14:
        return 1
    t = seg[6:]
    end = {b"II": "<", b"MM": ">"}.get(t[:2])
    if not end:
        return 1
    try:
        (ifd,) = struct.unpack(end + "I", t[4:8])
        (n,) = struct.unpack(end + "H", t[ifd:ifd + 2])
        for i in range(min(n, 256)):
            e = t[ifd + 2 + 12 * i: ifd + 14 + 12 * i]
            tag, typ, _cnt = struct.unpack(end + "HHI", e[:8])
            if tag == 0x0112 and typ == 3:
                (v,) = struct.unpack(end + "H", e[8:10])
                return v if 1 <= v <= 8 else 1
    except (struct.error, IndexError):
        return 1
    return 1


def _orientation_app1(v: int) -> bytes:
    """A minimal EXIF APP1 segment holding only Orientation=v."""
    tiff = b"MM\x00*\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, v, 0) + b"\x00\x00\x00\x00"
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def jpeg_head(buf: bytes):
    """(cleaned header + the rest of buf) once the start-of-scan marker is in buf; INCOMPLETE before; UNPARSED if odd."""
    if buf[:2] != b"\xff\xd8":
        return UNPARSED
    out, pos, orient, n = [b"\xff\xd8"], 2, 1, len(buf)
    while True:
        if pos >= n:
            return INCOMPLETE
        if buf[pos] != 0xFF:
            return UNPARSED
        while pos < n and buf[pos] == 0xFF:  # fill bytes
            pos += 1
        if pos >= n:
            return INCOMPLETE
        m = buf[pos]
        start = pos - 1
        pos += 1
        if m == 0xDA:  # start of scan: the image data follows, keep everything from here
            break
        if m in _JPEG_STANDALONE:
            out.append(buf[start:pos])
            continue
        if m == 0xD9:
            return UNPARSED
        if pos + 2 > n:
            return INCOMPLETE
        (ln,) = struct.unpack(">H", buf[pos:pos + 2])
        if ln < 2:
            return UNPARSED
        if pos + ln > n:
            return INCOMPLETE
        seg = buf[pos + 2:pos + ln]
        if m == 0xE1 and orient == 1:
            orient = _orientation(seg)
        if m not in _JPEG_DROP:
            out.append(b"\xff" + bytes([m]) + buf[pos:pos + ln])
        pos += ln
    if orient != 1:  # keep the picture the right way up: after SOI and a JFIF APP0 if there is one
        at = 2 if len(out) > 1 and out[1][:2] == b"\xff\xe0" else 1
        out.insert(at, _orientation_app1(orient))
    return b"".join(out) + bytes(buf[start:])


def png_head(buf: bytes):
    if buf[:8] != _PNG_SIG:
        return UNPARSED
    out, pos, n = [_PNG_SIG], 8, len(buf)
    while True:
        if pos + 8 > n:
            return INCOMPLETE
        (ln,) = struct.unpack(">I", buf[pos:pos + 4])
        typ = bytes(buf[pos + 4:pos + 8])
        if typ in (b"IDAT", b"IEND"):
            return b"".join(out) + bytes(buf[pos:])
        if ln > HEAD_MAX:
            return UNPARSED
        if pos + 12 + ln > n:
            return INCOMPLETE
        if typ not in _PNG_DROP:
            out.append(bytes(buf[pos:pos + 12 + ln]))
        pos += 12 + ln


def png_all(buf: bytes) -> bytes:
    """The whole PNG with every metadata chunk dropped, through IEND (a valid PNG may put tEXt / iTXt XMP with GPS, or
    eXIf, AFTER the image data, which png_head passes through untouched). Anything after IEND goes too. UNPARSED for a
    file that isn't a complete, well-formed chunk list (the caller falls back to png_head)."""
    if buf[:8] != _PNG_SIG:
        return UNPARSED
    out, pos, n = [_PNG_SIG], 8, len(buf)
    while pos + 12 <= n:
        (ln,) = struct.unpack(">I", buf[pos:pos + 4])
        typ = bytes(buf[pos + 4:pos + 8])
        end = pos + 12 + ln
        if end > n:
            return UNPARSED
        if typ not in _PNG_DROP:
            out.append(bytes(buf[pos:end]))
        if typ == b"IEND":
            return b"".join(out)
        pos = end
    return UNPARSED


def head(ext: str, buf: bytes):
    """The cleaned start of a file of type ext ('jpg', 'png'); INCOMPLETE until the header is all there; UNPARSED for
    any other type or a layout we don't recognise."""
    if ext == "jpg":
        return jpeg_head(buf)
    if ext == "png":
        return png_head(buf)
    return UNPARSED


def strip_bytes(ext: str, data: bytes) -> bytes:
    """The whole-file version (for uploads that arrive as one bytes object): a PNG is cleaned through IEND."""
    r = png_all(data) if ext == "png" else UNPARSED
    r = r or head(ext, data)
    return r if r else data


def _zero(m: re.Match) -> bytes:
    return re.sub(rb"\d", b"0", m.group(0))


def video_location_patches(read, size: int) -> list[tuple[int, bytes]]:
    """(offset, new bytes) pairs that blank every ISO 6709 position inside the file's top-level moov box(es).
    read(start, end_inclusive) -> bytes. Reads only box headers and the moov box."""
    patches, off, guard = [], 0, 0
    while off + 8 <= size and guard < 4096:
        guard += 1
        h = read(off, min(size, off + 16) - 1)
        if len(h) < 8:
            break
        (bsz,) = struct.unpack(">I", h[:4])
        typ, hl = h[4:8], 8
        if bsz == 1:
            if len(h) < 16:
                break
            (bsz,) = struct.unpack(">Q", h[8:16])
            hl = 16
        elif bsz == 0:
            bsz = size - off
        if bsz < hl:
            break
        if typ == b"moov" and bsz <= MOOV_MAX:
            box = read(off, min(size, off + bsz) - 1)
            for m in ISO6709.finditer(box):
                patches.append((off + m.start(), _zero(m)))
        off += bsz
    return patches
