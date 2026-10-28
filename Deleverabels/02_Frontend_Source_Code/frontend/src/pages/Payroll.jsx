import { useState } from 'react'
import { BadgeCheck, Calculator, Check, Download, FileText, Plus } from 'lucide-react'
import api, { describeError, downloadFile } from '../api/client'
import { useToast } from '../components/toastContext'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { formatDate, formatDateTime, formatMoney, monthLabel } from '../lib/format'
import {
  AsyncContent, Button, Card, ConfirmDialog, DataTable, Dialog, EmptyState, FilterBar, Notice, PageHeader, Pagination, Select,
  StatusBadge, TextInput,
} from '../components/ui'

const RECORD_STATUSES = ['CALCULATED', 'APPROVED', 'PAID']

// One record with its line items. Approve (CALCULATED -> APPROVED) and mark paid (APPROVED -> PAID) are administrative
// status changes; nobody may approve or pay their own payroll (the API answers 403), and marking paid moves no money.
function RecordDetail({ recordId, onClose, onChanged }) {
  const result = useApi(`/payroll/records/${recordId}`)
  const [reference, setReference] = useState('')
  const [confirming, setConfirming] = useState(null)
  const action = useAction()
  const act = (kind) => action.run(
    () => api.post(`/payroll/records/${recordId}/${kind}`, kind === 'mark-paid' ? cleanParams({ payment_reference: reference.trim() }) : {}),
    { success: kind === 'approve' ? 'Payroll record approved' : 'Payroll record marked as paid', onSuccess: () => { setConfirming(null); result.reload(); onChanged() } })
  const lines = (title, items, currency) => (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">{title}</h3>
      {items.length ? (
        <ul className="divide-y divide-slate-100 text-sm">
          {items.map((line) => <li key={line.code} className="flex justify-between gap-3 py-1"><span>{line.name}</span><span>{formatMoney(line.amount, currency)}</span></li>)}
        </ul>
      ) : <p className="text-sm text-slate-500">None</p>}
    </div>
  )
  return (
    <Dialog open title="Payroll record" onClose={onClose} wide
      footer={result.data && !confirming && (
        <>
          {result.data.status === 'CALCULATED' && <Button icon={Check} onClick={() => setConfirming('approve')}>Approve</Button>}
          {result.data.status === 'APPROVED' && <Button icon={BadgeCheck} onClick={() => setConfirming('mark-paid')}>Mark as paid</Button>}
        </>
      )}>
      <AsyncContent result={result}>
        {(r) => (
          <div className="space-y-4">
            <p className="text-sm text-slate-700"><span className="font-medium">{r.employee.full_name}</span> ({r.employee.employee_code}) · {monthLabel(r.period.year, r.period.month)} · <StatusBadge value={r.status} /></p>
            {r.warnings.length > 0 && <Notice tone="warning">{r.warnings.join(' · ')}</Notice>}
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">{lines('Earnings', r.earnings, r.currency)}{lines('Deductions', r.deductions, r.currency)}</div>
            <div className="grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-sm">
              <p>Gross <span className="block font-semibold">{formatMoney(r.gross_earnings, r.currency)}</span></p>
              <p>Deductions <span className="block font-semibold">{formatMoney(r.total_deductions, r.currency)}</span></p>
              <p>Net pay <span className="block text-lg font-semibold text-teal-800">{formatMoney(r.net_pay, r.currency)}</span></p>
            </div>
            {r.payment_reference && <p className="text-xs text-slate-500">Payment reference: {r.payment_reference}</p>}
            {confirming && (
              <div className="space-y-3 border-t border-slate-100 pt-4">
                <p className="text-sm text-slate-700">{confirming === 'approve'
                  ? 'Approve this record? Approved records are visible to the employee and are no longer recalculated.'
                  : 'Mark this record as paid? This records an administrative status only; it does not move money.'}</p>
                {confirming === 'mark-paid' && <TextInput label="Payment reference" value={reference} maxLength={100} onChange={(e) => setReference(e.target.value)}
                  hint="Optional reference to an external payment run, never a bank account number" error={action.errors.payment_reference} />}
                {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
                <div className="flex justify-end gap-2">
                  <Button variant="secondary" onClick={() => setConfirming(null)} disabled={action.busy}>Back</Button>
                  <Button onClick={() => act(confirming)} loading={action.busy}>Confirm</Button>
                </div>
              </div>
            )}
          </div>
        )}
      </AsyncContent>
    </Dialog>
  )
}

function Records({ period, onChanged }) {
  const { filters, page, setPage, update, reset } = useListState({ status: '', department_id: '' })
  const list = useApi(`/payroll/periods/${period.id}/records`, cleanParams({ ...filters, page, page_size: 20 }))
  const departments = useApi('/departments', { page_size: 100 })
  const toast = useToast()
  const [exporting, setExporting] = useState('')
  const [open, setOpen] = useState(null)
  const [confirmCalc, setConfirmCalc] = useState(false)
  const [skipped, setSkipped] = useState([])
  const calc = useAction()

  // Creates CALCULATED records for every employee with a salary profile, or recalculates CALCULATED ones.
  // APPROVED and PAID records are never touched; every skip is listed with its reason.
  const calculate = () => calc.run(() => api.post(`/payroll/periods/${period.id}/calculate`, {}), {
    success: (res) => `Calculated: ${res.data.created} new, ${res.data.recalculated} recalculated, ${res.data.skipped.length} skipped`,
    onSuccess: (res) => { setConfirmCalc(false); setSkipped(res.data.skipped); list.reload(); onChanged() },
  })

  const exportAs = async (format) => {
    if (exporting) return
    setExporting(format)
    try {
      const rows = await downloadFile('/reports/payroll', cleanParams({ period_id: period.id, format, ...filters }))
      toast.success(`Payroll register exported${rows !== null ? ` (${rows} rows)` : ''}. The export is recorded in the audit log.`)
    } catch (error) {
      toast.error(describeError(error))
    } finally {
      setExporting('')
    }
  }

  const columns = [
    { key: 'employee', header: 'Employee', render: (r) => `${r.employee.full_name} (${r.employee.employee_code})` },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'gross', header: 'Gross', className: 'text-right', render: (r) => formatMoney(r.gross_earnings, r.currency) },
    { key: 'deductions', header: 'Deductions', className: 'text-right', render: (r) => formatMoney(r.total_deductions, r.currency) },
    { key: 'net', header: 'Net pay', className: 'text-right font-semibold', render: (r) => formatMoney(r.net_pay, r.currency) },
    { key: 'warnings', header: 'Warnings', render: (r) => (r.has_warnings ? <StatusBadge value="AT_RISK" /> : '—') },
  ]
  return (
    <Card title={`Records — ${monthLabel(period.year, period.month)}`} description={`${formatDate(period.start_date)} – ${formatDate(period.end_date)}`}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button size="sm" icon={Calculator} onClick={() => setConfirmCalc(true)}>Calculate</Button>
          {['csv', 'xlsx', 'pdf'].map((format) => (
            <Button key={format} variant="secondary" size="sm" icon={Download} loading={exporting === format} onClick={() => exportAs(format)}>{format.toUpperCase()}</Button>
          ))}
        </div>
      }>
      {skipped.length > 0 && (
        <div className="mb-3"><Notice tone="warning">Skipped in the last calculation: {skipped.map((s) => `employee #${s.employee_id} — ${s.reason}`).join('; ')}</Notice></div>
      )}
      <FilterBar onReset={reset}>
        <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={RECORD_STATUSES} placeholder="All" className="w-40" />
        <Select label="Department" value={filters.department_id} onChange={(e) => update({ department_id: e.target.value })} placeholder="All" className="w-48"
          options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
      </FilterBar>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No payroll records" message="This period has not been calculated, or no records match the filters." />}>
        {(d) => (<><DataTable caption="Payroll records" columns={columns} rows={d.items} onRowClick={(r) => setOpen(r.id)} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      <ConfirmDialog open={confirmCalc} title={`Calculate payroll for ${monthLabel(period.year, period.month)}?`} confirmLabel="Calculate" busy={calc.busy}
        onCancel={() => setConfirmCalc(false)} onConfirm={calculate}
        message="Creates records for every employee with an active salary profile and recalculates records that are still CALCULATED. Approved and paid records are never changed. The calculation is audited." />
      {open && <RecordDetail recordId={open} onClose={() => setOpen(null)} onChanged={() => { list.reload(); onChanged() }} />}
    </Card>
  )
}

function NewPeriod({ onClose, onSaved }) {
  const [year, setYear] = useState(() => String(new Date().getFullYear()))
  const [month, setMonth] = useState(() => String(new Date().getMonth() + 1))
  const action = useAction()
  const submit = async (event) => {
    event.preventDefault()
    await action.run(() => api.post('/payroll/periods', { year: Number(year), month: Number(month) }),
      { success: 'Payroll period created', onSuccess: (res) => onSaved(res.data) })
  }
  return (
    <Dialog open title="New payroll period" onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="period-form" loading={action.busy}>Create</Button></>}>
      <form id="period-form" onSubmit={submit} noValidate className="grid grid-cols-2 gap-4">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="col-span-2 text-sm text-red-700">{action.message}</p>}
        <TextInput label="Year" type="number" min={2000} max={2100} required value={year} onChange={(e) => setYear(e.target.value)} error={action.errors.year} />
        <Select label="Month" value={month} onChange={(e) => setMonth(e.target.value)} error={action.errors.month}
          options={Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: monthLabel(2026, i + 1).split(' ')[0] }))} />
      </form>
    </Dialog>
  )
}

// ADMIN/HR only (route-guarded; the API also returns 403 to others).
export function Payroll() {
  const [year, setYear] = useState('')
  const periods = useApi('/payroll/periods', cleanParams({ year, page_size: 24 }))
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)
  return (
    <div>
      <PageHeader title="Payroll" description="Payroll periods, calculation, approval and payment status. Marking a record paid records an administrative status; it does not move money."
        actions={<Button icon={Plus} onClick={() => setCreating(true)}>New period</Button>} />
      {creating && <NewPeriod onClose={() => setCreating(false)} onSaved={(p) => { setCreating(false); periods.reload(); setSelected(p) }} />}
      <Card title="Periods">
        <FilterBar>
          <TextInput label="Year" type="number" min={2000} max={2100} value={year} onChange={(e) => setYear(e.target.value)} className="w-32" />
        </FilterBar>
        <AsyncContent result={periods} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No payroll periods" />}>
          {(d) => (
            <div className="flex flex-wrap gap-2">
              {d.items.map((p) => (
                <button key={p.id} type="button" onClick={() => setSelected(p)}
                  className={`rounded-md border px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-blue-600 ${selected?.id === p.id ? 'border-blue-500 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'}`}>
                  <span className="block font-medium text-slate-900">{monthLabel(p.year, p.month)}</span>
                  <span className="text-xs text-slate-500">
                    {/* record_counts lists every status, including zeros; only non-zero counts are shown */}
                    {Object.entries(p.record_counts).filter(([, n]) => n > 0).map(([s, n]) => `${n} ${s.toLowerCase()}`).join(', ') || 'Not calculated'}
                  </span>
                </button>
              ))}
            </div>
          )}
        </AsyncContent>
      </Card>
      {selected ? <div className="mt-4"><Records key={selected.id} period={selected} onChanged={periods.reload} /></div> : <p className="mt-4 text-sm text-slate-500">Select a period to see its records.</p>}
    </div>
  )
}

function PayslipView({ recordId, onClose }) {
  const result = useApi(`/payroll/me/${recordId}/payslip`)
  const lines = (title, items, currency) => (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">{title}</h3>
      {items.length ? (
        <ul className="divide-y divide-slate-100 text-sm">
          {items.map((line) => (
            <li key={line.code} className="flex justify-between py-1"><span>{line.name}</span><span>{formatMoney(line.amount, currency)}</span></li>
          ))}
        </ul>
      ) : <p className="text-sm text-slate-500">None</p>}
    </div>
  )
  return (
    <Dialog open title="Payslip" onClose={onClose} wide>
      <AsyncContent result={result}>
        {(p) => (
          <div className="space-y-4">
            <p className="text-sm text-slate-700">
              <span className="font-medium">{p.employee.full_name}</span> ({p.employee.employee_code}) · {monthLabel(p.period.year, p.period.month)} · <StatusBadge value={p.status} />
            </p>
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              {lines('Earnings', p.earnings, p.currency)}
              {lines('Deductions', p.deductions, p.currency)}
            </div>
            <div className="grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-sm">
              <p>Gross <span className="block font-semibold">{formatMoney(p.gross_earnings, p.currency)}</span></p>
              <p>Deductions <span className="block font-semibold">{formatMoney(p.total_deductions, p.currency)}</span></p>
              <p>Net pay <span className="block text-lg font-semibold text-teal-800">{formatMoney(p.net_pay, p.currency)}</span></p>
            </div>
            <Notice>{p.note}</Notice>
          </div>
        )}
      </AsyncContent>
    </Dialog>
  )
}

// Every role: their own APPROVED/PAID payroll only.
export function Payslips() {
  const { page, setPage } = useListState({})
  const list = useApi('/payroll/me', { page, page_size: 12 })
  const [open, setOpen] = useState(null)
  const columns = [
    { key: 'period', header: 'Period', render: (r) => monthLabel(r.period.year, r.period.month) },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'net', header: 'Net pay', className: 'text-right', render: (r) => formatMoney(r.net_pay, r.currency) },
    { key: 'paid_at', header: 'Paid', render: (r) => formatDateTime(r.paid_at) },
    { key: 'view', header: <span className="sr-only">View</span>, render: (r) => <Button variant="ghost" size="sm" icon={FileText} onClick={() => setOpen(r.id)}>Payslip</Button> },
  ]
  return (
    <div>
      <PageHeader title="My payslips" description="Your approved and paid payroll records." />
      <Card>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No payslips yet" message="Payslips appear once your payroll is approved." />}>
          {(d) => (<><DataTable caption="My payslips" columns={columns} rows={d.items} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {open && <PayslipView recordId={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
