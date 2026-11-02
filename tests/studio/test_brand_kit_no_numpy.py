"""The web dyno (Dockerfile.studio) installs requirements-studio.txt only: no numpy. Every brand kit preview, the Video
style gallery included, is drawn there, so drawing them must never import numpy (2 Nov 2026: compose imported the music
bed at module level and every gallery card answered 500 in the ui-smoke job)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CODE = r"""
import sys
sys.modules["numpy"] = None  # import numpy -> ImportError, as on the web dyno
from studio import brand_kits, brand_onboard
from studio.media import styles
s = brand_onboard.sample("nothing")
k = brand_kits.normalize({**s["kit"], "logo": "sample:nothing"})
got = brand_kits.render_cards(k, brand_onboard.sample_logo("nothing"), list(brand_kits.KIT_CARDS) + list(styles.ALL_IDS), 160, "webp")
bad = {c: v.get("error") for c, v in got.items() if "bytes" not in v}
assert not bad, bad
print("ok", len(got))
"""


def test_every_brand_kit_preview_draws_without_numpy():
    r = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.startswith("ok ")
