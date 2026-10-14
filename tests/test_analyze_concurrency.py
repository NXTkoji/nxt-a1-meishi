"""
POST /api/v2/sessions/{sid}/analyze must parse card groups concurrently.

Each card is one Claude Vision call of several seconds. Running them one after
another made a 10-card batch take 10× one call. These tests replace the Claude
call with a fake that sleeps, then check:

  * several cards are in flight at once, but never more than the cap
  * every card still gets exactly one result, and "done" is the last event
  * one card failing does not stop the others
"""
import asyncio
import json

import pytest

from app.config import settings
from app.db.models import ScanSession, ScanSessionImage
from app.routers.v2 import sessions as sessions_router
from app.schemas.parsed_card import ParsedCard

SID = "analyze-concurrency-test"
N_CARDS = 6
FAKE_CALL_SECONDS = 0.3


@pytest.fixture
def grouped_session(client_with_test_db, tmp_path, monkeypatch):
    """A session in 'grouping' state with N_CARDS single-sided card groups."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "api_key", "")

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            session = ScanSession(external_id=SID, status="grouping")
            db.add(session)
            await db.flush()
            for i in range(N_CARDS):
                db.add(ScanSessionImage(
                    session_id=session.id,
                    image_path=f"{SID}/card{i}.jpg",
                    image_filename=f"card{i}.jpg",
                    temp_card_id=f"card-{i}",
                    side_order=0,
                ))
            await db.commit()

    asyncio.run(_seed())
    return client_with_test_db


def _install_fake_parser(monkeypatch, fail_paths=()):
    """Replace the Claude call with a sleep; record peak concurrency."""
    state = {"in_flight": 0, "peak": 0}

    async def fake_stream(side_paths, few_shot_block=""):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            yield "Sending to Claude Vision…"
            await asyncio.sleep(FAKE_CALL_SECONDS)
            if side_paths[0] in fail_paths:
                raise RuntimeError("boom")
            card = ParsedCard.model_validate(
                {"names": [{"language": "en", "full_name": {"value": side_paths[0]}}]}
            )
            yield f"result:{card.model_dump_json()}"
        finally:
            state["in_flight"] -= 1

    monkeypatch.setattr(sessions_router, "stream_parse_card_sides", fake_stream)
    return state


def _events(res) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]


def test_cards_are_analyzed_concurrently_within_cap(grouped_session, monkeypatch):
    state = _install_fake_parser(monkeypatch)

    res = grouped_session.post(f"/api/v2/sessions/{SID}/analyze")
    assert res.status_code == 200, res.text
    events = _events(res)

    # Overlap actually happened, but the cap was respected.
    assert state["peak"] > 1
    assert state["peak"] <= sessions_router.ANALYZE_CONCURRENCY

    # Every card got exactly one result; "done" is last and appears once.
    results = [e["temp_card_id"] for e in events if e["type"] == "result"]
    assert sorted(results) == sorted(f"card-{i}" for i in range(N_CARDS))
    assert events[-1]["type"] == "done"
    assert sum(e["type"] == "done" for e in events) == 1


def test_one_failing_card_does_not_block_others(grouped_session, monkeypatch):
    _install_fake_parser(monkeypatch, fail_paths={f"{SID}/card2.jpg"})

    res = grouped_session.post(f"/api/v2/sessions/{SID}/analyze")
    events = _events(res)

    errors = [e["temp_card_id"] for e in events if e["type"] == "error"]
    results = [e["temp_card_id"] for e in events if e["type"] == "result"]
    assert errors == ["card-2"]
    assert len(results) == N_CARDS - 1
    assert events[-1]["type"] == "done"
