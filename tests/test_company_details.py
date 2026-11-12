"""Issuing fills the Odoo company with what the app knows about the organization:
other-language names, address, phone, fax and website from its work contacts."""
from sqlalchemy import select

from app.db.models import ContactDetail, Organization, OrganizationName, Person, PersonName, Position
from tests.test_issue_flow import TAX, _drive_root, env  # noqa: F401


async def _org_with_contacts(db):
    """誠實化工 with three names and one person whose work contacts belong to it."""
    org = Organization()
    db.add(org)
    await db.flush()
    for lang, name in [("zh-TW", "誠實化工機械有限公司"), ("en", "Cheng Shih Chemical Machinery"),
                       ("ja", "誠実化工機械")]:
        db.add(OrganizationName(org_id=org.id, language=lang, name=name, is_current=True))
    person = Person()
    db.add(person)
    await db.flush()
    db.add(PersonName(person_id=person.id, language="zh-TW", full_name="張煒南", name_type="primary"))
    # The person's first position is elsewhere, so 誠實化工 is index 1 ("_pos:1")
    other = Organization()
    db.add(other)
    await db.flush()
    db.add(OrganizationName(org_id=other.id, language="en", name="Elsewhere", is_current=True))
    db.add(Position(person_id=person.id, org_id=other.id))
    await db.flush()
    db.add(Position(person_id=person.id, org_id=org.id))
    for t, v, label, cc in [
        ("phone_work", "02-1111111", None, None),          # belongs to "Elsewhere"
        ("phone_work", "03-5551234", "_pos:1", None),
        ("phone_fax", "03-5352292", "_pos:1", None),
        ("address_work", "302新竹縣竹北市新溪街175巷36號", "_pos:1", "TW"),
        ("url_website", "https://example.tw", "_pos:1", None),
        ("phone_mobile", "0937-547848", None, None),       # personal, never on the company
    ]:
        db.add(ContactDetail(person_id=person.id, detail_type=t, value=v, label=label, country_code=cc))
    await db.flush()
    return org.external_id


def _company(env):
    return next(r for r in env.odoo.rows("res.partner") if r.get("company_registry") == "C0200")


def test_new_company_gets_names_address_phone_fax_and_website(env):
    env.odoo.add("res.country", name="Taiwan", code="TW")
    ext = env.run(_org_with_contacts)
    run = env.commit(org_ext_id=ext, customer_name="誠實化工機械有限公司")
    assert run.status == "done", run.error
    co = _company(env)
    assert co["name"] == "誠實化工機械有限公司"
    assert co["x_studio_name_2nd_language"] == "Cheng Shih Chemical Machinery / 誠実化工機械"
    assert co["street"] == "302新竹縣竹北市新溪街175巷36號" and co["country_id"] == 1
    assert co["phone"] == "03-5551234" and co["x_fax"] == "03-5352292"
    assert co["website"] == "https://example.tw"
    assert "mobile" not in co


def test_existing_company_keeps_what_odoo_already_has(env):
    existing = env.odoo.add("res.partner", name="誠實化工機械有限公司", is_company=True,
                            phone="03-9999999", vat=TAX)
    ext = env.run(_org_with_contacts)
    run = env.commit(org_ext_id=ext, customer_name="誠實化工機械有限公司")
    assert run.status == "done", run.error
    co = env.odoo.data["res.partner"][existing]
    assert co["company_registry"] == "C0200"
    assert co["phone"] == "03-9999999"                 # not overwritten
    assert co["x_fax"] == "03-5352292" and co["website"] == "https://example.tw"


def test_organization_without_contacts_still_issues(env):
    run = env.commit()
    assert run.status == "done", run.error
    co = _company(env)
    assert not co.get("phone") and not co.get("street")


def test_review_lists_the_company_details(env):
    ext = env.run(_org_with_contacts)
    out = env.propose(org_ext_id=ext, customer_name="誠實化工機械有限公司")
    assert "Add to the Odoo company where empty: other names, address, phone, fax, website" in out.actions
    assert not env.odoo.rows("res.partner")             # propose still creates nothing
