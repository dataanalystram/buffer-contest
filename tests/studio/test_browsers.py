"""Regression: a machine without Chromium must skip the browser tests, never leave Playwright's loop running.

Runs in a fresh interpreter, because the session's shared Chromium may already own this process's Playwright loop."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

SCRIPT = """
import pytest
from playwright.sync_api import sync_playwright
from tests.studio.browsers import launch_or_skip
try:
    launch_or_skip(sync_playwright)
    raise SystemExit("launched a browser that should not exist")
except pytest.skip.Exception:
    pass
p = sync_playwright().start()   # the old leak made this raise 'Sync API inside the asyncio loop'
p.stop()
print("ok")
"""


def test_missing_chromium_skips_and_stops_the_driver():
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "CHROMIUM_PATH": "/nonexistent/chromium"}
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=root, env=env, capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("ok")
