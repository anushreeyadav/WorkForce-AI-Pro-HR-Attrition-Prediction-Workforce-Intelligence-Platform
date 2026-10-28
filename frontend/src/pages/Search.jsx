import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Search as SearchIcon } from 'lucide-react'
import { useAuth } from '../auth/context'
import { useApi } from '../hooks/useApi'
import { linkFor } from '../lib/links'
import { formatDateTime, humanize } from '../lib/format'
import { AsyncContent, Button, Card, Checkbox, EmptyState, Notice, PageHeader, Select, StatusBadge, TextInput } from '../components/ui'

const TYPES = ['employee', 'department', 'task', 'intervention', 'recommendation', 'goal', 'review_cycle', 'performance_review']

function summaryText(summary) {
  return Object.entries(summary)
    .filter(([key, value]) => value !== null && value !== undefined && !['status'].includes(key))
    .map(([key, value]) => `${humanize(key)}: ${typeof value === 'string' ? humanize(value) : value}`)
    .join(' · ')
}

export default function Search() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const types = params.getAll('types')
  const limit = params.get('limit_per_type') ?? '5'
  // A new search from the top bar (a new ?q=) replaces whatever is being typed here.
  const [draft, setDraft] = useState({ q, value: q })
  const term = draft.q === q ? draft.value : q
  const setTerm = (value) => setDraft({ q, value })
  const [selectedTypes, setSelectedTypes] = useState(types)
  const valid = q.trim().length >= 2
  const result = useApi(valid ? '/search' : null, { q: q.trim(), types: types.length ? types : undefined, limit_per_type: limit })

  const submit = (event) => {
    event.preventDefault()
    const next = new URLSearchParams({ q: term.trim(), limit_per_type: limit })
    selectedTypes.forEach((t) => next.append('types', t))
    setParams(next)
  }
  const toggle = (type) => setSelectedTypes((list) => (list.includes(type) ? list.filter((t) => t !== type) : [...list, type]))

  return (
    <div>
      <PageHeader title="Search" description="Find people, departments, tasks, goals and more that you are allowed to see. Matches names, codes and titles only." />
      <Card>
        <form onSubmit={submit} className="space-y-3" role="search">
          <div className="flex flex-wrap items-end gap-3">
            <TextInput label="Search for" value={term} onChange={(e) => setTerm(e.target.value)} minLength={2} maxLength={100} className="w-full max-w-md"
              error={term && term.trim().length < 2 ? 'Enter at least 2 characters' : undefined} />
            <Select label="Per type" value={limit} onChange={(e) => setParams((p) => { const n = new URLSearchParams(p); n.set('limit_per_type', e.target.value); return n })}
              className="w-28" options={['5', '10', '20'].map((v) => ({ value: v, label: v }))} />
            <Button type="submit" icon={SearchIcon} disabled={term.trim().length < 2}>Search</Button>
          </div>
          <fieldset className="flex flex-wrap gap-4">
            <legend className="mb-1 text-xs font-medium text-slate-500">Record types (none selected = all you may search)</legend>
            {TYPES.map((type) => <Checkbox key={type} label={humanize(type)} checked={selectedTypes.includes(type)} onChange={() => toggle(type)} />)}
          </fieldset>
        </form>
      </Card>

      {valid && (
        <div className="mt-4">
          <AsyncContent result={result} isEmpty={(d) => d.results.length === 0}
            empty={<EmptyState title={`No results for “${q}”`} message="Search matches names, codes and titles, not descriptions or notes." />}>
            {(d) => {
              const groups = TYPES.map((type) => [type, d.results.filter((r) => r.type === type)]).filter(([, items]) => items.length)
              return (
                <div className="space-y-4">
                  {(d.unavailable_types.length > 0 || d.truncated_types.length > 0) && (
                    <Notice>
                      {d.truncated_types.length > 0 && `More matches exist for: ${d.truncated_types.map(humanize).join(', ')} — refine your search or show more per type. `}
                      {d.unavailable_types.length > 0 && `Not searched for your role: ${d.unavailable_types.map(humanize).join(', ')}.`}
                    </Notice>
                  )}
                  {groups.map(([type, items]) => (
                    <Card key={type} title={`${humanize(type)} (${items.length})`}>
                      <ul className="divide-y divide-slate-100">
                        {items.map((item) => {
                          const href = linkFor(user, item.type, item.id)
                          return (
                            <li key={`${item.type}-${item.id}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                              <div className="min-w-0">
                                {href ? <Link to={href} className="font-medium text-blue-700 hover:underline">{item.label}</Link> : <span className="font-medium text-slate-900">{item.label}</span>}
                                <p className="text-xs text-slate-500">{summaryText(item.summary)}{item.timestamp ? ` · updated ${formatDateTime(item.timestamp)}` : ''}</p>
                              </div>
                              {item.summary.status !== undefined && <StatusBadge value={item.summary.status} />}
                            </li>
                          )
                        })}
                      </ul>
                    </Card>
                  ))}
                </div>
              )
            }}
          </AsyncContent>
        </div>
      )}
    </div>
  )
}
