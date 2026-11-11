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

## Non-Goals

- Renaming or renumbering any legacy folder or code. Legacy codes are kept as aliases.
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

Already existed: `res.partner.ref` (Customer ID), `vat` (統一編號), `category_id` (tags), `parent_id`.

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
2. Create or update `res.partner` (`ref`, `vat`, tags).
3. Create the customer folder `C0200 <name>` under NXT Customers and store its URL on the partner.
4. Create `project.project` with `x_project_code`, `x_product_line`, `x_sold_via`.
5. Create the project folder `C0200-DS01 <site>` with subfolders and store its URL.
6. Show the final codes and links.

Every step checks whether its result already exists (by `ref`, `x_project_code`, folder name) before creating, so re-running is safe.

### Project folder template

```
C0200 <customer>/
  C0200-DS01 <site>/
    1 photos/
    2 proposal/
    3 construction/
    4 finance & logistics/
    5 maintenance/
```

## Changes in this repository

- **Migration**: `organizations` gains `tax_id` (unique, nullable), `customer_code` (unique, nullable), `odoo_partner_id`, `drive_folder_id`, `drive_url`, `legacy_codes`. New `projects` table (`code` unique, `org_id`, `product_line`, `site_name`, `sold_via_org_id`, `odoo_project_id`, `drive_folder_id`, `drive_url`, `created_at`) and an `issue_runs` table for step state.
- **New service** `app/services/project_codes.py`: validation, organization matching, propose, commit, Drive folder creation (Google Drive API, using the existing OAuth with Drive scope).
- **Config**: `google_drive_customers_folder_id`.
- **Fix** `_find_or_create_company` in `odoo_sync.py`: look up by `ref` and `vat` before name, and set `ref` on create, so card exports stop creating duplicate companies.
- **Routes**: `POST /api/v2/customers/resolve`, `POST /api/v2/projects/propose`, `POST /api/v2/projects/commit`, `POST /api/v2/projects/{id}/retry`.
- **UI**: "Issue project code" action on the organization view, with the review card and Approve button.

## Risks

- Odoo is the source of truth; local SQLite is a cache. A failed commit must be resumable (hence `issue_runs` and the existence checks).
- The tentative Customer ID is a preview only; the final ID is drawn on approval.
- The OneDrive Graph service uses `/me/drive`; the mirror must be a separate job.

## Migration of existing customers (separate phase)

1. Resolve duplicates (e.g. DS130).
2. Backfill existing customers into Odoo with `x_legacy_codes`; no codes are reissued.
3. A customer gets a Customer ID when it next has activity or a new project.
4. Reseller P-sites (e.g. P065S50) become projects under their end-user customer with `sold_via` = the reseller.
