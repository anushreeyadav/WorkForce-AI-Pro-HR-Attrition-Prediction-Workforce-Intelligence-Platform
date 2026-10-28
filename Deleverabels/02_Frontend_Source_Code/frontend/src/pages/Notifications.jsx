import { Link } from 'react-router-dom'
import { CheckCheck } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import { cleanParams, useApi } from '../hooks/useApi'
import { useAction, useListState } from '../hooks/useList'
import { linkFor } from '../lib/links'
import { formatDateTime } from '../lib/format'
import { AsyncContent, Button, Card, Checkbox, EmptyState, FilterBar, PageHeader, Pagination, PriorityBadge, Select } from '../components/ui'

export default function Notifications() {
  const { user } = useAuth()
  const { filters, page, setPage, update, reset } = useListState({ unread_only: false, sort: 'priority' })
  const list = useApi('/notifications/me', cleanParams({ ...filters, page, page_size: 20 }))
  const action = useAction()
  const markRead = (id) => action.run(() => api.post(`/notifications/${id}/read`), { onSuccess: list.reload })

  return (
    <div>
      <PageHeader title="Notifications" description="In-app notifications for you. Reading one never changes the related record." />
      <Card>
        <FilterBar onReset={reset}>
          <Select label="Order" value={filters.sort} onChange={(e) => update({ sort: e.target.value })} className="w-52"
            options={[{ value: 'priority', label: 'Unread and urgent first' }, { value: 'recent', label: 'Newest first' }]} />
          <div className="pb-2"><Checkbox label="Unread only" checked={filters.unread_only} onChange={(e) => update({ unread_only: e.target.checked })} /></div>
        </FilterBar>
        <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="You're all caught up" />}>
          {(d) => (
            <>
              <ul className="divide-y divide-slate-100">
                {d.items.map((n) => {
                  const href = n.entity_type ? linkFor(user, n.entity_type, n.entity_id) : null
                  return (
                    <li key={n.id} className={`flex flex-wrap items-start justify-between gap-3 py-3 ${n.is_read ? 'opacity-70' : ''}`}>
                      <div className="min-w-0">
                        <p className="flex items-center gap-2 text-sm font-medium text-slate-900">
                          {!n.is_read && <span className="h-2 w-2 rounded-full bg-blue-600" aria-label="Unread" />}
                          {n.title}
                          <PriorityBadge value={n.priority} />
                        </p>
                        <p className="mt-0.5 text-sm text-slate-700">{n.message}</p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          {formatDateTime(n.created_at)}
                          {href && <> · <Link to={href} className="text-blue-700 hover:underline">Open</Link></>}
                        </p>
                      </div>
                      {!n.is_read && <Button variant="ghost" size="sm" icon={CheckCheck} onClick={() => markRead(n.id)} disabled={action.busy}>Mark read</Button>}
                    </li>
                  )
                })}
              </ul>
              <Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} />
            </>
          )}
        </AsyncContent>
      </Card>
    </div>
  )
}
