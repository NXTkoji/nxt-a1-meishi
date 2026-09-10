"""Collection scalability: facets, counts and pagination.

The load-bearing assertion here is test_facets_counts_match_month_query — if facets
and the ?month= filter ever disagree, a month header shows one number and expanding
it shows another.
"""
import asyncio
import itertools
from datetime import date, datetime

import pytest
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Card
from app.db.session import get_db
from app.main import app
from app.routers.v2.cards import _pick_name

# persons.external_id and cards.external_id are both unique, so every _seed_cards call
# needs its own namespace — otherwise a second call in the same test raises
# IntegrityError. This counter hands one out automatically.
_seed_serial = itertools.count()


def _seed_cards(specs, prefix=None):
    """Seed a cohort of cards and return their database ids, in spec order.

    Each spec is a tuple:
        (received_date | None, created_at)              — unnamed card
        (received_date | None, created_at, full_name)   — named card

    Every unnamed card in one call hangs off a single shared Person that has no
    PersonName at all, so ?q= can never match it. A named card gets its own Person
    plus one current PersonName — that is what makes ?q= behaviour testable.

    `prefix` namespaces the external_ids; leave it unset to get a fresh one, which is
    what allows a single test to seed two independent cohorts.
    """
    if prefix is None:
        prefix = f"s{next(_seed_serial)}"

    async def _run():
        from app.db.models import Card, Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            # The shared, nameless person — created lazily so a test seeding only
            # named cards does not leave an unreferenced row behind.
            shared_person_id = None

            ids = []
            for i, spec in enumerate(specs):
                recv, created = spec[0], spec[1]
                full_name = spec[2] if len(spec) > 2 else None

                if full_name is None:
                    if shared_person_id is None:
                        person = Person(external_id=f"p-{prefix}")
                        db.add(person)
                        await db.flush()
                        shared_person_id = person.id
                    person_id = shared_person_id
                else:
                    person = Person(external_id=f"p-{prefix}-{i}")
                    db.add(person)
                    await db.flush()
                    db.add(PersonName(
                        person_id=person.id,
                        language="en",
                        name_type="primary",
                        full_name=full_name,
                        is_current=True,
                    ))
                    person_id = person.id

                card = Card(
                    external_id=f"c-{prefix}-{i}",
                    person_id=person_id,
                    received_date=recv,
                    created_at=created,
                )
                db.add(card)
                await db.flush()
                ids.append(card.id)

            await db.commit()
            return ids

    # asyncio.run returns the coroutine's own result — no smuggling dict needed.
    return asyncio.run(_run())


def test_facets_groups_by_received_date(client_with_test_db):
    """received_date wins over created_at for bucketing."""
    _seed_cards([(date(2026, 3, 15), datetime(2026, 9, 1))])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 3, "count": 1}]


def test_facets_falls_back_to_created_at(client_with_test_db):
    _seed_cards([(None, datetime(2026, 9, 1))])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 9, "count": 1}]


def test_facets_newest_first(client_with_test_db):
    _seed_cards([
        (date(2026, 3, 1), datetime(2026, 3, 1)),
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2025, 12, 1), datetime(2025, 12, 1)),
    ])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert [(f["year"], f["month"]) for f in facets] == [(2026, 9), (2026, 3), (2025, 12)]


def test_facets_counts_match_month_query(client_with_test_db):
    """Every facet count equals what ?month= actually returns. The guarantee."""
    _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 9, 20), datetime(2026, 9, 20)),
        (None, datetime(2026, 8, 5)),
        (date(2025, 12, 31), datetime(2026, 1, 2)),
    ])

    facets = client_with_test_db.get("/api/v2/cards/facets").json()

    # Absolute expectation first. The loop below only proves facets and ?month= agree
    # with each other — both are built from the same expression, so breaking that
    # expression (say, to plain created_at) moves both together and the loop still
    # passes. This assertion pins the buckets themselves, and the last seeded card
    # (received 2025-12-31, created 2026-01-02) is what makes it also guard the
    # received_date-wins-over-created_at rule.
    assert facets == [
        {"year": 2026, "month": 9, "count": 2},
        {"year": 2026, "month": 8, "count": 1},
        {"year": 2025, "month": 12, "count": 1},
    ]

    for f in facets:
        month = f"{f['year']}-{f['month']:02d}"
        rows = client_with_test_db.get("/api/v2/cards", params={"month": month, "limit": 500}).json()
        assert len(rows) == f["count"], f"facet {month} says {f['count']}, month query returned {len(rows)}"


def test_facets_respects_q(client_with_test_db):
    """?q= narrows the buckets, not just the rows.

    q is the one filter that pushes correlated EXISTS subqueries into the GROUP BY
    statement, so it is the one most likely to break the facet/row agreement.
    """
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1), "Akira Matsumoto")])
    _seed_cards([(date(2026, 3, 1), datetime(2026, 3, 1), "Bruno Costa")])

    unfiltered = client_with_test_db.get("/api/v2/cards/facets").json()
    assert unfiltered == [
        {"year": 2026, "month": 9, "count": 1},
        {"year": 2026, "month": 3, "count": 1},
    ]

    narrowed = client_with_test_db.get(
        "/api/v2/cards/facets", params={"q": "Matsumoto"}
    ).json()
    assert narrowed == [{"year": 2026, "month": 9, "count": 1}]

    # And the narrowed count still matches the rows that bucket returns.
    rows = client_with_test_db.get(
        "/api/v2/cards", params={"q": "Matsumoto", "month": "2026-09", "limit": 500}
    ).json()
    assert len(rows) == 1


def test_facets_excludes_soft_deleted(client_with_test_db):
    ids = _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 9, 2), datetime(2026, 9, 2)),
    ])

    async def _soft_delete():
        from app.db.models import Card

        async for db in app.dependency_overrides[get_db]():
            card = await db.get(Card, ids[0])
            card.deleted_at = datetime.utcnow()
            await db.commit()
            break

    asyncio.run(_soft_delete())

    facets = client_with_test_db.get("/api/v2/cards/facets").json()
    assert facets == [{"year": 2026, "month": 9, "count": 1}]


def test_count_matches_list_length(client_with_test_db):
    _seed_cards([(date(2026, 9, i + 1), datetime(2026, 9, i + 1)) for i in range(7)])

    total = client_with_test_db.get("/api/v2/cards/count").json()["total"]
    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    assert total == len(rows) == 7


def test_count_respects_filters(client_with_test_db):
    _seed_cards([
        (date(2026, 9, 1), datetime(2026, 9, 1)),
        (date(2026, 8, 1), datetime(2026, 8, 1)),
    ])

    total = client_with_test_db.get("/api/v2/cards/count", params={"month": "2026-09"}).json()["total"]
    assert total == 1


def test_list_pagination_is_stable(client_with_test_db):
    """Three pages cover all 30 rows exactly once — no overlap, nothing skipped.

    All 30 cards deliberately share one received_date AND one created_at, so every row
    ties on the leading sort key (a bulk import writing a single timestamp does exactly
    this). That is the shape where a non-total ORDER BY *could* duplicate or skip rows.

    Be honest about what this proves, though: it does NOT guard the tiebreaker. SQLite
    returns tied rows in rowid order for this query plan, so removing Card.id from the
    ORDER BY — or removing the ORDER BY entirely — leaves this test passing. It is an
    end-to-end smoke test of paging; test_list_ordering_is_total below is what actually
    guards the total-order invariant.
    """
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1, 12, 0)) for _ in range(30)])

    page1 = client_with_test_db.get("/api/v2/cards", params={"limit": 10, "offset": 0}).json()
    page2 = client_with_test_db.get("/api/v2/cards", params={"limit": 10, "offset": 10}).json()
    page3 = client_with_test_db.get("/api/v2/cards", params={"limit": 10, "offset": 20}).json()

    assert len(page1) == len(page2) == len(page3) == 10
    seen = [c["id"] for c in page1 + page2 + page3]
    # No duplicates and nothing skipped: three pages must cover all 30 exactly once.
    assert len(set(seen)) == 30


def test_list_ordering_is_total(client_with_test_db, monkeypatch):
    """The ORDER BY list_cards emits must end in a unique column.

    Without a tiebreaker, LIMIT/OFFSET pagination can duplicate or skip rows whose
    sort keys tie: the database is free to return tied rows in a different order per
    query, so page 2 may repeat or omit a row from page 1. The leading key here is
    coalesce(received_date, created_at), which ties constantly — every card imported
    in one batch shares it.

    This invariant is *structural*, not behavioural, on SQLite: SQLite happens to
    return tied rows in rowid order for this query plan, so a paging test cannot
    observe the missing tiebreaker (see test_list_pagination_is_stable above, which
    still passes with the ORDER BY deleted outright). So assert on the SQL instead.

    The statement is captured from the live request rather than rebuilt here, so this
    test is bound to what the endpoint actually executes — mutating list_cards' own
    order_by fails it.
    """
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1, 12, 0)) for _ in range(3)])

    captured = []
    original_execute = AsyncSession.execute

    async def _spy_execute(self, statement, *args, **kwargs):
        captured.append(statement)
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _spy_execute)

    assert client_with_test_db.get("/api/v2/cards", params={"limit": 2}).status_code == 200

    # One request fires several statements (the card page, its eager-loaded sides, the
    # sync-history lookup). Keep only the one selecting Card entities — that is the
    # paginated statement whose ordering has to be total. The eager-load statement
    # selects CardSide, and the history one selects CardSyncHistory, so entity identity
    # separates them cleanly without matching on SQL text.
    card_selects = [
        s for s in captured
        if getattr(s, "column_descriptions", None)
        and s.column_descriptions[0].get("entity") is Card
        and s._order_by_clauses
    ]
    assert card_selects, "no ordered Card select was executed by GET /api/v2/cards"

    # Compile the ORDER BY clauses individually — safer than slicing the full SQL
    # string, which would also match an ORDER BY inside a subquery.
    order_by_sql = [
        str(clause.compile(dialect=sqlite_dialect())) for clause in card_selects[-1]._order_by_clauses
    ]
    assert any("cards.id" in clause for clause in order_by_sql), (
        f"ORDER BY {order_by_sql} has no unique column — LIMIT/OFFSET paging is unsound"
    )


def test_month_query_paginates(client_with_test_db):
    """A month holding more rows than `limit` is fully reachable via offset."""
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1, 0, i)) for i in range(25)])

    first = client_with_test_db.get(
        "/api/v2/cards", params={"month": "2026-09", "limit": 20, "offset": 0}
    ).json()
    rest = client_with_test_db.get(
        "/api/v2/cards", params={"month": "2026-09", "limit": 20, "offset": 20}
    ).json()

    assert len(first) == 20
    assert len(rest) == 5
    assert not ({c["id"] for c in first} & {c["id"] for c in rest})


# ---------------------------------------------------------------------------
# Parameter validation. Without a pattern these parse blindly server-side and
# raise, so malformed input came back as a 500 — and an unpadded "2026-9" was
# silently accepted, which would let a frontend that forgets padStart(2, "0")
# work by accident.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["/api/v2/cards", "/api/v2/cards/count"])
@pytest.mark.parametrize("params", [
    {"month": "garbage"},
    {"month": "2026-9"},     # unpadded month must be rejected, not silently accepted
    {"month": "2026-13"},
    {"date": "not-a-date"},
])
def test_malformed_date_params_are_rejected(client_with_test_db, endpoint, params):
    assert client_with_test_db.get(endpoint, params=params).status_code == 422


@pytest.mark.parametrize("endpoint", ["/api/v2/cards", "/api/v2/cards/count"])
def test_wellformed_date_params_are_accepted(client_with_test_db, endpoint):
    """The patterns must not reject input the endpoints are supposed to take."""
    assert client_with_test_db.get(endpoint, params={"month": "2026-09"}).status_code == 200
    assert client_with_test_db.get(endpoint, params={"date": "2026-09-01"}).status_code == 200


@pytest.mark.parametrize("endpoint", ["/api/v2/cards", "/api/v2/cards/count"])
@pytest.mark.parametrize("date_param", [
    "2026-13-45",   # month 13 and day 45 — both out of range
    "2026-02-30",   # correctly shaped, but February never has 30 days
    "2026-04-31",   # April has 30 days
    "2025-02-29",   # 2025 is not a leap year
])
def test_impossible_but_wellformed_dates_are_rejected(client_with_test_db, endpoint, date_param):
    """A date that passes the regex but is not a real day must be a 422, not a 500.

    No regex can reject 2026-02-30 or 2025-02-29 without encoding leap-year rules, so
    the pattern alone cannot make this safe — `_apply_card_filters` has to catch the
    ValueError from `date.fromisoformat` and turn it into a 422. Before that guard
    existed these four inputs raised an uncaught ValueError out of the endpoint.
    """
    assert client_with_test_db.get(endpoint, params={"date": date_param}).status_code == 422


@pytest.mark.parametrize("limit", [-1, 0])
def test_nonpositive_limit_is_rejected(client_with_test_db, limit):
    """?limit=-1 must be a 422, not the entire table.

    SQLite reads `LIMIT -1` as "no limit", so before the `ge=1` bound this returned
    every row with a 200 — which also meant an unbounded IN (...) in the batched name
    lookup and every current name held in memory. `offset` already had ge=0.
    """
    assert client_with_test_db.get(
        "/api/v2/cards", params={"limit": limit}
    ).status_code == 422


def _seed_person_with_names(person_ext_id, names, cards):
    """Seed one person carrying several PersonNames, plus cards pointing at them.

    _seed_cards cannot express this shape — it gives every named card its own person
    with exactly one current "en" PersonName, and has no way to set
    display_name_language. The point here is a person with *several* current names, so
    the preference rule has something to choose between.

    `names` is a list of (language, full_name, is_current) in INSERTION order, and that
    order is load-bearing: PersonName.id ascending is the resolver's tiebreak, so the
    first current entry is the fallback name.
    `cards` is a list of (external_id, display_name_language).

    Returns the cards' external_ids in the order given.
    """
    async def _run():
        from app.db.models import Card, Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id=person_ext_id)
            db.add(person)
            await db.flush()

            for language, full_name, is_current in names:
                db.add(PersonName(
                    person_id=person.id,
                    language=language,
                    name_type="legal",
                    full_name=full_name,
                    is_current=is_current,
                ))
            await db.flush()

            created = [
                Card(external_id=ext, person_id=person.id, display_name_language=lang)
                for ext, lang in cards
            ]
            db.add_all(created)
            await db.flush()
            await db.commit()
            return [c.external_id for c in created]

    return asyncio.run(_run())


def test_name_batch_honours_display_language(client_with_test_db):
    """display_name_language picks the matching name; otherwise lowest-id current name.

    This pins the user-visible rule that decides which script a person's name appears
    in on a collection thumbnail. It has to hold whether the names are resolved one
    card at a time or in a single batched query.
    """
    ext_en, ext_none, ext_ko = _seed_person_with_names(
        "p-names",
        names=[
            # "ja" first: lowest PersonName.id, therefore the fallback.
            ("ja", "山田太郎", True),
            ("en", "Taro Yamada", True),
            # Not current: must never be picked, even though it matches "ko" exactly.
            ("ko", "야마다", False),
        ],
        cards=[("c-en", "en"), ("c-none", None), ("c-ko", "ko")],
    )

    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["person_name"] for r in rows}

    # Preference honoured, even though the "en" name has the higher id.
    assert by_ext[ext_en] == "Taro Yamada"
    # No preference -> lowest-id current name.
    assert by_ext[ext_none] == "山田太郎"
    # Preference that matches no *current* name -> same fallback, never the
    # is_current=False Korean name.
    assert by_ext[ext_ko] == "山田太郎"


def test_name_batch_matches_language_by_prefix(client_with_test_db):
    """A card asking for "zh" takes a "zh-TW" name — the match is a prefix, not equality.

    Real data holds "zh-TW" and "zh" side by side, so a resolver that compared
    languages with == would silently fall back to the wrong script.
    """
    (ext_id,) = _seed_person_with_names(
        "p-prefix",
        # "en" first: it is the lowest-id current name, so it is the fallback the
        # test would land on if the prefix match stopped working.
        names=[("en", "Wang Da Ming", True), ("zh-TW", "王大明", True)],
        cards=[("c-zh", "zh")],
    )

    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["person_name"] for r in rows}
    assert by_ext[ext_id] == "王大明"


def test_name_batch_handles_person_with_no_names(client_with_test_db):
    """A card whose person has no current name at all reports person_name=None."""
    _seed_cards([(date(2026, 9, 1), datetime(2026, 9, 1))], prefix="nonames")

    rows = client_with_test_db.get("/api/v2/cards", params={"limit": 500}).json()
    assert [r["person_name"] for r in rows] == [None]


# ---------------------------------------------------------------------------
# _pick_name as a pure function — no HTTP, no database.
#
# The tests above pin the rule end to end, which is what proves the endpoint wires it
# up. These pin the rule itself, and reach two cases the seeder cannot express: an
# empty full_name and an empty candidate list.
#
# `candidates` is always (language, full_name) pairs ordered by PersonName.id
# ascending — the ordering list_cards' batched query is responsible for producing.
# ---------------------------------------------------------------------------

# One person, two current names, "ja" holding the lower id (so "ja" is the fallback).
JA_THEN_EN = [("ja", "山田太郎"), ("en", "Taro Yamada")]


def test_pick_name_prefers_the_matching_language():
    """A preference that matches wins over the lowest-id name."""
    assert _pick_name(JA_THEN_EN, "en") == "Taro Yamada"


def test_pick_name_matches_language_by_prefix():
    """"zh" must take a "zh-TW" name — a prefix test, not equality."""
    candidates = [("en", "Wang Da Ming"), ("zh-TW", "王大明")]
    assert _pick_name(candidates, "zh") == "王大明"


def test_pick_name_prefix_match_is_case_insensitive():
    """SQL LIKE was case-insensitive for ASCII; str.startswith is not.

    Both operands are asserted, so lowering only one of them fails here.
    """
    # Candidate cased, preference lowercase.
    assert _pick_name([("ja", "山田太郎"), ("EN-GB", "Taro Yamada")], "en") == "Taro Yamada"
    # Preference cased, candidate lowercase.
    assert _pick_name([("ja", "山田太郎"), ("en-gb", "Taro Yamada")], "EN") == "Taro Yamada"


def test_pick_name_falls_back_when_the_preference_matches_nothing():
    """An unmatched preference lands on the lowest-id name, not on nothing."""
    assert _pick_name(JA_THEN_EN, "ko") == "山田太郎"


def test_pick_name_falls_back_when_no_preference_is_expressed():
    """display_name_language=None -> the lowest-id current name."""
    assert _pick_name(JA_THEN_EN, None) == "山田太郎"


def test_pick_name_returns_none_without_candidates():
    """A person with no current names at all resolves to None, not to a crash."""
    assert _pick_name([], "en") is None
    assert _pick_name([], None) is None


def test_pick_name_empty_match_falls_through_to_the_fallback():
    """An empty full_name on the FIRST language match does not try the next match.

    The old SQL took `LIMIT 1` and then tested `if preferred:`, so an empty lowest-id
    language match fell through to the unfiltered fallback rather than to the second
    "en" name. Only reachable via the empty string (full_name is NOT NULL), and pinned
    here because no HTTP-level test can express it.
    """
    candidates = [("ja", "山田太郎"), ("en", ""), ("en", "Taro Yamada")]
    assert _pick_name(candidates, "en") == "山田太郎"
