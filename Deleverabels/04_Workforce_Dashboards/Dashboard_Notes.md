# Workforce dashboards: screenshot notes

These are genuine screenshots of the running application, rendered by Microsoft Edge (headless, 1440 px wide) from the production frontend build. The backend served them in the disposable demo environment described in [`../README.md`](../README.md#how-the-evidence-was-produced). All people, departments and figures are **synthetic**; emails use the reserved `example.com` domain.

How to read them:
- **Where these come from.** They are copies of captures in [`../06_UI_Screenshots_Demo/`](../06_UI_Screenshots_Demo).
- **Values are computed by the application.** Every number on screen was calculated by the application from the synthetic records; nothing was edited.
- **"Unavailable" and "insufficient data" are real outputs.** Where the screenshots show them, that is the application's genuine response for the demo data, not a placeholder.

| File | Route and role | What it shows | Unavailable or limited metrics |
|---|---|---|---|
| [01_main_workforce_dashboard_hr.png](Screenshots/01_main_workforce_dashboard_hr.png) | `/`, HR | 14 active employees; 4 departments; 2 leave requests awaiting review; 4 active tasks; department distribution; 30-day attendance (95.3%, 2 still checked in); 24-month workforce trend; risk alerts by priority and review-deadline state; stability indicator 75.9/100 (heuristic); recent activity; open tasks; latest payroll period | The trend line starts 11 months ago, because demo history begins there; earlier months are shown as not observed, never as zero |
| [02_department_dashboard_manager.png](Screenshots/02_department_dashboard_manager.png) | `/`, MANAGER (Engineering) | The same cards limited to the manager's department | Department scope only, by design |
| [03_employee_self_service_dashboard.png](Screenshots/03_employee_self_service_dashboard.png) | `/`, EMPLOYEE | Own record status; available leave days (26); own attendance (95.5%); no open tasks | Risk and organization metrics are not available to employees, by design |
| [04_employee_summary.png](Screenshots/04_employee_summary.png) | `/employees`, HR | Directory with search, department, status and sort filters | — |
| [05_department_summary.png](Screenshots/05_department_summary.png) | `/departments`, HR | Departments with employee counts and status | — |
| [06_attendance_summary.png](Screenshots/06_attendance_summary.png) | `/attendance` → "All records", HR | Team attendance summary and records with status filters | — |
| [07_leave_summary_review_queue.png](Screenshots/07_leave_summary_review_queue.png) | `/leave` → "Review queue", MANAGER | Pending leave requests in the manager's department, with approve and reject actions | — |
| [08_attrition_risk_overview.png](Screenshots/08_attrition_risk_overview.png) | `/risk`, HR | Alert counts (open, acknowledged, past deadline, created in 30 days); alert list with priority and review-deadline state; disclaimer | Numeric risk scores are deliberately never shown. The 2 alerts come from the **synthetic demo model** (see [`../03_AI_Prediction_APIs/AI_API_GUIDE.md`](../03_AI_Prediction_APIs/AI_API_GUIDE.md)) |
| [09_stability_forecast_department_analytics.png](Screenshots/09_stability_forecast_department_analytics.png) | `/analytics`, HR | Overview cards; monthly trend; stability indicator 75.9/100 with its component breakdown; 6-month linear headcount forecast with a backtest note; stability by department | Every department shows **insufficient data**: its average headcount (1–6) is below the minimum of 10, so no department score is produced. That is the documented safeguard, working as intended |
| [10_activity_monitoring_status.png](Screenshots/10_activity_monitoring_status.png) | `/activity`, HR | Monitoring status (snapshots available, latest activity, 2 active alerts); activity feed (890 records) with filters; manual check and opt-in auto-check | All attendance rows share one "recorded" time because the 90-day history was inserted in one step when the demo was seeded; "event time" shows each real check-in. The feed is polling-based, not live |

Not shown: the platform has no single "executive" dashboard beyond these pages, and none was invented.
