"""All ORM models. Importing this package registers every table on Base.metadata."""

from app.models.attendance import Attendance
from app.models.attrition import AttritionModelVersion, AttritionPrediction
from app.models.audit_log import AuditLog
from app.models.auth import RevokedToken
from app.models.base import Base
from app.models.dataset import Dataset, DatasetVersion
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import (
    AlertPriority,
    AlertStatus,
    AttendanceStatus,
    CalculationMethod,
    DatasetPurpose,
    DatasetStatus,
    EmploymentStatus,
    EscalationStage,
    GoalCategory,
    GoalStatus,
    InterventionCategory,
    InterventionEventType,
    InterventionPriority,
    InterventionStatus,
    LeaveRequestStatus,
    ModelVersionStatus,
    PayFrequency,
    PayrollStatus,
    PerformanceEventType,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationScope,
    RecommendationStatus,
    ReviewCycleStatus,
    ReviewStatus,
    RiskCategory,
    RiskEventType,
    SalaryComponentKind,
    TaskBlockReason,
    TaskEventType,
    TaskPriority,
    TaskStatus,
    TaskType,
    UserRole,
)
from app.models.intervention import Intervention, InterventionEvent
from app.models.leave import LeaveBalance, LeavePolicy, LeaveRequest, LeaveType
from app.models.notification import Notification
from app.models.performance import Goal, PerformanceEvent, PerformanceReview, ReviewCycle
from app.models.recommendation import Recommendation
from app.models.risk_monitoring import RiskAlert, RiskAlertEscalation, RiskEvent
from app.models.payroll import (
    PayrollLineItem,
    PayrollPeriod,
    PayrollProfile,
    PayrollProfileComponent,
    PayrollRecord,
    SalaryComponent,
)
from app.models.shift import Shift
from app.models.task import Task, TaskEvent
from app.models.user import User
from app.models.workforce import WorkforceSnapshot, WorkforceSnapshotDepartment

__all__ = [
    "Attendance", "AttendanceStatus", "AuditLog", "Base", "Department", "Employee",
    "EmploymentStatus", "LeaveBalance", "LeavePolicy", "LeaveRequest", "LeaveRequestStatus",
    "LeaveType", "Notification", "Shift", "User", "UserRole",
    "CalculationMethod", "PayFrequency", "PayrollLineItem", "PayrollPeriod", "PayrollProfile",
    "PayrollProfileComponent", "PayrollRecord", "PayrollStatus", "SalaryComponent", "SalaryComponentKind",
    "AttritionModelVersion", "AttritionPrediction", "ModelVersionStatus", "RiskCategory",
    "Dataset", "DatasetPurpose", "DatasetStatus", "DatasetVersion",
    "AlertPriority", "AlertStatus", "EscalationStage", "RiskAlert", "RiskAlertEscalation", "RiskEvent", "RiskEventType",
    "WorkforceSnapshot", "WorkforceSnapshotDepartment",
    "Intervention", "InterventionCategory", "InterventionEvent", "InterventionEventType", "InterventionPriority",
    "InterventionStatus",
    "Recommendation", "RecommendationCategory", "RecommendationPriority", "RecommendationScope",
    "RecommendationStatus",
    "Goal", "GoalCategory", "GoalStatus", "PerformanceEvent", "PerformanceEventType", "PerformanceReview",
    "ReviewCycle", "ReviewCycleStatus", "ReviewStatus",
    "Task", "TaskBlockReason", "TaskEvent", "TaskEventType", "TaskPriority", "TaskStatus", "TaskType",
    "RevokedToken",
]
