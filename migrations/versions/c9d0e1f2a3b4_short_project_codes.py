"""short_project_codes

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-18

Project codes drop the "D" and the hyphen: C0201-DS01 becomes C0201S01, and the
product line DS / DJ becomes S / J.
"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = re.compile(r"^(C\d{4,})-D([A-Z])(\d{2,})$")
_NEW = re.compile(r"^(C\d{4,})([A-Z])(\d{2,})$")


def _rewrite(rewrite, line_map) -> None:
    bind = op.get_bind()
    for row_id, code, line in bind.execute(sa.text("SELECT id, code, product_line FROM projects")).fetchall():
        new_code = rewrite(code)
        new_line = line_map.get(line, line)
        if new_code != code or new_line != line:
            bind.execute(sa.text("UPDATE projects SET code = :c, product_line = :l WHERE id = :i"),
                         {"c": new_code, "l": new_line, "i": row_id})


def upgrade() -> None:
    _rewrite(lambda c: _OLD.sub(r"\1\2\3", c), {"DS": "S", "DJ": "J"})


def downgrade() -> None:
    _rewrite(lambda c: _NEW.sub(r"\1-D\2\3", c), {"S": "DS", "J": "DJ"})
