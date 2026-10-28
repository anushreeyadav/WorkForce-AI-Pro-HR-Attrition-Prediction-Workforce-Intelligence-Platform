"""Catalog of every audit action the application writes: category, entity type and a short description.

Stored action names are NOT renamed (existing rows and consumers keep working). The catalog adds a normalized
`category` for filtering and display, and is the allow-list for the `action` filter of the audit query API.
A test scans the source code so that every emitted action must appear here.
"""

ACTIONS: dict[str, tuple[str, str, str]] = {
    # action: (category, entity_type, description)
    # Authentication (no credentials, tokens or submitted emails are ever recorded)
    "auth.registered": ("auth", "user", "Self-service account registered"),
    "auth.login_succeeded": ("auth", "user", "Successful sign-in"),
    "auth.login_failed": ("auth", "user", "Failed sign-in (reason code only)"),
    "auth.logout": ("auth", "user", "Signed out; that access token was revoked"),
    "auth.password_changed": ("auth", "user", "Own password changed; every session of the account ended"),
    "auth.password_change_failed": ("auth", "user", "Password change refused: wrong current password"),
    # Account administration (ADMIN only; never passwords, tokens or token ids)
    "user.deactivated": ("account", "user", "Login account deactivated; all its sessions ended"),
    "user.reactivated": ("account", "user", "Login account reactivated"),
    "user.role_changed": ("account", "user", "Role changed; all sessions of the account ended"),
    "user.sessions_revoked": ("account", "user", "All sessions of the account ended"),
    # People and organization
    "employee.created": ("employee", "employee", "Employee record created"),
    "employee.updated": ("employee", "employee", "Employee record updated"),
    "employee.status_changed": ("employee", "employee", "Employment status changed"),
    "department.created": ("department", "department", "Department created"),
    "department.updated": ("department", "department", "Department updated"),
    "shift.created": ("shift", "shift", "Shift created"),
    "shift.updated": ("shift", "shift", "Shift updated"),
    "shift.status_changed": ("shift", "shift", "Shift activated or deactivated"),
    # Attendance and leave
    "attendance.corrected": ("attendance", "attendance", "Attendance record corrected"),
    "leave.requested": ("leave", "leave_request", "Leave requested"),
    "leave.approved": ("leave", "leave_request", "Leave approved"),
    "leave.rejected": ("leave", "leave_request", "Leave rejected"),
    "leave.cancelled": ("leave", "leave_request", "Leave cancelled"),
    "leave_type.created": ("leave", "leave_type", "Leave type created"),
    "leave_type.updated": ("leave", "leave_type", "Leave type updated"),
    "leave_policy.created": ("leave", "leave_policy", "Leave policy created"),
    "leave_policy.updated": ("leave", "leave_policy", "Leave policy updated"),
    # Payroll (amounts are never recorded)
    "payroll.component.created": ("payroll", "salary_component", "Salary component created"),
    "payroll.component.updated": ("payroll", "salary_component", "Salary component updated"),
    "payroll.profile.created": ("payroll", "payroll_profile", "Payroll profile created"),
    "payroll.profile.updated": ("payroll", "payroll_profile", "Payroll profile updated (field names only)"),
    "payroll.period.created": ("payroll", "payroll_period", "Payroll period created"),
    "payroll.period.calculated": ("payroll", "payroll_period", "Payroll period calculated"),
    "payroll.record.approved": ("payroll", "payroll_record", "Payroll record approved"),
    "payroll.record.paid": ("payroll", "payroll_record", "Payroll record marked paid"),
    "payroll.report_exported": ("payroll", "payroll_period", "Payroll register exported"),
    # Datasets and AI model lifecycle
    "dataset.uploaded": ("dataset", "dataset", "Dataset uploaded"),
    "dataset.version_created": ("dataset", "dataset", "Dataset version added"),
    "dataset.validated": ("dataset", "dataset", "Dataset version validated"),
    "dataset.rejected": ("dataset", "dataset", "Dataset version failed validation"),
    "dataset.archived": ("dataset", "dataset", "Dataset archived"),
    "dataset.downloaded": ("dataset", "dataset", "Dataset version downloaded"),
    "ai.attrition.predicted": ("ai", "attrition_prediction", "Risk estimate stored for one employee"),
    "ai.attrition.batch_predicted": ("ai", "attrition_batch", "Risk estimates stored for a batch"),
    "ai.attrition.prediction_unavailable": ("ai", "attrition_model", "Prediction requested without a usable model"),
    "ai.attrition.model_trained": ("ai", "attrition_model", "Model trained"),
    "ai.attrition.training_rejected": ("ai", "dataset", "Training refused for a dataset"),
    "ai.attrition.model_activated": ("ai", "attrition_model", "Model activated"),
    "ai.attrition.activation_refused": ("ai", "attrition_model", "Activation refused by the activation-time checks"),
    # Risk monitoring and escalation
    "risk.monitoring_run": ("risk", "risk_monitoring", "Monitoring run"),
    "risk.alert_created": ("risk", "risk_alert", "Risk alert created"),
    "risk.alert_escalated": ("risk", "risk_alert", "Risk alert priority raised by monitoring"),
    "risk.alert_acknowledged": ("risk", "risk_alert", "Risk alert acknowledged"),
    "risk.alert_resolved": ("risk", "risk_alert", "Risk alert resolved"),
    "risk.alert_dismissed": ("risk", "risk_alert", "Risk alert dismissed"),
    "risk.alert_assigned": ("risk", "risk_alert", "Risk alert assigned"),
    "risk.escalation_evaluated": ("risk", "risk_monitoring", "Review-deadline evaluation run"),
    "risk.alert_review_escalated": ("risk", "risk_alert", "Overdue/approaching review escalated to reviewers"),
    # Interventions and recommendations
    "intervention.created": ("intervention", "intervention", "Intervention created"),
    "intervention.updated": ("intervention", "intervention", "Intervention updated"),
    "intervention.assigned": ("intervention", "intervention", "Intervention assigned"),
    "intervention.started": ("intervention", "intervention", "Intervention started"),
    "intervention.completed": ("intervention", "intervention", "Intervention completed"),
    "intervention.cancelled": ("intervention", "intervention", "Intervention cancelled"),
    "intervention.note_added": ("intervention", "intervention", "Follow-up note added (text never recorded)"),
    "recommendation.evaluated": ("recommendation", "recommendation", "Recommendation rules evaluated"),
    "recommendation.accepted": ("recommendation", "recommendation", "Recommendation accepted"),
    "recommendation.dismissed": ("recommendation", "recommendation", "Recommendation dismissed"),
    "recommendation.completed": ("recommendation", "recommendation", "Recommendation completed"),
    "recommendation.intervention_linked": ("recommendation", "recommendation", "Intervention linked to a recommendation"),
    "recommendation.generated": ("recommendation", "recommendation", "Recommendation generated (rules-v2)"),
    "recommendation.superseded": ("recommendation", "recommendation", "Recommendation superseded by newer evidence or no longer current (rules-v2)"),
    "recommendation.task_linked": ("recommendation", "recommendation", "Existing task linked to a recommendation"),
    # Workforce tasks (titles, descriptions, summaries, block details and cancellation reasons are never recorded)
    "task.created": ("task", "task", "Task created"),
    "task.updated": ("task", "task", "Task details updated (field names only)"),
    "task.assigned": ("task", "task", "Task assigned or reassigned"),
    "task.started": ("task", "task", "Task started"),
    "task.blocked": ("task", "task", "Task blocked (reason code only)"),
    "task.unblocked": ("task", "task", "Task unblocked"),
    "task.completed": ("task", "task", "Task completed"),
    "task.cancelled": ("task", "task", "Task cancelled"),
    "task.reopened": ("task", "task", "Task reopened"),
    # Performance management (review text, goal text, feedback, notes, reasons and rating values are never recorded)
    "performance.cycle_created": ("performance", "performance_cycle", "Review cycle created"),
    "performance.cycle_updated": ("performance", "performance_cycle", "Review cycle updated (field names only)"),
    "performance.cycle_opened": ("performance", "performance_cycle", "Review cycle opened"),
    "performance.cycle_reviews_started": ("performance", "performance_cycle", "Review cycle moved to manager reviews"),
    "performance.cycle_completed": ("performance", "performance_cycle", "Review cycle completed"),
    "performance.cycle_cancelled": ("performance", "performance_cycle", "Review cycle cancelled"),
    "performance.goal_created": ("performance", "performance_goal", "Goal created"),
    "performance.goal_updated": ("performance", "performance_goal", "Goal updated (fields, status, progress)"),
    "performance.review_created": ("performance", "performance_review", "Performance review created"),
    "performance.review_updated": ("performance", "performance_review", "Review draft updated (field names only)"),
    "performance.review_reassigned": ("performance", "performance_review", "Reviewer reassigned"),
    "performance.self_assessment_submitted": ("performance", "performance_review", "Self-assessment submitted"),
    "performance.manager_review_submitted": ("performance", "performance_review", "Manager evaluation submitted"),
    "performance.review_finalized": ("performance", "performance_review", "Review finalized"),
    "performance.review_reopened": ("performance", "performance_review", "Finalized review reopened for correction"),
    "performance.review_cancelled": ("performance", "performance_review", "Review cancelled"),
    # Workforce analytics and compliance
    "workforce.snapshot_captured": ("workforce", "workforce_snapshot", "Workforce snapshot captured"),
    "audit.report_exported": ("audit", "audit_log", "Audit log exported"),
}

CATEGORIES = tuple(sorted({category for category, _, _ in ACTIONS.values()}))
ENTITY_TYPES = tuple(sorted({entity for _, entity, _ in ACTIONS.values()}))


def category_of(action: str) -> str:
    """Catalog category, or the first dot segment for actions written before the catalog existed."""
    return ACTIONS[action][0] if action in ACTIONS else action.split(".", 1)[0]


def description_of(action: str) -> str | None:
    return ACTIONS[action][2] if action in ACTIONS else None
