"""add_card_sync_history

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-25

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'card_sync_history',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('cards.id'), nullable=False),
        sa.Column('destination', sa.String(32), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    op.create_index('ix_card_sync_history_card_id', 'card_sync_history', ['card_id'])


def downgrade() -> None:
    op.drop_index('ix_card_sync_history_card_id', table_name='card_sync_history')
    op.drop_table('card_sync_history')
