"""Image editor: rotate / flip / crop / aspect / resize / colours / text, saved as a new copy; preview; REST = MCP."""
from PIL import Image

from huhqueue import actions as reg
from huhqueue.services import image_edit

from .conftest import make_image, upload


def _quadrants(path, size=(400, 200)):
    """Left half red, right half blue: makes flips and rotations easy to check."""
    im = Image.new("RGB", size, (255, 0, 0))
    im.paste((0, 0, 255), (size[0] // 2, 0, size[0], size[1]))
    im.save(path, "PNG")
    return path


def test_apply_order_flip_rotate_crop_resize():
    im = Image.new("RGB", (400, 200), (255, 0, 0))
    im.paste((0, 0, 255), (200, 0, 400, 200))
    out = image_edit.apply(im, {"flip_h": True})
    assert out.getpixel((10, 10))[:3] == (0, 0, 255)            # blue now on the left
    out = image_edit.apply(im, {"rotate": 90})
    assert out.size == (200, 400)
    assert out.getpixel((100, 10))[:3] == (255, 0, 0)            # clockwise: the left (red) half is now on top
    assert out.getpixel((100, 390))[:3] == (0, 0, 255)
    out = image_edit.apply(im, {"rotate": 90, "crop": {"x": 0, "y": 200, "width": 200, "height": 200}})
    assert out.size == (200, 200) and out.getpixel((100, 100))[:3] == (0, 0, 255)
    out = image_edit.apply(im, {"aspect": "1:1", "resize": {"width": 100}})
    assert out.size == (100, 100)
    assert image_edit.center_crop(1080, 1080, 4 / 5) == {"x": 108, "y": 0, "width": 864, "height": 1080}


def test_text_and_colour_change_pixels():
    im = Image.new("RGB", (300, 300), (40, 40, 40))
    out = image_edit.apply(im, {"texts": [{"text": "HELLO", "x": 0.5, "y": 0.5, "size": 0.2, "color": "#ffffff"}]})
    assert any(out.getpixel((x, 150))[:3] == (255, 255, 255) for x in range(300))
    brighter = image_edit.apply(im, {"brightness": 2})
    assert brighter.getpixel((5, 5))[0] > 40


def test_edit_route_saves_a_new_copy_and_keeps_metadata(api, channel, tmp_path):
    a = upload(api, _quadrants(tmp_path / "card.png"))[0]
    api.patch(f"/api/media/{a['id']}", {"alt_text": "Red and blue", "tags": ["launch"], "folder": "brand"})
    r = api.post(f"/api/media/{a['id']}/edit", {"aspect": "1:1", "rotate": 90, "texts": [{"text": "Hi"}]})
    assert r.status_code == 200, r.text
    m = r.json()["media"]
    assert m["id"] != a["id"] and m["filename"] == "card-edited.png" and (m["width"], m["height"]) == (200, 200)
    assert m["alt_text"] == "Red and blue" and m["tags"] == ["launch"] and m["folder"] == "brand"
    orig = api.get("/api/media").json()
    assert {x["id"] for x in orig} >= {a["id"], m["id"]}
    # editing the copy again does not stack "-edited-edited"
    m2 = api.post(f"/api/media/{m['id']}/edit", {"flip_v": True, "alt_text": "new alt"}).json()["media"]
    assert m2["filename"] == "card-edited.png" and m2["alt_text"] == "new alt"


def test_preview_returns_data_url_without_saving(api, channel, tmp_path):
    a = upload(api, make_image(tmp_path / "big.jpg", (2000, 1000), "JPEG"))[0]
    before = len(api.get("/api/media").json())
    r = api.post(f"/api/media/{a['id']}/edit", {"preview": True, "aspect": "4:5"}).json()
    assert r["preview"].startswith("data:image/jpeg;base64,") and (r["width"], r["height"]) == (800, 1000)
    assert len(api.get("/api/media").json()) == before


def test_bad_input_is_a_400(api, channel, tmp_path):
    a = upload(api, make_image(tmp_path / "x.png"))[0]
    assert api.post(f"/api/media/{a['id']}/edit", {"aspect": "wide"}).status_code == 400
    assert api.post(f"/api/media/{a['id']}/edit", {"crop": {"x": 5000, "y": 0, "width": 10, "height": 10}}).status_code == 400
    assert api.post(f"/api/media/{a['id']}/edit", {"brightness": 9}).status_code == 400
    assert api.post("/api/media/md_nope/edit", {"rotate": 90}).status_code == 404


def test_media_edit_is_an_mcp_tool():
    a = reg.BY_ID["media.edit"]
    assert a.mcp and a.agent_visible() and "media_edit" in reg.mcp_tool_names()
    assert a.request({"id": "md_1", "aspect": "1:1"}) == ("POST", "/api/media/md_1/edit", None, {"aspect": "1:1"})
