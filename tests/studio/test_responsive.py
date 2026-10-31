"""Every screen of the sample workspace (and the landing page) at phone, tablet and desktop widths, light and dark: no
page may scroll sideways, and below 1024 px every control is at least 44 px to tap. Uses the same probe as
scripts/responsive_audit.py. Skips without Chromium."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
_spec = importlib.util.spec_from_file_location("responsive_audit", Path(__file__).resolve().parents[2] / "scripts" / "responsive_audit.py")
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)


@pytest.mark.parametrize("scheme", ra.SCHEMES)
@pytest.mark.parametrize("width", ra.WIDTHS)
def test_no_sideways_scroll_and_44px_taps(demo_live, chromium, width, scheme):
    base, buffer_calls = demo_live
    rows = ra.audit_with(chromium, base, widths=[width], schemes=[scheme])
    assert {r["page"] for r in rows} == {"landing", *ra.TABS}
    over = [f"{r['page']}: {r['over']} px too wide ({', '.join(r['culprits'][:3])})" for r in rows if r["over"] > 0]
    assert not over, f"{width} px {scheme}: " + "; ".join(over)
    if width < 1024:
        small = [f"{r['page']}: {', '.join(r['small'][:6])}" for r in rows if r["small"]]
        assert not small, f"{width} px {scheme}, taps under 44 px: " + "; ".join(small)
    assert buffer_calls == []  # the sample never calls Buffer
