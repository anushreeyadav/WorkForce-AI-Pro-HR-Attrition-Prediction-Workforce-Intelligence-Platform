# Backend source code (FastAPI)

This is a clean copy of `C:\WorkForce_AI_Pro\backend` (189 files) and `database/` (1 file). The original project was only read, never changed.

| Path | Content |
|---|---|
| `backend/app/main.py` | Application factory: middleware (CORS, trusted hosts, security headers, upload limit), error handlers, routers |
| `backend/app/api/` | 22 routers. The core dependencies are `deps.py` (JWT authentication, session checks, `require_roles`) and `auth.py` (register, login, logout with revocation, rate limits). Account administration is `users.py`. The rest cover employees, departments, attendance, leave, payroll, attrition (AI), datasets, risk monitoring, workforce analytics, interventions, recommendations, activity, reports, audit logs, tasks, performance and search |
| `backend/app/core/` | `config.py` (validated settings, strict in staging/production), `security.py` (bcrypt, JWT), `permissions.py` (role model), `rate_limit.py`, `database.py` |
| `backend/app/models/` | SQLAlchemy ORM models (36 tables) |
| `backend/app/schemas/` | Pydantic request and response schemas (`extra="forbid"`) |
| `backend/app/services/` | Business logic: scoping, audit, payroll engine, analytics, recommendation rules, reports and exports |
| `backend/ml/` | Attrition ML package: features, training, prediction, artifacts, dataset validation |
| `backend/alembic/`, `alembic.ini` | 16-revision migration chain (head `c4e7a91d3f20`) |
| `backend/tests/` | pytest suite (2229 tests passing, 1 skipped; see [`../09_Test_Results`](../09_Test_Results/TEST_RESULTS.md)) |
| `backend/requirements.txt` | Pinned dependencies |
| `backend/.env.example` | Configuration **template** with empty secret values (no real configuration) |

**Excluded on purpose:** `.env` (none exists), `.venv`, `__pycache__`, `.pytest_cache`, compiled files, trained model artifacts (`ml/models`, which is empty anyway) and registered datasets (`ml/datasets`).

To run it, see [`../project_docs/README.md`](../project_docs/README.md) (Getting started) and [`../project_docs/docs/DEPLOYMENT_READINESS.md`](../project_docs/docs/DEPLOYMENT_READINESS.md). The tests need no database: `.\.venv\Scripts\python.exe -m pytest`.
