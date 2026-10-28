"""A tiny dependency-free PDF writer (vector text, rectangles, lines and RGBA images).

Enough for HuhQueue's branded reports without pulling in reportlab: Helvetica / Helvetica-Bold (the PDF base-14 fonts,
so nothing is embedded), WinAnsi text, filled rectangles, lines and a PNG logo (alpha kept via an SMask).
Coordinates are top-left based in points (A4 = 595 x 842) and converted to PDF's bottom-left space on write.
"""
from __future__ import annotations

import zlib
from pathlib import Path

A4 = (595.0, 842.0)

# Helvetica advance widths (1/1000 em) for chars 32..126, from the Adobe AFM files.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
         556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
         722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
         556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
         260, 334, 584]
_HELV_B = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
           556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611,
           833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556,
           611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
           389, 280, 389, 584]


def hex_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


_REP = {"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
        "\u2192": "->", "\u2191": "^", "\u2193": "v", "\u00a0": " "}


def _clean(text: str) -> str:
    """Map to WinAnsi (cp1252); characters outside it (emoji, CJK) become '?' rather than breaking the file."""
    out = "".join(_REP.get(ch, ch) for ch in str(text))
    return out.encode("cp1252", "replace").decode("cp1252")


def text_width(text: str, size: float, bold: bool = False) -> float:
    table = _HELV_B if bold else _HELV
    w = 0
    for ch in _clean(text):
        o = ord(ch)
        w += table[o - 32] if 32 <= o <= 126 else 556
    return w * size / 1000


def fit(text: str, size: float, max_w: float, bold: bool = False) -> str:
    """Trim text with an ellipsis so it fits max_w points."""
    text = _clean(text).replace("\n", " ")
    if text_width(text, size, bold) <= max_w:
        return text
    while text and text_width(text + "...", size, bold) > max_w:
        text = text[:-1]
    return text.rstrip() + "..."


def wrap(text: str, size: float, max_w: float, bold: bool = False, max_lines: int = 3) -> list[str]:
    words, lines, cur = _clean(text).replace("\n", " ").split(), [], ""
    used = 0
    for w in words:
        cand = f"{cur} {w}".strip()
        if text_width(cand, size, bold) <= max_w:
            cur, used = cand, used + 1
            continue
        if cur:
            lines.append(cur)
        if len(lines) == max_lines:
            cur = ""
            break
        cur, used = fit(w, size, max_w, bold), used + 1
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if used < len(words) and lines:
        lines[-1] = fit(lines[-1] + " ...", size, max_w, bold)
    return lines


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Page:
    def __init__(self, doc: "PDF"):
        self.doc, self.ops = doc, []
        self.w, self.h = doc.size

    def _y(self, y: float, h: float = 0) -> float:
        return self.h - y - h

    def rect(self, x, y, w, h, fill: str | None = "#000000", stroke: str | None = None, width: float = 0.8):
        cmd = []
        if fill:
            r, g, b = hex_rgb(fill)
            cmd.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        if stroke:
            sr, sg, sb = hex_rgb(stroke)
            cmd.append(f"{sr:.3f} {sg:.3f} {sb:.3f} RG {width:.2f} w")
        cmd.append(f"{x:.2f} {self._y(y, h):.2f} {w:.2f} {h:.2f} re {'B' if fill and stroke else ('f' if fill else 'S')}")
        self.ops.append(" ".join(cmd))

    def line(self, x1, y1, x2, y2, color="#000000", width: float = 0.8):
        r, g, b = hex_rgb(color)
        self.ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {x1:.2f} {self._y(y1):.2f} m {x2:.2f} {self._y(y2):.2f} l S")

    def text(self, x, y, s, size: float = 10, bold: bool = False, color="#1f1830", align: str = "left"):
        """y is the baseline, measured from the top."""
        s = _clean(s)
        if align == "right":
            x -= text_width(s, size, bold)
        elif align == "center":
            x -= text_width(s, size, bold) / 2
        r, g, b = hex_rgb(color)
        self.ops.append(f"BT {r:.3f} {g:.3f} {b:.3f} rg /{'F2' if bold else 'F1'} {size:.2f} Tf {x:.2f} {self._y(y):.2f} Td ({_esc(s)}) Tj ET")

    def image(self, name: str, x, y, w, h):
        self.ops.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self._y(y, h):.2f} cm /{name} Do Q")


class PDF:
    def __init__(self, size=A4, title: str = "Report"):
        self.size, self.title = size, title
        self.pages: list[Page] = []
        self.images: dict[str, dict] = {}

    def page(self) -> Page:
        p = Page(self)
        self.pages.append(p)
        return p

    def add_png(self, name: str, path: str | Path) -> tuple[int, int] | None:
        """Register an image (alpha preserved). Returns its pixel size, or None if it can't be read."""
        try:
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGBA")
                rgb, alpha = im.convert("RGB").tobytes(), im.getchannel("A").tobytes()
                self.images[name] = {"w": im.width, "h": im.height, "rgb": zlib.compress(rgb), "alpha": zlib.compress(alpha)}
                return im.width, im.height
        except Exception:
            return None

    def render(self) -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        add(b"")  # 1: catalog (filled below)
        add(b"")  # 2: pages
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        xobjs = {}
        for name, im in self.images.items():
            smask = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                        b"/Filter /FlateDecode /Length %d >>\nstream\n" % (im["w"], im["h"], len(im["alpha"])) + im["alpha"] + b"\nendstream")
            xobjs[name] = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                              b"/Filter /FlateDecode /SMask %d 0 R /Length %d >>\nstream\n" % (im["w"], im["h"], smask, len(im["rgb"]))
                              + im["rgb"] + b"\nendstream")
        xo = " ".join(f"/{n} {i} 0 R" for n, i in xobjs.items())
        res = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << {xo} >> >>".encode()
        kids = []
        for p in self.pages:
            data = zlib.compress("\n".join(p.ops).encode("cp1252", "replace"))
            c = add(b"<< /Filter /FlateDecode /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
            kids.append(add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Resources " % self.size + res
                            + b" /Contents %d 0 R >>" % c))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{k} 0 R" for k in kids), len(kids))).encode()
        info = add(("<< /Title (%s) /Producer (HuhQueue) >>" % _esc(_clean(self.title))).encode("cp1252", "replace"))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for o in offsets:
            out += b"%010d 00000 n \n" % o
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info, xref)
        return bytes(out)
