"""Send a file at 390 px (an iPhone): the signed-in page shows a code + QR, Stop receiving kills the code, and a phone with
the code sends a photo into the Library. Screenshots go to $HQ_SHOTS (when set) for the security report."""
from __future__ import annotations

import io
import os
from pathlib import Path

from .test_e2e_studio import NO_TOUR, live  # noqa: F401  (the signed-in live server fixture)


def _jpeg() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (53.0, 20.0, 59.0)}
    Image.new("RGB", (120, 80), (40, 90, 200)).save(b, "JPEG", exif=ex.tobytes())
    return b.getvalue()


def _shot(page, name: str) -> None:
    d = os.environ.get("HQ_SHOTS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), full_page=True)


def _no_overflow(page) -> None:
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], f"horizontal overflow at 390 px: {w}"


def test_send_page_at_390px_code_stop_and_phone_upload(live, chromium, tmp_path):  # noqa: F811
    base, _fake, app = live
    ctx = chromium.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    page = ctx.new_page()
    page.goto(base + "/send")
    page.get_by_test_id("send-qr").wait_for(state="visible")
    code = page.get_by_test_id("send-code").inner_text().replace("-", "")
    assert len(code) == 8
    _no_overflow(page)
    _shot(page, "01-desktop-owner-code-390")

    # the phone: no cookie, just the link
    phone = chromium.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pp = phone.new_page()
    pp.goto(f"{base}/send/{code}")
    pp.get_by_test_id("send-drop").wait_for(state="visible")
    _no_overflow(pp)
    _shot(pp, "02-phone-drop-390")
    f = tmp_path / "IMG_0420.jpg"
    f.write_bytes(_jpeg())
    pp.get_by_test_id("send-file").set_input_files(str(f))
    pp.locator("#sdList li.ok").first.wait_for(timeout=15000)
    assert "In your Library" in pp.locator("#sdList").inner_text()
    _no_overflow(pp)
    _shot(pp, "03-phone-sent-390")

    # Stop receiving on the owner's screen: the phone's next file is refused
    page.get_by_test_id("send-stop").click()
    page.get_by_test_id("send-stopped").wait_for(state="visible")
    _shot(page, "04-owner-stopped-390")
    r = pp.request.put(f"{base}/send/{code}/file?name=late.jpg", data=_jpeg() + b"late")
    assert r.status == 404
    pp.goto(f"{base}/send/{code}")
    pp.get_by_test_id("send-enter").wait_for(state="visible")
    _no_overflow(pp)
    _shot(pp, "05-phone-dead-code-390")

    # the type-a-code page on a phone
    pp.goto(f"{base}/send")
    pp.get_by_test_id("send-code-input").wait_for(state="visible")
    _no_overflow(pp)
    _shot(pp, "06-phone-enter-code-390")
    ctx.close(); phone.close()
