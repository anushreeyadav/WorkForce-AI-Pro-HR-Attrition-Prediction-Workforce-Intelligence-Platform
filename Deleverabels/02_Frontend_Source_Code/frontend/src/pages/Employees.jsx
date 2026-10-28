import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Pencil, Plus, UserCog } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { isHrAdmin } from '../lib/navigation'
import { formatDate, humanize } from '../lib/format'
import {
  AsyncContent, Button, Card, ConfirmDialog, DataTable, DetailList, Dialog, EmptyState, FilterBar, PageHeader,
  Pagination, Select, StatusBadge, TextArea, TextInput,
} from '../components/ui'

const STATUSES = ['ACTIVE', 'ON_LEAVE', 'INACTIVE']
const EMPTY_FORM = { employee_code: '', full_name: '', work_email: '', department_id: '', designation: '', date_of_joining: '', phone: '' }

function useDepartments() {
  const result = useApi('/departments', { page_size: 100 })
  return (result.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.is_active ? d.name : `${d.name} (inactive)` }))
}

function EmployeeForm({ employee, departments, onClose, onSaved }) {
  const [form, setForm] = useState(employee ? {
    employee_code: employee.employee_code, full_name: employee.full_name, work_email: employee.work_email,
    department_id: String(employee.department.id), designation: employee.designation,
    date_of_joining: employee.date_of_joining, phone: employee.phone ?? '',
  } : EMPTY_FORM)
  const [local, setLocal] = useState({})
  const action = useAction()
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const errors = { ...local, ...action.errors }

  const submit = async (event) => {
    event.preventDefault()
    const missing = {}
    for (const key of ['employee_code', 'full_name', 'work_email', 'department_id', 'designation', 'date_of_joining']) {
      if (!String(form[key]).trim()) missing[key] = 'Required'
    }
    if (form.work_email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.work_email)) missing.work_email = 'Enter a valid email address'
    setLocal(missing)
    if (Object.keys(missing).length) return
    const body = { ...form, department_id: Number(form.department_id), phone: form.phone.trim() || null }
    let payload = body
    if (employee) {
      payload = Object.fromEntries(Object.entries(body).filter(([k, v]) => (k === 'department_id' ? v !== employee.department.id : v !== (employee[k] ?? null))))
      if (!Object.keys(payload).length) return onClose()
    }
    await action.run(() => (employee ? api.patch(`/employees/${employee.id}`, payload) : api.post('/employees', payload)),
      { success: employee ? 'Employee updated' : 'Employee created', onSuccess: (res) => onSaved(res.data) })
  }

  return (
    <Dialog open title={employee ? `Edit ${employee.full_name}` : 'New employee'} onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="employee-form" loading={action.busy}>Save</Button></>}>
      <form id="employee-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        <TextInput label="Employee code" required value={form.employee_code} onChange={set('employee_code')} error={errors.employee_code} maxLength={20} />
        <TextInput label="Full name" required value={form.full_name} onChange={set('full_name')} error={errors.full_name} maxLength={150} />
        <TextInput label="Work email" type="email" required value={form.work_email} onChange={set('work_email')} error={errors.work_email} maxLength={255} />
        <Select label="Department" required value={form.department_id} onChange={set('department_id')} options={departments} placeholder="Select…" error={errors.department_id} />
        <TextInput label="Designation" required value={form.designation} onChange={set('designation')} error={errors.designation} maxLength={100} />
        <TextInput label="Date of joining" type="date" required value={form.date_of_joining} onChange={set('date_of_joining')} error={errors.date_of_joining} />
        <TextInput label="Phone" value={form.phone} onChange={set('phone')} error={errors.phone} maxLength={20} hint="Optional" />
      </form>
    </Dialog>
  )
}

function StatusChange({ employee, onClose, onSaved }) {
  const [status, setStatus] = useState(employee.status)
  const [reason, setReason] = useState('')
  const action = useAction()
  const confirm = () => action.run(() => api.patch(`/employees/${employee.id}/status`, { status, reason: reason.trim() || null }),
    { success: `Status changed to ${humanize(status)}`, onSuccess: (res) => onSaved(res.data) })
  return (
    <ConfirmDialog open title={`Change status of ${employee.full_name}`} confirmLabel="Change status" danger={status === 'INACTIVE'}
      busy={action.busy} onCancel={onClose} onConfirm={confirm}
      message="Status changes are recorded in the audit trail. Setting INACTIVE records a departure for workforce analytics.">
      <div className="mt-4 space-y-3">
        <Select label="New status" value={status} onChange={(e) => setStatus(e.target.value)} options={STATUSES} />
        <TextArea label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={255} hint="Optional; stored as the justification for this change" />
        {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
      </div>
    </ConfirmDialog>
  )
}

export default function Employees() {
  const { user } = useAuth()
  const canWrite = isHrAdmin(user)
  const departments = useDepartments()
  const [searchParams, setSearchParams] = useSearchParams()
  const { filters, page, setPage, update, reset } = useListState({ q: '', department_id: '', status: '', sort_by: 'full_name', sort_order: 'asc' })
  const q = useDebounced(filters.q)
  const list = useApi('/employees', cleanParams({ ...filters, q: q.trim(), page, page_size: 20 }))
  const [selected, setSelected] = useState(null)
  const [editing, setEditing] = useState(null)
  const [changingStatus, setChangingStatus] = useState(null)
  const focus = searchParams.get('focus')

  useEffect(() => {
    if (!focus) return
    api.get(`/employees/${focus}`).then((res) => setSelected(res.data)).catch(() => setSelected(null))
  }, [focus])

  const closeDetail = () => {
    setSelected(null)
    if (focus) setSearchParams({})
  }
  const saved = (employee) => {
    setEditing(null)
    setChangingStatus(null)
    setSelected(employee)
    list.reload()
  }

  const columns = [
    { key: 'employee_code', header: 'Code' },
    { key: 'full_name', header: 'Name', render: (e) => <span className="font-medium text-slate-900">{e.full_name}</span> },
    { key: 'department', header: 'Department', render: (e) => e.department.name },
    { key: 'designation', header: 'Designation' },
    { key: 'status', header: 'Status', render: (e) => <StatusBadge value={e.status} /> },
    { key: 'date_of_joining', header: 'Joined', render: (e) => formatDate(e.date_of_joining) },
  ]

  return (
    <div>
      <PageHeader title="Employees" description={canWrite ? 'Organization directory.' : user.role === 'MANAGER' ? 'Employees in your department.' : 'Your employee record.'}
        actions={canWrite && <Button icon={Plus} onClick={() => setEditing({})}>New employee</Button>} />
      <Card>
        <FilterBar onReset={reset}>
          <TextInput label="Search" placeholder="Name, code, email or designation" value={filters.q} maxLength={100}
            onChange={(e) => update({ q: e.target.value })} className="w-64" />
          <Select label="Department" value={filters.department_id} onChange={(e) => update({ department_id: e.target.value })} options={departments} placeholder="All" className="w-48" />
          <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={STATUSES} placeholder="All" className="w-36" />
          <Select label="Sort by" value={filters.sort_by} onChange={(e) => update({ sort_by: e.target.value })} className="w-40"
            options={[{ value: 'full_name', label: 'Name' }, { value: 'employee_code', label: 'Code' }, { value: 'date_of_joining', label: 'Joining date' }, { value: 'created_at', label: 'Created' }]} />
          <Select label="Order" value={filters.sort_order} onChange={(e) => update({ sort_order: e.target.value })} className="w-32"
            options={[{ value: 'asc', label: 'Ascending' }, { value: 'desc', label: 'Descending' }]} />
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No employees match" message="Try a different search or clear the filters." />}>
          {(d) => (
            <>
              <DataTable caption="Employees" columns={columns} rows={d.items} onRowClick={setSelected} />
              <Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} />
            </>
          )}
        </AsyncContent>
      </Card>

      {selected && !editing && !changingStatus && (
        <Dialog open title={selected.full_name} onClose={closeDetail} wide
          footer={canWrite && (
            <>
              <Button variant="secondary" icon={UserCog} onClick={() => setChangingStatus(selected)}>Change status</Button>
              <Button icon={Pencil} onClick={() => setEditing(selected)}>Edit</Button>
            </>
          )}>
          <DetailList items={[
            ['Employee code', selected.employee_code], ['Status', <StatusBadge key="s" value={selected.status} />],
            ['Department', selected.department.name], ['Designation', selected.designation],
            ['Date of joining', formatDate(selected.date_of_joining)], ['Work email', selected.work_email],
            ...(canWrite ? [['Phone', selected.phone || '—'], ['Login linked', selected.user_id ? 'Yes' : 'No']] : []),
          ]} />
        </Dialog>
      )}
      {editing && <EmployeeForm employee={editing.id ? editing : null} departments={departments} onClose={() => setEditing(null)} onSaved={saved} />}
      {changingStatus && <StatusChange employee={changingStatus} onClose={() => setChangingStatus(null)} onSaved={saved} />}
    </div>
  )
}
