"""Single source of truth for which roles may do what. Routers reference these, never literal role lists.

Authorization has two layers, and both must pass:
1. Role: `require_roles(*permissions.X)` on every route, checked against the database user loaded for the token.
2. Scope and object: which records the caller may see or change, enforced in queries by the shared helpers
   (employee_access.employee_scope/apply_scope, workforce_analytics.resolve_department, and the module `visible`
   helpers). MANAGER scope is the department of their own CURRENT employee record (not INACTIVE); out-of-scope
   records return 404, visible records the caller may not change return 403.
See docs/SECURITY_RBAC.md (project root) for the role model and the full route inventory.
"""

from app.models.enums import UserRole

ALL_ROLES = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE)

# Departments
DEPARTMENT_WRITE = (UserRole.ADMIN, UserRole.HR)
DEPARTMENT_READ = ALL_ROLES  # the department directory is visible to every signed-in user
DEPARTMENT_MEMBERS_READ = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)  # managers: own department only

# Employees
EMPLOYEE_WRITE = (UserRole.ADMIN, UserRole.HR)
# Every role may call the read endpoints, but results are scoped (see app.services.employee_access):
# ADMIN/HR see everyone, MANAGER sees their own department, EMPLOYEE sees only their own record.
EMPLOYEE_READ = ALL_ROLES
EMPLOYEE_READ_UNRESTRICTED = frozenset({UserRole.ADMIN, UserRole.HR})

# Shifts
SHIFT_WRITE = (UserRole.ADMIN, UserRole.HR)
SHIFT_READ = ALL_ROLES  # other roles only ever see active shifts
SHIFT_READ_INACTIVE = frozenset({UserRole.ADMIN, UserRole.HR})

# Attendance
ATTENDANCE_SELF_SERVICE = ALL_ROLES  # requires a linked employee record
# Reports are scoped like employee reads: ADMIN/HR organization-wide, MANAGER own department.
ATTENDANCE_REPORT = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)
ATTENDANCE_DETAIL = ALL_ROLES  # EMPLOYEE: own records only
ATTENDANCE_CORRECT = (UserRole.ADMIN, UserRole.HR)

# Leave
LEAVE_ADMIN = (UserRole.ADMIN, UserRole.HR)  # manage leave types and policies
LEAVE_CATALOG_READ = ALL_ROLES  # other roles see only active types / applicable active policies
LEAVE_CATALOG_READ_ALL = frozenset({UserRole.ADMIN, UserRole.HR})
LEAVE_SELF_SERVICE = ALL_ROLES  # requires a linked employee record
# Reviewers: ADMIN/HR organization-wide, MANAGER own department (never their own requests).
LEAVE_REVIEW = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)
# May cancel other employees' requests (pending, or approved at any time) with a comment.
LEAVE_CANCEL_ANY = frozenset({UserRole.ADMIN, UserRole.HR})

# Notifications: every user reads only their own.
NOTIFICATIONS_READ = ALL_ROLES

# Payroll. MANAGER has no payroll administration or organization-wide access (salary data is not
# needed for any manager workflow); everyone can read their own APPROVED/PAID payroll and payslips.
PAYROLL_ADMIN = (UserRole.ADMIN, UserRole.HR)
PAYROLL_SELF_SERVICE = ALL_ROLES  # requires a linked employee record

# AI attrition risk. Decision support for HR only: employees never see risk scores (not even their own).
ATTRITION_PREDICT = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)  # MANAGER: own department only
ATTRITION_ORG_WIDE = (UserRole.ADMIN, UserRole.HR)  # summaries and model status
ATTRITION_MODEL_ADMIN = (UserRole.ADMIN,)  # training and activation

# Workforce datasets. ADMIN manages them (upload, validate, version, archive, download). HR may read
# metadata and validation reports (which contain no row values) for governance review. MANAGER and
# EMPLOYEE have no access.
DATASET_ADMIN = (UserRole.ADMIN,)
DATASET_READ = (UserRole.ADMIN, UserRole.HR)

# Risk monitoring. Alerts are HR review aids. ADMIN/HR organization-wide; MANAGER only their own
# department; nobody sees alerts about themselves; EMPLOYEE has no access.
RISK_MONITOR_RUN = (UserRole.ADMIN, UserRole.HR)
RISK_ALERT_REVIEW = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)
RISK_ALERT_ASSIGN = (UserRole.ADMIN, UserRole.HR)

# Workforce analytics (aggregates only). ADMIN/HR: organization-wide and snapshot management.
# MANAGER: their own department only. EMPLOYEE: no access.
WORKFORCE_SNAPSHOT_ADMIN = (UserRole.ADMIN, UserRole.HR)
WORKFORCE_ANALYTICS_READ = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# HR interventions (internal records). ADMIN/HR organization-wide; MANAGER own department; nobody handles
# or sees interventions about themselves; EMPLOYEE has no access.
INTERVENTION_MANAGE = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# Rule-based recommendations. Same audience as risk alerts: ADMIN/HR organization-wide, MANAGER own
# department (never about themselves), EMPLOYEE no access.
RECOMMENDATION_REVIEW = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# Workforce activity feed and operational signals. ADMIN/HR organization-wide; MANAGER own department;
# risk/intervention/recommendation items about the caller are never shown; EMPLOYEE has no access.
ACTIVITY_READ = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# Risk-alert review deadlines: evaluate escalations. ADMIN/HR organization-wide; MANAGER own department only
# (alerts about the caller are never included). Escalation notifications always go to ADMIN/HR reviewers.
RISK_ESCALATION_EVALUATE = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# Reports and exports. Each report reuses the permission of the module it comes from; scope rules are the same
# (MANAGER pinned to their own department, other departments 404). EMPLOYEE has no report access (their own
# records stay in the self-service endpoints). Payroll exports are ADMIN/HR only, like payroll administration.
REPORT_CATALOG_READ = ALL_ROLES  # the catalog lists only the reports the caller may run
REPORTS = {
    "employees": (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER),
    "attendance": ATTENDANCE_REPORT,
    "leave": LEAVE_REVIEW,
    "payroll": PAYROLL_ADMIN,
    "attrition": WORKFORCE_ANALYTICS_READ,  # aggregates only, as in the workforce-analytics AI risk indicators
    "workforce": WORKFORCE_ANALYTICS_READ,
    "activity": ACTIVITY_READ,
}

# Audit logs (append-only; no API edits or deletes them). ADMIN/HR: organization-wide query and export.
# MANAGER: query only, limited to records whose entity belongs to their department (see audit_query). EMPLOYEE: none.
AUDIT_READ = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)
AUDIT_EXPORT = (UserRole.ADMIN, UserRole.HR)

# Workforce tasks. Everyone may read the tasks visible to them (EMPLOYEE: tasks assigned to them) and perform the
# assignee transitions (start, block, unblock, complete). Creating, editing, (re)assigning, cancelling and reopening
# need a task manager: ADMIN/HR for any task, MANAGER for tasks of their own department.
TASK_READ = ALL_ROLES
TASK_MANAGE = (UserRole.ADMIN, UserRole.HR, UserRole.MANAGER)

# Performance management. Everyone reads what is visible to them (EMPLOYEE: own goals and reviews; MANAGER: their
# department; ADMIN/HR: everything) and acts by record role (owner, reviewer, employee's manager). Being the assigned
# reviewer adds no visibility: a reviewer must stay eligible (ADMIN/HR, or MANAGER of the employee's department). Review-cycle administration, finalization, correction and cancellation are ADMIN/HR only.
PERFORMANCE_READ = ALL_ROLES
PERFORMANCE_ADMIN = (UserRole.ADMIN, UserRole.HR)

# Login-account administration (list, deactivate/reactivate, change role, end sessions). ADMIN only: HR manages
# employee records, not logins, and cannot grant itself or anyone else a role. Nobody changes their own status or
# role, and the last active ADMIN cannot be deactivated or demoted.
USER_ADMIN = (UserRole.ADMIN,)
