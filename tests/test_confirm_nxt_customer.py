"""The scan review can mark an organization as an NXT customer; saving the card
turns the organization's switch on (and never turns it off)."""
from sqlalchemy import select

from app.db.models import Organization, OrganizationName
from app.services import claude_parser
from tests.test_confirm_work_contacts import _confirm, _db, _org, staged  # noqa: F401


def _org_flag(client, name):
    async def q(db):
        org_id = await db.scalar(select(OrganizationName.org_id).where(OrganizationName.name == name))
        return (await db.get(Organization, org_id)).is_nxt_customer
    return _db(client, q)


def test_flagged_organization_is_saved_as_nxt_customer(staged):
    _confirm(staged, [dict(_org("誠實化工機械有限公司"), is_nxt_customer=True), _org("Other Co")], [])
    assert _org_flag(staged, "誠實化工機械有限公司") is True
    assert _org_flag(staged, "Other Co") is False


def test_unflagged_save_does_not_turn_an_existing_customer_off(staged):
    async def seed(db):
        org = Organization(is_nxt_customer=True)
        db.add(org)
        await db.flush()
        db.add(OrganizationName(org_id=org.id, language="en", name="Old Customer", is_current=True))
    _db(staged, seed)
    _confirm(staged, [_org("Old Customer")], [])
    assert _org_flag(staged, "Old Customer") is True


def test_parser_asks_for_and_keeps_the_gui_number():
    assert "gui_number" in claude_parser._SCHEMA and "統一編號" in claude_parser._RULES
    card = claude_parser._build_parsed_card({"contact_details": [
        {"detail_type": "gui_number", "value": {"value": "90270905", "confidence": 1.0}}]})
    assert card.contact_details[0].detail_type == "gui_number"
