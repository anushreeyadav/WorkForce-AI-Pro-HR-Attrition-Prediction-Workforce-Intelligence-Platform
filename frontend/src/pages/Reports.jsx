import { useState } from 'react'
import { Download } from 'lucide-react'
import { describeError, downloadFile } from '../api/client'
import { useAuth } from '../auth/context'
import { useToast } from '../components/toastContext'
import { cleanParams, useApi } from '../hooks/useApi'
import { isHrAdmin } from '../lib/navigation'
import { monthLabel } from '../lib/format'
import { AsyncContent, Button, Card, EmptyState, Notice, PageHeader, Select, TextInput } from '../components/ui'

function ReportCard({ report, departments, periods, canPickDepartment }) {
  const toast = useToast()
  const [values, setValues] = useState({ format: 'csv', date_from: '', date_to: '', department_id: '', period_id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const has = (name) => report.filters.includes(name)
  const set = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }))

  const download = async () => {
    if (busy) return
    if (has('period_id') && !values.period_id) return setError('Choose a payroll period.')
    setBusy(true)
    setError('')
    try {
      const params = cleanParams({
        format: values.format, period_id: values.period_id, department_id: canPickDepartment ? values.department_id : '',
        date_from: has('date_from') ? values.date_from : '', date_to: has('date_to') ? values.date_to : '',
      })
      const rows = await downloadFile(report.path.replace(/^\/api/, ''), params)
      toast.success(`${report.title} downloaded${rows !== null ? ` (${rows} rows)` : ''}`)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title={report.title} description={report.description}>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Format" value={values.format} onChange={set('format')} className="w-28" options={report.formats.map((f) => ({ value: f, label: f.toUpperCase() }))} />
        {has('period_id') && (
          <Select label="Period" value={values.period_id} onChange={set('period_id')} placeholder="Select…" className="w-44"
            options={periods.map((p) => ({ value: String(p.id), label: monthLabel(p.year, p.month) }))} />
        )}
        {has('date_from') && <TextInput label="From" type="date" value={values.date_from} onChange={set('date_from')} className="w-40" />}
        {has('date_to') && <TextInput label="To" type="date" value={values.date_to} onChange={set('date_to')} className="w-40" />}
        {has('department_id') && canPickDepartment && (
          <Select label="Department" value={values.department_id} onChange={set('department_id')} placeholder="Organization" className="w-48" options={departments} />
        )}
        <Button icon={Download} loading={busy} onClick={download}>Download</Button>
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
      <p className="mt-2 text-xs text-slate-500">Scope: {report.scope}</p>
    </Card>
  )
}

export default function Reports() {
  const { user } = useAuth()
  const hrAdmin = isHrAdmin(user)
  const catalog = useApi('/reports/catalog')
  const departments = useApi(hrAdmin ? '/departments' : null, { page_size: 100 })
  const periods = useApi(hrAdmin ? '/payroll/periods' : null, { page_size: 24 })
  const departmentOptions = (departments.data?.items ?? []).map((d) => ({ value: String(d.id), label: d.name }))
  return (
    <div>
      <PageHeader title="Reports" description="Download reports as CSV, Excel or PDF. Each report uses the same permissions as its module." />
      <AsyncContent result={catalog} isEmpty={(d) => d.reports.length === 0} empty={<EmptyState title="No reports are available for your role" />}>
        {(c) => (
          <div className="space-y-4">
            <Notice>Reports with more than {c.limits.max_rows} rows ({c.limits.pdf_max_rows} for PDF) are refused rather than cut short; narrow the filters if that happens. Default date ranges apply when dates are left empty.</Notice>
            {c.reports.map((report) => (
              <ReportCard key={report.id} report={report} departments={departmentOptions} periods={periods.data?.items ?? []} canPickDepartment={hrAdmin} />
            ))}
          </div>
        )}
      </AsyncContent>
    </div>
  )
}
