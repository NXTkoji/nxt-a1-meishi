"""
Work contacts point at their organization by list index in the label
("_pos:N"; no label or any other label means index 0), mirroring
getPosIdx/posLabel in the frontend. The list is the person's positions ordered
by Position.id — the order _load_person_out returns them in.

The same convention applies to a ParsedCard draft, where the index is into
ParsedCard.positions. Anything that moves positions between lists (merging
persons, saving a draft onto a person) must recompute these indexes.
"""
from __future__ import annotations

WORK_CONTACT_TYPES = {"phone_work", "phone_fax", "email_work", "address_work", "url_website", "gui_number"}


def pos_index(label: str | None) -> int:
    """List index a label points at; 0 for no label or a printed label."""
    if label and label.startswith("_pos:") and label[5:].isdigit():
        return int(label[5:])
    return 0


def pos_label(index: int) -> str | None:
    """Label for a list index; index 0 is stored as no label."""
    return None if index == 0 else f"_pos:{index}"


def relabel(label: str | None, new_index: int) -> str | None:
    """Label pointing at new_index. Kept as is when the index is unchanged, so a
    printed label (e.g. "電話") at index 0 survives."""
    return label if new_index == pos_index(label) else pos_label(new_index)
