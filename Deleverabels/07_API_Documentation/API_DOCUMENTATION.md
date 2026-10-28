# API documentation: WorkForce AI Pro

This is the reference for the WorkForce AI Pro HTTP API. It is generated from, and checked against, the actual application:

| File | What it is |
|---|---|
| [`openapi.json`](openapi.json) | The OpenAPI 3 schema produced by `app.openapi()`, with placeholder settings and no database. 147 paths, 172 operations, every request and response schema |
| [`endpoint_inventory.md`](endpoint_inventory.md) | All 172 operations with module, method, path, required roles (read from each route's `require_roles` dependency), summary, request-body type and success code |
| [`examples/`](examples) | **Live** request/response pairs captured from the running application with synthetic data. Access tokens are replaced by `<redacted>` and the password by `<demo password>` |

Full role and scope rules, including the per-route inventory with roles: [`../project_docs/docs/SECURITY_RBAC.md`](../project_docs/docs/SECURITY_RBAC.md).

## Opening the interactive docs (Swagger UI)

1. Start the backend locally in development: `cd backend` then `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload`. This needs a configured `backend/.env`; see the root README.
2. Open `http://127.0.0.1:8000/docs` (Swagger UI) or `http://127.0.0.1:8000/redoc`.
3. To call protected endpoints, use **Authorize** with the `access_token` returned by `POST /api/auth/login`.

`/docs`, `/redoc` and `/openapi.json` are **disabled in staging and production** unless `API_DOCS_ENABLED=true`. Offline, `openapi.json` here can be loaded into any OpenAPI viewer, for example editor.swagger.io via "File → Import file".

## Authentication

| Step | Request | Result |
|---|---|---|
| Register (public) | `POST /api/auth/register` `{"full_name", "email", "password"}` | Always an **EMPLOYEE** account; any other field (such as `role`) gives 422 |
| Sign in (public) | `POST /api/auth/login` `{"email", "password"}` | `{"access_token", "token_type": "bearer", "expires_in", "user"}` ([example](examples/login_success.json)) |
| Use the token | `Authorization: Bearer <access_token>` on every protected request | [`GET /api/auth/me`](examples/me.json) |
| Sign out | `POST /api/auth/logout` | The token is **revoked on the server**; later use gets 401 |

How tokens work:
- **Format:** HMAC-signed JWTs (HS256 by default) lasting `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30).
- **Claims:** `sub`, `iss`, `iat`, `nbf`, `exp`, `jti`, `type`, plus `ver`, the account's session version. They contain no role, email or password.
- **Checks on every request:** the server re-checks the account: active, session version current, token not revoked, and employment not ended for ADMIN/HR.
- **Rate limits:** sign-in and registration are rate-limited. Over the limit you get `429 {"detail": "Too many attempts. Try again later."}` with `Retry-After`.

## Roles

There are four roles: ADMIN, HR, MANAGER and EMPLOYEE. Each route's required roles are in [`endpoint_inventory.md`](endpoint_inventory.md).

Authorization has two layers, both enforced on the server:
1. **Role** per route. A wrong role gets 403 `{"detail": "You do not have permission to perform this action"}` ([example](examples/role_forbidden_403.json)).
2. **Scope and record.** Managers are limited to their own department and employees to their own records.
   - Out-of-scope records return **404**, like missing ones ([example](examples/out_of_scope_404.json)).
   - Visible records the caller may not change return **403**.

Account administration (`/api/users…`) is ADMIN only ([example](examples/users_admin_list.json)); HR gets 403 ([example](examples/users_forbidden_for_hr.json)).

## Error conventions

| Status | When | Body |
|---|---|---|
| 401 | Missing, invalid, expired or revoked token; deactivated account | `{"detail": "Not authenticated"}` or `{"detail": "Invalid or expired token"}` plus `WWW-Authenticate: Bearer` ([example](examples/missing_token_401.json)) |
| 403 | Wrong role, or a visible record the caller may not change | `{"detail": "<reason>"}` |
| 404 | Missing **or** out-of-scope record | `{"detail": "<Thing> not found"}` |
| 409 | State conflict or optimistic-concurrency conflict (`expected_version` mismatch) | `{"detail": "<reason>"}` |
| 410 | Expired activity-feed cursor | `{"detail": "<reason>"}` |
| 413 / 415 | Dataset upload too large / not a `.csv` | `{"detail": "<reason>"}` |
| 422 | Validation failure, **including unknown query or body fields** | `{"detail": [{"type", "loc", "msg"}]}`; submitted values are never echoed ([example](examples/validation_error_422.json), [unknown filter](examples/unknown_filter_422.json)) |
| 429 | Authentication rate limit | `{"detail": "Too many attempts. Try again later."}` plus `Retry-After` |
| 503 | Database unavailable, or no usable attrition model | `{"detail": "Database unavailable"}`, or `{"detail": {"code", "message", "hint"}}` for the model |

## Pagination and filters

- **Pagination:** list endpoints take `page` (≥ 1) and `page_size` (1–100, default 20) and return the envelope `{"items": [...], "total", "page", "page_size", "pages"}` ([example](examples/employee_list_paginated.json)).
- **Filters** are explicit query parameters per endpoint (see `openapi.json`). Unknown parameters are rejected with 422, so a misspelled filter never silently returns unfiltered data.
- **Search:** text search (`q`) is case-insensitive substring matching with literal `%` and `_`. Global search is `GET /api/search?q=…&types=…&limit_per_type=…` ([example](examples/search.json)).

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /api/health` | Process is up (no database needed) ([example](examples/health.json)) |
| `GET /api/health/db` | `{"status": "ok", "database": "reachable"}`, or 503 if the database is unreachable |

## Reports and exports

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /api/reports/catalog` | any signed-in user | Lists only the reports the caller may run ([manager example](examples/report_catalog_manager.json)) |
| `GET /api/reports/{employees, attendance, leave, workforce, activity, attrition}` | ADMIN, HR, MANAGER (manager: own department) | `format=csv|xlsx|pdf`, `department_id`, date range where applicable |
| `GET /api/reports/payroll` | ADMIN, HR | `period_id` required; every export is audited |
| `GET /api/audit-logs/export` | ADMIN, HR | Same filters as the audit query |

- **Response headers:** `Content-Disposition: attachment; filename="…"`, `X-Report-Rows`, `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- **Size limits:** more than `REPORT_MAX_ROWS` (5,000) rows, or 1,000 for PDF, gives **422**, never a truncated file.
- **Spreadsheet safety:** CSV and XLSX exports neutralize spreadsheet formula injection.
- **Samples:** genuine exports are in [`../05_Analytics_Reports/`](../05_Analytics_Reports).

## AI endpoints

See [`../03_AI_Prediction_APIs/AI_API_GUIDE.md`](../03_AI_Prediction_APIs/AI_API_GUIDE.md).

## What is not covered

- **Endpoints:** this documentation describes only endpoints that exist. None were added for it.
- **Database:** responses were captured on the disposable SQLite demo database, not MySQL.
