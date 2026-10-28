import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { isHrAdmin } from '../lib/navigation'
import { formatDate, formatDateTime, humanize } from '../lib/format'
import {
  AsyncContent, Button, Card, DataTable, DetailList, Dialog, EmptyState, FilterBar, MetricCard, Notice, PageHeader,
  Pagination, PriorityBadge, Select, StatusBadge, TextArea, TextInput,
} from '../components/ui'

const DISCLAIMER = 'Risk alerts are review aids based on statistical estimates. They are not proof of an employee’s intentions and must not be the basis for any automatic employment decision.'

function AlertDetail({ alert, onClose, onChanged }) {
  const navigate = useNavigate()
  const [mode, setMode] = useState(null)
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const action = useAction()
  const active = ['OPEN', 'ACKNOWLEDGED'].includes(alert.status)

  const submit = async () => {
    if (mode !== 'acknowledge' && reason.trim().length < 5) return
    const request = mode === 'acknowledge'
      ? api.post(`/risk-monitoring/alerts/${alert.id}/acknowledge`, cleanParams({ note: note.trim() }))
      : api.post(`/risk-monitoring/alerts/${alert.id}/resolve`, cleanParams({ outcome: mode, reason: reason.trim(), note: note.trim() }))
    await action.run(() => request, { success: `Alert ${mode === 'acknowledge' ? 'acknowledged' : humanize(mode).toLowerCase()}`, onSuccess: onChanged })
  }

  return (
    <Dialog open title={`Risk alert #${alert.id}`} onClose={onClose} wide
      footer={active && !mode && (
        <>
          <Button variant="secondary" onClick={() => navigate(`/interventions?alert=${alert.id}&employee=${alert.employee.id}`)}>Plan intervention</Button>
          {alert.status === 'OPEN' && <Button variant="secondary" onClick={() => setMode('acknowledge')}>Acknowledge</Button>}
          <Button variant="secondary" onClick={() => setMode('DISMISSED')}>Dismiss</Button>
          <Button onClick={() => setMode('RESOLVED')}>Resolve</Button>
        </>
      )}>
      <DetailList items={[
        ['Employee', `${alert.employee.full_name} (${alert.employee.employee_code})`],
        ['Status', <StatusBadge key="s" value={alert.status} />], ['Priority', <PriorityBadge key="p" value={alert.priority} />],
        ['Trigger', humanize(alert.trigger_type)], ['First detected', formatDateTime(alert.first_detected_at)],
        ['Review deadline', `${formatDateTime(alert.sla?.review_due_at)} (${humanize(alert.sla?.state)})`],
        ['Model version', alert.model_version], ['Events', alert.event_count],
      ]} />
      <div className="mt-4"><Notice>{DISCLAIMER}</Notice></div>
      {mode && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          {mode !== 'acknowledge' && (
            <TextArea label={mode === 'RESOLVED' ? 'Resolution reason' : 'Reason for dismissal'} required value={reason} maxLength={500}
              onChange={(e) => setReason(e.target.value)} error={reason && reason.trim().length < 5 ? 'At least 5 characters' : action.errors.reason} />
          )}
          <TextArea label="Reviewer note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} hint="Optional; visible to reviewers only" />
          {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setMode(null)} disabled={action.busy}>Back</Button>
            <Button onClick={submit} loading={action.busy}>Confirm {mode === 'acknowledge' ? 'acknowledgement' : humanize(mode).toLowerCase()}</Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

export default function Risk() {
  const { user } = useAuth()
  const summary = useApi('/risk-monitoring/summary')
  const departments = useApi(isHrAdmin(user) ? '/departments' : null, { page_size: 100 })
  const { filters, page, setPage, update, reset } = useListState({ status: '', priority: '', department_id: '', detected_from: '', detected_to: '' })
  const list = useApi('/risk-monitoring/alerts', cleanParams({ ...filters, page, page_size: 20 }))
  const [selected, setSelected] = useState(null)

  const columns = [
    { key: 'id', header: 'Alert', render: (a) => `#${a.id}` },
    { key: 'employee', header: 'Employee', render: (a) => `${a.employee.full_name} (${a.employee.employee_code})` },
    { key: 'priority', header: 'Priority', render: (a) => <PriorityBadge value={a.priority} /> },
    { key: 'status', header: 'Status', render: (a) => <StatusBadge value={a.status} /> },
    { key: 'sla', header: 'Review deadline', render: (a) => <span className="flex flex-col gap-0.5"><StatusBadge value={a.sla?.state} /><span className="text-xs text-slate-500">{formatDate(a.sla?.review_due_at)}</span></span> },
    { key: 'first_detected_at', header: 'First detected', render: (a) => formatDate(a.first_detected_at) },
  ]

  return (
    <div>
      <PageHeader title="Risk monitoring" description="Alerts raised from stored attrition-risk estimates, for supportive review. You never see alerts about yourself." />
      <div className="mb-4"><Notice tone="warning">{DISCLAIMER}</Notice></div>
      <AsyncContent result={summary} skeleton={2}>
        {(s) => (
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <MetricCard label="Open" value={s.alerts_by_status.OPEN ?? 0} tone="blue" />
            <MetricCard label="Acknowledged" value={s.alerts_by_status.ACKNOWLEDGED ?? 0} tone="amber" />
            <MetricCard label="Past review deadline" value={s.active_alerts_by_sla_state?.OVERDUE ?? 0} tone="red" />
            <MetricCard label="Created (30 days)" value={s.alerts_created_last_30_days} tone="slate" />
          </div>
        )}
      </AsyncContent>
      <Card>
        <FilterBar onReset={reset}>
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={['OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED']} placeholder="All" className="w-40" />
          <Select label="Priority" value={filters.priority} onChange={(e) => update({ priority: e.target.value })} options={['LOW', 'MEDIUM', 'HIGH']} placeholder="All" className="w-32" />
          {isHrAdmin(user) && (
            <Select label="Department" value={filters.department_id} onChange={(e) => update({ department_id: e.target.value })} placeholder="All" className="w-48"
              options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
          )}
          <TextInput label="Detected from" type="date" value={filters.detected_from} onChange={(e) => update({ detected_from: e.target.value })} className="w-40" />
          <TextInput label="to" type="date" value={filters.detected_to} onChange={(e) => update({ detected_to: e.target.value })} className="w-40" />
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No alerts match" />}>
          {(d) => (<><DataTable caption="Risk alerts" columns={columns} rows={d.items} onRowClick={setSelected} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {selected && <AlertDetail alert={selected} onClose={() => setSelected(null)} onChanged={() => { setSelected(null); list.reload(); summary.reload() }} />}
    </div>
  )
}
