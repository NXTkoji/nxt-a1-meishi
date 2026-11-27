# Party Codes (Suppliers, Potential Parties) — Implementation Plan

Spec: `docs/superpowers/specs/2026-11-24-party-codes-design.md`

Tests never touch the real DB, Odoo or Drive (see `tests/conftest.py`, fakes in `tests/fake_odoo.py` and the FakeDrive in `tests/test_drive_folders.py`). User-facing text must not say "NXT" (spec decision 4).

## Task 1 — Odoo setup (production, by API, once)
Do this only after the spec PR is merged. Create, checking each exists first:
- `ir.sequence` code `nxt.supplier`, prefix `S`, padding 3, next number 100, `company_id` = NXT (id 1), like `nxt.customer`.
- `res.partner` fields `x_supplier_code` (char, indexed), `x_supplier_drive_url` (char), `x_customer_status` and `x_supplier_status` (selection: potential / active / inactive).
- Tags `Supplier: Parts`, `Supplier: Administration`, `Supplier: Construction`, `Supplier: Forwarding`, `Supplier: Inspection`, `Supplier: Marketing`, `Supplier: Packaging`, `Supplier: Other`.
- Check the Odoo API user can `next_by_code('nxt.supplier')` with context `allowed_company_ids=[1]`, read `number_next_actual`, and write the new fields (same checks as the customer live verification).

## Task 2 — Database migration and models
- Alembic revision (head = the revision no other file lists as `down_revision`): `organizations` gets `supplier_code` (unique, nullable), `supplier_category`, `customer_status`, `supplier_status`, `supplier_drive_folder_id`, `supplier_drive_url`; `tax_id` widened to 32; `issue_runs.kind` (default `project`).
- Data migration: `customer_status` = `active` where `customer_code` is set, `potential` where `is_nxt_customer` and no code. Reversible.
- Update `app/db/models.py`. Test: up/down on a scratch SQLite file (pattern of `tests/test_short_code_migration.py`).

## Task 3 — Supplier validation and matching
- `app/services/supplier_codes.py`: `SUPPLIER_CODE_RE = ^S[ACFIMP]?\d{3,}$`, `is_supplier_code()`, category enum and its map to Drive folder names and Odoo tags, `validate_tax_id_for_country()` (TW checksum, JP `T\d{13}`, others non-empty), `next_supplier_number` helper if needed.
- Reuse `match_organization` from `project_codes.py` (tax ID, phone, fuzzy name); extend it to the Odoo side (find by `x_supplier_code`, `vat`, name).
- Tests: code shapes (`S059`, `SA002`, `S100`, rejects `C0201`, `S12`), `S999` never issued, tax ID per country, matching.

## Task 4 — Odoo client additions
- In `app/services/odoo_codes.py`: `peek_next_supplier_code()`, `draw_supplier_code()`, `find_company(supplier_code=...)`, supplier fields in `COMPANY_FIELDS`, write of `x_supplier_*`, status fields and category tags.
- Guard (spec, "Changes to the customer flow"): `customer_id_of` / `old_code_of` ignore supplier-shaped values; the customer step moves a supplier-shaped old code to `x_supplier_code` instead of `x_legacy_codes` and never queues it as a folder to move.
- Company scoping (spec, "Odoo companies"): `ODOO_COMPANY_ID` setting (+ `.env.example`); every call passes `allowed_company_ids`; searches use `company_id in (<id>, False)`; created partners get `company_id`. Apply it to the existing customer lookups too.
- Tests with the fake Odoo, including the S059-becomes-customer case and a partner of another company that must never match (same name, `company_id = 2`).

## Task 5 — Drive supplier folders
- Config `google_drive_suppliers_folder_id` (+ `.env.example`).
- `DriveFolders`: `ensure_supplier_folder(category, code, name)` (create under the category folder, existence check first), `find_potential_folder(name)` (look in `Potential Suppliers` and each category's `Potential`), promotion move + prefix with the code.
- Tests with FakeDrive: new folder, promotion move with prefix, several matches (warning, nothing moved), existing coded folder untouched.

## Task 6 — Propose and commit for suppliers
- `app/services/supplier_flow.py` (or an extension of `issue_flow.py`) using the same step runner and `issue_runs` with `kind='supplier'`. Steps: `local_org`, `supplier_code`, `odoo_company`, `drive_supplier`, `person`. Reuse an existing Odoo `x_supplier_code`; never draw twice on retry.
- Tests: full success, failure at the Drive step then retry (no duplicate, no second draw), existing supplier (code reused), dual-role party (customer data untouched).

## Task 7 — API routes
- `POST /api/v2/suppliers/propose`, `POST /api/v2/suppliers/commit`; the run-retry route dispatches on `kind`; `PUT /api/v2/organizations/{org}/roles` (statuses, category; plain update, Odoo status if a company exists). Keep `PUT .../nxt-customer`. Schemas in `app/schemas/api.py`; register in `app/main.py`.
- `OrganizationOut` exposes `customer_status`, `supplier_status`, `supplier_code`, `supplier_category`, `supplier_drive_url`.
- Card export writes the status fields and category tag to the Odoo company (no code for `potential`).

## Task 8 — UI
- Replace the NXT customer switch with a Relationship panel (spec, UI): customer status, supplier status + category, "Activate supplier" with review card, Approve, Retry, folder link.
- Neutral strings in `frontend/src/i18n.ts` for ja, en, zh-TW (keep `nxt*` keys, drop "NXT" from visible text).
  - Already done on 2026-11-27 for the customer strings ("Add to Project Management"); the supplier strings are still to add.
- Module switch (spec decision 8): gate the whole Relationship panel on `project_management_enabled` (hook `useProjectManagementEnabled`), in both the scan review and the person page, and rename the Settings label (`pmModuleLabel`, `pmModuleHint`) to "Customers & suppliers" in all three languages. Keep the setting key. Check in the browser on the Mac: all three languages, narrow width.

## Task 9 — Backfill script
- `scripts/backfill_suppliers.py`: dry-run CSV report by default, `--apply` to write; rules and conflict handling as in the spec. Idempotent.
- Tests: dry-run report on fakes (code match, other-company partner skipped, shared partner listed not changed, minimal company creation, potential folders, `S058` conflict, 光殿貿易 name-only candidate), apply twice changes nothing.
- First real run: dry-run on the Mac, review the CSV with the user, then `--apply`.

## Task 10 — Live verification
Use a clearly named test supplier (`ZZ TEST SUPPLIER 請刪除`), then delete its Odoo record and Drive folder:
1. Potential → active: code drawn from `nxt.supplier` (S100 or the current next number), folder created under the right category folder.
2. A folder named like the party in `Potential Suppliers` is moved and prefixed.
3. A dual-role party (an existing customer) gets a supplier code without touching its Customer ID or customer folder.
4. A party with `S059`-style code in Company ID that becomes a customer keeps the code in `x_supplier_code`.
5. Folder names with 中文 and "&" are created as expected.

## Later phases (not in this plan)
- Configurable product lines and Odoo tag names for other companies.
- Moving folders when a party becomes inactive.
- Customer-side status UI polish; scheduled Drive-to-OneDrive mirror.

## Handoff
Update this table in each PR. Mark a task done only when its tests pass.

| Task | Status |
|---|---|
| 1 Odoo setup | not started (after spec PR merge) |
| 2 Migration and models | not started |
| 3 Validation and matching | not started |
| 4 Odoo client additions and customer-flow guard | not started |
| 5 Drive supplier folders | not started |
| 6 Propose and commit | not started |
| 7 API routes | not started |
| 8 UI | not started |
| 9 Backfill script | not started |
| 10 Live verification | not started |

Facts from the 2026-11-24 audit (for whoever builds the backfill):
- Odoo companies: 1 NXT, 2 智原フードサービス (Tomohara), 3 正康有限公司 (Zenkan), 9 福原康児. Only company 1 is in scope. The sequence `nxt.customer` (id 77, next 202 at the time) belongs to company 1.
- NXT records: 30 top-level companies carry a supplier code in Company ID (`S001`, `S010`, `S049`, `S059`, `S063`, `S067`, `S072`, `S073`, `SA002 SA006 SA025 SA028 SA034 SA035 SA038 SA039 SA042 SA043 SA044`, `SC029 SC060 SC061 SC070`, `SF003`, `SM013 SM023 SM024 SM025 SM026`, `SP045`). The short names (`VGJ`, `WHL`, `BLD`, `LGJ`, `NHC`, `JFI`, `OKK`, `TOM`, `KMR`, `ACT`, `LNV`, `MJP`, `JFE`, `MRI`, `WNL`) all belong to company 2 and are out of scope. Four partners had no company on 2026-11-24 (美羅池餐飲 `DS164 MLC`, 奇桔數位行銷 `SM025`, 香榭裡商號 `SA043`, 正康有限公司 `P076`); the owner assigned all four to company 1 the same day, and 美羅池餐飲's Company ID is now `DS164`. The shared-partner rule stays for any that appear later.
- Three NXT-side companies are customer and supplier at once: 光殿貿易 (Company ID `P030`; Drive `SM009`), 縉業金屬工業 (`DS117`), and 美羅池餐飲 (`DS164`).
- Drive "NXT Suppliers", top level only: Parts 20 coded (S049 to S075), Japan 9 coded (S001 to S058) plus 7 uncoded, Administration 23 (SA001 to SA046), Construction 9 coded plus regional folders (台中, 台北新北, 台南, 嘉義, 高雄), Forwarding 4, Inspection 3, Marketing 17, Package 4. That is 89 coded folders before looking inside regional, "Potential", "not in use" and "inactive parts suppliers" folders, so the real total is higher. Odoo has `SC029`, `SC060`, `SC061`, which are not at the top level of the Construction folder (probably in the regional folders).
- `S058` is used twice (上億國際 Aqua Aircon in Parts, エスポ化学 in Japan). `S999 Others` is a folder, not a supplier.
- `SA034` is on both 交通部 and its branch 公路總局 in Odoo: not a conflict (branch under parent).
