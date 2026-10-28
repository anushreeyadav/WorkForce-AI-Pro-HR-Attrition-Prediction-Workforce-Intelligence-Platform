# Database

MySQL 8.0 setup for WorkForce AI Pro. Schema and seed scripts will live in this folder.

The application **never creates or drops tables on startup**. Schema changes are applied explicitly
with Alembic migrations in `backend/alembic/versions/`.

## Models

Defined in `backend/app/models/`. All timestamps are stored as UTC (`DATETIME`, naive UTC) and returned
as timezone-aware UTC datetimes. Naive datetimes are rejected on write.

| Table         | Purpose | Key constraints |
|---------------|---------|-----------------|
| `users`       | Login accounts: name, email, bcrypt `password_hash`, `role` (`ADMIN`/`HR`/`MANAGER`/`EMPLOYEE`), `is_active`, `last_login_at` | `uq_users_email`; email is lowercased before saving; index on `role` |
| `departments` | Organisational units: name, description, `is_active` | `uq_departments_name` |
| `employees`   | HR record: `employee_code`, optional `user_id`, name, `work_email`, `department_id`, designation, `date_of_joining`, `status` (`ACTIVE`/`INACTIVE`/`ON_LEAVE`), phone | Unique `employee_code`, `work_email` and `user_id` (one account ↔ at most one employee). FK `user_id → users.id ON DELETE SET NULL`; FK `department_id → departments.id ON DELETE RESTRICT`. Indexes on `department_id`, `status` |
| `shifts`      | Working windows: `name`, `start_time`/`end_time` (wall-clock in `ORG_TIMEZONE`; end earlier than start = overnight), `break_minutes`, `overtime_threshold_minutes`, `is_active` | `uq_shifts_name`. CHECKs: start ≠ end, break 0–240, threshold 1–1440 |
| `attendance_records` | One row per employee per work date: `employee_id`, optional `shift_id`, `work_date` (org-local), UTC `check_in`/`check_out`, `break_minutes`, server-computed `work_minutes`/`overtime_minutes`, `status` (`PRESENT`/`LATE`/`HALF_DAY`/`ABSENT`/`ON_LEAVE`), `note` | `uq_attendance_records_employee_id_work_date`. FKs to `employees` and `shifts` (`ON DELETE RESTRICT`). CHECKs: check-out after check-in (and only with a check-in), non-negative minutes. Indexes on `work_date`, `status`, `shift_id` |
| `leave_types` | Kinds of leave: `name`, stable `code`, `description`, `is_paid`, `is_active` | Unique `name` and `code` |
| `leave_policies` | Allowance rules per leave type, organization-wide (`department_id` NULL) or per department: `annual_allowance_days`, `max_consecutive_days`, `carry_forward_days`, `count_weekends`, `effective_from`/`effective_to`, `is_active` | CHECKs on ranges and `effective_to >= effective_from`. Index (`leave_type_id`, `department_id`). Overlapping active policies for the same type and scope are rejected by the API |
| `leave_requests` | `employee_id`, `leave_type_id`, applied `policy_id`, `start_date`/`end_date`, server-computed `working_days`, `reason`, `status` (`PENDING`/`APPROVED`/`REJECTED`/`CANCELLED`), reviewer and canceller ids, timestamps and comments | CHECKs: `end_date >= start_date`, `working_days` 1–366. Indexes on (`employee_id`, `start_date`, `end_date`), `status`, `leave_type_id` |
| `leave_balances` | Per employee, leave type and calendar year: `allocated_days`, `carried_forward_days`, `used_days`, `pending_days` | `uq_leave_balances_employee_id_leave_type_id_year`. CHECKs: all ≥ 0 and `used + pending <= allocated + carried` (no overspend) |
| `notifications` | Minimal in-app messages: `user_id`, `category`, `title`, `message`, `entity_type`/`entity_id`, `is_read`, `read_at` | FK `user_id → users.id ON DELETE CASCADE`. Index (`user_id`, `is_read`) |
| `salary_components` | Catalog of earning / deduction / tax components: `code`, `name`, `kind` (`EARNING`/`DEDUCTION`/`TAX`), `calculation_method` (`FIXED`/`PERCENT_OF_BASE`/`PERCENT_OF_GROSS`), `display_order`, `is_active` | Unique `code` and `name`. CHECK: an EARNING cannot be `PERCENT_OF_GROSS` |
| `payroll_profiles` | Salary structure per employee and effective date range: `base_salary` NUMERIC(12,2) per month, `pay_frequency` (`MONTHLY`), `is_active` | CHECKs: base > 0, valid date range. Overlapping active profiles are rejected by the API |
| `payroll_profile_components` | A component's value on one profile: `amount` (FIXED) **or** `percentage` (0–100) | Unique (`profile_id`, `component_id`); CHECKs: exactly one of amount/percentage, ranges |
| `payroll_periods` | Calendar months: `year`, `month`, `start_date`, `end_date` | `uq_payroll_periods_year_month` |
| `payroll_records` | One per employee per period: snapshot `base_salary`, `gross_earnings`, `total_deductions`, `net_pay` (NUMERIC(12,2)), `status` (`CALCULATED`/`APPROVED`/`PAID`), `warnings` JSON, calculated/approved/paid by + at, `payment_reference` | `uq_payroll_records_period_id_employee_id`. CHECKs: non-negative amounts, net = gross − deductions (within 0.005) |
| `payroll_line_items` | Itemized lines with the inputs used (`rate`, `basis`, `quantity`) and `amount` | Unique (`record_id`, `sequence`); CHECKs: amount ≥ 0, rate 0–100 |
| `attrition_model_versions` | Model registry: `model_version`, `feature_schema_version`, `status` (`CANDIDATE`/`ACTIVE`/`REJECTED`/`RETIRED`), training time and user, `dataset_fingerprint`, `artifact_sha256`, `training_rows`, `evaluation` JSON, activation time and user | Unique `model_version`; index on `status` |
| `attrition_predictions` | Prediction history: `employee_id`, `model_version_id`, `feature_schema_version`, `risk_score` NUMERIC(5,2), `risk_category`, the six schema `features` (JSON), short `explanation`, `predicted_at`, `requested_by_user_id`, `batch_id` | CHECK `risk_score` 0–100; indexes (`employee_id`, `predicted_at`), `risk_category`, `model_version_id`, `batch_id`. No automatic retention purge yet; define a retention policy before production |
| `datasets` | Registered datasets: `display_name`, `purpose` (`ATTRITION_TRAINING`), creator, `archived_at`/by | — |
| `dataset_versions` | Immutable file versions: `version_number`, server-generated `storage_key`, `sha256`, `size_bytes`, `row_count`, `column_count`, `status` (`UPLOADED`/`VALIDATING`/`VALIDATED`/`REJECTED`/`ARCHIVED`), `schema_version`, `training_eligible`, `validation_report` JSON (counts and codes only), uploader | Unique (`dataset_id`, `version_number`), unique `storage_key`, **unique `sha256`** (identical content once); CHECKs on version, size and shape. File contents are never stored in the database |
| `risk_alerts` | HR review workflow: `employee_id`, `trigger_type`, `priority`, `status` (`OPEN`/`ACKNOWLEDGED`/`RESOLVED`/`DISMISSED`), latest prediction/score/category snapshot, `model_version`, `event_count`, first/last observed, assignee, acknowledgement and resolution (by/at/reason), reviewer notes | Indexes (`employee_id`, `status`), `status`, `priority`, `assigned_to_user_id`; CHECKs on score and count. At most one active alert per employee (enforced by the service) |
| `risk_events` | Append-only detected conditions: `event_type`, unique `dedup_key`, current/previous prediction ids, score and category snapshots, `model_version`, `detected_at`, optional `alert_id` | Unique `dedup_key`; indexes (`employee_id`, `detected_at`), `event_type`, `alert_id`. No feature values are stored |
| `workforce_snapshots` | Immutable point-in-time aggregates: `snapshot_date` (org-local), `captured_at`/by, `period_start`, total/active/on-leave/inactive counts, month-to-date joiners and recorded departures, `source`, `data_quality` JSON | Unique `snapshot_date`; CHECKs: counts ≥ 0, statuses sum to the total, `period_start <= snapshot_date`. No employee-level data |
| `workforce_snapshot_departments` | Per-department rows of a snapshot (department name at capture time and the same counts) | Unique (`snapshot_id`, `department_id`); same count CHECKs; index on `department_id` |
| `interventions` | HR action records: `employee_id`, optional `risk_alert_id`, `category`, `title`, `description`, `priority`, `status` (`OPEN`/`IN_PROGRESS`/`COMPLETED`/`CANCELLED`), assignee, `due_date`, started/completed/cancelled timestamps, `outcome`, `cancellation_reason`, created/updated by, `version`, `active_alert_key`, `idempotency_key` | Unique `active_alert_key` (one active intervention per alert and category) and `idempotency_key`; CHECKs: completed has outcome, cancelled has reason, inactive has no key, version ≥ 1; indexes (`employee_id`, `status`), `status`, `priority`, `risk_alert_id`, `assigned_to_user_id` |
| `intervention_events` | Append-only history: `version`, `event_type`, from/to status, actor, `note` (notes only), `details` JSON (field names, assignee ids), `created_at` | Unique (`intervention_id`, `version`) |
| `recommendations` | Rule-based recommendation review records: `rule_id`, `rule_version`, `scope` (`INDIVIDUAL`/`DEPARTMENT`/`ORGANIZATION`), `category`, `priority`, `status` (`AVAILABLE`/`ACCEPTED`/`DISMISSED`/`COMPLETED`), subject (`employee_id` / `department_id`), `risk_alert_id`, `intervention_id`, rule-generated text, `evidence` JSON (ids and aggregate values only), `evidence_window`, `source_data_as_of`, `evaluated_at`, reviewer fields, `version` | Unique `dedup_key`; CHECKs: scope matches subject, dismissed has reason, completed has outcome, version ≥ 1; indexes (`scope`, `status`), `status`, `rule_id`, `employee_id`, `department_id`, `risk_alert_id` |
| `risk_alert_escalations` | Append-only review-deadline escalation history: `alert_id`, `stage` (`APPROACHING`/`OVERDUE`), `alert_priority` and `review_due_at` at escalation time, `recipient_count`, `triggered_by_user_id`, `created_at`. No scores, names or notes | Unique (`alert_id`, `stage`); CHECK `recipient_count >= 1`; index `created_at`. Escalation never changes the alert |
| `tasks` | Workforce tasks: `title`, `description`, `task_type`, `priority`, `status`, creator/updater users, `assignee_employee_id`, `department_id`, `due_date`, started/completed/cancelled times, `completion_summary`, `cancellation_reason`, `block_reason`, `block_details`, optional `intervention_id` or `recommendation_id`, `version`, `idempotency_key` | Unique `idempotency_key`; CHECKs: version ≥ 1, at most one source, completed has time, cancelled has time and reason, blocked has reason; indexes (`department_id`, `status`), (`assignee_employee_id`, `status`), `due_date`, `created_by_user_id`, `intervention_id`, `recommendation_id`; FKs RESTRICT (employees, departments, sources) / SET NULL (users) |
| `task_events` | Append-only task history: `task_id`, `version`, `event_type`, from/to status, actor, `details` JSON (ids, field names and codes only) | Unique (`task_id`, `version`) |
| `performance_cycles` | Review cycles: `name` (unique), `description`, period and deadline dates, `status`, `cancellation_reason`, creator/updater, `version` | CHECKs: period ordered, deadlines ordered, version ≥ 1, cancelled has reason; index `status` |
| `performance_goals` | Employee goals: `employee_id`, optional `cycle_id`, title, description, `category`, `measurable_target`, `target_value`, `unit`, start/target dates, `progress_percent`, `status`, `completed_at`, `manager_feedback`, creator/updater, `version` | CHECKs: progress 0–100, dates ordered, completed needs 100% and a time, version ≥ 1; indexes (`employee_id`, `status`), `cycle_id` |
| `performance_reviews` | One review per employee per cycle: reviewer, `status`, self-assessment, manager evaluation fields (JSON lists for strengths/improvement areas/development goals/training), `manager_private_notes`, `overall_rating`, lifecycle times, cancellation and reopen reasons, `version` | Unique (`cycle_id`, `employee_id`); CHECKs: rating 1–5, submitted/finalized has rating, finalized has time, cancelled has reason, version ≥ 1; indexes (`employee_id`, `status`), `reviewer_user_id`, `status` |
| `performance_events` | Append-only history for exactly one cycle, goal or review (`event_type`, from/to status, actor, `details` with ids/field names/statuses/ratings, never text) | CHECK: exactly one subject; unique (`cycle_id`, `version`), (`goal_id`, `version`), (`review_id`, `version`) |
| `audit_logs`  | Who did what: `actor_user_id`, `action`, `entity_type`, `entity_id`, JSON `metadata`, `created_at` | FK `actor_user_id → users.id ON DELETE SET NULL`. Indexes on `action`, `actor_user_id`, `created_at`, (`entity_type`, `entity_id`). Metadata containing keys like `password`, `token`, `secret`, `hash`, `api_key` or `authorization` is rejected |

Employees hold no login fields, no salary data and no attendance or shift fields (payroll lives in its own tables and is never returned by employee endpoints); payroll will get its own model.
CHECK constraints are enforced by MySQL 8.0.16 and later.

## Migrations

Migrations live in `backend/alembic/versions/`:

| Revision | Creates | Status |
|----------|---------|--------|
| `14cb546c9a5e` | `users`, `departments`, `employees`, `audit_logs` | **Not applied** to any real database |
| `319083e42c0c` | `shifts`, `attendance_records` (revises `14cb546c9a5e`) | **Not applied** to any real database |
| `da1930de632b` | `leave_types`, `leave_policies`, `leave_requests`, `leave_balances`, `notifications` (revises `319083e42c0c`) | **Not applied** to any real database |
| `a28868d7048a` | `salary_components`, `payroll_profiles`, `payroll_profile_components`, `payroll_periods`, `payroll_records`, `payroll_line_items` (revises `da1930de632b`) | **Not applied** to any real database |
| `371a76ea7369` | `attrition_model_versions`, `attrition_predictions` (revises `a28868d7048a`) | **Not applied** to any real database |
| `66d767384f54` | `datasets`, `dataset_versions`; adds nullable `attrition_model_versions.dataset_version_id` (index + FK, `ON DELETE RESTRICT`) (revises `371a76ea7369`) | **Not applied** to any real database |
| `03f5784d8edc` | `risk_alerts`, `risk_events` (revises `66d767384f54`) | **Not applied** to any real database |
| `70dd4dec2e60` | `workforce_snapshots`, `workforce_snapshot_departments` (revises `03f5784d8edc`) | **Not applied** to any real database |
| `87cc35d2de25` | `interventions`, `intervention_events` (revises `70dd4dec2e60`) | **Not applied** to any real database |
| `a6382858faa9` | `recommendations` (revises `87cc35d2de25`) | **Not applied** to any real database |
| `0bc0f8d45e33` | `risk_alert_escalations` (revises `a6382858faa9`) | **Not applied** to any real database |
| `818f279b6dde` (unchanged by the audit-log work, which needed no migration) | Index-only: 13 activity-feed indexes on `attendance_records`, `leave_requests`, `employees`, `audit_logs` (`action`, `created_at`), `risk_events`, `risk_alerts`, `intervention_events`, `recommendations` (revises `0bc0f8d45e33`). No table or column changes; downgrade drops exactly these indexes | **Not applied** to any real database |
| `0d5f1e6ae076` | `tasks`, `task_events` (revises `818f279b6dde`). Tested on scratch in-memory SQLite (upgrade adds exactly these tables, indexes and constraints; downgrade restores the previous schema); MySQL DDL rendered offline | **Not applied** to any real database |
| `b647981f350c` | `performance_cycles`, `performance_goals`, `performance_reviews`, `performance_events` (revises `0d5f1e6ae076`). Tested on scratch in-memory SQLite (upgrade adds exactly these tables, constraints and indexes; existing tables unchanged; downgrade restores the previous schema); MySQL DDL rendered offline | **Not applied** to any real database |
| `a0bd1c34f49c` | `recommendations` only, additive: appended enum values (category FOLLOW_UP_REVIEW/GOAL_SUPPORT/DATA_REFRESH, priority CRITICAL, status SUPERSEDED) and nullable `task_id` (FK tasks, indexed), `rank_score` (CHECK 0–100), `priority_reasons`, `evidence_fingerprint`, `superseded_by_id` (FK recommendations). Batch mode keeps every existing constraint on SQLite; the offline MySQL SQL is 12 ALTER statements and one CREATE INDEX (pinned in tests). Downgrade refuses while rules-v2 data exists (revises `b647981f350c`) | **Not applied** to any real database |
| `c4e7a91d3f20` | `revoked_tokens`; adds `users.token_version INTEGER NOT NULL DEFAULT 0` (revises `a0bd1c34f49c`) | **Not applied** to any real database |
| `5b9e2d7c41a8` (head) | MySQL only: the 90 timestamp columns in 33 tables become `DATETIME(6)` (one `ALTER TABLE … MODIFY` per table; each rebuilds its table). No-op on other databases (revises `c4e7a91d3f20`) | **Not applied** to any real database |

**Verification:** every revision is tested on in-memory SQLite and as rendered MySQL SQL (`alembic upgrade head --sql`). In Sub-prompt 32 the whole chain was also applied, fully downgraded and re-applied on a **disposable** MySQL 8.0.46 instance (never a real database). That run found, and the fix removed, a MySQL-only downgrade defect: indexes were dropped before their tables while a foreign key still needed them (error 1553). Because MySQL DDL is not transactional, the failure left the schema half-changed. `tests/test_migrations.py::test_downgrades_are_ordered_for_mysql_foreign_keys` now guards against it. Evidence is in [`docs/evidence/subprompt32/mysql/`](../docs/evidence/subprompt32/mysql/).

**MySQL server settings the application relies on:** InnoDB, `utf8mb4`, and `binlog_format=ROW` (the MySQL 8.0 default). The application's sessions run at READ COMMITTED isolation (`app/core/database.py`), which statement-based binary logging does not allow.

**Running the test suite against MySQL (optional, disposable database only):** see `backend/tests/mysql_guard.py`. The suite refuses to run unless it positively identifies the database as disposable:
- the database is named `wfai_test_*` or `wfai_disposable_*`;
- the host is loopback;
- a marker row matches `WFAI_MYSQL_TEST_MARKER`;
- the schema is at head;
- the `users` table is empty.

It never creates, migrates or drops anything; the operator migrates the database explicitly first.
`alembic.ini` contains **no database URL**: `alembic/env.py` reads it from `backend/.env` through the app
settings and logs only host, port and database name.

All commands below run from `backend/` with the virtual environment active.

### Inspect (safe, no database connection)

```powershell
alembic heads                     # should show a single head
alembic history                   # list revisions
alembic upgrade head --sql        # print the MySQL DDL that would run; nothing is executed
```

### Generate a new migration after changing models

Autogenerate compares the models with a **live** database, so only run it once `backend/.env` points at
your verified development database:

```powershell
alembic revision --autogenerate -m "describe the change"
```

Always open and review the generated file in `alembic/versions/` before applying it. Autogenerate
can miss renames (it sees a drop plus a create) and some type changes.

### Apply migrations

> ⚠️ **Never run migrations against an unverified or unintended database.** `alembic upgrade` and
> `alembic downgrade` change the schema of whatever `backend/.env` points at. Confirm the target first.

1. Complete the one-time setup and verification below, and confirm `GET /api/health/db` returns
   `reachable`.
2. Check the target in `backend/.env`: `DB_HOST`, `DB_PORT` and `DB_NAME` must be your development
   database, not a shared or production server.
3. Preview with `alembic upgrade head --sql` and read the output.
4. Apply with `alembic upgrade head`. Alembic logs `Target database: host:port/name` first.
5. Confirm with `alembic current`.

To undo the last migration: `alembic downgrade -1`. **This drops tables and their data.**

## Expected configuration

The backend connects with SQLAlchemy + PyMySQL using the values in `backend/.env`:

| Variable      | Default            | Required | Notes |
|---------------|--------------------|----------|-------|
| `DB_HOST`     | `127.0.0.1`        | no       | |
| `DB_PORT`     | `3306`             | no       | |
| `DB_NAME`     | `workforce_ai_pro` | no       | Create with `utf8mb4` |
| `DB_USER`     | none               | **yes**  | Dedicated app user, not `root` |
| `DB_PASSWORD` | none               | **yes**  | Must not be empty; special characters are escaped safely |

The connection always uses `charset=utf8mb4`.

## One-time setup (run by you, as a MySQL admin)

Pick a strong password and substitute it for `<APP_PASSWORD>`. Do not save it in this file.

```sql
CREATE DATABASE IF NOT EXISTS workforce_ai_pro
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'workforce_app'@'localhost' IDENTIFIED BY '<APP_PASSWORD>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
  ON workforce_ai_pro.* TO 'workforce_app'@'localhost';
```

Run it with `mysql -u root -p` (you will be prompted for the root password).

> If the backend connects via `127.0.0.1` and MySQL resolves the client as `127.0.0.1` rather than
> `localhost`, also create `'workforce_app'@'127.0.0.1'` with the same grants, or set `DB_HOST=localhost`.

## Verify (read-only)

```powershell
# Database exists and app user is defined (prompts for root password)
mysql -u root -p -e "SHOW DATABASES LIKE 'workforce_ai_pro'; SELECT user, host FROM mysql.user WHERE user = 'workforce_app';"

# App user can log in and reach the database (prompts for the app password)
mysql -u workforce_app -p -h 127.0.0.1 workforce_ai_pro -e "SELECT 1;"
```

Then, with the backend running, `GET http://127.0.0.1:8000/api/health/db` should return
`{"status": "ok", "database": "reachable"}`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Server won't start: `Invalid configuration (...)` | A required variable in `backend/.env` is missing or unsafe. The message names the field. |
| `/api/health/db` returns `503 Database unavailable` | Check the backend log for the error type, then work through the rows below. |
| MySQL not running | `Get-Service MySQL80`; start it with `Start-Service MySQL80` (admin PowerShell). |
| Access denied | Wrong `DB_USER`/`DB_PASSWORD`, or the user exists only for a different host (`localhost` vs `127.0.0.1`). |
| Unknown database | `DB_NAME` doesn't exist yet; run the setup SQL above. |
| `cryptography` package is required | Already in `requirements.txt` (needed for MySQL 8 `caching_sha2_password`). Reinstall requirements in the venv. |
| Connection timeout | Wrong `DB_HOST`/`DB_PORT`, or a firewall blocking port 3306. |
