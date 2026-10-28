# Sub-prompt 32 evidence

Outputs produced while closing the production-readiness gaps (2026-10-27 to 10-28). Everything ran locally with synthetic data:
- **MySQL:** a **disposable** MySQL 8.0.46 instance started from a scratch data directory on `127.0.0.1:3399`, never the machine's `MySQL80` service or any real database. Its identity (`@@datadir`, port) was checked before every DDL step.
- **Backend:** the unmodified application on a throwaway SQLite file, or on that disposable database.

The readiness assessment is in [`../../DEPLOYMENT_READINESS.md`](../../DEPLOYMENT_READINESS.md).

| Folder | Content |
|---|---|
| `mysql/01_migrations_mysql_roundtrip.txt` | First MySQL run: upgrade, then a full downgrade and upgrade. Recorded after the 1553 downgrade fix; before it, the downgrade failed and left `users.token_version` dropped, a failure kept only in this document's history |
| `mysql/02_datetime6_upgrade_roundtrip.txt` | New revision `5b9e2d7c41a8`: up, down, up |
| `mysql/03_concurrency_before_fix_repeatable_read.txt` | Concurrency tests **before** the isolation fix: double-booked leave `[201, 201, 409, 409]` and `[500, 500, 201, 500]` |
| `mysql/04_concurrency_after_fix_read_committed.txt` | After all fixes: 11 tests, 5 of 5 repetitions pass; admin-race outcome counts |
| `mysql/05_full_suite_on_mysql.txt` | Full backend suite in MySQL mode: 2260 passed, 7 skipped |
| `mysql/06_final_full_chain_roundtrip.txt` | Final: all 17 revisions down to base and back; live schema equals the models |
| `tests/` | Full SQLite suite (2255 passed, 12 skipped), the focused suites, and frontend test, lint and build |
| `browser/browser_results.json`, `browser/screenshots/` | 17 of 17 Edge workflow tests with notes (API setup and checks labelled); 21 genuine screenshots of synthetic data |
| `load/load_sqlite_c8.json`, `load/load_mysql_c8.json`, `load/load_mysql_c16.json` | Bounded load runs (after the sign-in deadlock fix): percentiles, throughput, status codes, server CPU and memory |
| `security/npm_audit.txt`, `security/pip_audit.txt` | Dependency audits: 0 known vulnerabilities |

**Not kept as files:**
- the pre-fix MySQL load runs (5 of 60 and 4 of 64 sign-in deadlocks, shown as 409);
- the first full MySQL run (2205 passed, 24 failed, all analysed in DEPLOYMENT_READINESS.md).

Those runs were superseded, and their numbers are quoted in the readiness document.

## Source changes in this phase

They were compared byte-for-byte with the pre-phase snapshot in `Deleverabels/01_*` and `02_*`, which was **not modified**.

- **Backend application:**
  - `app/api/attrition.py`, `app/services/attrition.py`: activation-time checks; MySQL `Decimal` features.
  - `app/api/auth.py`, `app/schemas/auth.py`: password change; sign-in lock order.
  - `app/api/health.py`: readiness.
  - `app/core/database.py`: READ COMMITTED.
  - `app/main.py`: MySQL error mapping; API CSP.
  - `app/models/base.py`: `DATETIME(6)`.
  - `app/services/audit_catalog.py`: 3 new audit actions.
  - `ml/attrition/training.py`: extended evaluation.
- **Migrations:**
  - `alembic/env.py`;
  - **new** `5b9e2d7c41a8`;
  - downgrade-only fixes in 14 existing revisions. Upgrade paths are unchanged.
- **Backend tests:**
  - **New:** `mysql_guard.py`, `test_mysql_concurrency.py`, `test_production_hardening.py`.
  - **Updated:**
    - `conftest.py`: opt-in MySQL mode;
    - dialect-aware constraint assertions in `test_attendance_api.py`, `test_interventions_api.py`, `test_leave_requests_api.py` and `test_payroll_api.py`;
    - hardcoded ids removed from `test_reports_api.py` and `test_tasks_api.py`;
    - simulated-writer markers in `test_interventions_api.py`, `test_payroll_api.py`, `test_recommendations_api.py`, `test_risk_monitoring_api.py` and `test_workforce_analytics_api.py`;
    - new routes added to the reviewed inventories (`test_authorization_matrix.py`, `test_models.py`);
    - `test_attrition_api.py` and `test_attrition_ml.py`: new tests;
    - `test_migrations.py`: new head and guards;
    - `test_no_server_errors.py`: the readiness probe's designed 503.
- **Frontend:**
  - `components/ChangePassword.jsx` (new), `AppLayout.jsx`, `auth/AuthContext.jsx`: password change;
  - `components/EmployeePicker.jsx`, `pages/Performance.jsx`: crash fix;
  - `api/client.js`: the 429 message;
  - `lib/props.test.js` (new);
  - `vite.config.js`: CSP for preview.
- **Documentation:** `docs/DEPLOYMENT_READINESS.md` (rewritten), `docs/FINAL_PROJECT_REPORT.md`, `docs/AI_MODEL_VALIDATION.md` (new), `docs/SECURITY_RBAC.md`, `database/README.md`, `backend/ml/README.md`, `README.md`.
- **QA tooling (new):** `qa/`.
