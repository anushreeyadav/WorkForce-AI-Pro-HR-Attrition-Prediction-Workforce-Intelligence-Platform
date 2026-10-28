import { useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Camera } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction } from '../hooks/useList'
import { isHrAdmin } from '../lib/navigation'
import { formatDate, formatNumber, formatPercent, humanize } from '../lib/format'
import {
  AsyncContent, Badge, Button, Card, ConfirmDialog, DataTable, EmptyState, MetricCard, Notice, PageHeader, Select, StatusBadge,
} from '../components/ui'

function Overview({ scope }) {
  const result = useApi('/workforce-analytics/overview', scope)
  return (
    <AsyncContent result={result} skeleton={2}>
      {({ data }) => (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard label="Active employees" value={formatNumber(data.current.active_employees)} hint={`${formatNumber(data.current.on_leave_employees)} on leave`} />
          <MetricCard label="Joiners (90 days)" value={formatNumber(data.joiners_last_90_days)} tone="teal" />
          <MetricCard label="Recorded departures (90 days)" value={formatNumber(data.recorded_departures_last_90_days)} tone="amber" hint="Dated when recorded" />
          <MetricCard label="Snapshots captured" value={formatNumber(data.snapshots.count)} tone="slate" hint={data.snapshots.latest_snapshot_date ? `Latest ${formatDate(data.snapshots.latest_snapshot_date)}` : 'None yet'} />
        </div>
      )}
    </AsyncContent>
  )
}

// Ratios from the API are fractions (0.12 = 12%); null means "not measurable", never 0.
const ratio = (value) => (value === null || value === undefined ? '—' : formatPercent(value * 100))

function Stability({ result }) {
  return (
    <Card title="Stability indicator" description="Heuristic (not a validated prediction) from headcount change, volatility and recorded turnover; not about individuals"
      actions={<Badge tone="neutral">Heuristic</Badge>}>
      <AsyncContent result={result}>
        {({ data }) => (
          <div className="text-sm">
            {data.status === 'ok' ? (
              <>
                <p className="text-3xl font-semibold text-slate-900">{formatNumber(data.score)}<span className="text-base font-normal text-slate-500"> / 100</span></p>
                <p className="text-xs text-slate-500">Higher is more stable</p>
                <ul className="mt-3 space-y-1 text-slate-600">
                  <li>Turnover component: {formatNumber(data.components.turnover)} (weight 50%)</li>
                  <li>Net-change component: {formatNumber(data.components.net_change)} (weight 25%)</li>
                  <li>Volatility component: {formatNumber(data.components.volatility)} (weight 25%)</li>
                </ul>
              </>
            ) : (
              <EmptyState title={humanize(data.status)} message={data.reason ?? 'Not enough snapshot history yet.'} />
            )}
            <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500">
              <li>Net change: {ratio(data.metrics.net_change_rate)} · Annualized turnover: {ratio(data.metrics.annualized_turnover)}</li>
              <li>Snapshots used: {data.metrics.observations} · Average active headcount: {data.metrics.average_active_headcount === null ? '—' : formatNumber(data.metrics.average_active_headcount)}</li>
              <li>Window {formatDate(data.window.from)} – {formatDate(data.window.to)} · Latest snapshot: {data.latest_snapshot_date ? formatDate(data.latest_snapshot_date) : 'none'}</li>
            </ul>
          </div>
        )}
      </AsyncContent>
    </Card>
  )
}

// ADMIN/HR, organization scope: the same indicator per department (computed as for each department alone).
function DepartmentStability({ result }) {
  const columns = [
    { key: 'department_name', header: 'Department', render: (d) => <span className="font-medium text-slate-900">{d.department_name}{d.is_active ? '' : ' (inactive)'}</span> },
    { key: 'score', header: 'Indicator', render: (d) => (d.status === 'ok' ? `${formatNumber(d.score)} / 100` : <StatusBadge value={d.status} />) },
    { key: 'reason', header: 'Why not scored', render: (d) => (d.status === 'ok' ? '—' : <span className="text-xs text-slate-600">{d.reason}</span>) },
    { key: 'net_change', header: 'Net change', render: (d) => ratio(d.metrics.net_change_rate) },
    { key: 'turnover', header: 'Annualized turnover', render: (d) => ratio(d.metrics.annualized_turnover) },
    { key: 'average', header: 'Avg. active', render: (d) => (d.metrics.average_active_headcount === null ? '—' : formatNumber(d.metrics.average_active_headcount)) },
    { key: 'latest', header: 'Latest snapshot', render: (d) => (d.latest_snapshot_date ? formatDate(d.latest_snapshot_date) : '—') },
  ]
  return (
    <Card title="Stability by department" description="Departments without enough history, with stale snapshots or with few people are shown as not scored, never as stable">
      <AsyncContent result={result} isEmpty={({ data }) => data.departments.length === 0} empty={<EmptyState title="No departments" />}>
        {({ data }) => <DataTable caption="Stability indicator by department" columns={columns} rows={data.departments} rowKey={(d) => d.department_id} />}
      </AsyncContent>
    </Card>
  )
}

function Trends({ scope }) {
  const result = useApi('/workforce-analytics/trends', scope)
  return (
    <Card title="Monthly trend (24 months)" description="Month-end headcount from snapshots; months without a snapshot are not observed">
      <AsyncContent result={result} isEmpty={({ data }) => !data.monthly.some((m) => m.observed) && !data.monthly.some((m) => m.joiners || m.recorded_departures)}
        empty={<EmptyState title="No history yet" message="Capture workforce snapshots to build headcount history." />}>
        {({ data }) => (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data.monthly} margin={{ left: -15 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="joiners" name="Joiners" fill="#0d9488" />
                <Bar dataKey="recorded_departures" name="Recorded departures" fill="#d97706" />
                <Line dataKey="active_employees" name="Active (month-end)" stroke="#2563eb" strokeWidth={2} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </AsyncContent>
    </Card>
  )
}

function Forecast({ scope }) {
  const result = useApi('/workforce-analytics/forecast', { ...scope, horizon_months: 6 })
  return (
    <Card title="Headcount forecast (6 months)" description="Linear trend over observed month-end headcount">
      <AsyncContent result={result}>
        {({ data }) => {
          if (data.status !== 'ok') {
            const title = data.status === 'stale_data' ? 'Snapshots are out of date' : 'Not enough history to forecast'
            const latest = data.latest_snapshot_date ? ` Latest snapshot: ${formatDate(data.latest_snapshot_date)}.` : ''
            return <EmptyState title={title} message={`${data.reason ?? ''} ${data.observed_months} of ${data.required_months} required observed months are available.${latest}`} />
          }
          const rows = [
            ...data.observed.map((o) => ({ month: o.month, observed: o.observed_active_employees })),
            ...data.projections.map((p) => ({ month: p.month, projected: p.projected_active_headcount })),
          ]
          return (
            <>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={rows} margin={{ left: -15 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line dataKey="observed" name="Observed" stroke="#2563eb" strokeWidth={2} />
                    <Line dataKey="projected" name="Projected" stroke="#64748b" strokeDasharray="5 4" strokeWidth={2} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                Observed {data.data_window.first_month} to {data.data_window.last_month} (latest snapshot {formatDate(data.latest_snapshot_date)}); projections start after the last observed month.
                {data.missing_months_in_data_window > 0 && ` ${data.missing_months_in_data_window} month(s) without a snapshot are skipped, not filled.`}
              </p>
              <p className="mt-1 text-xs text-slate-500">{data.backtest_note}{data.backtest_mean_absolute_error !== null ? ` (MAE ${data.backtest_mean_absolute_error})` : ''}</p>
              <div className="mt-2"><Notice>{data.note}</Notice></div>
            </>
          )
        }}
      </AsyncContent>
    </Card>
  )
}

// One stability request per view: with the department comparison shown, its by_department response also carries the
// organization's own indicator, so the card and the table share it instead of requesting it twice.
function AnalyticsBody({ scope, compare }) {
  const stability = useApi('/workforce-analytics/stability', compare ? { ...scope, by_department: true } : scope)
  return (
    <>
      <Overview scope={scope} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2"><Trends scope={scope} /></div>
        <Stability result={stability} />
      </div>
      <Forecast scope={scope} />
      {compare && <DepartmentStability result={stability} />}
    </>
  )
}

export default function Analytics() {
  const { user } = useAuth()
  const hrAdmin = isHrAdmin(user)
  const [department, setDepartment] = useState('')
  const departments = useApi(hrAdmin ? '/departments' : null, { page_size: 100 })
  const [confirm, setConfirm] = useState(false)
  const [version, setVersion] = useState(0)
  const action = useAction()
  const scope = cleanParams({ department_id: department })

  const capture = async () => {
    if (await action.run(() => api.post('/workforce-analytics/snapshots/capture', {}), { success: 'Snapshot captured' })) {
      setConfirm(false)
      setVersion((v) => v + 1)
    }
  }

  return (
    <div>
      <PageHeader title="Workforce analytics" description={hrAdmin ? 'Aggregate headcount, turnover and stability. Department figures never identify individuals.' : 'Aggregates for your department.'}
        actions={hrAdmin && (
          <>
            <Select label="Scope" value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="Organization" className="w-52"
              options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
            <div className="self-end"><Button icon={Camera} variant="secondary" onClick={() => setConfirm(true)}>Capture snapshot</Button></div>
          </>
        )} />
      <div key={`${department}-${version}`} className="space-y-4">
        <AnalyticsBody scope={scope} compare={hrAdmin && !department} />
      </div>
      <ConfirmDialog open={confirm} title="Capture today's workforce snapshot?" busy={action.busy} onCancel={() => setConfirm(false)} onConfirm={capture}
        message="Records today's headcount for the whole organization. Only one snapshot per day is allowed; it is recorded in the audit log." />
    </div>
  )
}
