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

from app.db.models import Card, Person
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


def test_list_ordering_is_total(client_with_test_db, captured_statements):
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

    captured = captured_statements()

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


def _seed_person_with_names(person_ext_id, names, cards=(), contact_details=()):
    """Seed one person carrying several PersonNames, plus cards pointing at them.

    _seed_cards cannot express this shape — it gives every named card its own person
    with exactly one current "en" PersonName, and has no way to set
    display_name_language. The point here is a person with *several* current names, so
    the preference rule has something to choose between.

    `names` is a list of (language, full_name, is_current) in INSERTION order, and that
    order is load-bearing: PersonName.id ascending is the resolver's tiebreak, so the
    first current entry is the fallback name. A name may carry an optional fourth
    element, family_name — the Persons tab sorts on it — which defaults to None.
    `cards` is a list of (external_id, display_name_language); it defaults to empty
    because the persons endpoint does not need cards to exist at all.
    `contact_details` is a list of (detail_type, country_code) in INSERTION order, and
    that order is load-bearing too: ContactDetail.id ascending is the persons list's
    tiebreak once detail_type has been compared.

    Returns the cards' external_ids in the order given.
    """
    async def _run():
        from app.db.models import Card, ContactDetail, Person, PersonName

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id=person_ext_id)
            db.add(person)
            await db.flush()

            for name in names:
                language, full_name, is_current = name[:3]
                family_name = name[3] if len(name) > 3 else None
                db.add(PersonName(
                    person_id=person.id,
                    language=language,
                    name_type="legal",
                    full_name=full_name,
                    family_name=family_name,
                    is_current=is_current,
                ))
                # Flushed one at a time so PersonName.id ascends in the order given;
                # both the name fold and the sort-name rule tiebreak on it.
                await db.flush()

            # Inserted one at a time with a flush between, so the ids ascend in the
            # order given — the ordering the country preference rule tiebreaks on.
            for detail_type, country_code in contact_details:
                db.add(ContactDetail(
                    person_id=person.id,
                    detail_type=detail_type,
                    value=f"{detail_type} value",
                    country_code=country_code,
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


# ---------------------------------------------------------------------------
# Persons list. The Persons tab of the Collection page reads GET /api/v2/persons,
# whose search branch used to apply neither LIMIT, OFFSET nor ORDER BY — it returned
# every match in whatever order SQLite felt like. Browse was capped at 200, below the
# live person count, so the tail was unreachable.
# ---------------------------------------------------------------------------


def _seed_persons(n, name_prefix="Tester", prefix=None):
    """Seed n persons, each with exactly one current PersonName.

    An adapter over _seed_cards rather than a third seeder: its named-spec form
    already creates one Person plus one current PersonName per spec, which is exactly
    the shape these tests need. Keeping one place that knows how a named person is
    built means a schema change lands in one seeder, not three.

    The cards it also creates are irrelevant here — the persons endpoint never looks
    at them.
    """
    _seed_cards(
        [(None, datetime(2026, 9, 1, 0, i % 60), f"{name_prefix} {i}") for i in range(n)],
        prefix=prefix,
    )


def test_persons_search_is_bounded(client_with_test_db):
    """A query matching more than `limit` persons returns `limit`, not everything.

    The search branch built `select(Person).where(Person.id.in_(all_ids))` bare, so
    ?q= ignored the limit entirely: 60 seeded persons came back for limit=10.
    """
    _seed_persons(60)

    rows = client_with_test_db.get("/api/v2/persons", params={"q": "Tester", "limit": 10}).json()
    assert len(rows) == 10


def test_persons_are_newest_first(client_with_test_db):
    """The list comes back in descending created_at — newest person first.

    This pins the sort DIRECTION, which nothing else did: flipping the endpoint's
    order_by to created_at.asc() left every other test green, even though "newest
    first" is what the Persons tab shows and what a later pager will assume.

    It replaces a test that asserted two identical requests returned the same order —
    a property of SQLite's query plan, not of this code, which no mutation could kill
    (not even deleting the order_by outright). The *totality* of the ordering is
    test_persons_ordering_is_total's job; this one owns the direction.

    _seed_persons gives each person a distinct created_at, so the expected order is
    unambiguous rather than a tie broken by id.
    """
    _seed_persons(30)

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    assert len(rows) == 30
    timestamps = [r["created_at"] for r in rows]
    # Guard the guard: if the seeder ever produced tied timestamps, the ordering
    # assertion below would hold in BOTH directions and quietly stop testing anything.
    assert len(set(timestamps)) == 30, "seeded created_at values tied — assertion vacuous"
    assert timestamps == sorted(timestamps, reverse=True)


def test_persons_search_paginates(client_with_test_db):
    """Two adjacent pages of one search share no rows — offset must be honoured."""
    _seed_persons(30)

    p1 = client_with_test_db.get(
        "/api/v2/persons", params={"q": "Tester", "limit": 10, "offset": 0}
    ).json()
    p2 = client_with_test_db.get(
        "/api/v2/persons", params={"q": "Tester", "limit": 10, "offset": 10}
    ).json()
    assert len(p1) == len(p2) == 10
    assert not ({r["id"] for r in p1} & {r["id"] for r in p2})


def test_persons_ordering_is_total(client_with_test_db, captured_statements):
    """The ORDER BY list_persons emits must end in a unique column.

    Same argument as test_list_ordering_is_total for cards, and the same reason it has
    to be structural: every person seeded in one batch shares a created_at to the
    microsecond, and SQLite happens to return such ties in rowid order, so a paging
    test cannot observe a missing tiebreaker. Without persons.id in the ORDER BY,
    LIMIT/OFFSET paging over tied created_at values may repeat or skip rows.

    Asserted against the statement the endpoint actually executes, not a rebuilt one.
    """
    _seed_persons(3)

    captured = captured_statements()

    assert client_with_test_db.get("/api/v2/persons", params={"limit": 2}).status_code == 200

    # One request fires three statements: the person page, the batched name lookup and
    # the batched country lookup. Only the first selects Person entities, which is how
    # it is picked out without matching on SQL text.
    person_selects = [
        s for s in captured
        if getattr(s, "column_descriptions", None)
        and s.column_descriptions[0].get("entity") is Person
        and s._order_by_clauses
    ]
    assert person_selects, "no ordered Person select was executed by GET /api/v2/persons"

    order_by_sql = [
        str(clause.compile(dialect=sqlite_dialect()))
        for clause in person_selects[-1]._order_by_clauses
    ]
    assert any("persons.id" in clause for clause in order_by_sql), (
        f"ORDER BY {order_by_sql} has no unique column — LIMIT/OFFSET paging is unsound"
    )


def test_persons_limit_accepts_500(client_with_test_db):
    """The cap was 200; the Collection page needs to match cards at 500.

    202 persons live meant the last two were unreachable at any limit.
    """
    _seed_persons(5)

    assert client_with_test_db.get("/api/v2/persons", params={"limit": 500}).status_code == 200


@pytest.mark.parametrize("limit", [-1, 0])
def test_persons_nonpositive_limit_is_rejected(client_with_test_db, limit):
    """?limit=-1 must be a 422, not the entire table.

    SQLite reads `LIMIT -1` as "no limit", so without `ge=1` this returns every person
    with a 200 — and an unbounded IN (...) in both batched lookups behind it.
    """
    assert client_with_test_db.get(
        "/api/v2/persons", params={"limit": limit}
    ).status_code == 422


def test_persons_count_matches_search(client_with_test_db):
    """/count and the list agree on what a search matches.

    They share _person_ids_matching precisely so they cannot drift apart; this is the
    test that would catch them being given separate copies of the rule.

    The second cohort exists so the answer is not simply "every person in the table" —
    a /count that ignored ?q= would otherwise agree by accident.
    """
    _seed_persons(12)
    _seed_persons(5, name_prefix="Bystander")

    total = client_with_test_db.get(
        "/api/v2/persons/count", params={"q": "Tester"}
    ).json()["total"]
    rows = client_with_test_db.get(
        "/api/v2/persons", params={"q": "Tester", "limit": 500}
    ).json()
    assert total == len(rows) == 12


def _seed_person_at_org(person_ext_id, person_name, org_name):
    """Seed one person whose CURRENT organisation name is `org_name`.

    Neither existing seeder can express this: _seed_cards and _seed_person_with_names
    both stop at PersonName, and nothing in the suite ever created an Organization,
    OrganizationName or Position. That is exactly why the organisation half of
    _person_ids_matching had no coverage — it could be deleted outright and the whole
    suite stayed green.

    The three rows are the minimum the search join needs: Position links the person to
    the org, and OrganizationName carries the searchable text with is_current=True.
    """
    async def _run():
        from app.db.models import (
            Organization, OrganizationName, Person, PersonName, Position,
        )

        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id=person_ext_id)
            db.add(person)
            org = Organization(external_id=f"o-{person_ext_id}")
            db.add(org)
            await db.flush()

            db.add(PersonName(
                person_id=person.id,
                language="en",
                name_type="primary",
                full_name=person_name,
                is_current=True,
            ))
            db.add(OrganizationName(
                org_id=org.id,
                language="en",
                name=org_name,
                is_current=True,
            ))
            db.add(Position(person_id=person.id, org_id=org.id))
            await db.flush()
            await db.commit()

    asyncio.run(_run())


def test_persons_search_matches_organisation_name(client_with_test_db):
    """?q= finds a person by their CURRENT organisation name, not just their own name.

    GET /api/v2/persons documents itself as "Search by name or organisation", and the
    Collection page's Cards tab relies on the organisation half: it feeds the matched
    person ids into the card filter, so a card whose person matched only by company
    disappears if that half breaks. Until this test, deleting the matched_org_ids
    query from _person_ids_matching left every other test passing.

    The person's own name deliberately shares no substring with the query, so only the
    organisation join can produce the match. The bystander proves the search still
    discriminates — a broken filter returning everyone would otherwise pass.
    """
    _seed_person_at_org("p-at-org", person_name="Alice Smith", org_name="Rotary Club of Taipei")
    _seed_person_at_org("p-elsewhere", person_name="Bob Jones", org_name="Some Other Company")

    rows = client_with_test_db.get(
        "/api/v2/persons", params={"q": "Rotary", "limit": 500}
    ).json()
    assert [r["external_id"] for r in rows] == ["p-at-org"]

    # /count shares _person_ids_matching with the list, so it must see the same match.
    total = client_with_test_db.get(
        "/api/v2/persons/count", params={"q": "Rotary"}
    ).json()["total"]
    assert total == 1


def test_persons_count_matches_unfiltered_list(client_with_test_db):
    """With no ?q=, /count is the total the pager needs to size itself."""
    _seed_persons(7)

    total = client_with_test_db.get("/api/v2/persons/count").json()["total"]
    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    assert total == len(rows) == 7


def test_persons_count_of_nothing_is_zero(client_with_test_db):
    """A search matching no person is 0, not a NULL leaking through as None."""
    _seed_persons(3)

    assert client_with_test_db.get(
        "/api/v2/persons/count", params={"q": "nobody-by-that-name"}
    ).json()["total"] == 0


def test_persons_country_prefers_home_over_work(client_with_test_db):
    """country_code takes the home address when both kinds exist.

    The rule is expressed as ORDER BY detail_type ASC, which only produces
    "home before work" because "address_home" < "address_work" alphabetically. The
    work address is inserted FIRST here so a lower ContactDetail.id cannot be what
    makes this pass — only the detail_type ordering can.
    """
    _seed_person_with_names(
        "p-country",
        names=[("en", "Home And Work", True)],
        contact_details=[("address_work", "US"), ("address_home", "JP")],
    )

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["country_code"] for r in rows}
    assert by_ext["p-country"] == "JP"


def test_persons_country_ignores_non_address_details(client_with_test_db):
    """A country_code on a phone or email row is never shown.

    The detail_type.in_(["address_home", "address_work"]) filter is only OBSERVABLE on
    a person who has no address at all — p-phoneonly below. The p-nonaddress case
    cannot see it: "address_work" < "phone_work" alphabetically, so the endpoint's
    `detail_type ASC` returns TW first whether or not the filter is there. Both are
    kept, because together they say "prefer the address" AND "never fall back to a
    non-address"; only the second one dies when the filter is deleted.
    """
    _seed_person_with_names(
        "p-nonaddress",
        names=[("en", "Phone First", True)],
        contact_details=[("phone_work", "FR"), ("address_work", "TW")],
    )
    # No address row at all: without the filter, this person's phone country leaks out
    # as their country_code.
    _seed_person_with_names(
        "p-phoneonly",
        names=[("en", "Phone Only", True)],
        contact_details=[("phone_work", "FR")],
    )

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["country_code"] for r in rows}
    assert by_ext["p-nonaddress"] == "TW"
    assert by_ext["p-phoneonly"] is None


def test_persons_country_skips_null_country_codes(client_with_test_db):
    """An address with no country_code is skipped, not returned as the answer.

    The NULL home address sorts first on every key, so a batch fold that kept the
    first row per person without filtering NULLs out in SQL would report None here.
    """
    _seed_person_with_names(
        "p-nullcountry",
        names=[("en", "Null Home", True)],
        contact_details=[("address_home", None), ("address_work", "JP")],
    )

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["country_code"] for r in rows}
    assert by_ext["p-nullcountry"] == "JP"


def test_persons_name_is_lowest_id_current_name(client_with_test_db):
    """primary_name/family_name come from the lowest-id CURRENT name.

    Unlike cards, the persons list takes no per-row language preference — the rule is
    just "first current name by id", so the batch fold has to keep the first row under
    ORDER BY person_id, id. The non-current name is seeded first, with the lowest id,
    so a fold that forgot the is_current filter would pick it.
    """
    _seed_person_with_names(
        "p-names-order",
        names=[
            ("ja", "旧姓名", False),      # lowest id, but not current
            ("en", "Current One", True),  # the expected answer
            ("ja", "山田太郎", True),      # current but higher id
        ],
    )

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    by_ext = {r["external_id"]: r["primary_name"] for r in rows}
    assert by_ext["p-names-order"] == "Current One"


def test_persons_with_no_name_or_country_are_still_listed(client_with_test_db):
    """A bare person still appears, with primary_name and country_code null.

    A batched lookup that inner-joined names would drop this row entirely.
    """
    _seed_person_with_names("p-bare", names=[])

    rows = client_with_test_db.get("/api/v2/persons", params={"limit": 500}).json()
    bare = [r for r in rows if r["external_id"] == "p-bare"]
    assert len(bare) == 1
    assert bare[0]["primary_name"] is None
    assert bare[0]["family_name"] is None
    assert bare[0]["country_code"] is None


# ---------------------------------------------------------------------------
# Persons tab as country groups. GET /api/v2/persons/facets returns each country with
# its count; each group then loads its own people with ?country=, in display order.
#
# The load-bearing guarantee mirrors the card tree's: a facet count must equal what its
# ?country= query returns and what /count?country= reports. Otherwise a group header
# shows one number and opening the group shows another.
#
# A `null` facet (no home or work address carrying a country code) is queried as
# ?country=none — a query string cannot carry a null.
# ---------------------------------------------------------------------------


def _country_param(code):
    """The ?country= value that selects a facet: the code itself, or "none" for null."""
    return "none" if code is None else code


def _seed_country_mix():
    """Seven persons covering every branch of the country rule.

    Expected buckets: JP 1, TW 3, US 1, null 2. Each person exists to pin one rule:
      * p-tw-home, p-jp-home — the plain case, a home address
      * p-tw-work           — a work address alone still counts
      * p-mixed             — work JP inserted FIRST, then home TW: home wins, and
                              a lower ContactDetail.id cannot be why
      * p-nullhome          — home address with a NULL code falls back to work US
      * p-phone             — a country code on a phone row is never a country
      * p-bare              — no contact details at all
    """
    _seed_person_with_names("p-tw-home", [("en", "Tw Home", True)],
                            contact_details=[("address_home", "TW")])
    _seed_person_with_names("p-tw-work", [("en", "Tw Work", True)],
                            contact_details=[("address_work", "TW")])
    _seed_person_with_names("p-jp-home", [("en", "Jp Home", True)],
                            contact_details=[("address_home", "JP")])
    _seed_person_with_names("p-mixed", [("en", "Mixed", True)],
                            contact_details=[("address_work", "JP"), ("address_home", "TW")])
    _seed_person_with_names("p-nullhome", [("en", "Null Home", True)],
                            contact_details=[("address_home", None), ("address_work", "US")])
    _seed_person_with_names("p-phone", [("en", "Phone", True)],
                            contact_details=[("phone_work", "FR")])
    _seed_person_with_names("p-bare", [("en", "Bare", True)])


def test_person_facets_counts_match_country_query(client_with_test_db):
    """Every facet count == its ?country= row count == its /count?country=. The guarantee."""
    _seed_country_mix()

    facets = client_with_test_db.get("/api/v2/persons/facets").json()

    # Absolute expectation first, for the same reason as the card test: the loop below
    # only proves the three endpoints agree with EACH OTHER. All three are built on
    # _person_country, so breaking that helper moves them together and the loop still
    # passes. This pins the buckets themselves.
    assert facets == [
        {"country_code": "JP", "count": 1},
        {"country_code": "TW", "count": 3},
        {"country_code": "US", "count": 1},
        {"country_code": None, "count": 2},
    ]

    # Every facet, not a sample — the null bucket takes a different SQL branch
    # (IS NULL rather than =) and is exactly where a disagreement would hide.
    for f in facets:
        country = _country_param(f["country_code"])
        rows = client_with_test_db.get(
            "/api/v2/persons", params={"country": country, "limit": 500}
        ).json()
        total = client_with_test_db.get(
            "/api/v2/persons/count", params={"country": country}
        ).json()["total"]
        assert f["count"] == len(rows) == total, (
            f"facet {country} says {f['count']}, list returned {len(rows)}, count says {total}"
        )


def test_person_facets_sum_to_total(client_with_test_db):
    """The groups partition the collection: no person is in two buckets or in none."""
    _seed_country_mix()

    facets = client_with_test_db.get("/api/v2/persons/facets").json()
    total = client_with_test_db.get("/api/v2/persons/count").json()["total"]
    assert total == 7
    assert sum(f["count"] for f in facets) == total


def test_person_country_filter_agrees_with_row_country(client_with_test_db):
    """Rows from ?country=XX all report country_code XX; rows from none report null.

    This is what pins the SQL helper (_person_country, used by the filter) against the
    Python fold in list_persons (which fills each row's country_code). They are two
    encodings of one rule; if they drift, a person shows a TW flag inside the JP group.
    p-mixed and p-nullhome are the persons on which a drifted helper would disagree.
    """
    _seed_country_mix()

    facets = client_with_test_db.get("/api/v2/persons/facets").json()
    seen = 0
    for f in facets:
        rows = client_with_test_db.get(
            "/api/v2/persons",
            params={"country": _country_param(f["country_code"]), "limit": 500},
        ).json()
        assert rows, f"facet {f} returned no rows — the agreement check would be vacuous"
        for r in rows:
            assert r["country_code"] == f["country_code"], (
                f"{r['external_id']} is in group {f['country_code']} "
                f"but its row says {r['country_code']}"
            )
        seen += len(rows)
    # Guard the guard: every seeded person was actually checked.
    assert seen == 7


def test_person_facets_prefer_home_over_work(client_with_test_db):
    """Work JP plus home TW counts under TW, and only ?country=TW returns the person.

    The work address is inserted first, so it holds the lower ContactDetail.id: only the
    detail_type ordering (home before work) can make this pass.
    """
    _seed_person_with_names(
        "p-mixed",
        names=[("en", "Home And Work", True)],
        contact_details=[("address_work", "JP"), ("address_home", "TW")],
    )

    facets = client_with_test_db.get("/api/v2/persons/facets").json()
    assert facets == [{"country_code": "TW", "count": 1}]

    tw = client_with_test_db.get("/api/v2/persons", params={"country": "TW"}).json()
    jp = client_with_test_db.get("/api/v2/persons", params={"country": "JP"}).json()
    assert [r["external_id"] for r in tw] == ["p-mixed"]
    assert jp == []


def test_person_facets_ignore_non_address_and_null_codes(client_with_test_db):
    """A phone-only country is no country; a NULL home code falls back to work."""
    _seed_person_with_names(
        "p-phone", names=[("en", "Phone Only", True)],
        contact_details=[("phone_work", "FR")],
    )
    _seed_person_with_names(
        "p-nullhome", names=[("en", "Null Home", True)],
        contact_details=[("address_home", None), ("address_work", "US")],
    )

    facets = client_with_test_db.get("/api/v2/persons/facets").json()
    assert facets == [
        {"country_code": "US", "count": 1},
        {"country_code": None, "count": 1},
    ]

    none_rows = client_with_test_db.get("/api/v2/persons", params={"country": "none"}).json()
    assert [r["external_id"] for r in none_rows] == ["p-phone"]
    # The phone's code must not be reachable as a group either.
    assert client_with_test_db.get("/api/v2/persons", params={"country": "FR"}).json() == []


def test_person_facets_respect_q(client_with_test_db):
    """?q= narrows the buckets, through both halves of _person_ids_matching.

    The organisation match has no contact details, so it lands in the null bucket; the
    name match lives in JP; the bystander in TW proves the narrowing discriminates.
    """
    _seed_person_with_names(
        "p-name-match", names=[("en", "Taro Matsumoto", True)],
        contact_details=[("address_home", "JP")],
    )
    _seed_person_with_names(
        "p-bystander", names=[("en", "Bob Jones", True)],
        contact_details=[("address_home", "TW")],
    )
    _seed_person_at_org("p-org-match", person_name="Alice Smith", org_name="Rotary Club of Taipei")

    unfiltered = client_with_test_db.get("/api/v2/persons/facets").json()
    assert unfiltered == [
        {"country_code": "JP", "count": 1},
        {"country_code": "TW", "count": 1},
        {"country_code": None, "count": 1},
    ]

    by_name = client_with_test_db.get("/api/v2/persons/facets", params={"q": "Matsumoto"}).json()
    assert by_name == [{"country_code": "JP", "count": 1}]

    by_org = client_with_test_db.get("/api/v2/persons/facets", params={"q": "Rotary"}).json()
    assert by_org == [{"country_code": None, "count": 1}]

    # q and country combine on the list and on count exactly as on the facet.
    rows = client_with_test_db.get(
        "/api/v2/persons", params={"q": "Rotary", "country": "none", "limit": 500}
    ).json()
    total = client_with_test_db.get(
        "/api/v2/persons/count", params={"q": "Rotary", "country": "none"}
    ).json()["total"]
    assert [r["external_id"] for r in rows] == ["p-org-match"]
    assert total == 1


def test_person_facets_order(client_with_test_db):
    """Codes ascending, null last — the frontend's current group order.

    Seeded out of order so insertion order cannot produce the expected sequence. The
    null bucket is seeded first: SQLite sorts NULL FIRST under a plain ASC, so a
    missing null-last rule puts it at the head.
    """
    _seed_person_with_names("p-none", [("en", "None", True)])
    _seed_person_with_names("p-us", [("en", "Us", True)], contact_details=[("address_home", "US")])
    _seed_person_with_names("p-jp", [("en", "Jp", True)], contact_details=[("address_home", "JP")])
    _seed_person_with_names("p-tw", [("en", "Tw", True)], contact_details=[("address_home", "TW")])

    facets = client_with_test_db.get("/api/v2/persons/facets").json()
    assert [f["country_code"] for f in facets] == ["JP", "TW", "US", None]


def test_person_group_order_is_by_sort_name(client_with_test_db):
    """Within a group: lower(family_name, else full_name), then id.

    Each seeded person pins one part of the rule:
      * "adams" before "Baker" — case-insensitive; a binary compare puts "B" (66)
        before "a" (97)
      * p-carter has no family name and sorts on full name "carter Xavier"; without
        the fallback its NULL key would sort first
      * p-doe-1 and p-doe-2 tie on "doe" (cased differently) and come back in id order
      * p-baker's lowest-id name is NOT current and would sort first ("aardvark") if
        the rule forgot is_current
    Insertion order is scrambled so neither id order nor newest-first matches.
    """
    tw = [("address_home", "TW")]
    _seed_person_with_names(
        "p-baker",
        [("en", "Aardvark Old", False, "Aardvark"), ("en", "Zed Baker", True, "Baker")],
        contact_details=tw,
    )
    _seed_person_with_names("p-carter", [("en", "carter Xavier", True, None)], contact_details=tw)
    _seed_person_with_names("p-doe-1", [("en", "John Doe", True, "Doe")], contact_details=tw)
    _seed_person_with_names("p-adams", [("en", "Yuri adams", True, "adams")], contact_details=tw)
    _seed_person_with_names("p-doe-2", [("en", "Jane DOE", True, "DOE")], contact_details=tw)

    rows = client_with_test_db.get("/api/v2/persons", params={"country": "TW", "limit": 500}).json()
    assert [r["external_id"] for r in rows] == [
        "p-adams", "p-baker", "p-carter", "p-doe-1", "p-doe-2",
    ]


def test_person_group_ordering_is_total(client_with_test_db, captured_statements):
    """The ?country= ORDER BY must contain persons.id as its own sort key.

    Structural for the same reason as test_persons_ordering_is_total: SQLite returns
    tied sort names in rowid order, so paging cannot observe a missing tiebreaker.

    The match is on the whole compiled clause, not a substring. The sort-name key is a
    correlated subquery whose SQL contains `person_names.person_id = persons.id`, so a
    substring test for "persons.id" would pass with the tiebreaker deleted.
    """
    _seed_person_with_names("p-a", [("en", "Same", True)], contact_details=[("address_home", "TW")])
    _seed_person_with_names("p-b", [("en", "Same", True)], contact_details=[("address_home", "TW")])

    captured = captured_statements()

    assert client_with_test_db.get(
        "/api/v2/persons", params={"country": "TW", "limit": 1}
    ).status_code == 200

    person_selects = [
        s for s in captured
        if getattr(s, "column_descriptions", None)
        and s.column_descriptions[0].get("entity") is Person
        and s._order_by_clauses
    ]
    assert person_selects, "no ordered Person select was executed by GET /api/v2/persons?country="

    order_by_sql = [
        str(clause.compile(dialect=sqlite_dialect())).strip()
        for clause in person_selects[-1]._order_by_clauses
    ]
    assert any(clause in ("persons.id", "persons.id ASC", "persons.id DESC") for clause in order_by_sql), (
        f"ORDER BY {order_by_sql} has no unique column — LIMIT/OFFSET paging is unsound"
    )


def test_person_group_pagination_is_stable(client_with_test_db):
    """30 people with one identical sort name page through with no overlap or gap.

    Like test_list_pagination_is_stable, an end-to-end smoke test: SQLite's rowid order
    for ties means it cannot by itself guard the tiebreaker — the structural test above
    does that.
    """
    for i in range(30):
        _seed_person_with_names(
            f"p-same-{i}", [("en", "Same Name", True, "Same")],
            contact_details=[("address_home", "TW")],
        )

    pages = [
        client_with_test_db.get(
            "/api/v2/persons", params={"country": "TW", "limit": 10, "offset": offset}
        ).json()
        for offset in (0, 10, 20)
    ]
    assert [len(p) for p in pages] == [10, 10, 10]
    ids = [r["id"] for page in pages for r in page]
    assert len(set(ids)) == 30


@pytest.mark.parametrize("endpoint", ["/api/v2/persons", "/api/v2/persons/count"])
@pytest.mark.parametrize("country", [
    "tw",    # lowercase — codes are stored uppercase, so this would silently match nothing
    "TWN",   # alpha-3
    "",      # empty — must not be read as "no filter"
])
def test_person_country_param_rejects_malformed(client_with_test_db, endpoint, country):
    assert client_with_test_db.get(endpoint, params={"country": country}).status_code == 422


@pytest.mark.parametrize("endpoint", ["/api/v2/persons", "/api/v2/persons/count"])
def test_person_country_param_validated(client_with_test_db, endpoint):
    """The pattern accepts what the endpoints take: an alpha-2 code and the none sentinel."""
    assert client_with_test_db.get(endpoint, params={"country": "none"}).status_code == 200
    assert client_with_test_db.get(endpoint, params={"country": "TW"}).status_code == 200
