import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Play } from 'lucide-react'
import api from '../api/client'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { formatDateTime, formatNumber, humanize } from '../lib/format'
import {
  AsyncContent, Badge, Button, Card, ConfirmDialog, DataTable, DetailList, Dialog, EmptyState, FilterBar, Notice,
  PageHeader, Pagination, PriorityBadge, Select, StatusBadge, TextArea,
} from '../components/ui'

const STATUSES = ['AVAILABLE', 'ACCEPTED', 'DISMISSED', 'COMPLETED', 'SUPERSEDED']
const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

function Decision({ rec, onDone }) {
  const [mode, setMode] = useState(null)
  const [text, setText] = useState('')
  const action = useAction()
  const run = async () => {
    if (mode !== 'accept' && text.trim().length < 5) return
    const body = mode === 'dismiss' ? { reason: text.trim() } : mode === 'complete' ? { outcome: text.trim() } : {}
    await action.run(() => api.post(`/recommendations/${rec.id}/${mode}`, { ...body, expected_version: rec.version }),
      { success: `Recommendation ${mode === 'accept' ? 'accepted' : mode === 'dismiss' ? 'dismissed' : 'completed'}`, onSuccess: onDone })
  }
  const buttons = []
  if (rec.status === 'AVAILABLE') buttons.push(['accept', 'Accept'])
  if (['AVAILABLE', 'ACCEPTED'].includes(rec.status)) buttons.push(['dismiss', 'Dismiss'])
  if (rec.status === 'ACCEPTED') buttons.push(['complete', 'Mark completed'])
  if (!buttons.length) return null
  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      {!mode ? (
        <div className="flex flex-wrap justify-end gap-2">
          {buttons.map(([id, label]) => <Button key={id} variant={id === 'dismiss' ? 'secondary' : 'primary'} onClick={() => setMode(id)}>{label}</Button>)}
        </div>
      ) : (
        <div className="space-y-3">
          {mode === 'accept' ? <p className="text-sm text-slate-700">Accepting records that you will act on this guidance. It does not create an intervention or task by itself.</p> : (
            <TextArea label={mode === 'dismiss' ? 'Reason for dismissal' : 'Outcome'} required value={text} maxLength={mode === 'dismiss' ? 500 : 1000}
              onChange={(e) => setText(e.target.value)} error={text && text.trim().length < 5 ? 'At least 5 characters' : undefined} />
          )}
          {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setMode(null)} disabled={action.busy}>Back</Button>
            <Button onClick={run} loading={action.busy}>Confirm</Button>
          </div>
        </div>
      )}
    </div>
  )
}

function Evaluate({ onClose, onDone }) {
  const [strategy, setStrategy] = useState('rules-v2')
  const [result, setResult] = useState(null)
  const action = useAction()
  const run = () => action.run(() => api.post('/recommendations/evaluate', { strategy }),
    { success: 'Evaluation finished', onSuccess: (res) => { setResult(res.data); onDone() } })
  return (
    <ConfirmDialog open title="Evaluate recommendation rules" confirmLabel={result ? 'Run again' : 'Run evaluation'} busy={action.busy} onCancel={onClose} onConfirm={run}
      message="Applies deterministic rules to existing records in your scope. No model is trained or called, and nothing is changed automatically.">
      <div className="mt-3 space-y-3">
        <Select label="Strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}
          options={[{ value: 'rules-v2', label: 'rules-v2 (ranked, explains priority)' }, { value: 'rules-v1', label: 'rules-v1 (original)' }]} />
        {result && (
          <p className="rounded-md bg-slate-50 p-3 text-sm text-slate-700">
            {result.created} new, {result.already_recorded} unchanged, {result.superseded} superseded, {result.being_addressed} already being addressed.
            {result.truncated_sources.length > 0 && ` Limit reached for: ${result.truncated_sources.join(', ')}.`}
          </p>
        )}
        {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
      </div>
    </ConfirmDialog>
  )
}

export default function Recommendations() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { filters, page, setPage, update, reset } = useListState({ rule_version: '', status: 'AVAILABLE', priority: '', sort: 'rank' })
  const list = useApi('/recommendations', cleanParams({ ...filters, page, page_size: 20 }))
  const summary = useApi('/recommendations/summary')
  const [selected, setSelected] = useState(null)
  const [evaluating, setEvaluating] = useState(false)
  const focus = searchParams.get('focus')

  useEffect(() => {
    if (!focus) return
    api.get(`/recommendations/${focus}`).then((res) => setSelected(res.data)).catch(() => setSelected(null))
  }, [focus])
  const close = () => {
    setSelected(null)
    if (focus) setSearchParams({})
  }

  const columns = [
    { key: 'title', header: 'Recommendation', render: (r) => <span className="font-medium text-slate-900">{r.title}</span> },
    { key: 'priority', header: 'Priority', render: (r) => <PriorityBadge value={r.priority} /> },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'scope', header: 'Scope', render: (r) => humanize(r.scope) },
    { key: 'rank_score', header: 'Rank', render: (r) => formatNumber(r.rank_score) },
    { key: 'rule_version', header: 'Strategy', render: (r) => <Badge>{r.rule_version}</Badge> },
    { key: 'evaluated_at', header: 'Evaluated', render: (r) => formatDateTime(r.evaluated_at) },
  ]

  return (
    <div>
      <PageHeader title="Recommendations" description="Rule-based guidance for human review. Never about yourself."
        actions={<Button icon={Play} onClick={() => setEvaluating(true)}>Evaluate</Button>} />
      <div className="mb-4"><Notice>Recommendations come from deterministic rules, not a trained model. The rank orders suggestions; it is not a probability. Never use them for punitive or employment decisions.</Notice></div>
      <AsyncContent result={summary} skeleton={1}>
        {(s) => (
          <p className="mb-3 text-sm text-slate-600">
            Active by priority: {Object.entries(s.active_by_priority).map(([p, n]) => `${humanize(p)} ${n}`).join(' · ')}
          </p>
        )}
      </AsyncContent>
      <Card>
        <FilterBar onReset={reset}>
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-40" />
          <Select label="Priority" value={filters.priority} onChange={(e) => update({ priority: e.target.value })} options={PRIORITIES} placeholder="All" className="w-36" />
          <Select label="Strategy" value={filters.rule_version} onChange={(e) => update({ rule_version: e.target.value })} options={['rules-v1', 'rules-v2'].map((v) => ({ value: v, label: v }))} placeholder="All" className="w-36" />
          <Select label="Sort" value={filters.sort} onChange={(e) => update({ sort: e.target.value })} className="w-40"
            options={[{ value: 'rank', label: 'Rank (rules-v2 first)' }, { value: 'evaluated', label: 'Newest' }]} />
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No recommendations match" message="Run an evaluation, or change the filters." />}>
          {(d) => (<><DataTable caption="Recommendations" columns={columns} rows={d.items} onRowClick={setSelected} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {selected && (
        <Dialog open title={selected.title} onClose={close} wide>
          <DetailList items={[
            ['Priority', <PriorityBadge key="p" value={selected.priority} />], ['Status', <StatusBadge key="s" value={selected.status} />],
            ['Category', humanize(selected.category)], ['Strategy', selected.rule_version],
            ['Evidence freshness', humanize(selected.evidence?.freshness)], ['Evaluated', formatDateTime(selected.evaluated_at)],
          ]} />
          <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Suggested action</h3>
          <p className="mt-1 text-sm text-slate-800">{selected.suggested_action}</p>
          <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Why</h3>
          <p className="mt-1 text-sm text-slate-800">{selected.explanation}</p>
          {selected.priority_reasons?.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">How the priority was set</h3>
              <ul className="mt-1 list-inside list-disc text-sm text-slate-700">{selected.priority_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </>
          )}
          {selected.dismissal_reason && <p className="mt-3 text-sm text-slate-600">Dismissed: {selected.dismissal_reason}</p>}
          {selected.completion_outcome && <p className="mt-3 text-sm text-slate-600">Outcome: {selected.completion_outcome}</p>}
          <div className="mt-4"><Notice>{selected.disclaimer}</Notice></div>
          <Decision rec={selected} onDone={() => { close(); list.reload(); summary.reload() }} />
        </Dialog>
      )}
      {evaluating && <Evaluate onClose={() => setEvaluating(false)} onDone={() => { list.reload(); summary.reload() }} />}
    </div>
  )
}
