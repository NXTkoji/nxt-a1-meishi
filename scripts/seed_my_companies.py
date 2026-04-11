"""
Seed script: populate my_companies and relationship_types.

Usage (from nxt-a1-meishi/ directory):
    python3 scripts/seed_my_companies.py

Safe to re-run — uses INSERT OR IGNORE semantics.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import AsyncSessionLocal
from app.db.models import MyCompany, RelationshipType


# ---------------------------------------------------------------------------
# My Companies
# ---------------------------------------------------------------------------

MY_COMPANIES = [
    {
        "name": "NXT株式会社",
        "google_label": "NXT",
        "notes": "Primary company — uses shared Odoo nxta.co account via top-level .env credentials.",
    },
    {
        "name": "個人 (Koji)",
        "google_label": "Personal",
        "notes": "Cards received in a personal capacity (not representing any company).",
    },
]


# ---------------------------------------------------------------------------
# Predefined relationship types
# ---------------------------------------------------------------------------

RELATIONSHIP_TYPES = [
    ("introduced_by",  "Introduced by",   True),
    ("colleague",      "Colleague",        True),
    ("reports_to",     "Reports to",       True),
    ("referred_by",    "Referred by",      True),
    ("friend",         "Friend",           True),
    ("family_member",  "Family member",    True),
    ("mentor",         "Mentor",           True),
    ("investor",       "Investor",         True),
    ("advisor",        "Advisor",          True),
    ("client",         "Client",           True),
    ("supplier",       "Supplier",         True),
    ("partner",        "Business partner", True),
]


async def seed(session: AsyncSession) -> None:
    # --- my_companies ---
    for data in MY_COMPANIES:
        exists = await session.scalar(
            select(MyCompany).where(MyCompany.name == data["name"])
        )
        if not exists:
            session.add(MyCompany(**data))
            print(f"  + MyCompany: {data['name']}")
        else:
            print(f"  = MyCompany already exists: {data['name']}")

    # --- relationship_types ---
    for key, label, is_predefined in RELATIONSHIP_TYPES:
        exists = await session.scalar(
            select(RelationshipType).where(RelationshipType.key == key)
        )
        if not exists:
            session.add(RelationshipType(key=key, label=label, is_predefined=is_predefined))
            print(f"  + RelationshipType: {key}")
        else:
            print(f"  = RelationshipType already exists: {key}")

    await session.commit()


async def main() -> None:
    print("Seeding database…")
    async with AsyncSessionLocal() as session:
        await seed(session)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
