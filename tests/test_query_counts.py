"""Performance guard: the card list must not issue a query per card.

Without this, an N+1 can creep back silently — it stays fast on a small database
and only hurts once a real collection grows. The live database holds ~200 cards, at
which size a per-card lookup costs about 60ms and is invisible; at 10,000 cards the
same code shape is 10,000-20,000 sequential round trips.

The counter hooks SQLAlchemy's `before_cursor_execute` on the *test* engine, so it
counts real statements sent to SQLite, not ORM calls.
"""
from datetime import date, datetime

from sqlalchemy import event

# Reuse the existing seeder rather than growing a second one. It gives every named
# card its own Person plus one current PersonName, which is exactly the shape that
# made the old per-card resolver issue a query per row.
from tests.test_collection_scalability import _seed_cards


def _count_queries(client, fn):
    """Run `fn` with a statement counter attached to the test engine; return (result, n)."""
    engine = client.session_maker.kw["bind"]
    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    # The async engine wraps a sync engine; events live on the sync one.
    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        result = fn()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
    return result, counter["n"]


def test_card_list_query_count_does_not_scale_with_rows(client_with_test_db):
    """60 cards must cost a fixed handful of queries, not one or two per card."""
    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"Person {i}") for i in range(60)],
        prefix="qc",
    )

    rows, n = _count_queries(
        client_with_test_db,
        lambda: client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json(),
    )

    assert len(rows) == 60
    # Cards + sides + sync history + names ≈ a handful, plus whatever the session
    # emits around the transaction. Anything near 60 is an N+1.
    assert n < 15, f"{n} queries for 60 cards — N+1 regression"


def test_card_list_query_count_is_flat_in_row_count(client_with_test_db):
    """The stronger claim: doubling the rows must not change the query count.

    A fixed threshold can be satisfied by accident (a smaller constant, a cheaper
    N+1). This compares two cohorts in the same database, so only a query shape that
    is genuinely independent of row count passes.
    """
    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"A {i}") for i in range(20)],
        prefix="flat-a",
    )
    small, n_small = _count_queries(
        client_with_test_db,
        lambda: client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json(),
    )

    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"B {i}") for i in range(20)],
        prefix="flat-b",
    )
    big, n_big = _count_queries(
        client_with_test_db,
        lambda: client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json(),
    )

    assert len(small) == 20 and len(big) == 40
    assert n_big == n_small, (
        f"{n_small} queries for 20 cards but {n_big} for 40 — query count scales with rows"
    )
