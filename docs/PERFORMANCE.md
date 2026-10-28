# Performance and scalability (Sub-prompt 28)

This document records how WorkForce AI Pro was measured, what was changed, and what is still unmeasured. It covers the backend's query patterns, write paths, analytics, reports and the frontend bundle.

**All measurements are SQL statement counts on in-memory SQLite.** Counts are deterministic and show whether a request does per-row work. They do **not** show MySQL timings, query plans, throughput or capacity, and no such claims are made here.

## How it was measured

[`backend/tests/test_query_scaling.py`](../backend/tests/test_query_scaling.py) builds one synthetic world and makes every request twice:

1. **Small world:** 2 employees per department.
2. **Grown world:** 8 employees per department.

Each employee has:
- a login and a leave request;
- an attendance record and a task;
- an intervention, a goal and a performance review;
- a risk alert, a payroll profile and record, and notifications.

Any request whose statement count grows between the two sizes has a per-row (N+1) query pattern, and the test fails. Lists are requested with `page_size=100`, so every row is loaded and serialized. A probe also confirmed that each list actually returned rows.

## Baseline findings

### Read endpoints: no per-row queries

All 38 read endpoints measured issue the same number of statements in both worlds. The endpoints cover:
- employees, departments and department members;
- attendance, leave, payroll records and profiles;
- tasks, interventions, recommendations, goals, reviews and risk alerts, with their summaries;
- notifications and audit logs;
- the activity feed and its summary;
- global search;
- workforce analytics (overview, retention, stability with `by_department`) and the attrition summary;
- all report exports and the audit export.

| Request | Statements (both sizes) |
|---|---|
| Employee, task, goal and review lists | 4–5 |
| Leave review queue | 5–6 |
| Payroll records and profiles | 6 |
| Audit logs | 3 (5 for a manager) |
| Activity feed and summary | 15 (bounded per source, not per row) |
| Global search | 13 (one query per searchable type) |
| CSV report exports | 2–4 (activity: 15) |

Earlier sub-prompts had already eager-loaded relationships (`selectinload`) and grouped aggregates, so no read path needed changes.

### Write and evaluation paths

The table counts SELECT statements only, because inserts naturally grow with new rows.

| Operation | Before | After |
|---|---|---|
| Payroll period calculation, 2 → 12 employees | 19 → 89 (about 7 SELECTs per employee) | **12 → 12** |
| Risk-monitoring run, 2 → 8 new alerts | 8 → 14 (HR recipients re-read per alert) | **7 → 7** |
| Recommendation evaluation (rules-v1 and rules-v2) | constant | unchanged |
| Alert-escalation evaluation | constant | unchanged |

## Optimizations implemented

1. **Payroll calculation reads its inputs in grouped queries** (`app/services/payroll.py`).
   - **Before:**
     - For each employee, the calculation loaded salary profiles (with components) and approved unpaid leave.
     - It counted ABSENT days, open check-ins and attendance-during-leave conflicts with one query each.
     - The calculation covers every profiled employee in one request, so 2,000 employees meant about 14,000 queries.
   - **After:**
     - `profiles_by_employee` and `prefetch_facts` read the same data with grouped queries, chunked 500 employee ids at a time. The per-employee assembly in `gather_facts` is unchanged.
     - The queries use the existing indexes: `uq_attendance_records_employee_id_work_date`, `ix_leave_requests_employee_dates` and `ix_payroll_profiles_employee_effective`.
   - **Evidence:**
     - All 130 payroll, engine and report tests pass.
     - The new scaling test fails against the original code (19 → 89) and passes after the change.
     - A one-off side-by-side run compared the original and new code on a world with unpaid leave, absences, open check-ins, leave conflicts, overlapping profiles, an inactive employee and a recalculation. Records, line items, warnings and skip reasons were identical.
   - **Unchanged:** the period row lock, conditional updates, skip reasons, the single commit with the audit row, and the rejection of concurrent approval (409).
2. **Risk-monitoring runs read the HR recipient list once** (`app/services/risk_monitoring.py`), instead of once per newly created alert. Recipients and their order are the same, and the employee concerned is still excluded.
3. **The Analytics page sends one stability request** (`frontend/src/pages/Analytics.jsx`).
   - The HR/ADMIN organization view used to request `/stability` and `/stability?by_department=true`. The second response already contains the organization result, so the card and the department table now share it.
   - This removes 1 request per page load (and per snapshot capture) on that view. Department views were already a single request.

## Reviewed and left unchanged (code review, with reasons)

- **Indexes:**
  - The grouped and scoped queries above match existing composite indexes. These include the audit log on entity and on action with time; tasks on department with status and on assignee with status; interventions, goals, reviews and alerts on employee with status; and predictions on employee with time.
  - No query showed a missing index, so the schema and migration head are unchanged.
- **Model loading:** the active attrition model is cached per process, keyed by model version and artifact SHA-256, guarded by a lock, and cleared on activation. It is not reloaded per request.
- **Feature extraction:** one grouped attendance query per batch. Batch prediction allows at most 100 employees; monitoring runs `RISK_MONITORING_MAX_BATCH` (500) with a cursor.
- **Reports and audit export:**
  - Rows are fetched with `LIMIT n+1`, and oversized requests are refused with 422 **before** rendering. CSV formula-injection protection, safe filenames, `Cache-Control: no-store` and payroll-export auditing are unchanged.
  - Rendering is in memory and bounded by those row limits; streaming was not needed.
- **Stored results:** analytics reads use stored snapshots and never capture or recompute history. The stability department comparison uses two grouped queries (Sub-prompt 27).
- **Database sessions:**
  - One session per request, closed by the dependency.
  - The pool is 5 + 5 overflow, timeout 10 s, `pool_pre_ping`, recycle 30 min. It was not changed: there is no evidence that it limits anything, and raising it without load tests would be arbitrary.
- **Frontend:**
  - Pages are lazy-loaded per route, and Recharts lives in its own chunk (`CartesianChart`, 373 kB, gzip 107 kB) that loads only on pages with charts.
  - The shared `index` chunk is 356 kB (gzip 115.5 kB) before and after; it holds React, the router, Axios and the layout.
  - `useApi` aborts stale requests, and nothing polls in the background.
  - Tables show at most 100 rows per page, so virtualization was not added.

## Existing limits (unchanged, enforced)

| Area | Limit |
|---|---|
| Pagination | `page_size` 1–100 on every list |
| Reports | `REPORT_MAX_ROWS` 5,000; `REPORT_PDF_MAX_ROWS` 1,000; `REPORT_MAX_RANGE_DAYS` 366; refused, never truncated |
| Audit queries and export | `AUDIT_QUERY_MAX_RANGE_DAYS` 366; export uses the report row limits |
| Activity feed | `ACTIVITY_FEED_MAX_WINDOW_DAYS` 31, `ACTIVITY_FEED_MAX_ITEMS` 5,000; signed incremental cursors expire after `ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS` 72; update pages at most 100 |
| Global search | 2–100 characters, at most 20 results per type, labels only |
| Datasets | `DATASET_MAX_UPLOAD_BYTES` 10 MB, 200,000 rows, 50 columns |
| AI | 100 employees per batch prediction; `RISK_MONITORING_MAX_BATCH` 500 and `ALERT_ESCALATION_MAX_BATCH` 500 per run, with cursors |
| Analytics | Trends and retention span at most 60 months; forecast horizon at most `WORKFORCE_FORECAST_MAX_HORIZON_MONTHS` (12) |

## Remaining constraints and recommendations

These need a disposable MySQL instance or deployment-level load testing. None of them were measured here.

- **Profile the heavy reads on MySQL 8 with `EXPLAIN ANALYZE`** at realistic volumes:
  - the activity feed (one query per source over the window);
  - audit queries for managers (`EXISTS` subqueries per entity type with a cast entity id);
  - substring search (`LIKE '%term%'` cannot use B-tree indexes);
  - stability and retention (status-change audit rows read per window).
- **Monitoring runs load full prediction history** for up to 500 employees per run, because the "previous comparable prediction" rule may skip incompatible ones. With long histories, consider a window-function query or a retention policy after profiling.
- **Some requests are long and synchronous:**
  - model training (`POST /api/ai/attrition/train`);
  - dataset validation;
  - payroll calculation for very large organizations;
  - large XLSX/PDF exports.

  They run in FastAPI's thread pool. Candidates for background processing later; no worker infrastructure was added.
- **Payroll calculation is still one transaction** for the whole period. Its SELECT count is now constant, but inserts (records and line items) still grow with headcount.
- **Not measured at all:** throughput, latency percentiles and concurrent-user capacity.
