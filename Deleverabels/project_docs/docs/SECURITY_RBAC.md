# Role-based access control

> Copy of the project document for this submission package. Links to source files were rewritten to point to the copies in `01_Backend_Source_Code/` and `02_Frontend_Source_Code/`; the original in the project is unchanged.

This document covers how WorkForce AI Pro decides who may call each API route and which records they may see or change. It also lists every registered route with its role requirement, and records the Sub-prompt 26 audit.

The source of truth is the code: [`backend/app/core/permissions.py`](../../01_Backend_Source_Code/backend/app/core/permissions.py) for roles, and the scope helpers named below. [`backend/tests/test_authorization_matrix.py`](../../01_Backend_Source_Code/backend/tests/test_authorization_matrix.py) fails if a route's role requirement drifts from the reviewed matrix, or if a new route is added without review.

## Two layers, both enforced on the server

1. **Role.** Every protected route declares `Depends(require_roles(*permissions.X))`.
   - The role is read from the `users` row loaded for the token's `sub`, never from token claims or request data.
   - A wrong role gets `403 {"detail": "You do not have permission to perform this action"}` before any handler code runs.
2. **Scope and object.** Inside the handler, records are filtered in SQL by shared helpers, and actions are checked against the caller's relationship to the record (owner, assignee, reviewer, department manager).
   - The helpers are `employee_access.employee_scope`/`apply_scope`, `workforce_analytics.resolve_department`, and the module `visible` helpers for tasks, interventions, recommendations, risk alerts and performance.

Conventions:
- **Out-of-scope records return 404,** so their existence is not revealed. **Visible records the caller may not change return 403.** Wrong state returns 409.
- The one documented exception: `GET /api/departments/{id}/employees` returns 403 to a manager asking about another department. Department names are public to every role, so nothing is revealed.
- The frontend hides what a role cannot use (`frontend/src/lib/navigation.js`), but it is never the security boundary.

## The four roles

| | ADMIN | HR | MANAGER | EMPLOYEE |
|---|---|---|---|---|
| Scope | Organization | Organization | Own department (current employee record) | Own records |
| Employees, departments | Read and write all | Read and write all | Read own department | Read own record |
| Attendance | Reports; correct others | Reports; correct others | Department reports | Self-service |
| Leave | Review all; cancel any; types and policies | Same as ADMIN | Review own department (not own requests) | Self-service |
| Payroll | Administration, exports | Administration, exports | Own payslips only | Own payslips only |
| Datasets / AI model | Upload, validate, train, activate; predictions | Read dataset metadata; predictions; model status | Predictions about own department | None |
| Risk alerts, interventions, recommendations | All except about themselves | Same as ADMIN | Own department, never about themselves | None |
| Analytics, activity, reports | Organization or any department | Same as ADMIN | Own department only | Report catalog only (empty) |
| Audit logs | Query and export | Query and export | Query own-department entity records | None |
| Tasks | Manage all | Manage all | Manage own department's tasks | Act on tasks assigned to them |
| Performance | Cycles; finalize, reopen, cancel reviews | Same as ADMIN | Goals and reviews for own department; review when assigned | Own goals; own self-assessment |
| Login accounts (Sub-prompt 31) | List; deactivate/reactivate; change role; end sessions (never their own status or role) | None | None | None |

Rules that apply to every role:
- **Nobody acts on records about themselves.** Nobody reviews or approves their own leave, corrects their own attendance, sees or predicts their own risk, handles interventions or recommendations about themselves, or reviews, finalizes or cancels their own performance review.
- **Only ADMIN changes roles, and never their own.** Registration always creates EMPLOYEE, and request schemas reject unknown fields (`extra="forbid"`), so a `role` field gets a 422 everywhere except the ADMIN-only `PATCH /api/users/{id}/role`. Nobody changes their own role or status; the acting administrator therefore always remains, so an active ADMIN always exists. The first ADMIN is provisioned by a reviewed operator action (see the root README).
- **Sign-in depends on the account, re-checked on every request** (one query): the account is active, the token's session version (`ver`) matches `users.token_version`, the token was not revoked by logout, and, for ADMIN and HR, the linked employee record is not INACTIVE. Deactivation, role changes and "revoke sessions" end every earlier token. Every failure is the same 401. A database outage gives 503, so revocation fails closed.
- **Manager scope follows current employment.** A MANAGER's department scope comes from their own employee record, and only while it is not INACTIVE. A departed manager keeps self-service access to their own records but loses all department access, eligibility as reviewer or assignee, and department notifications.
- **Assignment grants nothing on its own.** Being the assigned reviewer, alert assignee or intervention owner never widens visibility; the person must remain eligible. The exception is task assignees, who see and act on tasks assigned to them, as designed.

## Sensitive data

- **Payroll:** salary values are visible only to ADMIN/HR (administration and audited exports) and to the employee themselves (APPROVED/PAID records).
- **Risk:**
  - Individual risk scores and model features are visible only to ADMIN/HR/MANAGER within scope, never about themselves.
  - Reports and search expose only aggregates; risk alerts are not searchable.
- **Performance:**
  - Ratings and evaluations are hidden from the employee until FINALIZED, including in review history.
  - Private reviewer notes are shown only to the reviewer and ADMIN/HR.
- **Audit:**
  - Audit rows hold ids, field names and statuses only: never passwords, tokens, review text, salary values or risk scores.
  - Free-text justifications are withheld on read.
  - Failed sign-ins record a reason code, never the submitted email or password.
- **Errors:** the global 422 handler never echoes submitted input. 401 bodies are generic, and database errors never include driver messages.

## Sub-prompt 26 audit

All 167 registered routes were inventoried from the live routers (listed below) and reviewed against the role model, together with the handler and service code that scopes records.

### Gaps found and fixed

1. **A stale reviewer assignment granted access to a performance review.**
   - **Problem:** `visible_reviews` included every review where `reviewer_user_id` was the caller, but reviewer eligibility was checked only at assignment. A manager who later moved department, left, or lost the MANAGER role could still read the self-assessment and private notes and submit a rating. The same applied through global search.
   - **Fix:** being the reviewer no longer adds visibility by itself (`app/services/performance.py`). Every eligible reviewer is ADMIN/HR (organization scope) or the current manager of the employee's department, so eligible reviewers are unaffected.
2. **A departed manager kept department-scoped access.**
   - **Problem:** nothing in the API can deactivate a login, and setting an employee INACTIVE did not affect their MANAGER powers. A departed manager could still approve leave, read risk alerts, interventions and recommendations, manage tasks, run reports and act as reviewer.
   - **Fix:** `employee_access.managed_department_id` now gives a MANAGER department scope only while their own employee record is not INACTIVE. It is used by `employee_scope`, `tasks.viewer` and `performance.eligible_reviewer`, and `department_manager_user_ids` no longer notifies departed managers. ON_LEAVE managers keep their scope.

`require_roles` now also exposes its role set as `allowed_roles`. The new route-matrix test reads it; it is never used for decisions.

### Behaviour retained after review

The following modules were audited and their existing rules kept unchanged:
- **People and time:** auth, departments, employees, shifts, attendance, leave.
- **Pay and data:** payroll, datasets, attrition.
- **Risk and workflow:** risk monitoring and escalation, analytics, interventions, recommendations, tasks.
- **Oversight:** activity, reports, audit logs, search, notifications.

In each, every route has an explicit role dependency, records are loaded through scoped queries, identifiers from other departments return 404, and filters cannot widen scope:
- department filters go through `resolve_department`;
- cursors are bound to the caller and their filters;
- report row limits refuse rather than truncate.

### Remaining limitations

Three Sub-prompt 26 limitations were closed in Sub-prompt 31 (see [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md)): account administration (`/api/users`, ADMIN only), server-side token revocation, and authentication rate limiting. ADMIN/HR logins whose employee record is INACTIVE now lose access. Still open:
- **Rate limits are in-process,** per worker and reset on restart. They cover sign-in and registration only.
- **No refresh tokens** and no password reset or change flow.
- **Managers see phone numbers and `user_id`.** `EmployeeRead` includes them for employees in the manager's department, and the frontend shows them only to HR/ADMIN. Reports and search exclude contact details. Restricting the API field is a contract change left for review.
- **Role changes made directly in the database** take effect on the next request, because roles are always read fresh. No audit row is written for them, since the change happens outside the API.

## Running the security tests

The tests use in-memory SQLite and test-only placeholder settings from `tests/conftest.py`. They never read `backend/.env` or connect to MySQL.

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_authorization_matrix.py   # matrix + scenarios
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_rbac.py tests/test_auth_api.py tests/test_security.py
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider                                     # full suite
```

`test_account_security.py` (Sub-prompt 31) covers token revocation, session versions, account administration (permissions, no self-changes, last-admin guard, audit content), ended employment for ADMIN/HR, rate limits (including the fail-open path), fail-closed revocation on database outage, and deployment configuration.

`test_authorization_matrix.py` covers:
- **Route table:** every registered route is in the reviewed matrix, its role dependency matches, and only health, register and login are public. Only `PATCH /api/users/{user_id}/role` accepts a role, and every `/users` route is ADMIN only.
- **Authentication:** every protected route returns 401 for a missing, malformed, expired, wrongly signed, unknown-user or deactivated-user token.
- **Roles:** every role outside a route's matrix entry gets the standard 403.
- **Cross-department attempts:** managers trying other departments by id or filter, employees trying other employees, and linked workflows (tasks sourced from interventions, reviews, goals, leave) used to reach out-of-scope records.
- **Record roles:** visible-but-forbidden actions such as an assignee cancelling or a reviewer finalizing.
- **Assignment and fields:** assignment eligibility, and protected-field injection (role, status, owner, creator).
- **Privacy:** notification ownership; rating and private-note privacy, including in history and audit rows; search type restrictions; credential-free failed-login audit.
- **The two fixes above,** each with a control case.

## Route inventory

172 routes: the 167 audited in Sub-prompt 26 plus the 5 ADMIN-only `/users` routes added in Sub-prompt 31.

Roles are those of the route's `require_roles` dependency. "Any signed-in user" means the route has no role dependency and the handler scopes the data: `/auth/me`, `/auth/logout`, the report catalog (which lists only the reports the caller may run), and global search (which searches only the types the caller may read).

#### health

| Method | Path | Roles |
|---|---|---|
| GET | `/api/health` | public |
| GET | `/api/health/db` | public |

#### auth

| Method | Path | Roles |
|---|---|---|
| POST | `/api/auth/register` | public |
| POST | `/api/auth/login` | public |
| GET | `/api/auth/me` | any signed-in user |
| POST | `/api/auth/logout` | any signed-in user |

#### departments

| Method | Path | Roles |
|---|---|---|
| POST | `/api/departments` | ADMIN, HR |
| GET | `/api/departments` | all roles |
| GET | `/api/departments/{department_id}` | all roles |
| PATCH | `/api/departments/{department_id}` | ADMIN, HR |
| GET | `/api/departments/{department_id}/employees` | ADMIN, HR, MANAGER |

#### employees

| Method | Path | Roles |
|---|---|---|
| POST | `/api/employees` | ADMIN, HR |
| GET | `/api/employees` | all roles |
| GET | `/api/employees/search` | all roles |
| GET | `/api/employees/{employee_id}` | all roles |
| PATCH | `/api/employees/{employee_id}` | ADMIN, HR |
| PATCH | `/api/employees/{employee_id}/status` | ADMIN, HR |

#### shifts

| Method | Path | Roles |
|---|---|---|
| POST | `/api/shifts` | ADMIN, HR |
| GET | `/api/shifts` | all roles |
| GET | `/api/shifts/{shift_id}` | all roles |
| PATCH | `/api/shifts/{shift_id}` | ADMIN, HR |
| PATCH | `/api/shifts/{shift_id}/status` | ADMIN, HR |

#### attendance

| Method | Path | Roles |
|---|---|---|
| POST | `/api/attendance/check-in` | all roles |
| POST | `/api/attendance/check-out` | all roles |
| GET | `/api/attendance/me` | all roles |
| GET | `/api/attendance/me/summary` | all roles |
| GET | `/api/attendance` | ADMIN, HR, MANAGER |
| GET | `/api/attendance/summary` | ADMIN, HR, MANAGER |
| GET | `/api/attendance/{attendance_id}` | all roles |
| PATCH | `/api/attendance/{attendance_id}/correction` | ADMIN, HR |

#### leave

| Method | Path | Roles |
|---|---|---|
| POST | `/api/leave/types` | ADMIN, HR |
| GET | `/api/leave/types` | all roles |
| PATCH | `/api/leave/types/{leave_type_id}` | ADMIN, HR |
| POST | `/api/leave/policies` | ADMIN, HR |
| GET | `/api/leave/policies` | all roles |
| PATCH | `/api/leave/policies/{policy_id}` | ADMIN, HR |
| POST | `/api/leave/requests` | all roles |
| GET | `/api/leave/requests/me` | all roles |
| GET | `/api/leave/balances/me` | all roles |
| GET | `/api/leave/requests` | ADMIN, HR, MANAGER |
| GET | `/api/leave/requests/{request_id}` | all roles |
| POST | `/api/leave/requests/{request_id}/approve` | ADMIN, HR, MANAGER |
| POST | `/api/leave/requests/{request_id}/reject` | ADMIN, HR, MANAGER |
| POST | `/api/leave/requests/{request_id}/cancel` | all roles |
| GET | `/api/leave/conflicts` | ADMIN, HR, MANAGER |

#### notifications

| Method | Path | Roles |
|---|---|---|
| GET | `/api/notifications/me` | all roles |
| POST | `/api/notifications/{notification_id}/read` | all roles |

#### payroll

| Method | Path | Roles |
|---|---|---|
| POST | `/api/payroll/components` | ADMIN, HR |
| GET | `/api/payroll/components` | ADMIN, HR |
| PATCH | `/api/payroll/components/{component_id}` | ADMIN, HR |
| POST | `/api/payroll/profiles` | ADMIN, HR |
| GET | `/api/payroll/profiles` | ADMIN, HR |
| GET | `/api/payroll/profiles/{profile_id}` | ADMIN, HR |
| PATCH | `/api/payroll/profiles/{profile_id}` | ADMIN, HR |
| POST | `/api/payroll/periods` | ADMIN, HR |
| GET | `/api/payroll/periods` | ADMIN, HR |
| GET | `/api/payroll/periods/{period_id}` | ADMIN, HR |
| POST | `/api/payroll/periods/{period_id}/calculate` | ADMIN, HR |
| GET | `/api/payroll/periods/{period_id}/records` | ADMIN, HR |
| GET | `/api/payroll/records/{record_id}` | ADMIN, HR |
| POST | `/api/payroll/records/{record_id}/approve` | ADMIN, HR |
| POST | `/api/payroll/records/{record_id}/mark-paid` | ADMIN, HR |
| GET | `/api/payroll/me` | all roles |
| GET | `/api/payroll/me/{record_id}/payslip` | all roles |

#### attrition

| Method | Path | Roles |
|---|---|---|
| POST | `/api/ai/attrition/predict/{employee_id}` | ADMIN, HR, MANAGER |
| POST | `/api/ai/attrition/predict-batch` | ADMIN, HR, MANAGER |
| GET | `/api/ai/attrition/history/{employee_id}` | ADMIN, HR, MANAGER |
| GET | `/api/ai/attrition/summary` | ADMIN, HR |
| GET | `/api/ai/attrition/model-status` | ADMIN, HR |
| POST | `/api/ai/attrition/train` | ADMIN |
| POST | `/api/ai/attrition/models/{model_version}/activate` | ADMIN |

#### datasets

| Method | Path | Roles |
|---|---|---|
| POST | `/api/datasets` | ADMIN |
| POST | `/api/datasets/{dataset_id}/versions` | ADMIN |
| GET | `/api/datasets` | ADMIN, HR |
| GET | `/api/datasets/{dataset_id}` | ADMIN, HR |
| GET | `/api/datasets/{dataset_id}/versions` | ADMIN, HR |
| POST | `/api/datasets/{dataset_id}/validate` | ADMIN |
| GET | `/api/datasets/{dataset_id}/validation` | ADMIN, HR |
| POST | `/api/datasets/{dataset_id}/archive` | ADMIN |
| GET | `/api/datasets/{dataset_id}/download` | ADMIN |

#### risk_monitoring

| Method | Path | Roles |
|---|---|---|
| POST | `/api/risk-monitoring/run` | ADMIN, HR |
| GET | `/api/risk-monitoring/alerts` | ADMIN, HR, MANAGER |
| GET | `/api/risk-monitoring/alerts/{alert_id}` | ADMIN, HR, MANAGER |
| POST | `/api/risk-monitoring/alerts/{alert_id}/acknowledge` | ADMIN, HR, MANAGER |
| POST | `/api/risk-monitoring/alerts/{alert_id}/resolve` | ADMIN, HR, MANAGER |
| POST | `/api/risk-monitoring/alerts/{alert_id}/assign` | ADMIN, HR |
| GET | `/api/risk-monitoring/employees/{employee_id}/history` | ADMIN, HR, MANAGER |
| GET | `/api/risk-monitoring/summary` | ADMIN, HR, MANAGER |
| POST | `/api/risk-monitoring/escalations/evaluate` | ADMIN, HR, MANAGER |

#### workforce_analytics

| Method | Path | Roles |
|---|---|---|
| POST | `/api/workforce-analytics/snapshots/capture` | ADMIN, HR |
| GET | `/api/workforce-analytics/snapshots` | ADMIN, HR |
| GET | `/api/workforce-analytics/overview` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-analytics/trends` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-analytics/forecast` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-analytics/stability` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-analytics/retention` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-analytics/departments/{department_id}` | ADMIN, HR, MANAGER |

#### interventions

| Method | Path | Roles |
|---|---|---|
| GET | `/api/interventions/categories` | ADMIN, HR, MANAGER |
| GET | `/api/interventions/summary` | ADMIN, HR, MANAGER |
| POST | `/api/interventions` | ADMIN, HR, MANAGER |
| GET | `/api/interventions` | ADMIN, HR, MANAGER |
| GET | `/api/interventions/{intervention_id}` | ADMIN, HR, MANAGER |
| PATCH | `/api/interventions/{intervention_id}` | ADMIN, HR, MANAGER |
| POST | `/api/interventions/{intervention_id}/assign` | ADMIN, HR, MANAGER |
| POST | `/api/interventions/{intervention_id}/start` | ADMIN, HR, MANAGER |
| POST | `/api/interventions/{intervention_id}/complete` | ADMIN, HR, MANAGER |
| POST | `/api/interventions/{intervention_id}/cancel` | ADMIN, HR, MANAGER |
| POST | `/api/interventions/{intervention_id}/notes` | ADMIN, HR, MANAGER |
| GET | `/api/interventions/{intervention_id}/history` | ADMIN, HR, MANAGER |

#### recommendations

| Method | Path | Roles |
|---|---|---|
| GET | `/api/recommendations/summary` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/evaluate` | ADMIN, HR, MANAGER |
| GET | `/api/recommendations` | ADMIN, HR, MANAGER |
| GET | `/api/recommendations/{recommendation_id}` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/{recommendation_id}/accept` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/{recommendation_id}/dismiss` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/{recommendation_id}/complete` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/{recommendation_id}/create-intervention` | ADMIN, HR, MANAGER |
| GET | `/api/recommendations/{recommendation_id}/history` | ADMIN, HR, MANAGER |
| POST | `/api/recommendations/{recommendation_id}/link-task` | ADMIN, HR, MANAGER |

#### workforce_activity

| Method | Path | Roles |
|---|---|---|
| GET | `/api/workforce-activity/sources` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/feed` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/summary` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/updates` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/status` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/signals` | ADMIN, HR, MANAGER |
| GET | `/api/workforce-activity/departments/{department_id}` | ADMIN, HR, MANAGER |

#### reports

| Method | Path | Roles |
|---|---|---|
| GET | `/api/reports/catalog` | any signed-in user |
| GET | `/api/reports/employees` | ADMIN, HR, MANAGER |
| GET | `/api/reports/attendance` | ADMIN, HR, MANAGER |
| GET | `/api/reports/leave` | ADMIN, HR, MANAGER |
| GET | `/api/reports/payroll` | ADMIN, HR |
| GET | `/api/reports/attrition` | ADMIN, HR, MANAGER |
| GET | `/api/reports/workforce` | ADMIN, HR, MANAGER |
| GET | `/api/reports/activity` | ADMIN, HR, MANAGER |

#### audit_logs

| Method | Path | Roles |
|---|---|---|
| GET | `/api/audit-logs/catalog` | ADMIN, HR, MANAGER |
| GET | `/api/audit-logs` | ADMIN, HR, MANAGER |
| GET | `/api/audit-logs/export` | ADMIN, HR |

#### tasks

| Method | Path | Roles |
|---|---|---|
| GET | `/api/tasks/summary` | all roles |
| POST | `/api/tasks` | ADMIN, HR, MANAGER |
| GET | `/api/tasks` | all roles |
| GET | `/api/tasks/{task_id}` | all roles |
| PATCH | `/api/tasks/{task_id}` | all roles |
| POST | `/api/tasks/{task_id}/assign` | all roles |
| POST | `/api/tasks/{task_id}/transition` | all roles |
| GET | `/api/tasks/{task_id}/history` | all roles |

#### performance

| Method | Path | Roles |
|---|---|---|
| GET | `/api/performance/summary` | all roles |
| POST | `/api/performance/cycles` | ADMIN, HR |
| GET | `/api/performance/cycles` | all roles |
| GET | `/api/performance/cycles/{cycle_id}` | all roles |
| PATCH | `/api/performance/cycles/{cycle_id}` | all roles |
| POST | `/api/performance/cycles/{cycle_id}/transition` | all roles |
| POST | `/api/performance/goals` | all roles |
| GET | `/api/performance/goals` | all roles |
| GET | `/api/performance/goals/{goal_id}` | all roles |
| GET | `/api/performance/goals/{goal_id}/history` | all roles |
| PATCH | `/api/performance/goals/{goal_id}` | all roles |
| POST | `/api/performance/reviews` | all roles |
| GET | `/api/performance/reviews` | all roles |
| GET | `/api/performance/reviews/{review_id}` | all roles |
| GET | `/api/performance/reviews/{review_id}/history` | all roles |
| PATCH | `/api/performance/reviews/{review_id}` | all roles |
| POST | `/api/performance/reviews/{review_id}/submit-self-assessment` | all roles |
| POST | `/api/performance/reviews/{review_id}/submit-manager-review` | all roles |
| POST | `/api/performance/reviews/{review_id}/finalize` | all roles |
| POST | `/api/performance/reviews/{review_id}/reopen` | all roles |
| POST | `/api/performance/reviews/{review_id}/cancel` | all roles |

#### search

| Method | Path | Roles |
|---|---|---|
| GET | `/api/search` | any signed-in user |

#### users

| Method | Path | Roles |
|---|---|---|
| GET | `/api/users` | ADMIN |
| GET | `/api/users/{user_id}` | ADMIN |
| PATCH | `/api/users/{user_id}/status` | ADMIN |
| PATCH | `/api/users/{user_id}/role` | ADMIN |
| POST | `/api/users/{user_id}/revoke-sessions` | ADMIN |
