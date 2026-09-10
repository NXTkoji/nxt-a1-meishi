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

import sqlalchemy as sa
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
    # deleted_at is NULL for ~98% of rows, so this is useless as a *filter*. It earns
    # its place only as a small covering index for the bare COUNT on /cards/count,
    # which reads deleted_at and nothing else. Every other query that filters on
    # deleted_at gets a better index below or via external_id's UNIQUE constraint.
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

# The index that backs the Collection list. It is an *expression* index because the
# application never orders or buckets by a stored column: _filing_date() in
# app/routers/v2/cards.py is coalesce(received_date, created_at), and the default
# ORDER BY, the ?year=/?month=/?date= filters and the /cards/facets GROUP BY are all
# built from that one expression. No single-column index on either underlying column
# can serve it — that is why ix_cards_created_at and ix_cards_received_date were
# dropped from this migration rather than kept: nothing the app issues could use them.
#
# The key order is not cosmetic, and neither is the leading deleted_at:
#
#   * deleted_at first — _apply_card_filters() unconditionally adds "deleted_at IS
#     NULL" to every list/count/facets query. SQLite treats that as an equality
#     constraint, so it seeks straight to the live rows and then walks the remaining
#     key columns already in order. Without this leading column the planner prefers
#     ix_cards_deleted_at for the WHERE and falls back to a temp B-tree for the sort,
#     leaving a bare coalesce(...) index unused — measured, not assumed.
#   * coalesce(...) DESC, id DESC — must match list_cards' ORDER BY
#     (_filing_date().desc(), Card.id.desc()) exactly, including the directions.
#     If either differs the index cannot supply the ordering and the sort comes back.
#
# Verified with EXPLAIN QUERY PLAN on the real emitted SQL: the Collection list goes
# from "SEARCH cards USING INDEX ix_cards_deleted_at" + "USE TEMP B-TREE FOR ORDER BY"
# to a single "SEARCH cards USING INDEX ix_cards_filing_date (deleted_at=?)".
_FILING_DATE_INDEX = "ix_cards_filing_date"
_FILING_DATE_COLUMNS = [
    "deleted_at",
    sa.text("coalesce(received_date, created_at) DESC"),
    sa.text("id DESC"),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols)
    op.create_index(_FILING_DATE_INDEX, "cards", _FILING_DATE_COLUMNS)


def downgrade() -> None:
    op.drop_index(_FILING_DATE_INDEX, table_name="cards")
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
