import { get, post, patch, del, uploadFile } from './client'
import type {
  Card,
  CardListItem,
  Country,
  MyCompany,
  Occasion,
  Person,
  PersonListItem,
  RelationshipType,
} from '../types'

// Cards
export const listCards = (params?: {
  person_id?: number
  occasion_id?: number
  my_company_id?: number
  q?: string
  year?: number
  month?: string        // "YYYY-MM"
  date?: string         // "YYYY-MM-DD"
  not_exported?: boolean
  offset?: number
  limit?: number
}) => {
  const qs = new URLSearchParams()
  if (params?.person_id) qs.set('person_id', String(params.person_id))
  if (params?.occasion_id) qs.set('occasion_id', String(params.occasion_id))
  if (params?.my_company_id) qs.set('my_company_id', String(params.my_company_id))
  if (params?.q) qs.set('q', params.q)
  if (params?.year) qs.set('year', String(params.year))
  if (params?.month) qs.set('month', params.month)
  if (params?.date) qs.set('date', params.date)
  if (params?.not_exported) qs.set('not_exported', 'true')
  if (params?.offset) qs.set('offset', String(params.offset))
  if (params?.limit) qs.set('limit', String(params.limit))
  return get<CardListItem[]>(`/api/v2/cards?${qs}`)
}

export const getCard = (id: string) => get<Card>(`/api/v2/cards/${id}`)

export const updateCard = (id: string, body: { received_date?: string | null; notes?: string; display_name_language?: string | null; my_company_ids?: number[]; occasion_id?: number | null }) =>
  patch<import('../types').Card>(`/api/v2/cards/${id}`, body)

export const deleteCard = (id: string) => del(`/api/v2/cards/${id}`)

export const addCardSide = (cardExtId: string, file: File): Promise<import('../types').CardSide> => {
  const form = new FormData()
  form.append('file', file)
  return uploadFile<import('../types').CardSide>(`/api/v2/cards/${cardExtId}/sides`, form)
}

export const deleteCardSide = (cardExtId: string, sideOrder: number) =>
  del(`/api/v2/cards/${cardExtId}/sides/${sideOrder}`)

export const promoteCardSideToFront = (cardExtId: string, sideOrder: number) =>
  post<void>(`/api/v2/cards/${cardExtId}/sides/${sideOrder}/promote`, {})

// Persons
export const listPersons = (q?: string) =>
  get<PersonListItem[]>(`/api/v2/persons${q ? `?q=${encodeURIComponent(q)}` : ''}`)

export const getPerson = (id: string) => get<Person>(`/api/v2/persons/${id}`)

export const deletePerson = (personExtId: string) => del(`/api/v2/persons/${personExtId}`)

export const updatePersonName = (personExtId: string, nameId: number, body: { full_name?: string; family_name?: string; given_name?: string }) =>
  patch<import('../types').PersonName>(`/api/v2/persons/${personExtId}/names/${nameId}`, body)

export const addContactDetail = (personExtId: string, body: { detail_type: string; value?: string; label?: string }) =>
  post<import('../types').ContactDetail>(`/api/v2/persons/${personExtId}/contact-details`, body)

export const updateContactDetail = (personExtId: string, detailId: number, body: { value?: string; label?: string; detail_type?: string; country_code?: string }) =>
  patch<import('../types').ContactDetail>(`/api/v2/persons/${personExtId}/contact-details/${detailId}`, body)

export const deleteContactDetail = (personExtId: string, detailId: number) =>
  del(`/api/v2/persons/${personExtId}/contact-details/${detailId}`)

export const updatePositionDetail = (personExtId: string, positionId: number, detailId: number, body: { title?: string; department?: string }) =>
  patch<import('../types').PositionDetail>(`/api/v2/persons/${personExtId}/positions/${positionId}/details/${detailId}`, body)

export const updateOrgName = (personExtId: string, positionId: number, orgNameId: number, body: { name: string }) =>
  patch<import('../types').OrgName>(`/api/v2/persons/${personExtId}/positions/${positionId}/org-name/${orgNameId}`, body)

// Countries
export const listCountries = () => get<Country[]>('/api/v2/countries')

export const createCountry = (body: { code: string; name: string }) =>
  post<Country>('/api/v2/countries', body)

export const updateCountry = (id: number, body: { name: string }) =>
  patch<Country>(`/api/v2/countries/${id}`, body)

export const deleteCountry = (id: number) => del(`/api/v2/countries/${id}`)

// Occasions
export const listOccasions = () => get<Occasion[]>('/api/v2/occasions')

export const createOccasion = (body: Partial<Occasion>) =>
  post<Occasion>('/api/v2/occasions', body)

export const updateOccasion = (id: number, body: Partial<Occasion>) =>
  patch<Occasion>(`/api/v2/occasions/${id}`, body)

export const deleteOccasion = (id: number) => del(`/api/v2/occasions/${id}`)

// Settings
export const listMyCompanies = () => get<MyCompany[]>('/api/v2/settings/my-companies')

export const createMyCompany = (body: { name: string; notes?: string }) =>
  post<MyCompany>('/api/v2/settings/my-companies', body)

export const updateMyCompany = (id: number, body: { name?: string; notes?: string }) =>
  patch<MyCompany>(`/api/v2/settings/my-companies/${id}`, body)

export const deleteMyCompany = (id: number) =>
  del(`/api/v2/settings/my-companies/${id}`)

export const listRelationshipTypes = () =>
  get<RelationshipType[]>('/api/v2/settings/relationship-types')

export const runExport = (body: {
  card_external_ids: string[]
  destinations: string[]
}) => post<import('../types').ExportResponse>('/api/v2/export', body)

// Re-export sessions API
export * from './sessions'
