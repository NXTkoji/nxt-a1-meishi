"""add_collection_indexes

The first indexes on these tables. SQLite does not index foreign keys automatically,
so every filter and join above was a full scan. Immaterial at a few hundred rows; the
point is that removing the 200-card ceiling does not just replace it with a slow one.

These live in the migration only, deliberately: they are pure database indexes with no
ORM-visible behaviour, so adding index=True to the model columns would make the model
and this file two sources of truth for the same DDL.

The other half of that decision lives in migrations/env.py. Because the models do not
declare these indexes, `alembic revision --autogenerate` sees them as indexes nobody
wants and emits drop_index for every one it can reflect (seven of the eight — the
expression index below escapes only because SQLAlchemy cannot reflect it). The
_MIGRATION_ONLY_INDEXES set and the include_object() hook there hide exactly the eight
names created here. Add an index to this file and you must add its name there too.

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
    # its place on one query and one query only: the bare COUNT on /cards/count, which
    # reads deleted_at and nothing else and so gets a much smaller covering index than
    # the alternative. Drop this index and that count falls onto the far wider
    # ix_cards_filing_date, costing ~20% at 100,000 rows (2.51 ms -> 3.08 ms). At the
    # few-hundred-row scale of today that gap is indistinguishable from noise, so this
    # is insurance for growth rather than a present-day win.
    #
    # Correcting the commit message for 5b99ea9, which also justified keeping this
    # index on a ?month= list regression of 1.38 -> 1.71 ms: that does not reproduce.
    # With and without it the month-filtered list has an identical query plan and
    # identical timing (1.087 vs 1.086 ms at 5.2k rows; 7.35 vs 7.40 ms at 100k) — it
    # was measurement noise. The COUNT above is the whole of the case for keeping it.
    #
    # Every other query that filters on deleted_at gets a better index below or via
    # external_id's UNIQUE constraint.
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
# That list of call sites says where the expression appears in the *application*, not
# how much of each query the index serves — read it as the latter and you will expect
# too much. It supplies the deleted_at seek everywhere, and the complete ORDER BY for
# the default list. It does NOT serve the /cards/facets grouping: the group key is
# CAST(STRFTIME('%Y', coalesce(...)) AS INTEGER), not the indexed coalesce(...), so
# that plan still shows "USE TEMP B-TREE FOR GROUP BY". The ?month= predicate is
# likewise a row-by-row filter across the seeked range rather than a seek of its own.
#
# The key order is not cosmetic, and neither is the leading deleted_at:
#
#   * deleted_at first — _apply_card_filters() unconditionally adds "deleted_at IS
#     NULL" to every list/count/facets query. SQLite treats that as an equality
#     constraint, so it seeks straight to the live rows and then walks the remaining
#     key columns already in order. Without this leading column the planner prefers
#     ix_cards_deleted_at for the WHERE and falls back to a temp B-tree for the sort,
#     leaving a bare coalesce(...) index unused. Measured, not assumed — but state-
#     dependent, so note what state: it holds while the database carries no
#     statistics, which is this app's permanent condition. Nothing here or in the
#     application ever runs ANALYZE and there is no sqlite_stat1 table.
#
#     It is the ABSENCE of statistics that produces this, not their content. With no
#     sqlite_stat1 SQLite falls back to a default selectivity estimate for
#     "deleted_at = ?", which makes the narrow index look cheap. (An earlier note in
#     the plan doc and in commit 454a136 had this backwards, blaming sqlite_stat1 for
#     averaging over distinct values; there is no such table to average anything.)
#
#     Run ANALYZE and the planner *does* start choosing a bare coalesce(...) index for
#     the default list, so "never chosen" would be too strong a claim to hang this on.
#     The shipped index still wins in that state: on ?month= it measured 1.088 ms
#     against 2.013 ms for the bare index, because leading with deleted_at prunes to
#     the live rows instead of scanning the whole index. The decision holds either way.
#
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
