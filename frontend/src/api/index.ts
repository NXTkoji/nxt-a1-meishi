import { get, post, patch, del, uploadFile } from './client'
import type {
  Card,
  CardListItem,
  MyCompany,
  Occasion,
  Person,
  PersonListItem,
  RelationshipType,
} from '../types'

// Cards
export const listCards = (params?: { person_id?: number; offset?: number; limit?: number }) => {
  const qs = new URLSearchParams()
  if (params?.person_id) qs.set('person_id', String(params.person_id))
  if (params?.offset) qs.set('offset', String(params.offset))
  if (params?.limit) qs.set('limit', String(params.limit))
  return get<CardListItem[]>(`/api/v2/cards?${qs}`)
}

export const getCard = (id: string) => get<Card>(`/api/v2/cards/${id}`)

export const updateCard = (id: string, body: { received_date?: string | null; notes?: string }) =>
  patch<import('../types').Card>(`/api/v2/cards/${id}`, body)

export const deleteCard = (id: string) => del(`/api/v2/cards/${id}`)

export const addCardSide = (cardExtId: string, file: File): Promise<import('../types').CardSide> => {
  const form = new FormData()
  form.append('file', file)
  return uploadFile<import('../types').CardSide>(`/api/v2/cards/${cardExtId}/sides`, form)
}

export const deleteCardSide = (cardExtId: string, sideOrder: number) =>
  del(`/api/v2/cards/${cardExtId}/sides/${sideOrder}`)

// Persons
export const listPersons = (q?: string) =>
  get<PersonListItem[]>(`/api/v2/persons${q ? `?q=${encodeURIComponent(q)}` : ''}`)

export const getPerson = (id: string) => get<Person>(`/api/v2/persons/${id}`)

export const deletePerson = (personExtId: string) => del(`/api/v2/persons/${personExtId}`)

export const updatePersonName = (personExtId: string, nameId: number, body: { full_name?: string; family_name?: string; given_name?: string }) =>
  patch<import('../types').PersonName>(`/api/v2/persons/${personExtId}/names/${nameId}`, body)

export const addContactDetail = (personExtId: string, body: { detail_type: string; value?: string; label?: string }) =>
  post<import('../types').ContactDetail>(`/api/v2/persons/${personExtId}/contact-details`, body)

export const updateContactDetail = (personExtId: string, detailId: number, body: { value?: string; label?: string; detail_type?: string }) =>
  patch<import('../types').ContactDetail>(`/api/v2/persons/${personExtId}/contact-details/${detailId}`, body)

export const deleteContactDetail = (personExtId: string, detailId: number) =>
  del(`/api/v2/persons/${personExtId}/contact-details/${detailId}`)

export const updatePositionDetail = (personExtId: string, positionId: number, detailId: number, body: { title?: string; department?: string }) =>
  patch<import('../types').PositionDetail>(`/api/v2/persons/${personExtId}/positions/${positionId}/details/${detailId}`, body)

export const updateOrgName = (personExtId: string, positionId: number, orgNameId: number, body: { name: string }) =>
  patch<import('../types').OrgName>(`/api/v2/persons/${personExtId}/positions/${positionId}/org-name/${orgNameId}`, body)

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

// Re-export sessions API
export * from './sessions'
