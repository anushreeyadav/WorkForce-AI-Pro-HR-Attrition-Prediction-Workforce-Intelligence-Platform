import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import EmployeePicker from '../components/EmployeePicker'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { formatDate, formatDateTime, humanize } from '../lib/format'
import {
  AsyncContent, Button, Card, Checkbox, DataTable, DetailList, Dialog, EmptyState, FilterBar, Notice, PageHeader, Pagination,
  PriorityBadge, Select, StatusBadge, TextArea, TextInput,
} from '../components/ui'

const CATEGORIES = ['MANAGER_CHECK_IN', 'WORKLOAD_REVIEW', 'CAREER_DEVELOPMENT', 'RECOGNITION', 'LEARNING_SUPPORT',
  'WORK_ENVIRONMENT_REVIEW', 'OTHER_SUPPORT']
const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH']
const ACTIVE = ['OPEN', 'IN_PROGRESS']

// POST /api/interventions. Scope, the optional risk alert and the assignee are validated by the server (404/422).
function InterventionForm({ prefill, onClose, onSaved }) {
  const { user } = useAuth()
  const [form, setForm] = useState({ employee_id: prefill.employee ?? '', category: 'MANAGER_CHECK_IN', priority: 'MEDIUM',
    title: '', description: '', due_date: '', assign_to_me: true })
  const [local, setLocal] = useState({})
  const action = useAction()
  const errors = { ...local, ...action.errors }
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    const missing = {}
    if (!form.employee_id) missing.employee_id = 'Required'
    if (form.title.trim().length < 3) missing.title = 'At least 3 characters'
    setLocal(missing)
    if (Object.keys(missing).length) return
    const body = cleanParams({
      employee_id: Number(form.employee_id), category: form.category, priority: form.priority, title: form.title.trim(),
      description: form.description.trim(), due_date: form.due_date,
      risk_alert_id: prefill.alert ? Number(prefill.alert) : null, assigned_to_user_id: form.assign_to_me ? user.id : null,
    })
    await action.run(() => api.post('/interventions', body), { success: 'Intervention created', onSuccess: (res) => onSaved(res.data) })
  }
  return (
    <Dialog open title="New intervention" onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="intervention-form" loading={action.busy}>Create</Button></>}>
      <form id="intervention-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        {prefill.alert ? (
          <p className="text-sm text-slate-700 sm:col-span-2">For the employee of risk alert #{prefill.alert}.</p>
        ) : (
          <div className="sm:col-span-2">
            <EmployeePicker label="Employee" required value={form.employee_id} onChange={set('employee_id')} error={errors.employee_id}
              hint="Employees in your scope; never yourself" />
          </div>
        )}
        <TextInput label="Title" required value={form.title} onChange={set('title')} error={errors.title} maxLength={150} className="sm:col-span-2" />
        <TextArea label="Description" value={form.description} onChange={set('description')} error={errors.description} maxLength={2000}
          className="sm:col-span-2" hint="Internal; avoid health or other sensitive personal details" />
        <Select label="Category" value={form.category} onChange={set('category')} options={CATEGORIES.map((c) => ({ value: c, label: humanize(c) }))} />
        <Select label="Priority" value={form.priority} onChange={set('priority')} options={PRIORITIES} />
        <TextInput label="Due date" type="date" value={form.due_date} onChange={set('due_date')} error={errors.due_date} />
        <div className="self-end pb-2"><Checkbox label="Assign to me" checked={form.assign_to_me} onChange={(e) => setForm((f) => ({ ...f, assign_to_me: e.target.checked }))} /></div>
      </form>
    </Dialog>
  )
}

// Remounted (key = version) after every change, so it always shows the latest events.
function History({ id }) {
  const result = useApi(`/interventions/${id}/history`, { page_size: 50 })
  return (
    <AsyncContent result={result} skeleton={1} isEmpty={(d) => d.items.length === 0} empty={<p className="text-sm text-slate-500">No history yet.</p>}>
      {(d) => (
        <ol className="space-y-1 text-sm">
          {d.items.map((e) => (
            <li key={e.id} className="text-slate-700">
              <span className="text-xs text-slate-500">{formatDateTime(e.created_at)}</span> · {humanize(e.event_type)}
              {e.to_status ? ` → ${humanize(e.to_status)}` : ''}{e.note ? <span className="block whitespace-pre-line pl-4 text-slate-800">{e.note}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </AsyncContent>
  )
}

// Lifecycle on the detail dialog: start, complete (outcome), cancel (reason), notes, take ownership. Each action sends the
// version shown, so a concurrent change is reported (409) instead of overwritten.
function InterventionDetail({ intervention, onClose, onChanged }) {
  const { user } = useAuth()
  const [mode, setMode] = useState(null)
  const [text, setText] = useState('')
  const action = useAction()
  const active = ACTIVE.includes(intervention.status)
  const version = { expected_version: intervention.version }
  const minLength = mode === 'note' ? 1 : 5
  const run = (request, success) => action.run(request, { success, onSuccess: (res) => { setMode(null); setText(''); onChanged(res.data) } })
  const confirm = () => {
    if (text.trim().length < minLength) return
    const id = intervention.id
    if (mode === 'complete') run(() => api.post(`/interventions/${id}/complete`, { ...version, outcome: text.trim() }), 'Intervention completed')
    if (mode === 'cancel') run(() => api.post(`/interventions/${id}/cancel`, { ...version, reason: text.trim() }), 'Intervention cancelled')
    if (mode === 'note') action.run(() => api.post(`/interventions/${id}/notes`, { note: text.trim() }),
      { success: 'Note added', onSuccess: () => { setMode(null); setText(''); onChanged({ ...intervention, version: intervention.version + 1 }, true) } })
  }
  return (
    <Dialog open title={intervention.title} onClose={onClose} wide
      footer={active && !mode && (
        <>
          {intervention.assigned_to_user_id !== user.id && (
            <Button variant="secondary" loading={action.busy} onClick={() => run(() => api.post(`/interventions/${intervention.id}/assign`, { ...version, user_id: user.id }), 'Assigned to you')}>Assign to me</Button>
          )}
          <Button variant="secondary" onClick={() => setMode('note')}>Add note</Button>
          <Button variant="secondary" onClick={() => setMode('cancel')}>Cancel intervention</Button>
          {intervention.status === 'OPEN'
            ? <Button loading={action.busy} onClick={() => run(() => api.post(`/interventions/${intervention.id}/start`, version), 'Intervention started')}>Start</Button>
            : <Button onClick={() => setMode('complete')}>Complete</Button>}
        </>
      )}>
      <DetailList items={[
        ['Employee', `${intervention.employee.full_name} (${intervention.employee.employee_code})`], ['Category', humanize(intervention.category)],
        ['Priority', <PriorityBadge key="p" value={intervention.priority} />], ['Status', <StatusBadge key="s" value={intervention.status} />],
        ['Due', formatDate(intervention.due_date)], ['Linked risk alert', intervention.risk_alert_id ? `#${intervention.risk_alert_id}` : '—'],
        ['Assigned to', intervention.assigned_to_user_id === user.id ? 'You' : intervention.assigned_to_user_id ? `User #${intervention.assigned_to_user_id}` : 'Unassigned'],
        ['Started', formatDateTime(intervention.started_at)], ['Completed', formatDateTime(intervention.completed_at)],
      ]} />
      {intervention.description && <><h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Description</h3><p className="mt-1 whitespace-pre-line text-sm text-slate-800">{intervention.description}</p></>}
      {intervention.outcome && <><h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Outcome</h3><p className="mt-1 whitespace-pre-line text-sm text-slate-800">{intervention.outcome}</p></>}
      {intervention.cancellation_reason && <><h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Cancellation reason</h3><p className="mt-1 text-sm text-slate-800">{intervention.cancellation_reason}</p></>}
      {mode && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <TextArea label={{ complete: 'Outcome', cancel: 'Reason for cancelling', note: 'Follow-up note' }[mode]} required value={text}
            maxLength={mode === 'note' ? 2000 : mode === 'cancel' ? 500 : 1000} onChange={(e) => setText(e.target.value)}
            error={text && text.trim().length < minLength ? `At least ${minLength} characters` : action.errors.outcome ?? action.errors.reason ?? action.errors.note} />
          {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => { setMode(null); setText('') }} disabled={action.busy}>Back</Button>
            <Button onClick={confirm} loading={action.busy} disabled={text.trim().length < minLength}>Confirm</Button>
          </div>
        </div>
      )}
      {!mode && action.message && <p role="alert" className="mt-3 text-sm text-red-700">{action.message}</p>}
      <h3 className="mt-5 mb-1 text-xs font-semibold uppercase text-slate-500">History</h3>
      <History key={intervention.version} id={intervention.id} />
      <div className="mt-4"><Notice>Completing an intervention does not resolve the linked risk alert.</Notice></div>
    </Dialog>
  )
}

export default function Interventions() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { filters, page, setPage, update, reset } = useListState({ q: '', status: '', priority: '', assigned_to_me: false, overdue: '' })
  const q = useDebounced(filters.q)
  const list = useApi('/interventions', cleanParams({ ...filters, q: q.trim(), page, page_size: 20 }))
  const summary = useApi('/interventions/summary')
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(null)
  const focus = searchParams.get('focus')
  const fromAlert = searchParams.get('alert')
  const forEmployee = searchParams.get('employee')

  useEffect(() => {
    if (!focus) return
    api.get(`/interventions/${focus}`).then((res) => setSelected(res.data)).catch(() => setSelected(null))
  }, [focus])
  const close = () => {
    setSelected(null)
    if (focus) setSearchParams({})
  }
  const prefill = creating ?? (fromAlert && forEmployee ? { alert: fromAlert, employee: forEmployee } : null)
  const closeForm = () => {
    setCreating(null)
    if (fromAlert) setSearchParams({})
  }
  const refresh = () => {
    list.reload()
    summary.reload()
  }

  const columns = [
    { key: 'title', header: 'Intervention', render: (i) => <span className="font-medium text-slate-900">{i.title}</span> },
    { key: 'employee', header: 'Employee', render: (i) => i.employee.full_name },
    { key: 'category', header: 'Category', render: (i) => humanize(i.category) },
    { key: 'priority', header: 'Priority', render: (i) => <PriorityBadge value={i.priority} /> },
    { key: 'status', header: 'Status', render: (i) => <StatusBadge value={i.status} /> },
    { key: 'due', header: 'Due', render: (i) => <span className={i.is_overdue ? 'font-medium text-red-700' : ''}>{formatDate(i.due_date)}{i.is_overdue ? ' (overdue)' : ''}</span> },
  ]

  return (
    <div>
      <PageHeader title="Interventions" description="Agreed retention and support actions. You never see interventions about yourself."
        actions={<Button icon={Plus} onClick={() => setCreating({})}>New intervention</Button>} />
      <AsyncContent result={summary} skeleton={1}>
        {(s) => <p className="mb-3 text-sm text-slate-600">{Object.entries(s.by_status).map(([k, n]) => `${humanize(k)} ${n}`).join(' · ')} · {s.overdue} overdue</p>}
      </AsyncContent>
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="Search" placeholder="Title" value={filters.q} maxLength={100} onChange={(e) => update({ q: e.target.value })} className="w-56" />
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={['OPEN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED']} placeholder="All" className="w-40" />
          <Select label="Priority" value={filters.priority} onChange={(e) => update({ priority: e.target.value })} options={PRIORITIES} placeholder="All" className="w-32" />
          <Select label="Overdue" value={filters.overdue} onChange={(e) => update({ overdue: e.target.value })} placeholder="Any" className="w-32"
            options={[{ value: 'true', label: 'Overdue' }, { value: 'false', label: 'Not overdue' }]} />
          <div className="pb-2"><Checkbox label="Assigned to me" checked={filters.assigned_to_me} onChange={(e) => update({ assigned_to_me: e.target.checked })} /></div>
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No interventions match" />}>
          {(d) => (<><DataTable caption="Interventions" columns={columns} rows={d.items} onRowClick={setSelected} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {selected && <InterventionDetail key={selected.id} intervention={selected} onClose={close}
        onChanged={(next, noteOnly) => { if (!noteOnly) setSelected(next); else api.get(`/interventions/${next.id}`).then((r) => setSelected(r.data)).catch(() => {}); refresh() }} />}
      {prefill && <InterventionForm prefill={prefill} onClose={closeForm} onSaved={(created) => { closeForm(); refresh(); setSelected(created) }} />}
    </div>
  )
}
