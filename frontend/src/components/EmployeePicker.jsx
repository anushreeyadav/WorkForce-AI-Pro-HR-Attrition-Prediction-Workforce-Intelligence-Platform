import { useState } from 'react'
import { cleanParams, useApi } from '../hooks/useApi'
import { useDebounced } from '../hooks/useList'
import { Select, TextInput } from './ui'

// Search-and-select for an employee in the caller's scope (GET /api/employees applies the caller's scope).
// `optionValue` picks what the select returns: the employee id (default) or, e.g., the linked login's user id.
// (Not named `valueOf`: every props object inherits Object.prototype.valueOf, so a default for it never applied
// and the picker crashed as soon as employees loaded; found by the browser tests in Sub-prompt 32.)
// The backend still validates every choice (eligibility, department, self-actions) and answers 404/422 otherwise.
export default function EmployeePicker({ label, value, onChange, error, hint, required, placeholder = 'Select…',
  optionValue = (e) => e.id, include = () => true }) {
  const [term, setTerm] = useState('')
  const q = useDebounced(term)
  const result = useApi('/employees', cleanParams({ status: 'ACTIVE', q: q.trim(), page_size: 50 }))
  const options = (result.data?.items ?? []).filter(include)
    .map((e) => ({ value: String(optionValue(e)), label: `${e.full_name} (${e.employee_code}) — ${e.department.name}` }))
  return (
    <div>
      <TextInput label={`Find ${label.toLowerCase()}`} value={term} onChange={(e) => setTerm(e.target.value)}
        placeholder="Type a name or code" maxLength={100} />
      <Select label={label} required={required} value={value} onChange={onChange} options={options} placeholder={placeholder}
        error={error ?? (result.error ? 'Could not load employees' : undefined)} hint={hint} className="mt-2" />
    </div>
  )
}
