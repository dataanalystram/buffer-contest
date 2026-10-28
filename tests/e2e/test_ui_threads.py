"""Browser tests for threads in the composer: split a long caption, edit / add / remove parts, preview, save."""
from .conftest import PASSWORD, T, api, goto, toast


def test_00_login_and_seed(page, server):
    goto(page, server, "/home")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    server["ch"] = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-thr", "username": "huhbyte_demo"})
    page.reload()                  # the UI loads channels once; pick up the one made through the API
    T(page, "page-home").wait_for()


def test_01_split_edit_and_save_a_thread(page, server):
    ch = server["ch"]
    goto(page, server, f"/home?compose=new&channels={ch['id']}")
    T(page, "composer-thread").wait_for()
    long = " ".join(f"Sentence number {i} about GPU computing." for i in range(40))   # ~1,600 chars, demo limit 500
    T(page, "composer-caption").fill(long)
    T(page, "thread-hint").wait_for()
    T(page, "thread-numbering").click()
    T(page, "thread-split").click()
    toast(page, "Split into")
    n = T(page, "thread-part").count()
    assert n >= 3
    assert T(page, "composer-caption").input_value().endswith(f"1/{n + 1}")
    assert T(page, "thread-count").inner_text() == f"{n + 1} posts"
    T(page, "thread-add").click()
    T(page, "thread-part-text").nth(n).fill("Follow for more")
    assert T(page, "thread-part-count").nth(n).inner_text() == "15/500"
    page.wait_for_function(f"document.querySelectorAll('[data-testid=preview-thread-part]').length === {n + 1}")
    T(page, "thread-part-remove").first.click()
    page.wait_for_function(f"document.querySelectorAll('[data-testid=thread-part]').length === {n}")
    T(page, "composer-save-draft").click()
    toast(page, "draft")
    drafts = api(page, "GET", "/api/posts?status=draft")
    thread = drafts[0]["options"]["thread"]
    assert len(thread) == n and thread[-1]["text"] == "Follow for more"
    assert drafts[0]["caption"].endswith(f"1/{n + 1}")


def test_02_thread_part_too_long_blocks_submit(page, server):
    ch = server["ch"]
    goto(page, server, f"/home?compose=new&channels={ch['id']}")
    T(page, "composer-caption").fill("Short first post")
    T(page, "thread-add").click()
    T(page, "thread-part-text").first.fill("z" * 501)
    page.wait_for_function("document.querySelector('[data-testid=composer-submit]').disabled")
    T(page, "thread-part-text").first.fill("fits")
    page.wait_for_function("!document.querySelector('[data-testid=composer-submit]').disabled")


def test_99_no_javascript_errors(page):
    assert not page.errors, page.errors
