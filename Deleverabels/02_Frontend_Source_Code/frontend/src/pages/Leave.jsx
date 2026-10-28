import { useState } from 'react'
import { Check, Plus, X } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { isHrAdmin, isStaff } from '../lib/navigation'
import { formatDate, formatNumber } from '../lib/format'
import {
  AsyncContent, Button, Card, ConfirmDialog, DataTable, Dialog, EmptyState, FilterBar, PageHeader, Pagination,
  Select, StatusBadge, Tabs, TextArea, TextInput,
} from '../components/ui'

const STATUSES = ['PENDING', 'APPROVED', 'REJECTED', 'CANCELLED']

function RequestForm({ onClose, onSaved }) {
  const types = useApi('/leave/types', { page_size: 100 })
  const [form, setForm] = useState({ leave_type_id: '', start_date: '', end_date: '', reason: '' })
  const [local, setLocal] = useState({})
  const action = useAction()
  const errors = { ...local, ...action.errors }
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    const missing = {}
    if (!form.leave_type_id) missing.leave_type_id = 'Required'
    if (!form.start_date) missing.start_date = 'Required'
    if (!form.end_date) missing.end_date = 'Required'
    if (form.start_date && form.end_date && form.end_date < form.start_date) missing.end_date = 'Must be on or after the start date'
    if (form.reason.trim().length < 5) missing.reason = 'At least 5 characters'
    setLocal(missing)
    if (Object.keys(missing).length) return
    await action.run(() => api.post('/leave/requests', { ...form, leave_type_id: Number(form.leave_type_id), reason: form.reason.trim() }),
      { success: 'Leave request submitted', onSuccess: onSaved })
  }

  return (
    <Dialog open title="Request leave" onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="leave-form" loading={action.busy}>Submit request</Button></>}>
      <form id="leave-form" onSubmit={submit} noValidate className="space-y-4">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
        <Select label="Leave type" required value={form.leave_type_id} onChange={set('leave_type_id')} error={errors.leave_type_id} placeholder="Select…"
          options={(types.data?.items ?? []).map((t) => ({ value: String(t.id), label: `${t.name}${t.is_paid ? '' : ' (unpaid)'}` }))} />
        <div className="grid grid-cols-2 gap-3">
          <TextInput label="First day" type="date" required value={form.start_date} onChange={set('start_date')} error={errors.start_date} />
          <TextInput label="Last day" type="date" required value={form.end_date} onChange={set('end_date')} error={errors.end_date} />
        </div>
        <TextArea label="Reason" required value={form.reason} onChange={set('reason')} error={errors.reason} maxLength={500}
          hint="Shared with your reviewer only. Avoid medical details unless your policy requires them." />
      </form>
    </Dialog>
  )
}

function MyRequests() {
  const { filters, page, setPage, update, reset } = useListState({ status: '' })
  const list = useApi('/leave/requests/me', cleanParams({ ...filters, page, page_size: 20 }))
  const [creating, setCreating] = useState(false)
  const [cancelling, setCancelling] = useState(null)
  const action = useAction()

  const cancel = async () => {
    if (await action.run(() => api.post(`/leave/requests/${cancelling.id}/cancel`, {}), { success: 'Leave request cancelled' })) {
      setCancelling(null)
      list.reload()
    }
  }
  const columns = [
    { key: 'type', header: 'Type', render: (r) => r.leave_type.name },
    { key: 'dates', header: 'Dates', render: (r) => `${formatDate(r.start_date)} – ${formatDate(r.end_date)}` },
    { key: 'working_days', header: 'Working days' },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'review_comment', header: 'Reviewer comment', render: (r) => r.review_comment || '—' },
    { key: 'actions', header: <span className="sr-only">Actions</span>, render: (r) => ['PENDING', 'APPROVED'].includes(r.status) && (
      <Button variant="ghost" size="sm" onClick={() => setCancelling(r)}>Cancel</Button>
    ) },
  ]
  return (
    <Card actions={<Button icon={Plus} size="sm" onClick={() => setCreating(true)}>Request leave</Button>} title="My requests">
      <FilterBar onReset={reset}>
        <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-40" />
      </FilterBar>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No leave requests" />}>
        {(d) => (<><DataTable caption="My leave requests" columns={columns} rows={d.items} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      {creating && <RequestForm onClose={() => setCreating(false)} onSaved={() => { setCreating(false); list.reload() }} />}
      <ConfirmDialog open={Boolean(cancelling)} title="Cancel this leave request?" danger confirmLabel="Cancel request" busy={action.busy}
        onCancel={() => setCancelling(null)} onConfirm={cancel}
        message="Approved leave can only be cancelled before it starts. Your reviewer is notified." />
    </Card>
  )
}

function Balances() {
  const result = useApi('/leave/balances/me')
  const columns = [
    { key: 'type', header: 'Leave type', render: (b) => b.leave_type.name },
    { key: 'allocated_days', header: 'Allocated' }, { key: 'carried_forward_days', header: 'Carried forward' },
    { key: 'used_days', header: 'Used' }, { key: 'pending_days', header: 'Pending' },
    { key: 'available_days', header: 'Available', render: (b) => <span className="font-semibold">{formatNumber(b.available_days)}</span> },
  ]
  return (
    <Card title={`Balances ${result.data?.[0]?.year ?? ''}`} description="Working days, per the leave policy that applies to you">
      <AsyncContent result={result} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No leave policy applies to you yet" />}>
        {(d) => <DataTable caption="Leave balances" columns={columns} rows={d} rowKey={(b) => `${b.leave_type.id}-${b.year}`} />}
      </AsyncContent>
    </Card>
  )
}

function ReviewQueue({ canPickDepartment }) {
  const { filters, page, setPage, update, reset } = useListState({ status: 'PENDING', department_id: '', date_from: '', date_to: '' })
  const list = useApi('/leave/requests', cleanParams({ ...filters, page, page_size: 20 }))
  const departments = useApi(canPickDepartment ? '/departments' : null, { page_size: 100 })
  const [decision, setDecision] = useState(null)
  const [comment, setComment] = useState('')
  const action = useAction()
  const rejecting = decision?.kind === 'reject'

  const decide = async () => {
    if (rejecting && comment.trim().length < 5) return
    const body = comment.trim() ? { comment: comment.trim() } : {}
    if (await action.run(() => api.post(`/leave/requests/${decision.request.id}/${decision.kind}`, body),
      { success: rejecting ? 'Leave request rejected' : 'Leave request approved' })) {
      setDecision(null)
      setComment('')
      list.reload()
    }
  }
  const columns = [
    { key: 'employee', header: 'Employee', render: (r) => `${r.employee.full_name} (${r.employee.employee_code})` },
    { key: 'type', header: 'Type', render: (r) => r.leave_type.name },
    { key: 'dates', header: 'Dates', render: (r) => `${formatDate(r.start_date)} – ${formatDate(r.end_date)}` },
    { key: 'working_days', header: 'Days' },
    { key: 'reason', header: 'Reason', render: (r) => <span className="line-clamp-2">{r.reason}</span> },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'actions', header: <span className="sr-only">Actions</span>, render: (r) => r.status === 'PENDING' && (
      <div className="flex gap-1">
        <Button size="sm" icon={Check} onClick={() => setDecision({ kind: 'approve', request: r })}>Approve</Button>
        <Button size="sm" variant="secondary" icon={X} onClick={() => setDecision({ kind: 'reject', request: r })}>Reject</Button>
      </div>
    ) },
  ]
  return (
    <Card title="Review queue" description="You cannot review your own requests.">
      <FilterBar onReset={reset}>
        <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-40" />
        <TextInput label="Overlapping from" type="date" value={filters.date_from} onChange={(e) => update({ date_from: e.target.value })} className="w-40" />
        <TextInput label="to" type="date" value={filters.date_to} onChange={(e) => update({ date_to: e.target.value })} className="w-40" />
        {canPickDepartment && (
          <Select label="Department" value={filters.department_id} onChange={(e) => update({ department_id: e.target.value })} placeholder="All" className="w-48"
            options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
        )}
      </FilterBar>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="Nothing to review" />}>
        {(d) => (<><DataTable caption="Leave requests to review" columns={columns} rows={d.items} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      <ConfirmDialog open={Boolean(decision)} title={rejecting ? 'Reject leave request' : 'Approve leave request'} danger={rejecting}
        confirmLabel={rejecting ? 'Reject' : 'Approve'} busy={action.busy} onCancel={() => { setDecision(null); setComment('') }} onConfirm={decide}
        message={decision && `${decision.request.employee.full_name}: ${decision.request.leave_type.name}, ${formatDate(decision.request.start_date)} – ${formatDate(decision.request.end_date)} (${decision.request.working_days} working days).`}>
        <div className="mt-3">
          <TextArea label={rejecting ? 'Reason for rejection' : 'Comment'} required={rejecting} value={comment} maxLength={500}
            onChange={(e) => setComment(e.target.value)} error={rejecting && comment && comment.trim().length < 5 ? 'At least 5 characters' : action.errors.comment}
            hint={rejecting ? 'Shown to the employee (5–500 characters)' : 'Optional'} />
          {action.message && !action.errors.comment && <p role="alert" className="mt-2 text-sm text-red-700">{action.message}</p>}
        </div>
      </ConfirmDialog>
    </Card>
  )
}

export default function Leave() {
  const { user } = useAuth()
  const staff = isStaff(user)
  const [tab, setTab] = useState('mine')
  const tabs = [{ id: 'mine', label: 'My requests' }, { id: 'balances', label: 'Balances' }, ...(staff ? [{ id: 'review', label: 'Review queue' }] : [])]
  return (
    <div>
      <PageHeader title="Leave" description="Requests, balances and approvals." />
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === 'mine' && <MyRequests />}
      {tab === 'balances' && <Balances />}
      {tab === 'review' && <ReviewQueue canPickDepartment={isHrAdmin(user)} />}
    </div>
  )
}
