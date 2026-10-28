# QA tooling (Sub-prompt 32)

These are local, disposable test harnesses. They are **not part of the application**, and they never touch `backend/.env` or a real database. All data is synthetic.

| Script | Purpose |
|---|---|
| `local_server.py` | Serves the **unmodified** API on `127.0.0.1:8000` with synthetic data. It uses a throwaway SQLite file in the temp folder, or, with `--mysql`, only a disposable MySQL database positively identified by `backend/tests/mysql_guard.py`. Its random password and account list go to `%TEMP%\wfai_qa\session.json`, never into the project |
| `browser_tests.py` | 17 workflow tests of the production frontend build in headless Microsoft Edge, over the DevTools Protocol, using trusted mouse and keyboard input. Records CSP violations, API 5xx responses and page errors, and saves screenshots |
| `loadtest.py` | Bounded load test with latency percentiles, throughput, status codes, and the server process's CPU and memory |

## Browser tests

```powershell
# 1. API without a model (the "no active model" state is tested first); alert threshold lowered for this process only
backend\.venv\Scripts\python.exe qa\local_server.py --no-model --alert-threshold 5
# 2. Production build served WITH the Content-Security-Policy header (vite.config.js -> preview.headers)
cd frontend; npm run build; npx vite preview --port 4173 --strictPort --host 127.0.0.1
# 3. Run (needs Microsoft Edge); exit code 0 only if every test passes with no CSP violation and no API 5xx
backend\.venv\Scripts\python.exe qa\browser_tests.py --out docs\evidence\subprompt32\browser
```

The tests change state (password change, approvals), so each full run needs a freshly started `local_server.py`. `--only t01,t05` runs a subset.

## Load tests

```powershell
backend\.venv\Scripts\python.exe qa\local_server.py --employees 500 --attendance-days 30
backend\.venv\Scripts\python.exe qa\loadtest.py --out load.json --concurrency 8 --requests 300 --server-pid <pid listening on 8000>
```

**Limits, enforced before anything is sent:**
- loopback targets only;
- concurrency at most 32;
- at most 2000 requests per scenario and 120 s;
- 15 s timeout per request;
- a scenario stops early when more than 20% of its requests fail.

The only writes are attrition predictions, into the disposable database. The rate limits of the QA server process are raised, because one client signs in many times; that never happens for a deployment.

## Against the disposable MySQL test database

Set `WFAI_MYSQL_TEST_URL` and `WFAI_MYSQL_TEST_MARKER` (see `backend/tests/mysql_guard.py`), migrate it explicitly (`alembic upgrade head`), and start `local_server.py --mysql`. The guard refuses any database that is not:
- named `wfai_test_*` or `wfai_disposable_*`;
- on a loopback host;
- marked with the matching token;
- at the migration head;
- empty of users.

Seeding commits data, so recreate the database before the next run.
