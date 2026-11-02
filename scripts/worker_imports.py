"""Every import the video worker can reach, so its requirements can be checked (owner bug 2 Nov 2026: the worker lacked
`cryptography` and every YouTube link -> split crashed on `from .takes import ...`).

    python scripts/worker_imports.py            # list the reachable studio modules and third-party packages
    python scripts/worker_imports.py --import   # import all of them (run in a venv with only requirements-worker.txt)
    python scripts/worker_imports.py --import --hard-only   # skip guarded imports (the lighter render installs)

Walks the AST from the worker entry modules, following `studio.*` imports at any depth, including imports inside
functions (the worker imports lazily). Imports inside `try: ... except ImportError` are optional and skipped.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ["studio.render_worker"]  # the GitHub Actions take / render / recap worker
STDLIB = set(sys.stdlib_module_names)
# Reachable but not needed by the worker: the Postgres store only opens for a postgres DATABASE_URL (web dyno); rembg is
# an optional cut-out that falls back to the plain photo; requests is only patched by the demo's no-outbound guard
# (studio/demo_view.py, guarded) for the Oracle SDK, which only the web dyno loads.
WEB_ONLY = {"psycopg", "rembg", "requests"}
# import name -> requirements-worker.txt name. Guarded (try/except ImportError) imports count too: the worker needs
# yt-dlp, faster-whisper, PySceneDetect and MediaPipe for real, it only degrades without them.
PIP_NAME = {"PIL": "pillow", "yt_dlp": "yt-dlp", "faster_whisper": "faster-whisper", "imageio_ffmpeg": "imageio-ffmpeg",
            "fontTools": "fonttools", "mediapipe": "mediapipe", "scenedetect": "scenedetect",
            "cv2": "opencv-python-headless"}


def _path(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    return None


def _optional(node: ast.AST, parents: dict) -> bool:
    n = node
    while n in parents:
        n = parents[n]
        if isinstance(n, ast.Try):
            for h in n.handlers:
                names = []
                if h.type is None:
                    return True
                for t in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
                    names.append(getattr(t, "id", getattr(t, "attr", "")))
                if {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names):
                    return True
    return False


def scan(entries=ENTRIES):
    seen, third, hard, todo = set(), {}, set(), list(entries)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        p = _path(mod)
        if p is None:
            continue
        seen.add(mod)
        # importing a submodule runs its package __init__ too
        parts = mod.split(".")
        for i in range(1, len(parts)):
            todo.append(".".join(parts[:i]))
        tree = ast.parse(p.read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        pkg = mod if p.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".")
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    b = ".".join(base + ([node.module] if node.module else []))
                    targets = [b] + [f"{b}.{a.name}" for a in node.names]
                else:
                    targets = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for t in targets:
                top = t.split(".")[0]
                if top == "studio":
                    if _path(t):
                        todo.append(t)
                elif top not in STDLIB and top != "__future__":
                    third.setdefault(top, set()).add(mod)
                    if not _optional(node, parents):
                        hard.add(top)
    return sorted(seen), {k: sorted(v) for k, v in sorted(third.items())}, hard


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mods, third, hard = scan()
    if "--import" not in argv:
        print("studio modules:", len(mods))
        for k, v in third.items():
            print(f"{k}{'' if k in hard else ' (guarded)'}: {', '.join(v[:4])}{' ...' if len(v) > 4 else ''}")
        return 0
    sys.path.insert(0, str(ROOT))
    bad = []
    names = [t for t in third if t not in WEB_ONLY and ("--hard-only" not in argv or t in hard)]
    for name in names + mods:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{name}: {e.__class__.__name__}: {e}")
    for b in bad:
        print("MISSING", b)
    print(f"worker imports: {len(names)} packages, {len(mods)} studio modules, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
