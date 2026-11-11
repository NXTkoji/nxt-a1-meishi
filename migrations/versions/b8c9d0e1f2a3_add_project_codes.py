"""add_project_codes

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-11

NXT customer fields on organizations, plus the projects and issue_runs tables
used by project code issuing.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column(
        'is_nxt_customer', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('organizations', sa.Column('tax_id', sa.String(16), nullable=True))
    op.add_column('organizations', sa.Column('customer_code', sa.String(16), nullable=True))
    op.add_column('organizations', sa.Column('odoo_partner_id', sa.Integer(), nullable=True))
    op.add_column('organizations', sa.Column('drive_folder_id', sa.String(128), nullable=True))
    op.add_column('organizations', sa.Column('drive_url', sa.String(512), nullable=True))
    op.add_column('organizations', sa.Column('legacy_codes', sa.String(256), nullable=True))
    # Unique indexes allow many NULLs in SQLite, so unset values never collide.
    op.create_index('uq_organizations_tax_id', 'organizations', ['tax_id'], unique=True)
    op.create_index('uq_organizations_customer_code', 'organizations', ['customer_code'], unique=True)

    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('product_line', sa.String(8), nullable=False),
        sa.Column('site_name', sa.String(256), nullable=True),
        sa.Column('sold_via_org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('odoo_project_id', sa.Integer(), nullable=True),
        sa.Column('drive_folder_id', sa.String(128), nullable=True),
        sa.Column('drive_url', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('code', name='uq_projects_code'),
    )
    op.create_index('ix_projects_org_id', 'projects', ['org_id'])

    op.create_table(
        'issue_runs',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('external_id', sa.String(36), nullable=False, unique=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('input_json', sa.Text(), nullable=False),
        sa.Column('steps_json', sa.Text(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('issue_runs')
    op.drop_index('ix_projects_org_id', table_name='projects')
    op.drop_table('projects')
    op.drop_index('uq_organizations_customer_code', table_name='organizations')
    op.drop_index('uq_organizations_tax_id', table_name='organizations')
    with op.batch_alter_table('organizations') as batch:
        for col in ('legacy_codes', 'drive_url', 'drive_folder_id', 'odoo_partner_id',
                    'customer_code', 'tax_id', 'is_nxt_customer'):
            batch.drop_column(col)
