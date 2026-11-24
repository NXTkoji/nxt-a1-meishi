"""add_party_roles

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-24

Supplier role and customer/supplier statuses on organizations, plus a kind on
issue runs (project or supplier). tax_id is widened for foreign tax numbers.
Status NULL means "none".
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("supplier_code", sa.String(16), nullable=True))
        batch.add_column(sa.Column("supplier_category", sa.String(16), nullable=True))
        batch.add_column(sa.Column("customer_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("supplier_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("supplier_drive_folder_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("supplier_drive_url", sa.String(512), nullable=True))
        batch.alter_column("tax_id", existing_type=sa.String(16), type_=sa.String(32),
                           existing_nullable=True)
    op.create_index("uq_organizations_supplier_code", "organizations", ["supplier_code"], unique=True)
    with op.batch_alter_table("issue_runs") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="project"))

    op.execute("UPDATE organizations SET customer_status = 'active' "
               "WHERE customer_code IS NOT NULL AND customer_code <> ''")
    op.execute("UPDATE organizations SET customer_status = 'potential' "
               "WHERE customer_status IS NULL AND is_nxt_customer = 1")


def downgrade() -> None:
    with op.batch_alter_table("issue_runs") as batch:
        batch.drop_column("kind")
    op.drop_index("uq_organizations_supplier_code", table_name="organizations")
    with op.batch_alter_table("organizations") as batch:
        batch.alter_column("tax_id", existing_type=sa.String(32), type_=sa.String(16),
                           existing_nullable=True)
        for col in ("supplier_drive_url", "supplier_drive_folder_id", "supplier_status",
                    "customer_status", "supplier_category", "supplier_code"):
            batch.drop_column(col)
