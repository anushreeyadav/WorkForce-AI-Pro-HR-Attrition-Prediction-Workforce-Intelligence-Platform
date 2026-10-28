import {
  Activity,
  BarChart3,
  Bell,
  Building2,
  CalendarDays,
  CalendarClock,
  ClipboardList,
  Database,
  FileBarChart,
  HandHeart,
  LayoutDashboard,
  Lightbulb,
  ScrollText,
  Search,
  ShieldAlert,
  Target,
  Users,
  Wallet,
  Receipt,
} from 'lucide-react'

export const ROLES = { ADMIN: 'ADMIN', HR: 'HR', MANAGER: 'MANAGER', EMPLOYEE: 'EMPLOYEE' }
const ALL = ['ADMIN', 'HR', 'MANAGER', 'EMPLOYEE']
const STAFF = ['ADMIN', 'HR', 'MANAGER']
const HR_ADMIN = ['ADMIN', 'HR']

// One entry per implemented page. The sidebar and the route guards both read this list, so there are no links
// to missing pages. Roles mirror the backend permissions (app/core/permissions.py); the server still enforces them.
export const NAV_GROUPS = [
  {
    label: 'Overview',
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboard, roles: ALL },
      { to: '/search', label: 'Search', icon: Search, roles: ALL },
      { to: '/notifications', label: 'Notifications', icon: Bell, roles: ALL },
    ],
  },
  {
    label: 'People',
    items: [
      { to: '/employees', label: 'Employees', icon: Users, roles: ALL },
      { to: '/departments', label: 'Departments', icon: Building2, roles: ALL },
      { to: '/attendance', label: 'Attendance', icon: CalendarClock, roles: ALL },
      { to: '/leave', label: 'Leave', icon: CalendarDays, roles: ALL },
      { to: '/payslips', label: 'My payslips', icon: Receipt, roles: ALL },
      { to: '/payroll', label: 'Payroll', icon: Wallet, roles: HR_ADMIN },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { to: '/risk', label: 'Risk monitoring', icon: ShieldAlert, roles: STAFF },
      { to: '/analytics', label: 'Workforce analytics', icon: BarChart3, roles: STAFF },
      { to: '/recommendations', label: 'Recommendations', icon: Lightbulb, roles: STAFF },
      { to: '/interventions', label: 'Interventions', icon: HandHeart, roles: STAFF },
    ],
  },
  {
    label: 'Work',
    items: [
      { to: '/tasks', label: 'Tasks', icon: ClipboardList, roles: ALL },
      { to: '/performance', label: 'Performance', icon: Target, roles: ALL },
      { to: '/activity', label: 'Activity feed', icon: Activity, roles: STAFF },
    ],
  },
  {
    label: 'Governance',
    items: [
      { to: '/reports', label: 'Reports', icon: FileBarChart, roles: STAFF },
      { to: '/audit', label: 'Audit logs', icon: ScrollText, roles: STAFF },
      { to: '/datasets', label: 'Datasets', icon: Database, roles: HR_ADMIN },
    ],
  },
]

export const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items)

export function canAccess(user, path) {
  const item = NAV_ITEMS.find((entry) => entry.to === path)
  return Boolean(user && item && item.roles.includes(user.role))
}

export function hasRole(user, ...roles) {
  return Boolean(user && roles.includes(user.role))
}

export const isHrAdmin = (user) => hasRole(user, 'ADMIN', 'HR')
export const isStaff = (user) => hasRole(user, 'ADMIN', 'HR', 'MANAGER')
