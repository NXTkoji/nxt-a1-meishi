import asyncio
from datetime import date

from app.db.session import get_db
from app.main import app


def test_build_legacy_card_carries_notes_date_and_my_company_labels(client_with_test_db):
    from app.db.models import Card, CardMyCompany, MyCompany, Person
    from app.services.legacy_card import build_legacy_card

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()

            nxt = MyCompany(name="NXT株式会社", google_label="NXT")
            unlabeled = MyCompany(name="正康有限公司")
            db.add_all([nxt, unlabeled])
            await db.flush()

            card = Card(
                external_id="c1",
                person_id=person.id,
                received_date=date(2026, 7, 18),
                notes="Met at trade show",
            )
            db.add(card)
            await db.flush()
            db.add(CardMyCompany(card_id=card.id, my_company_id=nxt.id))
            db.add(CardMyCompany(card_id=card.id, my_company_id=unlabeled.id))
            await db.flush()
            await db.refresh(card, attribute_names=["my_company_links", "occasion"])
            for link in card.my_company_links:
                await db.refresh(link, attribute_names=["my_company"])
            await db.refresh(person, attribute_names=["names", "relationships_from"])

            legacy = build_legacy_card(card, person, [], [])

            assert legacy.received_date == "2026-07-18"
            assert legacy.notes == "Met at trade show"
            assert sorted(legacy.my_company_labels) == ["NXT", "正康有限公司"]
            assert legacy.occasion_name == ""
            assert legacy.received_location == ""
            assert legacy.person.relations == []
            break

    asyncio.run(_run())


def test_pick_native_prefers_traditional_chinese_then_japanese_then_other():
    from app.services.legacy_card import pick_native

    assert pick_native([("en", "CEO"), ("ja", "社長"), ("zh-TW", "執行長")]) == "執行長"
    assert pick_native([("en", "CEO"), ("ja", "社長")]) == "社長"
    assert pick_native([("en", "CEO"), ("ko", "대표")]) == "대표"
    # English is never picked; empty values are skipped.
    assert pick_native([("en", "CEO")]) == ""
    assert pick_native([("zh-TW", ""), ("ja", None), ("ja", "社長")]) == "社長"


def test_build_legacy_card_uses_chinese_title_and_company(client_with_test_db):
    """Regression: only Japanese titles reached Google, so a Chinese-only or
    Chinese+English title (246 of 386 positions) was dropped."""
    from app.db.models import Card, Organization, OrganizationName, Person, Position, PositionDetail
    from app.services.legacy_card import build_legacy_card

    async def _run():
        async for db in app.dependency_overrides[get_db]():
            person = Person(external_id="p1")
            db.add(person)
            await db.flush()
            org = Organization()
            db.add(org)
            await db.flush()
            # English name first, so a "first current name" fallback would pick it.
            db.add_all([
                OrganizationName(org_id=org.id, language="en", name="College of Management, NTU", is_current=True),
                OrganizationName(org_id=org.id, language="zh-TW", name="國立臺灣大學 管理學院", is_current=True),
            ])
            pos = Position(person_id=person.id, org_id=org.id)
            db.add(pos)
            await db.flush()
            db.add_all([
                PositionDetail(position_id=pos.id, language="en", title="Professor Rank Specialist", department="Alumni Office"),
                PositionDetail(position_id=pos.id, language="zh-TW", title="執行長 CEO", department="校友聯絡室 AO"),
            ])
            card = Card(external_id="c1", person_id=person.id)
            db.add(card)
            await db.flush()
            await db.refresh(card, attribute_names=["my_company_links", "occasion"])
            await db.refresh(person, attribute_names=["names", "relationships_from"])
            await db.refresh(pos, attribute_names=["details", "organization"])
            await db.refresh(pos.organization, attribute_names=["names"])

            legacy = build_legacy_card(card, person, [], [pos])
            p = legacy.person.positions[0]
            assert p.company == "國立臺灣大學 管理學院"
            assert p.company_english == "College of Management, NTU"
            assert p.title == "執行長 CEO"
            assert p.title_english == "Professor Rank Specialist"
            assert p.department == "校友聯絡室 AO"
            break

    asyncio.run(_run())
