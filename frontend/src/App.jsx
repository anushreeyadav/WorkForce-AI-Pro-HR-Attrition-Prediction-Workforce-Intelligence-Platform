import { lazy, Suspense } from 'react'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import AppLayout from './components/AppLayout'
import { RequireAuth, RequireRole } from './components/guards'
import { ToastProvider } from './components/Toast'
import { Spinner } from './components/ui'
import Login from './pages/Login'

// Pages load on demand so the sign-in page and shell stay small.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Employees = lazy(() => import('./pages/Employees'))
const Departments = lazy(() => import('./pages/Departments'))
const Attendance = lazy(() => import('./pages/Attendance'))
const Leave = lazy(() => import('./pages/Leave'))
const Payroll = lazy(() => import('./pages/Payroll').then((m) => ({ default: m.Payroll })))
const Payslips = lazy(() => import('./pages/Payroll').then((m) => ({ default: m.Payslips })))
const Risk = lazy(() => import('./pages/Risk'))
const Analytics = lazy(() => import('./pages/Analytics'))
const Recommendations = lazy(() => import('./pages/Recommendations'))
const Interventions = lazy(() => import('./pages/Interventions'))
const Tasks = lazy(() => import('./pages/Tasks'))
const Performance = lazy(() => import('./pages/Performance'))
const Notifications = lazy(() => import('./pages/Notifications'))
const Activity = lazy(() => import('./pages/Activity'))
const Reports = lazy(() => import('./pages/Reports'))
const Audit = lazy(() => import('./pages/Audit'))
const Datasets = lazy(() => import('./pages/Datasets'))
const Search = lazy(() => import('./pages/Search'))

const PAGES = [
  ['/', Dashboard], ['/employees', Employees], ['/departments', Departments], ['/attendance', Attendance],
  ['/leave', Leave], ['/payslips', Payslips], ['/payroll', Payroll], ['/risk', Risk], ['/analytics', Analytics],
  ['/recommendations', Recommendations], ['/interventions', Interventions], ['/tasks', Tasks],
  ['/performance', Performance], ['/notifications', Notifications], ['/activity', Activity], ['/reports', Reports],
  ['/audit', Audit], ['/datasets', Datasets], ['/search', Search],
]

function NotFound() {
  return (
    <div className="py-20 text-center">
      <h1 className="text-lg font-semibold text-slate-900">Page not found</h1>
      <Link to="/" className="mt-2 inline-block text-sm text-blue-700 hover:underline">Back to the dashboard</Link>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
              {PAGES.map(([path, Page]) => (
                <Route key={path} path={path} element={
                  <RequireRole path={path}>
                    <Suspense fallback={<Spinner />}><Page /></Suspense>
                  </RequireRole>
                } />
              ))}
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
