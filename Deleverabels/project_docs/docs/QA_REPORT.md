# QA report (Sub-prompt 29)

> Copy of the project document for this submission package. Links to source files were rewritten to point to the copies in `01_Backend_Source_Code/` and `02_Frontend_Source_Code/`; the original in the project is unchanged.

This was a testing and defect-finding pass over the backend, frontend, authorization, migrations and AI/analytics modules. Every result below was observed in this session. The only environment is the development machine; there was no MySQL server and no browser.

## Environment and commands

- **Backend:** Python 3.14 virtualenv (`backend/.venv`), pytest. Tests run on **in-memory SQLite** with the test-only placeholder settings from `tests/conftest.py`; `.env` is never read and MySQL is never contacted. ML artifacts and datasets in tests go to temporary directories.
- **Frontend:** Node 24, Vite 8, oxlint.

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -rs            # full suite
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_authorization_matrix.py
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_query_scaling.py
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_migrations.py
.venv\Scripts\python.exe -m alembic heads      # with placeholder DB_* / JWT_SECRET_KEY environment variables only
.venv\Scripts\python.exe -m alembic history    # reads migration scripts only, no database connection
cd ..\frontend
npm run lint
npm run build
```

## Results

| Check | Baseline (before this phase) | Final |
|---|---|---|
| Full backend suite | 1783 passed, 1 skipped, 0 failed | 2167 passed, 1 skipped, 0 failed |
| Authorization matrix (`test_authorization_matrix.py`) | included above | 313 passed |
| Query scaling (`test_query_scaling.py`) | included above | 3 passed |
| Migrations (`test_migrations.py`) | included above | 18 passed |
| New: frontend contract (`test_frontend_contract.py`) | — | 156 passed |
| New: server-error sweep (`test_no_server_errors.py`) | — | 228 passed |
| Frontend lint (oxlint) | 0 warnings, 0 errors | 0 warnings, 0 errors |
| Frontend production build | succeeds | succeeds; `index` 355.98 kB (gzip 115.54 kB) |

**The one skip:** `tests/test_dataset_validation.py:200` checks that a symbolic link inside the dataset directory is refused. It is skipped because this Windows account may not create symlinks. This **is** security-relevant: symlink escape protection for dataset storage was not exercised on this machine. The other path checks (resolution inside the storage directory, rejected names) did run and pass.

## Defects found

**No reproducible product defects were found in this phase.** No production code was changed.

One test expectation of mine was wrong and was corrected:
- My first draft of the server-error sweep asserted that every list route rejects an unknown query parameter.
- In fact, routes with a filter model reject unknown fields (422). Routes that take no query parameters at all (summaries, catalogs, `/auth/me`) ignore them, which is FastAPI's default behaviour.
- An ignored parameter cannot widen what a caller sees, so this is not a defect. The sweep now asserts "never a 5xx" for every route.

## Coverage added in this phase

1. **`tests/test_frontend_contract.py`** (156 cases). The backend rejects unknown query fields, so a filter the React app sends but the API does not accept would break a page with a 422. This module checks every query the frontend can send:
   - It replays every GET request the frontend makes (paths, filter names and every option value offered in the UI, as listed in `frontend/src/pages/*.jsx`), as the role that would send it.
   - It builds audit filters from the live audit catalog.
   - It runs every report in the report catalog, in every format, as HR and as a manager.
   - It covers the activity feed's incremental-update request with its cursor and filters.
   - Result: all accepted, no mismatches.
2. **`tests/test_no_server_errors.py`** (228 cases):
   - **Empty world:** every registered route as ADMIN and as EMPLOYEE, with empty bodies and placeholder ids.
   - **Malformed queries:** every list route with zero, negative and oversized paging, a 500-character search, non-numeric and negative ids, invalid dates, reversed date ranges and an unknown filter.
   - **Populated world:** every route as ADMIN, HR and MANAGER against a world with records in every module (id 1 exists), with empty bodies.
   - **Result:** no 5xx anywhere, except the documented 503 `artifact_error` from single-employee prediction when the active model has no artifact files. The test checks that code explicitly.

## Security and regression coverage (re-verified)

The tests below exist from earlier phases and pass in the final run:
- **Authentication and roles:**
  - All 167 routes are checked against the reviewed role matrix.
  - Every protected route returns 401 for a missing, malformed, expired, wrongly signed, unknown-user or deactivated-user token.
  - Every disallowed role gets 403.
  - Registration cannot inject a role.
  - Inactive accounts are refused.
- **Department and record scope:**
  - Managers are pinned to their department and get 404 for other departments.
  - Employees reach only their own records.
  - Visible-but-forbidden actions return 403.
  - Stale reviewer assignments and departed (INACTIVE) managers retain nothing.
- **Privacy:**
  - Ratings and private review notes are hidden from the employee, including in history and audit rows.
  - Risk scores are never searchable or shown to employees.
  - Notifications contain no reasons, review text or scores. The one notification with an employee name (leave awaiting review) goes only to that department's managers, who review the request.
  - Audit rows never hold passwords or tokens, and failed sign-ins record neither the email nor the password.
  - 422 responses never echo submitted input.
  - Logging records exception type names only.
- **Files:**
  - Dataset storage resolves paths inside its directory.
  - Model artifact names must match `lr-<14 digits>-<8 hex>` and resolve inside the artifact directory.
  - Activation looks the version up in the database first.
  - CSV and XLSX exports neutralise formula injection.
- **Workflows:**
  - Covered: leave, payroll (calculation, approval and payment transitions, payslips, audited exports), attendance corrections, tasks (transitions, idempotency, version conflicts), performance reviews (privacy, eligibility, finalization), interventions, recommendations (rules-v1 and rules-v2 versioning, deduplication, supersession, lifecycle), risk monitoring and escalation (thresholds, idempotency, recipients), activity-feed cursors (signature, expiry, scope binding), and workforce stability and forecasts (minimum population, stale snapshots, missing months, department scoping).
- **Query scaling:** the statement count of 38 read endpoints, payroll calculation and the risk-monitoring run does not grow with the data.

## Frontend review (static, not in a browser)

Reviewed by reading the code and by the contract test above:
- **Routes:** route guards and role navigation come from one table (`lib/navigation.js`) that matches the backend role matrix.
- **Sessions:** a 401 signs the user out and shows "session expired".
- **Errors and empty data:**
  - 403 shows "not available for your role".
  - A failed metric shows "Unavailable", never 0. The remaining `?? 0` fallbacks read count maps from successful responses, where an absent bucket means zero.
- **Risk:** individual risk scores are not rendered anywhere; only aggregate stability indicators appear.
- **Analytics:** the page makes exactly one stability request per view (`Analytics.jsx`).

**Not executed: browser or end-to-end tests.** No browser automation exists in the project, and none was added (that would be a new dependency). Rendering, focus behaviour, responsive layout and real navigation have not been verified in a browser.

## Migration verification

- **Chain:** 15 revisions, linear, from `14cb546c9a5e` to `a0bd1c34f49c`. `alembic heads` shows the single head `a0bd1c34f49c`.
- **Tests (`tests/test_migrations.py`, 18 passed):**
  - single linear head;
  - no database URL in `alembic.ini`;
  - upgrade to head on a **disposable SQLite** database matches the models, and downgrade to base removes everything;
  - offline MySQL SQL rendering creates only the expected objects;
  - each revision adds only its own tables, columns and indexes;
  - the rules-v2 revision's guarded downgrade is preserved.
- **No migration was added.** No migration was run against MySQL.

## AI and analytics validation

- **The attrition model is not validated.** The repository contains **no trained model artifacts and no evaluation evidence** (`backend/ml/models` is empty). The model must not be described as validated or production-ready.
- **Model unavailable:** the API returns 503 with `no_active_model`, `schema_mismatch` or `artifact_error`. Batch prediction is capped at 100 employees.
- **Training:** tests train only on synthetic data in temporary directories, to check the pipeline and the activation gates.
- **Stability indicator:** documented heuristic, not a validated predictor. **Forecast:** linear trend with an explicit staleness check.

## Not executed, and why

| Not run | Reason |
|---|---|
| MySQL integration, upgrade and downgrade on MySQL, `EXPLAIN` plans, performance profiling | Constraint: no connection to the real MySQL server, and no disposable MySQL instance available |
| Browser / end-to-end UI tests | No browser test setup in the project; not added in this phase |
| Load, concurrency and throughput tests | Out of scope; no measurement environment |
| Symlink escape test | The OS account may not create symlinks (see the skip above) |
| Validation of the attrition model on a real dataset | No approved dataset or trained model exists |

## Outstanding risks and production-readiness limitations

- **Accounts and tokens** (see `docs/SECURITY_RBAC.md`):
  - There is no account-administration API, so deactivating a login requires a database change.
  - Logout does not revoke tokens.
  - There is no rate limiting.
- **Unverified environments:** MySQL-specific behaviour and performance (see `docs/PERFORMANCE.md`), and the UI in a real browser.
- **Long synchronous requests:** training, dataset validation, very large payroll runs and large exports.
- **Attrition model:** unvalidated. Its outputs are review aids only.
