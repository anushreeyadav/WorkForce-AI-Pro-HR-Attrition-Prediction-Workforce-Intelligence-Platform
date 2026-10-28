import { canAccess } from './navigation.js'

// Frontend page for a backend record, used by notifications and global search. Returns null when the user's
// role has no page for it (the record is then shown without a link).
const ROUTES = {
  employee: (id) => ['/employees', `?focus=${id}`],
  department: () => ['/departments', ''],
  task: (id) => ['/tasks', `?focus=${id}`],
  intervention: (id) => ['/interventions', `?focus=${id}`],
  recommendation: (id) => ['/recommendations', `?focus=${id}`],
  goal: () => ['/performance', '?tab=goals'],
  performance_goal: () => ['/performance', '?tab=goals'],
  review_cycle: () => ['/performance', '?tab=cycles'],
  performance_review: () => ['/performance', '?tab=reviews'],
  leave_request: () => ['/leave', ''],
  risk_alert: () => ['/risk', ''],
  payroll_record: () => ['/payslips', ''],
}

export function linkFor(user, type, id) {
  const route = ROUTES[type]
  if (!route) return null
  const [path, query] = route(id)
  return canAccess(user, path) ? `${path}${query}` : null
}
