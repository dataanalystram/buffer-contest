"""One Playwright driver per test session for the Studio browser tests.

Playwright's sync API runs its own event loop. A driver that is started but never stopped leaves that loop running, and
every later ``sync_playwright()`` in the same process then fails with "using Playwright Sync API inside the asyncio
loop". That is how a machine without Chromium (studio-ci) turned four clean skips into two One take failures: the first
test skipped after ``start()`` without ``stop()``. ``launch_or_skip`` always stops the driver when it can't launch."""
import os
import socket
import threading
import time

import pytest


def launch_or_skip(sync_playwright):
    """Start Playwright and launch Chromium, or stop the driver and skip. Returns (playwright, browser)."""
    p = sync_playwright().start()
    try:
        return p, p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    except Exception as e:  # no browser on this machine
        p.stop()
        pytest.skip(f"chromium unavailable: {str(e).splitlines()[0]}")


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def serve(app, port: int, timeout: float = 30.0):
    """Run the app on 127.0.0.1:port in a thread and wait until /healthz answers. The old 5-second wait was too short on
    a slow machine, and a test then hit ERR_CONNECTION_REFUSED instead of failing on what it tests. Returns the server
    (set ``should_exit`` to stop it)."""
    import httpx
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    server.should_exit = True
    raise RuntimeError(f"the test server on port {port} did not start within {timeout:.0f} s")
