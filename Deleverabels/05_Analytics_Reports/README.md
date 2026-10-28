# Analytics reports: genuine sample exports (synthetic data)

These files were downloaded from the application's real export endpoints by [`../tools/generate_reports.py`](../tools/generate_reports.py), against the unmodified backend in the disposable demo environment. The script signs in through `POST /api/auth/login`, follows the report catalog the API returns for each role, and saves each file under the name the server sends.

**Every value is synthetic demo data:** fictitious people, demo salaries in INR, generated attendance. [`report_manifest.json`](report_manifest.json) records the endpoint, parameters, HTTP status, row count (`X-Report-Rows`) and size for every file.

Open CSV in a spreadsheet or text editor, XLSX in Excel or LibreOffice, and PDF in any PDF viewer.

## `organization_hr/`: HR, organization scope (23 files)

| Report | Formats | Rows | Endpoint |
|---|---|---|---|
| Employee directory | CSV, XLSX, PDF | 14 | `GET /api/reports/employees` |
| Attendance (last 30 days) | CSV, XLSX, PDF | 296 | `GET /api/reports/attendance` |
| Leave (requests overlapping the next 45 days) | CSV, XLSX, PDF | 4 | `GET /api/reports/leave` |
| Payroll register (last month; audited export) | CSV, XLSX, PDF | 14 | `GET /api/reports/payroll` |
| AI risk indicators (aggregate counts only) | CSV, XLSX, PDF | 5 | `GET /api/reports/attrition` |
| Workforce trend (12 months) | CSV, XLSX, PDF | 12 | `GET /api/reports/workforce` |
| Activity (last 7 days) | CSV, XLSX, PDF | 890 | `GET /api/reports/activity` |
| Audit log (last 30 days) | CSV, PDF | 145 / 146 | `GET /api/audit-logs/export` |

The audit PDF has one more row than the CSV because exporting the CSV was itself audited.

## `department_manager/`: MANAGER, own department only (6 files)

These are the employee, attendance, leave, attrition, workforce and activity reports in CSV, automatically limited to the manager's department (department 2, Engineering).

**Payroll was refused:** the manager's payroll export attempt returned **403**, because payroll exports are ADMIN/HR only. The refusal is recorded in the manifest. That is the authorization working, not a missing file.

## Notes

- **Contents:** reports carry no contact details, leave reasons or individual risk scores. The attrition report holds aggregate counts only. The payroll register holds the synthetic demo salary figures.
- **Known cosmetic issue:** in the PDF exports, the subtitle line slightly overlaps the table header row. This is a rendering detail of the existing PDF writer (`app/services/report_export.py`); it was left unchanged in this task.
