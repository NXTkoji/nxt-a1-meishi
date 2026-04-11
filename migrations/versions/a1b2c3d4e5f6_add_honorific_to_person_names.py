"""add_honorific_to_person_names

Revision ID: a1b2c3d4e5f6
Revises: 99950262e015
Create Date: 2026-04-07

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '99950262e015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('person_names', sa.Column('honorific', sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column('person_names', 'honorific')
