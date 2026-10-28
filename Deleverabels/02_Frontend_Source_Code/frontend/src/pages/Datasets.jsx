import { useState } from 'react'
import { ShieldCheck, Upload } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { hasRole } from '../lib/navigation'
import { formatDateTime, formatNumber, humanize } from '../lib/format'
import {
  AsyncContent, Badge, Button, Card, Checkbox, DataTable, Dialog, EmptyState, FilterBar, Notice, PageHeader, Pagination, Select,
  StatusBadge, TextInput,
} from '../components/ui'

const STATUSES = ['UPLOADED', 'VALIDATING', 'VALIDATED', 'REJECTED', 'ARCHIVED']

// GET /ai/attrition/model-status (ADMIN/HR). Shows availability and the model registry only; it never trains,
// activates or calls a model, and it states plainly that the model is a statistical estimate, not a validated predictor.
function ModelStatus() {
  const result = useApi('/ai/attrition/model-status')
  return (
    <Card title="Attrition model" description="Training and activation are separate, explicit ADMIN steps in the API; nothing here trains or activates a model.">
      <AsyncContent result={result} skeleton={1}>
        {(s) => (
          <div className="space-y-2 text-sm">
            <p className="flex flex-wrap items-center gap-2">
              {s.available ? <Badge tone="success">Active model available</Badge> : <Badge tone="warning">No usable model</Badge>}
              {s.active_model && <span className="text-slate-600">{s.active_model.model_version} · trained {formatDateTime(s.active_model.trained_at)} on {formatNumber(s.active_model.training_rows)} rows</span>}
            </p>
            {!s.available && <p className="text-slate-600">{s.reason} Predictions return “unavailable” until an administrator trains and activates a model on a validated dataset.</p>}
            <p className="text-xs text-slate-500">Feature schema {s.feature_schema_version} · {s.recent_models.length} model version(s) registered.</p>
            <Notice tone="warning">Risk estimates are statistical review aids, not validated predictions of individual behaviour, and must not drive employment decisions.</Notice>
          </div>
        )}
      </AsyncContent>
    </Card>
  )
}

// ADMIN only: register a CSV as a new dataset, or as a new immutable version of an existing one. Upload never
// validates or trains; limits (size, rows, columns) and prohibited columns are enforced by the server.
function UploadDialog({ dataset, onClose, onSaved }) {
  const [file, setFile] = useState(null)
  const [name, setName] = useState('')
  const action = useAction()
  const ready = file && (dataset || name.trim().length >= 2)
  const submit = async (event) => {
    event.preventDefault()
    if (!ready) return
    const form = new FormData()
    form.append('file', file)
    if (!dataset) form.append('display_name', name.trim())
    await action.run(() => api.post(dataset ? `/datasets/${dataset.id}/versions` : '/datasets', form),
      { success: dataset ? 'New version uploaded' : 'Dataset uploaded', onSuccess: onSaved })
  }
  return (
    <Dialog open title={dataset ? `New version of ${dataset.display_name}` : 'Upload dataset'} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="upload-form" icon={Upload} loading={action.busy} disabled={!ready}>Upload</Button></>}>
      <form id="upload-form" onSubmit={submit} noValidate className="space-y-4">
        {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
        {!dataset && <TextInput label="Display name" required value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />}
        <label className="block text-sm font-medium text-slate-700">
          CSV file
          <input type="file" accept=".csv,text/csv" required onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2" />
        </label>
        <p className="text-xs text-slate-500">Approved feature columns only; identifiers and sensitive characteristics are rejected. Validation is a separate step.</p>
      </form>
    </Dialog>
  )
}

function ValidationReport({ report }) {
  if (!report) return null
  return (
    <div className="space-y-2 text-sm">
      <p className="flex items-center gap-2"><StatusBadge value={report.status} />
        {report.training_eligible ? <Badge tone="success">Training eligible</Badge> : <Badge>Not training eligible</Badge>}</p>
      {report.report && (
        <>
          <p className="text-slate-700">{formatNumber(report.report.rows_total)} rows · {formatNumber(report.report.rows_valid)} valid · {formatNumber(report.report.rows_invalid)} invalid</p>
          {report.report.fatal_errors?.length > 0 && <Notice tone="warning">{report.report.fatal_errors.join(' · ')}</Notice>}
          {report.report.warnings?.length > 0 && <p className="text-xs text-slate-600">Warnings: {report.report.warnings.join(' · ')}</p>}
        </>
      )}
    </div>
  )
}

function Versions({ dataset, admin, onClose, onChanged }) {
  const result = useApi(`/datasets/${dataset.id}/versions`)
  const action = useAction()
  const [report, setReport] = useState(null)
  const [uploading, setUploading] = useState(false)
  const validate = (version) => action.run(() => api.post(`/datasets/${dataset.id}/validate`, null, { params: { version } }),
    { success: (res) => `Validation finished: ${humanize(res.data.status)}`, onSuccess: (res) => { setReport(res.data); result.reload(); onChanged() } })
  const columns = [
    { key: 'version_number', header: 'Version', render: (v) => `v${v.version_number}` },
    { key: 'status', header: 'Status', render: (v) => <StatusBadge value={v.status} /> },
    { key: 'training_eligible', header: 'Training eligible', render: (v) => (v.training_eligible ? <Badge tone="success">Yes</Badge> : <Badge>No</Badge>) },
    { key: 'rows', header: 'Rows × columns', render: (v) => `${formatNumber(v.row_count)} × ${v.column_count}` },
    { key: 'schema', header: 'Schema', render: (v) => v.schema_version ?? '—' },
    { key: 'validated_at', header: 'Validated', render: (v) => formatDateTime(v.validated_at) },
    { key: 'models', header: 'Used by models', render: (v) => (v.used_by_models.length ? v.used_by_models.join(', ') : '—') },
    ...(admin ? [{ key: 'validate', header: <span className="sr-only">Validate</span>, render: (v) => ['UPLOADED', 'REJECTED'].includes(v.status) && !dataset.archived_at && (
      <Button size="sm" variant="secondary" icon={ShieldCheck} loading={action.busy} onClick={() => validate(v.version_number)}>Validate</Button>) }] : []),
  ]
  return (
    <Dialog open title={`${dataset.display_name} — versions`} onClose={onClose} wide
      footer={admin && !dataset.archived_at && <Button variant="secondary" icon={Upload} onClick={() => setUploading(true)}>Upload new version</Button>}>
      <AsyncContent result={result} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No versions" />}>
        {(d) => <DataTable caption="Dataset versions" columns={columns} rows={d} />}
      </AsyncContent>
      {action.message && <p role="alert" className="mt-2 text-sm text-red-700">{action.message}</p>}
      {report && <div className="mt-4 border-t border-slate-100 pt-4"><ValidationReport report={report} /></div>}
      <p className="mt-3 text-xs text-slate-500">Dataset contents are never shown here; downloads are ADMIN-only and audited.</p>
      {uploading && <UploadDialog dataset={dataset} onClose={() => setUploading(false)} onSaved={() => { setUploading(false); result.reload(); onChanged() }} />}
    </Dialog>
  )
}

export default function Datasets() {
  const { user } = useAuth()
  const admin = hasRole(user, 'ADMIN')
  const { filters, page, setPage, update, reset } = useListState({ q: '', status: '', include_archived: false })
  const q = useDebounced(filters.q)
  const list = useApi('/datasets', cleanParams({ ...filters, q: q.trim(), page, page_size: 20 }))
  const [selected, setSelected] = useState(null)
  const [uploading, setUploading] = useState(false)
  const columns = [
    { key: 'display_name', header: 'Dataset', render: (d) => <span className="font-medium text-slate-900">{d.display_name}</span> },
    { key: 'purpose', header: 'Purpose', render: (d) => humanize(d.purpose) },
    { key: 'status', header: 'Latest version', render: (d) => <span className="flex items-center gap-2">v{d.latest_version.version_number}<StatusBadge value={d.latest_version.status} /></span> },
    { key: 'rows', header: 'Rows', render: (d) => formatNumber(d.latest_version.row_count) },
    { key: 'versions', header: 'Versions', render: (d) => d.version_count },
    { key: 'created_at', header: 'Created', render: (d) => formatDateTime(d.created_at) },
  ]
  return (
    <div>
      <PageHeader title="Datasets" description={admin ? 'Register, version and validate workforce datasets.' : 'Registered workforce datasets, their versions and validation outcomes (read-only for HR).'}
        actions={admin && <Button icon={Upload} onClick={() => setUploading(true)}>Upload dataset</Button>} />
      <div className="mb-4"><ModelStatus /></div>
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="Search" placeholder="Dataset name" value={filters.q} maxLength={100} onChange={(e) => update({ q: e.target.value })} className="w-56" />
          <Select label="Latest status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-40" />
          <div className="pb-2"><Checkbox label="Include archived" checked={filters.include_archived} onChange={(e) => update({ include_archived: e.target.checked })} /></div>
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No datasets" message={admin ? 'Upload a CSV with the approved feature columns to get started.' : undefined} />}>
          {(d) => (<><DataTable caption="Datasets" columns={columns} rows={d.items} onRowClick={setSelected} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
        </AsyncContent>
      </Card>
      {selected && <Versions key={selected.id} dataset={selected} admin={admin} onClose={() => setSelected(null)} onChanged={list.reload} />}
      {uploading && <UploadDialog onClose={() => setUploading(false)} onSaved={(res) => { setUploading(false); list.reload(); setSelected(res.data) }} />}
    </div>
  )
}
