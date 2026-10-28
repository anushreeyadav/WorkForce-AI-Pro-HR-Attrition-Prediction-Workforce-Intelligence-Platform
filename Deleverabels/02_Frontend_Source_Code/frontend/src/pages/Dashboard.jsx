import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Building2, CalendarDays, ClipboardList, Users } from 'lucide-react'
import { useAuth } from '../auth/context'
import { useApi } from '../hooks/useApi'
import { isHrAdmin, isStaff } from '../lib/navigation'
import { formatDate, formatDateTime, formatNumber, formatPercent, humanize, isoDate, monthLabel } from '../lib/format'
import { describeError } from '../api/client'
import { AsyncContent, Card, EmptyState, MetricCard, Notice, PageHeader, PriorityBadge, StatusBadge } from '../components/ui'

const CHART_BLUE = '#2563eb'
const CHART_TEAL = '#0d9488'

// A metric whose value comes from one request: shows "…" while loading and "Unavailable" (with the reason as a
// hint) when the request fails. A failure is never shown as 0.
function Metric({ result, label, icon, tone, value, hint }) {
  let shown = '…'
  let note = hint
  if (result.error) {
    shown = 'Unavailable'
    note = describeError(result.error)
  } else if (result.data) {
    shown = value(result.data)
  }
  return <MetricCard label={label} value={shown} hint={note} icon={icon} tone={tone} />
}

function DepartmentChart({ staff }) {
  // Staff: current active headcount per department from workforce analytics. Others: the directory's employee
  // count per department (all statuses), which every role may read.
  const overview = useApi(staff ? '/workforce-analytics/overview' : null)
  const departments = useApi(staff ? null : '/departments', { page_size: 100 })
  const result = staff ? overview : departments
  const rows = staff
    ? (overview.data?.data?.by_department ?? []).map((d) => ({ name: d.department_name ?? `#${d.department_id}`, value: d.active_employees }))
    : (departments.data?.items ?? []).map((d) => ({ name: d.name, value: d.employee_count }))
  return (
    <Card title="Department distribution" description={staff ? 'Active employees per department (current records)' : 'Employees on record per department (all statuses)'}>
      <AsyncContent result={result} isEmpty={() => rows.length === 0} empty={<EmptyState title="No departments yet" />}>
        {() => (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={rows.length > 6 ? -30 : 0} textAnchor={rows.length > 6 ? 'end' : 'middle'} height={rows.length > 6 ? 60 : 30} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="value" name="Employees" fill={CHART_BLUE} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </AsyncContent>
    </Card>
  )
}

function AttendanceCard({ staff }) {
  const range = { date_from: isoDate(-29), date_to: isoDate(0) }
  const result = useApi(staff ? '/attendance/summary' : '/attendance/me/summary', range)
  return (
    <Card title="Attendance — last 30 days" description={staff ? 'Records in your scope' : 'Your own records'}>
      <AsyncContent result={result} isEmpty={(d) => d.records === 0}
        empty={<EmptyState title="No attendance records" message="No attendance was recorded in the last 30 days. Missing days are not counted as absences." />}>
        {(d) => {
          const rows = [
            { name: 'Present', value: d.present_days }, { name: 'Late', value: d.late_days },
            { name: 'Half day', value: d.half_days }, { name: 'Absent', value: d.absent_days }, { name: 'On leave', value: d.on_leave_days },
          ]
          return (
            <div>
              <p className="text-sm text-slate-600">
                Attendance rate <span className="text-lg font-semibold text-slate-900">{formatPercent(d.attendance_percentage)}</span>
                <span className="ml-2 text-xs text-slate-500">({formatNumber(d.records)} records, {formatNumber(d.open_records)} still checked in)</span>
              </p>
              <div className="mt-3 h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={rows} layout="vertical" margin={{ left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" width={70} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar dataKey="value" name="Days" fill={CHART_TEAL} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )
        }}
      </AsyncContent>
    </Card>
  )
}

function WorkforceTrend() {
  const result = useApi('/workforce-analytics/trends')
  const rows = (result.data?.data?.monthly ?? []).map((m) => ({ month: m.month, active: m.active_employees, joiners: m.joiners, departures: m.recorded_departures }))
  const observed = rows.filter((r) => r.active !== null).length
  return (
    <Card title="Workforce trend" description="Month-end active headcount from captured snapshots; joiners and recorded departures per month">
      <AsyncContent result={result} isEmpty={() => observed === 0}
        empty={<EmptyState title="No snapshots captured yet" message="Headcount history comes only from workforce snapshots. Capture snapshots in Workforce analytics to see this trend." />}>
        {() => (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="active" name="Active (month-end)" stroke={CHART_BLUE} strokeWidth={2} connectNulls={false} dot={{ r: 2 }} />
                <Line type="monotone" dataKey="joiners" name="Joiners" stroke={CHART_TEAL} strokeWidth={1.5} dot={false} />
                <Line type="monotone" dataKey="departures" name="Recorded departures" stroke="#d97706" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </AsyncContent>
    </Card>
  )
}

function RiskCard() {
  const summary = useApi('/risk-monitoring/summary')
  const stability = useApi('/workforce-analytics/stability')
  return (
    <Card title="Risk & stability" description="Indicators for review, not conclusions about individuals"
      actions={<Link to="/risk" className="text-xs font-medium text-blue-700 hover:underline">Open risk monitoring</Link>}>
      <AsyncContent result={summary}>
        {(d) => (
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-xs font-medium uppercase text-slate-500">Active alerts by priority</p>
              <ul className="mt-1 space-y-1">
                {Object.entries(d.active_alerts_by_priority).map(([p, n]) => (
                  <li key={p} className="flex justify-between"><PriorityBadge value={p} /><span>{n}</span></li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium uppercase text-slate-500">Review deadlines</p>
              <ul className="mt-1 space-y-1">
                {Object.entries(d.active_alerts_by_sla_state ?? {}).map(([s, n]) => (
                  <li key={s} className="flex justify-between"><StatusBadge value={s} /><span>{n}</span></li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </AsyncContent>
      <div className="mt-4 border-t border-slate-100 pt-3 text-sm">
        <AsyncContent result={stability} skeleton={1}>
          {(e) => e.data.status === 'ok' ? (
            <p>Stability indicator <span className="font-semibold">{formatNumber(e.data.score)}</span> / 100</p>
          ) : (
            <p className="text-slate-600">Stability indicator: {humanize(e.data.status)}{e.data.reason ? ` — ${e.data.reason}` : ''}</p>
          )}
        </AsyncContent>
      </div>
    </Card>
  )
}

function PayrollCard() {
  const result = useApi('/payroll/periods', { page_size: 1 })
  return (
    <Card title="Latest payroll period" actions={<Link to="/payroll" className="text-xs font-medium text-blue-700 hover:underline">Open payroll</Link>}>
      <AsyncContent result={result} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No payroll periods yet" />}>
        {(d) => {
          const period = d.items[0]
          return (
            <div className="text-sm">
              <p className="font-medium text-slate-900">{monthLabel(period.year, period.month)}</p>
              <ul className="mt-2 space-y-1">
                {Object.entries(period.record_counts).map(([s, n]) => (
                  <li key={s} className="flex justify-between"><StatusBadge value={s} /><span>{n} record{n === 1 ? '' : 's'}</span></li>
                ))}
                {Object.keys(period.record_counts).length === 0 && <li className="text-slate-500">Not calculated yet.</li>}
              </ul>
            </div>
          )
        }}
      </AsyncContent>
    </Card>
  )
}

function ActivityCard() {
  const result = useApi('/workforce-activity/feed', { page_size: 6 })
  return (
    <Card title="Recent activity" description="Recorded events in your scope (last 7 days)"
      actions={<Link to="/activity" className="text-xs font-medium text-blue-700 hover:underline">View all</Link>}>
      <AsyncContent result={result} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No recorded activity this week" />}>
        {(d) => (
          <ul className="divide-y divide-slate-100 text-sm">
            {d.items.map((item) => (
              <li key={item.event_id} className="flex items-start justify-between gap-3 py-2">
                <div>
                  <p className="text-slate-800">{item.description}</p>
                  <p className="text-xs text-slate-500">{humanize(item.category)} · {formatDateTime(item.recorded_at)}</p>
                </div>
                {item.priority && <PriorityBadge value={item.priority} />}
              </li>
            ))}
          </ul>
        )}
      </AsyncContent>
    </Card>
  )
}

function TasksCard() {
  const result = useApi('/tasks', { status: 'OPEN', sort: 'priority', page_size: 5 })
  return (
    <Card title="Open tasks by priority" actions={<Link to="/tasks" className="text-xs font-medium text-blue-700 hover:underline">All tasks</Link>}>
      <AsyncContent result={result} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No open tasks" />}>
        {(d) => (
          <ul className="divide-y divide-slate-100 text-sm">
            {d.items.map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <Link to={`/tasks?focus=${task.id}`} className="block truncate font-medium text-slate-800 hover:text-blue-700">{task.title}</Link>
                  <p className="text-xs text-slate-500">Due {formatDate(task.due_date)}{task.is_overdue ? ' · overdue' : ''}</p>
                </div>
                <PriorityBadge value={task.priority} />
              </li>
            ))}
          </ul>
        )}
      </AsyncContent>
    </Card>
  )
}

export default function Dashboard() {
  const { user } = useAuth()
  const staff = isStaff(user)
  const hrAdmin = isHrAdmin(user)
  const employees = useApi('/employees', { status: 'ACTIVE', page_size: 1 })
  const departments = useApi('/departments', { is_active: true, page_size: 1 })
  const pendingLeave = useApi(staff ? '/leave/requests' : null, { status: 'PENDING', page_size: 1 })
  const balances = useApi(staff ? null : '/leave/balances/me')
  const taskSummary = useApi('/tasks/summary')

  return (
    <div>
      <PageHeader title={`Welcome, ${user.full_name.split(' ')[0]}`}
        description={staff ? 'Workforce overview for your scope. Figures come from recorded data; AI risk indicators are estimates for review.' : 'Your attendance, leave and tasks at a glance.'} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric result={employees} label={staff ? 'Active employees' : 'Your record'} icon={Users}
          value={(d) => (staff ? formatNumber(d.total) : d.total ? 'Active' : 'Not active')} hint={staff ? (hrAdmin ? 'Organization-wide' : 'In your department') : undefined} />
        <Metric result={departments} label="Active departments" icon={Building2} tone="teal" value={(d) => formatNumber(d.total)} />
        {staff ? (
          <Metric result={pendingLeave} label="Leave awaiting review" icon={CalendarDays} tone="amber" value={(d) => formatNumber(d.total)} />
        ) : (
          <Metric result={balances} label="Leave days available" icon={CalendarDays} tone="amber"
            value={(d) => (d.length ? formatNumber(d.reduce((sum, b) => sum + b.available_days, 0)) : 'No policy')} hint="All leave types, this year" />
        )}
        <Metric result={taskSummary} label="Active tasks" icon={ClipboardList} tone="slate"
          value={(d) => formatNumber((d.by_status.OPEN ?? 0) + (d.by_status.IN_PROGRESS ?? 0) + (d.by_status.BLOCKED ?? 0))}
          hint={taskSummary.data ? `${taskSummary.data.overdue} overdue` : undefined} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <DepartmentChart staff={staff} />
        <AttendanceCard staff={staff} />
        {staff && <WorkforceTrend />}
        {staff && <RiskCard />}
        {staff && <ActivityCard />}
        <TasksCard />
        {hrAdmin && <PayrollCard />}
      </div>
      {staff && (
        <div className="mt-6">
          <Notice>AI risk scores and rule-based recommendations are indicators to support human review. They are not evidence of anyone&apos;s intentions and must not be used for automatic decisions.</Notice>
        </div>
      )}
    </div>
  )
}
