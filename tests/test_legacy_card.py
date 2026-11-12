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
    pos = NS(id=1, organization=org, details=[NS(language=title_lang, title="經理", department=None)])
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
    company = fake.add("res.partner", name="誠實化工機械有限公司", is_company=True, company_registry="C0201")
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, fake, 1))
    legacy = build_legacy_card(*_plain_card())
    pid = asyncio.run(odoo_sync.sync_to_odoo(legacy, None, company))
    rec = fake.data["res.partner"][pid]
    assert rec["function"] == "經理"
    assert rec["street"] == "302新竹縣竹北市新溪街175巷36號"
    assert rec["parent_id"] == company


# ── The company gets the card's work address and phone ──────────────────────

def _two_org_card():
    """A person at two organizations; each work contact names its own by "_pos:N"."""
    from types import SimpleNamespace as NS
    a = NS(names=[NS(name="Elsewhere Ltd", language="en", is_current=True)])
    b = NS(names=[NS(name="誠實化工機械有限公司", language="zh-TW", is_current=True)])
    # Passed in id order 7, 3: the labels count positions by id, so 3 is index 0
    positions = [NS(id=7, organization=b, details=[]), NS(id=3, organization=a, details=[])]
    person = NS(names=[NS(full_name="張煒南", language="zh-TW", name_type="primary", is_current=True)],
                relationships_from=[], birthday=None, notes=None)

    def cd(t, v, label=None, cc=None):
        return NS(detail_type=t, value=v, label=label, country_code=cc)

    contacts = [
        cd("phone_work", "02-1111111"),                                   # index 0: Elsewhere
        cd("phone_work", "03-5551234", "_pos:1"),                          # index 1: 誠實化工
        cd("address_work", "302新竹縣竹北市新溪街175巷36號", "_pos:1", "TW"),
        cd("phone_mobile", "0937-547848"),                                 # personal
    ]
    card = NS(my_company_links=[], occasion=None, occasion_label=None,
              received_date=None, received_location=None, notes=None)
    return card, person, contacts, positions


def test_each_position_carries_its_own_work_address_and_phone():
    from app.services.legacy_card import build_legacy_card
    first, second = build_legacy_card(*_two_org_card()).person.positions
    assert first.company == "誠實化工機械有限公司"
    assert (first.phone, first.address, first.address_country_code) == (
        "03-5551234", "302新竹縣竹北市新溪街175巷36號", "TW")
    assert (second.company, second.phone, second.address) == ("Elsewhere Ltd", "02-1111111", "")


def _export(monkeypatch, fake, company_partner_id=None):
    from app.services import odoo_sync
    from app.services.legacy_card import build_legacy_card
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, fake, 1))
    card, person, contacts, positions = _two_org_card()
    # As in the app: a company given by id is the one linked to the card's organization
    positions[0].organization.odoo_partner_id = company_partner_id
    legacy = build_legacy_card(card, person, contacts, positions)
    return asyncio.run(odoo_sync.sync_to_odoo(legacy, None, company_partner_id))


def test_new_company_from_an_export_gets_address_and_phone(monkeypatch):
    from tests.fake_odoo import FakeOdoo
    fake = FakeOdoo()
    tw = fake.add("res.country", name="Taiwan", code="TW")
    _export(monkeypatch, fake)
    co = next(r for r in fake.rows("res.partner") if r.get("is_company"))
    assert co["name"] == "誠實化工機械有限公司"
    assert co["street"] == "302新竹縣竹北市新溪街175巷36號" and co["country_id"] == tw
    assert co["phone"] == "03-5551234"
    assert not co.get("mobile")


def test_export_only_fills_what_the_company_lacks(monkeypatch):
    from tests.fake_odoo import FakeOdoo
    fake = FakeOdoo()
    fake.add("res.country", name="Taiwan", code="TW")
    company = fake.add("res.partner", name="誠實化工機械有限公司", is_company=True, company_registry="C0201",
                       phone="03-9999999", street="", country_id=False)
    _export(monkeypatch, fake, company)
    co = fake.data["res.partner"][company]
    assert co["phone"] == "03-9999999"                  # edited in Odoo: kept
    assert co["street"] == "302新竹縣竹北市新溪街175巷36號"


def test_company_given_by_id_is_filled_only_from_its_own_position(monkeypatch):
    """Issuing passes the issued company's id; the person's first position may be
    another organization, whose address and phone must not land on the customer."""
    from types import SimpleNamespace as NS
    from app.services import odoo_sync
    from app.services.legacy_card import build_legacy_card
    from tests.fake_odoo import FakeOdoo
    fake = FakeOdoo()
    fake.add("res.country", name="Taiwan", code="TW")
    other = fake.add("res.partner", name="Elsewhere Ltd", is_company=True)
    customer = fake.add("res.partner", name="誠實化工機械有限公司", is_company=True, company_registry="C0201")
    card, person, contacts, positions = _two_org_card()
    # First position (id 3) is Elsewhere at index 0; the customer (id 7) is index 1
    positions = [positions[1], positions[0]]
    positions[0].organization.odoo_partner_id = other
    positions[1].organization.odoo_partner_id = customer
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, fake, 1))
    legacy = build_legacy_card(card, person, contacts, positions)
    asyncio.run(odoo_sync.sync_to_odoo(legacy, None, customer))
    co = fake.data["res.partner"][customer]
    assert co.get("phone") == "03-5551234"              # its own office phone, not 02-1111111
    assert co.get("street") == "302新竹縣竹北市新溪街175巷36號"
    assert not fake.data["res.partner"][other].get("phone")


def test_unknown_company_id_is_not_filled(monkeypatch):
    from app.services import odoo_sync
    from app.services.legacy_card import build_legacy_card
    from tests.fake_odoo import FakeOdoo
    fake = FakeOdoo()
    stranger = fake.add("res.partner", name="Unrelated Co", is_company=True)
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, fake, 1))
    legacy = build_legacy_card(*_two_org_card())        # no position links to `stranger`
    asyncio.run(odoo_sync.sync_to_odoo(legacy, None, stranger))
    assert not fake.data["res.partner"][stranger].get("phone")
    assert not fake.data["res.partner"][stranger].get("street")
