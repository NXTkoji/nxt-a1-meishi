/**
 * NxtCustomerPanel — "NXT customer" switch and project-code issuing for one organization.
 *
 * Shown inside an organization section of PersonEditor. With the switch off the panel is
 * just the switch; everything else appears only for NXT customers.
 *
 * Issuing is propose, then approve: "Review" calls /projects/propose (creates nothing),
 * "Approve and issue" calls /projects/commit. A run that stops part-way can be retried.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  commitProject,
  getOrganization,
  listIssuedOrganizations,
  proposeProject,
  retryIssueRun,
  setNxtCustomer,
} from '../api'
import { useLang } from '../LangContext'
import { useToast } from './Toast'
import type { IssueProposal, IssueRequest, IssueRun } from '../types'

/** The API reports errors as "422 Unprocessable Entity: {"detail": "..."}"; show only the detail. */
function errorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const start = raw.indexOf('{')
  if (start >= 0) {
    try {
      const detail = JSON.parse(raw.slice(start)).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) return detail.map(d => d.msg ?? String(d)).join('; ')
    } catch { /* fall through to the raw text */ }
  }
  return raw
}

const inputCls = 'w-full rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none'
const btnPrimary = 'rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50'
const btnGhost = 'rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50'

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-gray-500 mb-0.5">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-400 mt-0.5">{hint}</span>}
    </label>
  )
}

function FolderLink({ url, label }: { url?: string | null; label: string }) {
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
      {label}
    </a>
  )
}

export function NxtCustomerPanel({ orgExtId, orgName, cardExtId, cardTaxId }: {
  orgExtId: string
  orgName: string
  /** Card whose person is exported under the company on approval; absent = no person step */
  cardExtId?: string
  /** 統一編號 read from the card; prefills the 統編 field */
  cardTaxId?: string
}) {
  const { t } = useLang()
  const { showToast } = useToast()
  const qc = useQueryClient()

  const { data: org, isError } = useQuery({
    queryKey: ['org', orgExtId],
    queryFn: () => getOrganization(orgExtId),
  })

  const [formOpen, setFormOpen] = useState(false)
  // Digits only: cards print it as "統一編號：90270905" or with spaces
  const [taxId, setTaxId] = useState((cardTaxId ?? '').replace(/\D/g, ''))
  const [phone, setPhone] = useState('')
  const [line, setLine] = useState('DS')
  const [site, setSite] = useState('')
  const [roles, setRoles] = useState<string[]>([])
  const [soldVia, setSoldVia] = useState('')
  const [legacy, setLegacy] = useState('')
  const [includePerson, setIncludePerson] = useState(true)

  const [proposal, setProposal] = useState<IssueProposal | null>(null)
  const [choice, setChoice] = useState<string>('')        // candidate org_ext_id, or 'new'
  const [run, setRun] = useState<IssueRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Only needed for the "sold via" picker; lists customers that already have a Customer ID
  const { data: orgs } = useQuery({
    queryKey: ['orgs-issued'],
    queryFn: listIssuedOrganizations,
    enabled: formOpen,
  })
  const resellers = (orgs ?? []).filter(o => o.external_id !== orgExtId)

  if (isError) return <p className="text-xs text-red-500">{t.nxtLoadError}</p>
  if (!org) return null

  const refreshOrg = () => {
    qc.invalidateQueries({ queryKey: ['org', orgExtId] })
    qc.invalidateQueries({ queryKey: ['orgs-issued'] })
  }

  const toggle = async (enabled: boolean) => {
    try {
      await setNxtCustomer(orgExtId, enabled)
      refreshOrg()
      if (!enabled) closeForm()
    } catch (err) {
      showToast(errorText(err), 'error')
    }
  }

  const request = (): IssueRequest => ({
    org_ext_id: orgExtId,
    customer_name: orgName,
    tax_id: taxId.trim() || undefined,
    phone: phone.trim() || undefined,
    product_line: line,
    site_name: site.trim(),
    roles,
    sold_via_org_ext_id: soldVia || undefined,
    legacy_codes: legacy.trim() || undefined,
    // Export the card's person under the company after the company step
    ...(cardExtId ? { card_external_id: cardExtId, include_person: includePerson } : {}),
    // The user's pick among fuzzy candidates, when the proposal asked for one
    ...(choice === 'new' ? { create_new: true } : choice ? { chosen_org_ext_id: choice } : {}),
  })

  const closeForm = () => {
    setFormOpen(false)
    setProposal(null)
    setRun(null)
    setChoice('')
    setError('')
  }

  const review = async () => {
    setBusy(true); setError(''); setRun(null)
    try {
      setProposal(await proposeProject(request()))
    } catch (err) {
      setProposal(null)
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const finish = (result: IssueRun) => {
    setRun(result)
    refreshOrg()
    if (result.status === 'done') {
      setProposal(null)
      showToast(t.nxtDone)
    }
  }

  const approve = async () => {
    setBusy(true); setError('')
    try {
      finish(await commitProject(request()))
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
  const issued = Boolean(org.customer_code)

  return (
    <div className="mt-2 rounded border border-gray-200 bg-gray-50 px-3 py-2 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-gray-600">{t.nxtSection}</span>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-gray-700">
          <input
            type="checkbox"
            checked={org.is_nxt_customer}
            disabled={issued}
            title={issued ? t.nxtSwitchLocked : undefined}
            onChange={e => toggle(e.target.checked)}
          />
          {t.nxtSwitchLabel}
        </label>
      </div>

      {org.is_nxt_customer && (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {org.customer_code && (<><dt className="text-gray-500">{t.nxtCustomerId}</dt><dd className="font-mono">{org.customer_code}</dd></>)}
            {org.tax_id && (<><dt className="text-gray-500">{t.nxtTaxId}</dt><dd className="font-mono">{org.tax_id}</dd></>)}
            {org.legacy_codes && (<><dt className="text-gray-500">{t.nxtOldCodes}</dt><dd className="font-mono">{org.legacy_codes}</dd></>)}
            {org.drive_url && (<><dt className="text-gray-500">{t.nxtDriveFolder}</dt><dd><FolderLink url={org.drive_url} label={t.nxtOpenFolder} /></dd></>)}
          </dl>

          {!formOpen && (
            <button className={btnPrimary} onClick={() => setFormOpen(true)}>{t.nxtIssueButton}</button>
          )}

          {formOpen && (
            <div className="space-y-2">
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label={t.nxtTaxId} hint={t.nxtTaxIdHint}>
                  <input className={inputCls} value={taxId} inputMode="numeric" maxLength={12}
                    placeholder={org.tax_id ?? ''} onChange={e => { setTaxId(e.target.value); setProposal(null) }} />
                </Field>
                <Field label={t.nxtPhone}>
                  <input className={inputCls} value={phone} onChange={e => { setPhone(e.target.value); setProposal(null) }} />
                </Field>
                <Field label={t.nxtProductLine}>
                  <select className={inputCls} value={line} onChange={e => { setLine(e.target.value); setProposal(null) }}>
                    <option value="DS">{t.nxtLineDS}</option>
                    <option value="DJ">{t.nxtLineDJ}</option>
                  </select>
                </Field>
                <Field label={t.nxtSiteName}>
                  <input className={inputCls} value={site} onChange={e => { setSite(e.target.value); setProposal(null) }} />
                </Field>
              </div>

              <div className="flex flex-wrap items-center gap-4 text-sm">
                <span className="text-xs text-gray-500">{t.nxtRoles}</span>
                {([['reseller', t.nxtRoleReseller], ['end_user', t.nxtRoleEndUser]] as const).map(([value, label]) => (
                  <label key={value} className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={roles.includes(value)}
                      onChange={e => {
                        setRoles(e.target.checked ? [...roles, value] : roles.filter(r => r !== value))
                        setProposal(null)
                      }}
                    />
                    {label}
                  </label>
                ))}
              </div>

              <Field label={t.nxtSoldVia}>
                <select className={inputCls} value={soldVia} onChange={e => { setSoldVia(e.target.value); setProposal(null) }}>
                  <option value="">{t.nxtSoldViaNone}</option>
                  {resellers.map(o => (
                    <option key={o.external_id} value={o.external_id}>{o.customer_code} {o.primary_name ?? ''}</option>
                  ))}
                </select>
              </Field>

              <Field label={t.nxtLegacyCodes} hint={t.nxtLegacyHint}>
                <input className={inputCls} value={legacy} placeholder={org.legacy_codes ?? 'DS176'}
                  onChange={e => { setLegacy(e.target.value); setProposal(null) }} />
              </Field>

              {/* Changes what approval does, so it clears the review like every other field */}
              {cardExtId && (
                <label className="flex items-center gap-1.5 text-xs text-gray-700">
                  <input type="checkbox" checked={includePerson}
                    onChange={e => { setIncludePerson(e.target.checked); setProposal(null) }} />
                  {t.nxtIncludePerson}
                </label>
              )}

              <div className="flex gap-2">
                <button className={btnPrimary} disabled={busy || !site.trim()} onClick={review}>
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
                      <input type="radio" name={`cand-${orgExtId}`} checked={choice === c.org_ext_id}
                        onChange={() => setChoice(c.org_ext_id)} />
                      <span>{c.customer_code ? `${c.customer_code} ` : ''}{c.name}
                        <span className="text-gray-400"> — {c.reason} ({Math.round(c.confidence * 100)}%)</span></span>
                    </label>
                  ))}
                  <label className="flex items-center gap-1.5 text-xs">
                    <input type="radio" name={`cand-${orgExtId}`} checked={choice === 'new'}
                      onChange={() => setChoice('new')} />
                    {t.nxtCreateNew}
                  </label>
                </fieldset>
              )}

              {proposal.project_code && (
                <p>
                  <span className="text-xs text-gray-500 mr-2">{t.nxtProjectCode}</span>
                  <span className="font-mono text-base font-semibold">{proposal.project_code}</span>
                  {proposal.project_code_tentative && (
                    <span className="block text-xs text-amber-600">{t.nxtPreviewNote}</span>
                  )}
                </p>
              )}

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
                  <p className="font-mono">{run.customer_code} · {run.project_code}</p>
                  <p className="text-xs space-x-3">
                    <FolderLink url={run.customer_drive_url} label={t.nxtDriveFolder} />
                    <FolderLink url={run.project_drive_url} label={t.nxtProjectFolder} />
                  </p>
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
