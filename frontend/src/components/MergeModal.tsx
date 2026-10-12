/**
 * MergeModal — 2-step modal for merging Person records.
 *
 * Step 1: user picks which person is the primary (surviving) record.
 * Step 2: confirmation before destructive merge.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getPerson, mergePersons } from '../api'
import { useToast } from './Toast'
import { useLang } from '../LangContext'
import type { Person, PersonListItem } from '../types'

interface Props {
  selected: PersonListItem[]
  onClose: () => void
}

export function MergeModal({ selected, onClose }: Props) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()
  const [step, setStep] = useState<1 | 2>(1)
  const [primaryId, setPrimaryId] = useState<string>(selected[0]?.external_id ?? '')

  const primary = selected.find(p => p.external_id === primaryId)
  const sources = selected.filter(p => p.external_id !== primaryId)

  const mutation = useMutation({
    mutationFn: () =>
      mergePersons(primaryId, sources.map(p => p.external_id)),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['persons'] })
      qc.invalidateQueries({ queryKey: ['cards'] })
      const msg = result.duplicate_contact_count > 0
        ? t.mergeDuplicatesFound(result.duplicate_contact_count)
        : t.mergeSucceeded
      showToast(msg)
      window.location.href = `/persons/${result.person.external_id}`
    },
    onError: () => {
      showToast(t.mergeError, 'error')
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative bg-white rounded-t-2xl sm:rounded-2xl w-full sm:max-w-md mx-0 sm:mx-4 p-6 space-y-5 shadow-xl">
        {step === 1 ? (
          <>
            <h2 className="text-lg font-semibold text-gray-900">{t.mergeModalTitle}</h2>
            <div className="space-y-2">
              {selected.map(p => (
                <label
                  key={p.external_id}
                  className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${
                    primaryId === p.external_id
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="primary"
                    value={p.external_id}
                    checked={primaryId === p.external_id}
                    onChange={() => setPrimaryId(p.external_id)}
                    className="accent-blue-600"
                  />
                  <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-medium shrink-0">
                    {(p.primary_name ?? '?').charAt(0)}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-gray-900">
                      {p.primary_name ?? '(No name)'}
                    </div>
                    <PersonSummary extId={p.external_id} />
                  </div>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-3">
              <button onClick={onClose} className="btn-secondary text-sm">{t.cancelBtn}</button>
              <button
                onClick={() => setStep(2)}
                disabled={!primaryId}
                className="btn-primary text-sm"
              >
                Next →
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold text-gray-900">
              {t.mergeModalConfirmTitle(primary?.primary_name ?? '')}
            </h2>
            <p className="text-sm text-gray-600">
              {t.mergeModalConfirmBody(sources.length)}
            </p>
            <div className="flex justify-between items-center">
              <button onClick={() => setStep(1)} className="text-sm text-blue-500 hover:text-blue-700">
                ← Back
              </button>
              <button
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending}
                className="btn-primary text-sm bg-red-600 hover:bg-red-700 disabled:opacity-50"
              >
                {mutation.isPending ? t.mergingBtn : t.mergeConfirmBtn}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * All current names and organisations of one person, so records that share a
 * primary name can be told apart when picking the primary.
 *
 * PersonListItem only carries primary_name, so each row loads the full person.
 * The ['person', extId] key and getPerson fetcher match PersonDetailPage and
 * DuplicateFieldEditor, so the cache is shared with them.
 */
function PersonSummary({ extId }: { extId: string }) {
  const { t } = useLang()
  const { data: person, isLoading, isError } = useQuery<Person>({
    queryKey: ['person', extId],
    queryFn: () => getPerson(extId),
  })

  if (isLoading) return <div className="text-xs text-gray-400">{t.loading}</div>
  if (isError || !person) return <div className="text-xs text-red-500">{t.loadError}</div>

  const names = currentNames(person)
  const orgs = currentOrgs(person)
  return (
    <div className="text-xs text-gray-500 space-y-0.5 break-words">
      {names.length > 0 && <div>{names.join(' / ')}</div>}
      {/* One line per organisation; its language variants joined on that line */}
      {orgs.map((org, i) => <div key={i}>{org}</div>)}
    </div>
  )
}

/** Current full names, deduplicated, in API order (all names if none is current). */
function currentNames(person: Person): string[] {
  const current = person.names.filter(n => n.is_current)
  const pool = current.length > 0 ? current : person.names
  return [...new Set(pool.map(n => n.full_name.trim()).filter(Boolean))]
}

/**
 * One entry per position: its current org names (all if none is current) joined
 * with " / ". Duplicate entries — the same organisation held twice — are dropped.
 */
function currentOrgs(person: Person): string[] {
  const lines = person.positions.map(pos => {
    const current = pos.org_names.filter(o => o.is_current)
    const pool = current.length > 0 ? current : pos.org_names
    return [...new Set(pool.map(o => o.name.trim()).filter(Boolean))].join(' / ')
  })
  return [...new Set(lines.filter(Boolean))]
}
