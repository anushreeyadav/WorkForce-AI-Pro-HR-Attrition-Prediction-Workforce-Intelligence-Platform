# Database schema: WorkForce AI Pro

**Source of truth:** the SQLAlchemy models in `backend/app/models` and the Alembic chain in `backend/alembic/versions`. Everything here was **generated from that code** by [`../tools/generate_docs_artifacts.py`](../tools/generate_docs_artifacts.py), with no database connection.

> **Generated, not verified on MySQL.** This schema has **not** been verified against a running MySQL server, and no migration has ever been applied to one. The migration tests check it only on disposable SQLite and as rendered MySQL SQL. No data, users or credentials are included.

| File | Content |
|---|---|
| [`table_inventory.md`](table_inventory.md) | All 36 tables: columns, types, nullability, primary keys, foreign keys with ON DELETE rules, server defaults, named constraints and indexes |
| [`er_diagram.md`](er_diagram.md) | Mermaid entity-relationship diagram (every foreign key). Renders in GitHub, the VS Code Markdown preview or mermaid.live |
| [`schema_mysql.sql`](schema_mysql.sql) | MySQL 8 DDL rendered **offline** by `alembic upgrade head --sql`; nothing was executed |
| [`migration_history.txt`](migration_history.txt) | All 16 revisions, oldest first; current head **`c4e7a91d3f20`** |

## Design overview

- **Database:** MySQL 8 (InnoDB, `utf8mb4`) through SQLAlchemy 2 and PyMySQL.
- **Timestamps:** stored as UTC `DATETIME`. Work and leave dates are organization-local `DATE`.
- **Names:** constraints are named by convention (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`).
- **Optimistic concurrency:** workflow tables carry a `version` column checked by conditional `UPDATE`s, so concurrent edits return 409 instead of overwriting.
- **History is append-only:** `*_events` tables and `audit_logs`.

| Area | Tables | Key relationships |
|---|---|---|
| Identity and access | `users`, `revoked_tokens` | `users.role` (ADMIN/HR/MANAGER/EMPLOYEE), `is_active`, `token_version` (session version). `revoked_tokens.user_id → users` (CASCADE) holds logged-out token ids |
| Organization | `departments`, `employees` | `employees.department_id → departments` (RESTRICT). `employees.user_id → users` (unique, optional: an employee may have one login) |
| Time | `shifts`, `attendance_records` | `attendance_records(employee_id, work_date)` unique; FKs to employees and shifts |
| Leave | `leave_types`, `leave_policies`, `leave_requests`, `leave_balances` | Policies per type and optional department. Requests and balances per employee and type. Reviewer and canceller are users |
| Payroll | `salary_components`, `payroll_profiles`, `payroll_profile_components`, `payroll_periods`, `payroll_records`, `payroll_line_items` | One profile per employee per effective range; `payroll_records(period_id, employee_id)` unique; line items per record |
| AI and datasets | `datasets`, `dataset_versions`, `attrition_model_versions`, `attrition_predictions` | Versions are immutable (unique sha256 and storage key). Each model records its dataset version. Predictions per employee and model, with feature snapshot and explanation |
| Risk monitoring | `risk_alerts`, `risk_events`, `risk_alert_escalations` | Alerts per employee with latest prediction, assignee and review-deadline fields. Events deduplicated by `dedup_key`. One escalation per (alert, stage) |
| Retention workflow | `interventions`, `intervention_events`, `recommendations` | An intervention optionally references a risk alert; one active intervention per (alert, category). Recommendations are versioned rules with unique `dedup_key`, optional links to alert, intervention, task and superseding recommendation |
| Work | `tasks`, `task_events` | A task links optionally to an intervention or recommendation, a department and an assignee employee. Unique `idempotency_key`. Events per version |
| Performance | `performance_cycles`, `performance_goals`, `performance_reviews`, `performance_events` | One review per (cycle, employee); the reviewer is a user. Goals per employee, optionally per cycle. Events per cycle, goal or review version |
| Analytics | `workforce_snapshots`, `workforce_snapshot_departments` | One immutable snapshot per date, with per-department counts (unique (snapshot, department)) |
| Communication and audit | `notifications`, `audit_logs` | Notifications per user (unread index). The audit log is append-only: actor, action, entity, sanitized JSON details |

Delete behaviour:
- **Records are never hard-deleted** through the API.
- **Most foreign keys are RESTRICT.** This preserves HR history.
- **Some are SET NULL** where an acting user may disappear, such as audit actors and `created_by`/`updated_by` columns.
- **CASCADE is rare:** used only for rows owned by a parent, exactly four foreign keys:
  - `revoked_tokens.user_id`;
  - `notifications.user_id`;
  - `payroll_profile_components.profile_id`;
  - `payroll_line_items.record_id`.

## Migration chain

16 revisions, linear, from `14cb546c9a5e` (initial core models) to `c4e7a91d3f20` (token revocation). Every revision has a downgrade. **Downgrades are destructive**: they drop tables or columns and their data. One downgrade (`a0bd1c34f49c`) refuses to run while rules-v2 data exists.

The operator procedure for applying them (verify the target, back up, review the generated SQL, then apply) is in [`../project_docs/docs/DEPLOYMENT_READINESS.md`](../project_docs/docs/DEPLOYMENT_READINESS.md#database-and-migration-precautions).
