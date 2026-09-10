"""add_collection_indexes

The first indexes on these tables. SQLite does not index foreign keys automatically,
so every filter and join above was a full scan. Immaterial at a few hundred rows; the
point is that removing the 200-card ceiling does not just replace it with a slow one.

These live in the migration only, deliberately: they are pure database indexes with no
ORM-visible behaviour, so adding index=True to the model columns would make the model
and this file two sources of truth for the same DDL.

Revision ID: a3b4c5d6e7f8
Revises: e1f2a3b4c5d6
Create Date: 2026-09-10

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, columns). Order matters only for downgrade, which walks it
# in reverse so the drops mirror the creates.
_INDEXES = [
    # Plain foreign-key / filter indexes on cards. SQLite creates no index for a
    # REFERENCES clause, so ?person_id= and ?occasion_id= were full scans.
    ("ix_cards_person_id", "cards", ["person_id"]),
    ("ix_cards_occasion_id", "cards", ["occasion_id"]),
    ("ix_cards_created_at", "cards", ["created_at"]),
    ("ix_cards_received_date", "cards", ["received_date"]),
    ("ix_cards_deleted_at", "cards", ["deleted_at"]),
    # Composite, not single-column: the batched lookups in Tasks 3 and 4 filter on
    # person_id AND is_current / detail_type together, and then ORDER BY the same
    # leading column. A single-column index on person_id alone would satisfy the
    # IN (...) but still leave the second predicate as a row-by-row filter and the
    # sort as a temp B-tree. Verified on live data that the name query currently does
    # "SCAN person_names" + "USE TEMP B-TREE FOR ORDER BY".
    ("ix_person_names_person_current", "person_names", ["person_id", "is_current"]),
    # Same reasoning: the persons list selects country codes for a whole page with
    # person_id IN (...) AND detail_type IN ('address_home', 'address_work'), ordered
    # by (person_id, detail_type, id) — so the index key order matches the sort order.
    ("ix_contact_details_person_type", "contact_details", ["person_id", "detail_type"]),
    ("ix_positions_person_id", "positions", ["person_id"]),
    ("ix_card_sync_history_card_id", "card_sync_history", ["card_id"]),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
