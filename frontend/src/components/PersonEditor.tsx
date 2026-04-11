/**
 * PersonEditor — inline-editable view of a Person record.
 * Used by both CardDetailPage and PersonDetailPage.
 */
import { useState } from 'react'
import {
  updatePersonName,
  addContactDetail,
  updateContactDetail,
  deleteContactDetail,
  updatePositionDetail,
  updateOrgName,
} from '../api'
import { useLang } from '../LangContext'
import { useToast } from './Toast'
import type { Person, PersonName, ContactDetail, PositionDetail, OrgName } from '../types'

// ─── Editable field ───────────────────────────────────────────────────────────

export function EditableField({
  value,
  onSave,
  multiline = false,
  placeholder,
}: {
  value: string
  onSave: (v: string) => Promise<void>
  multiline?: boolean
  placeholder?: string
}) {
  const { t } = useLang()
  const { showToast } = useToast()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)

  const commit = async () => {
    const v = draft.trim()
    if (v === value) { setEditing(false); return }
    setSaving(true)
    try {
      await onSave(v)
      setEditing(false)
      showToast(t.savedChanges)
    } catch {
      showToast(t.saveError, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        {multiline ? (
          <textarea
            className="text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none resize-none"
            rows={2}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape') setEditing(false) }}
            autoFocus
          />
        ) : (
          <input
            className="text-sm border border-blue-400 rounded px-2 py-0.5 focus:outline-none w-48"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
            autoFocus
          />
        )}
        <button
          className="text-xs text-blue-600 font-medium disabled:opacity-50"
          onClick={commit}
          disabled={saving}
        >{t.saveBtn}</button>
        <button className="text-xs text-gray-400" onClick={() => { setDraft(value); setEditing(false) }}>
          {t.cancelBtn}
        </button>
      </span>
    )
  }

  return (
    <button
      className="group inline-flex items-center gap-1 text-left hover:text-blue-600"
      onClick={() => { setDraft(value); setEditing(true) }}
    >
      <span>{value || <span className="text-gray-300 italic">{placeholder ?? t.emptyField}</span>}</span>
      <span className="opacity-0 group-hover:opacity-60 text-xs text-blue-400">✏️</span>
    </button>
  )
}

// ─── Drag-and-drop ────────────────────────────────────────────────────────────

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
  detailId: number
  detail_type: string
  fromSection: 'personal' | 'work'
  fromPosIdx: number
}

export function getPosIdx(label: string | null | undefined): number {
  const m = label?.match(/^_pos:(\d+)$/)
  return m ? parseInt(m[1]) : 0
}
export function posLabel(idx: number): string | undefined {
  return idx === 0 ? undefined : `_pos:${idx}`
}

// ─── Row layout ───────────────────────────────────────────────────────────────

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 py-1 border-b border-gray-100 last:border-0">
      <span className="w-36 shrink-0 text-xs text-gray-400 pt-0.5">{label}</span>
      <div className="flex-1 text-sm text-gray-900">{children}</div>
    </div>
  )
}

// ─── Add-field dropdown ───────────────────────────────────────────────────────

const PERSONAL_TYPES_LIST = [
  'phone_mobile', 'email_personal', 'address_home',
  'social_wechat', 'social_line', 'social_linkedin', 'social_other',
  'relationship', 'personal_title', 'introducer',
] as const
const WORK_TYPES_LIST = ['phone_work', 'phone_fax', 'email_work', 'address_work', 'url_website'] as const

export const PERSONAL_TYPE_SET = new Set(PERSONAL_TYPES_LIST as unknown as string[])
export const WORK_TYPE_SET = new Set(WORK_TYPES_LIST as unknown as string[])

function AddFieldMenu({
  types,
  onAdd,
  labelMap,
}: {
  types: readonly string[]
  onAdd: (type: string) => void
  labelMap: Record<string, string>
}) {
  const { t } = useLang()
  const [open, setOpen] = useState(false)
  return (
    <div className="relative inline-block">
      <button className="text-xs text-blue-500 hover:text-blue-700 py-1" onClick={() => setOpen(o => !o)}>
        {t.addFieldLabel}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-6 z-20 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[160px]">
            {types.map(type => (
              <button
                key={type}
                className="block w-full text-left text-xs px-3 py-1.5 hover:bg-blue-50 text-gray-700"
                onClick={() => { onAdd(type); setOpen(false) }}
              >
                {labelMap[type] ?? type}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Contact section ──────────────────────────────────────────────────────────

function ContactSection({
  label,
  sectionKey,
  posIdx = 0,
  contacts,
  onSave,
  onDelete,
  onMove,
  onAdd,
  labelMap,
}: {
  label: string
  sectionKey: 'personal' | 'work'
  posIdx?: number
  contacts: ContactDetail[]
  onSave: (d: ContactDetail, v: string) => Promise<void>
  onDelete: (d: ContactDetail) => Promise<void>
  onMove: (payload: DragPayload) => Promise<void>
  onAdd: (type: string) => Promise<void>
  labelMap: Record<string, string>
}) {
  const [dropState, setDropState] = useState<'none' | 'valid' | 'invalid'>('none')

  const getPayload = (e: React.DragEvent): DragPayload | null => {
    try { return JSON.parse(e.dataTransfer.getData('application/x-contact-detail')) }
    catch { return null }
  }

  const isValid = (p: DragPayload) => {
    if (p.fromSection === sectionKey && p.fromPosIdx === posIdx) return false
    if (p.fromSection !== sectionKey) {
      return (sectionKey === 'work' ? TO_WORK : TO_PERSONAL)[p.detail_type] !== undefined
    }
    return sectionKey === 'work'
  }

  const dropBorder =
    dropState === 'valid' ? 'border-blue-400 bg-blue-50/40' :
    dropState === 'invalid' ? 'border-red-300' :
    'border-gray-100'

  return (
    <div
      className={`mt-2 rounded border ${dropBorder} bg-gray-50/50 overflow-hidden transition-colors`}
      onDragOver={e => {
        e.preventDefault()
        const p = getPayload(e)
        if (p) setDropState(isValid(p) ? 'valid' : 'invalid')
      }}
      onDragLeave={() => setDropState('none')}
      onDrop={e => {
        e.preventDefault()
        setDropState('none')
        const p = getPayload(e)
        if (p && isValid(p)) onMove(p)
      }}
    >
      <div className="px-3 py-1 bg-gray-100/60 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">{label}</span>
        <AddFieldMenu
          types={sectionKey === 'personal' ? PERSONAL_TYPES_LIST : WORK_TYPES_LIST}
          onAdd={onAdd}
          labelMap={labelMap}
        />
      </div>
      <div className="px-3 min-h-[32px]">
        {contacts.length === 0 && (
          <p className="text-xs text-gray-300 italic py-2 select-none">drag here to move a field</p>
        )}
        {contacts.map(d => (
          <div
            key={d.id}
            className="flex items-center gap-2 py-1 border-b border-gray-100 last:border-0 group cursor-grab active:cursor-grabbing"
            draggable
            onDragStart={e => {
              const payload: DragPayload = {
                detailId: d.id,
                detail_type: d.detail_type,
                fromSection: sectionKey,
                fromPosIdx: posIdx,
              }
              e.dataTransfer.setData('application/x-contact-detail', JSON.stringify(payload))
              e.dataTransfer.effectAllowed = 'move'
            }}
          >
            <span className="text-gray-300 select-none text-xs shrink-0" title="Drag to move">⠿</span>
            <span className="w-28 shrink-0 text-xs text-gray-400">{labelMap[d.detail_type] ?? d.detail_type}</span>
            <div className="flex-1 text-sm">
              <EditableField
                value={d.value}
                onSave={v => onSave(d, v)}
                multiline={d.detail_type.startsWith('address')}
              />
            </div>
            <button
              className="opacity-0 group-hover:opacity-100 text-xs text-red-400 hover:text-red-600 shrink-0"
              onClick={() => onDelete(d)}
            >✕</button>
          </div>
        ))}
      </div>
    </div>
  )
}

function honorificLabel(nameLang: string, fallback: string): string {
  if (nameLang.startsWith('zh')) return '敬稱'
  if (nameLang === 'ja') return '敬称'
  return fallback
}

// ─── Main PersonEditor ────────────────────────────────────────────────────────

export function PersonEditor({
  person,
  onUpdated,
}: {
  person: Person
  onUpdated: () => void
}) {
  const { t } = useLang()
  const { showToast } = useToast()

  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); onUpdated() }
    catch { showToast(t.saveError, 'error') }
  }

  const saveNameField = (name: PersonName, field: 'full_name' | 'family_name' | 'given_name' | 'honorific', value: string) =>
    run(() => updatePersonName(person.external_id, name.id, { [field]: value }))

  const saveContact = (detail: ContactDetail, value: string) =>
    run(() => updateContactDetail(person.external_id, detail.id, { value }))

  const removeContact = (detail: ContactDetail) =>
    run(() => deleteContactDetail(person.external_id, detail.id).then(() => showToast(t.deleteConfirmed)))

  const addContact = (type: string, posIdx: number) =>
    run(() => addContactDetail(person.external_id, {
      detail_type: type,
      value: '',
      label: posLabel(posIdx),
    }))

  const moveContact = (payload: DragPayload, targetSection: 'personal' | 'work', targetPosIdx: number) =>
    run(() => {
      if (payload.fromSection !== targetSection) {
        const map = targetSection === 'work' ? TO_WORK : TO_PERSONAL
        const newType = map[payload.detail_type]
        if (!newType) return Promise.resolve()
        return updateContactDetail(person.external_id, payload.detailId, {
          detail_type: newType,
          label: posLabel(targetPosIdx),
        })
      } else if (targetSection === 'work' && payload.fromPosIdx !== targetPosIdx) {
        return updateContactDetail(person.external_id, payload.detailId, {
          label: posLabel(targetPosIdx),
        })
      }
      return Promise.resolve()
    })

  const saveOrgName = (pos: Person['positions'][0], orgName: OrgName, value: string) =>
    run(() => updateOrgName(person.external_id, pos.id, orgName.id, { name: value }))

  const savePosDetail = (
    pos: Person['positions'][0],
    detail: PositionDetail,
    field: 'title' | 'department',
    value: string,
  ) => run(() => updatePositionDetail(person.external_id, pos.id, detail.id, { [field]: value }))

  const personalContacts = person.contact_details.filter(d => PERSONAL_TYPE_SET.has(d.detail_type))
  const getWorkContactsForPos = (pi: number) =>
    person.contact_details.filter(d => WORK_TYPE_SET.has(d.detail_type) && getPosIdx(d.label) === pi)

  return (
    <div className="space-y-4">
      {/* Names */}
      {person.names.filter(n => n.is_current).map(name => (
        <section key={name.id} className="rounded-lg border border-gray-200 overflow-hidden">
          <div className="bg-gray-50 px-3 py-1.5 flex items-center gap-2">
            <span className="font-medium text-xs text-gray-600">{t.nameSection(1)}</span>
            <span className="text-xs bg-gray-200 text-gray-600 rounded px-1">{name.language}</span>
          </div>
          <div className="px-3 pt-1 pb-2 space-y-1.5">
            <Row label={t.fieldFullName}>
              <EditableField value={name.full_name} onSave={v => saveNameField(name, 'full_name', v)} />
            </Row>
            {name.family_name !== undefined && (
              <Row label={t.fieldFamilyName}>
                <EditableField value={name.family_name ?? ''} onSave={v => saveNameField(name, 'family_name', v)} />
              </Row>
            )}
            {name.given_name !== undefined && (
              <Row label={t.fieldGivenName}>
                <EditableField value={name.given_name ?? ''} onSave={v => saveNameField(name, 'given_name', v)} />
              </Row>
            )}
            <Row label={honorificLabel(name.language, t.fieldHonorific)}>
              <EditableField value={name.honorific ?? ''} onSave={v => saveNameField(name, 'honorific', v)} />
            </Row>
            <ContactSection
              label={t.personalContactsLabel}
              sectionKey="personal"
              posIdx={0}
              contacts={personalContacts}
              onSave={saveContact}
              onDelete={removeContact}
              onMove={p => moveContact(p, 'personal', 0)}
              onAdd={type => addContact(type, 0)}
              labelMap={t.contactLabels}
            />
          </div>
        </section>
      ))}

      {/* Positions / Organizations */}
      {person.positions.map((pos, pi) => (
        <section key={pos.id} className="rounded-lg border border-gray-200 overflow-hidden">
          <div className="bg-gray-50 px-3 py-1.5">
            <span className="font-medium text-xs text-gray-600">{t.orgSection(pi + 1)}</span>
          </div>
          <div className="px-3 pt-1 pb-2 space-y-1.5">
            {pos.org_names.map(on => (
              <Row key={on.id} label={t.fieldCompany(on.language)}>
                <EditableField value={on.name} onSave={v => saveOrgName(pos, on, v)} />
              </Row>
            ))}
            {pos.details.map(d => (
              <span key={d.id}>
                {d.title !== undefined && (
                  <Row label={t.fieldTitle(d.language)}>
                    <EditableField value={d.title ?? ''} onSave={v => savePosDetail(pos, d, 'title', v)} />
                  </Row>
                )}
                {d.department !== undefined && (
                  <Row label={t.fieldDept(d.language)}>
                    <EditableField value={d.department ?? ''} onSave={v => savePosDetail(pos, d, 'department', v)} />
                  </Row>
                )}
              </span>
            ))}
            <ContactSection
              label={t.workContactsLabel}
              sectionKey="work"
              posIdx={pi}
              contacts={getWorkContactsForPos(pi)}
              onSave={saveContact}
              onDelete={removeContact}
              onMove={p => moveContact(p, 'work', pi)}
              onAdd={type => addContact(type, pi)}
              labelMap={t.contactLabels}
            />
          </div>
        </section>
      ))}
    </div>
  )
}
