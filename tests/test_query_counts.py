"""Performance guard: collection endpoints must not issue a query per row.

Without this, an N+1 can creep back silently — it stays fast on a small database
and only hurts once a real collection grows. The live database holds ~200 cards, at
which size a per-card lookup costs about 60ms and is invisible; at 10,000 cards the
same code shape is 10,000-20,000 sequential round trips.

The counting is done by the `query_counter` fixture in tests/conftest.py, which hooks
SQLAlchemy's `before_cursor_execute` on the *test* engine, so it counts real statements
sent to SQLite, not ORM calls.

The two tests below catch different regressions and both are needed:
  * the constant-bound test is the only one bounding the constant — five new
    unconditional queries would keep the flat test's two cohorts equal and sail through;
  * the flat test is the only one catching growth proportional to rows — a smaller
    constant plus a cheap N+1 can still sit under a fixed threshold.
"""
from datetime import date, datetime

import pytest

# Reuse the existing seeder rather than growing a second one. It gives every named
# card its own Person plus one current PersonName, which is exactly the shape that
# made the old per-card resolver issue a query per row.
from tests.test_collection_scalability import _seed_cards

# Every collection endpoint whose query count must be independent of the row count.
# /api/v2/persons joined this list once its name and contact-detail lookups were
# batched — it used to run two SELECTs per person and would have failed here.
BOUNDED_ENDPOINTS = [
    "/api/v2/cards",
    "/api/v2/cards/facets",
    "/api/v2/cards/count",
    "/api/v2/persons",
    "/api/v2/persons/count",
]


@pytest.mark.parametrize("endpoint", BOUNDED_ENDPOINTS)
def test_card_list_costs_a_constant_number_of_queries(
    client_with_test_db, query_counter, endpoint
):
    """60 rows must cost a fixed handful of queries, not one or two per row.

    The seeder gives every named card its own Person, so 60 cards is also 60 persons —
    one cohort exercises both collections.
    """
    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"Person {i}") for i in range(60)],
        prefix="qc",
    )

    resp, n = query_counter(
        client_with_test_db,
        lambda: client_with_test_db.get(endpoint, params={"limit": 500}),
    )

    assert resp.status_code == 200
    # GET /api/v2/cards is the busiest, at four statements: the card page, its
    # eager-loaded sides (selectinload), the sync-history lookup, and the batched name
    # lookup. GET /api/v2/persons is three: the person page, the batched name lookup
    # and the batched country lookup. /facets and both /count endpoints are one
    # statement each. The bound sits just above four on purpose — the previous `< 15`
    # left room for a 3.5x constant regression.
    assert n <= 6, f"{n} queries for 60 rows on {endpoint} — N+1 regression"


# Only the list endpoints: /facets and /count return a scalar or a handful of buckets,
# so "rows returned" is not a meaningful axis for them.
@pytest.mark.parametrize("endpoint", ["/api/v2/cards", "/api/v2/persons"])
def test_list_query_count_is_flat_in_row_count(
    client_with_test_db, query_counter, endpoint
):
    """The stronger claim: doubling the rows must not change the query count.

    A fixed threshold can be satisfied by accident (a smaller constant, a cheaper
    N+1). This compares two cohorts in the same database, so only a query shape that
    is genuinely independent of row count passes.

    Each named card gets its own Person, so the two cohorts are 20 then 40 rows on
    either endpoint.
    """
    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"A {i}") for i in range(20)],
        prefix="flat-a",
    )
    small, n_small = query_counter(
        client_with_test_db,
        lambda: client_with_test_db.get(endpoint, params={"limit": 500}).json(),
    )

    _seed_cards(
        [(date(2026, 9, 1), datetime(2026, 9, 1, 0, i % 60), f"B {i}") for i in range(20)],
        prefix="flat-b",
    )
    big, n_big = query_counter(
        client_with_test_db,
        lambda: client_with_test_db.get(endpoint, params={"limit": 500}).json(),
    )

    assert len(small) == 20 and len(big) == 40
    assert n_big == n_small, (
        f"{n_small} queries for 20 rows but {n_big} for 40 on {endpoint} "
        "— query count scales with rows"
    )
