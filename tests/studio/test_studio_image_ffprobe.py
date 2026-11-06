"""The web image must ship ffprobe/ffmpeg: studio code calls them in the web process (6 Nov 2026 live bug:
"That file can't be previewed: [Errno 2] No such file or directory: 'ffprobe'")."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_studio_dockerfile_installs_ffmpeg_and_ffprobe():
    text = (ROOT / "Dockerfile.studio").read_text()
    runs = [l for l in text.splitlines() if l.strip().startswith("RUN")]
    assert any(re.search(r"apt-get install[^\n]*\bffmpeg\b", l) for l in runs), "Dockerfile.studio must apt-get install ffmpeg (ships ffprobe)"


def test_web_code_really_needs_ffprobe():
    # guard: if the web code ever stops calling ffprobe, this test documents why the install exists
    src = (ROOT / "studio" / "mediasafe.py").read_text()
    assert '"ffprobe"' in src
