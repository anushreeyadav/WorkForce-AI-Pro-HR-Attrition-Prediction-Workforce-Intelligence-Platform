from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    HR = "HR"
    MANAGER = "MANAGER"
    EMPLOYEE = "EMPLOYEE"


class EmploymentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ON_LEAVE = "ON_LEAVE"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    LATE = "LATE"
    HALF_DAY = "HALF_DAY"
    ABSENT = "ABSENT"
    ON_LEAVE = "ON_LEAVE"


class LeaveRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SalaryComponentKind(StrEnum):
    EARNING = "EARNING"
    DEDUCTION = "DEDUCTION"
    TAX = "TAX"


class CalculationMethod(StrEnum):
    FIXED = "FIXED"
    PERCENT_OF_BASE = "PERCENT_OF_BASE"
    PERCENT_OF_GROSS = "PERCENT_OF_GROSS"  # deductions and tax only (earnings would be circular)


class PayFrequency(StrEnum):
    MONTHLY = "MONTHLY"


class PayrollStatus(StrEnum):
    CALCULATED = "CALCULATED"
    APPROVED = "APPROVED"
    PAID = "PAID"


class RiskCategory(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ModelVersionStatus(StrEnum):
    CANDIDATE = "CANDIDATE"  # passed the evaluation gate; not serving predictions
    ACTIVE = "ACTIVE"  # the single model used for predictions
    REJECTED = "REJECTED"  # failed the evaluation gate; can never be activated
    RETIRED = "RETIRED"  # previously active


class DatasetPurpose(StrEnum):
    ATTRITION_TRAINING = "ATTRITION_TRAINING"


class DatasetStatus(StrEnum):
    UPLOADED = "UPLOADED"  # stored and structurally parsed; not yet validated against the feature schema
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"  # passed schema validation (training eligibility is recorded separately)
    REJECTED = "REJECTED"  # fatal validation errors; cannot be used for training
    ARCHIVED = "ARCHIVED"  # retired; kept for traceability, never used for new training


class RiskEventType(StrEnum):
    THRESHOLD_CROSSED = "THRESHOLD_CROSSED"
    SCORE_INCREASE = "SCORE_INCREASE"
    SCORE_DECREASE = "SCORE_DECREASE"
    STALE_PREDICTION = "STALE_PREDICTION"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class EscalationStage(StrEnum):
    """Review-deadline stage of an unacknowledged OPEN risk alert (see app/services/alert_sla.py)."""

    APPROACHING = "APPROACHING"
    OVERDUE = "OVERDUE"


class AlertPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class InterventionCategory(StrEnum):
    MANAGER_CHECK_IN = "MANAGER_CHECK_IN"
    WORKLOAD_REVIEW = "WORKLOAD_REVIEW"
    CAREER_DEVELOPMENT = "CAREER_DEVELOPMENT"
    RECOGNITION = "RECOGNITION"
    LEARNING_SUPPORT = "LEARNING_SUPPORT"
    WORK_ENVIRONMENT_REVIEW = "WORK_ENVIRONMENT_REVIEW"
    OTHER_SUPPORT = "OTHER_SUPPORT"


class InterventionPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class InterventionStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class InterventionEventType(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ASSIGNED = "ASSIGNED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NOTE_ADDED = "NOTE_ADDED"


class RecommendationScope(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    DEPARTMENT = "DEPARTMENT"
    ORGANIZATION = "ORGANIZATION"


class RecommendationCategory(StrEnum):
    SUPPORT_CHECK_IN = "SUPPORT_CHECK_IN"
    WORKLOAD_REVIEW = "WORKLOAD_REVIEW"
    CAREER_DEVELOPMENT = "CAREER_DEVELOPMENT"
    DEPARTMENT_RETENTION_REVIEW = "DEPARTMENT_RETENTION_REVIEW"
    WORKFORCE_TREND_REVIEW = "WORKFORCE_TREND_REVIEW"
    DATA_QUALITY = "DATA_QUALITY"
    # rules-v2
    FOLLOW_UP_REVIEW = "FOLLOW_UP_REVIEW"
    GOAL_SUPPORT = "GOAL_SUPPORT"
    DATA_REFRESH = "DATA_REFRESH"


class RecommendationPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"  # rules-v2 only


class RecommendationStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ACCEPTED = "ACCEPTED"
    DISMISSED = "DISMISSED"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"  # rules-v2: evidence changed (superseded_by_id set) or no longer current


class TaskType(StrEnum):
    FOLLOW_UP = "FOLLOW_UP"
    DOCUMENTATION = "DOCUMENTATION"
    ONBOARDING = "ONBOARDING"
    OFFBOARDING = "OFFBOARDING"
    TRAINING = "TRAINING"
    REVIEW = "REVIEW"
    COMPLIANCE = "COMPLIANCE"
    OTHER = "OTHER"


class TaskPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskBlockReason(StrEnum):
    WAITING_ON_EMPLOYEE = "WAITING_ON_EMPLOYEE"
    WAITING_ON_APPROVAL = "WAITING_ON_APPROVAL"
    WAITING_ON_INFORMATION = "WAITING_ON_INFORMATION"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    OTHER = "OTHER"


class TaskEventType(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ASSIGNED = "ASSIGNED"
    STARTED = "STARTED"
    BLOCKED = "BLOCKED"
    UNBLOCKED = "UNBLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REOPENED = "REOPENED"


class ReviewCycleStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GoalCategory(StrEnum):
    BUSINESS = "BUSINESS"
    DEVELOPMENT = "DEVELOPMENT"
    LEARNING = "LEARNING"
    TEAM = "TEAM"
    OTHER = "OTHER"


class GoalStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    AT_RISK = "AT_RISK"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ReviewStatus(StrEnum):
    ASSIGNED = "ASSIGNED"
    SELF_SUBMITTED = "SELF_SUBMITTED"
    MANAGER_SUBMITTED = "MANAGER_SUBMITTED"
    FINALIZED = "FINALIZED"
    CANCELLED = "CANCELLED"


class PerformanceEventType(StrEnum):
    CYCLE_CREATED = "CYCLE_CREATED"
    CYCLE_UPDATED = "CYCLE_UPDATED"
    CYCLE_OPENED = "CYCLE_OPENED"
    CYCLE_REVIEWS_STARTED = "CYCLE_REVIEWS_STARTED"
    CYCLE_COMPLETED = "CYCLE_COMPLETED"
    CYCLE_CANCELLED = "CYCLE_CANCELLED"
    GOAL_CREATED = "GOAL_CREATED"
    GOAL_UPDATED = "GOAL_UPDATED"
    GOAL_PROGRESS_CHANGED = "GOAL_PROGRESS_CHANGED"
    GOAL_STATUS_CHANGED = "GOAL_STATUS_CHANGED"
    GOAL_REASSIGNED = "GOAL_REASSIGNED"
    REVIEW_CREATED = "REVIEW_CREATED"
    REVIEW_UPDATED = "REVIEW_UPDATED"
    REVIEW_REASSIGNED = "REVIEW_REASSIGNED"
    SELF_ASSESSMENT_SUBMITTED = "SELF_ASSESSMENT_SUBMITTED"
    MANAGER_REVIEW_SUBMITTED = "MANAGER_REVIEW_SUBMITTED"
    REVIEW_FINALIZED = "REVIEW_FINALIZED"
    REVIEW_REOPENED = "REVIEW_REOPENED"
    REVIEW_CANCELLED = "REVIEW_CANCELLED"
