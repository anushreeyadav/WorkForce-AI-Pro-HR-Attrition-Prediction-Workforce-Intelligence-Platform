import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus, UserPlus } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { isStaff } from '../lib/navigation'
import { formatDate, formatDateTime, humanize } from '../lib/format'
import {
  AsyncContent, Button, Card, Checkbox, DataTable, DetailList, Dialog, EmptyState, FilterBar, MetricCard, PageHeader,
  Pagination, PriorityBadge, Select, StatusBadge, TextArea, TextInput,
} from '../components/ui'

const STATUSES = ['OPEN', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED', 'CANCELLED']
const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
const TYPES = ['FOLLOW_UP', 'DOCUMENTATION', 'ONBOARDING', 'OFFBOARDING', 'TRAINING', 'REVIEW', 'COMPLIANCE', 'OTHER']
const DONE = { start: 'Task started', block: 'Task blocked', unblock: 'Task unblocked', complete: 'Task completed', cancel: 'Task cancelled', reopen: 'Task reopened' }
const BLOCK_REASONS = ['WAITING_ON_EMPLOYEE', 'WAITING_ON_APPROVAL', 'WAITING_ON_INFORMATION', 'EXTERNAL_DEPENDENCY', 'OTHER']
// Mirrors app/services/tasks.TRANSITIONS (the server is authoritative): action -> [allowed from, managers only]
const TRANSITIONS = {
  start: [['OPEN'], false], block: [['OPEN', 'IN_PROGRESS'], false], unblock: [['BLOCKED'], false],
  complete: [['OPEN', 'IN_PROGRESS'], false], cancel: [['OPEN', 'IN_PROGRESS', 'BLOCKED'], true], reopen: [['COMPLETED', 'CANCELLED'], true],
}

function useEmployeeOptions(enabled, term) {
  const result = useApi(enabled ? '/employees' : null, cleanParams({ status: 'ACTIVE', q: term.trim(), page_size: 50 }))
  return (result.data?.items ?? []).map((e) => ({ value: String(e.id), label: `${e.full_name} (${e.employee_code}) — ${e.department.name}` }))
}

function TaskForm({ onClose, onSaved }) {
  const [form, setForm] = useState({ title: '', description: '', task_type: 'FOLLOW_UP', priority: 'MEDIUM', due_date: '', assignee_employee_id: '' })
  const [term, setTerm] = useState('')
  const employees = useEmployeeOptions(true, useDebounced(term))
  const [local, setLocal] = useState({})
  const action = useAction()
  const errors = { ...local, ...action.errors }
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    if (form.title.trim().length < 3) return setLocal({ title: 'At least 3 characters' })
    setLocal({})
    const body = cleanParams({ ...form, title: form.title.trim(), description: form.description.trim(),
      assignee_employee_id: form.assignee_employee_id ? Number(form.assignee_employee_id) : null })
    await action.run(() => api.post('/tasks', body), { success: 'Task created', onSuccess: onSaved })
  }
  return (
    <Dialog open title="New task" onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="task-form" loading={action.busy}>Create task</Button></>}>
      <form id="task-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        <TextInput label="Title" required value={form.title} onChange={set('title')} error={errors.title} maxLength={150} className="sm:col-span-2" />
        <TextArea label="Description" value={form.description} onChange={set('description')} error={errors.description} maxLength={2000} className="sm:col-span-2" />
        <Select label="Type" value={form.task_type} onChange={set('task_type')} options={TYPES} />
        <Select label="Priority" value={form.priority} onChange={set('priority')} options={PRIORITIES} />
        <TextInput label="Due date" type="date" value={form.due_date} onChange={set('due_date')} error={errors.due_date} />
        <div>
          <TextInput label="Find assignee" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Type a name or code" maxLength={100} />
          <Select label="Assignee" value={form.assignee_employee_id} onChange={set('assignee_employee_id')} options={employees} placeholder="Unassigned"
            error={errors.assignee_employee_id} className="mt-2" hint="Active employees in your scope with a login" />
        </div>
      </form>
    </Dialog>
  )
}

function TaskDetail({ taskId, onClose, onChanged }) {
  const detail = useApi(`/tasks/${taskId}`)
  const [mode, setMode] = useState(null)
  const [fields, setFields] = useState({ block_reason: 'WAITING_ON_INFORMATION', block_details: '', completion_summary: '', cancellation_reason: '', assignee: '' })
  const [term, setTerm] = useState('')
  const debounced = useDebounced(term)
  const action = useAction()
  const task = detail.data
  const employees = useEmployeeOptions(mode === 'assign', debounced)

  const transition = async (name) => {
    const body = { action: name, expected_version: task.version }
    if (name === 'block') Object.assign(body, cleanParams({ block_reason: fields.block_reason, block_details: fields.block_details.trim() }))
    if (name === 'complete' && fields.completion_summary.trim()) body.completion_summary = fields.completion_summary.trim()
    if (name === 'cancel') {
      if (fields.cancellation_reason.trim().length < 5) return
      body.cancellation_reason = fields.cancellation_reason.trim()
    }
    if (await action.run(() => api.post(`/tasks/${task.id}/transition`, body), { success: DONE[name] })) {
      setMode(null)
      detail.reload()
      onChanged()
    }
  }
  const assign = async () => {
    if (!fields.assignee) return
    if (await action.run(() => api.post(`/tasks/${task.id}/assign`, { assignee_employee_id: Number(fields.assignee), expected_version: task.version }), { success: 'Task assigned' })) {
      setMode(null)
      detail.reload()
      onChanged()
    }
  }
  const available = task ? Object.entries(TRANSITIONS).filter(([, [from, managersOnly]]) => from.includes(task.status) && (!managersOnly || task.can_manage)).map(([name]) => name) : []
  const needsInput = ['block', 'complete', 'cancel']

  return (
    <Dialog open title={task ? task.title : 'Task'} onClose={onClose} wide>
      <AsyncContent result={detail}>
        {(t) => (
          <div className="space-y-4">
            <DetailList items={[
              ['Status', <StatusBadge key="s" value={t.status} />], ['Priority', <PriorityBadge key="p" value={t.priority} />],
              ['Type', humanize(t.task_type)], ['Assignee', t.assignee ? `${t.assignee.full_name} (${t.assignee.employee_code})` : 'Unassigned'],
              ['Due', `${formatDate(t.due_date)}${t.is_overdue ? ' (overdue)' : ''}`], ['Completed', formatDateTime(t.completed_at)],
              ...(t.status === 'BLOCKED' ? [['Blocked because', humanize(t.block_reason)]] : []),
            ]} />
            {t.description && <p className="whitespace-pre-line text-sm text-slate-800">{t.description}</p>}
            {t.block_details && <p className="text-sm text-slate-600">Block details: {t.block_details}</p>}
            {t.completion_summary && <p className="text-sm text-slate-600">Completion summary: {t.completion_summary}</p>}
            {t.cancellation_reason && <p className="text-sm text-slate-600">Cancelled: {t.cancellation_reason}</p>}

            {!mode && (available.length > 0 || t.can_manage) && (
              <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                {available.map((name) => (
                  <Button key={name} size="sm" variant={name === 'cancel' ? 'secondary' : 'primary'} loading={action.busy && !needsInput.includes(name)}
                    onClick={() => (needsInput.includes(name) ? setMode(name) : transition(name))}>{humanize(name)}</Button>
                ))}
                {t.can_manage && ['OPEN', 'IN_PROGRESS', 'BLOCKED'].includes(t.status) && (
                  <Button size="sm" variant="secondary" icon={UserPlus} onClick={() => setMode('assign')}>{t.assignee ? 'Reassign' : 'Assign'}</Button>
                )}
              </div>
            )}
            {mode && (
              <div className="space-y-3 border-t border-slate-100 pt-4">
                {mode === 'block' && (
                  <>
                    <Select label="Why is it blocked?" value={fields.block_reason} onChange={(e) => setFields((f) => ({ ...f, block_reason: e.target.value }))} options={BLOCK_REASONS} />
                    <TextArea label="Details" value={fields.block_details} maxLength={500} onChange={(e) => setFields((f) => ({ ...f, block_details: e.target.value }))} hint="Optional, 5–500 characters" />
                  </>
                )}
                {mode === 'complete' && <TextArea label="Completion summary" value={fields.completion_summary} maxLength={2000} onChange={(e) => setFields((f) => ({ ...f, completion_summary: e.target.value }))} hint="Optional" />}
                {mode === 'cancel' && <TextArea label="Cancellation reason" required value={fields.cancellation_reason} maxLength={500}
                  onChange={(e) => setFields((f) => ({ ...f, cancellation_reason: e.target.value }))}
                  error={fields.cancellation_reason && fields.cancellation_reason.trim().length < 5 ? 'At least 5 characters' : undefined} />}
                {mode === 'assign' && (
                  <>
                    <TextInput label="Find employee" value={term} onChange={(e) => setTerm(e.target.value)} maxLength={100} />
                    <Select label="Assignee" value={fields.assignee} onChange={(e) => setFields((f) => ({ ...f, assignee: e.target.value }))} options={employees} placeholder="Select…" />
                  </>
                )}
                {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
                <div className="flex justify-end gap-2">
                  <Button variant="secondary" onClick={() => setMode(null)} disabled={action.busy}>Back</Button>
                  <Button onClick={() => (mode === 'assign' ? assign() : transition(mode))} loading={action.busy}>Confirm</Button>
                </div>
              </div>
            )}
            {t.recent_history.length > 0 && (
              <div className="border-t border-slate-100 pt-4">
                <h3 className="mb-2 text-xs font-semibold uppercase text-slate-500">Recent history</h3>
                <ol className="space-y-1 text-sm text-slate-700">
                  {t.recent_history.map((e) => (
                    <li key={e.id}>{formatDateTime(e.created_at)} — {humanize(e.event_type)}{e.to_status ? ` → ${humanize(e.to_status)}` : ''}</li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        )}
      </AsyncContent>
    </Dialog>
  )
}

export default function Tasks() {
  const { user } = useAuth()
  const canCreate = isStaff(user)
  const [searchParams, setSearchParams] = useSearchParams()
  const { filters, page, setPage, update, reset } = useListState({ q: '', status: '', priority: '', overdue: '', assigned_to_me: false, sort: 'updated' })
  const q = useDebounced(filters.q)
  const list = useApi('/tasks', cleanParams({ ...filters, q: q.trim(), page, page_size: 20 }))
  const summary = useApi('/tasks/summary')
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState(null)
  const focus = searchParams.get('focus')
  const shownId = selected ?? (focus ? Number(focus) : null) // ?focus=id opens a task linked from elsewhere
  const close = () => {
    setSelected(null)
    if (focus) setSearchParams({})
  }
  const refresh = () => { list.reload(); summary.reload() }

  const columns = [
    { key: 'title', header: 'Task', render: (t) => <span className="font-medium text-slate-900">{t.title}</span> },
    { key: 'priority', header: 'Priority', render: (t) => <PriorityBadge value={t.priority} /> },
    { key: 'status', header: 'Status', render: (t) => <StatusBadge value={t.status} /> },
    { key: 'assignee', header: 'Assignee', render: (t) => t.assignee?.full_name ?? 'Unassigned' },
    { key: 'due', header: 'Due', render: (t) => <span className={t.is_overdue ? 'font-medium text-red-700' : ''}>{formatDate(t.due_date)}{t.is_overdue ? ' (overdue)' : ''}</span> },
    { key: 'type', header: 'Type', render: (t) => humanize(t.task_type) },
  ]

  return (
    <div>
      <PageHeader title="Tasks" description={canCreate ? 'Workforce tasks you manage or are assigned.' : 'Tasks assigned to you.'}
        actions={canCreate && <Button icon={Plus} onClick={() => setCreating(true)}>New task</Button>} />
      <AsyncContent result={summary} skeleton={2}>
        {(s) => (
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <MetricCard label="Open" value={s.by_status.OPEN} />
            <MetricCard label="In progress" value={s.by_status.IN_PROGRESS} tone="teal" />
            <MetricCard label="Blocked" value={s.by_status.BLOCKED} tone="amber" />
            <MetricCard label="Overdue" value={s.overdue} tone="red" hint={`${s.due_within_7_days} due within 7 days`} />
          </div>
        )}
      </AsyncContent>
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="Search" placeholder="Title" value={filters.q} maxLength={100} onChange={(e) => update({ q: e.target.value })} className="w-56" />
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-36" />
          <Select label="Priority" value={filters.priority} onChange={(e) => update({ priority: e.target.value })} options={PRIORITIES} placeholder="All" className="w-32" />
          <Select label="Overdue" value={filters.overdue} onChange={(e) => update({ overdue: e.target.value })} placeholder="Any" className="w-32"
            options={[{ value: 'true', label: 'Overdue' }, { value: 'false', label: 'Not overdue' }]} />
          <Select label="Sort" value={filters.sort} onChange={(e) => update({ sort: e.target.value })} className="w-36"
            options={[{ value: 'updated', label: 'Recently updated' }, { value: 'due', label: 'Due date' }, { value: 'priority', label: 'Priority' }, { value: 'created', label: 'Newest' }]} />
          <div className="pb-2"><Checkbox label="Assigned to me" checked={filters.assigned_to_me} onChange={(e) => update({ assigned_to_me: e.target.checked })} /></div>
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No tasks match" />}>
          {(d) => (<><DataTable caption="Tasks" columns={columns} rows={d.items} onRowClick={(t) => setSelected(t.id)} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {creating && <TaskForm onClose={() => setCreating(false)} onSaved={() => { setCreating(false); refresh() }} />}
      {shownId && <TaskDetail taskId={shownId} onClose={close} onChanged={refresh} />}
    </div>
  )
}
