"""add_birthday_to_persons

Revision ID: e1f2a3b4c5d6
Revises: d15c68815a27
Create Date: 2026-07-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd15c68815a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('persons', sa.Column('birthday', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('persons', 'birthday')
