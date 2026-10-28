"""Versioned attrition feature schema and validation (no FastAPI, no database).

Only job-related features derived from data the platform already holds. Each feature has a rationale.
Deliberately excluded:
- Direct identifiers (name, email, employee code, phone, user id).
- Protected or sensitive characteristics and obvious proxies (age, gender, marital status, religion,
  nationality, ethnicity, disability, health). Leave-type patterns are excluded in v1 because leave
  categories such as maternity or sick leave act as proxies for pregnancy and health.
- Salary/payroll (sensitive; revisit only with HR/legal review).
- Employment status and anything recorded after an exit decision (would leak the outcome).
- Performance reviews and promotions: those modules do not exist yet (future integrations).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_SCHEMA_VERSION = "attrition-features-v1"
TARGET = "attrited"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    kind: str  # "numeric" or "categorical"
    nullable: bool
    minimum: float | None
    maximum: float | None
    description: str
    rationale: str


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("tenure_months", "numeric", False, 0, 600,
                "Whole months from date of joining to the prediction date",
                "Attrition risk commonly varies with tenure"),
    FeatureSpec("late_rate_90d", "numeric", True, 0, 1,
                "Share of attended days (PRESENT/LATE/HALF_DAY) marked LATE in the last 90 days; null if none recorded",
                "Change in punctuality can reflect disengagement"),
    FeatureSpec("avg_overtime_minutes_90d", "numeric", True, 0, 1440,
                "Mean overtime minutes per checked-out day in the last 90 days; null if none recorded",
                "Sustained overtime is associated with burnout"),
    FeatureSpec("recorded_absence_days_90d", "numeric", False, 0, 90,
                "Attendance records explicitly marked ABSENT in the last 90 days (missing days are not absences)",
                "Explicitly recorded absence only"),
    FeatureSpec("attendance_days_recorded_90d", "numeric", False, 0, 90,
                "Attendance records of any status in the last 90 days",
                "Data-coverage signal so sparse attendance data is not mistaken for behaviour"),
    FeatureSpec("department", "categorical", True, None, None,
                "Department identifier (category)",
                "Team-level working conditions differ; job-related"),
)

FEATURE_COLUMNS = [f.name for f in FEATURES]
NUMERIC_FEATURES = [f.name for f in FEATURES if f.kind == "numeric"]
CATEGORICAL_FEATURES = [f.name for f in FEATURES if f.kind == "categorical"]
_SPECS = {f.name: f for f in FEATURES}

FORBIDDEN_COLUMNS = frozenset({
    "id", "employee_id", "user_id", "name", "full_name", "first_name", "last_name", "email", "work_email",
    "employee_code", "phone", "password", "password_hash", "address", "gender", "sex", "age", "date_of_birth",
    "dob", "marital_status", "religion", "nationality", "ethnicity", "race", "disability", "health",
    "pregnancy", "salary", "base_salary", "net_pay", "status", "employment_status", "exit_date",
})

# Features that may be omitted later without breaking v1 (documentation only).
FUTURE_FEATURES = {
    "performance_score_recent": "Requires a performance-review module (not implemented)",
    "months_since_last_promotion": "Requires promotion history (not implemented)",
}


class FeatureValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_frame(frame: pd.DataFrame, *, require_target: bool) -> pd.DataFrame:
    """Return a cleaned copy with only schema columns (and the target), or raise FeatureValidationError.

    Error messages name columns and counts only; they never echo data values.
    """
    errors: list[str] = []
    columns = set(frame.columns)
    expected = set(FEATURE_COLUMNS) | ({TARGET} if require_target else set())

    forbidden = sorted(columns & FORBIDDEN_COLUMNS)
    if forbidden:
        errors.append(f"Forbidden columns (identifiers, sensitive or leaking data): {', '.join(forbidden)}")
    unknown = sorted(columns - expected - FORBIDDEN_COLUMNS)
    if unknown:
        errors.append(f"Unknown columns: {', '.join(unknown)}")
    missing = sorted(expected - columns)
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
    if errors:
        raise FeatureValidationError(errors)

    clean = pd.DataFrame(index=frame.index)
    for spec in FEATURES:
        column = frame[spec.name]
        if spec.kind == "numeric":
            values = pd.to_numeric(column, errors="coerce")
            not_numeric = int((values.isna() & column.notna()).sum())
            if not_numeric:
                errors.append(f"{spec.name}: {not_numeric} non-numeric value(s)")
            if spec.minimum is not None:
                below = int((values < spec.minimum).sum())
                if below:
                    errors.append(f"{spec.name}: {below} value(s) below {spec.minimum}")
            if spec.maximum is not None:
                above = int((values > spec.maximum).sum())
                if above:
                    errors.append(f"{spec.name}: {above} value(s) above {spec.maximum}")
            clean[spec.name] = values.astype("float64")
        else:
            values = column.astype("string").str.strip()
            values = values.mask(values == "")
            too_long = int((values.str.len() > 50).sum())
            if too_long:
                errors.append(f"{spec.name}: {too_long} value(s) longer than 50 characters")
            # NaN (not None) so scikit-learn imputers recognise the value as missing.
            clean[spec.name] = values.astype("object").where(values.notna(), np.nan)
        if not spec.nullable:
            nulls = int(clean[spec.name].isna().sum())
            if nulls:
                errors.append(f"{spec.name}: {nulls} missing value(s) (not allowed)")

    if require_target:
        target = pd.to_numeric(frame[TARGET], errors="coerce")
        invalid = int((~target.isin([0, 1])).sum())
        if invalid:
            errors.append(f"{TARGET}: {invalid} value(s) not 0 or 1 (1 = left, 0 = stayed)")
        clean[TARGET] = target

    if errors:
        raise FeatureValidationError(errors)
    if require_target:
        clean[TARGET] = clean[TARGET].astype("int64")
    return clean


def validate_record(record: dict) -> pd.DataFrame:
    """Validate a single feature dictionary for prediction (one-row frame)."""
    return validate_frame(pd.DataFrame([record]), require_target=False)


def missing_inputs(record: dict) -> list[str]:
    return [name for name in FEATURE_COLUMNS if record.get(name) is None or record.get(name) != record.get(name)]


def describe_schema() -> list[dict]:
    return [
        {"name": f.name, "kind": f.kind, "nullable": f.nullable, "minimum": f.minimum, "maximum": f.maximum,
         "description": f.description, "rationale": f.rationale}
        for f in FEATURES
    ]
