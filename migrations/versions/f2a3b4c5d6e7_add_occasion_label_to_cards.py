"""add_occasion_label_to_cards

Revision ID: f2a3b4c5d6e7
Revises: a3b4c5d6e7f8
Create Date: 2026-09-09

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cards', sa.Column('occasion_label', sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column('cards', 'occasion_label')
