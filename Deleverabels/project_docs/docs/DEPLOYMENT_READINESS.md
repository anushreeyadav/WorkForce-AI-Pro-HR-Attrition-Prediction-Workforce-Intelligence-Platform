# Deployment readiness (Sub-prompt 31)

> Copy of the project document for this submission package. Links to source files were rewritten to point to the copies in `01_Backend_Source_Code/` and `02_Frontend_Source_Code/`; the original in the project is unchanged.

This phase hardened authentication, account lifecycle, configuration and operational documentation, to prepare WorkForce AI Pro for a **controlled staging deployment**. Every result below was observed in the development environment. **No deployment was performed**, and **nothing was run against MySQL**.

## Go / no-go

| Target | Recommendation | Why |
|---|---|---|
| **Staging** (synthetic or approved test data, restricted access) | **Conditional GO** | The security controls are implemented and tested. Before go, the operator must: run the migrations on a fresh staging MySQL after reviewing the SQL; run a single uvicorn worker (or accept per-worker rate limits); put TLS and a correctly configured proxy in front; set strict configuration; and complete the smoke-test plan below. |
| **Production** (real employee data) | **NO-GO** | Nothing has been verified on MySQL or under load. There is no browser end-to-end testing, no shared rate-limit store, no password reset flow and no monitoring or backup automation. The attrition model is not validated. Each is listed under "Remaining risks". |

## Security changes in this phase

1. **Server-side token revocation.**
   - `POST /api/auth/logout` stores the token's id (`jti`, never the token) in the new `revoked_tokens` table. That token gets 401 from then on; other sessions are unaffected.
   - Rows for expired tokens are deleted during later logouts.
2. **Session versions.**
   - Each access token carries `ver`, the account's `users.token_version`.
   - Deactivation, role changes and `POST /api/users/{id}/revoke-sessions` increment it, so every earlier token gets 401.
   - Reactivating an account does not revive old tokens.
   - Tokens issued before this change (no `ver`) are read as version 0, the default, so upgrading signs nobody out.
3. **One authentication query per request** checks that the account is active, the session version matches, the token is not revoked, and (for ADMIN/HR) the linked employee record is not INACTIVE. If the database cannot answer, the request gets 503: revocation **fails closed**.
4. **Ended employment for ADMIN/HR.**
   - An ADMIN or HR login whose employee record is INACTIVE is refused at sign-in (403, audited as `employment_inactive`) and on every request (401).
   - Unchanged policy: departed MANAGER and EMPLOYEE logins keep self-service access only (Sub-prompt 26).
5. **Account administration API, ADMIN only:**
   - `GET /api/users` and `GET /api/users/{id}`;
   - `PATCH /api/users/{id}/status` (deactivate or reactivate);
   - `PATCH /api/users/{id}/role`;
   - `POST /api/users/{id}/revoke-sessions`.

   Nobody changes their own status or role, so the acting administrator always remains an active ADMIN (a last-admin guard is also in place as defense in depth). Every change is audited (`user.deactivated`, `user.reactivated`, `user.role_changed`, `user.sessions_revoked`) with roles and statuses only: never passwords, hashes, tokens or token ids. HR, MANAGER and EMPLOYEE get 403.
6. **Authentication rate limiting** (described in the next section).
7. **Configuration hardening:**
   - `ENVIRONMENT` is now validated (`development` | `test` | `staging` | `production`).
   - In staging and production the server refuses to start with wildcard `CORS_ORIGINS`, wildcard `ALLOWED_HOSTS`, or rate limits disabled. Production also requires `https://` CORS origins.
   - Interactive docs and `/openapi.json` are off in staging and production unless `API_DOCS_ENABLED=true`.
   - Unknown `Host` headers get 400 when `ALLOWED_HOSTS` is set.
   - Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; API responses also carry `Cache-Control: no-store`.
   - Configuration errors name fields only, never values.

## Rate-limiting behaviour and limitations

| Endpoint | Limit (defaults) | Key |
|---|---|---|
| `POST /api/auth/login` | 50 attempts per 15 min | client address |
| `POST /api/auth/login` | 5 failed attempts per 15 min | client address + submitted email (hashed) |
| `POST /api/auth/register` | 10 per 15 min | client address |

- **Over a limit:** `429 {"detail": "Too many attempts. Try again later."}` with `Retry-After` (seconds). The response is identical for existing and unknown accounts, and limits are checked before any password work, so they reveal nothing about accounts.
- **Counting:**
  - A successful sign-in resets that account key's failure count.
  - Inactive-account refusals do not count, since they require the correct password.
  - Counts expire with the sliding window.
- **What is recorded:** nothing about the submitted email or password goes to logs or audit records; the 429 log line names the endpoint only.
- **Failure behaviour:** the limiter **fails open**. An internal error lets the request through and logs the error type, so a fault cannot lock every user out. Revocation, by contrast, fails closed.
- **Limitations:**
  - **Process-local state.** Each worker process counts separately, and counts reset on restart. With N workers the effective limit is up to N times higher. Multi-worker or multi-host deployments need a shared store, such as a database table or Redis, implemented as a separate change.
  - **Client address.** The limiter uses the peer address the server sees. Behind a reverse proxy, start uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy address>`. Otherwise every user shares the proxy's budget.
  - **Distributed guessing.** Guessing against one account from many addresses is slowed only per address. Account lockout was not added, because it would let anyone lock out a victim.
  - **Memory is bounded** at 20,000 tracked keys, dropping the stalest first.

## Account lifecycle

| Event | Effect |
|---|---|
| Logout | That token is revoked (401 from then on) |
| Admin deactivates an account | Every session ends; sign-in refused (403) until reactivated |
| Admin reactivates | Sign-in works again; old tokens stay invalid |
| Admin changes a role | Every session ends; the new role applies from the next sign-in |
| Admin "revoke sessions" | Every session ends; the account stays active (also allowed on one's own account) |
| ADMIN/HR employee record set INACTIVE | Access ends at once and sign-in is refused; restored if the record becomes ACTIVE or ON_LEAVE again |
| MANAGER/EMPLOYEE employee record set INACTIVE | Self-service access only (department scope ends) |

Not available: password reset or change, account deletion (accounts are kept for audit history), and refresh tokens.

## Configuration requirements

Placeholders only; never commit real values. Set them in `backend/.env` or as environment variables (environment variables take precedence). The full list is in `backend/.env.example`.

| Variable | Required | Staging / production guidance |
|---|---|---|
| `ENVIRONMENT` | yes for deployments | `staging` or `production` (enables the strict checks) |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | yes | The verified target database only |
| `DB_USER`, `DB_PASSWORD` | yes | A dedicated least-privilege application user, never root. Migrations may use a separate, more privileged user |
| `JWT_SECRET_KEY` | yes | ≥ 32 random characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Unique per environment; rotating it signs everyone out |
| `JWT_ALGORITHM` | no | `HS256` (default), `HS384` or `HS512` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | Keep short (default 30) |
| `CORS_ORIGINS` | yes | e.g. `["https://hr.example.com"]` (no `*`; https in production) |
| `ALLOWED_HOSTS` | yes | e.g. `["api.example.com"]` (no `*`) |
| `API_DOCS_ENABLED` | no | Leave unset (off) unless docs are needed |
| `AUTH_RATE_LIMIT_*`, `AUTH_LOGIN_*`, `AUTH_REGISTER_*` | no | Defaults above; cannot be disabled when deployed |
| `ORG_TIMEZONE` | yes in practice | The organization's IANA zone, e.g. `Asia/Kolkata` |
| `ML_ARTIFACT_DIR`, `ML_DATASET_DIR` | no | Directories writable by the API user only. Back them up with the database (datasets and models are referenced by hash) |
| `VITE_API_BASE_URL` (frontend build) | when the API is on another origin | e.g. `https://api.example.com/api`. Compiled into the bundle, so never put secrets there |

Configuration that is no longer a concern:
- **Debug mode:** FastAPI's debug is off.
- **Cookies:** none are used; tokens are bearer tokens kept in the browser's `sessionStorage`.
- **Logs:** they hold exception type names only. The database engine hides query parameters (`hide_parameters=True`).

## Runtime and installation

**Supported versions:** Python 3.14 (backend virtualenv); Node.js 24 with npm (frontend build only); MySQL 8.0.

```powershell
# Backend (on the server)
cd backend
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# provide configuration (environment or backend\.env), then:
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
# no --reload outside development; one worker unless per-worker rate limits are accepted

# Frontend (build machine)
cd frontend
npm ci
npm run lint; npm test; npm run build    # static files in frontend\dist\
```

- **Serving:**
  - Serve `frontend/dist` as static files over HTTPS, with an SPA fallback to `index.html`, and proxy `/api` to uvicorn (or set `VITE_API_BASE_URL`).
  - Terminate TLS at the proxy and redirect HTTP to HTTPS.
  - Allow request bodies up to `DATASET_MAX_UPLOAD_BYTES` + 64 KB for dataset uploads.
- **Health checks:** `GET /api/health` (process only, no database) and `GET /api/health/db` (database reachable, or 503).
- **Startup:**
  - The application **never creates tables or applies migrations at startup**.
  - It refuses to start with missing or unsafe configuration, naming fields but never values.
  - The database connection is opened lazily.
- **Shutdown:**
  - Stop uvicorn gracefully, letting in-flight requests finish.
  - Every request commits or rolls back as a whole, so an interrupted request leaves no partial writes.
  - Rate-limit counters are lost on restart.
- **Logging and monitoring:**
  - Ship uvicorn and application logs.
  - Alert on 5xx rates, 503 `Database unavailable`, repeated 429s, and `auth.login_failed` bursts in the audit log.
  - Monitor `/api/health/db`.
- **Long synchronous operations**, which run inside the request (a single worker blocks while they run):
  - model training;
  - dataset validation;
  - payroll calculation for very large organizations;
  - large XLSX/PDF exports (bounded by report row limits);
  - risk-monitoring and escalation runs (bounded batches).

  Set proxy timeouts accordingly. Background processing is future work.

## Database and migration precautions

- **Driver:** SQLAlchemy with PyMySQL (`mysql+pymysql`, `charset=utf8mb4`).
- **Engine settings:** pool of 5 + 5 overflow, `pool_pre_ping`, recycle after 30 min, 5 s connect timeout.
- **Migration chain:** 16 revisions, linear. The head is now **`c4e7a91d3f20`** (token revocation), which follows `a0bd1c34f49c`.
- **The new revision** adds the `revoked_tokens` table and the column `users.token_version INTEGER NOT NULL DEFAULT 0`. It is additive and safe for existing rows.
- **Downgrades are destructive.** Every revision's downgrade drops what it created: tables and their data, or columns. The rules-v2 revision refuses to downgrade while rules-v2 data exists. Downgrading `c4e7a91d3f20` makes logged-out tokens usable again until they expire.
- **Verification:** all revisions were checked only on disposable SQLite and as rendered MySQL SQL. **None has been applied to any MySQL database.**

**Operator procedure (staging), with no step skipped:**
1. Confirm the target: `DB_HOST`, `DB_PORT` and `DB_NAME` point to the staging database, never production by accident. `GET /api/health/db` should return `reachable`.
2. Take a backup (`mysqldump --single-transaction --routines --triggers <db> > backup.sql`) and test that it restores into a scratch database.
3. Run `alembic heads` (expect exactly `c4e7a91d3f20 (head)`) and `alembic current`.
4. Run `alembic upgrade head --sql > review.sql` and have a second person review it.
5. Apply in a maintenance window with `alembic upgrade head`; Alembic prints the target first, so check it. Confirm with `alembic current`.
6. Provision the first ADMIN by the reviewed manual step in the root README. Assign other roles through `PATCH /api/users/{id}/role`.

## Staging smoke-test plan

Run with synthetic data and test accounts only, and record each result.

1. **Health:** `GET /api/health` returns 200. `GET /api/health/db` returns `reachable`. `/docs` returns 404 unless docs were enabled. A wrong `Host` header returns 400.
2. **Authentication:**
   - Register an account; it gets the EMPLOYEE role.
   - Log in, then call `GET /api/auth/me`.
   - Log out; the same token now gets 401.
   - Five wrong passwords give 401 ×5, then 429 with `Retry-After`.
3. **Account administration (ADMIN):**
   - Promote a test user to MANAGER; their old token gets 401 and the new sign-in works.
   - Deactivate them; they get 401 and sign-in returns 403.
   - Reactivate them; sign-in works again.
   - HR calling `/api/users` gets 403.
4. **Department isolation:** a manager of department A gets 404 for department B's employees, leave requests, analytics and reports. An employee sees only their own records.
5. **Core HR workflows** (through the UI):
   - leave request and approval;
   - attendance check-in and check-out;
   - a payroll period through calculate, approve and mark paid, then the employee's payslip;
   - task creation and transitions;
   - a review cycle through to finalization;
   - an intervention created from a risk alert, then completed.
6. **Exports:** a CSV and a PDF report; the payroll export appears in the audit log.
7. **AI status:** the Datasets page shows "No usable model", and the prediction endpoint returns 503 `no_active_model` until a model is deliberately trained and activated.
8. **Audit:** `auth.*` and `user.*` rows exist with no credentials, tokens or submitted emails.

## AI model readiness

- **Current state:** no trained model exists in the repository. Prediction endpoints return 503 with `no_active_model`, `schema_mismatch` or `artifact_error`, and the UI shows "No usable model".
- **Already tested** (`test_attrition_api.py`, `test_attrition_ml.py`):
  - tampered or missing artifacts make the model unavailable;
  - incompatible schemas are rejected;
  - a model failing the quality gate cannot be activated;
  - training never activates a model; activation is an explicit ADMIN action.
- **Required before any production use:**
  - Train only on an approved, lawfully obtained dataset that has passed validation, never on unapproved real employee data.
  - Evaluate on held-out data: ROC-AUC (activation gate `ML_ACTIVATION_MIN_ROC_AUC`), precision and recall at the configured thresholds, and **calibration**.
  - Run a **bias and fairness review** across relevant groups, with legal and HR sign-off.
  - Set up **drift monitoring** and a retraining policy.
  - Document intended use: review support only, never automated decisions.
- **No evaluation metrics exist yet, and none are claimed.**

## Verification results (this phase)

| Check | Result |
|---|---|
| Full backend suite | 2229 passed, 1 skipped, 0 failed |
| New security tests (`test_account_security.py`) | 34 passed |
| Authorization matrix (5 new ADMIN-only routes reviewed into the matrix) | 323 passed |
| Query scaling | 3 passed |
| Migrations (new revision test included) | 19 passed; single head `c4e7a91d3f20` |
| Server-error sweep (now including `/users`) | 234 passed |
| Frontend unit tests | 22 passed |
| Frontend lint / build | 0 warnings, 0 errors / succeeds |
| Mutation check | 8 security controls disabled one at a time; every one caught by the tests; files restored byte-identical |

**Pre-existing flaky test fixed:** `test_recommendation_engine_api.py::test_access_boundaries` asserted that the digits `987` (a sentinel feature value) never appear in the raw response text. Timestamps carry microseconds, so "987" occasionally appears by chance; it failed once in a full run and passed in 40 repeated runs. The check now compares JSON values, so a leaked feature value is still caught, and names and score strings are still matched as text.

**Updated test expectations** (deliberate changes of contract, not weakening): the token claims now include `ver`; logout now revokes the token (the old test asserted it stayed valid); the reviewed route matrix and model inventories include the 5 `/users` routes, `revoked_tokens` and `users.token_version`; the rule "no API assigns roles" is now "only the ADMIN-only `PATCH /api/users/{user_id}/role` does"; and the migration tests name the new head and its one ALTER statement.

**Baseline note:** the suite run started at the beginning of this phase overlapped my first code edits, and reported 3 failures. It is not a clean baseline. The last clean baseline is Sub-prompt 30's final run: 2177 passed, 1 skipped.

**Not run here:**
- MySQL integration and applying the migration;
- multi-worker rate-limit behaviour;
- TLS and proxy configuration;
- browser end-to-end tests;
- load tests;
- the dataset symlink-escape test (this OS account may not create symlinks).

## Remaining risks

- **No verification on MySQL:** migrations, queries, locking (`SELECT … FOR UPDATE` for the last-admin guard), and performance.
- **Rate limiting** is per-process and only on sign-in and registration. There is no global API rate limit.
- **Missing account features:** no password reset or change flow, no MFA, no refresh tokens.
- **First ADMIN** provisioning is a manual database step, documented in the root README.
- **Tokens in `sessionStorage`** are readable by any script on the page, so an XSS flaw would expose them. There is no Content-Security-Policy yet; add one at the static-file server.
- **Long synchronous operations,** with no background workers.
- **No automated backups, monitoring or alerting** (operator responsibility).
- **Attrition model:** unvalidated, with no trained model.
- **Browser verification** is limited to the smoke test from Sub-prompt 30.
