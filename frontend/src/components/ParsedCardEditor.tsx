/**
 * Review + edit a ParsedCard after Claude analysis.
 *
 * Layout:
 *   Personal N  ──  full/family/given + personal contacts (mobile, personal email, home, social, relationship)
 *   Organization N  ──  company/title/dept + work contacts (work phone, fax, work email, work address, website)
 *
 * Low-confidence fields (<0.7) are highlighted in yellow.
 * Users can add, edit, delete, and drag-and-drop contact fields between sections.
 */
import { useState } from 'react'
import { ConfidenceBadge } from './ConfidenceBadge'
import { useLang } from '../LangContext'
import type { ParsedCard, ParsedContactDetail, ParsedName, ParsedPosition } from '../types'

// ─── Constants ────────────────────────────────────────────────────────────────

const PERSONAL_TYPES = [
  'phone_mobile', 'email_personal', 'address_home',
  'social_wechat', 'social_line', 'social_linkedin', 'social_other',
  'relationship', 'personal_title', 'introducer',
] as const

const WORK_TYPES = [
  'phone_work', 'phone_fax', 'email_work', 'address_work', 'url_website', 'gui_number',
] as const

const LANG_OPTIONS = ['zh-TW', 'zh-CN', 'en', 'ja', 'ko'] as const

// Type conversion when dragging between sections
const TO_WORK: Record<string, string> = {
  phone_mobile: 'phone_work',
  email_personal: 'email_work',
  address_home: 'address_work',
}
const TO_PERSONAL: Record<string, string> = {
  phone_work: 'phone_mobile',
  email_work: 'email_personal',
  address_work: 'address_home',
}

interface DragPayload {
  index: number
  detail_type: string
  fromSection: 'personal' | 'work'
  fromPosIdx: number   // org index for work contacts, 0 for personal
}

// Work contacts use label "_pos:N" to track which org they belong to.
// No label / null = position 0 (first org).
function getPosIdx(label: string | null | undefined): number {
  const m = label?.match(/^_pos:(\d+)$/)
  return m ? parseInt(m[1]) : 0
}
function posLabel(idx: number): string | undefined {
  return idx === 0 ? undefined : `_pos:${idx}`
}

// ─── Field row ────────────────────────────────────────────────────────────────

function FieldRow({
  label,
  value,
  confidence,
  onEdit,
  onDelete,
  multiline = false,
  draggable = false,
  onDragStart,
  onCommit,
}: {
  label: string
  value: string
  confidence: number
  onEdit: (v: string) => void
  onDelete?: () => void
  multiline?: boolean
  draggable?: boolean
  onDragStart?: (e: React.DragEvent) => void
  onCommit?: (oldValue: string, newValue: string) => void
}) {
  const { t } = useLang()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const low = confidence < 0.7

  const commit = () => {
    if (draft !== value) onCommit?.(value, draft)
    onEdit(draft)
    setEditing(false)
  }

  return (
    <div
      className={`flex items-start gap-2 py-1.5 border-b border-gray-100 last:border-0 ${low ? 'bg-yellow-50' : ''} ${draggable ? 'cursor-grab active:cursor-grabbing' : ''}`}
      draggable={draggable}
      onDragStart={onDragStart}
    >
      <span className="w-36 shrink-0 text-xs text-gray-400 pt-1">
        {draggable && <span className="mr-1 text-gray-300 select-none">⠿</span>}
        {label}
      </span>
      {editing ? (
        <div className="flex gap-1 flex-1">
          {multiline ? (
            <textarea
              className="flex-1 text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
              rows={3}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              autoFocus
            />
          ) : (
            <input
              className="flex-1 text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
              autoFocus
            />
          )}
          <button className="text-xs text-blue-600 font-medium shrink-0" onClick={commit}>{t.saveBtn}</button>
          <button className="text-xs text-gray-400 shrink-0" onClick={() => setEditing(false)}>{t.cancelBtn}</button>
        </div>
      ) : (
        <div className="flex flex-1 items-center gap-1 group min-w-0">
          <button
            className="flex-1 text-left text-sm text-gray-900 hover:text-blue-600 flex items-center gap-2 min-w-0"
            onClick={() => { setDraft(value); setEditing(true) }}
          >
            <span className="flex-1 truncate">{value || <span className="text-gray-300 italic">{t.emptyField}</span>}</span>
            <ConfidenceBadge confidence={confidence} />
            <span className="opacity-0 group-hover:opacity-100 text-xs text-blue-400 shrink-0">✏️</span>
          </button>
          {onDelete && (
            <button
              className="text-xs text-red-300 hover:text-red-600 shrink-0 ml-1"
              onClick={onDelete}
              title={t.removeLabel}
            >✕</button>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Add-field dropdown ───────────────────────────────────────────────────────

function AddFieldMenu({
  types,
  onAdd,
}: {
  types: readonly string[]
  onAdd: (type: string) => void
}) {
  const { t } = useLang()
  const [open, setOpen] = useState(false)

  return (
    <div className="relative inline-block">
      <button
        className="text-xs text-blue-500 hover:text-blue-700 py-1"
        onClick={() => setOpen(o => !o)}
      >
        {t.addFieldLabel}
      </button>
      {open && (
        <div className="absolute right-0 top-6 z-20 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[160px] max-h-64 overflow-y-auto">
          {types.map(type => (
            <button
              key={type}
              className="block w-full text-left text-xs px-3 py-1.5 hover:bg-blue-50 text-gray-700"
              onClick={() => { onAdd(type); setOpen(false) }}
            >
              {t.contactLabels[type as keyof typeof t.contactLabels] ?? type}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Contact sub-section ─────────────────────────────────────────────────────

function ContactSubSection({
  label,
  sectionKey,
  posIdx = 0,
  contacts,
  availableTypes,
  onEdit,
  onDelete,
  onAdd,
  onMoveHere,
  onEditCommit,
}: {
  label: string
  sectionKey: 'personal' | 'work'
  posIdx?: number
  contacts: { index: number; detail: ParsedContactDetail }[]
  availableTypes: readonly string[]
  onEdit: (index: number, value: string) => void
  onDelete: (index: number) => void
  onAdd: (type: string) => void
  onMoveHere: (payload: DragPayload) => void
  onEditCommit?: (index: number, oldValue: string, newValue: string) => void
}) {
  const { t } = useLang()
  const [dropState, setDropState] = useState<'none' | 'valid' | 'invalid'>('none')

  const getPayload = (e: React.DragEvent): DragPayload | null => {
    try { return JSON.parse(e.dataTransfer.getData('application/x-contact')) }
    catch { return null }
  }

  const isValid = (payload: DragPayload) => {
    // Same section AND same org → no-op
    if (payload.fromSection === sectionKey && payload.fromPosIdx === posIdx) return false
    // Cross-section → need type conversion
    if (payload.fromSection !== sectionKey) {
      const map = sectionKey === 'work' ? TO_WORK : TO_PERSONAL
      return payload.detail_type in map
    }
    // Same work section, different org → always valid
    return sectionKey === 'work'
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    const payload = getPayload(e)
    if (!payload) return
    setDropState(isValid(payload) ? 'valid' : 'invalid')
    e.dataTransfer.dropEffect = isValid(payload) ? 'move' : 'none'
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDropState('none')
    const payload = getPayload(e)
    if (!payload || !isValid(payload)) return
    onMoveHere(payload)
  }

  // Always render — even empty sections act as drop targets

  const dropBorder =
    dropState === 'valid' ? 'border-blue-400 bg-blue-50/40' :
    dropState === 'invalid' ? 'border-red-300' :
    'border-gray-100'

  return (
    <div
      className={`mt-2 rounded border ${dropBorder} bg-gray-50/50 transition-colors`}
      onDragOver={handleDragOver}
      onDragLeave={() => setDropState('none')}
      onDrop={handleDrop}
    >
      <div className="px-3 py-1 bg-gray-100/60 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">{label}</span>
        <AddFieldMenu types={availableTypes} onAdd={onAdd} />
      </div>
      <div className="px-3 min-h-[32px]">
        {contacts.length === 0 && (
          <p className="text-xs text-gray-300 italic py-2 select-none">drag here</p>
        )}
        {contacts.map(({ index, detail }) => (
          <FieldRow
            key={index}
            label={t.contactLabels[detail.detail_type as keyof typeof t.contactLabels] ?? detail.detail_type}
            value={detail.value.value}
            confidence={detail.value.confidence}
            multiline={detail.detail_type.startsWith('address')}
            onEdit={v => onEdit(index, v)}
            onDelete={() => onDelete(index)}
            onCommit={(old, nv) => onEditCommit?.(index, old, nv)}
            draggable
            onDragStart={e => {
              const payload: DragPayload = { index, detail_type: detail.detail_type, fromSection: sectionKey, fromPosIdx: posIdx }
              e.dataTransfer.setData('application/x-contact', JSON.stringify(payload))
              e.dataTransfer.effectAllowed = 'move'
            }}
          />
        ))}
        {contacts.length === 0 && (
          <p className="text-xs text-gray-400 italic py-2">{t.emptyField}</p>
        )}
      </div>
    </div>
  )
}

// ─── Main editor ─────────────────────────────────────────────────────────────

export interface CorrectionPayload {
  field_path: string
  claude_value: string | null
  user_value: string
  correction_type: string
}

interface Props {
  parsed: ParsedCard
  onChange: (updated: ParsedCard) => void
  onCorrection?: (c: CorrectionPayload) => void
}

export function ParsedCardEditor({ parsed, onChange, onCorrection }: Props) {
  const { t } = useLang()

  // ── helpers ──

  const setContact = (i: number, value: string) =>
    onChange({
      ...parsed,
      contact_details: parsed.contact_details.map((cd, idx) =>
        idx === i ? { ...cd, value: { ...cd.value, value } } : cd,
      ),
    })

  const deleteContact = (i: number) =>
    onChange({ ...parsed, contact_details: parsed.contact_details.filter((_, idx) => idx !== i) })

  const addContact = (type: string) =>
    onChange({
      ...parsed,
      contact_details: [
        ...parsed.contact_details,
        { detail_type: type, value: { value: '', confidence: 1.0 }, label: undefined },
      ],
    })

  const moveContact = (payload: DragPayload, targetSection: 'personal' | 'work', targetPosIdx: number) => {
    if (payload.fromSection !== targetSection) {
      // Cross-section: change detail_type
      const map = targetSection === 'work' ? TO_WORK : TO_PERSONAL
      const newType = map[payload.detail_type]
      if (!newType) return
      onChange({
        ...parsed,
        contact_details: parsed.contact_details.map((cd, idx) =>
          idx === payload.index ? { ...cd, detail_type: newType, label: posLabel(targetPosIdx) } : cd,
        ),
      })
    } else if (targetSection === 'work' && payload.fromPosIdx !== targetPosIdx) {
      // Same work section, different org: change label only
      onChange({
        ...parsed,
        contact_details: parsed.contact_details.map((cd, idx) =>
          idx === payload.index ? { ...cd, label: posLabel(targetPosIdx) } : cd,
        ),
      })
    }
  }

  // Work contacts partitioned by org index
  const getWorkContactsForPos = (pi: number) =>
    parsed.contact_details
      .map((d, i) => ({ index: i, detail: d }))
      .filter(({ detail }) =>
        WORK_TYPES.includes(detail.detail_type as any) && getPosIdx(detail.label) === pi,
      )

  const setNameField = (
    i: number,
    field: 'full_name' | 'family_name' | 'given_name',
    value: string,
  ) =>
    onChange({
      ...parsed,
      names: parsed.names.map((n, idx) => {
        if (idx !== i) return n
        if (field === 'full_name') return { ...n, full_name: { ...n.full_name, value } }
        if (field === 'family_name') return { ...n, family_name: { value, confidence: 1.0 } }
        if (field === 'given_name') return { ...n, given_name: { value, confidence: 1.0 } }
        return n
      }),
    })

  const deleteName = (i: number) =>
    onChange({ ...parsed, names: parsed.names.filter((_, idx) => idx !== i) })

  const addName = () =>
    onChange({
      ...parsed,
      names: [
        ...parsed.names,
        { language: 'en', name_type: 'primary', full_name: { value: '', confidence: 1.0 } },
      ] as ParsedName[],
    })

  const setOrgName = (pi: number, oi: number, value: string) =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        return {
          ...p,
          org_names: p.org_names.map((on, oidx) =>
            oidx === oi ? { ...on, name: { ...on.name, value } } : on,
          ),
        }
      }),
    })

  const setOrgNameLanguage = (pi: number, oi: number, lang: string) =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        return {
          ...p,
          org_names: p.org_names.map((on, oidx) =>
            oidx === oi ? { ...on, language: lang } : on,
          ),
        }
      }),
    })

  const deleteOrgName = (pi: number, oi: number) =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        return {
          ...p,
          org_names: p.org_names.filter((_, oidx) => oidx !== oi),
        }
      }),
    })

  const setDetail = (pi: number, di: number, field: 'title' | 'department', value: string) =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        return {
          ...p,
          details: p.details.map((d, didx) => {
            if (didx !== di) return d
            return { ...d, [field]: { value, confidence: 1.0 } }
          }),
        }
      }),
    })

  const deleteDetailField = (pi: number, di: number, field: 'title' | 'department') =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        const updated = p.details.map((d, didx) => {
          if (didx !== di) return d
          const next = { ...d, [field]: undefined }
          return next
        })
        // Remove detail entries that have neither title nor department
        return { ...p, details: updated.filter(d => d.title || d.department) }
      }),
    })

  const addDetail = (pi: number) =>
    onChange({
      ...parsed,
      positions: parsed.positions.map((p, pidx) => {
        if (pidx !== pi) return p
        return {
          ...p,
          details: [...p.details, { language: 'en', title: { value: '', confidence: 1.0 } }],
        }
      }),
    })

  const deleteOrg = (pi: number) =>
    onChange({ ...parsed, positions: parsed.positions.filter((_, idx) => idx !== pi) })

  const addOrg = () =>
    onChange({
      ...parsed,
      positions: [
        ...parsed.positions,
        {
          org_names: [{ language: 'en', name: { value: '', confidence: 1.0 } }],
          details: [],
        },
      ] as ParsedPosition[],
    })

  // ── contact partitioning ──
  const personalContacts = parsed.contact_details
    .map((d, i) => ({ index: i, detail: d }))
    .filter(({ detail }) => PERSONAL_TYPES.includes(detail.detail_type as any))

  // ── render ──
  return (
    <div className="space-y-3 text-sm">

      {/* Personal sections (formerly "Names") */}
      {parsed.names.map((n, i) => (
        <section key={i} className="rounded-lg border border-gray-200">
          <div className="bg-gray-50 px-3 py-1.5 flex items-center gap-2">
            <span className="font-medium text-xs text-gray-600">{t.nameSection(i + 1)}</span>
            <span className="text-xs bg-gray-200 text-gray-600 rounded px-1">{n.language}</span>
            <span className="text-xs text-gray-400">{n.name_type}</span>
            {parsed.names.length > 1 && (
              <button className="ml-auto text-xs text-red-400 hover:text-red-600" onClick={() => deleteName(i)}>
                {t.removeLabel}
              </button>
            )}
          </div>
          <div className="px-3 pt-1">
            <FieldRow label={t.fieldFullName} value={n.full_name.value} confidence={n.full_name.confidence} onEdit={v => setNameField(i, 'full_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].full_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />
            {n.family_name && <FieldRow label={t.fieldFamilyName} value={n.family_name.value} confidence={n.family_name.confidence} onEdit={v => setNameField(i, 'family_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].family_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />}
            {n.given_name && <FieldRow label={t.fieldGivenName} value={n.given_name.value} confidence={n.given_name.confidence} onEdit={v => setNameField(i, 'given_name', v)} onCommit={(old, nv) => onCorrection?.({ field_path: `names[${i}].given_name`, claude_value: old, user_value: nv, correction_type: 'field_value' })} />}

            {/* Personal contacts — only under first personal section */}
            {i === 0 && (
              <ContactSubSection
                label={t.personalContactsLabel}
                sectionKey="personal"
                contacts={personalContacts}
                availableTypes={PERSONAL_TYPES}
                onEdit={setContact}
                onDelete={deleteContact}
                onAdd={addContact}
                onMoveHere={payload => moveContact(payload, 'personal', 0)}
                onEditCommit={(idx, old, nv) => onCorrection?.({ field_path: `contact_details[${idx}].value`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
              />
            )}
          </div>
        </section>
      ))}

      <button className="text-xs text-blue-500 hover:text-blue-700 pl-1" onClick={addName}>
        {t.addNameLabel}
      </button>

      {/* Organizations */}
      {parsed.positions.map((pos, pi) => (
        <section key={pi} className="rounded-lg border border-gray-200">
          <div className="bg-gray-50 px-3 py-1.5 flex items-center gap-2">
            <span className="font-medium text-xs text-gray-600">{t.orgSection(pi + 1)}</span>
            {parsed.positions.length > 1 && (
              <button className="ml-auto text-xs text-red-400 hover:text-red-600" onClick={() => deleteOrg(pi)}>
                {t.removeLabel}
              </button>
            )}
          </div>
          <div className="px-3 pt-1">
            {pos.org_names.map((on, oi) => (
              <div key={oi} className="flex items-center gap-1">
                <div className="flex-1 min-w-0">
                  <FieldRow
                    label={t.fieldCompany(on.language)}
                    value={on.name.value}
                    confidence={on.name.confidence}
                    onEdit={v => setOrgName(pi, oi, v)}
                    onCommit={(old, nv) => onCorrection?.({ field_path: `positions[${pi}].org_names[${oi}].name`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
                  />
                </div>
                <select
                  value={on.language}
                  onChange={e => setOrgNameLanguage(pi, oi, e.target.value)}
                  className="text-xs border border-gray-200 rounded px-1 py-0.5 bg-white text-gray-500 shrink-0"
                  title="Language"
                >
                  {LANG_OPTIONS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
                {pos.org_names.length > 1 && (
                  <button
                    className="text-xs text-red-400 hover:text-red-600 shrink-0"
                    onClick={() => deleteOrgName(pi, oi)}
                    title={t.removeLabel}
                  >✕</button>
                )}
              </div>
            ))}
            {pos.details.map((d, di) => (
              <span key={di}>
                {d.title && (
                  <FieldRow
                    label={t.fieldTitle(d.language)}
                    value={d.title.value}
                    confidence={d.title.confidence}
                    onEdit={v => setDetail(pi, di, 'title', v)}
                    onDelete={() => deleteDetailField(pi, di, 'title')}
                    onCommit={(old, nv) => onCorrection?.({ field_path: `positions[${pi}].details[${di}].title`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
                  />
                )}
                {d.department && (
                  <FieldRow
                    label={t.fieldDept(d.language)}
                    value={d.department.value}
                    confidence={d.department.confidence}
                    onEdit={v => setDetail(pi, di, 'department', v)}
                    onDelete={() => deleteDetailField(pi, di, 'department')}
                    onCommit={(old, nv) => onCorrection?.({ field_path: `positions[${pi}].details[${di}].department`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
                  />
                )}
              </span>
            ))}
            <button className="text-xs text-blue-500 hover:text-blue-700 py-1" onClick={() => addDetail(pi)}>
              {t.addTitleLabel}
            </button>

            {/* Work contacts — one section per org */}
            <ContactSubSection
              label={t.workContactsLabel}
              sectionKey="work"
              posIdx={pi}
              contacts={getWorkContactsForPos(pi)}
              availableTypes={WORK_TYPES}
              onEdit={setContact}
              onDelete={deleteContact}
              onAdd={type => {
                // new contact assigned to this org
                onChange({
                  ...parsed,
                  contact_details: [
                    ...parsed.contact_details,
                    { detail_type: type, value: { value: '', confidence: 1.0 }, label: posLabel(pi) },
                  ],
                })
              }}
              onMoveHere={payload => moveContact(payload, 'work', pi)}
              onEditCommit={(idx, old, nv) => onCorrection?.({ field_path: `contact_details[${idx}].value`, claude_value: old, user_value: nv, correction_type: 'field_value' })}
            />
          </div>
        </section>
      ))}

      <button className="text-xs text-blue-500 hover:text-blue-700 pl-1" onClick={addOrg}>
        {t.addOrgLabel}
      </button>
    </div>
  )
}
