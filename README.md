# WorkForce AI Pro

HR Attrition Prediction & Workforce Intelligence Platform.

**Project status:** see [docs/FINAL_PROJECT_REPORT.md](docs/FINAL_PROJECT_REPORT.md) for what is implemented, what the UI exposes, what has
and has not been verified, and the remaining limitations. It is a development build: not deployed, not verified on
MySQL, and the attrition model is not validated. Staging preparation (security hardening, configuration, migration
procedure, smoke tests, go/no-go): [docs/DEPLOYMENT_READINESS.md](docs/DEPLOYMENT_READINESS.md).

## Stack

| Layer    | Technology |
|----------|------------|
| Backend  | Python 3.14, FastAPI, SQLAlchemy 2, PyMySQL, Pydantic v2, PyJWT, bcrypt |
| ML       | pandas, NumPy, scikit-learn, joblib |
| Database | MySQL 8.0 |
| Frontend | React 19, Vite 8, Tailwind CSS 4, React Router, Axios, Recharts, lucide-react |
| Tests    | pytest, HTTPX (FastAPI TestClient) |

## Layout

```
backend/    FastAPI app (app/, models in app/models/), Alembic migrations (alembic/), ML code (ml/), tests (tests/)
frontend/   React + Vite app
database/   MySQL schema and seed scripts
docs/       SECURITY_RBAC.md (role model, route inventory), PERFORMANCE.md (query-count baseline, limits,
            scaling notes), QA_REPORT.md (test results, coverage, unexecuted tests, open risks),
            FINAL_PROJECT_REPORT.md (project status: UI vs API coverage, verification, limitations, run and deploy notes),
            DEPLOYMENT_READINESS.md (security hardening, configuration, staging procedure, go/no-go)
```

## Getting started

### Backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1          # venv already created; to recreate: py -3.14 -m venv .venv
pip install -r requirements.txt
Copy-Item .env.example .env           # only if backend\.env doesn't exist yet
```

Edit `backend\.env` and set the required values:

| Variable         | Required | Notes |
|------------------|----------|-------|
| `DB_USER`        | yes      | Dedicated MySQL app user (see [database/README.md](database/README.md)) |
| `DB_PASSWORD`    | yes      | Must not be empty |
| `JWT_SECRET_KEY` | yes      | ≥ 32 random characters (≥ 48 bytes for HS384, ≥ 64 for HS512); placeholders like `change-me` are rejected |
| `JWT_ALGORITHM`  | no       | `HS256` (default), `HS384` or `HS512` only |
| `JWT_ISSUER`     | no       | Default `workforce-ai-pro`; tokens with any other issuer are rejected |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | Default `30`, allowed 1–1440 |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `CORS_ORIGINS` | no | Defaults in `.env.example` |

Generate a JWT secret:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

The server refuses to start if a required value is missing or unsafe, and the error names the field
without printing any values.

Run the server and tests:

```powershell
uvicorn app.main:app --reload         # http://127.0.0.1:8000/docs
pytest                                # no database or .env needed
```

Health checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health      # {"status":"ok"}, works without MySQL
Invoke-RestMethod http://127.0.0.1:8000/api/health/db   # {"status":"ok","database":"reachable"}, or 503 if MySQL is unreachable
```

Database setup and troubleshooting: [database/README.md](database/README.md).

### Authentication

| Endpoint | Auth | Behaviour |
|----------|------|-----------|
| `POST /api/auth/register` | public | Creates an **EMPLOYEE** account only. Body: `full_name`, `email`, `password`; any other field (including `role`) is rejected with 422. Duplicate email → 409 |
| `POST /api/auth/login` | public | Body: `email`, `password`. Returns `access_token`, `token_type: "bearer"`, `expires_in` (seconds) and safe user info. Wrong email or password → 401 `Invalid email or password`; inactive account (or ADMIN/HR whose employee record is INACTIVE) → 403; too many attempts → 429 with `Retry-After` |
| `GET /api/auth/me` | bearer | Returns `id`, `full_name`, `email`, `role`, `is_active` |
| `POST /api/auth/logout` | bearer | **Revokes the presented token on the server**; later requests with it get 401 |
| `GET/PATCH/POST /api/users…` | ADMIN | Account administration: list, deactivate/reactivate, change role, end all sessions (see [docs/DEPLOYMENT_READINESS.md](docs/DEPLOYMENT_READINESS.md)) |

Send the token as `Authorization: Bearer <access_token>`. Missing, malformed, expired or wrongly signed
tokens get 401 with `WWW-Authenticate: Bearer`.

**Passwords** are hashed with bcrypt (cost 12) and only the hash is stored. New passwords must be
12–72 characters **and** at most 72 bytes in UTF-8 (bcrypt's limit). Longer input is rejected, never
truncated. Validation errors never echo submitted values.

**Tokens** are HMAC-signed JWTs with `sub` (user id), `iss`, `iat`, `nbf`, `exp`, `jti`, `type: "access"`
and `ver` (the account's session version). No role, email or password data is put in the token. Every
request re-checks the account in one query: active, session version current, token not revoked, and (for
ADMIN/HR) employment not ended. Deactivation, role changes and "revoke sessions" end every earlier token.

**Role checks** for future routers:

```python
from fastapi import Depends
from app.api.deps import CurrentUser, require_roles
from app.models import User, UserRole

@router.get("/something")
def handler(user: User = Depends(require_roles(UserRole.ADMIN, UserRole.HR))): ...  # else 403

@router.get("/mine")
def handler(user: CurrentUser): ...  # any authenticated, active user
```

Frontend role checks are for display only; the backend dependencies are the security boundary.

**Role-based access control** (audited in Sub-prompt 26): see [docs/SECURITY_RBAC.md](docs/SECURITY_RBAC.md)
for the four-role permission model, every registered route with its required roles, the fixed gaps and the
remaining limitations.
- *Role authorization* decides whether a role may call a route (`require_roles`; a wrong role gets 403 before
  any handler code runs).
- *Department and object authorization* decides which records that caller may see or change, enforced in
  SQL by the shared scope helpers.
  - Managers are limited to the department of their own **current** employee record. A manager whose record
    is INACTIVE loses department access but keeps self-service.
  - Out-of-scope records return 404.
  - Visible records the caller may not change return 403.
- `backend/tests/test_authorization_matrix.py` fails if a route's role requirement drifts from the reviewed
  matrix, or if a route is added without review. It runs on in-memory SQLite with placeholder settings:
  `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_authorization_matrix.py`.

**Known limitations**

- Authentication rate limits are **in-process** (counted per worker process, reset on restart). A multi-worker
  or multi-host deployment needs a shared store to enforce one limit. Behind a proxy, configure forwarded client
  addresses or every user shares one budget. See [docs/DEPLOYMENT_READINESS.md](docs/DEPLOYMENT_READINESS.md).
- Users can change their own password (key icon in the header, `POST /api/auth/change-password`); this ends every
  session. There is no "forgot password" reset (it needs a configured, tested e-mail provider), no MFA and no
  refresh tokens.
- If the logout request cannot reach the server, the client discards the token but it stays valid until `exp`.

#### Provisioning the first administrator (manual, operator-only)

There is intentionally **no default admin, no hard-coded password, and no public way to create
privileged users**. The planned approach:

1. Add a local-only CLI command, for example
   `.\.venv\Scripts\python.exe -m app.cli create-admin --email admin@yourcompany.com --name "..."`.
2. The command prompts for the password with `getpass` (never a command-line argument, so it stays out
   of shell history), validates it with the same rules as registration, hashes it with bcrypt and inserts
   one `ADMIN` user in a single transaction.
3. It refuses to run if an ADMIN already exists unless an explicit `--allow-additional` flag is given,
   and it prints the target `host:port/database` and asks for confirmation before writing.
4. After that, admins promote other users (HR, MANAGER) through the ADMIN-only `PATCH /api/users/{id}/role`
   (available since Sub-prompt 31).

The CLI in steps 1–3 is still not implemented, so the **first** admin can be created only by a deliberate,
reviewed operator action: register an account, then run
`UPDATE users SET role = 'ADMIN' WHERE email = '...';` against the verified target database. Every later role
change goes through the API and is audited.

### Departments & employees

| Endpoint | Purpose |
|----------|---------|
| `POST /api/departments` | Create (201). Name is trimmed and checked for duplicates case-insensitively (409) |
| `GET /api/departments` | Paginated directory with `employee_count`; filter `is_active` |
| `GET /api/departments/{id}` | Detail with `employee_count` (404 if missing) |
| `PATCH /api/departments/{id}` | Partial update of `name`, `description`, `is_active` |
| `GET /api/departments/{id}/employees` | Paginated members; filter `status` |
| `POST /api/employees` | Create (201). Code is uppercased, work email lowercased; duplicates → 409 |
| `GET /api/employees` | Paginated list with filters and optional `q` search |
| `GET /api/employees/search` | Same filters, but `q` (2–100 chars) is required |
| `GET /api/employees/{id}` | Profile (404 if missing **or outside your scope**) |
| `PATCH /api/employees/{id}` | Partial update; omitted fields are untouched |
| `PATCH /api/employees/{id}/status` | `ACTIVE` / `INACTIVE` / `ON_LEAVE`, optional `reason` |

**Pagination:** `page` (≥ 1, default 1) and `page_size` (1–100, default 20). Every list returns
`{items, total, page, page_size, pages}`.

**Employee filters:** `department_id`, `status`, `designation` (exact, case-insensitive),
`joined_from` / `joined_to` (inclusive; `from` must not be after `to`), `user_active` (true/false: the
linked login account is active/inactive; employees without an account are excluded), `q` (substring of
code, name, work email or designation; `%` and `_` match literally), `sort_by`
(`full_name` | `employee_code` | `date_of_joining` | `created_at`) and `sort_order` (`asc` | `desc`).
Unknown query parameters and unknown body fields are rejected with 422.

**No hard deletes.** There is no DELETE for departments or employees (405). Deactivate a department with
`is_active: false`; change an employee's status instead of removing them. New employees cannot be added
to, or moved into, an inactive department (422). Every create, update and status change writes an
`audit_logs` row (who, what, and a from→to diff), committed in the same transaction as the change.

**Permissions** (enforced on the server; definitions in `backend/app/core/permissions.py`):

| Action | ADMIN | HR | MANAGER | EMPLOYEE |
|--------|-------|----|---------|----------|
| Create / update departments | ✅ | ✅ | ❌ | ❌ |
| View department directory and details | ✅ | ✅ | ✅ | ✅ |
| List a department's employees | ✅ | ✅ | own department only | ❌ |
| Create / update employees, change status | ✅ | ✅ | ❌ | ❌ |
| View / list / search employees | all | all | own department | own record only |

A MANAGER's or EMPLOYEE's scope comes from the employee record linked to their login (`employees.user_id`).
A MANAGER or EMPLOYEE with no linked record sees no employees. Records outside your scope return 404,
the same as missing records, so ids cannot be probed.

### Shifts & attendance

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/shifts` | ADMIN, HR | Create a shift (201; duplicate name, ignoring case, → 409) |
| `GET /api/shifts`, `GET /api/shifts/{id}` | all | ADMIN/HR see all shifts (filter `is_active`); other roles see **active shifts only** |
| `PATCH /api/shifts/{id}` | ADMIN, HR | Partial update; the merged start/end/break is re-validated |
| `PATCH /api/shifts/{id}/status` | ADMIN, HR | `{"is_active": false, "reason": "..."}`. There is no DELETE; deactivated shifts stay on past records |
| `POST /api/attendance/check-in` | any role with a linked employee | Body: optional `shift_id` (active shift), optional `note` |
| `POST /api/attendance/check-out` | same | Body: optional `break_minutes` (defaults to the shift's break, else 0), optional `note` |
| `GET /api/attendance/me` | same | Own history; filters `date_from`, `date_to`, `status`; paginated |
| `GET /api/attendance/me/summary` | same | Own summary; defaults to the current month (org time zone) up to today |
| `GET /api/attendance` | ADMIN, HR (all), MANAGER (own department) | Report; filters `employee_id`, `department_id`, `shift_id`, `status`, `date_from`, `date_to`; paginated, newest work date first |
| `GET /api/attendance/summary` | same as report | Aggregate summary for the same filters |
| `GET /api/attendance/{id}` | ADMIN/HR any; MANAGER own department; EMPLOYEE own only | Out-of-scope records return 404 |
| `PATCH /api/attendance/{id}/correction` | ADMIN, HR | Correct `check_in`, `check_out`, `break_minutes`, `shift_id` or `status`; `reason` (5–500 chars) required. Nobody can correct their own attendance |

Self-service endpoints never accept an employee id or timestamps. The employee is the caller's linked
record (no linked record → 404), and check-in/check-out use **server time**. Calculated fields
(`work_minutes`, `overtime_minutes`) are never accepted from clients. Date ranges are limited to 366 days.

**Time zone.** Instants (`check_in`, `check_out`) are stored and returned in UTC. Work dates and shift
times are interpreted in `ORG_TIMEZONE` (an IANA name, default `UTC`; **set it to your organization's
zone**, e.g. `Asia/Kolkata`). Invalid names stop the server from starting. Correction timestamps must
include a UTC offset.

**Rules**

| Rule | Definition |
|------|------------|
| Work date | The org-local date of check-in. For an **overnight shift** (end earlier than start, e.g. 22:00–06:00), a check-in after midnight but before the shift's end time belongs to the previous day's shift |
| One record per day | One attendance record per employee per work date. A second check-in that day, even after check-out, gets 409; mistakes are fixed through corrections |
| Open check-in | While a check-in is open you cannot check in again. Check-out more than 24 h after check-in is refused; HR must correct it |
| Late | With a shift: check-in more than `ATTENDANCE_LATE_GRACE_MINUTES` (default 15) after the scheduled start → `LATE`. Without a shift: never late |
| Work minutes | Whole minutes between check-in and check-out minus recorded break minutes. Break cannot exceed the session; a session cannot exceed 24 h |
| Overtime | Work minutes above the shift's `overtime_threshold_minutes`, or `ATTENDANCE_DEFAULT_OVERTIME_THRESHOLD_MINUTES` (default 480) without a shift |
| Half day | At check-out (or correction), work minutes below 50% of expected minutes (shift duration minus its break, or the default threshold without a shift) → `HALF_DAY`, replacing `PRESENT`/`LATE` |
| ABSENT / ON_LEAVE | Never set automatically. Only an ADMIN/HR correction sets them for now; a scheduled absence workflow will come later |

**Summaries.** `records` counts attendance rows in the range; days without a row are **not** counted as
absences. `attended_days` = PRESENT + LATE + HALF_DAY. `attendance_percentage` =
attended ÷ (attended + ABSENT records) × 100, or `null` when that denominator is 0; ON_LEAVE is excluded.

**Corrections and audit.** Each correction recomputes work date, durations and (unless overridden)
status, and writes an `attendance.corrected` audit row with the from→to diff, the employee id and the
reason. The audit row is written **in the same transaction**: if it cannot be written, the correction
is rolled back. Shift create, update and status changes are audited as `shift.*`. Normal check-in and
check-out are not audited; the record itself is the trail.

### Leave management

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/leave/types`, `PATCH /api/leave/types/{id}` | ADMIN, HR | Create / update leave types. `code` is uppercased and **cannot change**; name and code duplicates → 409 |
| `GET /api/leave/types` | all | ADMIN/HR see all (filter `is_active`); others see active types only |
| `POST /api/leave/policies`, `PATCH /api/leave/policies/{id}` | ADMIN, HR | Allowance rules; overlapping **active** policies for the same type and scope → 409 |
| `GET /api/leave/policies` | all | ADMIN/HR see all; others see active policies of active types for their department or organization-wide |
| `POST /api/leave/requests` | any role with a linked employee | `leave_type_id`, `start_date`, `end_date`, `reason` (5–500). Working days are computed by the server |
| `GET /api/leave/requests/me` | same | Own requests; filters `status`, `leave_type_id`, `date_from`, `date_to` |
| `GET /api/leave/balances/me` | same | Own balances for `year` (default: current year in `ORG_TIMEZONE`). Read-only |
| `GET /api/leave/requests/{id}` | owner, or a reviewer in scope | Out-of-scope requests return 404 |
| `POST /api/leave/requests/{id}/cancel` | owner, or ADMIN/HR | See cancellation rules |
| `GET /api/leave/requests` | ADMIN, HR (all), MANAGER (own department) | Review queue; filters `status`, `employee_id`, `department_id`, `leave_type_id`, date window |
| `POST /api/leave/requests/{id}/approve` | same | Optional `comment` |
| `POST /api/leave/requests/{id}/reject` | same | `comment` (5–500) **required** |
| `GET /api/leave/conflicts` | same | Check-ins recorded on approved-leave dates, for HR review |
| `GET /api/notifications/me`, `POST /api/notifications/{id}/read` | everyone (own only) | Minimal in-app notifications (`unread_only` filter; `sort=recent` (default) or `sort=priority`). Each item has a derived `priority`: `URGENT` (`risk_alert_overdue`), `HIGH` (`risk_alert_due_soon`, `risk_alert`), otherwise `NORMAL`. `sort=priority` orders unread first, then priority, then newest, then id. The field is additive, the default order is unchanged, and reading never changes the related record |

No leave type or policy can be deleted. Deactivate it instead.

**Policies.** `department_id` empty means organization-wide. For a request, the active policy for the
employee's department whose effective range covers the start date wins over an organization-wide one,
and the chosen policy must also cover the end date. The request records which policy was applied.
Editing a policy does not change balances that already exist (see limitations).

**Leave days.** Dates are organization-local calendar dates (`ORG_TIMEZONE`). Working days exclude
`LEAVE_WEEKEND_DAYS` (default `[5, 6]` = Saturday and Sunday) unless the policy sets `count_weekends`,
which counts every calendar day. There is **no public-holiday calendar** yet. A request must:
be within one calendar year; start no more than `LEAVE_MAX_BACKDATE_DAYS` (default 30) in the past and
no later than next year; contain at least one working day; not exceed the policy's
`max_consecutive_days` (counted in leave days); and not overlap your own PENDING or APPROVED requests.

**Balances.** One row per employee, leave type and calendar year, created when first needed, with
`allocated_days` copied from the policy and `carried_forward_days` = min(policy `carry_forward_days`,
last year's unused days). `available = allocated + carried − used − pending`.

| Event | Balance change | Status change |
|-------|----------------|---------------|
| Submit | pending += days (only if available) | → PENDING |
| Approve | pending −= days, used += days | PENDING → APPROVED |
| Reject | pending −= days | PENDING → REJECTED |
| Cancel pending | pending −= days | PENDING → CANCELLED |
| Cancel approved | used −= days | APPROVED → CANCELLED |

**Cancellation rules.** The employee can cancel a PENDING request at any time, and an APPROVED request
only **before its start date**. ADMIN/HR can cancel anyone else's PENDING or APPROVED request at any time,
but must give a comment; the employee is notified. ADMIN/HR cannot use that power on their own requests.
Managers cannot cancel. REJECTED or CANCELLED requests cannot be cancelled again (409).

**Approval rules.** Only PENDING requests can be reviewed; otherwise 409. Nobody can approve or reject
their own request (403), so a manager's own leave needs another manager in scope, HR or an admin.

**Concurrency.** Each status change is a conditional `UPDATE … WHERE status = 'PENDING'` (or the expected
status), and each balance change is a conditional `UPDATE` that re-checks availability in the same
statement. A second, concurrent approval or an overspending reservation matches no row and is rejected
(409/422) instead of double-counting. The `no_overspend` CHECK constraint is a final database guard.
On MySQL, submissions are also serialized per employee with `SELECT … FOR UPDATE`. Tests simulate these
races deterministically on SQLite (which ignores `FOR UPDATE`). **True parallel load on MySQL has not been
tested**, so verify on staging before relying on it in production.

**Transactions, audit, notifications.** Each endpoint commits once: the status change, balance change,
audit row (`leave.requested`, `leave.approved`, `leave.rejected`, `leave.cancelled`, `leave_type.*`,
`leave_policy.*`) and notifications succeed or fail together. Audit rows hold ids, dates and day counts,
**not** the employee's reason or the reviewer's comment. Notifications go to department managers on
submission, and to the employee on approval, rejection or HR cancellation. Messages contain only the
leave type and dates. **No email is sent**; email delivery is deferred.

**Attendance.** Leave never creates, edits or deletes attendance records. `approved_leave_dates()` in
`app/services/leave_attendance.py` lets reports tell approved leave apart from missing attendance.
Approving leave over dates that already have a check-in records those dates in the audit row
(`attendance_conflict_dates`). `GET /api/leave/conflicts` lists such cases for HR to resolve manually.

**Limitations.** No public holidays or half-day leave. Policy changes don't update existing balances. No
automatic absence generation and no year-end carry-forward job (carry-forward is calculated when the
new year's balance is first created). Requests can't span two calendar years. No email notifications.

### Payroll

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST/GET /api/payroll/components`, `PATCH /api/payroll/components/{id}` | ADMIN, HR | Salary component catalog. `code`, `kind` and `calculation_method` are fixed once created; deactivate with `is_active: false` |
| `POST/GET /api/payroll/profiles`, `GET/PATCH /api/payroll/profiles/{id}` | ADMIN, HR | Employee salary profiles; `components` in PATCH replaces the list. Overlapping active profiles → 409 |
| `POST/GET /api/payroll/periods`, `GET /api/payroll/periods/{id}` | ADMIN, HR | Monthly periods (one per month, 409 on duplicate) with record counts per status |
| `POST /api/payroll/periods/{id}/calculate` | ADMIN, HR | Create or recalculate records; optional `employee_ids` |
| `GET /api/payroll/periods/{id}/records` | ADMIN, HR | Paginated records; filters `status`, `department_id` |
| `GET /api/payroll/records/{id}` | ADMIN, HR | Full record with itemized lines and warnings |
| `POST /api/payroll/records/{id}/approve` | ADMIN, HR | CALCULATED → APPROVED; employee is notified |
| `POST /api/payroll/records/{id}/mark-paid` | ADMIN, HR | APPROVED → PAID; optional `payment_reference` |
| `GET /api/payroll/me` | any role with a linked employee | Your APPROVED/PAID records only |
| `GET /api/payroll/me/{record_id}/payslip` | same | Payslip data for your own APPROVED/PAID record; anything else → 404 |

**Roles.** MANAGER has no payroll administration and no access to other people's pay; no manager workflow
needs salary data. Like everyone, a manager can read their own payslips. Nobody can create or edit
their own salary profile, approve their own payroll, or mark it paid (403). Calculating a period that
includes yourself is allowed because it is mechanical. Payroll data is never returned by the
employee or department endpoints.

**Status workflow.** `CALCULATED → APPROVED → PAID`. Any other transition → 409. Calculation creates
records as CALCULATED. Running it again recalculates only CALCULATED records in place (line items
replaced, never duplicated) and **skips APPROVED and PAID records**. There is no correction workflow
for approved or paid payroll yet. A `DRAFT` status was not added because nothing in this workflow
produces one. Every transition is a conditional `UPDATE … WHERE status = <expected>`, and the
`(period, employee)` unique constraint blocks duplicates. If another transaction changes a record
mid-calculation, the whole calculation is rejected with 409 and nothing is committed. On MySQL,
calculations of the same period are also serialized with `SELECT … FOR UPDATE`. **True parallel load on
MySQL has not been tested.**

**Calculation rules** (`app/services/payroll_engine.py`, pure `Decimal`, no floats):

1. **Profile:** exactly one active profile must overlap the month. Several (a mid-month salary change)
   → the employee is skipped: proration is not supported. Inactive employees and employees without a
   profile are skipped with a reason.
2. **Earnings:** `BASIC` = base salary, then EARNING components: `FIXED` amount or `PERCENT_OF_BASE`.
3. **Gross** = sum of earnings.
4. **Deductions:** optional per-day deductions, then DEDUCTION components (`FIXED`, `PERCENT_OF_BASE`,
   `PERCENT_OF_GROSS`).
5. **Tax:** TAX components via a separate `TaxCalculator` interface. The default only applies the
   configured amounts or rates. **It is not a statutory or Indian income-tax computation** (no slabs,
   regimes, exemptions or annualisation) and is not tax advice. Replace the calculator for real tax rules.
6. **Net** = gross − deductions. A negative net → the employee is skipped with a reason, never clamped.
7. **Rounding:** each line is rounded half-up to 0.01; totals are exact sums of the rounded lines.
   Lines are ordered by (`display_order`, `code`), so identical inputs always give identical results.

No company-specific percentages or tax rates are built in; HR configures every component and value.

**Attendance and leave.** Payroll only reads attendance and leave, never changes them.
**Missing attendance is never treated as absence.** Deductions are **off by default**:

| Setting | When true |
|---------|-----------|
| `PAYROLL_DEDUCT_UNPAID_LEAVE` | Deducts base ÷ working days × days of APPROVED leave whose leave type has `is_paid = false` (days counted with the leave calendar, clipped to the month) |
| `PAYROLL_DEDUCT_RECORDED_ABSENCE` | Deducts base ÷ working days × attendance records explicitly marked `ABSENT` |

When a rule is off but matching days exist, the record gets a warning instead (for example
`2 recorded ABSENT day(s) not deducted (rule disabled)`). Records are also flagged for mid-month joining or
profile changes (no proration), open check-ins, attendance on approved-leave days, and ignored inactive
components. Working days per month use `LEAVE_WEEKEND_DAYS`.

**Audit and security.** Component, profile, period, calculation, approval and payment events are audited
in the same transaction as the change; an audit failure rolls everything back. Audit rows contain ids,
counts and changed field names, **never salary amounts or itemized pay**. "Mark paid" is an administrative
status only: no money moves and no bank is contacted. `payment_reference` must look like a payment-run
reference; values that look like account numbers are rejected.

**Payslips.** `GET /api/payroll/me/{record_id}/payslip` builds the payslip from your own persisted
APPROVED/PAID record. It includes employee code, name, department, designation, period, earnings,
deductions, totals, status and generation time. It contains no email, user id, bank details or
internal warnings. **PDF export is pending**: no PDF library is installed, and this phase added no
dependencies.

**Example** (placeholder values):

```http
POST /api/payroll/profiles
{"employee_id": 12, "base_salary": "40000.00", "effective_from": "2026-01-01",
 "components": [{"component_id": 1, "percentage": "40"},     // HRA: EARNING, PERCENT_OF_BASE
                {"component_id": 3, "percentage": "10"}]}    // WITHHOLDING: TAX, PERCENT_OF_GROSS

POST /api/payroll/periods            {"year": 2026, "month": 3}
POST /api/payroll/periods/7/calculate {}
→ {"period_id": 7, "created": 1, "recalculated": 0, "record_ids": [31], "skipped": []}

GET /api/payroll/me/31/payslip
→ {"record_id": 31, "employee": {"employee_code": "EMP-0012", "full_name": "Example Person",
   "department": "Engineering", "designation": "Engineer"},
   "period": {"id": 7, "year": 2026, "month": 3, "start_date": "2026-03-01", "end_date": "2026-03-31"},
   "currency": "INR",
   "earnings": [{"code": "BASIC", "amount": "40000.00", ...}, {"code": "HRA", "rate": "40.00", "amount": "16000.00", ...}],
   "deductions": [{"code": "WITHHOLDING", "rate": "10.00", "basis": "56000.00", "amount": "5600.00", ...}],
   "gross_earnings": "56000.00", "total_deductions": "5600.00", "net_pay": "50400.00",
   "status": "APPROVED", "generated_at": "...", "note": "..."}
```

Money is always a decimal **string** in JSON (`"40000.00"`) so no precision is lost.

**Limitations.** Monthly pay only; no proration; no correction or reversal workflow for approved or paid
payroll; no statutory tax engine; no PDF; no bank or payment integration; profile edits do not change
existing records (records are snapshots).

### AI attrition risk (decision support)

> **No real model has been trained.** The pipeline is implemented and tested on **synthetic** data only.
> Until an administrator trains a model on a legitimate dataset and it passes the evaluation gate, every
> prediction endpoint returns **503 `no_active_model`**; scores are never invented.

Full details (feature schema, exclusions, training, artifacts, score meaning): [backend/ml/README.md](backend/ml/README.md).

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/ai/attrition/predict/{employee_id}` | ADMIN, HR; MANAGER for their own department | Compute features from platform data, score, store, audit |
| `POST /api/ai/attrition/predict-batch` | same | `{"employee_ids": [...]}` (1–100, unique). Out-of-scope or unknown ids are reported as "Not found" |
| `GET /api/ai/attrition/history/{employee_id}` | same | Paginated prediction history, newest first |
| `GET /api/ai/attrition/summary` | ADMIN, HR | Latest prediction per current employee, counted by category; optional `department_id` |
| `GET /api/ai/attrition/model-status` | ADMIN, HR | Availability, active model and evaluation, recent models, feature schema, thresholds, monitoring counts |
| `POST /api/ai/attrition/train` | ADMIN | `{"dataset_id": 3, "version": 2}` (version defaults to latest). Only a VALIDATED, training-eligible registry version; never activates the model |
| `POST /api/ai/attrition/models/{model_version}/activate` | ADMIN | Activate a CANDIDATE that passed the gate; retires the previous model |

**Safeguards**

- **Employees never see risk scores, not even their own.** No employee endpoint carries risk data. Nobody can
  request or view predictions about themselves (403). Managers see only their own department; anyone
  outside scope looks "not found".
- Predictions are computed from stored data on the server. Clients cannot submit features or scores.
  Predictions **never change** employee, attendance, leave or payroll records, and never trigger actions.
- Each prediction stores only the six schema features, the score and category, the model version and a
  short explanation. Audit rows hold ids, counts, versions and outcomes, never feature values. The
  prediction and its audit row commit together; an audit failure rolls the prediction back.
- Requests made while no model is available are counted (audit action
  `ai.attrition.prediction_unavailable`) and shown in `model-status` monitoring, along with prediction
  counts per category.

**Reading a result:** `risk_score` 0–100 is the model's estimated probability × 100. It is **not a
confidence level and not proof that anyone will leave**. Categories: LOW below 40, MEDIUM below 70, HIGH
otherwise (configurable). `factors` are the strongest associations for that estimate, **not causes**.
`missing_inputs` lists data that was absent; those inputs were imputed and are never given as a reason.
Held-out metrics describe the model on its evaluation data only and do not guarantee accuracy for any
individual. Use these scores as one input to a human conversation, never as the basis for an employment
decision.

**Before real use:** assemble a legitimate, representative historical dataset with outcomes recorded
after each feature snapshot; review it with HR and legal; upload and validate it through `/api/datasets`;
train (the result is a CANDIDATE); review the metrics, including by department, for uneven error rates;
and only then activate. Also define a retention policy
for `attrition_predictions`.

### Workforce datasets

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/datasets` | ADMIN | Multipart `file` (CSV) and `display_name`. Registers version 1 as `UPLOADED`. Nothing is validated, trained or activated automatically |
| `POST /api/datasets/{id}/versions` | ADMIN | Upload a new immutable version; earlier versions never change |
| `POST /api/datasets/{id}/validate` | ADMIN | Validate the latest version (or `?version=n`) → `VALIDATED` or `REJECTED` |
| `GET /api/datasets`, `GET /api/datasets/{id}`, `GET /api/datasets/{id}/versions`, `GET /api/datasets/{id}/validation` | ADMIN, HR | Metadata and validation reports only (no row contents). Filters `status`, `include_archived` |
| `POST /api/datasets/{id}/archive` | ADMIN | Retire a dataset; files and records are kept for traceability |
| `GET /api/datasets/{id}/download` | ADMIN | Checksum-verified, audited CSV download (`Cache-Control: no-store`) |

MANAGER and EMPLOYEE have no access. HR can read metadata and reports for governance review, but cannot
upload, validate, archive or download.

**Upload rules.** CSV only, at most `DATASET_MAX_UPLOAD_BYTES` (default 10 MiB), 200,000 rows, 50 columns and
200 characters per field. The **content** is checked, not just the filename or MIME type:
- UTF-8 only (a BOM is allowed); no binary or NUL bytes.
- Well-formed CSV with the same number of fields on every line.
- Column names limited to letters, digits, spaces, `_` and `-`.

Oversized requests that declare a `Content-Length` are refused before parsing. The original filename is
**not stored**: an administrator gives a display name instead.

**Storage and integrity.** Files are written to `ML_DATASET_DIR` under a random server-generated name: the file
is written in full to a temporary name, then hard-linked into place, which never overwrites. Paths and storage
names are never accepted from or returned to clients. Every version records its SHA-256, and **identical
content can be registered only once** (409 names the existing dataset and version). Every read (validation,
training, download) re-checks the checksum and refuses symbolic links. A tampered or missing file makes
validation fail (REJECTED, with `integrity_error`), and blocks training and download (409).

**Statuses.** `UPLOADED → VALIDATING → VALIDATED | REJECTED`. `REJECTED` versions can be re-run. `VALIDATED`
versions are immutable and can be re-run only after the feature schema changes. A validation left in
`VALIDATING` by a crash can be retried after 15 minutes. `ARCHIVED` (all versions of an archived dataset) is
final. New content always means a new version.

**Validation report.** Includes:
- rows inspected, valid and invalid;
- missing, non-numeric, out-of-range and invalid-category counts per feature;
- duplicate rows, distinct valid rows and target-class distribution;
- fatal errors and warnings;
- up to 50 row-level references (`{"row", "field", "issue"}`, 1-based data rows) plus the total count;
- schema version and timestamp.

**Cell values never appear** in reports, errors, logs or audit rows. The same file and schema always produce
the same report. Rules are in [backend/ml/README.md](backend/ml/README.md).

**Training.** `POST /api/ai/attrition/train` takes a `dataset_id` (and optional `version`). The server checks
that the version is VALIDATED, training-eligible, not archived and validated against the current schema. It
then verifies the checksum, revalidates the file, trains, and records the dataset id, version and SHA-256 on
the model (`dataset_version_id`) and in its artifact metadata. Refusals are audited
(`ai.attrition.training_rejected`). A successful validation says nothing about model quality, fairness or
production readiness.

**Failure handling.** If the database step fails after a file was stored, the file is removed in the same
request. If a validation fails part-way, the version is returned to its previous status. Losing a race for
the next version number gives 409 and removes the file. Datasets and their files are **never deleted**
by the API, including when models reference them.

**Cleanup of abandoned files** (manual, for example from a scheduled task): this removes `*.partial` files and
`.csv` files with no registry row, older than one hour. Registered datasets are never touched.

```python
from datetime import UTC, datetime
from sqlalchemy import select
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models import DatasetVersion
from app.services.dataset_storage import cleanup_orphans

with get_session_factory()() as db:
    keys = set(db.scalars(select(DatasetVersion.storage_key)))
print(cleanup_orphans(get_settings().ml_dataset_dir, keys, datetime.now(UTC)))
```

**Audit.** `dataset.uploaded`, `dataset.version_created`, `dataset.validated`, `dataset.rejected`,
`dataset.archived`, `dataset.downloaded`, `ai.attrition.model_trained` and `ai.attrition.training_rejected`
are recorded with ids, versions, counts, checksums and outcomes only.

**Limitations and future governance.**
- Only CSV is supported, for attrition training only.
- Validation runs synchronously inside the request, bounded by the size limits rather than by a timeout.
- There is no retention policy or deletion workflow for datasets.
- There is no record of where each dataset came from, or of the consent and legal basis for using it; add
  this before using real employee data.
- Requests without `Content-Length` are bounded only while the file is read.
- Fairness or bias review of datasets and models must be done by people.

### Risk monitoring and alerts

> **Risk alerts are review aids, not proof of an employee's intentions, and never grounds for an automatic
> employment decision.** Monitoring reads stored predictions only. It never scores, trains or calls a model,
> and never changes employee, attendance, leave, payroll or performance data.

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/risk-monitoring/run` | ADMIN, HR | Bounded pass over current employees: `{"limit": 200, "after_employee_id": 0}`. Returns counts and `next_after_employee_id` |
| `GET /api/risk-monitoring/alerts` | ADMIN, HR (all); MANAGER (own department) | Filters `status`, `priority`, `trigger_type`, `department_id`, `employee_id`, `assigned_to_me`; paginated |
| `GET /api/risk-monitoring/alerts/{id}` | same | Alert detail (score and category snapshot only, no feature values) |
| `POST /api/risk-monitoring/alerts/{id}/acknowledge` | same | OPEN → ACKNOWLEDGED, optional `note`. Repeating it is a no-op |
| `POST /api/risk-monitoring/alerts/{id}/resolve` | same | `{"outcome": "RESOLVED" or "DISMISSED", "reason": "...", "note": "..."}`. Reason is required (5–500 characters). Repeating the same outcome is a no-op |
| `POST /api/risk-monitoring/alerts/{id}/assign` | ADMIN, HR | `{"user_id": n}`: an active ADMIN/HR user, or a MANAGER whose department covers the employee. Never the employee concerned |
| `GET /api/risk-monitoring/employees/{id}/history` | ADMIN, HR, MANAGER (scoped) | Risk-event history, newest first |
| `GET /api/risk-monitoring/summary` | same | Counts by status, active priority and department, alerts created in the last 7/30 days, events by type. Always limited to your scope |

Out-of-scope alerts, employees and history return 404, and summaries count only what you may see.
**Nobody sees alerts or history about themselves**, and EMPLOYEE has no access at all.

**Which predictions are used.** For each current (non-inactive) employee:
- The latest prediction is the newest by prediction time, then id; insertion order doesn't matter.
- It must come from a model trained on the current feature schema and not REJECTED. Otherwise the employee
  is skipped as `incompatible_model`.
- Employees without predictions are skipped as `no_prediction`. **A missing prediction is never treated
  as a score.**
- Changes are measured only against the newest **strictly earlier** prediction from the **same model
  version**. After a model change, the first prediction is a fresh baseline, because scores from different
  models aren't calibrated alike.

**Events** (an append-only history; each has a unique deduplication key built from prediction ids, so
repeated runs never duplicate one):

| Event | Rule (configurable) |
|-------|---------------------|
| `THRESHOLD_CROSSED` | Latest score ≥ `RISK_ALERT_SCORE_THRESHOLD` (70), and the previous comparable score was below it or doesn't exist |
| `SCORE_INCREASE` / `SCORE_DECREASE` | \|latest − previous\| ≥ `RISK_CHANGE_THRESHOLD_POINTS` (20) |
| `STALE_PREDICTION` | Latest prediction older than `RISK_STALE_PREDICTION_DAYS` (90): refresh it with a new prediction |

A score change reflects the model's estimate. It is not evidence that a person's circumstances or
intentions have changed.

**Alerts.**
- A threshold crossing, or an increase that ends at or above `ML_RISK_MEDIUM_THRESHOLD` (40), raises an
  alert. Decreases, small increases and stale predictions are recorded as events only.
- Priority uses the latest score only: HIGH ≥ `ML_RISK_HIGH_THRESHOLD` (70), MEDIUM ≥ 40, otherwise LOW.
  No personal attributes are used.

**Deduplication and repeated runs.**
- There is **at most one active (OPEN or ACKNOWLEDGED) alert per employee**. New qualifying events attach to
  it and increase its `event_count`.
- Priority can escalate (audited as `risk.alert_escalated`) but is never lowered automatically.
- A newer prediction updates the alert's latest score snapshot and `last_observed_at`. Rerunning over the
  same predictions changes nothing.
- Resolved or dismissed alerts are **never reopened**. Only a new qualifying event (for example, the score
  drops below the threshold and later crosses it again) creates a new alert.
- Lower scores never resolve an alert automatically; a reviewer decides.

**Lifecycle.**
- `OPEN → ACKNOWLEDGED → RESOLVED | DISMISSED`, or straight from OPEN to RESOLVED or DISMISSED.
- Every transition records who made it and when, and is audited.
- A closed alert can't be acknowledged or given a different outcome (409).
- Reviewer actions are conditional updates, so if two reviewers act at once, the second gets 409 instead of
  overwriting the first.

**Notifications.**
- When an alert is **created**, each active HR user is notified in-app. The employee concerned is never
  notified, even if they are in HR.
- The message contains only the alert id and priority: no name, score or features.
- Assignment notifies the assignee.
- Repeated observations do not notify again.
- Reading a notification does not acknowledge or resolve the alert.
- No emails are sent.

**Audit and transactions.** `risk.monitoring_run` records counts, new event ids, cursor and limit, but no
names. `risk.alert_created`, `risk.alert_escalated`, `risk.alert_acknowledged`, `risk.alert_resolved`,
`risk.alert_dismissed` and `risk.alert_assigned` record ids, statuses, priorities and model versions. A
run is a single transaction, and an audit failure rolls everything back. If a concurrent run records the
same events first, the unique keys reject this run with 409 and nothing from it is kept.

**Running it.** There is **no scheduler and no background loop**; nothing runs at startup. An ADMIN or HR
user (or a later scheduled job using such an account) calls `POST /api/risk-monitoring/run` repeatedly,
passing each response's `next_after_employee_id` as `after_employee_id`, until it is `null`. Each call
examines at most `RISK_MONITORING_MAX_BATCH` (500) employees. Refresh predictions first if many are
reported stale.

**Limitations.**
- No automatic scheduling.
- Alerts depend entirely on predictions that already exist.
- Thresholds are organization-wide.
- No email delivery. Review deadlines are evaluated only on request (see below).
- Managers are scoped by their own department only, with no reporting lines.
- Events and alerts have no retention policy yet.

#### Review deadlines (SLA) and escalation

> Escalation reminds **reviewers**; it never acts on an alert. It does not acknowledge, resolve or dismiss
> alerts, change their priority or assignee, call a model, or create alerts, interventions or recommendations.

**Deadlines.**
- Each alert's review window depends on its current priority: `ALERT_REVIEW_SLA_HOURS_HIGH` (24),
  `_MEDIUM` (72) and `_LOW` (168), with HIGH ≤ MEDIUM ≤ LOW enforced at startup.
- `review_due_at = first_detected_at + window`. `first_detected_at` is when the alert was raised and is never
  overwritten. If monitoring later raises the priority, the shorter window applies from the same start.
- "Reviewed" means acknowledged. States, calculated in UTC with timezone-aware timestamps:

| State | Rule |
|-------|------|
| `CLOSED` | `RESOLVED` or `DISMISSED` |
| `ACKNOWLEDGED` | Review has started; not escalated |
| `OVERDUE` | `OPEN` and now ≥ `review_due_at` (the deadline instant counts as overdue) |
| `APPROACHING` | `OPEN` and now ≥ `ALERT_REVIEW_APPROACHING_PERCENT` (75%) of the window |
| `ON_TRACK` | Otherwise |

- For acknowledged or closed alerts, `met_deadline` says whether the first review action came by the deadline.
- The state is **calculated when read**, never stored. Every alert response includes an `sla` object
  (`state`, `review_window_hours`, `review_due_at`, `approaching_at`, `met_deadline`), and
  `GET /api/risk-monitoring/summary` adds `active_alerts_by_sla_state`. Reading writes nothing.

**Evaluation: `POST /api/risk-monitoring/escalations/evaluate`.**
- Body: `{"limit": 200, "after_alert_id": 0, "department_id": n?}`.
- Examines up to `limit` active alerts (capped by `ALERT_ESCALATION_MAX_BATCH`, 500) in alert-id order.
  Repeat it with `next_after_alert_id` until that is `null`.
- Returns counts (`examined_alerts`, `on_track`, `approaching`, `overdue`, `acknowledged`), `new_escalations`
  (alert id, stage, priority, deadline, recipient count), `already_escalated`, `approaching_not_notified` and
  `no_eligible_recipient_alert_ids`.
- Access: ADMIN and HR organisation-wide or for one department; MANAGER only their own department (any
  other department returns 404); EMPLOYEE 403. Alerts about the caller are never included.

**Escalation policy.**
- Only `OPEN` alerts that are `APPROACHING` or `OVERDUE` are escalated. Acknowledged, resolved and dismissed
  alerts never are.
- Each (alert, stage) is escalated **at most once**: a `risk_alert_escalations` row (unique on alert + stage)
  plus one notification per recipient, in the same transaction. Repeated evaluations add nothing. A
  concurrent evaluation that records the same stage first makes this one fail with 409, keeping nothing.
- If an alert is already overdue when first evaluated, only `OVERDUE` is recorded.
- Set `ALERT_ESCALATION_NOTIFY_APPROACHING=false` to escalate only overdue alerts.
- **Recipients:** active users whose role is in `ALERT_ESCALATION_RECIPIENT_ROLES` (default ADMIN and HR;
  only these two roles are allowed). The employee the alert is about is never notified, even if they hold
  one of those roles. A manager may trigger an evaluation, but managers are not recipients.
- **No eligible recipient:** nothing is recorded or sent for that alert, and it is listed in
  `no_eligible_recipient_alert_ids`. A later evaluation escalates it once a recipient exists.
- Recipients are fixed when the stage is recorded; reviewers added later are not notified retroactively.
- Notification content is only the alert reference and priority: categories `risk_alert_due_soon` and
  `risk_alert_overdue`, for example "Risk alert #12 (HIGH priority) has not been acknowledged and is past its
  review deadline."

**Audit.**
- `risk.escalation_evaluated` (every evaluation) records counts, escalated alert ids, cursor, limit and
  department.
- `risk.alert_review_escalated` (each new escalation) records the escalation id, stage, priority, deadline and
  recipient count.
- No scores, features, names, emails or notes. An audit failure rolls the whole evaluation back.
- Reading SLA state is not audited.

**Activity feed.** Escalations appear in `/api/workforce-activity/feed` as category `risk_escalation` (kind
`RISK_EVENT`, source `risk_alert_escalations`, `event_id = risk_alert_escalation:<id>:<stage>`), with the same
scope rules as other risk items. The escalation table is the single source; escalation audit rows are not listed.

**Evaluation, not a scheduler.**
- Nothing runs automatically. An ADMIN, HR or MANAGER user (or a future external scheduler using such an
  account) must call the endpoint.
- A deadline that passes between calls is escalated at the next call.
- Run it after `POST /api/risk-monitoring/run`, so newly raised alerts are included.

**Limitations and precautions.**
- Deadlines count calendar hours: no working hours, weekends or holidays.
- Priority-based windows assume monitoring priorities are meaningful. Review the defaults before relying on them.
- The `ALERTS_AWAITING_REVIEW` activity signal uses its own coarse threshold (`ACTIVITY_ALERT_REVIEW_DAYS`) and
  covers acknowledged alerts too; it is not the SLA.
- Disabling a recipient role or deactivating all reviewers suppresses escalation; check
  `no_eligible_recipient_alert_ids`.
- Escalation history has no retention policy yet.

### Workforce analytics (forecasting, stability, retention)

Aggregate analytics only: no names, emails, employee ids, salaries or risk features appear in any response.
Every result is labelled as one of three kinds:
- **observed outcomes**: employee records, the status audit trail and captured snapshots;
- **stored AI risk indicators**: existing predictions and alerts, which are never confirmed departures;
- **estimates**: forecasts.

| Endpoint | Who | Query parameters |
|----------|-----|------------------|
| `POST /api/workforce-analytics/snapshots/capture` | ADMIN, HR | Body `{"snapshot_date": "YYYY-MM-DD"}` (optional; must be today) |
| `GET /api/workforce-analytics/snapshots` | ADMIN, HR | `date_from`, `date_to`, `page`, `page_size` |
| `GET /api/workforce-analytics/overview` | ADMIN, HR, MANAGER | `department_id` |
| `GET /api/workforce-analytics/trends` | same | `department_id`, `date_from`, `date_to` (default last 24 months, max 60) |
| `GET /api/workforce-analytics/forecast` | same | `department_id`, `horizon_months` (1 to `WORKFORCE_FORECAST_MAX_HORIZON_MONTHS`, default 6) |
| `GET /api/workforce-analytics/stability` | same | `department_id`, `by_department` (`true` adds one entry per department in scope; managers get only their own) |
| `GET /api/workforce-analytics/retention` | same | `department_id`, `date_from`, `date_to` (default last 12 months, max 60) |
| `GET /api/workforce-analytics/departments/{department_id}` | same | `horizon_months`. Returns overview, forecast, stability and retention together |

**Scope.**
- ADMIN and HR see the organisation, or any department via `department_id`.
- A MANAGER is pinned to their own department. Any other department returns the same 404 as a missing one,
  and a manager with no linked employee record gets 403.
- EMPLOYEE has no access.
- Read requests are not audited and change nothing.

**Data sources and what they can support.**

| Metric | Source | Reliability |
|--------|--------|-------------|
| Current headcount by status and department | Live employee records | Reliable |
| Joiners | `date_of_joining` (employees are never deleted) | Reliable effective date entered by HR |
| Recorded departures / reactivations | `employee.status_changed` audit rows (to / from `INACTIVE`) | Dated when **recorded**, which may lag the real last working day. Leavers created as INACTIVE, or from before the audit trail, have no date and are excluded (counted as `inactive_without_recorded_departure`) |
| Historical headcount | **Captured snapshots only** | Never reconstructed from current records; months without a snapshot are reported as not observed |
| Department of a departure | Employee's current department | Moves between departments are not tracked |

**Snapshots.**
- An explicit API call captures today's counts, overall and per department (departments with no employees are
  recorded as 0). It also records month-to-date joiners and recorded departures, plus data-quality notes.
- **One snapshot per date, immutable.** A second capture gets 409 and nothing is overwritten, and there are no
  update or delete endpoints.
- Past or future dates are refused (422): current records can't describe another day, and history is never
  backfilled.
- Captures are audited (`workforce.snapshot_captured`). There is **no scheduler**; capture on the same day
  each month (for example, the last working day) to build a monthly series.

**Trends.**
- Each month is represented by its last snapshot; months without one are `observed: false` with null values,
  never filled in.
- Monthly turnover = recorded departures in the month ÷ the average of the previous and current month-end
  active headcount, available only when both months are observed.
- Yearly turnover uses the average of the observed month-end values (at least 2 are needed). It is the rate over
  the months in range (`months_in_range`), not annualized.
- Joiners and recorded departures in each row always cover the **whole calendar month**, even when
  `date_from`/`date_to` fall mid-month, and never days after today. Future joining dates are not counted
  as observed joiners. `month_complete` is false for the current, still-running month.

**Forecast (estimate).**
- Ordinary least-squares linear trend of month-end **active headcount** against month number, fitted on observed
  months only.
- Needs at least `WORKFORCE_FORECAST_MIN_MONTHS` (6) observed months; otherwise the status is
  `insufficient_history` with no projections.
- If the latest snapshot is older than `WORKFORCE_SNAPSHOT_MAX_AGE_DAYS` (45), the status is `stale_data` with
  no projections. Projecting from an old month-end would present months that have already passed as
  estimates. Observed history is still returned.
- Each response includes `reason`, `latest_snapshot_date` and `missing_months_in_data_window` (months between
  the first and last observation without a snapshot: skipped, never filled). Projections start in the month
  after the last observed month.
- Projections are rounded half-up to whole people and never go below 0.
- A hold-out backtest (the mean absolute error over the last 3 observed months, fitted on the earlier ones) is
  reported only when there are at least 9 observed months. No other accuracy is claimed.
- It uses no individual risk scores and is not a prediction of who will leave.
- Department forecasts use that department's snapshot rows. Small departments are allowed; zero is a real value.

**Stability indicator (0–100, higher = more stable).** A documented heuristic, not a validated measure, and
never about individuals. It is computed over the last `WORKFORCE_STABILITY_WINDOW_MONTHS` (12) of snapshots:

    turnover   = 100 × max(0, 1 − annualized_turnover / 0.30)
    net_change = 100 × max(0, 1 − |net_change_rate| / 0.20)
    volatility = 100 × max(0, 1 − coefficient_of_variation / 0.10)
    score      = 0.5 × turnover + 0.25 × net_change + 0.25 × volatility   (1 decimal)

- annualized_turnover = recorded departures between the first and last snapshot ÷ average active
  headcount × 365 ÷ span (span ≥ 28 days).
- net_change_rate = (last − first) ÷ first.
- coefficient_of_variation = standard deviation ÷ mean of the active headcounts.

No score is produced when (checked in this order, `reason_code` in brackets):
- there are fewer than 3 snapshots: `insufficient_data` (`too_few_snapshots`);
- the latest snapshot is older than `WORKFORCE_SNAPSHOT_MAX_AGE_DAYS` (45): `stale_data` (`stale_snapshots`);
- the average active headcount of the snapshots in the window is below
  `WORKFORCE_STABILITY_MIN_AVERAGE_HEADCOUNT` (default 10): `insufficient_data` (`small_population`). One
  joiner or leaver in a 4-person team moves every component by 25 percentage points or more, so such a
  score would describe one person, not a workforce trend;
- any component is unavailable: `insufficient_data` (`missing_metrics`, with `missing_metrics`).

Missing metrics are `null`, never 0, and a department without reliable history is never reported as stable.
The observed metrics (`net_change_rate`, `annualized_turnover`, `coefficient_of_variation`,
`average_active_headcount`, `observations`, `span_days`) are returned even when no score is produced.
Identical data gives an identical result.

Reading the score:
- 100 means no recorded departures, no net change and no volatility over the window.
- Each component reaches 0 at its documented limit: 30% annualized turnover, a 20% net change, or a 10%
  coefficient of variation.
- Recommendation rules use `RECOMMENDATION_STABILITY_REVIEW_BELOW` (60) as a review prompt, not as a
  verdict. There are no other bands.
- The weights (turnover 50%; net change and volatility 25% each) are a documented design choice, not
  fitted to outcomes.
- Net change and volatility both react to headcount swings. Turnover is weighted highest because it is the
  only component based on recorded departures.

One implementation (`app/services/workforce_analytics.stability_report`) serves the API, the department
comparison and both recommendation strategies, so they always agree. `by_department=true` computes every
department with two grouped queries and gives exactly the result of asking for each department alone.

**Retention.**
- `observed`:
  - joiners, recorded departures and reactivations in the window;
  - turnover rate with its explicit denominator (snapshots in the window and average active headcount),
    or `unavailable` when fewer than 2 snapshots fall in the window;
  - monthly and yearly breakdowns;
  - the count of inactive employees with unknown departure dates.
- `ai_risk_indicators`, kept separate:
  - the latest stored, schema-compatible prediction per current employee in scope, counted by category,
    plus employees without predictions or with incompatible ones only;
  - risk alerts by status.
- The caller's own record is excluded from the AI counts. No model is called; risk scores and alerts are
  never counted as departures.

**What each result is, and who uses it.**

| Result | Kind | API | Frontend |
|--------|------|-----|----------|
| Current headcount, joiners, recorded departures, turnover rates | Observed | overview, trends, retention, `/api/reports/workforce` | Dashboard, Analytics |
| Stability indicator | Heuristic indicator | stability, departments/{id}; recommendation rules `AGG_LOW_STABILITY` (v1) and `V2_STABILITY_REVIEW` (v2) | Dashboard (Risk & stability card), Analytics (indicator and department comparison) |
| Headcount projection | Forecast (estimate) | forecast, departments/{id} | Analytics |
| Stored risk predictions and alerts by category | Stored AI risk indicator | retention (`ai_risk_indicators`), `/api/reports/attrition` | — |

The stability indicator uses only snapshot headcounts and recorded departures. It does not use protected
characteristics, attendance, leave, performance-review content or individual risk scores.

**Limitations.**
- Trend, forecast, stability and turnover need snapshots captured over time; until then they report
  `insufficient_history`, `insufficient_data` or `unavailable`.
- **Uneven snapshot spacing.** The stability indicator uses every snapshot in the window, so months with
  several snapshots weigh more in the volatility component. Capture on a regular schedule (for example
  monthly) for comparable results. Trends and the forecast use one month-end per month.
- **Short windows.** Turnover over a short span (28 days or more) is annualized and can be noisy.
- Departure dates are the dates they were recorded.
- Department transfers aren't tracked.
- Forecasting is linear only, without seasonality.
- There is no scheduler.
- Small-group suppression isn't applied to counts (managers already have individual access within their
  department). Small populations only suppress the stability *score*.
- **Performance is tested on SQLite only.** The analytics read bounded aggregates (snapshots, the status
  audit trail by date range or by the ids of inactive employees through `ix_audit_logs_entity`), but query
  plans and timings have not been profiled on MySQL.

### HR interventions and retention actions

Internal workflow records for supportive HR actions, for example a manager check-in, workload review, career
development, recognition, learning support or work-environment review. An intervention may reference a risk
alert, but doesn't have to.

**They never contact the employee, change employment status, or change the linked risk alert**, and they store
no prediction features. Record agreed actions and factual follow-up only, never speculation about intentions,
health or private life.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/interventions/categories` | General HR workflow options (not personalised or AI-generated recommendations) |
| `GET /api/interventions/summary` | Counts by status, active by priority and category, and overdue, within your scope |
| `POST /api/interventions` | Create: `employee_id`, `category`, `title` (3–150), optional `description` (≤1000), `priority`, `due_date` (today to +366 days), `assigned_to_user_id`, `risk_alert_id`, `idempotency_key` |
| `GET /api/interventions` | List with filters `status`, `priority`, `category`, `employee_id`, `department_id`, `risk_alert_id`, `assigned_to_me`, `overdue`, `due_from`/`due_to`; paginated |
| `GET /api/interventions/{id}` | Detail |
| `PATCH /api/interventions/{id}` | Edit `title`, `description`, `category`, `priority`, `due_date` while active; optional `expected_version` |
| `POST /api/interventions/{id}/assign` | `{"user_id": n}` |
| `POST /api/interventions/{id}/start` | OPEN → IN_PROGRESS |
| `POST /api/interventions/{id}/complete` | IN_PROGRESS → COMPLETED; `outcome` (5–1000) required |
| `POST /api/interventions/{id}/cancel` | OPEN/IN_PROGRESS → CANCELLED; `reason` (5–500) required |
| `POST /api/interventions/{id}/notes` | Add a follow-up note (≤2000) in any status |
| `GET /api/interventions/{id}/history` | Versioned history (creation, edits, assignments, status changes, notes), oldest first |

**Access.**
- ADMIN and HR: the whole organisation. MANAGER: employees in their own department. EMPLOYEE: no access (403).
- **Nobody can create, see or be assigned an intervention about themselves.**
- Out-of-scope employees, alerts and interventions return the same 404 as missing ones, and list filters and
  counts never reach outside your scope.

**Assignment (narrowest rule, the same as risk alerts).** The assignee must be an active ADMIN or HR user, or a
MANAGER whose department covers the employee, and never the employee concerned. This is checked on create and on
every reassignment.

**Workflow.**
- `OPEN → IN_PROGRESS → COMPLETED`, and `OPEN | IN_PROGRESS → CANCELLED`. Completed and cancelled are final: no
  reopening, editing or reassignment. Other transitions return 409.
- Repeating start, complete, cancel or assign-to-the-same-user returns 200 and changes nothing (no new history
  or notification). Notes are not deduplicated.
- **Concurrency:** every change is a conditional update on the record's `version`, and the history has a unique
  `(intervention_id, version)`. Two simultaneous changes cannot overwrite each other; the second gets 409.
  Clients may also send `expected_version` to detect stale edits.
- **Retries:** an `idempotency_key` on create returns the original intervention (200) with no duplicate history
  or notification. The key belongs to its creator; reuse by someone else gets 409.

**Linking to risk alerts.**
- The alert must be visible to you (otherwise 404, the same as a missing alert) and belong to the same
  employee (otherwise 422).
- The database allows **at most one active intervention per alert and category** (a unique `active_alert_key`
  that is cleared on completion or cancellation). Different categories are allowed for different purposes.
- Completing an intervention never resolves the alert, and alerts never create interventions automatically.

**Notifications and audit.**
- Only the assignee is notified, on creation or assignment, and never when they assign it to themselves. The
  message contains only the intervention id and priority. The employee concerned is never notified, and no
  email is sent.
- Audit actions: `intervention.created`, `intervention.updated` (field names only), `intervention.assigned`,
  `intervention.started`, `intervention.completed`, `intervention.cancelled` and `intervention.note_added`
  (version only). They contain ids, categories and statuses, never titles, descriptions, notes or outcomes.
- Each change commits together with its history row, audit row and notification; an audit failure rolls back
  all of them.

**Limitations.**
- No SLA reminders or escalation timers; overdue is only reported.
- Notes are not deduplicated and cannot be edited or deleted. This is deliberate, to keep the history intact.
- Description and note text is free text; reviewers are responsible for keeping it factual.
- No retention or deletion policy yet.

### Rule-based recommendations

> **Engine strategies.** `POST /api/recommendations/evaluate` accepts `"strategy": "rules-v1"` (the default,
> unchanged) or `"rules-v2"` (see *Recommendation engine: rules-v2* below). Every record keeps the `rule_version`
> that produced it, and rules-v1 records are never relabelled. Neither strategy is a trained or validated model.


> **Rule-based guidance for human review, not a trained model and not a causal finding.** Risk scores are
> estimates, not evidence of intent. Recommendations never create interventions automatically, never change
> risk alerts or employee data, never notify employees, and never suggest punitive action.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/recommendations/evaluate` | Apply the rules to existing records: `{"department_id": n?, "include_individual": true, "max_alerts": 200}`. Returns counts, new ids and the rules that were not actionable, with reasons |
| `GET /api/recommendations` | List with filters `status`, `scope`, `category`, `priority`, `rule_id`, `employee_id`, `department_id`; paginated |
| `GET /api/recommendations/summary` | Counts by status, plus active ones by category, priority and scope, within your scope |
| `GET /api/recommendations/{id}` | Detail, including `rule_id`, `rule_version`, explanation, evidence references and data freshness |
| `POST /api/recommendations/{id}/accept` | AVAILABLE → ACCEPTED |
| `POST /api/recommendations/{id}/dismiss` | AVAILABLE/ACCEPTED → DISMISSED; `reason` (5–500) required |
| `POST /api/recommendations/{id}/complete` | ACCEPTED → COMPLETED; `outcome` (5–1000) required |
| `POST /api/recommendations/{id}/create-intervention` | For an ACCEPTED individual recommendation: create an intervention through the normal intervention workflow (optional `title`, `description`, `priority`, `due_date`, `assigned_to_user_id`), or link an existing one with `intervention_id` |

**Signals used and rules (`rules-v1`).** Thresholds are configurable (`RECOMMENDATION_*`). Missing data never
triggers a rule; it is reported as `INSUFFICIENT_DATA`.

| Rule | Scope | Evidence | Suggestion |
|------|-------|----------|------------|
| `IND_ALERT_CHECKIN` | Individual | Active risk alert with no active intervention linked to it (priority = the alert's priority) | Supportive one-to-one check-in |
| `IND_WORKLOAD_REVIEW` | Individual | The alert's stored prediction is schema-compatible and fresh, and its strongest "raises" factor is overtime | Workload review |
| `IND_CAREER_CONVERSATION` | Individual | Same, strongest factor is tenure | Development conversation |
| `AGG_SNAPSHOT_FRESHNESS` | Organization | No snapshot, or the latest is older than `WORKFORCE_SNAPSHOT_MAX_AGE_DAYS` | Capture snapshots |
| `AGG_UNKNOWN_DEPARTURES` | Org / department | Inactive employees without a recorded departure date | Review leaver records |
| `AGG_TURNOVER_REVIEW` | Org / department | Annualized recorded turnover with a snapshot denominator ≥ 20% | Retention review (HIGH) |
| `AGG_LOW_STABILITY` | Org / department | Stability indicator (status ok) < 60 | Workforce trend review |
| `AGG_HEADCOUNT_DECLINE` | Org / department | Three consecutive declines across four consecutive observed month-ends | Review headcount and workload |
| `AGG_ALERT_CLUSTER` | Department | Active risk alerts in the department ≥ 3 | Team-level review of conditions (not of individuals) |

**Individual-level guidance.**
- Only for employees with an active risk alert that the caller may already see. It uses the alert and its
  **stored** prediction only; no model is called.
- **Lateness and recorded-absence factors are deliberately never mapped to a specific suggestion**, because they
  can stand in for health or family circumstances. Such cases get only the general supportive check-in.
- Stale or incompatible predictions produce no factor-based guidance.
- Responses show which input *category* was the strongest association (for example "working-hours"), never
  feature values, weights or scores.

**Aggregate guidance.** Uses snapshots, the status audit trail and stability metrics. Turnover rules need a
snapshot-based denominator. Departments are never compared with each other. Aggregate recommendations can't
create interventions.

**Persistence and deduplication.**
- Recommendations are generated **on demand by `evaluate`**, and only actionable (AVAILABLE) ones are stored, with
  the minimum fields needed for review.
- `INSUFFICIENT_DATA` and `SUPPRESSED` outcomes are returned by the evaluation but not stored.
- Each stored recommendation has a unique `dedup_key` = rule + rule version + subject + evidence window (the alert
  id for individual rules, the evaluation month for aggregate rules). Re-running an evaluation never duplicates a
  recommendation or resurrects a dismissed one; new evidence (a new alert, a new month) can create a new one.
- A concurrent evaluation that records the same keys first gets 409, and nothing from it is kept.

**Lifecycle.**
- `AVAILABLE → ACCEPTED → COMPLETED`; `AVAILABLE | ACCEPTED → DISMISSED`. Completed and dismissed are final.
- Repeating an action is a no-op.
- Every decision is a conditional update on the record's `version` (optionally checked with `expected_version`),
  so simultaneous reviewers get 409 instead of overwriting each other.
- Reviewer, time and rule version are recorded.
- `create-intervention` reuses the intervention service: the same scope, assignee and duplicate rules (one
  active intervention per alert and category), notifications only to the assignee, and the same audit trail.
  **The linked alert is never resolved.**

**Access.** Same audience as risk alerts.
- ADMIN and HR: organisation-wide.
- MANAGER: individual recommendations for their department, plus their department's aggregate ones. Never
  organisation-level ones, and evaluation is pinned to their department.
- EMPLOYEE: no access.
- Nobody sees recommendations about themselves. Out-of-scope records return 404.

**Audit and notifications.**
- `recommendation.evaluated` records counts, new ids and the rule version.
- `recommendation.accepted`, `.dismissed` and `.completed` record the rule id, rule version, from/to status and
  scope.
- `recommendation.intervention_linked` records the intervention id.
- Audit rows never contain explanation text, evidence values, reasons or outcomes.
- Evaluation sends **no notifications**, to avoid alert fatigue; reviewers use the list and summary.

**Limitations.**
- Rules and thresholds are fixed heuristics that haven't been validated against outcomes.
- Factor mapping uses only each prediction's strongest association.
- No scheduler; evaluation is explicit.
- No retention policy for recommendation records.

### Recommendation engine: rules-v2

`app/services/recommendation_engine.py` runs a documented pipeline. `app/services/recommendation_rules_v2.py`
contains only pure, deterministic rules, kept separate from persistence, serialisation and notifications.

**Pipeline.**
1. Collect authorised signals. Every source query is scoped to the caller and bounded by `max_alerts` (capped by
   `RECOMMENDATION_MAX_ALERTS_PER_EVALUATION`).
2. Check availability and freshness.
3. Evaluate the rules.
4. Produce candidates.
5. Assign priority and `rank_score`.
6. Attach evidence and an explanation.
7. Deduplicate and persist.
8. Return the candidates in rank order (`ranked` in the response).

It never trains or calls a model, never sends data to external services, and never creates interventions or tasks.

**Rules.**

| Rule | Scope | Fires when | Priority |
|------|-------|-----------|----------|
| `V2_ALERT_SUPPORT_REVIEW` | individual | An active risk alert, no active intervention, and a usable prediction. Suggests HR review whether a supportive conversation is appropriate | the alert's priority; a HIGH alert past its review deadline is CRITICAL |
| `V2_PREDICTION_REFRESH` | individual | The alert's prediction is missing, incompatible or stale. Suggests refreshing the analysis, with **no employee-specific action** | LOW (MEDIUM for a HIGH alert) |
| `V2_INTERVENTION_FOLLOW_UP` | individual | An active intervention past its due date. Suggests reviewing the plan | MEDIUM, one level up at ≥ 14 days overdue, one level up for a HIGH intervention |
| `V2_GOAL_CHECK_IN` | individual | Goals AT_RISK, or active and past their target date. Suggests a manager check-in about support | MEDIUM if any is AT_RISK, otherwise LOW |
| `V2_STABILITY_REVIEW` | organisation / department | Stability below `RECOMMENDATION_STABILITY_REVIEW_BELOW` and/or three consecutive month-end declines | MEDIUM; HIGH when both |
| `V2_TASK_FOLLOW_UP` | department | Unresolved HIGH or CRITICAL tasks past their due date (count only) | MEDIUM; HIGH if any is CRITICAL |
| `V2_REVIEW_FOLLOW_UP` | department | Performance reviews still awaiting the reviewer after the cycle's manager deadline (count only) | MEDIUM |

**Signals deliberately not used:**
- attendance and leave patterns (possible proxies for health or family circumstances);
- protected characteristics;
- review text, ratings or notes;
- model features or score values.

Missing data never raises a priority. It produces `INSUFFICIENT_DATA` in `not_actionable`, or a data-refresh
suggestion.

**Ranking (`rank_score` 0–100).** This is a deterministic *ordering* score, **not a probability or confidence**.
No confidence value is produced.
- base: LOW 20, MEDIUM 40, HIGH 60, CRITICAL 80;
- \+ urgency: min(10, days overdue ÷ 3);
- \+ freshness: 5 if the newest supporting record is ≤ 7 days old, 2 if ≤ 30 days;
- \+ 3 for individual follow-ups.

`priority_reasons` explains each part. Ties are broken by priority, rule, subject and evidence window. Employees
are never ranked by predicted probability.

**Deduplication and lifecycle.**
- The key is rule + `rules-v2` + subject + evidence window + an evidence fingerprint (a hash of the material
  evidence, excluding timestamps).
- Unchanged evidence creates nothing, and no notification. A dismissed record is never resurrected for the same
  evidence.
- If evidence changes while the earlier record is still AVAILABLE, a new record is created and the old one
  becomes **SUPERSEDED** (`superseded_by_id`).
- If an ACCEPTED record exists for the same rule, subject and window, nothing new is created
  (`being_addressed`).
- AVAILABLE rules-v2 records in the evaluated scope whose rule no longer fires become SUPERSEDED with no
  successor. This doesn't happen when that rule family's source query hit the limit in this run
  (`truncated_sources`).
- Transitions are defined once, in `recommendations.LIFECYCLE`:

| Action | From | To |
|--------|------|----|
| accept | AVAILABLE | ACCEPTED |
| dismiss | AVAILABLE, ACCEPTED | DISMISSED |
| complete | ACCEPTED | COMPLETED |
| supersede | AVAILABLE | SUPERSEDED (system only) |

- History is never deleted.

**APIs** (existing routes are reused):
- `evaluate` takes `strategy` and returns `ranked`, `superseded`, `being_addressed` and `truncated_sources`
  (additive fields).
- The list endpoint takes `rule_version` and `sort=rank` (rules-v1 records last).
- `summary` includes CRITICAL and SUPERSEDED.
- **New:** `GET /{id}/history`, built from the audit trail. Managers see actors outside their department as
  hidden. rules-v1 records have no per-record generation event; their `evaluated_at` is the generation time.
- **New:** `POST /{id}/link-task`, which links an ACCEPTED recommendation to an existing task you manage in the
  same department. Tasks are never created here.
- `create-intervention` maps a CRITICAL recommendation to a HIGH intervention, since interventions have no CRITICAL
  priority.

**Access.** Unchanged:
- ADMIN and HR: organisation-wide.
- MANAGER: their department only (individual and department records; never organisation-level ones).
- EMPLOYEE: 403.
- Nobody sees recommendations about themselves, and evaluation never produces them for the caller.
- Out of scope is 404; visible but not permitted is 403.

**Notifications.**
- A newly CRITICAL individual rules-v2 recommendation notifies the employee's department managers.
- Accepting a rules-v2 individual recommendation notifies them that it needs follow-up.
- The actor and the employee concerned are never notified.
- Repeated evaluations don't notify again.

**Audit.**
- `recommendation.generated` and `recommendation.superseded` (with reason `evidence_changed` or
  `evidence_no_longer_current`), per record.
- `recommendation.task_linked`.
- `recommendation.evaluated`, extended with v2 counts.
- No text, scores or features are recorded.

**Limitations.**
- The rules and thresholds are heuristics that haven't been validated against outcomes.
- Expiry only happens when an evaluation runs; there is no scheduler.
- The activity feed's `priority` filter does not accept CRITICAL (an existing contract). CRITICAL
  recommendations still appear in the feed.
- Downgrading migration `a0bd1c34f49c` refuses to run while any rules-v2 data exists.

### Workforce activity feed and operational signals

> **Read-only, assembled on demand from existing records.** Not a live stream, not a separate event store, and
> not a prediction. Reading never creates records, audit rows, notifications, alerts, recommendations or
> interventions. There is no websocket, scheduler or background worker, and **no database migration**.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/workforce-activity/feed` | Unified, paginated feed. Filters: `date_from`, `date_to` (organisation-local dates, inclusive; default last 7 days; at most `ACTIVITY_FEED_MAX_WINDOW_DAYS`), `department_id`, `category` (repeatable), `kind`, `priority`, `page`, `page_size` |
| `GET /api/workforce-activity/summary` | Counts by category, kind and action over **exactly** the same scope, window and filters as the feed |
| `GET /api/workforce-activity/departments/{id}` | Department activity counts for a window (default 30 days), plus that department's signals |
| `GET /api/workforce-activity/signals` | Documented aggregate and data-quality signals, evaluated now |
| `GET /api/workforce-activity/sources` | Catalogue of categories, sources, timestamp basis and exclusions |
| `GET /api/workforce-activity/updates` | Incremental updates after a signed cursor, oldest first (polling; see below) |
| `GET /api/workforce-activity/status` | Read-only monitoring status: snapshot freshness, latest recorded activity, active alerts by priority and SLA state, completeness |

**Sources (one source per action, so nothing is listed twice).**

| Category | Kind | Source | Actions | `source_event_at` |
|----------|------|--------|---------|-------------------|
| `attendance` | Observed | `attendance_records` | `checked_in` | check-in time |
| `leave` | Observed | `leave_requests` | `submitted`, `approved`, `rejected`, `cancelled` | — |
| `employee_record` | Observed | `employees` | `created` | date of joining |
| `employee_status` | Observed | `audit_logs` (`employee.status_changed`) | `status_changed` | — |
| `risk_event` | Risk | `risk_events` | the event type | — |
| `risk_alert` | Risk | `risk_alerts` | `created`, `acknowledged`, `resolved`/`dismissed` | — |
| `risk_escalation` | Risk | `risk_alert_escalations` | `approaching`, `overdue` | — |
| `intervention` | Observed | `intervention_events` | the workflow event type | — |
| `recommendation` | Observed | `recommendations` | `generated`, then the review outcome | — |

- **Excluded:** payroll (sensitive amounts; managers have no payroll access) and audit rows that duplicate a
  domain record (for example `leave.approved`).
- **Identity and deduplication:** `event_id = "<source>:<record id>:<action>"`, unique by construction and
  de-duplicated defensively.
- **Timestamps:** `recorded_at` is when the platform stored the record (UTC). `source_event_at` appears only where
  an independent real-world time exists; status changes are dated by recording time, which may lag reality.
- **Ordering:** `recorded_at` descending, then `event_id` descending (a stable, lexical tie-breaker), so pages
  never overlap or skip for unchanged data.
- **Bounds:** each source is one query within the window, capped at `ACTIVITY_FEED_MAX_ITEMS`. If a cap is hit,
  `truncated=true` and `truncated_sources` name the sources; narrow the window or filters. The query count is
  fixed regardless of data volume or number of departments (no N+1).

**Privacy.** Items carry ids (record, employee, department), category, action, a fixed description and, for
risk/intervention/recommendation items, a priority. **Never** names, employee codes, emails, risk scores, model
features, leave reasons, review comments, intervention notes or descriptions, alert notes, recommendation text,
or payroll values. Status-change descriptions show only the from/to status, not the reason.

**Department activity.** Current employees; attendance record counts by status (zero records is reported as
`unavailable`, not zero); leave submitted/approved/rejected/cancelled and approval rate = approved / (approved +
rejected) among requests reviewed in the window; recorded deactivations/reactivations; risk alerts created;
intervention events; snapshot recency; inactive employees without a recorded departure.

**Signals.** Each has `kind` (`AGGREGATE_SIGNAL` or `DATA_QUALITY_SIGNAL`), `status` (`ACTIVE`, `CLEAR` or
`INSUFFICIENT_DATA`), an explanation, evidence (aggregate values only) and its source.

| Signal | Scope | Kind | Active when |
|--------|-------|------|-------------|
| `SNAPSHOT_STALE` | Organisation | Data quality | No snapshot, or the latest is older than `WORKFORCE_SNAPSHOT_MAX_AGE_DAYS` |
| `UNKNOWN_DEPARTURE_DATES` | Organisation | Data quality | Inactive employees have no recorded departure |
| `ATTENDANCE_DATA_GAP` | Department | Data quality | Current employees but no attendance records in `ACTIVITY_ATTENDANCE_GAP_DAYS` days |
| `HEADCOUNT_DECLINE` | Department | Aggregate | Three consecutive declines across four consecutive observed month-end snapshots (needs ≥ 4) |
| `ALERTS_AWAITING_REVIEW` | Department | Aggregate | Active risk alerts first detected more than `ACTIVITY_ALERT_REVIEW_DAYS` days ago |

Signals describe recorded data and its quality. **They are not resignation predictions, there is no
employee-level instability score, and no individual is classified from isolated events.** Signals never create
alerts, recommendations or interventions.

**Access.**
- ADMIN and HR: organisation-wide, or one department via `department_id`.
- MANAGER: pinned to their own department; another department returns 404. Organisation-level signals and
  recommendations are not shown.
- EMPLOYEE: no access (403).
- Nobody sees risk, escalation, intervention or recommendation items about themselves.

**Limitations.** Counts reflect what has been recorded, not everything that happened. Attendance absence
records depend on how attendance is captured. Very busy windows may be truncated. Item descriptions are fixed
labels in English.

#### Incremental updates (polling)

> **This is polling, not push.** There is no WebSocket, server-sent events or background worker. Items appear
> once they are stored in the database (and at least `ACTIVITY_UPDATES_SETTLE_SECONDS` old). Nothing is
> real-time telemetry.

**Typical client flow.**
1. Call `GET /feed` (newest first). The response now also carries `updates_cursor` (an additive field;
   existing fields are unchanged).
2. Every 15–30 seconds (`recommended_poll_seconds`, default 20), call
   `GET /updates?cursor=<cursor>` with the **same** `department_id`, `category`, `kind` and `priority` filters.
3. Append `items` (oldest first), de-duplicating by `event_id`. Store `next_cursor`.
4. If `has_more` is true, call again immediately with `next_cursor`; otherwise wait for the next interval.
5. Errors:
   - `410`: the cursor expired. Reload `/feed`.
   - `422`: the cursor is invalid or doesn't match the request. Reload `/feed`.
   - `5xx` or a network error: back off exponentially (for example 30 s, 60 s, 120 s, capped at 5 minutes, with
     jitter), and reset after a success.
   - Pause polling while the tab is hidden.

**Response.** `server_time`, `items` (same item shape as `/feed`), `count`, `page_size` (requested `limit`
capped by `ACTIVITY_UPDATES_MAX_PAGE_SIZE`, 100), `has_more`, `next_cursor`, `settled_until`,
`recommended_poll_seconds`, `scope`. Without a cursor, updates start at `date_from` (or
`ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS` ago). `date_from`/`date_to` are validated exactly as for `/feed`.

**Ordering and cursor contract.**
- Position key: `(recorded_at, source_rank, record id, action_rank)`, ascending.
- Source ranks follow the catalogue order (attendance 0 … recommendation 8). Action ranks order the actions
  of one record (for example leave submitted < reviewed < cancelled). The ranks are fixed.
- Ties at the same `recorded_at` are therefore broken numerically and deterministically across sources, so
  adjacent pages never overlap or skip when data is unchanged.
- `/feed` keeps its existing order (`recorded_at` desc, `event_id` desc) for compatibility.
- Each source/action is one SQL query with a keyset condition and a limit of `page_size + 1`, merged in memory.
  This is a fixed number of bounded queries per poll, independent of history size; there are no full scans of
  the feed.
  - If rows fetched for one source are dropped after fetching (for example an incomplete status audit), the
    page stops at that source's last fetched row (the *frontier*), so nothing behind it is skipped.
- Cursors are opaque and **HMAC-signed** with a key derived from `JWT_SECRET_KEY`.
  - Each cursor is bound to the issuing user and to a fingerprint of the department scope and filters.
  - Edited, forged, malformed or oversized (> 512 characters) cursors, or ones used with different
    filters, scope or user, get **422**. Cursors older than `ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS` (72) get
    **410**.
  - Rotating `JWT_SECRET_KEY` invalidates all cursors.
  - A cursor never widens access: scope is always re-derived from the caller's role and department.
- `next_cursor` advances to `settled_until` when nothing more is pending, so an idle client's cursor never
  expires while it keeps polling.

**Completeness limitations (when a client cannot assume it has every event).**
- *Late commits:* a record whose `recorded_at` is earlier than an already-issued cursor (a transaction that
  committed more than `ACTIVITY_UPDATES_SETTLE_SECONDS` after its timestamp, or a backdated row) is not
  returned by `/updates`. `/feed` still lists it, so reload `/feed` periodically (for example every 15 minutes).
- *Mutable sources:* a recommendation's review timestamp is overwritten when it moves from accepted to
  completed. The new action appears as a new item, and the earlier one disappears from `/feed`.
- *Deletion and retention:* removed source records vanish without a removal event. There is no retention
  policy yet.
- *Feed caps:* `/feed` and `/summary` read at most `ACTIVITY_FEED_MAX_ITEMS` rows per source/action
  (`truncated`). `/updates` is not capped; it pages through everything.
- *Recorded time only:* `recorded_at` is when the platform stored a record, not when it happened in the real
  world (`source_event_at` is given only where a separate event time exists).

#### Monitoring status

`GET /api/workforce-activity/status` (optional `department_id`) is read-only; it calls no model and writes nothing.

| Field | Meaning |
|-------|---------|
| `server_time` | When the response was produced (UTC) |
| `snapshot` | Latest workforce snapshot (containing the department, for a department scope): `available`, `stale` (older than `WORKFORCE_SNAPSHOT_MAX_AGE_DAYS`) or `insufficient_data` (none) |
| `activity` | Newest `recorded_at` across all sources in scope within `ACTIVITY_FEED_MAX_WINDOW_DAYS`: `available` or `insufficient_data` |
| `risk_alerts` | Active alerts in scope by priority and SLA state (`ON_TRACK`, `APPROACHING`, `OVERDUE`, `ACKNOWLEDGED`), using the review-deadline rules. Counts only: no scores, features or employees |
| `completeness` | Whether any source in the default 7-day feed window has reached the per-source cap (`feed_truncation_possible`, `categories_at_or_over_cap`), plus known limitations |
| `polling` | Recommended interval, maximum page size, settle delay and cursor maximum age |

**Access.**
- ADMIN and HR: organisation-wide, or one department.
- MANAGER: their own department only; any other department returns 404.
- EMPLOYEE: 403.
- Alerts about the caller are excluded from the counts, consistent with the feed.

#### Feed query indexes

Migration `818f279b6dde` (index-only, revises `0bc0f8d45e33`; **not applied to any real database**) adds the
indexes the feed, updates, status, summary and department queries actually use. Each feed source query filters a
range or keyset on one timestamp, orders by `(timestamp, id)` and has a `LIMIT`. InnoDB secondary indexes include
the primary key, so a single-column timestamp index serves that order.

| Index | Query pattern served |
|-------|----------------------|
| `ix_attendance_records_created_at` | attendance check-ins; department attendance counts; attendance-gap signal |
| `ix_leave_requests_created_at` / `_reviewed_at` / `_cancelled_at` | leave submitted / reviewed / cancelled items; department leave counts |
| `ix_employees_created_at` | employee-record items |
| `ix_audit_logs_action_created_at` (`action`, `created_at`) | status-change items (equality on `action`, then time range and order); recorded-departure analytics |
| `ix_risk_events_detected_at` | risk-event items (the existing index leads with `employee_id`, which these queries don't filter on) |
| `ix_risk_alerts_created_at` / `_acknowledged_at` / `_resolved_at` | alert created / acknowledged / closed items |
| `ix_intervention_events_created_at` | intervention items |
| `ix_recommendations_evaluated_at` / `_reviewed_at` | recommendation generated / reviewed items |

**Not added:**
- `risk_alert_escalations.created_at` is already indexed.
- Joins use primary keys or the existing unique `(intervention_id, version)`.
- Department filters use the existing `ix_employees_department_id`.

**Two query changes made with the indexes:**
- The status-change join now casts the **audit** side (`employees.id = CAST(audit_logs.entity_id AS SIGNED)`)
  instead of the employee id, so the `employees` primary key is usable. The column types are unchanged.
- Keyset conditions now start with `timestamp >= t`, giving a single index range.

**Validation (SQLite only).**
- A migration test upgrades a scratch in-memory SQLite database, then checks that only these 13 indexes appear,
  that no table, column or unique constraint changes, that every name is at most 64 characters, and that
  downgrade restores the previous shape exactly.
- `EXPLAIN QUERY PLAN` on SQLite for all 14 source queries (window and keyset forms, organisation and
  department scope) went from 26 full-table `SCAN`s to 0. Temporary sorts fell from 54 to 26; the remaining
  sorts are department-scoped plans where SQLite, without statistics, prefers the department index.

**Expected MySQL benefit, not measured here.** No MySQL server was used, so the MySQL gain is an assumption:
these queries should become index range scans with no filesort for organisation-wide reads. Department-scoped
reads may still choose `ix_employees_department_id`, depending on data distribution. Before relying on it, run
`EXPLAIN` on a staging copy. Each index adds a little write cost to inserts and updates of these columns.

### Reports and exports

Downloadable reports as **CSV** (UTF-8 with BOM), **Excel** (`.xlsx`) or **PDF** (A4 landscape), built with the
Python standard library (no new dependency).

| Endpoint | Content | Roles | Filters |
|----------|---------|-------|---------|
| `GET /api/reports/catalog` | Reports you may run, with formats, filters and limits | everyone (EMPLOYEE gets an empty list) | — |
| `GET /api/reports/employees` | Code, name, department, designation, status, joining date (no email or phone) | ADMIN, HR, MANAGER | `department_id`, `status` |
| `GET /api/reports/attendance` | Attendance rows with check-in/out and minutes (no notes); same rows and order as `GET /api/attendance` | ADMIN, HR, MANAGER | `date_from`, `date_to` (default last 30 days), `department_id`, `status` |
| `GET /api/reports/leave` | Requests overlapping the range, with status and timestamps (no reasons or comments); same rows as `GET /api/leave/requests` | ADMIN, HR, MANAGER | `date_from`, `date_to`, `department_id`, `status` |
| `GET /api/reports/payroll` | Gross, deductions and net pay per employee for one period; same rows as the period records API | ADMIN, HR | `period_id` (required), `department_id`, `status` |
| `GET /api/reports/attrition` | Per department: latest stored risk-estimate category counts and alert status counts | ADMIN, HR, MANAGER | `department_id` |
| `GET /api/reports/workforce` | Monthly snapshot trend; same rows as `GET /api/workforce-analytics/trends` | ADMIN, HR, MANAGER | `date_from`, `date_to` (default 12 months, at most 60) |
| `GET /api/reports/activity` | Activity-feed items; same items as `GET /api/workforce-activity/feed` | ADMIN, HR, MANAGER | `date_from`, `date_to` (≤ 31 days), `department_id`, `category`, `kind` |

Every report takes `format=csv|xlsx|pdf` (default csv).

**Scope and privacy.**
- Each report uses the permission of its source module.
- MANAGER is pinned to their own department; another department returns 404.
- EMPLOYEE has no report access; their own data stays in the self-service endpoints.
- Columns are an explicit allow-list. Reports never include passwords, tokens, emails, phone numbers, notes,
  leave reasons, review comments, intervention text, recommendation text, model features or individual risk
  scores.
- The attrition report is aggregate only. It uses the same calculation as the analytics AI risk indicators and
  excludes the caller's own record.
- Salary values appear only in the payroll report (ADMIN/HR).

**Spreadsheet safety.**
- Text starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading apostrophe in CSV and XLSX.
- XLSX cells are typed (numbers, dates and booleans as values), and formulas are never written.
- Only the listed columns are written: there are no hidden sheets or columns.
- Times are shown in `ORG_TIMEZONE`.

**Limits.**
- More than `REPORT_MAX_ROWS` (5000) rows, or `REPORT_PDF_MAX_ROWS` (1000) for PDF, returns **422**; the report
  is not truncated.
- Attendance and leave ranges are at most `REPORT_MAX_RANGE_DAYS` (366) days.
- Unknown parameters, bad formats, statuses or dates also return 422.

**Responses.**
- Headers: `Content-Disposition: attachment` with a sanitised filename (for example
  `attendance_department-3_2026-09-27_2026-10-26_20261026-1430.csv`), `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff` and `X-Report-Rows`.
- PDFs repeat the header row on every page, number the pages, and shorten long cells with "...". Characters
  outside Windows-1252 print as "?"; use CSV or XLSX for full Unicode.

**Audit.**
- Only payroll exports are audited (`payroll.report_exported`: period, format, row count, department, status).
  This follows the audited dataset-download convention, and the row never contains amounts.
- If that audit row can't be written, the export fails and nothing is downloaded.
- Other reports are read-only and write nothing.

**Limitations.**
- Reports are generated synchronously, within the row limits; there are no background jobs or scheduled
  reports.
- The attrition report runs a few queries per department.
- Employee-level attrition reports are deliberately not offered.

### Audit logs and compliance

**What is audited.** Every state-changing administrative workflow writes one audit row in the same transaction as
the change: if the row can't be written, the change is rolled back. `app/services/audit_catalog.py` lists every
action (a test fails if code writes an action that isn't listed, or the catalogue lists one nothing writes).

| Category | Actions |
|----------|---------|
| `auth` (new) | `auth.registered`, `auth.login_succeeded`, `auth.login_failed` (reason code `unknown_account` / `invalid_password` / `inactive_account`; never the submitted email, password or token) |
| `employee`, `department`, `shift` | created, updated, status changed |
| `attendance` | `attendance.corrected` |
| `leave` | requested, approved, rejected, cancelled; leave type and policy created/updated |
| `payroll` | components, profiles (field names only), periods created/calculated, records approved/paid, `payroll.report_exported` |
| `dataset`, `ai` | upload, new version, validated/rejected, archived, downloaded; prediction, batch, unavailable, training, training rejected, activation |
| `risk` | monitoring run, alert created/escalated/acknowledged/resolved/dismissed/assigned, review-deadline evaluation and escalation |
| `intervention`, `recommendation` | full lifecycle (note text never recorded); recommendation evaluation, decisions, intervention links |
| `task` (new) | created, updated (field names only), assigned, started, blocked (reason code only), unblocked, completed, cancelled, reopened |
| `performance` (new) | cycle created/updated/opened/reviews started/completed/cancelled; goal created/updated; review created, updated, reassigned, self-assessment submitted, manager review submitted, finalized, reopened, cancelled (field names and statuses only; no text and no rating values) |
| `workforce`, `audit` (new) | snapshot captured; `audit.report_exported` |

**Deliberately not audited:**
- Reads: lists, feed polls, status checks, notifications, and non-payroll report downloads.
- Self-service check-in/out.
- Logout: tokens are stateless, so there's nothing to change.

There are no user-role, password-reset or configuration endpoints, so no access-control changes exist to audit.

**Failed sign-ins.**
- The audit row is committed on its own, and the request still fails.
- A successful sign-in issues no token if its audit row can't be written.
- There is no rate limiting yet, so repeated failures can grow the table (see *Retention*).

**Metadata policy** (`app/services/audit.py`).
- **Rejected:** credential-like keys (`password`, `secret`, `token`, `authorization`, `api_key`, `hash`, at any
  depth). The model raises, and the transaction fails.
- **Redacted at write time,** at any depth, case-insensitive. The key stays, so an investigator can see the field
  changed, but its value becomes `[redacted]`. This covers:
  - risk scores and model features;
  - salary amounts;
  - bank details;
  - phone, email and full name;
  - free-text notes, comments, explanations and resolution, dismissal or completion text.
  Organisational `description` fields (departments, leave types) are kept.
- **Documented exception:** the free-text `reason` of `attendance.corrected` and `employee.status_changed` is the
  stored justification for those changes. It stays in the database but is **withheld** from the query API and
  exports. Reading it requires direct database access by an authorised operator.
- **Read-time redaction:** the same redaction runs again on read, so rows written before this policy are also
  sanitised.

**Query API.**
- `GET /api/audit-logs`: filters `date_from`, `date_to` (organisation-local dates; default the last 30 days, at
  most `AUDIT_QUERY_MAX_RANGE_DAYS`, 366), `department_id`, `actor_user_id`, `action`, `category`, `entity_type`,
  `entity_id`, `sort=newest|oldest`, `page`, `page_size` (≤ 100).
  - Actions, categories and entity types are validated against the catalogue. Unknown values, bad dates or
    unknown parameters return 422.
  - Ordering is `created_at`, then `id` as the tie-breaker.
  - Each item has: `id`, `created_at`, `actor_user_id`, `actor_hidden`, `action`, `category`, `description`,
    `entity_type`, `entity_id` and sanitised `details`.
- `GET /api/audit-logs/catalog`: the static list of actions, categories and entity types, marking which ones a
  manager can see.
- **ADMIN and HR:** everything, or the records associated with one department.
- **MANAGER:** only records whose entity is in their own department, resolved in SQL, for employee, department,
  attendance, leave request, risk alert, intervention and recommendation records.
  - All other types are **excluded rather than guessed**: auth, payroll, datasets, models, monitoring runs,
    catalogues, shifts and exports.
  - Risk, intervention and recommendation records about the manager are excluded.
  - Actors who are neither the manager nor linked to an employee in the department appear as
    `actor_user_id: null, actor_hidden: true`.
  - Another department returns 404.
- **EMPLOYEE:** 403.
- No endpoint edits or deletes audit rows.

**Export.**
- `GET /api/audit-logs/export?format=csv|xlsx|pdf` (ADMIN and HR only), with the same filters and the same
  sanitised rows as the query API.
- Limits are `REPORT_MAX_ROWS` (5000), or `REPORT_PDF_MAX_ROWS` (1000) for PDF. More returns 422.
- It uses the report writers: formula-injection protection, typed Excel cells, a sanitised filename,
  `no-store` and `nosniff`.
- Each export writes exactly **one** `audit.report_exported` row, containing the format, row count, dates and
  filters. It never contains exported values.
  - That row is written after the rows are read, so an export never includes itself.
  - If the row can't be written, the export fails and no file is returned.

**Retention and tamper-resistance (gaps).**
- There is **no retention policy and no automatic deletion**.
- **Recommendation for approval:** a configurable retention period (for example 7 years for payroll and HR
  actions, a shorter period for failed sign-ins), enforced by a reviewed, audited, manually run archive job.
- The rows are append-only through the application, but **not tamper-evident**: a privileged database operator can
  still change or delete them. Hash-chaining or write-once external storage would be needed for that.
- No migration was needed: the existing indexes cover these queries (`created_at`, `action + created_at`,
  `entity_type + entity_id`, `actor_user_id`).

### Workforce tasks

Operational HR follow-up tasks assigned to employees, with versioned history. A task is a workflow record: completing
one linked to an intervention or recommendation **does not change that record and is not evidence that it
succeeded**.

| Endpoint | Purpose | Who |
|----------|---------|-----|
| `POST /api/tasks` | Create: `title`, `description`, `task_type`, `priority` (LOW/MEDIUM/HIGH/CRITICAL), `due_date`, `assignee_employee_id`, `department_id`, optional `intervention_id` **or** `recommendation_id`, `idempotency_key` | ADMIN, HR, MANAGER |
| `GET /api/tasks` | List visible tasks. Filters: `status`, `priority`, `task_type`, `assignee_employee_id`, `created_by_user_id`, `department_id`, `intervention_id`, `recommendation_id`, `assigned_to_me`, `overdue`, `due_from`, `due_to`. `sort=updated` (default), `created`, `due` (no due date last) or `priority`, with id as the tie-breaker. Pages of at most 100 | everyone (scoped) |
| `GET /api/tasks/summary` | Counts by status, active by priority and type, overdue, due within 7 days, assigned to me, unassigned (same scope as the list) | everyone (scoped) |
| `GET /api/tasks/{id}` | Task plus its latest 20 history events | anyone who can see it |
| `PATCH /api/tasks/{id}` | Edit `title`, `description`, `task_type`, `priority`, `due_date` while OPEN, IN_PROGRESS or BLOCKED; optional `expected_version` | task managers |
| `POST /api/tasks/{id}/assign` | Assign or reassign: `assignee_employee_id`, optional `expected_version` | task managers |
| `POST /api/tasks/{id}/transition` | `action` = start, block, unblock, complete, cancel or reopen (below) | see table |
| `GET /api/tasks/{id}/history` | Paginated history, oldest first | anyone who can see it |

**Who can see and manage tasks.**
- **Task managers:** ADMIN and HR for every task; MANAGER for tasks of their own department.
- **Visibility:**
  - task managers see the tasks they manage;
  - every user also sees tasks assigned to them, and can only read those;
  - EMPLOYEE sees only their assigned tasks.
- **Tasks about the viewer:** a task linked to an intervention or recommendation about the viewer is hidden from
  them, like the record itself.
- **Not found vs forbidden:** tasks, employees, departments and source records out of your scope return **404**.
  Visible tasks you may not change return **403**.
- **Creating:** EMPLOYEE can't create tasks or (re)assign them, including to themselves.
- **Department:** MANAGER tasks are pinned to the manager's own department. Another department returns 404, and a
  manager without a department gets 403.
- **Department default,** in order: the linked record's department, the department given, the assignee's
  department, or none (an organisation-level task, ADMIN/HR only).
- **Cross-department assignment** (assignee outside the task's department) is ADMIN/HR only.

**Assignee eligibility.** The employee must:
- be in the actor's scope;
- not be INACTIVE;
- have an active login (so they can see and act on the task);
- for a linked task, be able to see the linked intervention or recommendation themselves (so an EMPLOYEE can't
  be given a task about someone else's intervention).

**Transitions.** Defined once, in `app/services/tasks.py`. Clients name an action and never submit a status.

| Action | From | To | Who | Body |
|--------|------|----|-----|------|
| `start` | OPEN | IN_PROGRESS | manager or assignee | — |
| `block` | OPEN, IN_PROGRESS | BLOCKED | manager or assignee | `block_reason` (WAITING_ON_EMPLOYEE, WAITING_ON_APPROVAL, WAITING_ON_INFORMATION, EXTERNAL_DEPENDENCY, OTHER), optional `block_details` (5–500) |
| `unblock` | BLOCKED | IN_PROGRESS | manager or assignee | — |
| `complete` | OPEN, IN_PROGRESS | COMPLETED | manager or assignee | optional `completion_summary` (≤ 2000) |
| `cancel` | OPEN, IN_PROGRESS, BLOCKED | CANCELLED | managers only | `cancellation_reason` (5–500) |
| `reopen` | COMPLETED, CANCELLED | OPEN | managers only | — |

- An action whose target is the current status is a **no-op**: 200, with no event, audit row or notification.
- Other disallowed transitions return 409.
- Fields that don't belong to the action return 422.
- Completion records `completed_at`.
- Reopening clears the completion or cancellation fields. The earlier events stay in history.

**Integrity.**
- Every change is a conditional update on the task's `version`, plus a `task_events` row with the new version
  (unique per task). Concurrent writers get **409** rather than overwriting each other.
- Each request commits once, together with its audit row and notifications. A failure, including an audit
  failure, rolls everything back.
- Creating with an `idempotency_key` returns the original task on retry, with no duplicate event or notification.
- Due dates are organisation-local dates, from today to 366 days ahead. A task is overdue when it is active and
  its due date is before today in `ORG_TIMEZONE`.

**Notifications** (in-app, same transaction; never the actor, one per recipient):
- assignment and reassignment go to the new assignee;
- a due-date change goes to the assignee;
- blocked goes to the creator, or the department's managers if the creator account no longer exists;
- completed goes to the creator;
- cancelled and reopened go to the assignee.

Messages contain only the task number and priority or status, never the title or any text.

**Audit.** The `task.*` actions are listed in the audit catalogue. Audit rows and history `details` hold ids, field
names, statuses and the block reason code. They never hold titles, descriptions, summaries, block details or
cancellation reasons. Managers see task audit records for tasks of their own department (`/api/audit-logs`).

**Limitations.**
- No comments or attachments.
- No unassign endpoint.
- No recurring tasks, reminders or scheduled due-date alerts (no background jobs).
- No email.
- Changing a task's department after creation isn't supported.
- The creator is recorded as a user, because ADMIN/HR accounts need not have an employee record.

### Performance management

Review cycles, employee goals and performance reviews, each with versioned history in `performance_events`.

**Rating scale** (`overall_rating`):

| Rating | Meaning |
|--------|---------|
| 1 | Needs significant improvement |
| 2 | Partially meets expectations |
| 3 | Meets expectations |
| 4 | Exceeds expectations |
| 5 | Consistently exceeds expectations |

**Review cycles** (ADMIN/HR administer them):
- **Dates:** `period_start ≤ period_end` (at most two years), and
  `period_start ≤ self_assessment_deadline ≤ manager_review_deadline ≤ period_end + 365 days`. All are
  organisation-local dates, checked in the API and by database constraints.
- **Transitions** (`POST /cycles/{id}/transition`):

| Action | From | To |
|--------|------|----|
| `open` | DRAFT | OPEN |
| `start_reviews` | OPEN | IN_PROGRESS |
| `complete` | IN_PROGRESS | COMPLETED (409 while any review isn't finalised or cancelled) |
| `cancel` | DRAFT, OPEN or IN_PROGRESS | CANCELLED (`cancellation_reason` required) |

  Repeating an action is a no-op.
- **Editing** (`PATCH`): every field while DRAFT; once open, only the description and deadlines. COMPLETED and
  CANCELLED cycles are read-only, and so are their reviews.
- **Visibility:** MANAGER sees every non-draft cycle. EMPLOYEE sees non-draft cycles in which they have a review
  or a goal.

**Goals** (`/goals`):
- **Owner** (any role, for their own goals): create, then edit the content, dates, progress and status of active
  goals (not CANCELLED).
- **Employee's manager** (department MANAGER, or ADMIN/HR, never for their own goal) can additionally:
  - set `manager_feedback`;
  - cancel a goal;
  - reopen a completed or cancelled goal;
  - reassign it to another employee they manage.
- **Rules:**
  - `progress_percent` is 0–100;
  - `COMPLETED` requires 100% progress and records `completed_at`;
  - `start_date ≤ target_date`;
  - goals can't be set for inactive employees, or added to completed or cancelled cycles.
- **History:** each change is one history event: GOAL_PROGRESS_CHANGED, GOAL_STATUS_CHANGED, GOAL_REASSIGNED or
  GOAL_UPDATED. Its details carry field names, the status from/to and the progress from/to.
- **Not automatic:** goals are never completed automatically, for example by task completion.

**Reviews** (`/reviews`; one per employee per cycle, enforced by a unique constraint):

| Step | Who | From → to | Notes |
|------|-----|-----------|-------|
| `POST /reviews` | ADMIN/HR or the employee's MANAGER (never for themselves) | → ASSIGNED | Cycle OPEN or IN_PROGRESS. The reviewer must be an active ADMIN/HR user or the MANAGER of the employee's department, and never the employee |
| `PATCH` `self_assessment` | the reviewed employee | while ASSIGNED | Until the self-assessment deadline |
| `submit-self-assessment` | the reviewed employee | ASSIGNED → SELF_SUBMITTED | Until the self-assessment deadline |
| `PATCH` evaluation (`manager_summary`, `strengths`, `improvement_areas`, `development_goals`, `training_recommendations` as lists of ≤ 10 items, `manager_private_notes`, `overall_rating`) | the assigned reviewer only | while ASSIGNED or SELF_SUBMITTED | Until the manager deadline; rating changes are recorded in history |
| `PATCH` `reviewer_user_id` | ADMIN/HR or the employee's manager | ASSIGNED or SELF_SUBMITTED | In a request of its own |
| `submit-manager-review` | the reviewer | → MANAGER_SUBMITTED | Needs a rating and a summary. From ASSIGNED only once the self-assessment deadline has passed |
| `finalize` | ADMIN/HR (not their own review) | MANAGER_SUBMITTED → FINALIZED | Read-only afterwards |
| `reopen` | ADMIN/HR (not their own review) | FINALIZED → SELF_SUBMITTED or ASSIGNED | Correction workflow: `reason` (10–500) is stored on the review and audited. Only while the cycle is active |
| `cancel` | ADMIN/HR (not their own review) | open statuses → CANCELLED | `reason` required. A finalised review must be reopened first |

**Confidentiality.**
- **Reviewed employee:**
  - always sees their own self-assessment;
  - sees the manager's evaluation and rating only once the review is FINALIZED;
  - never sees `manager_private_notes`, cancellation reasons or reopen reasons;
  - in history, never sees rating values.
- **Reviewers and other readers:** see the self-assessment only after it is submitted.
  - Private notes are visible to the reviewer and ADMIN/HR only.
  - The employee's department manager sees the evaluation, but not the private notes, unless they are the reviewer.
- **Visibility:**
  - EMPLOYEE: their own goals and reviews.
  - MANAGER: goals and reviews of their department, plus reviews they are assigned to.
  - ADMIN/HR: everything.
  - Anyone always sees their own review in the employee view.
- **Status codes:** out of scope is 404; visible but not permitted is 403; wrong state or deadline is 409; a stale
  `expected_version` is 409.
- **Concurrency:** every change is a conditional update on `version` plus one history event (unique per record
  and version).
- **Transactions:** each request commits once, together with its audit row and notifications. An audit failure
  rolls everything back.

**Notifications** (in-app, never to the actor, ids and statuses only; no text and no ratings):
- review opened (to the employee) and review assigned (to the reviewer);
- self-assessment submitted (to the reviewer);
- manager review submitted (to the review's creator);
- review finalised (to the employee and reviewer);
- review reopened or cancelled (to the reviewer);
- goal set or reassigned (to the employee);
- goal changed by a manager (to the employee);
- goal marked AT_RISK or COMPLETED by its owner (to the department's managers).

**Summary** (`GET /api/performance/summary?cycle_id=`): cycles, reviews and goals by status, the distribution of
finalised ratings (not shown to EMPLOYEE), average progress of active goals, goals at risk or overdue, and reviews
awaiting you. All counts use the same scope as the lists.

**Limitations and deferred work.**
- No reminders or deadline notifications (no scheduler).
- No calibration or 360° feedback.
- No goal weighting and no task links.
- Reopening is only possible while the cycle is OPEN or IN_PROGRESS.
- Review text is free text, protected by access rules and never copied into audit rows or notifications.

### Search, filtering and sorting

**Shared conventions** (`app/services/search.py`):
- **Keywords (`q`):** trimmed, 1–100 characters (2–100 for `/api/employees/search` and `/api/search`).
  Case-insensitive substring, with `%`, `_` and `!` matched literally (LIKE with a `!` escape that behaves the
  same on MySQL and SQLite). Always a bound parameter.
- **Exact filters:** ids, statuses, priorities and categories are validated enums or positive integers.
- **Date filters:** inclusive organisation-local dates. A reversed range returns 422.
- **Sorting:** only allow-listed fields. A client string is never turned into a column, and an unsupported sort
  returns 422. Every ordering ends with the record id, so equal timestamps paginate deterministically.
- **Pagination:** `page` ≥ 1 and `page_size` 1–100.
- **Unknown parameters** return 422, as before.

**Filters added in this step** (everything else already existed and is unchanged):

| Endpoint | New filters |
|----------|-------------|
| `GET /api/departments` | `q` (department name) |
| `GET /api/datasets` | `q` (dataset name) |
| `GET /api/payroll/periods/{id}/records` | `employee_id` (ADMIN/HR only, as before) |
| `GET /api/risk-monitoring/alerts` | `detected_from`, `detected_to` (first detection, organisation-local dates) |
| `GET /api/interventions` | `q` (title), `assigned_to_user_id` |
| `GET /api/tasks` | `q` (title) |
| `GET /api/performance/goals` | `q` (goal title) |
| `GET /api/workforce-activity/feed`, `/summary`, `/updates` | `priority` now accepts `CRITICAL` |

**Filters that already existed, by module** (see each module's section):
- **employees:** `q`, department, status, designation, joining-date range, `user_active`; sort by name, code,
  joining date or creation.
- **attendance:** date range, employee, department, shift, status.
- **leave review:** status, employee, department, leave type, date window.
- **risk alerts:** status, priority, trigger, department, employee, `assigned_to_me`.
- **interventions and tasks:** status, priority, category or type, department, assignee, due range, overdue.
  Tasks also sort by updated, created, due or priority.
- **performance:** cycles by status and period; reviews by cycle, employee, reviewer, department, status.
- **recommendations:** `rule_version`, category, priority, status, scope, rule, `sort=rank`.
- **activity feed:** window, department, category, kind, priority.
- **audit logs:** actor, action, category, entity type and id, date window.

**Keyword fields are deliberately limited** to names, codes and titles. Descriptions, notes, feedback, review
content and reasons are never searched, so a search can't be used to probe confidential text.

**CRITICAL priority in the activity feed.**
- `CRITICAL` is a real value: rules-v2 recommendations use it.
- The feed's `priority` filter now accepts LOW, MEDIUM, HIGH and CRITICAL. Values are case-sensitive, and
  anything else is still 422.
- Only the sources that carry a priority can match: risk alerts, escalations and interventions (LOW–HIGH), and
  recommendations (LOW–CRITICAL). Attendance, leave, employee records, status changes and risk events have no
  priority and are excluded whenever a priority filter is given; no priority is invented for them.
- The earlier test that rejected `CRITICAL` now uses `URGENT` as its invalid example.

**Global search: `GET /api/search?q=...&types=...&limit_per_type=5`** (authentication required).
- **Parameters:**
  - `q`: 2–100 characters;
  - `types`: any of employee, department, task, intervention, recommendation, goal, review_cycle,
    performance_review (default: all you may search);
  - `limit_per_type`: 1–20.
- **Access:** each type is one bounded query through its module's own visibility rules.

| Type | Who | Visible records | Matched fields |
|------|-----|-----------------|----------------|
| employee | everyone | ADMIN/HR all; MANAGER their department; EMPLOYEE themselves | name, code (not email or phone) |
| department | everyone | all | name |
| task | everyone | ADMIN/HR all; MANAGER their department and tasks assigned to them; EMPLOYEE tasks assigned to them | title |
| intervention | ADMIN, HR, MANAGER | their scope, never about themselves | title |
| recommendation | ADMIN, HR, MANAGER | their scope, never about themselves | title |
| goal | everyone | as `GET /api/performance/goals` | title |
| review_cycle | everyone | as `GET /api/performance/cycles` | name |
| performance_review | everyone | as `GET /api/performance/reviews` | cycle name, employee name or code |

- **Unavailable types:** types your role can't search are listed in `unavailable_types` and never queried.
  Employees never receive recommendations or interventions.
- **Result fields:** `type`, `id`, `label`, a few permitted `summary` fields (statuses, priorities, dates,
  ids), `timestamp`, and `resource`, the detail endpoint, which applies its own checks.
- **Never returned:** descriptions, notes, review content or ratings, salary values, risk scores, model
  features, contact details or audit metadata.
- **Not searched:** payroll, audit logs, risk alerts (finding an alert by an employee's name would reveal
  individual risk data), attendance, leave and datasets. Use their module endpoints.
- **Ordering** is deterministic: record type, then match quality (exact label, prefix, substring), then the label
  case-insensitively, then id. Each record appears once. `truncated_types` lists types with more than
  `limit_per_type` matches.

**Indexes and performance.**
- No migration was added.
- Exact filters and date ranges use existing indexes: status, priority, department, assignee, due dates,
  `first_detected_at`/timestamps, and the activity-feed indexes from `818f279b6dde`.
- Substring keyword matching (`LIKE '%term%'`) can't use a B-tree index, so a new index wouldn't help. Keyword
  queries scan the caller's scoped rows and are bounded by `LIMIT` and pagination.
- If very large tables make keyword search slow, consider MySQL FULLTEXT indexes or prefix matching as a reviewed,
  separate change.

**Limitations.**
- No relevance scoring beyond exact, prefix and substring.
- No stemming or fuzzy matching.
- Keyword search is not full-text.
- Global search covers the types listed above only.

### Frontend

```powershell
cd frontend
npm install
npm run dev                           # http://localhost:5173 (proxies /api to :8000)
```

Other commands: `npm run lint` (oxlint) and `npm run build` (production bundle in `frontend/dist/`). The backend must be running on port 8000.

The UI includes:
- a dashboard;
- a role-aware sidebar and global search;
- people, attendance, leave, payslip and payroll pages;
- risk, analytics, recommendation and intervention pages;
- task, performance, activity, report, audit and dataset pages.

Every page calls the real API. It shows loading, empty and error states and never substitutes placeholder values. Role visibility mirrors the backend, which remains the security boundary.

See [frontend/README.md](frontend/README.md) for:
- the structure;
- each page with its roles and backend endpoints;
- session and error handling;
- limitations, including that there is no frontend test runner and some workflows are API-only: payroll calculation and approval, dataset upload, creating interventions, and manager review evaluations.
