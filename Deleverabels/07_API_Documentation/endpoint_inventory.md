# Endpoint inventory

Generated from the application code (172 operations): methods and paths from `app.openapi()`, roles from each route's `require_roles` dependency (the same introspection the authorization-matrix test uses).

| Module | Method | Path | Roles | Summary | Request body | Success |
|---|---|---|---|---|---|---|
| health | GET | `/api/health/db` | public | Database Health | — | 200 |
| auth | POST | `/api/auth/register` | public | Register | application/json | 201 |
| auth | POST | `/api/auth/login` | public | Login | application/json | 200 |
| auth | GET | `/api/auth/me` | any signed-in user | Read Current User | — | 200 |
| auth | POST | `/api/auth/logout` | any signed-in user | Log out and revoke this access token | — | 200 |
| departments | POST | `/api/departments` | ADMIN, HR | Create Department | application/json | 201 |
| departments | GET | `/api/departments` | ADMIN, EMPLOYEE, HR, MANAGER | List Departments | — | 200 |
| departments | GET | `/api/departments/{department_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Department | — | 200 |
| departments | PATCH | `/api/departments/{department_id}` | ADMIN, HR | Update Department | application/json | 200 |
| departments | GET | `/api/departments/{department_id}/employees` | ADMIN, HR, MANAGER | List Department Employees | — | 200 |
| employees | POST | `/api/employees` | ADMIN, HR | Create Employee | application/json | 201 |
| employees | GET | `/api/employees` | ADMIN, EMPLOYEE, HR, MANAGER | List Employees | — | 200 |
| employees | GET | `/api/employees/search` | ADMIN, EMPLOYEE, HR, MANAGER | Search Employees | — | 200 |
| employees | GET | `/api/employees/{employee_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Employee | — | 200 |
| employees | PATCH | `/api/employees/{employee_id}` | ADMIN, HR | Update Employee | application/json | 200 |
| employees | PATCH | `/api/employees/{employee_id}/status` | ADMIN, HR | Change Employee Status | application/json | 200 |
| shifts | POST | `/api/shifts` | ADMIN, HR | Create Shift | application/json | 201 |
| shifts | GET | `/api/shifts` | ADMIN, EMPLOYEE, HR, MANAGER | List Shifts | — | 200 |
| shifts | GET | `/api/shifts/{shift_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Shift | — | 200 |
| shifts | PATCH | `/api/shifts/{shift_id}` | ADMIN, HR | Update Shift | application/json | 200 |
| shifts | PATCH | `/api/shifts/{shift_id}/status` | ADMIN, HR | Change Shift Status | application/json | 200 |
| attendance | POST | `/api/attendance/check-in` | ADMIN, EMPLOYEE, HR, MANAGER | Check In | application/json | 201 |
| attendance | POST | `/api/attendance/check-out` | ADMIN, EMPLOYEE, HR, MANAGER | Check Out | application/json | 200 |
| attendance | GET | `/api/attendance/me` | ADMIN, EMPLOYEE, HR, MANAGER | My Attendance | — | 200 |
| attendance | GET | `/api/attendance/me/summary` | ADMIN, EMPLOYEE, HR, MANAGER | My Summary | — | 200 |
| attendance | GET | `/api/attendance` | ADMIN, HR, MANAGER | Attendance Report | — | 200 |
| attendance | GET | `/api/attendance/summary` | ADMIN, HR, MANAGER | Attendance Report Summary | — | 200 |
| attendance | GET | `/api/attendance/{attendance_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Attendance | — | 200 |
| attendance | PATCH | `/api/attendance/{attendance_id}/correction` | ADMIN, HR | Correct Attendance | application/json | 200 |
| leave | POST | `/api/leave/types` | ADMIN, HR | Create Leave Type | application/json | 201 |
| leave | GET | `/api/leave/types` | ADMIN, EMPLOYEE, HR, MANAGER | List Leave Types | — | 200 |
| leave | PATCH | `/api/leave/types/{leave_type_id}` | ADMIN, HR | Update Leave Type | application/json | 200 |
| leave | POST | `/api/leave/policies` | ADMIN, HR | Create Leave Policy | application/json | 201 |
| leave | GET | `/api/leave/policies` | ADMIN, EMPLOYEE, HR, MANAGER | List Leave Policies | — | 200 |
| leave | PATCH | `/api/leave/policies/{policy_id}` | ADMIN, HR | Update Leave Policy | application/json | 200 |
| leave | POST | `/api/leave/requests` | ADMIN, EMPLOYEE, HR, MANAGER | Create Leave Request | application/json | 201 |
| leave | GET | `/api/leave/requests/me` | ADMIN, EMPLOYEE, HR, MANAGER | My Leave Requests | — | 200 |
| leave | GET | `/api/leave/balances/me` | ADMIN, EMPLOYEE, HR, MANAGER | My Leave Balances | — | 200 |
| leave | GET | `/api/leave/requests` | ADMIN, HR, MANAGER | Review Queue | — | 200 |
| leave | GET | `/api/leave/requests/{request_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Leave Request | — | 200 |
| leave | POST | `/api/leave/requests/{request_id}/approve` | ADMIN, HR, MANAGER | Approve Leave Request | application/json | 200 |
| leave | POST | `/api/leave/requests/{request_id}/reject` | ADMIN, HR, MANAGER | Reject Leave Request | application/json | 200 |
| leave | POST | `/api/leave/requests/{request_id}/cancel` | ADMIN, EMPLOYEE, HR, MANAGER | Cancel Leave Request | application/json | 200 |
| leave | GET | `/api/leave/conflicts` | ADMIN, HR, MANAGER | Leave Attendance Conflicts | — | 200 |
| notifications | GET | `/api/notifications/me` | ADMIN, EMPLOYEE, HR, MANAGER | My Notifications | — | 200 |
| notifications | POST | `/api/notifications/{notification_id}/read` | ADMIN, EMPLOYEE, HR, MANAGER | Mark Notification Read | — | 200 |
| payroll | POST | `/api/payroll/components` | ADMIN, HR | Create Component | application/json | 201 |
| payroll | GET | `/api/payroll/components` | ADMIN, HR | List Components | — | 200 |
| payroll | PATCH | `/api/payroll/components/{component_id}` | ADMIN, HR | Update Component | application/json | 200 |
| payroll | POST | `/api/payroll/profiles` | ADMIN, HR | Create Profile | application/json | 201 |
| payroll | GET | `/api/payroll/profiles` | ADMIN, HR | List Profiles | — | 200 |
| payroll | GET | `/api/payroll/profiles/{profile_id}` | ADMIN, HR | Get Profile | — | 200 |
| payroll | PATCH | `/api/payroll/profiles/{profile_id}` | ADMIN, HR | Update Profile | application/json | 200 |
| payroll | POST | `/api/payroll/periods` | ADMIN, HR | Create Period | application/json | 201 |
| payroll | GET | `/api/payroll/periods` | ADMIN, HR | List Periods | — | 200 |
| payroll | GET | `/api/payroll/periods/{period_id}` | ADMIN, HR | Get Period | — | 200 |
| payroll | POST | `/api/payroll/periods/{period_id}/calculate` | ADMIN, HR | Calculate Period | application/json | 200 |
| payroll | GET | `/api/payroll/periods/{period_id}/records` | ADMIN, HR | List Period Records | — | 200 |
| payroll | GET | `/api/payroll/records/{record_id}` | ADMIN, HR | Get Record | — | 200 |
| payroll | POST | `/api/payroll/records/{record_id}/approve` | ADMIN, HR | Approve Record | — | 200 |
| payroll | POST | `/api/payroll/records/{record_id}/mark-paid` | ADMIN, HR | Mark Record Paid | application/json | 200 |
| payroll | GET | `/api/payroll/me` | ADMIN, EMPLOYEE, HR, MANAGER | My Payroll | — | 200 |
| payroll | GET | `/api/payroll/me/{record_id}/payslip` | ADMIN, EMPLOYEE, HR, MANAGER | My Payslip | — | 200 |
| attrition | POST | `/api/ai/attrition/predict/{employee_id}` | ADMIN, HR, MANAGER | Predict Employee | — | 201 |
| attrition | POST | `/api/ai/attrition/predict-batch` | ADMIN, HR, MANAGER | Predict Batch | application/json | 200 |
| attrition | GET | `/api/ai/attrition/history/{employee_id}` | ADMIN, HR, MANAGER | Prediction History | — | 200 |
| attrition | GET | `/api/ai/attrition/summary` | ADMIN, HR | Risk Summary | — | 200 |
| attrition | GET | `/api/ai/attrition/model-status` | ADMIN, HR | Model Status | — | 200 |
| attrition | POST | `/api/ai/attrition/train` | ADMIN | Train Model | application/json | 201 |
| attrition | POST | `/api/ai/attrition/models/{model_version}/activate` | ADMIN | Activate Model | — | 200 |
| datasets | POST | `/api/datasets` | ADMIN | Upload Dataset | multipart/form-data | 201 |
| datasets | POST | `/api/datasets/{dataset_id}/versions` | ADMIN | Create Version | multipart/form-data | 201 |
| datasets | GET | `/api/datasets` | ADMIN, HR | List Datasets | — | 200 |
| datasets | GET | `/api/datasets/{dataset_id}` | ADMIN, HR | Get Dataset | — | 200 |
| datasets | GET | `/api/datasets/{dataset_id}/versions` | ADMIN, HR | List Versions | — | 200 |
| datasets | POST | `/api/datasets/{dataset_id}/validate` | ADMIN | Validate Version | — | 200 |
| datasets | GET | `/api/datasets/{dataset_id}/validation` | ADMIN, HR | Get Validation | — | 200 |
| datasets | POST | `/api/datasets/{dataset_id}/archive` | ADMIN | Archive Dataset | — | 200 |
| datasets | GET | `/api/datasets/{dataset_id}/download` | ADMIN | Download Version | — | 200 |
| risk_monitoring | POST | `/api/risk-monitoring/run` | ADMIN, HR | Run Monitoring | application/json | 200 |
| risk_monitoring | GET | `/api/risk-monitoring/alerts` | ADMIN, HR, MANAGER | List Alerts | — | 200 |
| risk_monitoring | GET | `/api/risk-monitoring/alerts/{alert_id}` | ADMIN, HR, MANAGER | Get Alert | — | 200 |
| risk_monitoring | POST | `/api/risk-monitoring/alerts/{alert_id}/acknowledge` | ADMIN, HR, MANAGER | Acknowledge Alert | application/json | 200 |
| risk_monitoring | POST | `/api/risk-monitoring/alerts/{alert_id}/resolve` | ADMIN, HR, MANAGER | Resolve Alert | application/json | 200 |
| risk_monitoring | POST | `/api/risk-monitoring/alerts/{alert_id}/assign` | ADMIN, HR | Assign Alert | application/json | 200 |
| risk_monitoring | GET | `/api/risk-monitoring/employees/{employee_id}/history` | ADMIN, HR, MANAGER | Employee History | — | 200 |
| risk_monitoring | GET | `/api/risk-monitoring/summary` | ADMIN, HR, MANAGER | Risk Summary | — | 200 |
| risk_monitoring | POST | `/api/risk-monitoring/escalations/evaluate` | ADMIN, HR, MANAGER | Evaluate Escalations | application/json | 200 |
| workforce_analytics | POST | `/api/workforce-analytics/snapshots/capture` | ADMIN, HR | Capture Snapshot | application/json | 201 |
| workforce_analytics | GET | `/api/workforce-analytics/snapshots` | ADMIN, HR | List Snapshots | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/overview` | ADMIN, HR, MANAGER | Overview | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/trends` | ADMIN, HR, MANAGER | Trends | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/forecast` | ADMIN, HR, MANAGER | Forecast | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/stability` | ADMIN, HR, MANAGER | Stability | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/retention` | ADMIN, HR, MANAGER | Retention | — | 200 |
| workforce_analytics | GET | `/api/workforce-analytics/departments/{department_id}` | ADMIN, HR, MANAGER | Department Detail | — | 200 |
| interventions | GET | `/api/interventions/categories` | ADMIN, HR, MANAGER | List Categories | — | 200 |
| interventions | GET | `/api/interventions/summary` | ADMIN, HR, MANAGER | Summary | — | 200 |
| interventions | POST | `/api/interventions` | ADMIN, HR, MANAGER | Create Intervention | application/json | 201 |
| interventions | GET | `/api/interventions` | ADMIN, HR, MANAGER | List Interventions | — | 200 |
| interventions | GET | `/api/interventions/{intervention_id}` | ADMIN, HR, MANAGER | Get Intervention | — | 200 |
| interventions | PATCH | `/api/interventions/{intervention_id}` | ADMIN, HR, MANAGER | Update Intervention | application/json | 200 |
| interventions | POST | `/api/interventions/{intervention_id}/assign` | ADMIN, HR, MANAGER | Assign Intervention | application/json | 200 |
| interventions | POST | `/api/interventions/{intervention_id}/start` | ADMIN, HR, MANAGER | Start Intervention | application/json | 200 |
| interventions | POST | `/api/interventions/{intervention_id}/complete` | ADMIN, HR, MANAGER | Complete Intervention | application/json | 200 |
| interventions | POST | `/api/interventions/{intervention_id}/cancel` | ADMIN, HR, MANAGER | Cancel Intervention | application/json | 200 |
| interventions | POST | `/api/interventions/{intervention_id}/notes` | ADMIN, HR, MANAGER | Add Note | application/json | 201 |
| interventions | GET | `/api/interventions/{intervention_id}/history` | ADMIN, HR, MANAGER | History | — | 200 |
| recommendations | GET | `/api/recommendations/summary` | ADMIN, HR, MANAGER | Summary | — | 200 |
| recommendations | POST | `/api/recommendations/evaluate` | ADMIN, HR, MANAGER | Evaluate | application/json | 200 |
| recommendations | GET | `/api/recommendations` | ADMIN, HR, MANAGER | List Recommendations | — | 200 |
| recommendations | GET | `/api/recommendations/{recommendation_id}` | ADMIN, HR, MANAGER | Get Recommendation | — | 200 |
| recommendations | POST | `/api/recommendations/{recommendation_id}/accept` | ADMIN, HR, MANAGER | Accept | application/json | 200 |
| recommendations | POST | `/api/recommendations/{recommendation_id}/dismiss` | ADMIN, HR, MANAGER | Dismiss | application/json | 200 |
| recommendations | POST | `/api/recommendations/{recommendation_id}/complete` | ADMIN, HR, MANAGER | Complete | application/json | 200 |
| recommendations | POST | `/api/recommendations/{recommendation_id}/create-intervention` | ADMIN, HR, MANAGER | Create Intervention | application/json | 200 |
| recommendations | GET | `/api/recommendations/{recommendation_id}/history` | ADMIN, HR, MANAGER | Recommendation History | — | 200 |
| recommendations | POST | `/api/recommendations/{recommendation_id}/link-task` | ADMIN, HR, MANAGER | Link Task | application/json | 200 |
| workforce_activity | GET | `/api/workforce-activity/sources` | ADMIN, HR, MANAGER | Sources | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/feed` | ADMIN, HR, MANAGER | Activity Feed | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/summary` | ADMIN, HR, MANAGER | Activity Summary | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/updates` | ADMIN, HR, MANAGER | Activity Updates | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/status` | ADMIN, HR, MANAGER | Monitoring Status | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/signals` | ADMIN, HR, MANAGER | Operational Signals | — | 200 |
| workforce_activity | GET | `/api/workforce-activity/departments/{department_id}` | ADMIN, HR, MANAGER | Department Activity | — | 200 |
| reports | GET | `/api/reports/catalog` | any signed-in user | Catalog | — | 200 |
| reports | GET | `/api/reports/employees` | ADMIN, HR, MANAGER | Employees Report | — | 200 |
| reports | GET | `/api/reports/attendance` | ADMIN, HR, MANAGER | Attendance Report | — | 200 |
| reports | GET | `/api/reports/leave` | ADMIN, HR, MANAGER | Leave Report | — | 200 |
| reports | GET | `/api/reports/payroll` | ADMIN, HR | Payroll Report | — | 200 |
| reports | GET | `/api/reports/attrition` | ADMIN, HR, MANAGER | Attrition Report | — | 200 |
| reports | GET | `/api/reports/workforce` | ADMIN, HR, MANAGER | Workforce Report | — | 200 |
| reports | GET | `/api/reports/activity` | ADMIN, HR, MANAGER | Activity Report | — | 200 |
| audit_logs | GET | `/api/audit-logs/catalog` | ADMIN, HR, MANAGER | Audit Catalog Endpoint | — | 200 |
| audit_logs | GET | `/api/audit-logs` | ADMIN, HR, MANAGER | List Audit Logs | — | 200 |
| audit_logs | GET | `/api/audit-logs/export` | ADMIN, HR | Export Audit Logs | — | 200 |
| tasks | GET | `/api/tasks/summary` | ADMIN, EMPLOYEE, HR, MANAGER | Task Summary | — | 200 |
| tasks | POST | `/api/tasks` | ADMIN, HR, MANAGER | Create Task | application/json | 201 |
| tasks | GET | `/api/tasks` | ADMIN, EMPLOYEE, HR, MANAGER | List Tasks | — | 200 |
| tasks | GET | `/api/tasks/{task_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Task | — | 200 |
| tasks | PATCH | `/api/tasks/{task_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Update Task | application/json | 200 |
| tasks | POST | `/api/tasks/{task_id}/assign` | ADMIN, EMPLOYEE, HR, MANAGER | Assign Task | application/json | 200 |
| tasks | POST | `/api/tasks/{task_id}/transition` | ADMIN, EMPLOYEE, HR, MANAGER | Transition Task | application/json | 200 |
| tasks | GET | `/api/tasks/{task_id}/history` | ADMIN, EMPLOYEE, HR, MANAGER | Task History | — | 200 |
| performance | GET | `/api/performance/summary` | ADMIN, EMPLOYEE, HR, MANAGER | Performance Summary | — | 200 |
| performance | POST | `/api/performance/cycles` | ADMIN, HR | Create Cycle | application/json | 201 |
| performance | GET | `/api/performance/cycles` | ADMIN, EMPLOYEE, HR, MANAGER | List Cycles | — | 200 |
| performance | GET | `/api/performance/cycles/{cycle_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Cycle | — | 200 |
| performance | PATCH | `/api/performance/cycles/{cycle_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Update Cycle | application/json | 200 |
| performance | POST | `/api/performance/cycles/{cycle_id}/transition` | ADMIN, EMPLOYEE, HR, MANAGER | Transition Cycle | application/json | 200 |
| performance | POST | `/api/performance/goals` | ADMIN, EMPLOYEE, HR, MANAGER | Create Goal | application/json | 201 |
| performance | GET | `/api/performance/goals` | ADMIN, EMPLOYEE, HR, MANAGER | List Goals | — | 200 |
| performance | GET | `/api/performance/goals/{goal_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Goal | — | 200 |
| performance | GET | `/api/performance/goals/{goal_id}/history` | ADMIN, EMPLOYEE, HR, MANAGER | Goal History | — | 200 |
| performance | PATCH | `/api/performance/goals/{goal_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Update Goal | application/json | 200 |
| performance | POST | `/api/performance/reviews` | ADMIN, EMPLOYEE, HR, MANAGER | Create Review | application/json | 201 |
| performance | GET | `/api/performance/reviews` | ADMIN, EMPLOYEE, HR, MANAGER | List Reviews | — | 200 |
| performance | GET | `/api/performance/reviews/{review_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Get Review | — | 200 |
| performance | GET | `/api/performance/reviews/{review_id}/history` | ADMIN, EMPLOYEE, HR, MANAGER | Review History | — | 200 |
| performance | PATCH | `/api/performance/reviews/{review_id}` | ADMIN, EMPLOYEE, HR, MANAGER | Update Review | application/json | 200 |
| performance | POST | `/api/performance/reviews/{review_id}/submit-self-assessment` | ADMIN, EMPLOYEE, HR, MANAGER | Submit Self Assessment | application/json | 200 |
| performance | POST | `/api/performance/reviews/{review_id}/submit-manager-review` | ADMIN, EMPLOYEE, HR, MANAGER | Submit Manager Review | application/json | 200 |
| performance | POST | `/api/performance/reviews/{review_id}/finalize` | ADMIN, EMPLOYEE, HR, MANAGER | Finalize Review | application/json | 200 |
| performance | POST | `/api/performance/reviews/{review_id}/reopen` | ADMIN, EMPLOYEE, HR, MANAGER | Reopen Review | application/json | 200 |
| performance | POST | `/api/performance/reviews/{review_id}/cancel` | ADMIN, EMPLOYEE, HR, MANAGER | Cancel Review | application/json | 200 |
| search | GET | `/api/search` | any signed-in user | Search | — | 200 |
| users | GET | `/api/users` | ADMIN | List Users | — | 200 |
| users | GET | `/api/users/{user_id}` | ADMIN | Get User | — | 200 |
| users | PATCH | `/api/users/{user_id}/status` | ADMIN | Change User Status | application/json | 200 |
| users | PATCH | `/api/users/{user_id}/role` | ADMIN | Change User Role | application/json | 200 |
| users | POST | `/api/users/{user_id}/revoke-sessions` | ADMIN | Revoke User Sessions | — | 200 |
| main | GET | `/api/health` | public | Health Check | — | 200 |
