import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import api, { describeError, errorStatus } from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useListState } from '../hooks/useList'
import { cursorNeedsReset, mergeUpdates, pollDelay } from '../lib/feed.js'
import { isHrAdmin } from '../lib/navigation'
import { formatDate, formatDateTime, formatNumber, humanize } from '../lib/format'
import {
  AsyncContent, Badge, Button, Card, Checkbox, DataTable, EmptyState, FilterBar, Notice, PageHeader, Pagination,
  PriorityBadge, Select, StatusBadge, TextInput,
} from '../components/ui'

const CATEGORIES = ['attendance', 'leave', 'employee_record', 'employee_status', 'risk_event', 'risk_alert', 'risk_escalation', 'intervention', 'recommendation']

const columns = [
  { key: 'recorded_at', header: 'Recorded', render: (i) => formatDateTime(i.recorded_at) },
  { key: 'description', header: 'Event', render: (i) => <span className="text-slate-900">{i.description}</span> },
  { key: 'category', header: 'Source', render: (i) => humanize(i.category) },
  { key: 'kind', header: 'Kind', render: (i) => <Badge tone={i.kind === 'RISK_EVENT' ? 'warning' : 'neutral'}>{i.kind === 'RISK_EVENT' ? 'Risk' : 'Observed'}</Badge> },
  { key: 'priority', header: 'Priority', render: (i) => (i.priority ? <PriorityBadge value={i.priority} /> : '—') },
  { key: 'source_event_at', header: 'Event time', render: (i) => (i.source_event_at ? (i.source_event_at.length === 10 ? formatDate(i.source_event_at) : formatDateTime(i.source_event_at)) : '—') },
]

// GET /workforce-activity/status for the same scope: data freshness and the server's polling recommendation.
function MonitoringStatus({ scope }) {
  const result = useApi('/workforce-activity/status', scope)
  return (
    <AsyncContent result={result} skeleton={1}>
      {(s) => (
        <div className="mb-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <Card>
            <p className="text-xs font-medium uppercase text-slate-500">Workforce snapshots</p>
            <p className="mt-1 flex items-center gap-2"><StatusBadge value={s.snapshot.status} />
              {s.snapshot.latest_snapshot_date ? `Latest ${formatDate(s.snapshot.latest_snapshot_date)} (${s.snapshot.age_days} days)` : 'None captured'}</p>
          </Card>
          <Card>
            <p className="text-xs font-medium uppercase text-slate-500">Latest recorded activity</p>
            <p className="mt-1">{s.activity.latest_recorded_at ? formatDateTime(s.activity.latest_recorded_at) : `Nothing in the last ${s.activity.lookback_days} days`}</p>
          </Card>
          <Card>
            <p className="text-xs font-medium uppercase text-slate-500">Active risk alerts</p>
            <p className="mt-1">{formatNumber(s.risk_alerts.active_total)} · {formatNumber(s.risk_alerts.by_sla_state.OVERDUE ?? 0)} past review deadline</p>
          </Card>
        </div>
      )}
    </AsyncContent>
  )
}

export default function Activity() {
  const { user } = useAuth()
  const hrAdmin = isHrAdmin(user)
  const departments = useApi(hrAdmin ? '/departments' : null, { page_size: 100 })
  const { filters, page, setPage, update, reset } = useListState({ category: '', kind: '', priority: '', department_id: '', date_from: '', date_to: '' })
  const feed = useApi('/workforce-activity/feed', cleanParams({ ...filters, page, page_size: 25 }))
  const [updates, setUpdates] = useState(null)
  const [checking, setChecking] = useState(false)
  const [autoCheck, setAutoCheck] = useState(false)
  const [notice, setNotice] = useState('')
  // Guards: one request at a time, and responses for an earlier filter/page/feed are dropped (generation changes).
  const inFlight = useRef(false)
  const generation = useRef(0)
  const latest = useRef({ updates: null, feed: null, filters })
  useEffect(() => {
    latest.current = { updates, feed: feed.data, filters }
  })
  const reloadFeed = feed.reload // stable (useCallback in useApi), unlike the feed result object

  const resetUpdates = useCallback(() => {
    generation.current += 1
    setUpdates(null)
    setNotice('')
  }, [])

  // Polling, not push: items recorded after the cursor issued with this feed page (then after each next_cursor).
  const checkUpdates = useCallback(async () => {
    const { updates: current, feed: page_, filters: active } = latest.current
    const cursor = current?.next_cursor ?? page_?.updates_cursor
    if (!cursor || inFlight.current) return
    inFlight.current = true
    setChecking(true)
    const mine = generation.current
    try {
      const { category, kind, priority, department_id } = active
      const res = await api.get('/workforce-activity/updates', { params: cleanParams({ cursor, category, kind, priority, department_id }) })
      if (mine !== generation.current) return
      setUpdates((previous) => mergeUpdates(previous, res.data, (latest.current.feed?.items ?? []).map((i) => i.event_id)))
    } catch (error) {
      if (mine !== generation.current) return
      if (cursorNeedsReset(errorStatus(error))) {
        // Expired or no longer valid: reload the feed, which issues a fresh cursor (documented recovery).
        resetUpdates()
        setNotice('Your update position expired, so the feed was reloaded.')
        reloadFeed()
      } else {
        setUpdates((previous) => ({ ...(previous ?? { items: [] }), error: describeError(error) }))
        setAutoCheck(false)
      }
    } finally {
      inFlight.current = false
      setChecking(false)
    }
  }, [reloadFeed, resetUpdates])

  // Optional auto-check while this page is open and visible, at the server's recommended interval. Stops on unmount
  // (including sign-out) and whenever it is switched off.
  const delay = pollDelay(updates?.recommended_poll_seconds)
  useEffect(() => {
    if (!autoCheck) return undefined
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') checkUpdates()
    }, delay)
    return () => clearInterval(timer)
  }, [autoCheck, delay, checkUpdates])

  const changeFilters = (patch) => {
    resetUpdates()
    update(patch)
  }
  const scope = cleanParams({ department_id: filters.department_id })

  return (
    <div>
      <PageHeader title="Activity feed" description="Recorded workforce events in your scope, newest first. Assembled from stored records by polling; not a live stream."
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <Checkbox label={`Auto-check every ${Math.round(delay / 1000)} s`} checked={autoCheck} onChange={(e) => setAutoCheck(e.target.checked)} disabled={!feed.data} />
            <Button variant="secondary" icon={RefreshCw} loading={checking} onClick={checkUpdates} disabled={!feed.data}>Check for new activity</Button>
          </div>
        } />
      <MonitoringStatus scope={scope} />
      {notice && <div className="mb-4"><Notice tone="warning">{notice}</Notice></div>}
      {updates && (
        <div className="mb-4">
          <Card title={`${updates.items.length} new event${updates.items.length === 1 ? '' : 's'} since this page loaded`}
            description={updates.has_more ? 'More are available — check again to load them.' : updates.settled_until ? `Up to ${formatDateTime(updates.settled_until)}` : undefined}>
            {updates.error ? <p role="alert" className="text-sm text-red-700">{updates.error}</p>
              : updates.items.length ? <DataTable caption="New activity" columns={columns} rows={updates.items} rowKey={(i) => i.event_id} />
                : <EmptyState title="Nothing new yet" />}
          </Card>
        </div>
      )}
      <Card>
        <FilterBar onReset={() => { resetUpdates(); reset() }}>
          <Select label="Source" value={filters.category} onChange={(e) => changeFilters({ category: e.target.value })} options={CATEGORIES} placeholder="All" className="w-44" />
          <Select label="Kind" value={filters.kind} onChange={(e) => changeFilters({ kind: e.target.value })} placeholder="All" className="w-36"
            options={[{ value: 'OBSERVED_EVENT', label: 'Observed' }, { value: 'RISK_EVENT', label: 'Risk' }]} />
          <Select label="Priority" value={filters.priority} onChange={(e) => changeFilters({ priority: e.target.value })} options={['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']} placeholder="Any" className="w-32" />
          {hrAdmin && (
            <Select label="Department" value={filters.department_id} onChange={(e) => changeFilters({ department_id: e.target.value })} placeholder="All" className="w-48"
              options={(departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))} />
          )}
          <TextInput label="From" type="date" value={filters.date_from} onChange={(e) => changeFilters({ date_from: e.target.value })} className="w-40" />
          <TextInput label="To" type="date" value={filters.date_to} onChange={(e) => changeFilters({ date_to: e.target.value })} className="w-40" />
        </FilterBar>
        {filters.priority && <div className="mb-3"><Notice>Only sources that carry a priority (alerts, escalations, interventions and recommendations) match a priority filter.</Notice></div>}
        <AsyncContent result={feed} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No recorded activity" message="The default window is the last 7 days (at most 31)." />}>
          {(d) => (
            <>
              {d.truncated && <div className="mb-3"><Notice tone="warning">Some sources reached the per-source limit for this window; narrow the dates or filters for a complete list.</Notice></div>}
              <DataTable caption="Activity" columns={columns} rows={d.items} rowKey={(i) => i.event_id} />
              <Pagination page={d.page} pages={d.pages} total={d.total} onPage={(p) => { resetUpdates(); setPage(p) }} />
            </>
          )}
        </AsyncContent>
      </Card>
    </div>
  )
}
