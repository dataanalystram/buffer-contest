"""End-to-end: Generate ideas, comment insights, channel groups (Settings + composer) and AI alt text in the composer."""
import json
from datetime import datetime

from huhqueue.models import Comment
from huhqueue.services import assistant

from ..conftest import make_image
from .conftest import PASSWORD, T, api, ask_ok, goto, toast


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "ai_one"})
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "ai_two"})
    with server["core"].db.session() as s:
        for i, t in enumerate(["How much does it cost?", "pricing please", "pricing is fair", "nice"]):
            s.add(Comment(channel_id=a["id"], external_id=f"e{i}", media_external_id="m", author=f"u{i}", text=t,
                          at=datetime.utcnow(), status="open"))
    page.reload()
    T(page, "page-home").wait_for()


def test_01_generate_ideas(page, server):
    goto(page, server, "/ideas")
    T(page, "ideas-generate").click()
    T(page, "ideas-generate-topic").fill("AI agents")
    T(page, "ideas-generate-count").fill("3")
    T(page, "ideas-generate-run").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=ideas-generate-item]').length === 3")
    T(page, "ideas-generate-item").nth(2).click()           # untick one
    T(page, "ideas-generate-save").click(); toast(page, "2 idea")
    assert len(api(page, "GET", "/api/ideas")["ideas"]) == 2


def test_02_comment_insights(page, server):
    goto(page, server, "/community?view=insights")
    T(page, "comment-insights").wait_for()
    page.wait_for_function("document.querySelectorAll('[data-testid=comment-theme]').length >= 2")
    assert "Questions" in T(page, "comment-theme").first.inner_text()


def test_03_channel_groups(page, server):
    goto(page, server, "/settings?section=channels")
    T(page, "channel-group-name").fill("Video")
    T(page, "channel-group-create").click()
    T(page, "channel-group").wait_for()
    T(page, "channel-group-toggle").nth(0).click()
    T(page, "channel-group-toggle").nth(1).click()
    page.wait_for_timeout(300)
    assert len(api(page, "GET", "/api/channel-groups")[0]["channel_ids"]) == 2
    goto(page, server, "/home?compose=new")
    T(page, "composer-channel-group").click()
    page.wait_for_function("[...document.querySelectorAll('[data-testid=composer-channel]')].every(b => b.getAttribute('aria-pressed') === 'true')")
    goto(page, server, "/settings?section=channels")
    T(page, "channel-group-delete").click(); ask_ok(page)
    T(page, "channel-group").wait_for(state="detached")


def test_04_alt_text_with_ai(page, server, tmp_path_factory):
    api(page, "PUT", "/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = ["A lavender square."]
    img = make_image(tmp_path_factory.mktemp("alt") / "card.png")
    from huhqueue.models import Channel
    with server["core"].db.session() as s:     # alt text lives in the Instagram section of the composer
        s.add(Channel(provider="instagram", external_id="1789", username="ai_insta", status="active"))
    page.reload()
    goto(page, server, "/home?compose=new")
    page.locator("[data-testid=composer-channel][aria-label*='instagram']").click()
    page.set_input_files("input[type=file]", str(img))
    T(page, "composer-alt-generate").wait_for()
    T(page, "composer-alt-generate").click(); toast(page, "Alt text written")
    assert T(page, "composer-alt-input").input_value() == "A lavender square."


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
