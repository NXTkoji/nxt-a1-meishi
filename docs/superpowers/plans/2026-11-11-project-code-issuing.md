# Project Code Issuing — Implementation Plan

Spec: `docs/superpowers/specs/2026-11-11-project-code-issuing-design.md`

Odoo prerequisites (sequence `nxt.customer`, custom fields on `res.partner` and `project.project`) are already created. Work happens in this repo only.

## Task 1 — Database migration
- Add Alembic revision: columns on `organizations` (`is_nxt_customer` bool default false, `tax_id`, `customer_code`, `odoo_partner_id`, `drive_folder_id`, `drive_url`, `legacy_codes`), unique indexes on `tax_id` and `customer_code` (nullable).
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
- `app/routers/v2/projects.py`: `POST /api/v2/customers/resolve`, `POST /api/v2/projects/propose`, `POST /api/v2/projects/commit`, `POST /api/v2/projects/runs/{run_id}/retry`, `GET /api/v2/projects`, `PUT /api/v2/organizations/{org}/nxt-customer`; register in `app/main.py`; schemas in `app/schemas/api.py`.

## Task 6b — Old customers (done)
- `IssueRequest.legacy_codes`; `project_codes.parse_legacy_codes/merge_legacy_codes`; `DriveFolders.find_folders_by_code/peek_legacy_folder/move_legacy_folder`; issue step `legacy` after `drive_customer`; propose previews the move (its router takes Drive lazily through `get_drive_opener`, only when `legacy_codes` is sent).

## Task 7 — UI
- "NXT customer" switch in the card editor and organization view (default off). Everything below is hidden unless it is on; with it off the existing card flow is unchanged. Toggling it calls `PUT /api/v2/organizations/{org}/nxt-customer`. The switch is local only; the Odoo tag `NXT Customer` is added when a Customer ID is issued. Once issued, the switch cannot be turned off (the API returns 409).
- On approval, after the company step, create or update the card's person as a child of the company using the existing `sync_to_odoo`; the review card has a checkbox to skip this.
- Organization view: "Issue project code" button, form (統編, phone, product line, site name, sold via, role, optional "old codes" such as DS176), review card showing match, suggested codes and what will be created, Approve button, final result with links and a Retry button on failure. Add Traditional Chinese strings in `i18n.ts`.

## Task 8 — Verification
- Run the full test suite. Dry-run against Odoo using a clearly named test customer, confirm the partner, project and folders, then delete the test records.

## Later phases (not in this plan)
- Backfill and dedupe of existing customers (separate spec).
- Scheduled Drive-to-OneDrive mirror.

## Odoo state (already created on 2026-11-11, do not recreate)
- `ir.sequence` id 77: code `nxt.customer`, prefix `C`, padding 4, next number 200. Draw with `ir.sequence.next_by_code('nxt.customer')`; peek with `number_next_actual` (read only).
- `res.partner` fields: `x_legacy_codes` (char), `x_drive_url` (char). Native fields used: `ref` (Customer ID), `vat` (統編), `category_id`, `parent_id`.
- `project.project` fields: `x_project_code` (char), `x_product_line` (selection `DS`/`DJ`), `x_sold_via` (many2one res.partner), `x_drive_url` (char).
- `res.partner.category` tags: `NXT Customer` (id 9), `NXT Reseller` (id 10), `NXT End User` (id 11). Look tags up by name, not id.
- Legacy codes (DS###, P###, P###S##, DJ###) are NOT in Odoo yet; the backfill is a later phase.

## Handoff
Status is updated by each PR. Mark a task done only when its tests pass.

| Task | Status |
|---|---|
| 1 Migration | done (mocked tests pass) |
| 2 Validation and matching | done (mocked tests pass) |
| 3 Odoo client additions | done (mocked tests pass) |
| 4 Drive folders | done (mocked tests pass) |
| 5 Propose and commit | done (mocked tests pass) |
| 6 API routes | done (mocked tests pass) |
| 6b Old customers (legacy codes, move old folder) | done (mocked tests pass) |
| 7 UI | not started |
| 8 Live verification | not started |

Split: tasks 1-6 are backend with mocked Odoo and Drive (no credentials needed). Tasks 7-8 need a local machine: the UI needs a browser check, and live verification needs the real `.env`.

Live verification needs these `.env` values: `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (must include Drive scope; mint with `scripts/get_drive_refresh_token.py`), and the new `GOOGLE_DRIVE_CUSTOMERS_FOLDER_ID` (the folder id of NXT Customers on the NXT Google Drive).
Use a clearly named test customer, for example `ZZ TEST 請刪除`, and delete the Odoo records and Drive folders afterwards.

## What the backend does (tasks 1-6, for whoever builds the UI)
- `POST /api/v2/projects/propose` takes an `IssueRequest` (see `app/schemas/api.py`) and returns a `ProposalOut`: `customer_mode` (`existing`/`new`), `customer_code` or a tentative `suggested_customer_code`, `project_code`, `candidates` and `needs_choice` (fuzzy name matches; the user must pick one or send `create_new: true`), `actions` (what approval will do) and `warnings`. It creates nothing and consumes no sequence number.
- `POST /api/v2/projects/commit` takes the same body (plus `chosen_org_ext_id` / `create_new` from the user's choice). It returns an `IssueRunOut`. On a partial failure `status` is `failed` with `failed_step` and `error`; the UI shows a Retry button that calls `POST /api/v2/projects/runs/{run_id}/retry`.
- Validation problems (bad 統編 checksum, unknown product line, 統編 owned by another customer, reseller not yet issued) are HTTP 422 with a readable `detail`.
- Odoo or Drive not configured: 503. Odoo data problem (duplicate Customer ID): 409.
- `IssueRequest.legacy_codes` (optional, e.g. `DS176, P065`): approval moves each old Drive folder into the customer folder (name kept) and records the codes; `ProposalOut.warnings` says when no old folder was found in the NXT Google Drive. Show these in the review card.
- Plain card exports now put the person under the organization's Odoo company when it has one (`organizations.odoo_partner_id`), and the name lookup ignores legal forms and prefers a company with a Customer ID.
- Run tests with `.venv/bin/python -m pytest tests -q`. New test files: `test_project_codes.py`, `test_odoo_codes.py`, `test_drive_folders.py`, `test_issue_flow.py`; fakes in `tests/fake_odoo.py`.

## Live verification checklist (task 8; the fakes cannot prove these)
1. The Odoo API user can call `ir.sequence.next_by_code('nxt.customer')` and read `number_next_actual` (permissions differ from the admin account used to create the sequence).
2. The API user can read and write the new `x_*` fields on `res.partner` and `project.project`, and create `res.partner.category` tags (they already exist: look up by name).
3. `project.project` create works with only `name`, `partner_id` and the x_ fields (some Odoo setups require more, such as a company or alias).
4. The Google refresh token has Drive scope and can create folders inside `GOOGLE_DRIVE_CUSTOMERS_FOLDER_ID`.
5. Drive folder names containing 中文 and "&" are created as expected.
6. A card export for a person at an issued customer lands under the company, and one for a non-customer behaves as before.
7. Old folder: create a folder named `DS999 ZZ old` in the NXT Customers tree, issue with `legacy_codes=DS999`, and confirm it moved into the customer folder with its name intact and `x_legacy_codes` set. Delete it afterwards too.
8. Delete the test customer's Odoo records and Drive folders afterwards.
