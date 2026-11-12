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


# ── Title and address reach the Odoo export ──────────────────────────────────

def _plain_card(title_lang="zh-TW"):
    """Duck-typed card for build_legacy_card: one zh-TW position and a work address."""
    from types import SimpleNamespace as NS
    org = NS(names=[NS(name="誠實化工機械有限公司", language="zh-TW", is_current=True)])
    pos = NS(organization=org, details=[NS(language=title_lang, title="經理", department=None)])
    person = NS(names=[NS(full_name="張煒南", language="zh-TW", name_type="primary", is_current=True)],
                relationships_from=[], birthday=None, notes=None)
    contacts = [NS(detail_type="address_work", value="302新竹縣竹北市新溪街175巷36號",
                   label=None, country_code="TW")]
    card = NS(my_company_links=[], occasion=None, occasion_label=None,
              received_date=None, received_location=None, notes=None)
    return card, person, contacts, [pos]


def test_title_in_any_language_is_kept():
    from app.services.legacy_card import build_legacy_card
    legacy = build_legacy_card(*_plain_card("zh-TW"))
    assert legacy.person.positions[0].title == "經理"


def test_address_keeps_its_country():
    from app.services.legacy_card import build_legacy_card
    addr = build_legacy_card(*_plain_card()).person.addresses[0]
    assert addr.full.startswith("302新竹縣") and addr.country_code == "TW"


def test_odoo_export_sends_title_and_address(monkeypatch):
    from app.services import odoo_sync
    from app.services.legacy_card import build_legacy_card
    from tests.fake_odoo import FakeOdoo
    fake = FakeOdoo()
    fake.add("res.country", name="Taiwan", code="TW")
    company = fake.add("res.partner", name="誠實化工機械有限公司", is_company=True, ref="C0201")
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, fake, 1))
    legacy = build_legacy_card(*_plain_card())
    pid = asyncio.run(odoo_sync.sync_to_odoo(legacy, None, company))
    rec = fake.data["res.partner"][pid]
    assert rec["function"] == "經理"
    assert rec["street"] == "302新竹縣竹北市新溪街175巷36號"
    assert rec["parent_id"] == company
