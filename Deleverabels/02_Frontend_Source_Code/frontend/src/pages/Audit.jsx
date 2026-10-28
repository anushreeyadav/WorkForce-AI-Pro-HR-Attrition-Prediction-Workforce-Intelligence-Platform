import { useState } from 'react'
import { Download } from 'lucide-react'
import { describeError, downloadFile } from '../api/client'
import { useAuth } from '../auth/context'
import { useToast } from '../components/toastContext'
import { cleanParams, useApi } from '../hooks/useApi'
import { useListState } from '../hooks/useList'
import { isHrAdmin } from '../lib/navigation'
import { formatDateTime, humanize } from '../lib/format'
import { AsyncContent, Button, Card, DataTable, EmptyState, FilterBar, Notice, PageHeader, Pagination, Select, TextInput } from '../components/ui'

export default function Audit() {
  const { user } = useAuth()
  const hrAdmin = isHrAdmin(user)
  const toast = useToast()
  const catalog = useApi('/audit-logs/catalog')
  const { filters, page, setPage, update, reset } = useListState({ date_from: '', date_to: '', category: '', action: '', entity_type: '', sort: 'newest' })
  const list = useApi('/audit-logs', cleanParams({ ...filters, page, page_size: 25 }))
  const [expanded, setExpanded] = useState(null)
  const [format, setFormat] = useState('csv')
  const [exporting, setExporting] = useState(false)
  const actions = (catalog.data?.actions ?? []).filter((a) => a.visible_to_you && (!filters.category || a.category === filters.category))

  const exportLogs = async () => {
    if (exporting) return
    setExporting(true)
    try {
      const rows = await downloadFile('/audit-logs/export', cleanParams({ ...filters, format }))
      toast.success(`Audit log exported${rows !== null ? ` (${rows} rows)` : ''}. The export itself is audited.`)
    } catch (error) {
      toast.error(describeError(error))
    } finally {
      setExporting(false)
    }
  }

  const columns = [
    { key: 'created_at', header: 'When', render: (a) => formatDateTime(a.created_at) },
    { key: 'action', header: 'Action', render: (a) => <span><span className="font-medium text-slate-900">{a.description ?? a.action}</span><span className="block text-xs text-slate-500">{a.action}</span></span> },
    { key: 'actor', header: 'Actor', render: (a) => (a.actor_hidden ? 'Outside your department' : a.actor_user_id ? `User #${a.actor_user_id}` : 'System / anonymous') },
    { key: 'entity', header: 'Record', render: (a) => `${humanize(a.entity_type)}${a.entity_id ? ` #${a.entity_id}` : ''}` },
    { key: 'details', header: 'Details', render: (a) => a.details ? (
      <button type="button" className="text-xs text-blue-700 hover:underline" onClick={(e) => { e.stopPropagation(); setExpanded(expanded === a.id ? null : a.id) }}>
        {expanded === a.id ? 'Hide' : 'Show'}
      </button>
    ) : '—' },
  ]

  return (
    <div>
      <PageHeader title="Audit logs" description={hrAdmin ? 'Administrative actions across the organization.' : 'Actions on records of your department.'}
        actions={hrAdmin && (
          <div className="flex items-end gap-2">
            <Select label="Export format" value={format} onChange={(e) => setFormat(e.target.value)} className="w-28" options={['csv', 'xlsx', 'pdf'].map((f) => ({ value: f, label: f.toUpperCase() }))} />
            <Button icon={Download} variant="secondary" loading={exporting} onClick={exportLogs}>Export</Button>
          </div>
        )} />
      <div className="mb-4"><Notice>Details are sanitized: sensitive values are redacted and free-text justifications are withheld. Audit rows are append-only in the application.</Notice></div>
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="From" type="date" value={filters.date_from} onChange={(e) => update({ date_from: e.target.value })} className="w-40" />
          <TextInput label="To" type="date" value={filters.date_to} onChange={(e) => update({ date_to: e.target.value })} className="w-40" />
          <Select label="Category" value={filters.category} onChange={(e) => update({ category: e.target.value, action: '' })} placeholder="All" className="w-40"
            options={(catalog.data?.categories ?? []).map((c) => ({ value: c, label: humanize(c) }))} />
          <Select label="Action" value={filters.action} onChange={(e) => update({ action: e.target.value })} placeholder="All" className="w-64"
            options={actions.map((a) => ({ value: a.action, label: a.description }))} />
          <Select label="Order" value={filters.sort} onChange={(e) => update({ sort: e.target.value })} className="w-32" options={[{ value: 'newest', label: 'Newest' }, { value: 'oldest', label: 'Oldest' }]} />
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No audit records" message="The default window is the last 30 days." />}>
          {(d) => (
            <>
              <DataTable caption="Audit logs" columns={columns} rows={d.items} />
              {expanded && (
                <pre className="mt-3 max-h-64 overflow-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                  {JSON.stringify(d.items.find((a) => a.id === expanded)?.details ?? {}, null, 2)}
                </pre>
              )}
              <Pagination page={d.page} pages={d.pages} total={d.total} onPage={(p) => { setExpanded(null); setPage(p) }} />
            </>
          )}
        </AsyncContent>
      </Card>
    </div>
  )
}
