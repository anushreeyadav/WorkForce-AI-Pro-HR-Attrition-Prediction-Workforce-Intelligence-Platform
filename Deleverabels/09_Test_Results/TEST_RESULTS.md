# Test results

Every count below was observed in this run on the development machine (Windows, Python 3.14 virtualenv, Node.js 24). The complete raw output of each command, with its command line and exit code, is in [`raw/`](raw).

- **Backend tests** use **in-memory SQLite** and test-only placeholder settings. They never read `backend/.env` and never contact MySQL.
- **No coverage percentage** was measured, and none is claimed.

## Summary

| Check | Command (from `backend/` or `frontend/`) | Result | Raw output |
|---|---|---|---|
| Backend: full suite | `python -m pytest -q -p no:cacheprovider -rs` | **2229 passed, 1 skipped, 0 failed**; exit 0 | [backend_full_suite.txt](raw/backend_full_suite.txt) |
| Authorization matrix (172 routes × roles and tokens) | `pytest tests/test_authorization_matrix.py` | 323 passed | [raw](raw/test_authorization_matrix.txt) |
| Account security (revocation, sessions, admin API, rate limits, config) | `pytest tests/test_account_security.py` | 34 passed | [raw](raw/test_account_security.txt) |
| Query scaling (38 read endpoints, payroll, monitoring) | `pytest tests/test_query_scaling.py` | 3 passed | [raw](raw/test_query_scaling.txt) |
| Migrations (SQLite upgrade/downgrade, offline MySQL SQL, per revision) | `pytest tests/test_migrations.py` | 19 passed | [raw](raw/test_migrations.txt) |
| Frontend/backend query contract | `pytest tests/test_frontend_contract.py` | 156 passed | [raw](raw/test_frontend_contract.txt) |
| UI workflow journeys (request level) | `pytest tests/test_ui_workflows.py` | 8 passed | [raw](raw/test_ui_workflows.txt) |
| Frontend navigation vs. backend roles | `pytest tests/test_frontend_navigation.py` | 2 passed | [raw](raw/test_frontend_navigation.txt) |
| No server errors (every route, malformed input, populated world) | `pytest tests/test_no_server_errors.py` | 234 passed | [raw](raw/test_no_server_errors.txt) |
| AI API / ML / datasets | `pytest tests/test_attrition_api.py`, `test_attrition_ml.py`, `test_datasets_api.py` | 31 / 48 / 27 passed | [api](raw/test_attrition_api.txt), [ml](raw/test_attrition_ml.txt), [datasets](raw/test_datasets_api.txt) |
| Migration head and chain | `alembic heads`, `alembic history` (scripts only) | Single head `c4e7a91d3f20`; 16 linear revisions | [alembic_heads_history.txt](raw/alembic_heads_history.txt) |
| Frontend unit tests | `npm test` (Node built-in test runner) | **22 passed, 0 failed**; exit 0 | [frontend_test.txt](raw/frontend_test.txt) |
| Frontend lint | `npm run lint` (oxlint) | 0 warnings, 0 errors; exit 0 | [frontend_lint.txt](raw/frontend_lint.txt) |
| Frontend production build | `npm run build` (Vite) | Succeeds; main chunk 356.55 kB (gzip 115.64 kB); exit 0 | [frontend_build.txt](raw/frontend_build.txt) |

The targeted suites are subsets of the full suite, run separately so each has its own output file.

## Skipped test

| Test | Reason | Effect |
|---|---|---|
| `tests/test_dataset_validation.py:200` (symlink escape) | "symlinks not permitted for this user on this platform": this Windows account may not create symbolic links | The **symlink-escape protection of dataset storage is not verified on this machine**. The other storage path checks ran and passed |

## Not executed, and why

| Not executed | Why |
|---|---|
| Any test against **MySQL** (integration, migrations applied to MySQL, `EXPLAIN` profiling) | Constraint: no connection to a real MySQL server, and no disposable MySQL instance available |
| **Signed-in browser end-to-end tests** as automated assertions | The project has no browser test framework, and none was added. The screenshots in [`../06_UI_Screenshots_Demo`](../06_UI_Screenshots_Demo) are real signed-in browser renders, but they are captures, not pass/fail tests |
| **Load, concurrency and throughput** tests | No measurement environment; nothing is claimed about capacity or latency |
| Multi-worker **rate-limit** behaviour, and **TLS/proxy** configuration | Requires a deployment environment |
| Validation of the attrition model on real data | No approved real dataset exists; the model is not production-validated |

## Evidence generation (not tests)

The screenshots, sample reports and API examples were produced with the scripts in [`../tools/`](../tools) against the unmodified application in a disposable SQLite demo environment, using synthetic data. They demonstrate behaviour but are not counted as test results.
