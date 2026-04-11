/**
 * CardDetailPage — view and edit a saved business card.
 *
 * URL: /cards/:external_id
 * Shows card images (click to enlarge) + editable person data.
 */
import { useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getCard,
  updateCard,
  getPerson,
  addCardSide,
  deleteCardSide,
} from '../api'
import { LightboxImage } from '../components/ImageLightbox'
import { PersonEditor } from '../components/PersonEditor'
import { useToast } from '../components/Toast'
import { useLang } from '../LangContext'
import type { Card, Person } from '../types'

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
              <p className="text-xs text-gray-400 mt-1">
                {side.side_order === 0 ? t.sideLabels[0] : side.side_order === 1 ? t.sideLabels[1] : t.sideN(side.side_order)}
              </p>
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
      <section className="border-t border-gray-100 pt-4 flex items-center gap-4 flex-wrap">
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
      </section>
    </div>
  )
}
