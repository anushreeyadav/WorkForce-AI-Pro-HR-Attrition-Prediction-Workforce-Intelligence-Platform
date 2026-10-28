# Deployment readiness (updated in Sub-prompt 32)

This document records what is verified, what is not, and what an operator must do before a deployment.

- **Sub-prompt 31** hardened authentication, account lifecycle and configuration.
- **Sub-prompt 32** closed production-readiness gaps with evidence:
  - the application ran against a **disposable MySQL 8.0 instance**;
  - it ran under **bounded local load**;
  - its workflows ran in a **real browser**;
  - security was hardened further.

**Nothing was deployed.** No real database, `.env` file or production infrastructure was used. All evidence is in [`docs/evidence/subprompt32/`](evidence/subprompt32/README.md).

## Decision: CONDITIONAL STAGING READY

| Target | Decision | Basis |
|---|---|---|
| **Staging** (synthetic or approved test data, restricted access) | **Conditional go** | MySQL 8.0 behavior is verified on a disposable instance:<br>• the full suite: 2260 passed;<br>• migrations up, down and up again;<br>• real concurrent requests.<br>The 17 browser workflows pass, bounded load gives 0 errors, and dependency audits are clean. Conditions are listed below. |
| **Production** (real employee data) | **Not ready** | • MySQL is not verified in the intended environment (version, managed-service configuration, data volume).<br>• The attrition model has no real-world validation (BLOCKED).<br>• Monitoring, alerting and backups are not set up.<br>• Rate limits are per process, and there is no MFA or password reset.<br>• Capacity is unknown beyond one local machine. |

**Staging conditions:**
1. Apply the migrations by the operator procedure below. The head is `5b9e2d7c41a8`; its `DATETIME(6)` revision rebuilds every table.
2. Use MySQL 8.0 with `binlog_format=ROW`.
3. Run **one** uvicorn worker, or accept per-worker rate limits.
4. Put TLS and the documented reverse proxy, with its headers, in front.
5. Use strict configuration (`ENVIRONMENT=staging`).
6. Pass `GET /api/health/ready` and the smoke-test plan.
7. Do not activate any attrition model trained on real data.

## Gap analysis and status (Sub-prompt 32)

The status values used below:

| Status | Meaning |
|---|---|
| **PASS** | Sufficient evidence supports completion. |
| **PARTIAL** | Some checks passed, but coverage is incomplete. |
| **BLOCKED** | Infrastructure, data or approval is missing. |
| **NOT VERIFIED** | Adequate evidence has not been obtained. |
| **FAIL** | A required check failed. |

| # | Area (priority) | Covered before this phase | Gap found | Done in this phase | Status |
|---|---|---|---|---|---|
| 1 | **MySQL compatibility** (critical) | Offline DDL only; never run on MySQL | Unknown runtime behavior | Ran the full suite on a disposable MySQL 8.0.46 and **fixed 5 MySQL-only defects** (next section) | **PARTIAL**: verified on local MySQL 8.0.46; the intended deployment database is NOT VERIFIED |
| 2 | **Concurrency and locking** (critical) | SQLite only, which serializes writers | Lock semantics untested | New multi-connection MySQL tests (11, 5 of 5 repetitions); fixed the isolation level, deadlock mapping and sign-in deadlock | **PASS** for the tested paths. Other write paths are covered by the READ COMMITTED change but have no dedicated race test |
| 3 | **Performance and load** (high) | Query-count tests | No load test | Bounded harness `qa/loadtest.py`; runs on SQLite and MySQL | **PARTIAL**: one machine, one worker, synthetic data, not a capacity estimate |
| 4 | **AI model validation** (high) | Gate, integrity, 503 behavior | Activation did not re-check the artifact or gate; evaluation lacked operating-threshold and calibration metrics | Activation-time checks; extended evaluation report; validation protocol ([AI_MODEL_VALIDATION.md](AI_MODEL_VALIDATION.md)) | **BLOCKED** for production validation (no real evaluation data or acceptance criteria). Pipeline controls: **PASS** |
| 5 | **Browser workflows** (high) | A signed-out smoke test only | No signed-in browser testing | `qa/browser_tests.py`: 17 tests in Edge covering all 10 required workflows; found and fixed a crash in the review and intervention forms | **PASS** (Edge/Chromium desktop) |
| 6 | **Mobile viewport** (medium) | None | Untested | 390 px emulation: navigation drawer and 11 pages without horizontal overflow | **PARTIAL**: emulation only, no physical device |
| 7 | **Security hardening** (high) | Revocation, session versions, rate limits, headers | No CSP; no password change; MySQL errors reported as an outage | CSP (frontend and API), self-service password change, MySQL error mapping, readiness check | **PARTIAL**: see "Unresolved security risks" |
| 8 | **Dependency security** (medium) | Not run | — | `npm audit`: 0; `pip-audit`: 0 known vulnerabilities | **PASS** at scan time (2026-10-28) |
| 9 | **Operational readiness** (high) | Health checks; partial guidance | No readiness check; no backup, monitoring or incident procedures | `GET /api/health/ready`; procedures below | **NOT VERIFIED**: documented, not exercised. Backups and monitoring: **BLOCKED** (no infrastructure; recovery objectives need owner approval) |

## MySQL defects found and fixed (all found on the disposable MySQL 8.0.46)

1. **Downgrades failed on MySQL (error 1553) and left the schema half-changed.**
   - **Cause:** autogenerated downgrades dropped indexes that foreign keys still needed, before dropping their tables. MySQL DDL is not transactional, so the failing step left the schema half-changed.
   - **Fix:** removed 60 redundant `drop_index` calls across 13 revisions (`DROP TABLE` removes its indexes on every database), and reordered the rules-v2 batch downgrade (foreign keys first). Upgrade paths are unchanged.
   - **Guard:** a static test fails on the original files and passes on the fixed ones.
   - **Evidence:** `mysql/01`, `mysql/06`.
2. **Concurrent leave submissions double-booked or failed (500).**
   - **Cause:** under MySQL's default REPEATABLE READ, reads after a `FOR UPDATE` row lock still use the snapshot taken *before* the lock. Two simultaneous submissions of the same dates both passed the overlap check, so the days were reserved twice. First requests of a year raced to create the balance row and returned 500.
   - **Fix:** the application's sessions run at **READ COMMITTED** (`app/core/database.py`, `ISOLATION_LEVEL`). The test guard uses the same level.
   - **Evidence:** before `[201, 201, 409, 409]` and `[500, 500, 201, 500]`; after, 5 of 5 repetitions pass (`mysql/03`, `mysql/04`).
3. **Deadlocks and lock-wait timeouts were reported as "Database unavailable" (503); CHECK violations too.**
   - **Fix:** `app/main.py` maps MySQL 1213/1205 to **409** "conflicted with a concurrent change, please retry", and 3819 (CHECK) to **500**, the same as SQLite. Other operational errors stay 503.
   - **Evidence:** the admin race sometimes produces an InnoDB deadlock, now a clean 409.
4. **Sign-in deadlocked when one account signed in concurrently.**
   - **Cause** (InnoDB deadlock report): the audit row's foreign-key check takes a shared lock on the user row, then the `last_login_at` update needs an exclusive one.
   - **Fix:** write the account row first.
   - **Evidence:** the regression test fails without the fix (`… 409`) and passes with it. MySQL load runs at concurrency 8 and 16 went from 8.3% and 6.25% sign-in errors to 0.
5. **Every prediction failed (500) for employees with attendance.** MySQL returns `SUM()` as `Decimal`, which cannot be stored in the JSON feature column. **Fix:** convert to `int` and `float` (`app/services/attrition.py`).

**Also changed:**
- **Timestamps are `DATETIME(6)` on MySQL** (new revision `5b9e2d7c41a8`, about 90 columns, MySQL only). Plain `DATETIME` rounds to whole seconds, so stored times could be up to 0.5 s in the future and same-second events compared equal. The code and the test suite assume microseconds.
- **The opt-in MySQL test mode** (`backend/tests/mysql_guard.py`) refuses to run unless it positively identifies a disposable database:
  - name `wfai_test_*` or `wfai_disposable_*`;
  - a loopback host;
  - a marker row that matches `WFAI_MYSQL_TEST_MARKER`;
  - the schema at head;
  - an empty `users` table.

  It never creates, migrates or drops anything. Its refusal paths were tested (wrong name, wrong marker, remote host).

**Not verified on MySQL:** the intended production server (version, managed-service parameters, `sql_mode`, collation defaults); data volumes beyond about 500 employees and 15,000 attendance rows; replication; failover; and backup and restore.

## Load and concurrency results (measured; indicative only)

**Setup:** the `qa/loadtest.py` safety limits apply:
- loopback targets only;
- concurrency at most 32;
- at most 2000 requests per scenario and 120 s;
- 15 s timeout per request;
- a scenario stops if more than 20% of requests fail.

**Environment:** one Windows 11 machine with 8 logical CPUs. Client, one uvicorn worker and MySQL all share it. The data is synthetic: 509 employees, about 30 days of attendance each, and an active synthetic model. Full results, including status codes and server CPU and memory, are in `evidence/subprompt32/load/`.

| Scenario | SQLite, conc. 8<br>p50 / p95 ms, req/s | MySQL, conc. 8<br>p50 / p95 ms, req/s | MySQL, conc. 16<br>p50 / p95 ms, req/s |
|---|---|---|---|
| Sign-in (bcrypt, 12 rounds) | 961 / 1066, 7.8 | 970 / 1285, 7.5 | 1850 / 2370, 7.9 |
| `GET /auth/me` | 82 / 177, 86.4 | 91 / 150, 83.1 | 115 / 1082, 56.9 |
| Employee list (HR) | 181 / 262, 42.2 | 202 / 258, 39.3 | 403 / 589, 38.3 |
| Attendance list (HR) | 261 / 331, 30.3 | 304 / 386, 26.5 | 441 / 779, 35.8 |
| Leave requests (manager) | 132 / 174, 58.9 | 154 / 187, 51.9 | 176 / 236, 86.6 |
| Payroll records (HR) | 276 / 366, 28.3 | 277 / 362, 33.5 | 356 / 513, 43.3 |
| Datasets list (ADMIN) | 150 / 198, 52.4 | 177 / 230, 44.6 | 200 / 296, 76.6 |
| Model status (HR) | 160 / 210, 49.0 | 185 / 228, 42.9 | 177 / 237, 87.6 |
| Global search (HR) | 276 / 355, 28.7 | 351 / 426, 22.8 | 344 / 550, 44.1 |
| Prediction (writes a row) | 561 / 977, 13.2 | 560 / 748, 15.7 | 513 / 749, 29.6 |
| **Total requests / errors** | 3960 / 0 | 3960 / 0 | 7864 / 0 |
| Peak server memory | 274 MB | 270 MB | 282 MB |

**Observations:**
- **Sign-in is CPU-bound by bcrypt.** About 8 sign-ins/s used about 6.6 cores. Size this deliberately: bcrypt cost 12 is a security choice.
- **Results are noisy.** Several conc.-16 rows show *higher* throughput than conc. 8, and an earlier MySQL run measured `auth/me` at a p50 of 30 ms. Treat the figures as indicative.
- **Before the sign-in fix,** the MySQL runs had 5 of 60 and 4 of 64 sign-ins return 409 (deadlocks). Also, 1 of 600 employee-list requests at conc. 16 ended in a client-side `RemoteProtocolError`, which did not recur.
- **Not tested:**
  - multiple workers or hosts;
  - realistic organization sizes;
  - long soak runs;
  - write-heavy mixes, such as payroll calculation for a large organization, model training or large exports.

## Browser workflow coverage (Edge 155, headless; the production build served with the CSP)

`qa/browser_tests.py` uses trusted mouse and keyboard input (CDP `Input.*`). `<select>`, date and file inputs are set through the DOM and declared as such. API calls inside a test are labelled "API setup" or "API check" and are not counted as browser evidence. The latest run passed **17 of 17**, with **0 CSP violations, 0 API 5xx responses and 0 uncaught page errors** (`evidence/subprompt32/browser/`).

| Required workflow | Tests |
|---|---|
| 1. Login, invalid credentials, logout, expired session | t01, t02 (the logged-out token gets 401: API check), t03 (an ADMIN ends the sessions, and the UI returns to sign-in with the notice) |
| 2. Role navigation and forbidden access | t04: navigation for all 4 roles, the "Not available for your role" page, and 403 from the API |
| 3. Employee management | t05: create through the form, record verified |
| 4. Attendance and leave | t06 check-in with a shift; t07 leave request by an employee, approval by a manager |
| 5. Payroll | t08: new period, calculate, open a record, approve |
| 6. Dataset upload and validation | t09: upload a synthetic CSV, validate |
| 7. AI model status, including no active model | t09 "No usable model"; t10 "Active model available" after API setup |
| 8. Risk alerts and interventions | t11: acknowledge an alert, then "Plan intervention", then create |
| 9. Performance reviews | t12: a manager opens a review |
| 10. Search, notifications, reports, audit logs | t13 search and notifications; t14 CSV report download; t15 audit log |
| Extra: password change | t16: a wrong current password is refused without sign-out; the change ends the session; sign in with the new password |
| Extra: mobile 390 px | t17: navigation drawer; 11 pages without horizontal page overflow |

**Defect found by the browser tests and fixed:** `EmployeePicker` crashed the "New review" and "New intervention" forms as soon as employees loaded. Its `valueOf` prop default never applied, because every props object inherits `Object.prototype.valueOf`. The prop was renamed (`optionValue`), and a frontend test now rejects destructured props that shadow `Object.prototype`. Request-level tests could not have caught this.

**Not covered:** Firefox and Safari; real phones; keyboard-only and screen-reader passes; visual regression.

**Manual checklist for a real phone** (staging):
1. Sign in.
2. Open the navigation drawer.
3. Request leave.
4. Check in.
5. Open a payslip.
6. Sign out.

At each step, check for no horizontal scrolling and readable tables (they scroll inside their card).

## Security status

### Implemented in Sub-prompt 32

- **Content-Security-Policy:**
  - **Frontend** (`frontend/vite.config.js`, `CONTENT_SECURITY_POLICY`; sent by `vite preview`):

    ```
    default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self';
    connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
    ```

    The production web server **must send the same header**, adding the API origin to `connect-src` when `VITE_API_BASE_URL` points elsewhere. The full browser run under this policy recorded 0 violations.
  - **API responses** (`/api/*`) carry `default-src 'none'; frame-ancestors 'none'`. Interactive docs are excluded (off in staging and production anyway).
- **Self-service password change** (`POST /api/auth/change-password` and a header dialog):
  - it requires the current password;
  - wrong attempts are rate-limited per account (`AUTH_LOGIN_MAX_FAILURES`), counted before any bcrypt work, and return 403 (never 401);
  - the new password follows the registration rules and must differ;
  - the update is conditional on the verified hash;
  - `token_version` is bumped, ending every session;
  - audit rows `auth.password_changed` and `auth.password_change_failed` hold no secrets;
  - validation errors never echo passwords.

  Tests: `test_production_hardening.py`, browser t16.
- **Database error mapping** (see above) and **`GET /api/health/ready`** (see below).
- **AI activation-time checks** (see [AI_MODEL_VALIDATION.md](AI_MODEL_VALIDATION.md)).

### Reviewed and unchanged (already adequate, with tests)

- **Token revocation and session versions:** last-admin protection; the deactivation and role-change effects.
- **Uploads:**
  - the size limit is enforced before parsing and again while reading;
  - files get server-generated names, and stored files are checksum-verified;
  - symlinked or out-of-directory paths are refused on read (`dataset_storage.read_verified`). The symlink test is skipped on this OS account.
- **Model artifacts:** loaded only after the SHA-256 check.
- **Error messages:**
  - validation errors never echo input;
  - database errors log the type or MySQL error number only;
  - configuration errors name fields, never values.
- **Frontend:** no `dangerouslySetInnerHTML`, `innerHTML` or `eval`; React escapes all output.

### Rate limiting: limitations and decision

- **Scope:** limits are in-process. Each worker counts separately, and counts reset on restart.
- **Failure handling:** the limiter fails open on internal errors, which are logged. That is acceptable because it cannot fail for external reasons: it holds no I/O. Revocation and password checks fail closed, because they need the database.
- **Shared limiter:**
  - **Evaluated:** a database-table limiter, which would work across workers without a new dependency but adds a write to every sign-in attempt; or Redis, which would be a new, unconfigured dependency.
  - **Not implemented.**
  - **Decision for staging:** one worker.
  - **Before multi-worker production:** implement a shared store (a separate, reviewed change).
- **Proxies:** behind a proxy, start uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy address>`; never `*`. The proxy must *overwrite* `X-Forwarded-For`, not append a client-supplied value. Otherwise every user shares the proxy's budget, or clients can choose their own key.

### Unresolved security risks

1. Rate limits are per process (above). There is no global API rate limit; apply one at the proxy.
2. No MFA and no "forgot password" reset (BLOCKED: needs an approved design and a configured, tested e-mail provider). Accounts are recovered by an ADMIN, by deactivation and reactivation with an out-of-band procedure. The first ADMIN is a manual database step.
3. Tokens live in `sessionStorage`. CSP reduces, but does not remove, XSS token exposure. Moving to `HttpOnly` cookies would change the authentication architecture and need a migration plan with CSRF protection. Not done.
4. No external penetration test or security review.
5. Managers see phone numbers and `user_id` of their department's employees through the API (unchanged; see SECURITY_RBAC.md).

## Health, readiness and operations

### Health versus readiness

| Endpoint | Purpose | Database | Use |
|---|---|---|---|
| `GET /api/health` | Liveness: the process answers | No | Process supervisor or container liveness probe |
| `GET /api/health/db` | Database reachable | `SELECT 1` | Diagnostics |
| `GET /api/health/ready` | **Readiness**: the database is reachable **and** `alembic_version` equals the head shipped with this code | Yes | Load-balancer readiness and post-deploy checks. Returns 503 `{"status":"not_ready","database":…,"schema":"not_migrated"/"revision_mismatch"/"unknown"}` until it is safe to take traffic. It reveals no revision ids, hosts or error text |

### Logging and redaction

**Current behavior:**
- Application logs carry exception types, MySQL error numbers and route paths. They never carry passwords, tokens, submitted e-mails or query values.
- The engine hides SQL parameters (`hide_parameters=True`).

**Recommended for deployment:**
- Ship uvicorn and application logs to a central store with access control and a retention period.
- Disable uvicorn's access log (`--no-access-log`), or make sure the proxy's access log drops **query strings**: search terms and filters can contain employee names.
- Use JSON log lines through `--log-config` if the log platform needs structured fields. Not implemented or verified here.

**Never log** `Authorization` headers, request bodies of `/api/auth/*`, or dataset contents.

### Monitoring and alerts (recommended; nothing is set up)

| Signal | Source | Alert when |
|---|---|---|
| Readiness | `/api/health/ready` | Not 200 for 2 minutes |
| Errors | 5xx rate from proxy logs | Above 1% for 5 minutes |
| Database | "Database unavailable" (503) | Any burst |
| Concurrency | "Transaction conflict … MySQL error 1213/1205" log lines | Sustained growth (lock contention) |
| Abuse | 429 responses; `auth.login_failed` and `auth.password_change_failed` audit rows | Bursts per address or account |
| Accounts and permissions | `user.role_changed` and `user.deactivated` audit rows | Every occurrence (review) |
| AI | `ai.attrition.activation_refused` and `ai.attrition.prediction_unavailable` audit rows | Any refused activation (possible tampering); unavailable bursts |
| Latency | Proxy p95 per route group | Above the agreed SLO. **SLOs need owner approval**; the load figures above are not SLOs |
| Resources | Host CPU, memory and disk; MySQL connections (the pool is 5 + 5 overflow per worker) | Sustained above 80% |

### Backup and restore (BLOCKED: no infrastructure; objectives need owner approval)

- **What to back up:**
  - the MySQL database;
  - `ML_ARTIFACT_DIR`, holding the model files referenced by SHA-256 in the database;
  - `ML_DATASET_DIR`, holding registered datasets referenced by SHA-256.

  The three must be backed up together, or the restored database will reference missing or different files. Integrity checks then refuse them (safe, but unusable).
- **How:**
  - `mysqldump --single-transaction --routines --triggers --set-gtid-purged=OFF <db>`, or managed snapshots with point-in-time recovery (binary logs);
  - file-system snapshots of the two directories, taken at the same time.
- **Objectives:** RPO and RTO **must be set and approved by the system owner**. Proposed starting point for discussion only: RPO ≤ 24 h (daily dumps plus binary logs), RTO ≤ 4 h.
- **Restore test (required before production):**
  1. Restore into a scratch database and directories.
  2. Run `alembic current` (expect the head) and `GET /api/health/ready`.
  3. Sign in as a test account.
  4. Open the model status: an active model must be "available", or the artifacts are out of step.
  5. Record the time taken against the RTO.

### Migration procedure (staging; for production, also within an approved change window)

1. **Preflight:**
   - confirm `DB_HOST`, `DB_PORT` and `DB_NAME` point to the intended database;
   - `GET /api/health/db` returns reachable;
   - check `SELECT @@version, @@binlog_format, @@transaction_isolation` (expect 8.0.x and `ROW`);
   - use a **separate migration account** with DDL rights. The application account needs only `SELECT, INSERT, UPDATE, DELETE`: the whole suite ran with such an account.
2. **Backup:** take one, and verify that it restores.
3. **Review:** run `alembic heads` (expect `5b9e2d7c41a8 (head)`), `alembic current`, and `alembic upgrade head --sql > review.sql`. A second person reviews the SQL.
4. **Apply** in a maintenance window: `alembic upgrade head`. **`5b9e2d7c41a8` rebuilds every table** (one `ALTER TABLE … MODIFY` per table, `ALGORITHM=COPY`), which takes time and blocks writes on large tables. Stop the application first.
5. **Verify:** `alembic current` shows the head; `GET /api/health/ready` returns 200; run the smoke tests.
6. **Recovery:**
   - Prefer **restore from backup** over downgrade. MySQL DDL is not transactional, so a failed migration can stop midway.
   - Downgrades now run cleanly on MySQL 8.0 (tested from head to base and back), but they **drop the tables and data** of the revisions they undo.
   - Downgrading `5b9e2d7c41a8` rounds timestamps to seconds.
   - Downgrading `c4e7a91d3f20` makes logged-out tokens valid again until they expire.

### TLS, reverse proxy, hosts, CORS and production configuration

- **TLS:** terminate it at the proxy, redirect HTTP to HTTPS, and send `Strict-Transport-Security: max-age=31536000` from the proxy (HTTPS only).
- **Static files:** serve `frontend/dist` with an SPA fallback to `index.html`, and send the frontend CSP above plus `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- **API:** proxy `/api` to uvicorn. Allow bodies up to `DATASET_MAX_UPLOAD_BYTES` + 64 KB on `/api/datasets` only. Set timeouts long enough for the synchronous long operations (below).
- **Hosts and origins:** set `ALLOWED_HOSTS` (no `*`) and `CORS_ORIGINS` (explicit origins, `https://` in production). Startup refuses unsafe values in staging and production.
- **Configuration:** use `ENVIRONMENT=staging|production`. Leave `API_DOCS_ENABLED` unset. Take secrets from a secret store (the configuration table follows).

### Process supervision, graceful shutdown and resource limits

- **Supervision:** run uvicorn under a supervisor (a Windows service wrapper, systemd, or a container runtime) with automatic restart and liveness on `/api/health`. No `--reload` outside development. Use **one worker** until rate limits are shared.
- **Graceful shutdown:** stop uvicorn with SIGTERM or the service stop, which lets in-flight requests finish. Every request commits or rolls back as a whole. Rate-limit counters reset on restart.
- **Resource limits:**
  - **Memory:** set a memory limit (about 280 MB observed per worker under the load runs; allow headroom for training and large exports).
  - **Database:** keep `max_connections` above workers × 10 (pool 5 + 5).
  - **Request bodies:** limit sizes at the proxy.
- **Long synchronous operations** run inside the request and occupy a worker thread:
  - model training;
  - dataset validation;
  - very large payroll runs;
  - large XLSX and PDF exports;
  - monitoring and escalation batches.

  Schedule them outside peak hours. Background processing is future work.

### AI model rollback and recovery

See [AI_MODEL_VALIDATION.md](AI_MODEL_VALIDATION.md#rollback-and-recovery):
- retrain from the same dataset version (deterministic), then activate;
- restore artifacts from backup;
- the activation-time checks refuse damaged candidates.

### Incident response and security-event investigation

1. **Contain:**
   - deactivate an affected account with `PATCH /api/users/{id}/status`, which ends its sessions, or end its sessions only with `POST /api/users/{id}/revoke-sessions`;
   - for a suspected secret leak, **rotate `JWT_SECRET_KEY`**, which signs everyone out, and the database credentials.
2. **Investigate:**
   - query the audit log (`GET /api/audit-logs`, ADMIN or HR; export as CSV) by actor, action and time window;
   - authentication and account events, model activations and refusals, exports and payroll actions are all recorded, with no credentials or tokens;
   - correlate with proxy logs by time and client address.
3. **Preserve:** export the relevant audit rows and logs before retention removes them, and keep the database backup from before the incident.
4. **Recover:** restore from backup if data integrity is in doubt, using the restore procedure above. Re-run `/api/health/ready` and the smoke tests.
5. **Notify:** follow the organization's data-breach policy; HR data is personal data. This document cannot set the deadlines.

## Account lifecycle (unchanged since Sub-prompt 31, plus password change)

| Event | Effect |
|---|---|
| Logout | That token is revoked (401 from then on) |
| **Password change (new)** | Every session ends, including the current one; sign in with the new password |
| Admin deactivates an account | Every session ends; sign-in refused (403) until reactivated |
| Admin reactivates | Sign-in works again; old tokens stay invalid |
| Admin changes a role | Every session ends; the new role applies from the next sign-in |
| Admin "revoke sessions" | Every session ends; the account stays active |
| ADMIN/HR employee record set INACTIVE | Access ends at once; sign-in refused while INACTIVE |
| MANAGER/EMPLOYEE employee record set INACTIVE | Self-service access only |

## Rate limits (defaults)

| Endpoint | Limit | Key |
|---|---|---|
| `POST /api/auth/login` | 50 attempts / 15 min | Client address |
| `POST /api/auth/login` | 5 failed attempts / 15 min | Client address + submitted e-mail (hashed) |
| `POST /api/auth/register` | 10 / 15 min | Client address |
| `POST /api/auth/change-password` | 5 wrong current passwords / 15 min | Account |

Over a limit: `429` with `Retry-After`. The response is identical for existing and unknown accounts, and nothing about the submitted e-mail or password is logged.

## Configuration requirements

Placeholders only; never commit real values. Supply them as environment variables from a secret store, or in `backend/.env` (environment variables take precedence). The full list is in `backend/.env.example`.

| Variable | Required | Staging / production guidance |
|---|---|---|
| `ENVIRONMENT` | Yes | `staging` or `production` (enables the strict checks) |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | Yes | The verified target database only |
| `DB_USER`, `DB_PASSWORD` | Yes | Application account with `SELECT, INSERT, UPDATE, DELETE` only; migrations use a separate account |
| `JWT_SECRET_KEY` | Yes | ≥ 32 random characters, unique per environment; rotating it signs everyone out |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Keep short (default 30) |
| `CORS_ORIGINS`, `ALLOWED_HOSTS` | Yes | Explicit values; `https://` origins in production |
| `AUTH_RATE_LIMIT_*`, `AUTH_LOGIN_*`, `AUTH_REGISTER_*` | No | Defaults above; cannot be disabled when deployed |
| `ORG_TIMEZONE` | Yes in practice | IANA zone, e.g. `Asia/Kolkata` |
| `ML_ARTIFACT_DIR`, `ML_DATASET_DIR` | No | Writable by the API account only; back up with the database |
| `ML_ACTIVATION_MIN_ROC_AUC`, `ML_RISK_*_THRESHOLD` | No | Set from the signed acceptance criteria (AI_MODEL_VALIDATION.md) |
| `VITE_API_BASE_URL` (frontend build) | When the API is on another origin | Also add that origin to the CSP `connect-src` |

## Staging smoke-test plan

Use synthetic data and test accounts only, and record each result. `qa/browser_tests.py` automates most of steps 2–7 against a local build.

1. **Health:**
   - `/api/health` returns 200;
   - `/api/health/ready` returns 200 `ready` (503 before migrating);
   - `/docs` returns 404;
   - a wrong `Host` header returns 400;
   - the frontend response carries the CSP header.
2. **Authentication:**
   - register, sign in, then `/auth/me`;
   - log out: the old token gets 401;
   - five wrong passwords give 429;
   - a password change ends every session.
3. **Accounts:**
   - promote, deactivate and reactivate a test user;
   - HR gets 403 on `/api/users`.
4. **Department isolation:** a manager of department A gets 404 for department B's records.
5. **Workflows (UI):**
   - leave request and approval;
   - check-in and check-out;
   - payroll calculate, approve and mark paid;
   - task transitions;
   - a performance review;
   - an intervention from an alert.
6. **Exports:** a CSV and a PDF; the payroll export appears in the audit log.
7. **AI:** "No usable model" is shown, and predictions return 503 `no_active_model`, until a model is deliberately activated.
8. **Audit:** `auth.*` and `user.*` rows exist with no credentials.

## Verification results (newly executed in Sub-prompt 32, 2026-10-27/28)

| Check | Result | Evidence |
|---|---|---|
| Backend full suite, SQLite | **2255 passed, 12 skipped, 0 failed**. Skips: the 11 MySQL-only tests, and the symlink test, which this OS account cannot run | `evidence/subprompt32/tests/backend_full_suite_sqlite.txt` |
| Backend full suite, **MySQL 8.0.46** (disposable, DML-only account) | **2260 passed, 7 skipped, 0 failed**. Skips: 5 shared-connection writer simulations (covered by real multi-connection tests), 1 DDL-based readiness test, and the symlink test | `mysql/05_full_suite_on_mysql.txt` |
| MySQL concurrency suite | 11 passed in **5 of 5 repetitions** (before the fixes: 2 failing) | `mysql/03`, `mysql/04` |
| Migrations on MySQL | All 17 revisions: upgrade, downgrade to base and upgrade again, exit 0; live schema equals the models | `mysql/01`, `02`, `06` |
| Focused: authentication and authorization | 466 passed | `tests/focused_auth_authorization.txt` |
| Focused: database and migrations | 151 passed | `tests/focused_database_migrations.txt` |
| Focused: AI and datasets | 149 passed, 1 skipped (symlink) | `tests/focused_ai_datasets.txt` |
| Focused: UI contract, workflows and no-5xx sweep | 402 passed | `tests/focused_ui_contract.txt` |
| Frontend unit tests | 23 passed (including the new props guard) | `tests/frontend_npm_test.txt` |
| Frontend lint and build | Exit 0 / exit 0 | `tests/frontend_lint.txt`, `frontend_build.txt` |
| Browser workflows (Edge 155) | **17 of 17 passed**; 0 CSP violations, 0 API 5xx, 0 page errors | `browser/browser_results.json`, 21 screenshots |
| Bounded load (SQLite c8; MySQL c8, c16) | 3960 / 3960 / 7864 requests, **0 errors** | `load/*.json` |
| `npm audit` | 0 vulnerabilities | `security/npm_audit.txt` |
| `pip-audit` (53 installed packages) | No known vulnerabilities | `security/pip_audit.txt` |

**Historical results** (Sub-prompts 30 and 31, not re-run as such): 2229 passed and 1 skipped at the end of Sub-prompt 31; the 8-control mutation check of Sub-prompt 31.
