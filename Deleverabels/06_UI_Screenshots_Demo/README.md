# UI screenshots and demo

There are 38 genuine screenshots of the running application. They were captured by [`../tools/capture_screenshots.py`](../tools/capture_screenshots.py), which drives Microsoft Edge (headless) over the Chrome DevTools Protocol, against:
- the production frontend build (`npm run build` then `npm run preview`);
- the **unmodified** backend, served by the disposable demo environment ([`../tools/demo_server.py`](../tools/demo_server.py)).

How they were taken:
- **Sign-in:** each role signed in through the real login form; the password field is masked.
- **Size:** 1440 px wide, full page. Pages with an open dialog are captured at the 1440×900 viewport.
- **Data:** all of it is synthetic (fictitious names, `example.com` emails, demo figures). No real credentials, tokens or personal data appear.
- **Format:** open the PNG files with any image viewer.

| Folder | File | Role | Route / action | Shows |
|---|---|---|---|---|
| Authentication | 01_login_page.png | — | `/login` | Sign-in form and the backend status line |
| | 02_login_invalid_credentials.png | — | Wrong credentials submitted | Generic "Invalid email or password" message (no account oracle) |
| | 03_protected_route_redirects_to_login.png | signed out | `/payroll` | A protected page redirects to sign-in |
| Dashboard | 01_main_dashboard_hr.png | HR | `/` | Organization dashboard (see [04 notes](../04_Workforce_Dashboards/Dashboard_Notes.md)) |
| | 02_dashboard_manager_department_scope.png | MANAGER | `/` | Department-scoped dashboard |
| | 03_dashboard_employee_self_service.png | EMPLOYEE | `/` | Self-service dashboard |
| Employee_Management | 01_employee_list.png | HR | `/employees` | Directory, filters, pagination |
| | 02_employee_detail.png | HR | Row click | Employee detail dialog with edit and status actions |
| | 03_departments.png | HR | `/departments` | Department management |
| Attendance_Leave | 01_my_attendance_employee.png | EMPLOYEE | `/attendance` | Own attendance; checked in today; 30-day summary |
| | 02_attendance_team_overview_hr.png | HR | `/attendance` → All records | Team attendance and summary |
| | 03_my_leave_requests_employee.png | EMPLOYEE | `/leave` | Own requests (one approved by the manager) |
| | 04_leave_balances_employee.png | EMPLOYEE | `/leave` → Balances | Balances per leave type |
| | 05_leave_review_queue_manager.png | MANAGER | `/leave` → Review queue | Pending requests in the manager's department |
| Payroll | 01_payroll_periods_and_records.png | HR | `/payroll` → last month | Periods and records (calculated, approved, paid); calculate and export actions |
| | 02_payroll_record_detail.png | HR | Row click | Earnings, deductions, net pay; approve or mark-paid action |
| | 03_my_payslips_employee.png | EMPLOYEE | `/payslips` | Own approved or paid payslips |
| | 04_payslip_dialog_employee.png | EMPLOYEE | Payslip | Payslip lines and totals |
| AI_Risk_Monitoring | 01_attrition_risk_alerts.png | HR | `/risk` | Alert counts and list; disclaimer (no numeric scores shown) |
| | 02_risk_alert_detail.png | HR | Row click | Alert detail; Plan intervention / Dismiss / Resolve |
| | 03_datasets_and_model_status_admin.png | ADMIN | `/datasets` | Model status (active **synthetic demo** model) and datasets |
| | 04_risk_page_not_available_to_employee.png | EMPLOYEE | `/risk` | Role guard: "Not available for your role" |
| | 05_dataset_versions_admin.png | ADMIN | Row click | Dataset versions, training eligibility, validate action |
| Analytics_Forecasting | 01_workforce_analytics_forecast_stability.png | HR | `/analytics` | Trend, stability (heuristic), forecast, department comparison (insufficient data for small departments) |
| Interventions_Tasks | 01_interventions.png | HR | `/interventions` | Interventions list and summary |
| | 02_intervention_detail_history.png | HR | Row click | Lifecycle actions and history (created, started, note) |
| | 03_recommendations.png | HR | `/recommendations` | Rule-based recommendations (rules-v2) |
| | 04_tasks.png | HR | `/tasks` | Task summary and list |
| | 05_task_detail.png | HR | Row click | Task detail with transitions and history |
| Performance_Reviews | 01_goals_manager.png | MANAGER | `/performance` | Goals for the department |
| | 02_reviews_manager.png | MANAGER | Reviews tab | Reviews where the manager is the reviewer |
| | 03_review_detail_reviewer_evaluation.png | MANAGER | Row click | Submitted self-assessment and the reviewer's evaluation editor |
| | 04_review_cycles_hr.png | HR | Cycles tab | Review cycles with the HR "New cycle" action |
| Notifications_Search | 01_notifications.png | HR | `/notifications` | In-app notifications |
| | 02_global_search.png | HR | `/search?q=Engineer` | Grouped search results |
| Reports_Audit | 01_activity_feed_monitoring_status.png | HR | `/activity` | Monitoring status and activity feed (polling) |
| | 02_reports_and_exports.png | HR | `/reports` | Report catalog with CSV, XLSX and PDF downloads |
| | 03_audit_logs.png | HR | `/audit` | Audit log query with filters and export |

Not captured:
- **The signed-in mobile layout.** Captures are desktop width only.
- **The model-unavailable UI state.** The demo activated its synthetic model during seeding. The unavailable state is documented in [`../03_AI_Prediction_APIs/AI_API_GUIDE.md`](../03_AI_Prediction_APIs/AI_API_GUIDE.md).
- **Upload and form-submission dialogs in mid-action.** The workflows themselves are covered by request-level tests in [`../09_Test_Results`](../09_Test_Results).
