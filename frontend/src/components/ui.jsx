import { useEffect, useId, useRef } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Inbox, Loader2, Lock, X } from 'lucide-react'
import { describeError } from '../api/client'
import { humanize } from '../lib/format'

// --- Buttons -------------------------------------------------------------------------------------------

const BUTTON_VARIANTS = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300',
  secondary: 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:text-slate-400',
  danger: 'bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300',
  ghost: 'text-slate-600 hover:bg-slate-100 disabled:text-slate-300',
}
const BUTTON_SIZES = { sm: 'h-8 px-3 text-sm gap-1.5', md: 'h-9 px-4 text-sm gap-2', lg: 'h-11 px-5 text-base gap-2' }

export function Button({ variant = 'primary', size = 'md', loading = false, icon: Icon, children, className = '', ...props }) {
  return (
    <button
      type="button"
      {...props}
      disabled={props.disabled || loading}
      className={`inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : Icon && <Icon className="h-4 w-4" aria-hidden="true" />}
      {children}
    </button>
  )
}

// --- Form fields --------------------------------------------------------------------------------------

const CONTROL = 'block w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 shadow-xs placeholder:text-slate-400 focus:border-blue-500 focus:outline-2 focus:outline-blue-200 disabled:bg-slate-50'

export function Field({ label, error, hint, required, children, className = '' }) {
  const id = useId()
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-slate-700">
        {label}
        {required && <span className="text-red-600"> *</span>}
      </label>
      {/* The visible message (error, otherwise hint) is announced with the control via aria-describedby */}
      {children(id, error ? `${id}-error` : hint ? `${id}-hint` : undefined)}
      {hint && !error && <p id={`${id}-hint`} className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && (
        <p id={`${id}-error`} className="mt-1 text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}

export function TextInput({ label, error, hint, required, className, ...props }) {
  return (
    <Field label={label} error={error} hint={hint} required={required} className={className}>
      {(id, describedBy) => (
        <input id={id} aria-invalid={Boolean(error)} aria-describedby={describedBy} required={required}
          className={`${CONTROL} ${error ? 'border-red-400' : 'border-slate-300'}`} {...props} />
      )}
    </Field>
  )
}

export function TextArea({ label, error, hint, required, className, ...props }) {
  return (
    <Field label={label} error={error} hint={hint} required={required} className={className}>
      {(id, describedBy) => (
        <textarea id={id} rows={3} aria-invalid={Boolean(error)} aria-describedby={describedBy} required={required}
          className={`${CONTROL} ${error ? 'border-red-400' : 'border-slate-300'}`} {...props} />
      )}
    </Field>
  )
}

export function Select({ label, error, hint, required, options, placeholder, className, ...props }) {
  return (
    <Field label={label} error={error} hint={hint} required={required} className={className}>
      {(id, describedBy) => (
        <select id={id} aria-invalid={Boolean(error)} aria-describedby={describedBy} required={required}
          className={`${CONTROL} ${error ? 'border-red-400' : 'border-slate-300'}`} {...props}>
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((option) => {
            const value = typeof option === 'object' ? option.value : option
            const text = typeof option === 'object' ? option.label : humanize(option)
            return <option key={value} value={value}>{text}</option>
          })}
        </select>
      )}
    </Field>
  )
}

export function Checkbox({ label, ...props }) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-slate-700">
      <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:outline-2 focus:outline-blue-300" {...props} />
      {label}
    </label>
  )
}

// --- Badges ------------------------------------------------------------------------------------------

const TONES = {
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  info: 'bg-blue-50 text-blue-700 ring-blue-200',
  success: 'bg-teal-50 text-teal-800 ring-teal-200',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200',
  danger: 'bg-red-50 text-red-700 ring-red-200',
}
const STATUS_TONES = {
  success: ['ACTIVE', 'APPROVED', 'COMPLETED', 'FINALIZED', 'PAID', 'VALIDATED', 'RESOLVED', 'PRESENT', 'ACCEPTED', 'CLEAR', 'ON_TRACK', 'available'],
  info: ['OPEN', 'PENDING', 'ASSIGNED', 'AVAILABLE', 'DRAFT', 'CALCULATED', 'SELF_SUBMITTED', 'MANAGER_SUBMITTED', 'NOT_STARTED', 'IN_REVIEW', 'UPLOADED', 'VALIDATING'],
  warning: ['IN_PROGRESS', 'ACKNOWLEDGED', 'LATE', 'HALF_DAY', 'ON_LEAVE', 'APPROACHING', 'AT_RISK', 'stale', 'stale_data', 'insufficient_data', 'insufficient_history'],
  danger: ['BLOCKED', 'OVERDUE', 'REJECTED', 'ABSENT', 'INVALID'],
  neutral: ['CANCELLED', 'DISMISSED', 'INACTIVE', 'SUPERSEDED', 'CLOSED', 'ARCHIVED'],
}
const PRIORITY_TONES = { LOW: 'neutral', MEDIUM: 'info', HIGH: 'warning', CRITICAL: 'danger', URGENT: 'danger', NORMAL: 'neutral' }

export function Badge({ tone = 'neutral', children }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export function StatusBadge({ value }) {
  if (!value) return <span className="text-slate-400">—</span>
  const tone = Object.entries(STATUS_TONES).find(([, values]) => values.includes(value))?.[0] ?? 'neutral'
  return <Badge tone={tone}>{humanize(value)}</Badge>
}

export function PriorityBadge({ value }) {
  if (!value) return <span className="text-slate-400">—</span>
  return <Badge tone={PRIORITY_TONES[value] ?? 'neutral'}>{humanize(value)}</Badge>
}

// --- Layout pieces ---------------------------------------------------------------------------------------

export function Card({ title, description, actions, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-xs ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function MetricCard({ label, value, hint, icon: Icon, tone = 'blue' }) {
  const toneClass = { blue: 'bg-blue-50 text-blue-700', teal: 'bg-teal-50 text-teal-700', amber: 'bg-amber-50 text-amber-700', red: 'bg-red-50 text-red-700', slate: 'bg-slate-100 text-slate-700' }[tone]
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-xs">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
        </div>
        {Icon && (
          <span className={`rounded-md p-2 ${toneClass}`}>
            <Icon className="h-5 w-5" aria-hidden="true" />
          </span>
        )}
      </div>
      {hint && <p className="mt-2 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function PageHeader({ title, description, actions, breadcrumbs }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {breadcrumbs && (
          <nav aria-label="Breadcrumb" className="mb-1 text-xs text-slate-500">
            {breadcrumbs.join(' / ')}
          </nav>
        )}
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-600">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Notice({ tone = 'info', children }) {
  const style = { info: 'border-blue-200 bg-blue-50 text-blue-900', warning: 'border-amber-200 bg-amber-50 text-amber-900' }[tone]
  return <p className={`rounded-md border px-3 py-2 text-xs ${style}`}>{children}</p>
}

// --- States -------------------------------------------------------------------------------------------

export function Spinner({ label = 'Loading' }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500">
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      {label}…
    </div>
  )
}

export function Skeleton({ rows = 4 }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-2 py-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-slate-100" style={{ width: `${90 - i * 10}%` }} />
      ))}
    </div>
  )
}

export function EmptyState({ title = 'Nothing to show', message, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <Inbox className="h-8 w-8 text-slate-300" aria-hidden="true" />
      <p className="mt-2 text-sm font-medium text-slate-700">{title}</p>
      {message && <p className="mt-1 max-w-md text-sm text-slate-500">{message}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function ErrorState({ message, status, onRetry }) {
  const denied = status === 403
  const Icon = denied ? Lock : AlertTriangle
  return (
    <div role="alert" className={`flex flex-col items-center justify-center rounded-md py-8 text-center ${denied ? 'text-slate-600' : 'text-red-700'}`}>
      <Icon className="h-7 w-7" aria-hidden="true" />
      <p className="mt-2 text-sm font-medium">{denied ? 'Not available for your role' : 'Could not load this'}</p>
      {message && <p className="mt-1 max-w-md text-sm opacity-90">{message}</p>}
      {onRetry && !denied && (
        <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// Renders loading / error / empty / content for a useApi() result.
export function AsyncContent({ result, isEmpty, empty, skeleton = 4, children }) {
  if (result.loading && !result.data) return <Skeleton rows={skeleton} />
  if (result.error) {
    return <ErrorState status={result.error.response?.status} message={describeError(result.error)} onRetry={result.reload} />
  }
  if (!result.data) return null
  if (isEmpty?.(result.data)) return empty ?? <EmptyState />
  return children(result.data)
}

// --- Tables -------------------------------------------------------------------------------------------

export function DataTable({ columns, rows, rowKey = (row) => row.id, onRowClick, caption }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-slate-50">
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={`whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 ${column.className ?? ''}`}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? 'cursor-pointer hover:bg-slate-50 focus-within:bg-slate-50' : ''}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => (e.key === 'Enter' ? onRowClick(row) : null) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
            >
              {columns.map((column) => (
                <td key={column.key} className={`px-3 py-2 align-top text-slate-700 ${column.className ?? ''}`}>
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Pagination({ page, pages, total, onPage }) {
  if (!total) return null
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between border-t border-slate-100 pt-3 text-sm text-slate-600">
      <span>
        Page {page} of {Math.max(pages, 1)} · {total} record{total === 1 ? '' : 's'}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" icon={ChevronLeft} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </nav>
  )
}

export function FilterBar({ children, onReset }) {
  return (
    <div className="mb-4 flex flex-wrap items-end gap-3">
      {children}
      {onReset && (
        <Button variant="ghost" size="sm" onClick={onReset}>
          Reset filters
        </Button>
      )}
    </div>
  )
}

// --- Dialogs ------------------------------------------------------------------------------------------

export function Dialog({ open, title, onClose, children, footer, wide = false }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const previous = document.activeElement
    ref.current?.querySelector('input, select, textarea, button')?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 sm:items-center">
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title}
        className={`w-full ${wide ? 'max-w-3xl' : 'max-w-lg'} rounded-lg bg-white shadow-xl`}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="rounded p-1 text-slate-500 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-blue-600">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function ConfirmDialog({ open, title, message, confirmLabel = 'Confirm', danger = false, busy = false, onConfirm, onCancel, children }) {
  return (
    <Dialog open={open} title={title} onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={busy}>{confirmLabel}</Button>
        </>
      }>
      {message && <p className="text-sm text-slate-700">{message}</p>}
      {children}
    </Dialog>
  )
}

export function DetailList({ items }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
          <dd className="mt-0.5 text-sm text-slate-900">{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

// --- Tabs ---------------------------------------------------------------------------------------------

export function Tabs({ tabs, active, onChange }) {
  const onKeyDown = (event) => {
    const index = tabs.findIndex((t) => t.id === active)
    if (event.key === 'ArrowRight') onChange(tabs[(index + 1) % tabs.length].id)
    if (event.key === 'ArrowLeft') onChange(tabs[(index - 1 + tabs.length) % tabs.length].id)
  }
  return (
    <div role="tablist" aria-label="Sections" onKeyDown={onKeyDown} className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((tab) => (
        <button key={tab.id} type="button" role="tab" aria-selected={tab.id === active} tabIndex={tab.id === active ? 0 : -1}
          onClick={() => onChange(tab.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-blue-600 ${
            tab.id === active ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
          {tab.label}
        </button>
      ))}
    </div>
  )
}
