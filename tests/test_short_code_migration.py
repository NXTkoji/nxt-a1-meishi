"""The migration that renames stored project codes (C0201-DS01 -> C0201S01) and back."""
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.config import Settings

BEFORE, AFTER = "b8c9d0e1f2a3", "c9d0e1f2a3b4"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "migrate.db"
    monkeypatch.setattr(Settings, "database_url", property(lambda self: f"sqlite+aiosqlite:///{path}"))
    return path


def _rows(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT code, product_line FROM projects ORDER BY id").fetchall()
    finally:
        con.close()


def test_upgrade_and_downgrade_rewrite_codes_and_product_lines(db_path):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, BEFORE)

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO organizations (id, external_id, created_at, updated_at) VALUES (1, 'o1', '2026-11-11', '2026-11-11')")
    for code, line in [("C0201-DS01", "DS"), ("C0201-DJ02", "DJ"), ("C10000-DS11", "DS")]:
        con.execute("INSERT INTO projects (code, org_id, product_line, created_at) VALUES (?, 1, ?, '2026-11-11')",
                    (code, line))
    con.commit()
    con.close()

    command.upgrade(cfg, AFTER)
    assert _rows(db_path) == [("C0201S01", "S"), ("C0201J02", "J"), ("C10000S11", "S")]

    command.downgrade(cfg, BEFORE)
    assert _rows(db_path) == [("C0201-DS01", "DS"), ("C0201-DJ02", "DJ"), ("C10000-DS11", "DS")]
