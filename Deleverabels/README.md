# WorkForce AI Pro: submission deliverables

**WorkForce AI Pro – HR Attrition Prediction & Workforce Intelligence Platform.** A FastAPI and SQLAlchemy backend (MySQL 8 target) with a React 19 and Vite frontend, covering:
- **People and time:** employees and departments, attendance, leave;
- **Pay:** payroll and payslips;
- **AI and analytics:** attrition-risk estimation and monitoring, workforce analytics and forecasting;
- **Workflow:** interventions, rule-based recommendations, tasks, performance reviews;
- **Oversight:** reports and exports, audit logs, an activity feed and global search, with role-based access control (ADMIN, HR, MANAGER, EMPLOYEE).

Project documentation is in [`project_docs/`](project_docs): the root README, [final project report](project_docs/docs/FINAL_PROJECT_REPORT.md), [security / RBAC](project_docs/docs/SECURITY_RBAC.md), [performance](project_docs/docs/PERFORMANCE.md), [QA report](project_docs/docs/QA_REPORT.md) and [deployment readiness](project_docs/docs/DEPLOYMENT_READINESS.md).

## Deliverables checklist

| # | Deliverable | Location | Status |
|---|---|---|---|
| 1 | Backend source code | [`01_Backend_Source_Code/`](01_Backend_Source_Code/README.md) | ✅ Clean copy (190 files); no secrets, environments or caches |
| 2 | Frontend source code | [`02_Frontend_Source_Code/`](02_Frontend_Source_Code/README.md) | ✅ Clean copy (53 files); no `node_modules` or build output |
| 3 | AI prediction APIs | [`03_AI_Prediction_APIs/AI_API_GUIDE.md`](03_AI_Prediction_APIs/AI_API_GUIDE.md) | ✅ Guide, endpoint inventory, 12 live examples, lifecycle, unavailable-model responses, source copy (26 files) |
| 4 | Workforce dashboards | [`04_Workforce_Dashboards/Dashboard_Notes.md`](04_Workforce_Dashboards/Dashboard_Notes.md) | ✅ 10 genuine screenshots plus notes |
| 5 | Analytics reports | [`05_Analytics_Reports/`](05_Analytics_Reports/README.md) | ✅ 29 genuine exports (CSV, XLSX, PDF) plus a manifest |
| 6 | UI screenshots / demo | [`06_UI_Screenshots_Demo/`](06_UI_Screenshots_Demo/README.md) | ✅ 38 genuine screenshots in 11 folders plus an index |
| 7 | API documentation | [`07_API_Documentation/API_DOCUMENTATION.md`](07_API_Documentation/API_DOCUMENTATION.md) | ✅ Guide, `openapi.json`, 172-operation inventory, 14 live examples |
| 8 | Database schema | [`08_Database_Schema/DATABASE_SCHEMA.md`](08_Database_Schema/DATABASE_SCHEMA.md) | ✅ Overview, table inventory (36 tables), ER diagram, offline MySQL DDL, migration history |
| 9 | Test results | [`09_Test_Results/TEST_RESULTS.md`](09_Test_Results/TEST_RESULTS.md) | ✅ Summary plus 16 raw output files with commands and exit codes |

The scripts that produced the evidence are in [`tools/`](tools/README.md), and [`FILE_INVENTORY.md`](FILE_INVENTORY.md) lists every file in this package.

## How to open

- **Screenshots:** PNG files, open with any image viewer. Start with [`06_UI_Screenshots_Demo/README.md`](06_UI_Screenshots_Demo/README.md), which says what each one shows.
- **Reports:** CSV in a spreadsheet or text editor, XLSX in Excel or LibreOffice, PDF in any viewer.
- **API:** load [`07_API_Documentation/openapi.json`](07_API_Documentation/openapi.json) into an OpenAPI viewer, or run the backend locally and open `http://127.0.0.1:8000/docs`.
- **ER diagram:** [`08_Database_Schema/er_diagram.md`](08_Database_Schema/er_diagram.md) is Mermaid; it renders in GitHub, the VS Code Markdown preview or mermaid.live.

## How the evidence was produced

There was no access to a real database, and that was by constraint. So the running-application evidence was produced as follows:
- The **unmodified** application ran against a **disposable SQLite database** in the system temp folder ([`tools/demo_server.py`](tools/demo_server.py)), with **synthetic** data: fictitious people, `example.com` emails and demo salaries.
- Data was created through the application's own API by the role a real user would use. Only past attendance and past workforce snapshots were inserted directly, because the API records only the present.
- The production frontend build was rendered in **Microsoft Edge** (headless), signed in through the real login form ([`tools/capture_screenshots.py`](tools/capture_screenshots.py)).
- The AI screens and examples use a **demo model trained on the repository's synthetic data generator**. Its scores and evaluation numbers describe generated data only.
- The demo database and session file were deleted afterwards. The real `.env` and MySQL were never used.

## Verified evidence vs. documentation-only items

| Verified (observed in this run) | Documentation only (not verified here) |
|---|---|
| Screenshots of real rendered pages (06, 04) | Behaviour on **MySQL** (schema, migrations, performance) |
| Report files from the real export endpoints (05) | **Model-unavailable UI state**: documented from code and tests; the demo had a model |
| Live API responses (03 and 07 `examples/`) | **Swagger UI**: instructions only; `openapi.json` was generated from the app |
| `openapi.json`, schema DDL and inventories generated from code (07, 08) | Deployment, TLS, multi-worker rate limits |
| Test counts and raw outputs (09) | Real-world model accuracy, calibration and fairness |

## Not generated, and why

| Item | Reason |
|---|---|
| MySQL-verified schema or a database backup | Real MySQL may not be contacted. The schema is generated from models and migrations and marked as such |
| Screenshots of the model-unavailable state | The demo activated a synthetic model during seeding. The state is documented in the AI guide with its code and tests |
| Mobile-width screenshots | Only desktop width (1440 px) was captured |
| Signed-in browser **tests** (pass/fail), and load or concurrency tests | No browser test framework or load environment exists; none was added |
| The symlink-escape test result | Skipped: this Windows account may not create symlinks |

## Limitations relevant to assessment

- **The attrition model is not production-validated.** There is no model trained on real data, and evaluation numbers on synthetic data are not evidence of accuracy.
- **Demo data volumes are small.** With 14 employees, each department's stability indicator is "insufficient data" (minimum average headcount 10); the organization-level indicator is shown. This is the documented safeguard.
- **Not deployed or verified on MySQL.** Staging readiness is a conditional go and production is a no-go; see [`project_docs/docs/DEPLOYMENT_READINESS.md`](project_docs/docs/DEPLOYMENT_READINESS.md).
- **Cosmetic:** in PDF exports, the subtitle line overlaps the table header slightly.
