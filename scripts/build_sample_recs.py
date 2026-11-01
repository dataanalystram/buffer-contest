"""Build the sample workspace's pre-made recommendations (studio/sample_recs/recs.json + studio/static/sample_recs/*.jpg).

The demo never scans, renders or calls a model for /api/recommendations: it serves this file, filtered by the visitor's
choices. Rebuild after the recorded sample sources change:  python scripts/build_sample_recs.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HQ_ANON_RPM", "1000000")
os.environ.setdefault("HQ_DEMO_PER_MIN", "1000000")

from fastapi.testclient import TestClient  # noqa: E402

from studio import brand, onboarding, render  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import load_settings  # noqa: E402

COVERS = 9  # pre-rendered covers: the top clusters overall; recommendations only pick clusters that have one


def main() -> None:
    tmp = tempfile.mkdtemp()
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="s" * 64,
                      db_path=os.path.join(tmp, "b.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s)
    c = TestClient(app, base_url="https://hq.test")
    c.get("/demo")
    from studio.actions import Actor
    uid = app.state.store.q("SELECT id FROM users")[0]["id"]
    a = Actor(uid)
    scan = app.state.house.sources.scan(a, {"sources": sorted({x for t in onboarding.CONTENT_TYPES.values() for x in t["sources"]})})
    clusters = [cl for cl in scan["clusters"] if cl["lane"] != "hidden"]
    clusters.sort(key=lambda cl: ({"postable": 0, "idea_bank": 1, "low": 2}.get(cl["lane"], 3), -cl["score"]))
    kit = dict(brand.DEFAULT_KIT)
    p = brand.visual_params(kit, [])
    out_dir = ROOT / "studio" / "static" / "sample_recs"
    for f in out_dir.glob("*.jpg"):
        f.unlink()
    feed = []
    for i, cl in enumerate(clusters[:40]):
        keep = {k: cl[k] for k in ("id", "topic", "category", "lane", "score", "story", "items")}
        if i < COVERS:
            st = cl["story"]
            b, _ = render.news_card(st.get("headline") or cl["items"][0]["title"], kit, dict(p), ratio="4:5", label=cl["topic"],
                                    credit=st.get("source") or cl["items"][0]["source"])
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(b)).convert("RGB")
            im.thumbnail((720, 900))
            name = f"rec-{i + 1}.jpg"
            im.save(out_dir / name, "JPEG", quality=78, optimize=True)
            keep["cover"] = f"/static/sample_recs/{name}"
        feed.append(keep)
    (ROOT / "studio" / "sample_recs" / "recs.json").write_text(json.dumps({"feed": feed}, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(feed)} clusters, {min(COVERS, len(feed))} covers")


if __name__ == "__main__":
    main()
