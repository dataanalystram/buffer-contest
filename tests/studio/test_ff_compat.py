"""FFmpeg version compatibility: loading a filter graph from a file works on 6.x (the live workers) and on 7+/9 (a Mac
with Homebrew), where -filter_complex_script is gone."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from studio.media import ff


@pytest.mark.parametrize("out,major", [("ffmpeg version 6.1.1-3ubuntu5 Copyright", 6), ("ffmpeg version 9.0.2 Copyright", 9),
                                       ("ffmpeg version n7.1 Copyright", 7), ("ffmpeg version N-112233-gabc Copyright", None)])
def test_major_from_version_banner(monkeypatch, out, major):
    ff.ffmpeg_major.cache_clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    assert ff.ffmpeg_major("ffmpeg-test") == major
    ff.ffmpeg_major.cache_clear()


def test_graph_file_args_by_version(monkeypatch):
    monkeypatch.setattr(ff, "ffmpeg_major", lambda exe="ffmpeg": 6)
    assert ff.filter_graph_file_args("g.txt") == ["-filter_complex_script", "g.txt"]
    for m in (7, 9, None):
        monkeypatch.setattr(ff, "ffmpeg_major", lambda exe="ffmpeg", m=m: m)
        assert ff.filter_graph_file_args("g.txt") == ["-/filter_complex", "g.txt"]


def test_the_installed_ffmpeg_accepts_the_chosen_form(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    ff.ffmpeg_major.cache_clear()
    g = tmp_path / "graph.txt"
    g.write_text("[0:v]scale=32:32[v]", encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
                        *ff.filter_graph_file_args(str(g)), "-map", "[v]", "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
