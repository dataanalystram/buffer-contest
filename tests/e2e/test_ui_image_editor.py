"""End-to-end: the image editor from the Library drawer and from the composer media strip."""
from ..conftest import make_image
from .conftest import PASSWORD, T, api, goto, toast


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "huhbyte_demo"})


def test_01_edit_from_library(page, server):
    goto(page, server, "/library")
    T(page, "page-library").wait_for()
    T(page, "library-file-input").set_input_files(str(make_image(server["dir"] / "wide.png", (1600, 900))))
    T(page, "library-item").first.wait_for()
    T(page, "library-item").first.click()
    T(page, "library-edit").click()
    T(page, "image-editor").wait_for()
    page.locator(".reactEasyCrop_Image").wait_for()
    T(page, "image-editor-aspect-1:1").click()
    T(page, "image-editor-rotate-right").click()
    T(page, "image-editor-flip-h").click()
    T(page, "image-editor-add-text").click()
    T(page, "image-editor-text-input").fill("GPT-6 is here")
    T(page, "image-editor-text-bg").check()
    T(page, "image-editor-alt").fill("Launch card")
    T(page, "image-editor-preview").click()
    T(page, "image-editor-preview-img").wait_for()
    assert "900 × 900" in T(page, "image-editor-size").inner_text()
    T(page, "image-editor-save").click()
    toast(page, "edited copy")
    T(page, "image-editor").wait_for(state="hidden")
    items = api(page, "GET", "/api/media")
    edited = [m for m in items if m["filename"] == "wide-edited.png"]
    assert edited and (edited[0]["width"], edited[0]["height"]) == (900, 900) and edited[0]["alt_text"] == "Launch card"
    assert not page.errors, page.errors


def test_02_edit_from_composer_swaps_the_media(page, server):
    m = next(x for x in api(page, "GET", "/api/media") if x["filename"] == "wide.png")
    goto(page, server, f"/home?compose=new&media={m['id']}")
    T(page, "composer-media-item").first.wait_for()
    T(page, "composer-media-item").first.hover()
    T(page, "composer-media-edit").first.click()
    T(page, "image-editor").wait_for()
    page.locator(".reactEasyCrop_Image").wait_for()
    T(page, "image-editor-aspect-9:16").click()
    T(page, "image-editor-save").click()
    T(page, "image-editor").wait_for(state="hidden")
    src = T(page, "composer-media-item").first.locator("img").get_attribute("src")
    assert src and m["id"] not in src
    assert not page.errors, page.errors
