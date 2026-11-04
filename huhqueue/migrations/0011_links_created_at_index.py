"""Index links.created_at for list_links (ORDER BY created_at DESC LIMIT 1000, the channel filter now in SQL).

Same name as the model's index=True, so a fresh database and an upgraded one end up identical. A database without a
links table yet gets it, index included, from create_all right after. CREATE INDEX IF NOT EXISTS is valid on SQLite
and Postgres.
"""
from __future__ import annotations

from sqlalchemy import inspect, text


def up(conn) -> None:
    if "links" in inspect(conn).get_table_names():
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_links_created_at ON links (created_at)"))
