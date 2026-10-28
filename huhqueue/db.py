"""Engine/session helpers."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        from .migrations import migrate
        self.migrations_applied = migrate(self.engine)   # versioned migrations (huhqueue/migrations/NNNN_*.sql|py)
        Base.metadata.create_all(self.engine)              # tables added by newer models
        self._add_missing_columns()                        # safety net for one release (CMS_PLAN §3.1)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        from .migrations import migrate_after
        migrate_after(self.engine)                         # data moves that need the newest tables

    def _add_missing_columns(self) -> None:
        """Tiny forward-only migration: add columns that newer versions introduced."""
        from sqlalchemy import inspect, text
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    ddl = col.type.compile(self.engine.dialect)
                    default = ""
                    if col.default is not None and getattr(col.default, "is_scalar", False):
                        v = col.default.arg
                        default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}{default}'))

    @contextmanager
    def session(self) -> Session:
        s = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
