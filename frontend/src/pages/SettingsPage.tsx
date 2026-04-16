import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listMyCompanies, createMyCompany, updateMyCompany, deleteMyCompany,
  listOccasions, createOccasion, updateOccasion, deleteOccasion,
} from '../api'
import { useToast } from '../components/Toast'
import { useLang } from '../LangContext'
import type { MyCompany, Occasion } from '../types'


// ─── Company row ──────────────────────────────────────────────────────────────

function CompanyRow({ company }: { company: MyCompany }) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(company.name)

  const updateMutation = useMutation({
    mutationFn: (name: string) => updateMyCompany(company.id, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-companies'] })
      setEditing(false)
      showToast(t.savedChanges)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteMyCompany(company.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-companies'] })
      showToast(t.deleteConfirmed)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const commit = () => {
    const name = draft.trim()
    if (!name || name === company.name) { setEditing(false); return }
    updateMutation.mutate(name)
  }

  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100 last:border-0">
      {editing ? (
        <>
          <input
            className="flex-1 text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(company.name); setEditing(false) } }}
            autoFocus
          />
          <button
            className="text-xs text-blue-600 font-medium disabled:opacity-50 shrink-0"
            onClick={commit}
            disabled={updateMutation.isPending}
          >{t.saveBtn}</button>
          <button
            className="text-xs text-gray-400 shrink-0"
            onClick={() => { setDraft(company.name); setEditing(false) }}
          >{t.cancelBtn}</button>
        </>
      ) : (
        <>
          <div className="flex-1 min-w-0">
            <button
              className="text-sm text-gray-900 hover:text-blue-600 text-left w-full truncate flex items-center gap-1 group"
              onClick={() => { setDraft(company.name); setEditing(true) }}
            >
              {company.name}
              <span className="opacity-0 group-hover:opacity-60 text-xs text-blue-400">✏️</span>
            </button>
            {company.notes && <p className="text-xs text-gray-400 mt-0.5">{company.notes}</p>}
          </div>
          <button
            onClick={() => { if (window.confirm(`${t.confirmDelete}\n"${company.name}"`)) deleteMutation.mutate() }}
            disabled={deleteMutation.isPending}
            className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50 shrink-0"
          >{t.deleteBtn}</button>
        </>
      )}
    </div>
  )
}

// ─── Occasion row ─────────────────────────────────────────────────────────────

function OccasionRow({ occasion }: { occasion: Occasion }) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(occasion.name)

  const updateMutation = useMutation({
    mutationFn: (name: string) => updateOccasion(occasion.id, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['occasions'] })
      setEditing(false)
      showToast(t.savedChanges)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteOccasion(occasion.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['occasions'] })
      showToast(t.deleteConfirmed)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const commit = () => {
    const name = draft.trim()
    if (!name || name === occasion.name) { setEditing(false); return }
    updateMutation.mutate(name)
  }

  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100 last:border-0">
      {editing ? (
        <>
          <input
            className="flex-1 text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(occasion.name); setEditing(false) } }}
            autoFocus
          />
          <button
            className="text-xs text-blue-600 font-medium disabled:opacity-50 shrink-0"
            onClick={commit}
            disabled={updateMutation.isPending}
          >{t.saveBtn}</button>
          <button
            className="text-xs text-gray-400 shrink-0"
            onClick={() => { setDraft(occasion.name); setEditing(false) }}
          >{t.cancelBtn}</button>
        </>
      ) : (
        <>
          <div className="flex-1 min-w-0">
            <button
              className="text-sm text-gray-900 hover:text-blue-600 text-left w-full truncate flex items-center gap-1 group"
              onClick={() => { setDraft(occasion.name); setEditing(true) }}
            >
              {occasion.name}
              <span className="opacity-0 group-hover:opacity-60 text-xs text-blue-400">✏️</span>
            </button>
            {occasion.event_date && (
              <p className="text-xs text-gray-400 mt-0.5">{occasion.event_date}</p>
            )}
          </div>
          <button
            onClick={() => { if (window.confirm(`${t.confirmDelete}\n"${occasion.name}"`)) deleteMutation.mutate() }}
            disabled={deleteMutation.isPending}
            className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50 shrink-0"
          >{t.deleteBtn}</button>
        </>
      )}
    </div>
  )
}

// ─── Settings page ────────────────────────────────────────────────────────────

export function SettingsPage() {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const [newCompany, setNewCompany] = useState('')
  const [newOccasion, setNewOccasion] = useState('')

  const { data: companies = [] } = useQuery<MyCompany[]>({
    queryKey: ['my-companies'],
    queryFn: listMyCompanies,
  })

  const { data: occasions = [] } = useQuery<Occasion[]>({
    queryKey: ['occasions'],
    queryFn: listOccasions,
  })

  const addCompanyMutation = useMutation({
    mutationFn: (name: string) => createMyCompany({ name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-companies'] })
      setNewCompany('')
      showToast(t.savedChanges)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const addOccasionMutation = useMutation({
    mutationFn: (name: string) => createOccasion({ name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['occasions'] })
      setNewOccasion('')
      showToast(t.savedChanges)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  return (
    <div className="max-w-2xl mx-auto py-8 px-4 space-y-8">
      <h1 className="text-lg font-semibold text-gray-900">{t.settingsTitle}</h1>

      {/* My Companies */}
      <section>
        <h2 className="text-sm font-medium text-gray-700 mb-3">{t.myCompaniesTitle}</h2>
        <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
          {companies.map(c => <CompanyRow key={c.id} company={c} />)}
          {companies.length === 0 && (
            <p className="text-sm text-gray-400 italic px-4 py-3">—</p>
          )}
        </div>
        <div className="flex gap-2 mt-3">
          <input
            type="text"
            value={newCompany}
            onChange={e => setNewCompany(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && newCompany.trim()) addCompanyMutation.mutate(newCompany.trim()) }}
            placeholder={t.addCompanyPlaceholder}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={() => { const n = newCompany.trim(); if (n) addCompanyMutation.mutate(n) }}
            disabled={!newCompany.trim() || addCompanyMutation.isPending}
            className="btn-primary text-sm disabled:opacity-50"
          >{t.addCompanyBtn}</button>
        </div>
      </section>

      {/* Occasions */}
      <section>
        <h2 className="text-sm font-medium text-gray-700 mb-3">{t.occasionsTitle}</h2>
        <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
          {occasions.map(o => <OccasionRow key={o.id} occasion={o} />)}
          {occasions.length === 0 && (
            <p className="text-sm text-gray-400 italic px-4 py-3">—</p>
          )}
        </div>
        <div className="flex gap-2 mt-3">
          <input
            type="text"
            value={newOccasion}
            onChange={e => setNewOccasion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && newOccasion.trim()) addOccasionMutation.mutate(newOccasion.trim()) }}
            placeholder={t.addOccasionPlaceholder}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={() => { const n = newOccasion.trim(); if (n) addOccasionMutation.mutate(n) }}
            disabled={!newOccasion.trim() || addOccasionMutation.isPending}
            className="btn-primary text-sm disabled:opacity-50"
          >{t.addCompanyBtn}</button>
        </div>
      </section>
    </div>
  )
}
