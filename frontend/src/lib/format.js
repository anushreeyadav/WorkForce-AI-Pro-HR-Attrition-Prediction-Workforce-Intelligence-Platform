// Display formatting. Missing values render as an em dash, never as 0.

export const DASH = '—'

export function formatDate(value) {
  if (!value) return DASH
  const date = typeof value === 'string' && value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value)
  if (Number.isNaN(date.getTime())) return DASH
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(value) {
  if (!value) return DASH
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return DASH
  return date.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function formatNumber(value, options) {
  if (value === null || value === undefined || value === '') return DASH
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString(undefined, options) : DASH
}

export function formatMoney(value, currency) {
  if (value === null || value === undefined) return DASH
  const number = Number(value)
  if (!Number.isFinite(number)) return DASH
  try {
    return number.toLocaleString(undefined, { style: 'currency', currency: currency || 'INR' })
  } catch {
    return `${number.toFixed(2)} ${currency || ''}`.trim()
  }
}

export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined) return DASH
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toFixed(digits)}%` : DASH
}

export function formatMinutes(value) {
  if (value === null || value === undefined) return DASH
  const hours = Math.floor(value / 60)
  const minutes = value % 60
  return hours ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${minutes}m`
}

export function humanize(value) {
  if (value === null || value === undefined || value === '') return DASH
  const text = String(value).replaceAll('_', ' ').toLowerCase()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// Today and offsets as YYYY-MM-DD in the browser's local time zone.
export function isoDate(offsetDays = 0) {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function monthLabel(year, month) {
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })
}
