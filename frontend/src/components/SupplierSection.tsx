/**
 * SupplierSection — the supplier side of the Relationship panel.
 *
 * Potential, inactive and none are set directly (PUT roles). Active needs a supplier code, so
 * "Activate supplier" opens a form, "Review" asks what would happen (creates nothing), and
 * "Approve and issue" does it: code from the Odoo sequence, Odoo company, Drive folder.
 * A run that stops part-way can be retried; nothing is drawn twice.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { commitSupplier, proposeSupplier, retryIssueRun, setRoles } from '../api'
import { useLang } from '../LangContext'
import { useToast } from './Toast'
import { Field, FolderLink, StatusSelect, type StatusValue } from './partyShared'
import { btnGhost, btnPrimary, errorText, inputCls } from './partyUi'
import type {
  IssueRun, OrgInfo, SupplierCategoryKey, SupplierProposal, SupplierRequest,
} from '../types'

const CATEGORY_KEYS: SupplierCategoryKey[] = [
  'parts', 'administration', 'construction', 'forwarding', 'inspection', 'marketing', 'packaging', 'other',
]
const COUNTRIES = ['TW', 'JP', ''] as const   // '' = any other country (tax ID kept as typed)

export function SupplierSection({ org, orgExtId, orgName, cardExtId, cardTaxId, onChanged }: {
  org: OrgInfo
  orgExtId: string
  orgName: string
  cardExtId?: string
  cardTaxId?: string
  onChanged: () => void
}) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()

  const status: StatusValue = org.supplier_status ?? ''
  const hasCode = Boolean(org.supplier_code)

  const [formOpen, setFormOpen] = useState(false)
  const [category, setCategory] = useState<SupplierCategoryKey | ''>(org.supplier_category ?? '')
  const [country, setCountry] = useState<string>('TW')
  const [taxId, setTaxId] = useState((cardTaxId ?? '').replace(/\D/g, ''))
  const [phone, setPhone] = useState('')
  const [includePerson, setIncludePerson] = useState(true)

  const [proposal, setProposal] = useState<SupplierProposal | null>(null)
  const [choice, setChoice] = useState('')                 // candidate org_ext_id, or 'new'
  const [run, setRun] = useState<IssueRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Any edit invalidates the review the user has already seen
  const edited = () => { setProposal(null); setRun(null) }

  const closeForm = () => {
    setFormOpen(false); setProposal(null); setRun(null); setChoice(''); setError('')
  }

  const changeStatus = async (next: StatusValue) => {
    try {
      await setRoles(orgExtId, { supplier_status: next === '' ? 'none' : next })
      onChanged()
      if (next === '') closeForm()
    } catch (err) {
      showToast(errorText(err), 'error')
    }
  }

  const changeCategory = async (next: SupplierCategoryKey) => {
    try {
      await setRoles(orgExtId, { supplier_category: next })
      setCategory(next)
      onChanged()
    } catch (err) {
      showToast(errorText(err), 'error')
    }
  }

  const request = (): SupplierRequest => ({
    org_ext_id: orgExtId,
    supplier_name: orgName,
    category: category as SupplierCategoryKey,
    tax_id: taxId.trim() || undefined,
    country: country || undefined,
    phone: phone.trim() || undefined,
    ...(cardExtId ? { card_external_id: cardExtId, include_person: includePerson } : {}),
    ...(choice === 'new' ? { create_new: true } : choice ? { chosen_org_ext_id: choice } : {}),
  })

  const review = async () => {
    setBusy(true); setError(''); setRun(null)
    try {
      setProposal(await proposeSupplier(request()))
    } catch (err) {
      setProposal(null)
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const finish = (result: IssueRun) => {
    setRun(result)
    onChanged()
    qc.invalidateQueries({ queryKey: ['orgs-issued'] })
    if (result.status === 'done') {
      setProposal(null)
      showToast(t.nxtDone)
    }
  }

  const approve = async () => {
    setBusy(true); setError('')
    try {
      finish(await commitSupplier(request()))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const retry = async () => {
    if (!run) return
    setBusy(true); setError('')
    try {
      finish(await retryIssueRun(run.run_id))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const canApprove = proposal && (!proposal.needs_choice || choice !== '')
  const showBody = status !== '' || formOpen

  return (
    <div className="space-y-2 border-t border-gray-200 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-gray-700">{t.relSupplier}</span>
        <span className="ml-auto">
          <StatusSelect t={t} value={status} hasCode={hasCode}
            onChange={changeStatus} onNeedsApproval={() => setFormOpen(true)} />
        </span>
      </div>

      {showBody && (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {org.supplier_code && (<><dt className="text-gray-500">{t.supCode}</dt><dd className="font-mono">{org.supplier_code}</dd></>)}
            {hasCode && org.supplier_category && (<><dt className="text-gray-500">{t.supCategory}</dt><dd title={t.supCategoryLocked}>{t.supCategories[org.supplier_category]}</dd></>)}
            {org.supplier_drive_url && (<><dt className="text-gray-500">{t.nxtDriveFolder}</dt><dd><FolderLink url={org.supplier_drive_url} label={t.nxtOpenFolder} /></dd></>)}
          </dl>

          {/* Category: editable while a prospect; once the code and folder exist it is the read-only row above */}
          {status !== '' && !formOpen && !hasCode && (
            <Field label={t.supCategory}>
              <select className={inputCls} value={org.supplier_category ?? ''}
                onChange={e => e.target.value && changeCategory(e.target.value as SupplierCategoryKey)}>
                <option value="">{t.supCategoryChoose}</option>
                {CATEGORY_KEYS.map(k => <option key={k} value={k}>{t.supCategories[k]}</option>)}
              </select>
            </Field>
          )}

          {!formOpen && !hasCode && (
            <button className={btnPrimary} onClick={() => { setCategory(org.supplier_category ?? ''); setFormOpen(true) }}>
              {t.supActivate}
            </button>
          )}

          {formOpen && (
            <div className="space-y-2">
              <p className="text-xs text-gray-500">{t.relActiveNeedsApproval}</p>
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label={t.supCategory}>
                  <select className={inputCls} value={category}
                    onChange={e => { setCategory(e.target.value as SupplierCategoryKey | ''); edited() }}>
                    <option value="">{t.supCategoryChoose}</option>
                    {CATEGORY_KEYS.map(k => <option key={k} value={k}>{t.supCategories[k]}</option>)}
                  </select>
                </Field>
                <Field label={t.supCountry}>
                  <select className={inputCls} value={country}
                    onChange={e => { setCountry(e.target.value); edited() }}>
                    {COUNTRIES.map(c => <option key={c} value={c}>{c === 'TW' ? t.supCountryTW : c === 'JP' ? t.supCountryJP : t.supCountryOther}</option>)}
                  </select>
                </Field>
                <Field label={t.nxtTaxId} hint={t.supTaxIdHint}>
                  <input className={inputCls} value={taxId} maxLength={16}
                    placeholder={org.tax_id ?? ''} onChange={e => { setTaxId(e.target.value); edited() }} />
                </Field>
                <Field label={t.nxtPhone}>
                  <input className={inputCls} value={phone} onChange={e => { setPhone(e.target.value); edited() }} />
                </Field>
              </div>

              {cardExtId && (
                <label className="flex items-center gap-1.5 text-xs text-gray-700">
                  <input type="checkbox" checked={includePerson}
                    onChange={e => { setIncludePerson(e.target.checked); edited() }} />
                  {t.nxtIncludePerson}
                </label>
              )}

              <div className="flex gap-2">
                <button className={btnPrimary} disabled={busy || !category} onClick={review}>
                  {busy && !proposal ? t.nxtWorking : t.nxtPreview}
                </button>
                <button className={btnGhost} onClick={closeForm}>{t.nxtCancel}</button>
              </div>
            </div>
          )}

          {error && <p className="text-xs text-red-600 whitespace-pre-wrap">{error}</p>}

          {proposal && (
            <div className="rounded border border-blue-200 bg-white px-3 py-2 space-y-2 text-sm">
              <p className="text-xs font-medium text-gray-600">{t.nxtProposalTitle}</p>

              {proposal.needs_choice && (
                <fieldset className="space-y-1">
                  <legend className="text-xs text-gray-600">{t.nxtChoose}</legend>
                  {proposal.candidates.map(c => (
                    <label key={c.org_ext_id} className="flex items-start gap-1.5 text-xs">
                      <input type="radio" name={`sup-cand-${orgExtId}`} checked={choice === c.org_ext_id}
                        onChange={() => setChoice(c.org_ext_id)} />
                      <span>{c.customer_code ? `${c.customer_code} ` : ''}{c.name}
                        <span className="text-gray-400"> — {c.reason} ({Math.round(c.confidence * 100)}%)</span></span>
                    </label>
                  ))}
                  <label className="flex items-center gap-1.5 text-xs">
                    <input type="radio" name={`sup-cand-${orgExtId}`} checked={choice === 'new'}
                      onChange={() => setChoice('new')} />
                    {t.nxtCreateNew}
                  </label>
                </fieldset>
              )}

              {(proposal.supplier_code || proposal.suggested_supplier_code) && (
                <p>
                  <span className="text-xs text-gray-500 mr-2">{t.supCode}</span>
                  <span className="font-mono text-base font-semibold">
                    {proposal.supplier_code ?? proposal.suggested_supplier_code}
                  </span>
                  {!proposal.supplier_code && (
                    <span className="block text-xs text-amber-600">{t.nxtPreviewNote}</span>
                  )}
                </p>
              )}
              <p className="text-xs text-gray-500">
                {t.supCategoryFolder}: <span className="text-gray-700">{proposal.category_folder}</span>
              </p>

              {proposal.actions.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500">{t.nxtWillDo}</p>
                  <ul className="list-disc pl-5 text-xs text-gray-700 space-y-0.5">
                    {proposal.actions.map(a => <li key={a}>{a}</li>)}
                  </ul>
                </div>
              )}

              {proposal.warnings.length > 0 && (
                <div className="rounded bg-amber-50 px-2 py-1">
                  <p className="text-xs font-medium text-amber-700">{t.nxtWarnings}</p>
                  <ul className="list-disc pl-5 text-xs text-amber-800 space-y-0.5">
                    {proposal.warnings.map(w => <li key={w}>{w}</li>)}
                  </ul>
                </div>
              )}

              <button className={btnPrimary} disabled={busy || !canApprove} onClick={approve}>
                {busy ? t.nxtWorking : t.nxtApprove}
              </button>
            </div>
          )}

          {run && (
            <div className={`rounded border px-3 py-2 text-sm space-y-1 ${run.status === 'done' ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}>
              {run.status === 'done' ? (
                <>
                  <p className="font-medium text-green-800">{t.nxtDone}</p>
                  <p className="font-mono">{run.supplier_code}</p>
                  <p className="text-xs"><FolderLink url={run.supplier_drive_url} label={t.nxtDriveFolder} /></p>
                </>
              ) : (
                <>
                  <p className="font-medium text-red-800">{t.nxtFailed}</p>
                  {run.failed_step && <p className="text-xs text-red-700">{t.nxtFailedAt}: {run.failed_step}</p>}
                  {run.error && <p className="text-xs text-red-700 whitespace-pre-wrap">{run.error}</p>}
                  <button className={btnPrimary} disabled={busy} onClick={retry}>
                    {busy ? t.nxtWorking : t.nxtRetry}
                  </button>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
