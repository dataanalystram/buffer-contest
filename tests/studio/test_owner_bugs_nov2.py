"""Owner bugs, 2 Nov 2026 ("make sure no bugs"). One regression test (or more) per bug."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _wi():
    spec = importlib.util.spec_from_file_location("worker_imports", ROOT / "scripts" / "worker_imports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reqs(name: str) -> set[str]:
    out = set()
    for line in (ROOT / name).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.add(re.split(r"[<>=\[ ;]", line, 1)[0].lower())
    return out


# ---- Bug 1: the worker venv lacked `cryptography` (Actions run 41363638009) ----

def test_worker_reaches_store_and_needs_cryptography():
    mods, third, hard = _wi().scan()
    assert "studio.takes" in mods and "studio.store" in mods
    assert "cryptography" in hard


def test_worker_requirements_cover_every_reachable_import():
    wi = _wi()
    _, third, hard = wi.scan()
    full, core = _reqs("requirements-worker.txt"), _reqs("requirements-worker-core.txt")
    need = {wi.PIP_NAME.get(t, t).lower() for t in third if t not in wi.WEB_ONLY}
    assert "cryptography" in full and "cryptography" in core
    assert need <= full, f"requirements-worker.txt is missing {sorted(need - full)}"
    hard_need = {wi.PIP_NAME.get(t, t).lower() for t in hard if t not in wi.WEB_ONLY}
    assert hard_need <= core, f"requirements-worker-core.txt is missing {sorted(hard_need - core)}"


def test_worker_workflows_install_from_the_checked_files():
    take = (ROOT / ".github/workflows/take.yml").read_text()
    render = (ROOT / ".github/workflows/render.yml").read_text()
    ci = (ROOT / ".github/workflows/studio-ci.yml").read_text()
    assert "-r requirements-worker.txt" in take and "-r requirements-worker-core.txt" in take
    assert "-r requirements-worker-core.txt" in render
    assert "worker_imports.py --import" in ci  # the clean-venv import job


def test_scan_follows_function_level_imports(tmp_path, monkeypatch):
    wi = _wi()
    pkg = tmp_path / "studio"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    from .b import g\n")
    (pkg / "b.py").write_text("import somepkg_xyz\ntry:\n    import optional_xyz\nexcept ImportError:\n    pass\n")
    monkeypatch.setattr(wi, "ROOT", tmp_path)
    mods, third, hard = wi.scan(["studio.a"])
    assert "studio.b" in mods
    assert "somepkg_xyz" in hard and "optional_xyz" in third and "optional_xyz" not in hard
