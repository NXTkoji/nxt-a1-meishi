"""add_notes_to_organizations

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-10-13

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Shared memo per organization (see Organization.notes).
    op.add_column('organizations', sa.Column('notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'notes')
