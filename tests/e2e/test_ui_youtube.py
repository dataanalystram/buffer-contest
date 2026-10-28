"""Browser tests for the YouTube composer: Short/Video, required title, the 15 categories, visibility, license,
notify/embed/kids switches, AI disclosure, tags, custom thumbnail, playlist and a scheduled (publishAt) post.

The YouTube channel is created in-process with a dummy token; playlists.list is stubbed on the connector class so
no request reaches Google."""
import subprocess

from huhqueue.connectors.base import Profile, TokenBundle
from huhqueue.connectors.youtube import CATEGORIES, YouTubeConnector
from huhqueue.services import oauth

from .conftest import PASSWORD, T, api, goto, make_image, toast


def _video(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=24", "-t", "2",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_00_login_and_seed(page, server, tmp_path_factory):
    goto(page, server, "/home")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    YouTubeConnector.list_playlists = lambda self, auth: [{"id": "PL1", "title": "AI explainers"}, {"id": "PL2", "title": "Launches"}]
    cid = oauth.upsert_channel(server["core"], "youtube", TokenBundle("yt-dummy"), Profile("UC123", "huhbyte_yt", "HuhByte"))
    server["yt"] = cid
    d = tmp_path_factory.mktemp("yt")
    server["video"] = str(_video(d / "short.mp4"))
    server["thumb"] = str(make_image(d / "thumb.png", size=(1280, 720)))
    page.reload()          # the store loaded channels at login, before the YouTube channel was seeded
    T(page, "page-home").wait_for()


def _open(page, server):
    goto(page, server, "/home")
    T(page, "new-post").click()
    T(page, "composer").wait_for()
    btn = page.locator(f'[data-testid=composer-channel][data-channel="{server['yt']}"]')
    btn.wait_for()
    assert "@huhbyte_yt (youtube)" in btn.get_attribute("aria-label")
    if btn.get_attribute("aria-pressed") != "true":
        btn.click()
    T(page, "customize-youtube").wait_for()


def test_01_title_and_video_required(page, server):
    _open(page, server)
    T(page, "composer-caption").fill("GPT-6 in 60 seconds")
    assert "a video" in T(page, "composer-yt-missing").inner_text() and "a title" in T(page, "composer-yt-missing").inner_text()
    assert T(page, "composer-save-draft").is_disabled() and T(page, "composer-submit").is_disabled()
    T(page, "composer-file-input").set_input_files([server["video"]])
    page.wait_for_function("document.querySelectorAll('[data-testid=composer-media-item]').length === 1")
    page.wait_for_function("(document.querySelector('[data-testid=composer-yt-missing]')?.innerText || '').indexOf('video') < 0")
    assert "a title" in T(page, "composer-yt-missing").inner_text()
    assert T(page, "composer-submit").is_disabled()
    T(page, "opt-youtube-yt_title").fill("GPT-6 explained")
    T(page, "composer-yt-missing").wait_for(state="detached")
    assert not T(page, "composer-save-draft").is_disabled()
    assert "GPT-6 explained" in T(page, "preview-youtube-title").first.inner_text()


def test_02_every_field_and_scheduled_publish(page, server):
    sel = T(page, "opt-youtube-yt_category")
    labels = [o.strip() for o in sel.locator("option").all_inner_texts()]
    assert len(labels) == 15 and sorted(labels) == sorted(n for _, n in CATEGORIES)
    assert sel.input_value() == "28"
    sel.select_option(label="Education")
    # Short ↔ Video changes the preview shape
    T(page, "opt-youtube-yt_format").select_option("video")
    T(page, "preview-youtube-video").first.wait_for()
    T(page, "opt-youtube-yt_format").select_option("short")
    page.wait_for_function("!document.querySelector('[data-testid=preview-youtube-video]')")
    vis = T(page, "opt-youtube-yt_privacy")
    assert [o.strip() for o in vis.locator("option").all_inner_texts()] == ["Public", "Unlisted", "Private"]
    vis.select_option("unlisted")
    lic = T(page, "opt-youtube-yt_license")
    assert "Creative Commons - Attribution" in lic.locator("option").all_inner_texts()
    lic.select_option("creativeCommon")
    for key, default in (("yt_notify_subscribers", "true"), ("yt_embeddable", "true"), ("yt_made_for_kids", "false"), ("yt_synthetic_media", "false")):
        sw = T(page, f"opt-youtube-{key}")
        assert sw.get_attribute("aria-checked") == default, key
        sw.click()
    T(page, "opt-youtube-yt_tags").fill("ai, gpt-6, openai")
    T(page, "opt-youtube-yt_playlist_id").locator("option", has_text="AI explainers").wait_for(state="attached")
    T(page, "opt-youtube-yt_playlist_id").select_option("PL1")
    T(page, "opt-youtube-yt_publish_at").fill("2031-06-01T18:00")
    T(page, "when-custom").click()
    T(page, "composer-datetime").fill("2031-06-01T09:30")
    T(page, "composer-submit").click()
    toast(page, "Scheduled")
    posts = api(page, "GET", f"/api/posts?channel_id={server['yt']}")
    items = posts["items"] if isinstance(posts, dict) else posts
    t = [x for p in items for x in p.get("targets", [p]) if (x.get("options") or {}).get("yt_title") == "GPT-6 explained"][0]
    o = t["options"]
    assert o["yt_category"] == "27" and o["yt_privacy"] == "unlisted" and o["yt_license"] == "creativeCommon"
    assert o["yt_notify_subscribers"] is False and o["yt_embeddable"] is False and o["yt_made_for_kids"] is True
    assert o["yt_synthetic_media"] is True and o["yt_tags"] == "ai, gpt-6, openai" and o["yt_playlist_id"] == "PL1"
    assert o["yt_publish_at"].startswith("2031-06-01")
    assert o.get("yt_format", "short") == "short"


def test_03_custom_thumbnail_picker(page, server):
    # put an image in the library, then the thumbnail select lists it
    _open(page, server)
    T(page, "composer-file-input").set_input_files([server["thumb"]])     # uploads into the library
    page.wait_for_function("document.querySelectorAll('[data-testid=composer-media-item]').length === 1")
    T(page, "composer-media-item").first.hover()
    T(page, "composer-media-remove").first.click()
    T(page, "composer-close").click()
    _open(page, server)
    thumb = T(page, "opt-youtube-yt_thumbnail_media_id")
    page.wait_for_function("document.querySelectorAll('[data-testid=opt-youtube-yt_thumbnail_media_id] option').length >= 2")
    val = thumb.locator("option").nth(1).get_attribute("value")
    thumb.select_option(val)
    assert thumb.input_value() == val
    T(page, "composer-close").click()


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
