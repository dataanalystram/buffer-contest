"""Shared fixtures for the browser tests (each test module gets its own server + browser).

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e -q      (or after `playwright install chromium`)
"""
import os
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

pw = pytest.importorskip("playwright.sync_api")

from huhqueue.api.app import create_app, create_edge_app  # noqa: E402
from huhqueue.config import load_settings  # noqa: E402
from huhqueue.core import Core  # noqa: E402

from ..conftest import make_image  # noqa: E402,F401

PASSWORD = "e2e-password-123"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("e2e")
    os.environ.update({"HUHQ_DATA_DIR": str(d / "data"), "HUHQ_ENABLE_FAKE": "1"})
    s = load_settings(env_file=d / "none.env")
    s.port, s.public_port = free_port(), free_port()
    core = Core(s)
    app, edge = create_app(core, start_worker=False), create_edge_app(core)
    servers = []
    for a, p in ((app, s.port), (edge, s.public_port)):
        srv = uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=p, log_level="warning"))
        threading.Thread(target=srv.run, daemon=True).start()
        servers.append(srv)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", s.port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"base": f"http://localhost:{s.port}", "core": core, "dir": d}
    for srv in servers:
        srv.should_exit = True


@pytest.fixture(scope="module")
def page(server):
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
                                    args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=server["base"])
        pg = ctx.new_page()
        pg.set_default_timeout(int(os.environ.get("E2E_TIMEOUT_MS", "8000")))
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept(getattr(pg, "prompt_answer", None) or "") if d.type == "prompt" else d.accept())

        yield pg
        browser.close()


def T(page, tid):
    return page.get_by_test_id(tid)


def ask_ok(page, answer=None):
    """Accept the in-app confirm / prompt dialog (data-testid="ask"), typing `answer` into a prompt."""
    T(page, "ask").wait_for()
    if answer is not None:
        T(page, "ask-input").fill(answer)
    T(page, "ask-ok").click()
    T(page, "ask").wait_for(state="hidden")


def toast(page, text):
    try:
        page.get_by_test_id("toast").filter(has_text=re.compile(text, re.I)).first.wait_for(timeout=8000)
    except Exception as e:
        seen = page.get_by_test_id("toast").all_inner_texts()
        raise AssertionError(f"toast /{text}/ not shown; visible toasts: {seen}") from e


def goto(page, server, hash_):
    page.goto(server["base"] + "/#" + hash_)


def poll(page, js, timeout=15):
    """Wait until an async JS predicate resolves truthy. page.wait_for_function() can't do this: it treats the
    returned Promise itself as truthy and returns at once, so an `async () => ...` predicate never actually waits."""
    deadline = time.monotonic() + timeout
    while True:
        left_ms = max(1, int((deadline - time.monotonic()) * 1000))   # a hung fetch can't outlive the deadline
        if page.evaluate("""([src, ms]) => Promise.race([(0, eval)('(' + src + ')')(),
                               new Promise((ok) => setTimeout(() => ok(false), ms))])""", [js, left_ms]):
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for: {js.strip()[:120]}")
        page.wait_for_timeout(200)


def api(page, method, path, body=None):
    """Call the API from inside the logged-in page (same cookies + CSRF) - used only for setup/asserts."""
    return page.evaluate("""async ([m, p, b]) => {
        const csrf = document.cookie.split('; ').find(c => c.startsWith('huhq_csrf='))?.split('=')[1] || ''
        const r = await fetch(p, {method: m, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: b ? JSON.stringify(b) : undefined})
        return r.json() }""", [method, path, body])


