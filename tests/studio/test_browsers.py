"""Regression: a machine without Chromium must skip the browser tests, never leave Playwright's loop running."""
import pytest

from .browsers import launch_or_skip

pw = pytest.importorskip("playwright.sync_api")


def test_missing_chromium_skips_and_stops_the_driver(monkeypatch):
    monkeypatch.setenv("CHROMIUM_PATH", "/nonexistent/chromium")
    with pytest.raises(pytest.skip.Exception):
        launch_or_skip(pw.sync_playwright)
    # the driver was stopped, so a second sync_playwright() in the same process still works
    p = pw.sync_playwright().start()
    p.stop()
