/**
 * DuplicateFieldEditor
 *
 * Inline two-column field editor shown when a scanned card matches an
 * existing person (matchConfidence >= 0.55).
 *
 * Left column  — existing person record (converted to ParsedCard shape)
 * Right column — new card from Claude OCR
 *
 * Drag a field from right → left to copy it into the existing record.
 * ✕ on either side removes the field from that column.
 * Green highlight on right-column fields that are new or different from left.
 *
 * Callbacks:
 *   onNotDuplicate()   — dismiss panel, save as new person
 *   onDiscard()        — remove this card from the session entirely
 *   onMerge(merged)    — save: merged ParsedCard is used to update existing person
 */
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPerson } from '../api'
import { useLang } from '../LangContext'
import type {
  ParsedCard,
  ParsedContactDetail,
  Person,
} from '../types'

// ---------------------------------------------------------------------------
// Conversion: Person (DB shape) → ParsedCard (editor shape)
// All confidence values are set to 1.0 for existing data.
// Person.contact_details[].value is a plain string; Person.positions[].org_names[].name
// and positions[].details[].title/department are also plain strings — wrap them as CF.
// ---------------------------------------------------------------------------

function personToParsedCard(person: Person): ParsedCard {
  const names = person.names
    .filter(n => n.is_current)
    .map(n => ({
      language: n.language,
      name_type: n.name_type,
      family_name: n.family_name ? { value: n.family_name, confidence: 1.0 } : undefined,
      given_name: n.given_name ? { value: n.given_name, confidence: 1.0 } : undefined,
      full_name: { value: n.full_name, confidence: 1.0 },
    }))

  const positions = person.positions
    .filter(p => p.status === 'current')
    .map(pos => ({
      org_names: pos.org_names
        .filter(on => on.is_current)
        .map(on => ({ language: on.language, name: { value: on.name, confidence: 1.0 } })),
      details: pos.details.map(pd => ({
        language: pd.language,
        title: pd.title ? { value: pd.title, confidence: 1.0 } : undefined,
        department: pd.department ? { value: pd.department, confidence: 1.0 } : undefined,
      })),
    }))

  // ContactDetail.value is a plain string in the DB shape; wrap it as CF
  const contact_details: ParsedContactDetail[] = person.contact_details.map(cd => ({
    detail_type: cd.detail_type,
    value: { value: cd.value, confidence: 1.0 },
    label: cd.label,
  }))

  return {
    names,
    positions,
    contact_details,
    languages_detected: [...new Set(names.map(n => n.language))],
    overall_confidence: 1.0,
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fieldKey(cd: ParsedContactDetail): string {
  return `${cd.detail_type}:${cd.value.value}`
}

function isNewOrDifferent(
  right: ParsedContactDetail,
  leftDetails: ParsedContactDetail[],
): boolean {
  const sameType = leftDetails.filter(l => l.detail_type === right.detail_type)
  if (sameType.length === 0) return true  // type doesn't exist on left
  return !sameType.some(l => l.value.value === right.value.value)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ContactDetailRow({
  cd,
  highlight,
  onDelete,
  draggable,
  onDragStart,
}: {
  cd: ParsedContactDetail
  highlight: boolean
  onDelete: () => void
  draggable?: boolean
  onDragStart?: (e: React.DragEvent) => void
}) {
  return (
    <div
      className={`flex items-center gap-2 px-2 py-1 rounded text-xs group ${highlight ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'}`}
      draggable={draggable}
      onDragStart={onDragStart}
    >
      {draggable && (
        <span className="cursor-grab text-gray-400 select-none" title="Drag to apply">⠿</span>
      )}
      <span className="text-gray-400 shrink-0 w-24 truncate">{cd.detail_type.replace(/_/g, ' ')}</span>
      <span className={`flex-1 font-medium truncate ${highlight ? 'text-green-700' : 'text-gray-800'}`}>
        {cd.value.value}
      </span>
      <button
        onClick={onDelete}
        className="ml-auto text-gray-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Remove field"
      >✕</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DuplicateFieldEditor({
  personExtId,
  newCard,
  matchName,
  matchConfidence,
  onNotDuplicate,
  onDiscard,
  onMerge,
}: {
  personExtId: string
  newCard: ParsedCard
  matchName?: string
  matchConfidence?: number
  onNotDuplicate: () => void
  onDiscard: () => void
  onMerge: (mergedCard: ParsedCard) => void
}) {
  const { t } = useLang()

  const { data: person, isLoading } = useQuery<Person>({
    queryKey: ['person', personExtId],
    queryFn: () => getPerson(personExtId),
  })

  // Left column state — starts as the existing person's data
  const [leftCard, setLeftCard] = useState<ParsedCard | null>(null)
  useEffect(() => {
    if (person && !leftCard) {
      setLeftCard(personToParsedCard(person))
    }
  }, [person, leftCard])

  const [isDragOver, setIsDragOver] = useState(false)
  const [rightDeleted, setRightDeleted] = useState<Set<string>>(new Set())

  if (isLoading || !leftCard) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-600">
        Loading existing contact…
      </div>
    )
  }

  const handleDropFromRight = (cd: ParsedContactDetail) => {
    setLeftCard(prev => {
      if (!prev) return prev
      // Remove any existing entry with the same type+value (dedup), then append
      const filtered = prev.contact_details.filter(l => fieldKey(l) !== fieldKey(cd))
      return { ...prev, contact_details: [...filtered, cd] }
    })
  }

  const deleteLeft = (cd: ParsedContactDetail) => {
    setLeftCard(prev => {
      if (!prev) return prev
      return { ...prev, contact_details: prev.contact_details.filter(l => fieldKey(l) !== fieldKey(cd)) }
    })
  }

  const deleteRight = (cd: ParsedContactDetail) => {
    setRightDeleted(prev => new Set(prev).add(fieldKey(cd)))
  }

  const rightDetails = newCard.contact_details.filter(cd => !rightDeleted.has(fieldKey(cd)))

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-amber-100 border-b border-amber-200">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-amber-800">
            {t.dupPanelTitle}
          </span>
          <span className="text-xs text-amber-600">
            {matchName} ({Math.round((matchConfidence ?? 0) * 100)}%)
          </span>
        </div>
        <button
          onClick={onNotDuplicate}
          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
        >
          {t.dupNotDuplicate}
        </button>
      </div>

      {/* Two-column editor */}
      <div className="grid grid-cols-2 gap-0 divide-x divide-amber-200">
        {/* Left: existing person */}
        <div
          className={`p-3 space-y-1 min-h-[120px] transition-colors ${isDragOver ? 'bg-green-50' : ''}`}
          onDragOver={e => { e.preventDefault(); setIsDragOver(true) }}
          onDragLeave={e => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragOver(false)
          }}
          onDrop={e => {
            e.preventDefault()
            setIsDragOver(false)
            const raw = e.dataTransfer.getData('cd')
            if (raw) {
              try { handleDropFromRight(JSON.parse(raw)) } catch { /* ignore */ }
            }
          }}
        >
          <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
            {t.dupExisting}
          </p>
          {/* Names (display-only) */}
          {leftCard.names.slice(0, 1).map((n, i) => (
            <div key={i} className="text-sm font-medium text-gray-800 mb-1">{n.full_name.value}</div>
          ))}
          {/* Contact details */}
          {leftCard.contact_details.map((cd, i) => (
            <ContactDetailRow
              key={`${fieldKey(cd)}-${i}`}
              cd={cd}
              highlight={false}
              onDelete={() => deleteLeft(cd)}
            />
          ))}
          {isDragOver && (
            <div className="border-2 border-dashed border-green-400 rounded px-2 py-1 text-xs text-green-600 text-center">
              Drop here
            </div>
          )}
        </div>

        {/* Right: new card */}
        <div className="p-3 space-y-1">
          <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
            {t.dupNewCard}
          </p>
          {/* Names */}
          {newCard.names.slice(0, 1).map((n, i) => (
            <div key={i} className="text-sm font-medium text-gray-800 mb-1">{n.full_name.value}</div>
          ))}
          {/* Contact details */}
          {rightDetails.map((cd, i) => {
            const highlight = isNewOrDifferent(cd, leftCard.contact_details)
            return (
              <ContactDetailRow
                key={`${fieldKey(cd)}-${i}`}
                cd={cd}
                highlight={highlight}
                onDelete={() => deleteRight(cd)}
                draggable
                onDragStart={e => {
                  e.dataTransfer.setData('cd', JSON.stringify(cd))
                  e.dataTransfer.effectAllowed = 'copy'
                }}
              />
            )
          })}
          {rightDetails.length === 0 && (
            <p className="text-xs text-gray-400 italic">No additional fields</p>
          )}
          <p className="text-xs text-gray-400 mt-2">{t.dupDragHint}</p>
        </div>
      </div>

      {/* Footer actions */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 bg-amber-50 border-t border-amber-200">
        <button
          onClick={onDiscard}
          className="text-xs text-red-500 hover:text-red-700"
        >
          {t.dupDiscard}
        </button>
        <button
          onClick={() => onMerge(leftCard)}
          className="btn-primary text-sm px-4 py-1.5"
        >
          {t.dupConfirmMerge}
        </button>
      </div>
    </div>
  )
}
