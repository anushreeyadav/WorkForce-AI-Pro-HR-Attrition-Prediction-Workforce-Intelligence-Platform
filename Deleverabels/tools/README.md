# Evidence tooling

These scripts produced the screenshots, sample reports, API examples and generated documentation in this package. They are **not part of the application** and do not modify it.

| Script | Produces | How |
|---|---|---|
| `demo_server.py` | A running demo backend on `127.0.0.1:8000` | Serves the **unmodified** app with a disposable SQLite file in the system temp folder; seeds synthetic data, mostly through the app's own API (see the script's docstring for the two direct history inserts) |
| `capture_screenshots.py` | `06_UI_Screenshots_Demo/**.png` | Headless Microsoft Edge over the DevTools Protocol, using the `websockets` package already in the backend virtualenv; signs in through the real login form |
| `generate_reports.py` | `05_Analytics_Reports/**` | Calls the real export endpoints as HR and as a MANAGER; saves each file under the server's filename |
| `capture_api_examples.py` | `07_API_Documentation/examples`, `03_AI_Prediction_APIs/examples` | Live calls; tokens and the password are redacted before saving |
| `generate_docs_artifacts.py` | `openapi.json`, endpoint inventory, schema SQL, table inventory, ER diagram, migration history | From code only (`app.openapi()`, ORM metadata, `alembic upgrade head --sql`); no database |

**How they were run** (from the project root):
1. `backend\.venv\Scripts\python.exe Deleverabels\tools\demo_server.py`
2. In `frontend`: `npm run build`, then `npx vite preview --port 4173`
3. `backend\.venv\Scripts\python.exe Deleverabels\tools\capture_screenshots.py`, then `generate_reports.py`, then `capture_api_examples.py`
4. `generate_docs_artifacts.py` (independent of steps 1–3)

**Safety:**
- The real MySQL database and `backend/.env` are never used.
- The demo password is random per run and kept outside the deliverables.
- After capture, the servers were stopped and the demo database and session file deleted.
- Model artifacts were written only to the temp folder; `backend/ml/models` stays empty.
