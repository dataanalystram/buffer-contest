from PIL import Image, ImageDraw, ImageFont
F = "/usr/share/fonts/truetype/dejavu/"
def font(sz, bold=False, mono=False):
    return ImageFont.truetype(F + ("DejaVuSansMono.ttf" if mono else "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), sz)
LAV, INK, BG = (201, 172, 250), (22, 19, 31), (250, 249, 252)

def center(d, y, text, f, fill, W):
    w = d.textlength(text, font=f); d.text(((W - w) / 2, y), text, font=f, fill=fill)

def mark(d, x, y, s):
    d.rounded_rectangle([x, y, x + s, y + s], radius=s // 4, fill=(124, 77, 219))
    f = font(int(s * .42), True); w = d.textlength("hq", font=f); d.text((x + (s - w) / 2, y + s * .22), "hq", font=f, fill="white")

def title(path, W=1920, H=1080):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im); s = W / 1920
    mark(d, int(W / 2 - 60 * s), int(H * .22), int(120 * s))
    center(d, H * .22 + 160 * s, "HuhQueue", font(int(88 * s), True), INK, W)
    center(d, H * .22 + 280 * s, "built on the Buffer API", font(int(40 * s)), (107, 101, 119), W)
    center(d, H * .22 + 380 * s, "Paste a link → a post for every channel → approve → Buffer queue", font(int(40 * s), True), (124, 77, 219), W)
    center(d, H * .22 + 450 * s, "Open source · agent-first · nothing posts without your OK", font(int(34 * s)), INK, W)
    im.save(path)

def end(path, W=1920, H=1080, follow=False):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im); s = W / 1920
    y = H * (.18 if follow else .25)
    mark(d, int(W / 2 - 50 * s), int(y), int(100 * s))
    center(d, y + 140 * s, "Try it live", font(int(70 * s), True), INK, W)
    center(d, y + 240 * s, "huhqueue-studio.onrender.com", font(int(46 * s), True), (124, 77, 219), W)
    center(d, y + 320 * s, "Open source: github.com/dataanalystram/buffer-contest", font(int(36 * s)), INK, W)
    center(d, y + 390 * s, "A Buffer Commit entry · not affiliated with Buffer", font(int(30 * s)), (107, 101, 119), W)
    if follow:
        f = font(int(64 * s), True); txt = "Follow @huhbyte"; w = d.textlength(txt, font=f)
        x0, y0 = (W - w) / 2 - 50 * s, H * .68
        d.rounded_rectangle([x0, y0, x0 + w + 100 * s, y0 + 120 * s], radius=int(60 * s), fill=LAV)
        d.text((x0 + 50 * s, y0 + 22 * s), txt, font=f, fill=INK)
        center(d, y0 + 160 * s, "daily AI news, made simple", font(int(38 * s)), INK, W)
    im.save(path)

def terminal(path, lines, W=1920, H=1080):
    im = Image.new("RGB", (W, H), (21, 18, 33)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([120, 90, W - 120, H - 90], radius=24, fill=(30, 26, 46))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([160 + i * 40, 125, 184 + i * 40, 149], fill=c)
    d.text((300, 120), "MCP client → HuhQueue  (real tool calls to the live app, 28 Oct 2026)", font=font(28), fill=(170, 160, 200))
    y = 200; fm = font(30, mono=True)
    for kind, t in lines:
        col = {"cmd": (201, 172, 250), "out": (232, 228, 245), "ok": (110, 231, 160), "dim": (150, 140, 175)}[kind]
        d.text((170, y), t, font=fm, fill=col); y += 46
    im.save(path)
