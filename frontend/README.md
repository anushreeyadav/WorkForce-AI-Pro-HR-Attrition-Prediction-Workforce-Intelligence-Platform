# WorkForce AI Pro — frontend

React 19 + Vite 8 + Tailwind CSS 4 single-page app for the WorkForce AI Pro backend. It uses React Router 7, Axios, Recharts 3 and lucide-react, with no other runtime dependencies.

## Running

The backend must be running on `http://127.0.0.1:8000` (see the root README).

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173; Vite proxies /api to 127.0.0.1:8000
npm run lint       # oxlint (.oxlintrc.json)
npm test           # unit tests for session, feed, navigation and error logic (Node's built-in test runner; no extra dependency)
npm run build      # production bundle in dist/
npm run preview    # serve the built bundle locally
```

`VITE_API_BASE_URL` (see `.env.example`) overrides the API base URL. It defaults to `/api`, which goes through the dev proxy. Never put secrets in `VITE_*` variables, because they are compiled into the bundle.

## Structure

```
src/
  main.jsx, App.jsx        router, providers, lazy-loaded routes wrapped in RequireAuth / RequireRole
  index.css                Tailwind import, base colours, focus-visible outline, reduced-motion rule
  api/client.js            shared Axios instance: bearer token, 401 handling, error messages, file downloads
  auth/                    AuthProvider (session state, AuthContext.jsx) and useAuth (context.js)
  hooks/useApi.js          GET with cancellation of stale requests; loading/error/data; reload()
  hooks/useList.js         list filter + page state, debounce, useAction (guarded mutations with toasts)
  lib/navigation.js        NAV_GROUPS: one source of truth for the sidebar and route role checks
  lib/links.js             maps backend entity types (search, notifications) to pages the user can open
  lib/format.js            date, number, money, percent formatting; missing values render as "—"
  lib/session.js           which 401 ends the current session; safe post-login redirect
  lib/feed.js              activity updates: merge without duplicates, cursor recovery, polling interval
  components/EmployeePicker.jsx  search-and-select within the caller's scope (server validates every choice)
  components/ui.jsx        design-system components (Button, inputs, Card, MetricCard, DataTable,
                           Pagination, Dialog, ConfirmDialog, Tabs, badges, Notice, empty/error states…)
  components/AppLayout.jsx collapsible sidebar, mobile drawer, top bar with search, user and sign-out
  components/guards.jsx    RequireAuth (redirect to /login) and RequireRole ("Not available for your role")
  components/Toast.jsx     toast provider; useToast lives in toastContext.js
  pages/                   one module per route
```

## Pages, roles and backend endpoints

"Staff" means ADMIN, HR and MANAGER. The navigation, the route guards and the dashboard cards all use the same role list. **The backend remains the security boundary.** The UI hides what a role cannot use; it never grants access. In the table, "scoped" means the backend limits which records each role sees.

| Route | Roles | Backend endpoints |
|---|---|---|
| `/login` | public | `POST /auth/login`, `GET /health` |
| `/` Dashboard | all | `/employees`, `/departments`, `/tasks/summary`, `/tasks`, `/workforce-analytics/trends`, `/workforce-activity/feed`; staff also `/leave/requests`, `/workforce-analytics/overview`, `/attendance/summary`, `/risk-monitoring/summary`, `/workforce-analytics/stability`; employees `/leave/balances/me`, `/attendance/me/summary`; HR/Admin `/payroll/periods` |
| `/search` | all | `GET /search` |
| `/notifications` | all | `/notifications/me`, `POST /notifications/{id}/read` |
| `/employees` | all (scoped) | `/employees`, `/employees/{id}`; HR/Admin: `POST /employees`, `PATCH /employees/{id}`, `PATCH /employees/{id}/status` |
| `/departments` | all | `/departments`; HR/Admin: `POST /departments`, `PATCH /departments/{id}` |
| `/attendance` | all; Team tab for staff | `/attendance/me`, `/attendance/me/summary`, `/shifts`, `POST /attendance/check-in`, `POST /attendance/check-out`; staff: `/attendance`, `/attendance/summary` |
| `/leave` | all; Review queue for staff | `/leave/requests/me`, `/leave/types`, `/leave/balances/me`, `POST /leave/requests`, `POST /leave/requests/{id}/cancel`; staff: `/leave/requests`, `POST …/approve`, `POST …/reject` |
| `/payslips` | all | `/payroll/me`, `/payroll/me/{id}/payslip` |
| `/payroll` | ADMIN, HR | `/payroll/periods`, `POST /payroll/periods`, `POST /payroll/periods/{id}/calculate`, `/payroll/periods/{id}/records`, `/payroll/records/{id}`, `POST …/approve`, `POST …/mark-paid`, `/reports/payroll` (export) |
| `/risk` | staff | `/risk-monitoring/alerts`, `/risk-monitoring/summary`, `POST …/alerts/{id}/acknowledge`, `POST …/alerts/{id}/resolve` |
| `/analytics` | staff | `/workforce-analytics/overview`, `/stability`, `/trends`, `/forecast`; `POST /workforce-analytics/snapshots/capture` |
| `/recommendations` | staff | `/recommendations`, `/recommendations/summary`, `/recommendations/{id}`, `POST …/{id}/accept`, `…/dismiss`, `…/complete`, `POST /recommendations/evaluate` |
| `/interventions` | staff | `/interventions`, `/interventions/summary`, `/interventions/{id}`, `/interventions/{id}/history`, `POST /interventions`, `POST …/start`, `…/complete`, `…/cancel`, `…/notes`, `…/assign` |
| `/tasks` | all (scoped) | `/tasks`, `/tasks/summary`, `/tasks/{id}`, `POST /tasks`, `POST /tasks/{id}/assign`, `POST /tasks/{id}/transition` |
| `/performance` | all (scoped) | `/performance/goals`, `POST /performance/goals`, `PATCH /performance/goals/{id}`, `/performance/reviews`, `/performance/reviews/{id}`, `PATCH /performance/reviews/{id}`, `POST …/submit-self-assessment`, `POST …/submit-manager-review`, `POST …/finalize`, `…/reopen`, `…/cancel`, `POST /performance/reviews`, `/performance/cycles`, `POST /performance/cycles`, `POST …/transition` |
| `/activity` | staff | `/workforce-activity/feed`, `/workforce-activity/status`, `/workforce-activity/updates` (manual check, or opt-in auto-check at the server's recommended interval) |
| `/reports` | staff | `/reports/catalog` and the download URLs it lists |
| `/audit` | staff (scoped); export HR/Admin | `/audit-logs`, `/audit-logs/catalog`, `/audit-logs/export` |
| `/datasets` | ADMIN, HR | `/datasets`, `/datasets/{id}/versions`, `/ai/attrition/model-status`; ADMIN: `POST /datasets`, `POST /datasets/{id}/versions`, `POST /datasets/{id}/validate` |

Privacy rules the UI follows in addition to the API:
- Employee phone numbers and login-link status are shown only to HR and admins.
- Risk alerts never show the numeric score.
- The manager's review evaluation is shown only when the API marks it `manager_evaluation_visible`.
- Private notes are shown only when the API returns them.

## Behaviour

- **Session.**
  - The access token is stored in `sessionStorage`. Sign-out, closing the tab and session expiry all clear it.
  - Identity always comes from `GET /auth/me`, never from client-side data.
  - A 401 for a request that carried the current token signs the user out (a late 401 from an earlier session is ignored). The login page then shows "session expired" and, after sign-in, returns the user to the page they were on if the new account may open it.
- **Errors.**
  - `describeError` converts 401, 403, 404, 409, 422 and 5xx/network failures into short messages that don't expose server internals.
  - 422 field errors appear next to the matching fields.
  - A metric that fails to load shows "Unavailable" with the reason, never 0.
- **Mutations.**
  - Duplicate submissions are blocked while a request is in flight.
  - Success toasts appear only after the server confirms the change.
  - Destructive or audited actions ask for confirmation first: status changes, rejections, cancellations, alert resolution, check-in/out and snapshot capture.
- **Requests.**
  - Changing filters or leaving a page aborts the previous request.
  - Search inputs are debounced.
  - Nothing polls in the background, except the Activity page's opt-in auto-check (only while the page is open and the tab visible, never faster than 15 s, stopped on sign-out). It is labelled as polling, not a live stream.
- **Accessibility.**
  - A skip link jumps to the main content.
  - Form controls are labelled, and focus outlines are visible.
  - Table rows and tabs work from the keyboard.
  - Dialogs close on Escape and give focus back to where it was.
  - Reduced-motion preferences are respected.
- **Disclaimers.** Risk, analytics and recommendation pages state that their outputs are rule-based or statistical estimates for supportive review, not automated employment decisions.

## Limitations

- Component rendering is not unit-tested (no jsdom or browser test framework). `npm test` covers the pure logic; the backend suite replays the pages' exact requests (`tests/test_frontend_contract.py`, `tests/test_ui_workflows.py`). Only a limited headless-browser smoke test (sign-in page, redirects, backend unavailable) was run; signed-in journeys have not been verified in a browser.
- API-only (not in the UI): attrition-model training, activation and predictions; shift, salary-profile and leave-policy administration; attendance corrections; risk-alert assignment and monitoring runs; editing intervention fields; creating interventions or tasks from recommendations. See docs/FINAL_PROJECT_REPORT.md.
- There is no dark mode and no internationalisation: dates use the browser locale and money defaults to INR. There are no live updates.
