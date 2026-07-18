import asyncio
from datetime import date

from app.db.session import get_db
from app.main import app


def test_build_legacy_card_carries_notes_date_and_met_as(client_with_test_db):
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
            await db.refresh(card, attribute_names=["my_company_links"])
            for link in card.my_company_links:
                await db.refresh(link, attribute_names=["my_company"])
            await db.refresh(person, attribute_names=["names"])

            legacy = build_legacy_card(card, person, [], [])

            assert legacy.received_date == "2026-07-18"
            assert legacy.notes == "Met at trade show"
            assert sorted(legacy.my_company_labels) == ["NXT", "正康有限公司"]
            break

    asyncio.run(_run())
