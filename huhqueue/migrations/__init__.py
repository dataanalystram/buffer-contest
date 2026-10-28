"""Versioned, forward-only migrations.

Files are `NNNN_name.sql` (statements split on ';') or `NNNN_name.py` with `def up(conn)` (a SQLAlchemy Connection).
They run in order inside one transaction each, and the version is recorded in `schema_migrations`.
`0001_baseline.sql` is the schema as create_all made it when the system started: a database that already has a
`channels` table is marked as at baseline without running it.

A `.py` migration may also define `AFTER_CREATE_ALL = True`: it then runs after `Base.metadata.create_all`, for data
moves into tables that newer models add.
"""
from __future__ import annotations

import importlib
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

HERE = Path(__file__).resolve().parent
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sql|py)$")


def available() -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(HERE.iterdir()):
        m = _NAME.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    return out


def _ensure_table(conn) -> None:
    conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"))


def applied(engine) -> list[int]:
    with engine.begin() as conn:
        _ensure_table(conn)
        return [r[0] for r in conn.execute(text("SELECT version FROM schema_migrations ORDER BY version"))]


def _after(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    return bool(getattr(importlib.import_module(f"huhqueue.migrations.{path.stem}"), "AFTER_CREATE_ALL", False))


def _run(conn, path: Path) -> None:
    if path.suffix == ".sql":
        sql = "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("--"))
        for stmt in (x.strip() for x in sql.split(";")):
            if stmt:
                conn.execute(text(stmt))
    else:
        importlib.import_module(f"huhqueue.migrations.{path.stem}").up(conn)


def _apply(engine, after: bool) -> list[int]:
    done = []
    with engine.begin() as conn:
        _ensure_table(conn)
        have = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
        if not have and "channels" in inspect(conn).get_table_names():
            conn.execute(text("INSERT INTO schema_migrations VALUES (1, 'baseline', :t)"), {"t": _now()})
            have.add(1)
    for version, name, path in available():
        if version in have or _after(path) != after:
            continue
        with engine.begin() as conn:
            _run(conn, path)
            conn.execute(text("INSERT INTO schema_migrations VALUES (:v, :n, :t)"), {"v": version, "n": name, "t": _now()})
        done.append(version)
    return done


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate(engine) -> list[int]:
    return _apply(engine, after=False)


def migrate_after(engine) -> list[int]:
    return _apply(engine, after=True)


def head() -> int:
    return max((v for v, _, _ in available()), default=0)
