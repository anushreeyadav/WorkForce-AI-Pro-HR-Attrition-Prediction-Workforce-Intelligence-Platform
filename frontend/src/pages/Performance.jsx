import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus } from 'lucide-react'
import api from '../api/client'
import { useAuth } from '../auth/context'
import EmployeePicker from '../components/EmployeePicker'
import { cleanParams, useApi } from '../hooks/useApi'
import { isHrAdmin, isStaff } from '../lib/navigation'
import { useAction, useDebounced, useListState } from '../hooks/useList'
import { formatDate, formatDateTime, humanize, isoDate } from '../lib/format'
import {
  AsyncContent, Button, Card, DataTable, DetailList, Dialog, EmptyState, FilterBar, Notice, PageHeader, Pagination,
  Select, StatusBadge, Tabs, TextArea, TextInput,
} from '../components/ui'

const GOAL_STATUSES = ['NOT_STARTED', 'IN_PROGRESS', 'AT_RISK', 'COMPLETED', 'CANCELLED']
const OWNER_STATUSES = ['NOT_STARTED', 'IN_PROGRESS', 'AT_RISK', 'COMPLETED']
const CATEGORIES = ['BUSINESS', 'DEVELOPMENT', 'LEARNING', 'TEAM', 'OTHER']
const REVIEW_STATUSES = ['ASSIGNED', 'SELF_SUBMITTED', 'MANAGER_SUBMITTED', 'FINALIZED', 'CANCELLED']

function GoalForm({ onClose, onSaved }) {
  const [form, setForm] = useState({ title: '', measurable_target: '', category: 'BUSINESS', start_date: isoDate(0), target_date: '', description: '' })
  const [local, setLocal] = useState({})
  const action = useAction()
  const errors = { ...local, ...action.errors }
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const submit = async (event) => {
    event.preventDefault()
    const missing = {}
    if (form.title.trim().length < 3) missing.title = 'At least 3 characters'
    if (form.measurable_target.trim().length < 3) missing.measurable_target = 'At least 3 characters'
    if (!form.target_date) missing.target_date = 'Required'
    if (form.target_date && form.target_date < form.start_date) missing.target_date = 'Must be on or after the start date'
    setLocal(missing)
    if (Object.keys(missing).length) return
    await action.run(() => api.post('/performance/goals', cleanParams({ ...form, title: form.title.trim(), description: form.description.trim() })),
      { success: 'Goal created', onSuccess: onSaved })
  }
  return (
    <Dialog open title="New goal" onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="goal-form" loading={action.busy}>Create goal</Button></>}>
      <form id="goal-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        <TextInput label="Title" required value={form.title} onChange={set('title')} error={errors.title} maxLength={150} className="sm:col-span-2" />
        <TextInput label="Measurable target" required value={form.measurable_target} onChange={set('measurable_target')} error={errors.measurable_target} maxLength={255} className="sm:col-span-2" />
        <Select label="Category" value={form.category} onChange={set('category')} options={CATEGORIES} />
        <div />
        <TextInput label="Start" type="date" required value={form.start_date} onChange={set('start_date')} error={errors.start_date} />
        <TextInput label="Target date" type="date" required value={form.target_date} onChange={set('target_date')} error={errors.target_date} />
        <TextArea label="Description" value={form.description} onChange={set('description')} maxLength={2000} className="sm:col-span-2" />
      </form>
    </Dialog>
  )
}

function GoalUpdate({ goal, onClose, onSaved }) {
  const [progress, setProgress] = useState(String(goal.progress_percent))
  const [status, setStatus] = useState(goal.status)
  const [feedback, setFeedback] = useState(goal.manager_feedback ?? '')
  const action = useAction()
  const value = Number(progress)
  const invalid = progress === '' || !Number.isInteger(value) || value < 0 || value > 100
  const completeTooEarly = status === 'COMPLETED' && value !== 100
  const submit = async () => {
    if (invalid || completeTooEarly) return
    const body = { expected_version: goal.version }
    if (value !== goal.progress_percent) body.progress_percent = value
    if (status !== goal.status) body.status = status
    if (goal.can_manage && feedback.trim() && feedback.trim() !== (goal.manager_feedback ?? '')) body.manager_feedback = feedback.trim()
    if (Object.keys(body).length === 1) return onClose()
    await action.run(() => api.patch(`/performance/goals/${goal.id}`, body), { success: 'Goal updated', onSuccess: onSaved })
  }
  return (
    <Dialog open title={goal.title} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button onClick={submit} loading={action.busy}>Save</Button></>}>
      <div className="space-y-4">
        <p className="text-sm text-slate-600">Target: {goal.measurable_target} · due {formatDate(goal.target_date)}</p>
        <TextInput label="Progress (%)" type="number" min={0} max={100} value={progress} onChange={(e) => setProgress(e.target.value)}
          error={invalid ? 'Enter a whole number from 0 to 100' : action.errors.progress_percent} />
        <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={goal.can_manage ? GOAL_STATUSES : OWNER_STATUSES}
          error={completeTooEarly ? 'A goal can be completed only at 100% progress' : action.errors.status} />
        {goal.can_manage && <TextArea label="Manager feedback" value={feedback} maxLength={2000} onChange={(e) => setFeedback(e.target.value)} />}
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
      </div>
    </Dialog>
  )
}

function Goals() {
  const { filters, page, setPage, update, reset } = useListState({ q: '', status: '', mine: false })
  const q = useDebounced(filters.q)
  const list = useApi('/performance/goals', cleanParams({ ...filters, q: q.trim(), page, page_size: 20 }))
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(null)
  const columns = [
    { key: 'title', header: 'Goal', render: (g) => <span className="font-medium text-slate-900">{g.title}</span> },
    { key: 'employee', header: 'Employee', render: (g) => g.employee.full_name },
    { key: 'progress', header: 'Progress', render: (g) => (
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-24 rounded bg-slate-100" role="progressbar" aria-valuenow={g.progress_percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-1.5 rounded bg-teal-600" style={{ width: `${g.progress_percent}%` }} />
        </div>
        <span className="text-xs text-slate-600">{g.progress_percent}%</span>
      </div>
    ) },
    { key: 'status', header: 'Status', render: (g) => <StatusBadge value={g.status} /> },
    { key: 'target_date', header: 'Target', render: (g) => <span className={g.is_overdue ? 'text-red-700' : ''}>{formatDate(g.target_date)}</span> },
  ]
  return (
    <Card actions={<Button size="sm" icon={Plus} onClick={() => setCreating(true)}>New goal</Button>} title="Goals">
      <FilterBar onReset={reset}>
        <TextInput label="Search" placeholder="Goal title" value={filters.q} maxLength={100} onChange={(e) => update({ q: e.target.value })} className="w-56" />
        <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={GOAL_STATUSES} placeholder="All" className="w-40" />
        <Select label="Whose" value={filters.mine ? 'mine' : ''} onChange={(e) => update({ mine: e.target.value === 'mine' })} className="w-40"
          options={[{ value: '', label: 'All visible to me' }, { value: 'mine', label: 'Only mine' }]} />
      </FilterBar>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No goals yet" />}>
        {(d) => (<><DataTable caption="Goals" columns={columns} rows={d.items} onRowClick={setEditing} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      {creating && <GoalForm onClose={() => setCreating(false)} onSaved={() => { setCreating(false); list.reload() }} />}
      {editing && <GoalUpdate goal={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload() }} />}
    </Card>
  )
}

function ReviewDetail({ reviewId, onClose, onChanged }) {
  const { user } = useAuth()
  const detail = useApi(`/performance/reviews/${reviewId}`)
  const [draft, setDraft] = useState(null)
  const action = useAction()
  const r = detail.data
  const editableSelf = r && r.your_role === 'employee' && r.status === 'ASSIGNED'
  const text = draft ?? r?.self_assessment ?? ''

  const save = () => action.run(() => api.patch(`/performance/reviews/${r.id}`, { self_assessment: text.trim(), expected_version: r.version }),
    { success: 'Draft saved', onSuccess: () => { setDraft(null); detail.reload() } })
  const submit = () => action.run(() => api.post(`/performance/reviews/${r.id}/submit-self-assessment`, { self_assessment: text.trim(), expected_version: r.version }),
    { success: 'Self-assessment submitted', onSuccess: () => { setDraft(null); detail.reload(); onChanged() } })
  const list = (label, items) => items?.length ? (
    <div><h3 className="text-xs font-semibold uppercase text-slate-500">{label}</h3><ul className="mt-1 list-inside list-disc text-sm text-slate-800">{items.map((i) => <li key={i}>{i}</li>)}</ul></div>
  ) : null

  return (
    <Dialog open title={r ? `Performance review #${r.id}` : 'Performance review'} onClose={onClose} wide>
      <AsyncContent result={detail}>
        {(review) => (
          <div className="space-y-4">
            <DetailList items={[
              ['Employee', `${review.employee.full_name} (${review.employee.employee_code})`], ['Status', <StatusBadge key="s" value={review.status} />],
              ['Your role', humanize(review.your_role)], ['Self-assessment submitted', formatDateTime(review.self_submitted_at)],
              ['Finalized', formatDateTime(review.finalized_at)],
            ]} />
            <div>
              <h3 className="text-xs font-semibold uppercase text-slate-500">Self-assessment</h3>
              {editableSelf ? (
                <div className="mt-2 space-y-2">
                  <TextArea label="Your self-assessment" value={text} maxLength={8000} onChange={(e) => setDraft(e.target.value)} error={action.errors.self_assessment}
                    hint="Drafts are visible only to you until you submit." />
                  {action.message && !action.errors.self_assessment && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
                  <div className="flex justify-end gap-2">
                    <Button variant="secondary" onClick={save} loading={action.busy} disabled={!text.trim()}>Save draft</Button>
                    <Button onClick={submit} loading={action.busy} disabled={!text.trim()}>Submit</Button>
                  </div>
                </div>
              ) : (
                <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{review.self_assessment || 'Not shared yet.'}</p>
              )}
            </div>
            {review.manager_evaluation_visible ? (
              <div className="space-y-3 border-t border-slate-100 pt-4">
                <h3 className="text-xs font-semibold uppercase text-slate-500">Manager evaluation</h3>
                {review.overall_rating && <p className="text-sm">Overall rating: <span className="font-semibold">{review.overall_rating}</span> — {review.rating_label}</p>}
                {review.manager_summary && <p className="whitespace-pre-line text-sm text-slate-800">{review.manager_summary}</p>}
                {list('Strengths', review.strengths)}
                {list('Improvement areas', review.improvement_areas)}
                {list('Development goals', review.development_goals)}
                {list('Training recommendations', review.training_recommendations)}
                {review.manager_private_notes && <Notice tone="warning">Private reviewer notes (never shown to the employee): {review.manager_private_notes}</Notice>}
              </div>
            ) : (
              <Notice>The manager evaluation and rating are shared with you once the review is finalized.</Notice>
            )}
            {review.your_role === 'reviewer' && ['ASSIGNED', 'SELF_SUBMITTED'].includes(review.status) && (
              <Evaluation key={review.version} review={review} onSaved={() => { detail.reload(); onChanged() }} />
            )}
            {isHrAdmin(user) && review.your_role !== 'employee' && (
              <AdminActions review={review} onDone={() => { detail.reload(); onChanged() }} />
            )}
          </div>
        )}
      </AsyncContent>
    </Dialog>
  )
}

const lines = (value) => value.split('\n').map((s) => s.trim()).filter(Boolean)
const RATINGS = [['1', 'Needs significant improvement'], ['2', 'Partially meets expectations'], ['3', 'Meets expectations'],
  ['4', 'Exceeds expectations'], ['5', 'Consistently exceeds expectations']]

// The assigned reviewer's evaluation: save as a draft (PATCH) or submit it (POST submit-manager-review). Deadlines and
// the self-assessment rule are enforced by the server (409 with an explanation).
function Evaluation({ review, onSaved }) {
  const [form, setForm] = useState({
    overall_rating: review.overall_rating ? String(review.overall_rating) : '', manager_summary: review.manager_summary ?? '',
    strengths: (review.strengths ?? []).join('\n'), improvement_areas: (review.improvement_areas ?? []).join('\n'),
    development_goals: (review.development_goals ?? []).join('\n'), training_recommendations: (review.training_recommendations ?? []).join('\n'),
    manager_private_notes: review.manager_private_notes ?? '',
  })
  const action = useAction()
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const body = () => {
    const values = { manager_summary: form.manager_summary.trim() || null, manager_private_notes: form.manager_private_notes.trim() || null,
      overall_rating: form.overall_rating ? Number(form.overall_rating) : null }
    for (const key of ['strengths', 'improvement_areas', 'development_goals', 'training_recommendations']) values[key] = lines(form[key])
    return { ...values, expected_version: review.version }
  }
  const tooMany = ['strengths', 'improvement_areas', 'development_goals', 'training_recommendations'].find((k) => lines(form[k]).length > 10)
  const save = () => action.run(() => api.patch(`/performance/reviews/${review.id}`, body()), { success: 'Evaluation draft saved', onSuccess: onSaved })
  const submit = () => action.run(() => api.post(`/performance/reviews/${review.id}/submit-manager-review`, body()),
    { success: 'Evaluation submitted for finalization', onSuccess: onSaved })
  const listField = (key, label) => (
    <TextArea label={label} value={form[key]} onChange={set(key)} hint="One item per line, at most 10" error={action.errors[key]} />
  )
  return (
    <div className="space-y-3 border-t border-slate-100 pt-4">
      <h3 className="text-xs font-semibold uppercase text-slate-500">Your evaluation (reviewer)</h3>
      <Select label="Overall rating" value={form.overall_rating} onChange={set('overall_rating')} placeholder="Not rated yet"
        options={RATINGS.map(([value, label]) => ({ value, label: `${value} — ${label}` }))} error={action.errors.overall_rating} />
      <TextArea label="Summary" value={form.manager_summary} maxLength={8000} onChange={set('manager_summary')} error={action.errors.manager_summary} />
      {listField('strengths', 'Strengths')}
      {listField('improvement_areas', 'Improvement areas')}
      {listField('development_goals', 'Development goals')}
      {listField('training_recommendations', 'Training recommendations')}
      <TextArea label="Private notes" value={form.manager_private_notes} maxLength={2000} onChange={set('manager_private_notes')}
        hint="Visible only to reviewers and HR, never to the employee" error={action.errors.manager_private_notes} />
      {tooMany && <p role="alert" className="text-sm text-red-700">{humanize(tooMany)}: at most 10 items.</p>}
      {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" onClick={save} loading={action.busy} disabled={Boolean(tooMany)}>Save draft</Button>
        <Button onClick={submit} loading={action.busy} disabled={Boolean(tooMany) || !form.overall_rating || !form.manager_summary.trim()}>Submit evaluation</Button>
      </div>
      <p className="text-xs text-slate-500">Submitting needs a rating and a summary. The employee sees the evaluation only after HR finalizes the review.</p>
    </div>
  )
}

// ADMIN/HR (never on their own review): finalize a submitted review, reopen a finalized one for correction, or cancel.
function AdminActions({ review, onDone }) {
  const [mode, setMode] = useState(null)
  const [reason, setReason] = useState('')
  const action = useAction()
  const minimum = mode === 'reopen' ? 10 : 5
  const done = (success) => ({ success, onSuccess: () => { setMode(null); setReason(''); onDone() } })
  const version = { expected_version: review.version }
  const confirm = () => {
    if (mode === 'finalize') return action.run(() => api.post(`/performance/reviews/${review.id}/finalize`, version), done('Review finalized'))
    if (reason.trim().length < minimum) return undefined
    return action.run(() => api.post(`/performance/reviews/${review.id}/${mode}`, { ...version, reason: reason.trim() }),
      done(mode === 'reopen' ? 'Review reopened for correction' : 'Review cancelled'))
  }
  const available = [
    review.status === 'MANAGER_SUBMITTED' && ['finalize', 'Finalize'],
    review.status === 'FINALIZED' && ['reopen', 'Reopen for correction'],
    ['ASSIGNED', 'SELF_SUBMITTED', 'MANAGER_SUBMITTED'].includes(review.status) && ['cancel', 'Cancel review'],
  ].filter(Boolean)
  if (!available.length) return null
  return (
    <div className="space-y-3 border-t border-slate-100 pt-4">
      <h3 className="text-xs font-semibold uppercase text-slate-500">HR actions</h3>
      {!mode ? (
        <div className="flex flex-wrap gap-2">
          {available.map(([key, label]) => <Button key={key} variant={key === 'finalize' ? 'primary' : 'secondary'} onClick={() => setMode(key)}>{label}</Button>)}
        </div>
      ) : (
        <>
          <p className="text-sm text-slate-700">{{
            finalize: 'Finalize this review? The evaluation and rating become visible to the employee.',
            reopen: 'Reopen this finalized review for correction? The reviewer can change the evaluation again.',
            cancel: 'Cancel this review? It cannot be resumed.' }[mode]}</p>
          {mode !== 'finalize' && <TextArea label="Reason" required value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)}
            hint={`At least ${minimum} characters; stored on the review, not in the audit log`} error={reason && reason.trim().length < minimum ? `At least ${minimum} characters` : undefined} />}
          {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => { setMode(null); setReason('') }} disabled={action.busy}>Back</Button>
            <Button variant={mode === 'cancel' ? 'danger' : 'primary'} onClick={confirm} loading={action.busy}
              disabled={mode !== 'finalize' && reason.trim().length < minimum}>Confirm</Button>
          </div>
        </>
      )}
    </div>
  )
}

// HR/ADMIN or a manager: open a review in an OPEN or IN_PROGRESS cycle. The reviewer must be ADMIN/HR or the employee's
// department manager (never the employee); the server checks and answers 422 otherwise.
function ReviewForm({ onClose, onSaved }) {
  const { user } = useAuth()
  const cycles = useApi('/performance/cycles', { page_size: 100 })
  const [form, setForm] = useState({ cycle_id: '', employee_id: '', reviewer_user_id: String(user.id) })
  const action = useAction()
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const ready = form.cycle_id && form.employee_id && form.reviewer_user_id
  const submit = async (event) => {
    event.preventDefault()
    if (!ready) return
    await action.run(() => api.post('/performance/reviews', { cycle_id: Number(form.cycle_id), employee_id: Number(form.employee_id),
      reviewer_user_id: Number(form.reviewer_user_id) }), { success: 'Review opened', onSuccess: onSaved })
  }
  const openCycles = (cycles.data?.items ?? []).filter((c) => ['OPEN', 'IN_PROGRESS'].includes(c.status))
  return (
    <Dialog open title="New performance review" onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="review-form" loading={action.busy} disabled={!ready}>Open review</Button></>}>
      <form id="review-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        <Select label="Cycle" required value={form.cycle_id} onChange={set('cycle_id')} placeholder={openCycles.length ? 'Select…' : 'No open cycle'}
          options={openCycles.map((c) => ({ value: String(c.id), label: c.name }))} />
        <div />
        <EmployeePicker label="Employee" required value={form.employee_id} onChange={set('employee_id')} hint="Employees you manage; never yourself" />
        <div>
          <Select label="Reviewer" value={form.reviewer_user_id === String(user.id) ? 'me' : 'other'} className="mb-2"
            onChange={(e) => setForm((f) => ({ ...f, reviewer_user_id: e.target.value === 'me' ? String(user.id) : '' }))}
            options={[{ value: 'me', label: 'Me' }, { value: 'other', label: 'Someone else' }]} />
          {form.reviewer_user_id !== String(user.id) && (
            <EmployeePicker label="Reviewer" required value={form.reviewer_user_id} onChange={set('reviewer_user_id')}
              optionValue={(e) => e.user_id} include={(e) => e.user_id !== null && e.user_id !== undefined}
              hint="Must be HR, an administrator or the employee's department manager" />
          )}
        </div>
      </form>
    </Dialog>
  )
}

function Reviews() {
  const { user } = useAuth()
  const { filters, page, setPage, update, reset } = useListState({ status: '', mine: false, to_review: false })
  const list = useApi('/performance/reviews', cleanParams({ ...filters, page, page_size: 20 }))
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)
  const columns = [
    { key: 'id', header: 'Review', render: (r) => `#${r.id}` },
    { key: 'employee', header: 'Employee', render: (r) => r.employee.full_name },
    { key: 'cycle_id', header: 'Cycle', render: (r) => `#${r.cycle_id}` },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge value={r.status} /> },
    { key: 'role', header: 'Your role', render: (r) => humanize(r.your_role) },
  ]
  return (
    <Card title="Reviews" description="Content is shown according to your role on each review."
      actions={isStaff(user) && <Button size="sm" icon={Plus} onClick={() => setCreating(true)}>New review</Button>}>
      <FilterBar onReset={reset}>
        <Select label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })} options={REVIEW_STATUSES} placeholder="All" className="w-44" />
        <Select label="Show" className="w-48" value={filters.mine ? 'mine' : filters.to_review ? 'review' : ''}
          onChange={(e) => update({ mine: e.target.value === 'mine', to_review: e.target.value === 'review' })}
          options={[{ value: '', label: 'All visible to me' }, { value: 'mine', label: 'About me' }, { value: 'review', label: 'I am the reviewer' }]} />
      </FilterBar>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No reviews" />}>
        {(d) => (<><DataTable caption="Performance reviews" columns={columns} rows={d.items} onRowClick={(r) => setSelected(r.id)} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      {selected && <ReviewDetail reviewId={selected} onClose={() => setSelected(null)} onChanged={list.reload} />}
      {creating && <ReviewForm onClose={() => setCreating(false)} onSaved={(res) => { setCreating(false); list.reload(); setSelected(res.data.id) }} />}
    </Card>
  )
}

// Cycle administration (ADMIN/HR): DRAFT -> OPEN -> IN_PROGRESS -> COMPLETED, or CANCELLED with a reason.
const CYCLE_ACTIONS = { DRAFT: [['open', 'Open cycle'], ['cancel', 'Cancel cycle']], OPEN: [['start_reviews', 'Start reviews'], ['cancel', 'Cancel cycle']],
  IN_PROGRESS: [['complete', 'Complete cycle'], ['cancel', 'Cancel cycle']] }

function CycleForm({ onClose, onSaved }) {
  const [form, setForm] = useState({ name: '', description: '', period_start: '', period_end: '', self_assessment_deadline: '', manager_review_deadline: '' })
  const action = useAction()
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))
  const ready = form.name.trim().length >= 3 && form.period_start && form.period_end && form.self_assessment_deadline && form.manager_review_deadline
  const submit = async (event) => {
    event.preventDefault()
    if (!ready) return
    await action.run(() => api.post('/performance/cycles', cleanParams({ ...form, name: form.name.trim(), description: form.description.trim() })),
      { success: 'Review cycle created (draft)', onSuccess: onSaved })
  }
  return (
    <Dialog open title="New review cycle" onClose={onClose} wide
      footer={<><Button variant="secondary" onClick={onClose} disabled={action.busy}>Cancel</Button><Button type="submit" form="cycle-form" loading={action.busy} disabled={!ready}>Create draft</Button></>}>
      <form id="cycle-form" onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {action.message && !Object.keys(action.errors).length && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{action.message}</p>}
        <TextInput label="Name" required value={form.name} onChange={set('name')} maxLength={120} error={action.errors.name} className="sm:col-span-2" />
        <TextArea label="Description" value={form.description} onChange={set('description')} maxLength={2000} className="sm:col-span-2" />
        <TextInput label="Period start" type="date" required value={form.period_start} onChange={set('period_start')} error={action.errors.period_start} />
        <TextInput label="Period end" type="date" required value={form.period_end} onChange={set('period_end')} error={action.errors.period_end} />
        <TextInput label="Self-assessment deadline" type="date" required value={form.self_assessment_deadline} onChange={set('self_assessment_deadline')} error={action.errors.self_assessment_deadline} />
        <TextInput label="Manager review deadline" type="date" required value={form.manager_review_deadline} onChange={set('manager_review_deadline')} error={action.errors.manager_review_deadline} />
        <p className="text-xs text-slate-500 sm:col-span-2">Cycles start as drafts (invisible to employees and managers) until opened.</p>
      </form>
    </Dialog>
  )
}

function CycleDetail({ cycle, onClose, onChanged }) {
  const [mode, setMode] = useState(null)
  const [reason, setReason] = useState('')
  const action = useAction()
  const confirm = () => {
    if (mode === 'cancel' && reason.trim().length < 5) return
    action.run(() => api.post(`/performance/cycles/${cycle.id}/transition`, cleanParams({ action: mode, expected_version: cycle.version,
      cancellation_reason: mode === 'cancel' ? reason.trim() : null })), { success: `Cycle: ${humanize(mode)}`, onSuccess: (res) => { setMode(null); onChanged(res.data) } })
  }
  const actions = CYCLE_ACTIONS[cycle.status] ?? []
  return (
    <Dialog open title={cycle.name} onClose={onClose} wide
      footer={!mode && actions.map(([key, label]) => <Button key={key} variant={key === 'cancel' ? 'secondary' : 'primary'} onClick={() => setMode(key)}>{label}</Button>)}>
      <DetailList items={[
        ['Status', <StatusBadge key="s" value={cycle.status} />], ['Period', `${formatDate(cycle.period_start)} – ${formatDate(cycle.period_end)}`],
        ['Self-assessment due', formatDate(cycle.self_assessment_deadline)], ['Manager review due', formatDate(cycle.manager_review_deadline)],
      ]} />
      {cycle.description && <p className="mt-3 whitespace-pre-line text-sm text-slate-800">{cycle.description}</p>}
      {mode && (
        <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
          <p className="text-sm text-slate-700">{{ open: 'Open the cycle? Reviews and goals can then be added and it becomes visible.',
            start_reviews: 'Start the review phase?', complete: 'Complete the cycle? Every review must be finalized or cancelled.',
            cancel: 'Cancel the cycle? This cannot be undone.' }[mode]}</p>
          {mode === 'cancel' && <TextArea label="Reason" required value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)}
            error={reason && reason.trim().length < 5 ? 'At least 5 characters' : undefined} />}
          {action.message && <p role="alert" className="text-sm text-red-700">{action.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setMode(null)} disabled={action.busy}>Back</Button>
            <Button variant={mode === 'cancel' ? 'danger' : 'primary'} onClick={confirm} loading={action.busy} disabled={mode === 'cancel' && reason.trim().length < 5}>Confirm</Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function Cycles() {
  const { user } = useAuth()
  const admin = isHrAdmin(user)
  const { page, setPage } = useListState({})
  const list = useApi('/performance/cycles', { page, page_size: 20 })
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState(null)
  const columns = [
    { key: 'name', header: 'Cycle', render: (c) => <span className="font-medium text-slate-900">{c.name}</span> },
    { key: 'status', header: 'Status', render: (c) => <StatusBadge value={c.status} /> },
    { key: 'period', header: 'Period', render: (c) => `${formatDate(c.period_start)} – ${formatDate(c.period_end)}` },
    { key: 'self', header: 'Self-assessment due', render: (c) => formatDate(c.self_assessment_deadline) },
    { key: 'manager', header: 'Manager review due', render: (c) => formatDate(c.manager_review_deadline) },
  ]
  return (
    <Card title="Review cycles" actions={admin && <Button size="sm" icon={Plus} onClick={() => setCreating(true)}>New cycle</Button>}>
      <AsyncContent result={list} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No review cycles visible to you" />}>
        {(d) => (<><DataTable caption="Review cycles" columns={columns} rows={d.items} onRowClick={admin ? setSelected : undefined} /><Pagination page={d.page} pages={d.pages} total={d.total} onPage={setPage} /></>)}
      </AsyncContent>
      {creating && <CycleForm onClose={() => setCreating(false)} onSaved={(res) => { setCreating(false); list.reload(); setSelected(res.data) }} />}
      {selected && <CycleDetail key={`${selected.id}-${selected.version}`} cycle={selected} onClose={() => setSelected(null)} onChanged={(next) => { setSelected(next); list.reload() }} />}
    </Card>
  )
}

export default function Performance() {
  const [searchParams] = useSearchParams()
  const [tab, setTab] = useState(searchParams.get('tab') || 'goals')
  return (
    <div>
      <PageHeader title="Performance" description="Goals, review cycles and reviews. Ratings use a 1–5 scale; evaluations are shared with the employee once finalized." />
      <Tabs active={tab} onChange={setTab} tabs={[{ id: 'goals', label: 'Goals' }, { id: 'reviews', label: 'Reviews' }, { id: 'cycles', label: 'Cycles' }]} />
      {tab === 'goals' && <Goals />}
      {tab === 'reviews' && <Reviews />}
      {tab === 'cycles' && <Cycles />}
    </div>
  )
}
