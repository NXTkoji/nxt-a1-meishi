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
  received_date?: string
  notes?: string
  display_name_language?: string
  sync_status: string
  created_at: string
  sides: CardSide[]
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
  status: string
  org_names: OrgName[]
  details: PositionDetail[]
}

export interface Person {
  id: number
  external_id: string
  notes?: string
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
