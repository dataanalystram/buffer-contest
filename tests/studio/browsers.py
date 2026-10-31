"""One Playwright driver per test session for the Studio browser tests.

Playwright's sync API runs its own event loop. A driver that is started but never stopped leaves that loop running, and
every later ``sync_playwright()`` in the same process then fails with "using Playwright Sync API inside the asyncio
loop". That is how a machine without Chromium (studio-ci) turned four clean skips into two One take failures: the first
test skipped after ``start()`` without ``stop()``. ``launch_or_skip`` always stops the driver when it can't launch."""
import os

import pytest


def launch_or_skip(sync_playwright):
    """Start Playwright and launch Chromium, or stop the driver and skip. Returns (playwright, browser)."""
    p = sync_playwright().start()
    try:
        return p, p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    except Exception as e:  # no browser on this machine
        p.stop()
        pytest.skip(f"chromium unavailable: {str(e).splitlines()[0]}")
