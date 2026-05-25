/**
 * CardDetailPage — view and edit a saved business card.
 *
 * URL: /cards/:external_id
 * Shows card images (click to enlarge) + editable person data.
 */
import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getCard,
  updateCard,
  deleteCard,
  getPerson,
  addCardSide,
  deleteCardSide,
  promoteCardSideToFront,
  listMyCompanies,
  listOccasions,
  createOccasion,
} from '../api'
import { LightboxImage } from '../components/ImageLightbox'
import { PersonEditor } from '../components/PersonEditor'
import { useToast } from '../components/Toast'
import { useLang } from '../LangContext'
import type { Card, MyCompany, Occasion, Person } from '../types'

function useCardExtId(): string {
  return window.location.pathname.split('/').pop() ?? ''
}

export function CardDetailPage() {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const extId = useCardExtId()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: card, isLoading: cardLoading, error: cardError } = useQuery<Card>({
    queryKey: ['card', extId],
    queryFn: () => getCard(extId),
    enabled: !!extId,
  })

  const { data: person, isLoading: personLoading } = useQuery<Person>({
    queryKey: ['person', card?.person_external_id],
    queryFn: () => getPerson(card!.person_external_id!),
    enabled: !!card?.person_external_id,
  })

  const { data: companies = [] } = useQuery<MyCompany[]>({
    queryKey: ['my-companies'],
    queryFn: listMyCompanies,
  })

  const { data: occasions = [] } = useQuery<Occasion[]>({
    queryKey: ['occasions'],
    queryFn: listOccasions,
  })

  const [addingOccasion, setAddingOccasion] = useState(false)
  const [newOccasionName, setNewOccasionName] = useState('')

  const addOccasionMutation = useMutation({
    mutationFn: (name: string) => createOccasion({ name }),
    onSuccess: (occ) => {
      qc.invalidateQueries({ queryKey: ['occasions'] })
      updateCard(card?.external_id ?? '', { occasion_id: occ.id }).then(() =>
        qc.invalidateQueries({ queryKey: ['card', extId] })
      )
      setAddingOccasion(false)
      setNewOccasionName('')
    },
  })

  const addSideMutation = useMutation({
    mutationFn: (file: File) => addCardSide(extId, file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['card', extId] })
      showToast(t.savedChanges)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const deleteSideMutation = useMutation({
    mutationFn: (sideOrder: number) => deleteCardSide(extId, sideOrder),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['card', extId] })
      showToast(t.deleteConfirmed)
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  const promoteMutation = useMutation({
    mutationFn: (sideOrder: number) => promoteCardSideToFront(extId, sideOrder),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['card', extId] }),
    onError: () => showToast(t.saveError, 'error'),
  })

  const deleteCardMutation = useMutation({
    mutationFn: () => deleteCard(extId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cards'] })
      window.location.href = '/collection'
    },
    onError: () => showToast(t.saveError, 'error'),
  })

  if (cardLoading || personLoading) {
    return <div className="max-w-4xl mx-auto py-12 text-center text-gray-400">{t.loading}</div>
  }
  if (cardError || !card) {
    return <div className="max-w-4xl mx-auto py-12 text-center text-red-400">Card not found</div>
  }

  const canDeleteSide = card.sides.length > 1
  const personExtId = card.person_external_id

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      {/* Back + person link */}
      <div className="flex items-center gap-3">
        <a href="/collection" className="text-sm text-blue-500 hover:text-blue-700">← {t.navCollection}</a>
        {personExtId && (
          <a href={`/persons/${personExtId}`} className="text-sm text-gray-400 hover:text-blue-500">
            {t.viewPerson} →
          </a>
        )}
      </div>

      {/* Card images */}
      <section className="flex gap-3 flex-wrap items-end">
        {card.sides
          .slice()
          .sort((a, b) => a.side_order - b.side_order)
          .map(side => (
            <div key={side.side_order} className="text-center group relative">
              <LightboxImage
                src={`/api/v2/images/${side.image_path}`}
                alt={`side ${side.side_order}`}
                className="h-40 w-auto max-w-xs rounded-lg border border-gray-200 object-contain bg-gray-50 shadow-sm"
              />
              {side.side_order === 0 ? (
                <p className="text-xs text-gray-400 mt-1">{t.sideLabels[0]}</p>
              ) : (
                <button
                  className="text-xs text-blue-500 hover:text-blue-700 mt-1 hover:underline underline-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
                  onClick={() => promoteMutation.mutate(side.side_order)}
                  disabled={promoteMutation.isPending}
                >
                  {t.swapSidesLabel}
                </button>
              )}
              {canDeleteSide && (
                <button
                  className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-red-500 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center shadow"
                  onClick={() => { if (window.confirm(t.confirmDelete)) deleteSideMutation.mutate(side.side_order) }}
                  title={t.removeLabel}
                >✕</button>
              )}
            </div>
          ))}

        {/* Add image button */}
        <div className="text-center">
          <button
            className="h-40 w-28 rounded-lg border-2 border-dashed border-gray-300 hover:border-blue-400 hover:bg-blue-50 flex flex-col items-center justify-center gap-1 text-gray-400 hover:text-blue-500 transition-colors disabled:opacity-50"
            onClick={() => fileInputRef.current?.click()}
            disabled={addSideMutation.isPending}
          >
            <span className="text-2xl">{addSideMutation.isPending ? '…' : '＋'}</span>
            <span className="text-xs">{t.addImageLabel}</span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={e => {
              const file = e.target.files?.[0]
              if (file) addSideMutation.mutate(file)
              e.target.value = ''
            }}
          />
        </div>
      </section>

      {/* Person data */}
      {person && (
        <PersonEditor
          person={person}
          onUpdated={() => qc.invalidateQueries({ queryKey: ['person', card.person_external_id] })}
        />
      )}

      {/* Metadata */}
      <section className="border-t border-gray-100 pt-4 space-y-3">
        {/* Met As */}
        {companies.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-400">{t.myCompanyLabel}</span>
            <div className="flex flex-wrap gap-1">
              {companies.map(c => {
                const selected = (card.my_company_ids ?? []).includes(c.id)
                return (
                  <button
                    key={c.id}
                    onClick={async () => {
                      const current = card.my_company_ids ?? []
                      const next = selected ? current.filter(id => id !== c.id) : [...current, c.id]
                      await updateCard(card.external_id, { my_company_ids: next })
                      qc.invalidateQueries({ queryKey: ['card', extId] })
                    }}
                    className={`px-2 py-0.5 rounded border text-xs ${selected ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 text-gray-600'}`}
                  >
                    {c.name}
                  </button>
                )
              })}
            </div>
          </div>
        )}
        {/* Occasion */}
        <div className="flex items-start gap-2 flex-wrap">
          <span className="text-xs text-gray-400 mt-0.5">{t.occasionLabel}</span>
          <div className="flex flex-col gap-1">
            <select
              value={card.occasion_id ?? ''}
              onChange={async e => {
                const val = e.target.value ? Number(e.target.value) : null
                await updateCard(card.external_id, { occasion_id: val })
                qc.invalidateQueries({ queryKey: ['card', extId] })
              }}
              className="border border-gray-200 rounded px-2 py-0.5 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
            >
              <option value="">{t.noneOption}</option>
              {[...occasions]
                .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                .map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            {addingOccasion ? (
              <div className="flex gap-1">
                <input
                  type="text"
                  value={newOccasionName}
                  onChange={e => setNewOccasionName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newOccasionName.trim()) addOccasionMutation.mutate(newOccasionName.trim())
                    if (e.key === 'Escape') { setAddingOccasion(false); setNewOccasionName('') }
                  }}
                  placeholder={t.occasionNewPlaceholder}
                  className="border border-gray-300 rounded px-2 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                  autoFocus
                />
                <button
                  className="text-xs text-blue-600 font-medium disabled:opacity-50"
                  disabled={!newOccasionName.trim() || addOccasionMutation.isPending}
                  onClick={() => newOccasionName.trim() && addOccasionMutation.mutate(newOccasionName.trim())}
                >{t.saveBtn}</button>
                <button
                  className="text-xs text-gray-400"
                  onClick={() => { setAddingOccasion(false); setNewOccasionName('') }}
                >{t.cancelBtn}</button>
              </div>
            ) : (
              <button
                className="text-xs text-blue-500 hover:text-blue-700"
                onClick={() => setAddingOccasion(true)}
              >{t.occasionAddNew}</button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-4 flex-wrap">
        {person && (() => {
          const langs = [...new Set(
            person.names.filter(n => n.is_current).map(n => n.language)
          )]
          if (langs.length < 2) return null
          return (
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">{t.thumbnailNameLabel}</span>
              <select
                value={card.display_name_language ?? ''}
                onChange={async e => {
                  await updateCard(card.external_id, { display_name_language: e.target.value || null })
                  qc.invalidateQueries({ queryKey: ['card', extId] })
                  qc.invalidateQueries({ queryKey: ['cards'] })
                }}
                className="text-xs border border-gray-200 rounded px-2 py-0.5 text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
              >
                <option value="">{t.thumbnailNameAuto}</option>
                {langs.map(lang => {
                  const name = person.names.find(n => n.is_current && n.language === lang)
                  return <option key={lang} value={lang}>{name?.full_name} ({lang})</option>
                })}
              </select>
            </div>
          )
        })()}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">{t.receivedDateLabel}</span>
          <input
            type="date"
            value={card.received_date ?? ''}
            onChange={async e => {
              await updateCard(card.external_id, { received_date: e.target.value || null })
              qc.invalidateQueries({ queryKey: ['card', extId] })
              showToast(t.savedChanges)
            }}
            className="text-xs border border-gray-200 rounded px-2 py-0.5 text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
        <p className="text-xs text-gray-300">ID: {card.external_id}</p>
        </div>

        <div className="pt-2">
          <button
            className="text-xs text-red-400 hover:text-red-600 disabled:opacity-50"
            disabled={deleteCardMutation.isPending}
            onClick={() => {
              if (window.confirm(t.confirmDeleteCard)) deleteCardMutation.mutate()
            }}
          >
            {t.deleteCardBtn}
          </button>
        </div>
      </section>
    </div>
  )
}
