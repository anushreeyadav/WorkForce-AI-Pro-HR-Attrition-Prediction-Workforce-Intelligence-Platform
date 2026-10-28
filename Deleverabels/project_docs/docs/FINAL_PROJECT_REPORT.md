# WorkForce AI Pro: final project report

> Copy of the project document for this submission package. Links to source files were rewritten to point to the copies in `01_Backend_Source_Code/` and `02_Frontend_Source_Code/`; the original in the project is unchanged.

WorkForce AI Pro is an HR attrition-risk and workforce-intelligence platform. This report covers the state of the project after the final integration phase (Sub-prompt 30).

Everything below is based on what was built and observed in the development environment. **This is a working development build, not a production-verified system.** The sections on verification status and limitations explain what has not been verified.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2, Pydantic v2, PyJWT, bcrypt, PyMySQL |
| ML | pandas, NumPy, scikit-learn (logistic regression), joblib |
| Database | MySQL 8.0 (target), managed by an Alembic migration chain of 15 revisions |
| Frontend | React 19, Vite 8, Tailwind CSS 4, React Router 7, Axios, Recharts 3, lucide-react |
| Tests | pytest with FastAPI TestClient on in-memory SQLite; Node's built-in test runner for frontend logic |

## What was built

### Backend

The backend has 21 routers and **167 audited routes**; see [SECURITY_RBAC.md](SECURITY_RBAC.md) for the full inventory.

| Area | Capabilities |
|---|---|
| Authentication and users | Registration (EMPLOYEE only), login, current user, logout (client-side) |
| People | Employees, departments, shifts |
| Time and leave | Attendance (check-in/out, reports, corrections); leave (types, policies, requests, balances, approvals, conflicts) |
| Payroll | Components, salary profiles, periods, calculation, approval, payment status, payslips |
| Datasets and AI | Dataset registry (upload, versions, validation, archive, download); attrition model training, activation and prediction |
| Risk | Risk monitoring, alerts, review deadlines (SLA) and escalation |
| Analytics | Workforce snapshots, trends, forecast, stability indicator and retention |
| Retention workflow | HR interventions; rule-based recommendations (`rules-v1`, `rules-v2`) |
| Work | Workforce tasks; performance management (cycles, goals, reviews) |
| Monitoring and oversight | Activity feed with signed incremental polling, monitoring status and operational signals; reports and exports (CSV, XLSX, PDF); audit logs and audit export |
| Shared | Notifications; global search |

### Frontend: 19 pages

Dashboard, Search, Notifications, Employees, Departments, Attendance, Leave, My payslips, Payroll, Risk monitoring, Workforce analytics, Recommendations, Interventions, Tasks, Performance, Activity feed, Reports, Audit logs, Datasets, plus the login page.

### API versus what the UI exposes

| Workflow | In the UI | API-only |
|---|---|---|
| Sign-in, role dashboard, search, notifications | Yes | — |
| Employees and departments | Directory, detail, HR/ADMIN create and edit, employee status changes | — |
| Attendance | Check-in/out, own records, team reports | Attendance corrections; shift administration |
| Leave | Request, cancel, balances, review queue (approve/reject) | Leave type and policy administration; attendance-conflict report |
| Payroll | **New in Sub-prompt 30:** create a period, calculate, open a record, approve, mark paid. Also exports, and payslips for everyone | Salary components and profiles |
| Datasets and AI | **New:** model status, dataset upload, new versions, validation. Also the version list | Model training and activation; individual or batch predictions; prediction history; archive and download |
| Risk | Alert list, acknowledge, resolve or dismiss; **new:** "Plan intervention" from an alert | Monitoring runs; alert assignment; escalation evaluation; risk-event history |
| Interventions | **New:** create, start, add note, assign to me, complete, cancel, history | Editing title, priority or due date |
| Recommendations | Evaluate (rules-v1 or rules-v2), accept, dismiss, complete | Create an intervention or link a task from a recommendation |
| Tasks | Create, assign, all transitions, history | — |
| Performance | Goals; employee self-assessment. **New:** reviewer evaluation (draft and submit); HR finalize, reopen and cancel; new review; cycle creation and transitions | Cycle edits after creation; goal history |
| Analytics | Overview, trends, stability (including the department comparison), forecast, snapshot capture | Retention endpoint; department detail endpoint; snapshot list |
| Activity | Feed, filters, monitoring status, incremental updates (manual or optional auto-check) | Operational signals; department activity |
| Reports and audit | All catalog reports in all formats; audit query and export | — |

API-only items are fully implemented and tested in the backend. The UI does not offer controls that cannot complete their action.

## Authentication and RBAC

- **Accounts and tokens:**
  - Roles: ADMIN, HR, MANAGER, EMPLOYEE.
  - Bcrypt password hashes. HMAC-signed JWT access tokens that carry only identifiers; the user and role are reloaded from the database on every request.
  - Deactivated or deleted accounts are refused immediately.
  - The frontend stores the token in `sessionStorage`, cleared when the tab closes.
- **Two layers, both enforced on the server** (details in [SECURITY_RBAC.md](SECURITY_RBAC.md)):
  1. **Role checks on every route.** A wrong role gets 403.
  2. **Department and record scope in queries.** Managers are limited to the department of their current employee record; employees to their own records. Out-of-scope records return 404, and visible records the caller may not change return 403.
- **Frontend role checks are for display only.** A test checks that the frontend's navigation matches the backend role matrix for all 19 pages.
- **Logout revokes the token on the server** (since Sub-prompt 31; see [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md)). If the server cannot be reached, the UI still discards the token, but it then stays valid until it expires.

## AI model and analytics status

- **The attrition model is not production-validated.**
  - The repository contains **no trained model and no evaluation evidence** on real data.
  - Tests train only on synthetic data in temporary directories.
  - Without an active model with artifact files, prediction endpoints return 503 with a reason code, and the UI shows "No usable model".
  - Risk outputs are statistical review aids, never a basis for employment decisions.
- **The stability indicator is a documented heuristic,** not a validated measure. It is withheld (`insufficient_data` or `stale_data`) for small populations, stale snapshots or missing data.
- **The headcount forecast is a linear trend.** It is refused when snapshots are stale, and it reports skipped months.
- **Recommendations are deterministic, versioned rules,** not a trained model.

## Sub-prompt 30 changes

### Integration defects fixed

1. **A late 401 from an earlier session signed out the next user.**
   - **Problem:** the client cleared whatever token was current when any 401 arrived. A request still in flight from a previous session (signed out, or another user signed in on the same tab) could therefore eject the new user.
   - **Fix:** the client now ends the session only for a 401 on a request that carried the current token. The rule lives in `lib/session.js` and is unit-tested.
2. **Redirect after sign-in ignored the new account's role.** After a different user signed in, the app returned them to the previous user's page (for example `/payroll`). It now returns only to pages the new account may open, and never to an off-site URL.
3. **Activity updates did not follow the documented polling contract.**
   - **Duplicates:** updates were appended without removing duplicates by `event_id`.
   - **Expired cursors:** an expired (410) or mismatched (422) cursor just showed an error.
   - **Stale responses:** a response arriving after the filters changed was appended to the new view.
   - **Fix:** de-duplication against the feed page and earlier responses; automatic feed reload for a fresh cursor; stale responses dropped; no overlapping requests (`lib/feed.js`, unit-tested).
4. **Payroll period cards never showed "Not calculated".** The API lists every status count including zeros; the page now shows only non-zero counts.
5. **Accessibility:**
   - Collapsed sidebar links had no reliable accessible name (only a `title`).
   - Form hints were not linked to their fields (`aria-describedby`).

### Workflows added to the UI

All use existing endpoints and existing RBAC; the backend is unchanged.

- **Interventions:** create (including from a risk alert, pre-filled), start, note, assign to me, complete, cancel, history. Each action sends the version shown, so a concurrent change is reported as a 409.
- **Payroll:** new period, calculation (with confirmation and a skip list), record detail, approve, mark paid. Self-approval is refused by the API and the message is shown.
- **Performance:**
  - Reviewers: evaluation editor (draft and submit).
  - HR/ADMIN: finalize, reopen with a reason, cancel; new review (reviewer "Me" or a chosen colleague; the server checks eligibility); cycle creation and transitions.
- **Datasets:** model-status card; upload a dataset or a new version (ADMIN); validate and see the report summary.
- **Activity:** monitoring-status strip; opt-in auto-check at the server's recommended interval (at least 15 s), only while the tab is visible, stopped on unmount and sign-out. The feed is labelled as polling, not live.

## Verification results (observed in this phase)

| Check | Result |
|---|---|
| Backend full suite (baseline at the start of Sub-prompt 30) | 2167 passed, 1 skipped |
| Backend full suite (final) | 2177 passed, 1 skipped, 0 failed |
| Authorization matrix | 313 passed |
| Query scaling | 3 passed |
| Frontend/backend query contract | 156 passed |
| UI workflow journeys (new, `test_ui_workflows.py`) | 8 passed |
| Frontend navigation vs. backend roles (new, `test_frontend_navigation.py`) | 2 passed |
| No server errors sweep | 228 passed |
| Migrations | 18 passed |
| Frontend unit tests (new, `npm test`) | 22 passed |
| Frontend lint (oxlint) | 0 warnings, 0 errors |
| Frontend production build | Succeeds; `index` chunk 356.5 kB (gzip 115.6 kB); pages lazy-loaded |
| Migration head | `a0bd1c34f49c`, single linear chain of 15 revisions |

**The one skip:** `tests/test_dataset_validation.py:200`. The symlink-escape test for dataset storage cannot run because this Windows account may not create symlinks.

### Browser testing status

- **What ran:** a **limited real-browser smoke test** of the production build, served by `vite preview` and rendered in headless Microsoft Edge with no backend running.
  - `/login` rendered the sign-in form and "Service status: unreachable". An unavailable backend is reported, not hidden.
  - `/` and `/payroll` redirected a signed-out visitor to the sign-in form.
- **What did not run:**
  - **Signed-in journeys in a browser.** There is no MySQL server, and the backend needs one for every data request.
  - **Click-through, responsive or keyboard testing in a browser.** The project has no browser automation, and none was added.
- **What stands in for it:** the UI workflows are covered by request-level tests that replay the pages' exact requests (`test_ui_workflows.py`, `test_frontend_contract.py`) and by frontend unit tests of the session, feed, navigation and error logic.
- **Not yet verified:** browser end-to-end verification of the 19 pages.

## Setup and local run (safe)

Prerequisites: Python 3.14, Node.js 24, and MySQL 8.0 for real data. The test suites need no database.

```powershell
# Backend
cd backend
.\.venv\Scripts\Activate.ps1               # or create: py -3.14 -m venv .venv
pip install -r requirements.txt
Copy-Item .env.example .env                # only if backend\.env does not exist; then set DB_USER, DB_PASSWORD, JWT_SECRET_KEY
uvicorn app.main:app --reload              # http://127.0.0.1:8000/docs
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider   # tests: in-memory SQLite, no .env or MySQL needed

# Frontend
cd ..\frontend
npm install
npm run dev                                # http://localhost:5173, proxies /api to 127.0.0.1:8000
npm run lint; npm test; npm run build
```

- Generate a JWT secret locally: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Never commit `.env`.
- The server refuses to start with missing or unsafe settings.
- There is no default administrator. See "Provisioning the first administrator" in the root README.

## Database migrations: operator review required

**No migration has been applied to any real database during this project.** All 15 revisions were verified only on disposable SQLite databases and as rendered MySQL SQL. Before modifying a real database, an operator must:

1. Take and verify a backup.
2. Confirm that `DB_HOST`, `DB_PORT` and `DB_NAME` in `backend/.env` point to the intended database (never a shared or production server by accident), and that `GET /api/health/db` reports `reachable`.
3. Run `alembic heads` (expect the single head `a0bd1c34f49c`) and `alembic upgrade head --sql`, then read the generated DDL.
4. Only then run `alembic upgrade head`, and confirm with `alembic current`.

`alembic downgrade` drops tables and their data. See [database/README.md](../../01_Backend_Source_Code/database/README.md).

## Deployment prerequisites (not performed)

**No deployment was performed or tested.** A deployment would need at least:
- **Infrastructure:**
  - A managed MySQL 8 instance with backups.
  - A dedicated least-privilege database user.
  - The secrets above supplied by a secret store.
- **Network:**
  - TLS termination in front of the API.
  - `CORS_ORIGINS` set to the real frontend origin.
  - The frontend built with `npm run build` and served as static files with the API reachable at `VITE_API_BASE_URL`.
- **Application:**
  - A process manager for uvicorn.
  - A first administrator provisioned by a deliberate, reviewed operator action.
- **Controls that do not exist yet:**
  - Rate limiting on sign-in.
  - Log shipping and monitoring.

## Known limitations and remaining risks

- **Security and accounts** (see [SECURITY_RBAC.md](SECURITY_RBAC.md)):
  - Closed in Sub-prompt 31 ([DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md)): account administration (ADMIN-only API), server-side token revocation, sign-in and registration rate limiting, and loss of access for ADMIN/HR with an INACTIVE employee record.
  - Still open: rate limits are per process; there is no password reset, MFA or refresh-token flow.
- **Verification:**
  - Nothing has been verified on MySQL: integration, migration application or query performance (see [PERFORMANCE.md](PERFORMANCE.md)).
  - There is no signed-in browser or end-to-end testing.
  - There are no load or concurrency tests.
- **AI:** the model is unvalidated; there is no trained model and no evaluation on real data.
- **Operations:**
  - Long synchronous requests: model training, dataset validation, very large payroll runs and large exports. Candidates for background processing.
  - There is no scheduler: snapshots and monitoring runs are triggered manually.
- **UI coverage:** the workflows listed as API-only above are not in the UI.

Further detail: [SECURITY_RBAC.md](SECURITY_RBAC.md), [PERFORMANCE.md](PERFORMANCE.md), [QA_REPORT.md](QA_REPORT.md).
