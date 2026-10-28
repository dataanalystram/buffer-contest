"""End-to-end: Settings › Webhooks (add, test ping, deliveries, toggle, delete)."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .conftest import PASSWORD, T, ask_ok, free_port, goto, toast

HITS = []


class Catch(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        HITS.append((self.headers.get("X-HuhQueue-Event"), self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_00_login(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_01_add_test_and_delete(page, server):
    port = free_port()
    srv = HTTPServer(("127.0.0.1", port), Catch)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        goto(page, server, "/settings?section=webhooks")
        T(page, "settings-webhooks").wait_for()
        T(page, "webhook-url").fill(f"http://127.0.0.1:{port}/hook")
        T(page, "webhook-event-post.sent").check()
        T(page, "webhook-add").click()
        T(page, "webhook-secret").wait_for()
        assert "whsec_" in T(page, "webhook-secret").inner_text()
        T(page, "webhook-row").first.wait_for()
        T(page, "webhook-test").click()
        toast(page, "Test delivered")
        assert HITS and HITS[-1][0] == "ping"
        T(page, "webhook-log").click()
        T(page, "webhook-delivery").first.wait_for()
        T(page, "webhook-active").click()
        T(page, "webhook-delete").click()
        ask_ok(page)
        page.get_by_text("No webhooks yet.").wait_for()
        assert not page.errors, page.errors
    finally:
        srv.shutdown()
