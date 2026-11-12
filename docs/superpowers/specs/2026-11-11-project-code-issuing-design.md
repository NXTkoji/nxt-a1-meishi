# Project Code Issuing (Customer ID + Project Code)

**Date:** 2026-11-11
**Status:** Design approved, Odoo setup done, pending implementation

## Problem

Project codes are assigned by hand (DS = SH200 deodorization, P = resellers, DJ = NXT-JM oil removal), with one folder per project in two Google Drives and one OneDrive. This breaks down because:

- The prefix encodes the customer's **role**, so a reseller who turns out to be an end user needs a new code.
- A customer buying both SH200 and NXT-JM needs two unrelated codes.
- Numbers are not unique across prefixes (094, 108, 111, 112, 117, 123 each exist under two prefixes).
- The same customer can exist twice (DS130 in both drives, with different spellings).
- Folder structure varies per customer.

## Goals

1. One permanent **Customer ID** per legal entity, independent of role and product.
2. **Project codes** that carry the product line and never collide.
3. Issuing is **propose then approve** inside the app. Nobody opens Odoo by hand.
4. On approval the app creates the Odoo contact, the Odoo project and the Google Drive folders.

## Scope: only for NXT customers

Most scanned cards are not NXT customers, so none of this appears by default.

- An organization has an **NXT customer** switch (`organizations.is_nxt_customer`, default off). In Odoo it is the company tag `NXT Customer`.
- The card editor shows the extra fields (統編, product line, site name, role, sold via) and the "Issue project code" action **only when the switch is on**. With it off, the card flow is exactly what it is today and no code, folder or project is created.
- The switch can be turned on later from the organization view (for example, a company scanned months ago becomes a customer). Issuing then reuses the existing Odoo company record and adds the Customer ID to it.

## Odoo record layout

The customer is the **company** record; people are contacts under it, as already done by the card export (`parent_id`).

1. Company partner (`is_company`): `company_registry` ("Company ID" in Odoo) = Customer ID, `vat` = 統編, tag `NXT Customer`, Drive URL. `ref` ("Reference") holds the user's own short names (e.g. 亞東石化) and is never used by the app.
2. Person partner(s): child of the company (`parent_id`), created or updated with the existing card export.
3. Project: `partner_id` = the company.

Individuals without a company: a company-type record named after the store or brand (or the person's name if there is none), with the person as its child contact. The phone number is the match key.

On approval the app ensures the company exists (matching by Customer ID in `company_registry`, then `vat`, then phone, then name candidates the user confirms), then creates or updates the card's person under it. A checkbox on the review card lets the user skip the person step.

## Non-Goals

- Renaming or renumbering any legacy folder or code. Legacy codes are kept as aliases and old folders keep their names (they are moved, never renamed).
- Replacing the card scan/export flow. It stays, but must stop creating duplicate companies.
- The OneDrive mirror (separate scheduled job, not part of the app).

## Code formats

| Object | Format | Example |
|---|---|---|
| Customer ID | `C` + 4 digits, never reused | `C0200` |
| Project code | `<customer>-<line><nn>` | `C0200-DS01`, `C0200-DJ01` |
| Product lines | `DS` = SH200 deodorization, `DJ` = NXT-JM oil removal | |
| Legacy alias | free text list | `DS176`, `P065, P065S50` |

- Customer numbering starts at **200**, above every legacy number (highest seen: DS176).
- `P` is retired as a prefix. A reseller-sold SH200 job is a `DS` project with `sold_via` = the reseller.
- Project number `nn` = highest existing number for that customer and line, plus 1.
- Role (Reseller, End user, or both) is an Odoo **tag**, not part of any code.

## Odoo model (set up on 2026-11-11)

Already existed: `res.partner.company_registry` ("Company ID": the Customer ID; before this feature it held old codes such as DS127, P030, SA028 or JFE), `vat` (統一編號), `category_id` (tags), `parent_id`. A Company ID counts as a Customer ID only when it matches `C` + 4 digits. When a company that already has an old code there is issued, the old code moves to `x_legacy_codes` (and, if it is a project-style code such as DS127, its Drive folder is moved like any other old code).

Created by API:

| Model | Field | Type |
|---|---|---|
| `ir.sequence` | code `nxt.customer`, prefix `C`, padding 4, next number 200 | sequence |
| `res.partner` | `x_legacy_codes` | char |
| `res.partner` | `x_drive_url` | char |
| `project.project` | `x_project_code` | char (indexed) |
| `project.project` | `x_product_line` | selection DS / DJ |
| `project.project` | `x_sold_via` | many2one res.partner |
| `project.project` | `x_drive_url` | char |

Uniqueness of `x_project_code` is enforced by the app (check before create), not by an SQL constraint.

Individuals without a 統編: the record is a company-type partner named after the store or brand, with the person as a child contact. The phone number is the match key.

## Flow: propose, then approve

### 1. Propose (no side effects)

Input: name, brand/store (optional), 統編 (optional), phone, product line, role, sold via (optional), site name.

The app returns a review card:

1. **Normalize**: 統編 must be 8 digits and pass the checksum; phone is sanitized.
2. **Match** in order: `vat` exact, phone exact, fuzzy name. Exact matches reuse the existing Customer ID. Fuzzy matches are shown as candidates and the user chooses. Never auto-merge.
3. **Suggest** the Customer ID (existing, or the next number read from the sequence without consuming it, labelled tentative) and the project code.
4. **List** exactly what approval will do: Odoo contact, Odoo project, Drive folders.

The user can correct product line, site name or the chosen candidate, then approves.

### 2. Approve (commit)

Executed as a resumable sequence of steps, recorded in a local state table so a failure can be retried without duplicating anything:

1. Draw the real Customer ID with Odoo `next_by_code` (atomic). It can differ from the tentative one if someone else issued meanwhile; the final value is shown.
2. Create or update `res.partner` (`company_registry`, `vat`, tags; an old code in `company_registry` moves to `x_legacy_codes`).
3. Create the customer folder `C0200 <name>` under NXT Customers and store its URL on the partner.
4. Create `project.project` with `x_project_code`, `x_product_line`, `x_sold_via`.
5. Create the project folder `C0200-DS01 <site>` with subfolders and store its URL.
6. Show the final codes and links.

Every step checks whether its result already exists (by Customer ID, `x_project_code`, folder name) before creating, so re-running is safe.

### Old customers (legacy codes and folders)

When an old customer starts a new project, the request carries the optional field `legacy_codes` (for example `DS176, P065S50`). On approval the old folder for each code is **moved, with its name unchanged, into the new customer folder**, so the folder tree itself shows the customer's history:

```
C0200 <customer>/
  C0200-DS01 <site>/        new project
  DS176 <old site>/         old project, moved as is
```

- Step `legacy` runs after `drive_customer`. It finds a folder named `<code>` or starting with `<code> ` anywhere in the NXT Google Drive (excluding the customer folder and the NXT Customers root) and moves it with `addParents`/`removeParents`. Not found is a warning, not a failure; the code is still recorded.
- Several folders for one code stops the run at `legacy` (nothing is moved) until the extra one is renamed or moved. A folder already inside the customer folder is left alone, so retries and later projects are safe.
- The codes are merged into `organizations.legacy_codes` and Odoo `x_legacy_codes`. One old code can belong to only one customer (422 otherwise).
- Propose previews this read-only ("Move old folder ... into the customer folder", or a warning that none was found).
- **Limit:** the app's Drive token only covers the NXT Google Drive. Old folders in another Google account's Drive (for example "NXT Customers Old") or in OneDrive cannot be moved by the app. The warning says so; move them by hand, or handle them in the later bulk migration.

### Folder template

```
C0200 <customer>/
  finance & logistics/        one per customer, shared by all its projects
  C0200-DS01 <site>/          a customer can have several project folders
    1 requirement/
    2 proposal/
    3 construction/
    4 maintenance/
  C0200-DJ01 <site>/
    ...
```

Photos have no folder of their own; they go into whichever numbered folder they belong to. The customer-level `finance & logistics` folder is created with the customer folder (and added to an existing customer folder the next time a project is issued).

## Changes in this repository

- **Migration**: `organizations` gains `is_nxt_customer` (bool, default false), `tax_id` (unique, nullable), `customer_code` (unique, nullable), `odoo_partner_id`, `drive_folder_id`, `drive_url`, `legacy_codes`. New `projects` table (`code` unique, `org_id`, `product_line`, `site_name`, `sold_via_org_id`, `odoo_project_id`, `drive_folder_id`, `drive_url`, `created_at`) and an `issue_runs` table for step state.
- **New service** `app/services/project_codes.py`: validation, organization matching, propose, commit, Drive folder creation (Google Drive API, using the existing OAuth with Drive scope).
- **Config**: `google_drive_customers_folder_id`.
- **Fix** `_find_or_create_company` in `odoo_sync.py`: look up by `vat` and name, preferring the company with a Customer ID in `company_registry`, so card exports stop creating duplicate companies.
- **Routes**: `POST /api/v2/customers/resolve`, `POST /api/v2/projects/propose`, `POST /api/v2/projects/commit`, `POST /api/v2/projects/{id}/retry`.
- **UI**: an "NXT customer" switch in the card editor and organization view. Only when it is on: the NXT fields and the "Issue project code" action, with the review card and Approve button.

## Risks

- Odoo is the source of truth; local SQLite is a cache. A failed commit must be resumable (hence `issue_runs` and the existence checks).
- The tentative Customer ID is a preview only; the final ID is drawn on approval.
- The OneDrive Graph service uses `/me/drive`; the mirror must be a separate job.

## Migration of existing customers (separate phase)

1. Resolve duplicates (e.g. DS130).
2. Backfill existing customers into Odoo with `x_legacy_codes`; no codes are reissued.
3. A customer gets a Customer ID when it next has activity or a new project.
4. Reseller P-sites (e.g. P065S50) become projects under their end-user customer with `sold_via` = the reseller.
