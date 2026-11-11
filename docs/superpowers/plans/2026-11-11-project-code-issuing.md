# Project Code Issuing — Implementation Plan

Spec: `docs/superpowers/specs/2026-11-11-project-code-issuing-design.md`

Odoo prerequisites (sequence `nxt.customer`, custom fields on `res.partner` and `project.project`) are already created. Work happens in this repo only.

## Task 1 — Database migration
- Add Alembic revision: columns on `organizations` (`tax_id`, `customer_code`, `odoo_partner_id`, `drive_folder_id`, `drive_url`, `legacy_codes`), unique indexes on `tax_id` and `customer_code` (nullable).
- Add tables `projects` and `issue_runs` (run id, input JSON, step name, status, result JSON, error, timestamps).
- Update `app/db/models.py`. Test: migration up/down on a scratch SQLite file.

## Task 2 — Validation and matching
- `app/services/project_codes.py`: `validate_tax_id()` (8 digits plus checksum), `normalize_phone()` (reuse the matcher's normalizer).
- `match_organization()`: `tax_id` exact, then phone via `ContactDetail`, then fuzzy name via `OrganizationName`. Returns candidates with a reason and confidence.
- Tests: exact hit, phone hit, fuzzy candidate only, no match, invalid 統編.

## Task 3 — Odoo client additions
- In `odoo_sync.py` (or a new `odoo_codes.py`): `peek_next_customer_id()` (read `number_next_actual`, no consumption), `draw_customer_id()` (`ir.sequence.next_by_code('nxt.customer')`), `find_partner_by_ref_or_vat()`, `next_project_number(customer_ref, line)`, `create_project()`.
- Fix `_find_or_create_company` to look up `ref`, then `vat`, then name, and to set `ref` when it creates. Tests with a mocked XML-RPC proxy.

## Task 4 — Drive folder creation
- `app/services/drive_folders.py` using the existing Google OAuth refresh token: `ensure_folder(parent_id, name)` (create only if missing), `ensure_project_tree(...)` for the five subfolders.
- Add `google_drive_customers_folder_id` to `config.py` and `.env.example`. Tests with a mocked Drive client.

## Task 5 — Propose and commit
- `propose()` builds the review card (no side effects).
- `commit()` runs the steps in the spec, each guarded by an existence check and recorded in `issue_runs`; a failed step stops the run and can be retried from that step.
- Tests: full success, failure at the Drive step then retry (no duplicates), existing customer (project only), reseller `sold_via`.

## Task 6 — API routes
- `app/routers/v2/projects.py`: `POST /api/v2/customers/resolve`, `POST /api/v2/projects/propose`, `POST /api/v2/projects/commit`, `POST /api/v2/projects/{id}/retry`; register in `app/main.py`; schemas in `app/schemas/api.py`.

## Task 7 — UI
- Organization view: "Issue project code" button, form (統編, phone, product line, site name, sold via, role), review card showing match, suggested codes and what will be created, Approve button, final result with links and a Retry button on failure. Add Traditional Chinese strings in `i18n.ts`.

## Task 8 — Verification
- Run the full test suite. Dry-run against Odoo using a clearly named test customer, confirm the partner, project and folders, then delete the test records.

## Later phases (not in this plan)
- Backfill and dedupe of existing customers (separate spec).
- Scheduled Drive-to-OneDrive mirror.
