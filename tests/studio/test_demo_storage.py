"""The demo never depends on the 96 MiB Buffer Commit kickoff file that used to sit on the HuhByte Hugging Face dataset:
the sample take ships its five credited clips in the repo and credits the YouTube original only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_sample_bundle_has_no_hosted_source():
    raw = (ROOT / "studio/sample_take/sample.json").read_text()
    src = json.loads(raw)["source"]
    assert "media_url" not in src
    assert src["url"].startswith("https://youtu.be/")  # the credit stays
    assert "huggingface.co" not in raw
    for i in range(1, 6):
        assert (ROOT / f"studio/sample_take/clip{i}.mp4").exists()


def test_no_code_points_at_the_kickoff_file():
    for p in [ROOT / "scripts/demo_buffer_split.py", ROOT / ".github/workflows/demo-clips.yml", ROOT / "studio/takes.py"]:
        assert "buffer_commit_kickoff" not in p.read_text(), p


def test_split_script_requires_source():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/demo_buffer_split.py"), "--out", "/tmp/x"], capture_output=True, text=True)
    assert r.returncode == 2 and "--source" in r.stderr
