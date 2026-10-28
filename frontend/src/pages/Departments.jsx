import { useState } from 'react'
import { Pencil, Plus } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { isHrAdmin } from '../lib/navigation'
import { formatNumber } from '../lib/format'
import {
  AsyncContent, Button, Card, Checkbox, DataTable, Dialog, EmptyState, FilterBar, PageHeader, Pagination, Select,
  StatusBadge, TextArea, TextInput,
} from '../components/ui'

function DepartmentForm({ department, onClose, onSaved }) {
  const [form, setForm] = useState({ name: department?.name ?? '', description: department?.description ?? '', is_active: department?.is_active ?? true })
  const [local, setLocal] = useState({})
  const action = useAction()
  const errors = { ...local, ...action.errors }

  const submit = async (event) => {
    event.preventDefault()
    if (!form.name.trim()) return setLocal({ name: 'Required' })
    setLocal({})
    const body = { name: form.name.trim(), description: form.description.trim() || null, is_active: form.is_active }
    await action.run(() => (department ? api.patch(`/departments/${department.id}`, body) : api.post('/departments', body)),
      { success: department ? 'Department updated' : 'Department created', onSuccess: onSaved })
  }

  return (
    <Dialog open title={department ? `Edit ${department.name}` : 'New department'} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="department-form" loading={action.busy}>Save</Button></>}>
      <form id="department-form" onSubmit={submit} noValidate className="space-y-4">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
        <TextInput label="Name" required value={form.name} maxLength={100} error={errors.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <TextArea label="Description" value={form.description} maxLength={1000} error={errors.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        <Checkbox label="Active" checked={form.is_active} onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))} />
      </form>
    </Dialog>
  )
}

export default function Departments() {
  const { user } = useAuth()
  const canWrite = isHrAdmin(user)
  const { filters, page, setPage, update, reset } = useListState({ q: '', is_active: '' })
  const q = useDebounced(filters.q)
  const list = useApi('/departments', cleanParams({ q: q.trim(), is_active: filters.is_active, page, page_size: 20 }))
  const [editing, setEditing] = useState(null)

  const columns = [
    { key: 'name', header: 'Department', render: (d) => <span className="font-medium text-slate-900">{d.name}</span> },
    { key: 'description', header: 'Description', render: (d) => <span className="line-clamp-2 text-slate-600">{d.description || '—'}</span> },
    { key: 'employee_count', header: 'Employees (all statuses)', render: (d) => formatNumber(d.employee_count) },
    { key: 'is_active', header: 'Status', render: (d) => <StatusBadge value={d.is_active ? 'ACTIVE' : 'INACTIVE'} /> },
    ...(canWrite ? [{ key: 'actions', header: <span className="sr-only">Actions</span>, render: (d) => (
      <Button variant="ghost" size="sm" icon={Pencil} onClick={() => setEditing(d)} aria-label={`Edit ${d.name}`}>Edit</Button>
    ) }] : []),
  ]

  return (
    <div>
      <PageHeader title="Departments" description="Organization structure and headcount on record."
        actions={canWrite && <Button icon={Plus} onClick={() => setEditing({})}>New department</Button>} />
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="Search" placeholder="Department name" value={filters.q} maxLength={100} onChange={(e) => update({ q: e.target.value })} className="w-64" />
          <Select label="Status" value={filters.is_active} onChange={(e) => update({ is_active: e.target.value })} className="w-36"
            placeholder="All" options={[{ value: 'true', label: 'Active' }, { value: 'false', label: 'Inactive' }]} />
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No departments match" />}>
          {(d) => (
            <>
              <DataTable caption="Departments" columns={columns} rows={d.items} />
              <Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} />
            </>
          )}
        </AsyncContent>
      </Card>
      {editing && <DepartmentForm department={editing.id ? editing : null} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload() }} />}
    </div>
  )
}
