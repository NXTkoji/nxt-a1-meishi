"""The migration that adds supplier/status columns to organizations and kind to issue_runs."""
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.config import Settings

BEFORE, AFTER = "c9d0e1f2a3b4", "d0e1f2a3b4c5"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "migrate.db"
    monkeypatch.setattr(Settings, "database_url", property(lambda self: f"sqlite+aiosqlite:///{path}"))
    return path


def _columns(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1]: r for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _indexes(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1]: r[2] for r in con.execute(f"PRAGMA index_list({table})")}
    finally:
        con.close()


def test_upgrade_sets_statuses_from_existing_customers_and_downgrade_restores(db_path):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, BEFORE)

    con = sqlite3.connect(db_path)
    rows = [  # id, customer_code, is_nxt_customer
        (1, "C0201", 1), (2, None, 1), (3, None, 0), (4, "", 1),
    ]
    for i, code, flag in rows:
        con.execute("INSERT INTO organizations (id, external_id, customer_code, is_nxt_customer, "
                    "tax_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '2026-11-24', '2026-11-24')",
                    (i, f"o{i}", code, flag, f"1234567{i}"))
    con.execute("INSERT INTO issue_runs (external_id, status, input_json, steps_json, created_at, updated_at) "
                "VALUES ('r1', 'done', '{}', '{}', '2026-11-24', '2026-11-24')")
    con.commit()
    con.close()

    command.upgrade(cfg, AFTER)
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT id, customer_status, supplier_status, supplier_code FROM organizations "
                       "ORDER BY id").fetchall() == [
        (1, "active", None, None), (2, "potential", None, None),
        (3, None, None, None), (4, "potential", None, None)]
    assert con.execute("SELECT kind FROM issue_runs").fetchall() == [("project",)]
    con.close()
    cols = _columns(db_path, "organizations")
    assert {"supplier_code", "supplier_category", "customer_status", "supplier_status",
            "supplier_drive_folder_id", "supplier_drive_url"} <= set(cols)
    assert "VARCHAR(32)" in cols["tax_id"][2].upper()
    # the unique indexes survive the table rebuild
    idx = _indexes(db_path, "organizations")
    assert idx.get("uq_organizations_supplier_code") == 1
    assert idx.get("uq_organizations_tax_id") == 1
    assert idx.get("uq_organizations_customer_code") == 1

    command.downgrade(cfg, BEFORE)
    cols = _columns(db_path, "organizations")
    assert "supplier_code" not in cols and "customer_status" not in cols
    assert "kind" not in _columns(db_path, "issue_runs")
    assert "VARCHAR(16)" in _columns(db_path, "organizations")["tax_id"][2].upper()
