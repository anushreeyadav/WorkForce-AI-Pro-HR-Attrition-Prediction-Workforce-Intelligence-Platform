import { useState } from 'react'
import { LogIn, LogOut } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { isHrAdmin, isStaff } from '../lib/navigation'
import { formatDate, formatDateTime, formatMinutes, formatNumber, formatPercent, isoDate } from '../lib/format'
import {
  AsyncContent, Button, Card, ConfirmDialog, DataTable, EmptyState, FilterBar, MetricCard, PageHeader, Pagination,
  Select, StatusBadge, Tabs, TextInput,
} from '../components/ui'

const STATUSES = ['PRESENT', 'LATE', 'HALF_DAY', 'ABSENT', 'ON_LEAVE']

function SummaryRow({ result }) {
  return (
    <AsyncContent result={result} skeleton={2}>
      {(s) => (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard label="Attendance rate" value={formatPercent(s.attendance_percentage)} hint="Attended ÷ (attended + recorded absences)" />
          <MetricCard label="Attended days" value={formatNumber(s.attended_days)} tone="teal" hint={`${s.late_days} late, ${s.half_days} half days`} />
          <MetricCard label="Recorded absences" value={formatNumber(s.absent_days)} tone="amber" hint="Days with no record are not counted" />
          <MetricCard label="Overtime" value={formatMinutes(s.overtime_minutes)} tone="slate" hint={`${formatMinutes(s.work_minutes)} worked`} />
        </div>
      )}
    </AsyncContent>
  )
}

function DateFilters({ filters, update }) {
  return (
    <>
      <TextInput label="From" type="date" value={filters.date_from} onChange={(e) => update({ date_from: e.target.value })} className="w-40" />
      <TextInput label="To" type="date" value={filters.date_to} onChange={(e) => update({ date_to: e.target.value })} className="w-40" />
    </>
  )
}

const columns = (withEmployee) => [
  ...(withEmployee ? [{ key: 'employee', header: 'Employee', render: (r) => `${r.employee.full_name} (${r.employee.employee_code})` }] : []),
  { key: 'work_date', header: 'Date', render: (r) => formatDate(r.work_date) },
  { key: 'shift', header: 'Shift', render: (r) => r.shift?.name ?? 'Unscheduled' },
  { key: 'check_in', header: 'Check-in', render: (r) => formatDateTime(r.check_in) },
  { key: 'check_out', header: 'Check-out', render: (r) => (r.is_open ? <StatusBadge value="IN_PROGRESS" /> : formatDateTime(r.check_out)) },
  { key: 'work_minutes', header: 'Worked', render: (r) => formatMinutes(r.work_minutes) },
  { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
]

function MyAttendance() {
  const { filters, page, setPage, update, reset } = useListState({ date_from: isoDate(-29), date_to: isoDate(0) })
  const range = cleanParams({ date_from: filters.date_from, date_to: filters.date_to })
  const list = useApi('/attendance/me', { ...range, page, page_size: 20 })
  const summary = useApi('/attendance/me/summary', range)
  const shifts = useApi('/shifts', { page_size: 100 })
  const [confirm, setConfirm] = useState(null)
  const [shiftId, setShiftId] = useState('')
  const [breakMinutes, setBreakMinutes] = useState('')
  const action = useAction()
  const open = list.data?.items.find((r) => r.is_open)

  const perform = async () => {
    const ok = await action.run(
      () => (confirm === 'in'
        ? api.post('/attendance/check-in', cleanParams({ shift_id: shiftId ? Number(shiftId) : null }))
        : api.post('/attendance/check-out', cleanParams({ break_minutes: breakMinutes === '' ? null : Number(breakMinutes) }))),
      { success: confirm === 'in' ? 'Checked in' : 'Checked out' })
    if (ok) {
      setConfirm(null)
      list.reload()
      summary.reload()
    }
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-2">
        <Button icon={LogIn} onClick={() => setConfirm('in')} disabled={Boolean(open) || list.loading}>Check in</Button>
        <Button variant="secondary" icon={LogOut} onClick={() => setConfirm('out')} disabled={!open}>Check out</Button>
      </div>
      <SummaryRow result={summary} />
      <Card>
        <FilterBar onReset={reset}><DateFilters filters={filters} update={update} /></FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No attendance in this period" />}>
          {(d) => (<><DataTable caption="My attendance" columns={columns(false)} rows={d.items} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      <ConfirmDialog open={Boolean(confirm)} title={confirm === 'in' ? 'Check in now?' : 'Check out now?'} busy={action.busy}
        confirmLabel={confirm === 'in' ? 'Check in' : 'Check out'} onCancel={() => setConfirm(null)} onConfirm={perform}
        message="The server records the current time; it cannot be backdated here.">
        <div className="mt-3">
          {confirm === 'in' ? (
            <Select label="Shift" value={shiftId} onChange={(e) => setShiftId(e.target.value)} placeholder="Unscheduled work"
              options={(shifts.data?.items ?? []).map((s) => ({ value: String(s.id), label: `${s.name} (${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)})` }))} />
          ) : (
            <TextInput label="Break minutes" type="number" min={0} max={1440} value={breakMinutes} onChange={(e) => setBreakMinutes(e.target.value)}
              hint="Leave empty to use the shift's default break" />
          )}
          {action.message && <p role="alert" className="mt-2 text-sm text-red-700">{action.message}</p>}
        </div>
      </ConfirmDialog>
    </>
  )
}

function TeamAttendance({ canPickDepartment }) {
  const { filters, page, setPage, update, reset } = useListState({ date_from: isoDate(-6), date_to: isoDate(0), status: '', department_id: '' })
  const params = cleanParams(filters)
  const list = useApi('/attendance', { ...params, page, page_size: 20 })
  const summary = useApi('/attendance/summary', params)
  const departments = useApi(canPickDepartment ? '/departments' : null, { page_size: 100 })
  return (
    <>
      <SummaryRow result={summary} />
      <Card>
        <FilterBar onReset={reset}>
          <DateFilters filters={filters} update={update} />
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-36" />
          {canPickDepartment && (
            <Select label="Department" value={filters.department_id} onChange={(e) => update({ department_id: e.target.value })} placeholder="All" className="w-48"
              options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
          )}
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No attendance records match" />}>
          {(d) => (<><DataTable caption="Team attendance" columns={columns(true)} rows={d.items} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
    </>
  )
}

export default function Attendance() {
  const { user } = useAuth()
  const staff = isStaff(user)
  const [tab, setTab] = useState('mine')
  return (
    <div>
      <PageHeader title="Attendance" description="Check-ins, check-outs and recorded attendance. Days without a record are never counted as absences." />
      {staff && <Tabs active={tab} onChange={setTab} tabs={[{ id: 'mine', label: 'My attendance' }, { id: 'team', label: user.role === 'MANAGER' ? 'My department' : 'All records' }]} />}
      {tab === 'mine' ? <MyAttendance /> : <TeamAttendance canPickDepartment={isHrAdmin(user)} />}
    </div>
  )
}
