# Party Codes: Suppliers, Potential Parties, and One Record per Organization

**Date:** 2026-11-24
**Status:** Design approved in conversation (decisions below), Odoo setup and implementation pending
**Builds on:** `2026-11-11-project-code-issuing-design.md` (customer ID, project codes, propose then approve)

## Problem

Customers and suppliers were split into two code schemes only because QuickBooks Online forced it (it has been replaced by Odoo). The split now causes trouble:

- A supplier sometimes buys from us (and a customer sometimes sells to us), so the same legal entity has two codes and two folders with no link between them. Example: 光殿貿易 is `P030` in NXT Customers and `SM009` in NXT Suppliers. 縉業金屬工業 and 美羅池餐飲 are already both customer and supplier in Odoo.
- Odoo's "Company ID" (`company_registry`) can hold only one code per party. Today it holds customer codes (`DS127`, `P030`, `C0201`) and supplier codes (`S059`, `SA002`, `SC070`) alike.
- Supplier codes carry a category prefix (S, SA, SC, SF, SI, SM, SP). Parts (S049 to S075) and Japan (S001 to S058) share the `S` namespace and collide at `S058` (上億國際 Aqua Aircon and エスポ化学). A supplier that changes category would carry a wrong prefix.
- There is no way to register a **potential** customer or supplier. Folders named "Potential" exist in Drive, but nothing in Odoo marks a party as potential; Odoo's `customer_rank` / `supplier_rank` are counters of posted documents and are 0 for both a prospect and an unclassified contact.
- The customer flow would misfile a supplier code: if a supplier with `S059` in Company ID becomes a customer, the issue step moves `S059` into `x_legacy_codes` (customer aliases) and overwrites Company ID.

## Decisions (agreed 2026-11-24)

1. **One record per organization**, whatever roles it has. Roles are independent, so a party can be an active supplier and a potential customer at the same time.
2. **Supplier code format:** a single sequence `S###`, starting at `S100` (above the highest legacy number, S075). The category is an Odoo tag and a Drive folder position, never part of the code.
3. **Potential parties get no code** until promoted to active. Promotion goes through the same propose-then-approve flow as customer codes.
4. **Neutral wording.** The app will be used by other companies later, so user-facing text (labels, review card, help) must not say "NXT". Internal names (`is_nxt_customer`, Odoo tag `NXT Customer`, sequence `nxt.customer`) stay as they are for now.
5. **Scope of the first build:** the supplier role and the status model for both roles in the app and Odoo, plus the backfill of existing suppliers. The customer issue flow is unchanged except for the two guards listed under "Changes to the customer flow".
6. **Existing unique supplier codes are kept as they are** (`S059`, `SA002`, `SC070`), so folder name and code stay equal and nothing printed changes. Only new suppliers draw from `S100`. (The alternative, re-issuing every old code as `S1xx`, was rejected.)
7. **Only NXT is in scope.** The Odoo database holds several companies (see "Odoo companies"); records of the other companies are never read, coded or changed by this work.
8. **The Settings module switch covers the whole Relationship panel** (decided 2026-11-27). The "Modules → Project Management" switch added on 2026-11-27 hides the per-organization panel. Once that panel becomes the Relationship panel (customer and supplier roles), the same switch hides all of it: off means no customer, supplier or code controls anywhere, so the app is a plain card manager. The switch is renamed to match ("Customers & suppliers" / 顧客・仕入先 / 客戶與供應商); the stored setting key `project_management_enabled` stays. (Rejected: hiding only project codes, or separate switches per role.)

## Odoo companies

The Odoo database is multi-company. Checked on 2026-11-24:

| id | Company | Scope |
|---|---|---|
| 1 | 紐克仕綠能科技股份有限公司 (NXT) | **in scope** |
| 2 | 智原フードサービス合同会社 (Tomohara) | out of scope. Its Japanese suppliers carry short names in Company ID (`VGJ`, `WHL`, `BLD`, `LGJ`, `NHC`, `JFI`, `OKK`, `TOM`, `KMR`, `ACT`, `LNV`, ...); these are not NXT codes and are never touched |
| 3 | 正康有限公司 (Zenkan) | out of scope |
| 9 | 福原康児 | out of scope |

Rules for every Odoo call this work makes:
- Searches use the domain `company_id in (1, False)` (NXT's own partners plus shared ones), and every call passes the context `allowed_company_ids = [1]`, so sequences and defaults resolve for NXT. The NXT company id is a setting (`ODOO_COMPANY_ID`), not a constant.
- Partners the app creates get `company_id = 1`. The new sequence is created for company 1, like `nxt.customer` (id 77).
- **Shared partners** (no company, for example 美羅池餐飲, 奇桔數位行銷 `SM025`, 香榭裡商號 `SA043`, 正康有限公司) are visible to every company, so a status or code written there is visible to all. The app and the backfill do not change them automatically: they are listed for confirmation.
- The existing customer lookups (`find_company`, `find_companies_by_name`) get the same company scoping, so a card export or issue run can never match another company's partner.

## Model

### Roles and status

Each organization has two independent role statuses:

| Role | Status values | Meaning |
|---|---|---|
| Customer | none, `potential`, `active`, `inactive` | `active` = has a Customer ID |
| Supplier | none, `potential`, `active`, `inactive` | `active` = has a Supplier code |

- `potential` has no code and no folder. It is a record (usually created from a scanned card) with a status.
- `active` has a code. Becoming active is a **promotion**: propose, review, approve.
- `inactive` keeps the code and folder; it only stops the party from appearing in pickers. Folders are not moved (manual for now).
- The existing `is_nxt_customer` switch becomes derived: true when the customer status is not none. It is kept as a column so nothing that reads it breaks.

### Codes

| Object | Format | Example |
|---|---|---|
| Customer ID | `C` + 4 digits (unchanged) | `C0201` |
| Supplier code (new) | `S` + 3 digits, from `S100`; four digits from `S1000` | `S100` |
| Supplier code (kept) | `S###`, or the old category forms `SA### SC### SF### SI### SM### SP###` | `S059`, `SA002`, `SC070` |
| Alias | free text in `x_legacy_codes` | `VGJ`, `DS164 MLC` |

Validation regex for a supplier code: `^S[ACFIMP]?\d{3,}$`. A code belongs to one party only (422 otherwise). `S999` is the name of the "Others" folder and is never issued.

### Odoo

Existing and unchanged: `company_registry` = Customer ID (only `C####` counts), `vat`, `x_legacy_codes`, `x_drive_url` (customer folder), tags `NXT Customer / Reseller / End User`.

To be created by API (production Odoo, done in plan task 1, not before):

| Model | Field / record | Type |
|---|---|---|
| `ir.sequence` | code `nxt.supplier`, prefix `S`, padding 3, next number 100 | sequence |
| `res.partner` | `x_supplier_code` | char (indexed) |
| `res.partner` | `x_supplier_drive_url` | char |
| `res.partner` | `x_customer_status` | selection potential / active / inactive |
| `res.partner` | `x_supplier_status` | selection potential / active / inactive |
| `res.partner.category` | tags `Supplier: Parts`, `Administration`, `Construction`, `Forwarding`, `Inspection`, `Marketing`, `Packaging`, `Other` | look up by name |

Rules:
- For NXT records, Company ID holds only Customer IDs from now on. The backfill moves supplier-shaped codes out of it into `x_supplier_code`.
- Statuses and the category tag are written whenever the app creates or updates the company (card export included), with no code drawn for `potential`.
- Companies under a parent (branches, e.g. 公路總局 under 交通部) carry no code of their own; the code sits on the top-level company.

### Local database

`organizations` gains `supplier_code` (unique, nullable), `supplier_category`, `customer_status`, `supplier_status`, `supplier_drive_folder_id`, `supplier_drive_url`. `tax_id` is widened to 32 characters. `issue_runs` gains `kind` (`project` default, `supplier`).

Migration of existing rows: `customer_status` = `active` where `customer_code` is set, `potential` where `is_nxt_customer` and no code, else none.

### Tax IDs

Suppliers include Japanese corporations (`T` + 13 digits), overseas companies (e.g. a Swedish VAT number) and public bodies with none. For suppliers `tax_id` is optional and validated by the organization's country: Taiwan = 8 digits with checksum (existing rule), Japan = `T\d{13}` or 13 digits, other countries = any non-empty text. Customers keep the existing 統編 rule.

## Drive layout

Two trees stay, because sales documents and purchasing documents differ (the `S059` folder holds POs, invoices, remittances and specifications). A party with both roles has one folder in each tree, each named with its own code; Odoo links them (`x_drive_url`, `x_supplier_drive_url`).

```
NXT Suppliers/
  Parts Suppliers/  Administration Suppliers/  Construction Suppliers/
  Forwarding Suppliers/  Inspection Suppliers/  Marketing Suppliers/
  Package Suppliers/  Japan Suppliers/ (legacy only)  S999 Others/
  Potential Suppliers/        (existing; holds prospects)
```

- A supplier folder is `S100 <name>`. It is created empty; year folders and documents are added by hand as today (no mandatory subfolders).
- New suppliers go in the folder matching their category: Parts, Administration, Construction, Forwarding, Inspection, Marketing, Packaging (`Package Suppliers`), Other (`S999 Others`). New Japanese suppliers go by category too; `Japan Suppliers` is kept for the existing ones. (Open question: should Japan stay a placement of its own? Default here: no.)
- **Promotion from potential:** the app looks in `Potential Suppliers` and in each category folder's `Potential` subfolder for a folder whose normalized name equals the party's. One match: it is moved under the category folder and prefixed with the code (these folders are uncoded, so adding the code is not a rename of a coded folder). None: a new folder is created. Several: warning, new folder created, nothing moved.
- Existing coded folders are never renamed or moved by the app.
- The app's Drive token covers only the NXT Google Drive. Folders elsewhere get a warning, as for customers.

## Flow

Same shape as the customer flow, reusing `issue_runs` and the step runner.

**Propose** (no side effects). Input: organization, category, tax ID (optional), phone, country. Match as for customers (tax ID, phone, fuzzy name against local organizations, then Odoo by tax ID and name). Show: the existing or tentative supplier code, the category folder, what approval will do, warnings. If the matched Odoo company already has an `x_supplier_code`, it is reused and nothing is drawn.

**Approve** steps (each idempotent, recorded in `issue_runs`): `local_org` → `supplier_code` (reuse the Odoo one, else draw `nxt.supplier`) → `odoo_company` (create or update; set `x_supplier_code`, `x_supplier_status=active`, category tag, vat; fill only empty fields) → `drive_supplier` (promotion rule above) → `person` (optional child contact, as for customers).

**Setting `potential` or `inactive`** is a plain update (`PUT /api/v2/organizations/{org}/roles`): local status plus the Odoo status field if a company exists. No run, no code.

## Changes to the customer flow

1. When the customer step finds an old code in Company ID that is supplier-shaped (`S###`, `SA###` and so on), it writes it to `x_supplier_code` (if empty) instead of `x_legacy_codes`, and never treats it as a project folder to move.
2. `customer_id_of` and `old_code_of` ignore supplier-shaped values. (The backfill clears them anyway; this guard covers records the backfill has not reached.)

## Backfill

A script, `scripts/backfill_suppliers.py`, dry-run by default (writes a CSV report), `--apply` to write. It reads the Odoo suppliers and the Drive supplier tree (top level and one level inside each category folder).

Matching: by code first (the Company ID value equals the folder's code), then by normalized name plus tax ID. A name-only match is a candidate the user confirms, never an automatic merge (this is how 光殿貿易 `P030` meets `SM009`).

Rules, in order:
0. Scope: only partners with `company_id in (1, False)`. Partners of other companies are counted in the report and never read further.
1. Company ID holds a supplier-shaped code: copy it to `x_supplier_code`, clear Company ID, set supplier status `active`, add the category tag from the folder it lives in. Shared partners (no company) are listed for confirmation instead.
2. A coded Drive folder with no Odoo record: create a minimal company (name, code, category, status, `company_id = 1`) unless the report marks it skipped.
3. Uncoded folders in `Potential*` locations and the regional Construction folders: status `potential`, no code.
4. The seven uncoded Japanese folders and anything ambiguous: listed in the report with the default `potential`, for the user to decide.
5. Conflicts are listed, never auto-resolved: the same code on two folders or two companies (`S058` is the known one; `SA034` on a parent and a branch is not a conflict), and customers whose Company ID looks like a supplier code.
6. Dual-role parties (customer and supplier) get both statuses and both codes; the customer code is not drawn here, it keeps coming from the customer flow.

Re-running is safe: every write checks the current Odoo value first.

## Non-Goals

- Moving or renaming existing coded folders, or merging the two Drive trees.
- Making product lines (S = SH200, J = NXT-JM) and the Odoo tag names configurable per company. Needed before other companies use the app; separate phase.
- Moving a folder when a party becomes inactive.
- A customer-side change to when the Customer ID is drawn.

## UI

The per-organization panel (today "NXT customer" with a switch) becomes a **Relationship** panel:

- Customer: status selector (none / potential / active / inactive). Choosing active, or the existing "Issue project code" action, runs the customer flow as today.
- Supplier: status selector, category selector, and an "Activate supplier" action that opens the review card (code, folder, what will be created) with Approve and Retry, like the customer review card.
- The whole panel is shown only while the module switch is on (decision 8).
- All strings neutral (`Customer`, `Supplier`, `Potential`, `Active`, `Inactive`); ja, en and zh-TW in `frontend/src/i18n.ts`. Existing `nxt*` strings keep their keys but their visible text drops "NXT".

## Routes

`POST /api/v2/suppliers/propose`, `POST /api/v2/suppliers/commit`, the existing run-retry route dispatches on `issue_runs.kind`, `PUT /api/v2/organizations/{org}/roles` (statuses, category). `PUT .../nxt-customer` stays working.
