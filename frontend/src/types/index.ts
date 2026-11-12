// Mirror of app/schemas/parsed_card.py and app/schemas/api.py

export interface CF {
  value: string
  confidence: number
}

export interface ParsedName {
  language: string
  name_type: string
  family_name?: CF
  given_name?: CF
  full_name: CF
}

export interface ParsedOrgName {
  language: string
  name: CF
}

export interface ParsedPositionDetail {
  language: string
  title?: CF
  department?: CF
}

export interface ParsedPosition {
  org_names: ParsedOrgName[]
  details: ParsedPositionDetail[]
  // Set in the scan review; saving the card turns the org's NXT customer switch on
  is_nxt_customer?: boolean
}

export interface ParsedContactDetail {
  detail_type: string
  value: CF
  label?: string
}

export interface ParsedCard {
  names: ParsedName[]
  positions: ParsedPosition[]
  contact_details: ParsedContactDetail[]
  card_date?: string
  birthday?: string
  notes?: string
  languages_detected: string[]
  overall_confidence: number
}

export interface MatchResult {
  is_existing: boolean
  person_id?: number
  person_external_id?: string   // add this line
  match_confidence: number
  match_method?: string
  matched_name?: string
}

// Session types
export interface SessionImage {
  id: number
  image_filename: string
  temp_card_id?: string
  side_order?: number
  uploaded_at: string
}

export interface Session {
  id: number
  external_id: string
  status: string
  notes?: string
  created_at: string
  images: SessionImage[]
}

// Analysis SSE event
export interface AnalysisEvent {
  type: 'progress' | 'result' | 'error' | 'done'
  temp_card_id?: string
  message?: string
  parsed?: ParsedCard
  match?: MatchResult
  error?: string
}

// Card draft for confirm
export interface CardDraft {
  temp_card_id: string
  parsed: ParsedCard
  match_person_id?: number
  my_company_ids: number[]
  occasion_id?: number
  received_date?: string
  notes?: string
}

// Confirmed response
export interface ConfirmedCard {
  temp_card_id: string
  card_id: number
  card_external_id: string
  person_id: number
  person_external_id: string
}

// Cards
export interface CardSide {
  side_order: number
  image_path: string
  image_filename: string
  width_px?: number
  height_px?: number
}

export interface Card {
  id: number
  external_id: string
  person_id: number
  person_external_id?: string
  occasion_id?: number
  /** Name kept from a deleted occasion. Shown only while occasion_id is unset. */
  occasion_label?: string | null
  received_date?: string
  notes?: string
  display_name_language?: string
  sync_status: string
  created_at: string
  sides: CardSide[]
  my_company_ids: number[]
}

export interface CardListItem {
  id: number
  external_id: string
  person_id: number
  received_date?: string
  sync_status: string
  created_at: string
  person_name?: string
  front_image_path?: string
  synced_destinations: string[]
}

/** One year/month bucket from GET /api/v2/cards/facets.
 *
 *  `month` is 1-12 (not 0-indexed like JS Date.getMonth()). `count` is authoritative:
 *  the backend buckets by the same filing-date expression that `?month=YYYY-MM` filters
 *  on, so a bucket's count always equals the number of rows that month query returns.
 *  That equality is what lets the Collection tree show a correct count for a month it
 *  has not fetched yet. */
export interface CardFacet {
  year: number
  month: number
  count: number
}

// Persons
export interface PersonName {
  id: number
  language: string
  name_type: string
  family_name?: string
  given_name?: string
  honorific?: string
  full_name: string
  is_current: boolean
  valid_from: string
  source: string
}

export interface ContactDetail {
  id: number
  detail_type: string
  value: string
  label?: string
  country_code?: string
  is_primary: boolean
}

export interface OrgName {
  id: number
  language: string
  name: string
  is_current: boolean
}

export interface PositionDetail {
  id: number
  language: string
  title?: string
  department?: string
}

export interface Position {
  id: number
  org_id: number
  /** External id of the organization; addresses its NXT customer panel. */
  org_ext_id?: string | null
  status: string
  org_names: OrgName[]
  details: PositionDetail[]
  /** The organization's shared memo — the same on every person at that org. */
  org_notes?: string | null
}

export interface Person {
  id: number
  external_id: string
  notes?: string
  birthday?: string
  created_at: string
  updated_at: string
  names: PersonName[]
  contact_details: ContactDetail[]
  positions: Position[]
}

export interface PersonListItem {
  id: number
  external_id: string
  primary_name?: string
  family_name?: string
  country_code?: string
  created_at: string
}

/** One country bucket of the Persons tab, from GET /api/v2/persons/facets.
 *  `country_code` is null for persons with no derivable country; the endpoint sorts
 *  codes ascending with that null bucket last. */
export interface PersonFacet {
  country_code: string | null
  count: number
}

export interface MergeResult {
  person: Person
  duplicate_contact_count: number
}

// Countries
export interface Country {
  id: number
  code: string
  name: string
  created_at: string
}

// Occasions
export interface Occasion {
  id: number
  name: string
  event_date?: string
  location?: string
  notes?: string
  created_at: string
  /** Live cards using this occasion. Computed by the list and update endpoints;
   *  0 from create, where no card can be linked yet. */
  card_count: number
}

// Settings
export interface MyCompany {
  id: number
  name: string
  google_label?: string
  notes?: string
}

export interface CardSyncBadge {
  destination: string   // "odoo" | "google_contacts"
}

export interface ExportResultItem {
  card_external_id: string
  destination: string
  result: 'created' | 'updated' | 'error'
  error_message?: string
}

export interface ExportResponse {
  results: ExportResultItem[]
}

export interface RelationshipType {
  id: number
  key: string
  label: string
  is_predefined: boolean
}


// ─── NXT customers / project codes ────────────────────────────────────────────

export interface OrgInfo {
  id: number
  external_id: string
  is_nxt_customer: boolean
  tax_id?: string | null
  customer_code?: string | null
  drive_url?: string | null
  legacy_codes?: string | null
}

export interface OrgListItem {
  id: number
  external_id: string
  primary_name?: string | null
  is_nxt_customer: boolean
  customer_code?: string | null
}

export interface IssueRequest {
  org_ext_id?: string
  customer_name?: string
  tax_id?: string
  phone?: string
  product_line: string
  site_name: string
  roles: string[]
  sold_via_org_ext_id?: string
  chosen_org_ext_id?: string
  create_new?: boolean
  // Card whose person is exported under the company on approval (unless include_person is false)
  card_external_id?: string
  include_person?: boolean
  legacy_codes?: string
}

export interface IssueCandidate {
  org_ext_id: string
  name: string
  customer_code?: string | null
  tax_id?: string | null
  reason: string
  confidence: number
  auto: boolean
}

export interface IssueProposal {
  customer_mode: 'existing' | 'new'
  org_ext_id?: string | null
  customer_name: string
  tax_id?: string | null
  customer_code?: string | null
  suggested_customer_code?: string | null
  needs_choice: boolean
  candidates: IssueCandidate[]
  project_code?: string | null
  project_code_tentative: boolean
  product_line: string
  site_name: string
  sold_via_name?: string | null
  actions: string[]
  warnings: string[]
}

export interface IssueRun {
  run_id: string
  status: 'done' | 'failed' | 'running'
  failed_step?: string | null
  error?: string | null
  steps: Record<string, unknown>
  customer_code?: string | null
  project_code?: string | null
  customer_drive_url?: string | null
  project_drive_url?: string | null
}
