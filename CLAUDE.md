# NXT 名刺整理器 (nxt-a1-meishi)

Business-card scanner. FastAPI + SQLAlchemy/Alembic on local SQLite (`~/.nxt-a1/meishi.db`), React/Vite frontend in `frontend/`. Cards are parsed with Claude Vision, then explicitly exported to Odoo, Google Contacts and OneDrive.

## Commands
- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/python -m pytest tests -q` (tests must never touch the real DB or network; see `tests/conftest.py`)
- Run: `uvicorn app.main:app --reload` (lifespan runs Alembic migrations against the DB in `.env`)
- Frontend: `cd frontend && npm install && npm run dev`

## Conventions
- New schema changes need an Alembic revision in `migrations/versions/`; head is the revision no other file lists as `down_revision`.
- Features are designed in `docs/superpowers/specs/` and planned in `docs/superpowers/plans/`. Read the matching spec before changing a feature.
- Odoo is the source of truth for customer data; the local SQLite is a cache. Odoo access is direct XML-RPC (`app/services/odoo_sync.py`).
- Odoo layout: company partner first, people as child contacts (`parent_id`).
- Never run anything that calls real Odoo or Google from tests. Mock the XML-RPC and Drive clients.
- UI strings are bilingual; add Traditional Chinese strings in `frontend/src/i18n.ts`.

## Active work
Project code issuing: `docs/superpowers/specs/2026-11-11-project-code-issuing-design.md` and its plan. Continue from the Handoff section at the end of the plan.
