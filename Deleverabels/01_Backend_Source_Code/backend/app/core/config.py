from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BACKEND_DIR = Path(__file__).resolve().parents[2]

JWT_SECRET_MIN_LENGTH = 32
JWT_MIN_KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}
UNSAFE_JWT_SECRETS = {
    "change-me",
    "changeme",
    "change_me",
    "secret",
    "jwt-secret",
    "jwt_secret",
    "your-secret-key",
    "your_secret_key",
    "supersecret",
    "password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "WorkForce AI Pro"
    # development | test | staging | production. Staging and production refuse insecure settings at startup.
    environment: Literal["development", "test", "staging", "production"] = "development"

    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str = "workforce_ai_pro"
    db_user: str = Field(min_length=1)
    db_password: SecretStr

    jwt_secret_key: SecretStr
    # HMAC only: the secret is shared, and "none" or asymmetric algorithms must never be accepted.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = Field(default="workforce-ai-pro", min_length=1)
    access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)

    cors_origins: list[str] = ["http://localhost:5173"]
    # Host headers the API answers to. "*" (any) is allowed only in development and test.
    allowed_hosts: list[str] = ["*"]
    # Interactive API docs (/docs, /redoc, /openapi.json). None = on in development and test, off in staging and
    # production unless explicitly enabled.
    api_docs_enabled: bool | None = None

    # Authentication rate limits (in-process; see app/core/rate_limit.py for the multi-worker limitation).
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_window_seconds: int = Field(default=900, ge=10, le=86400)
    # Failed sign-ins per client address and submitted email within the window.
    auth_login_max_failures: int = Field(default=5, ge=1, le=1000)
    # Sign-in attempts (successful or not) per client address within the window.
    auth_login_max_attempts_per_client: int = Field(default=50, ge=1, le=100000)
    # Registrations per client address within the window.
    auth_register_max_per_client: int = Field(default=10, ge=1, le=100000)

    # Attendance. Work dates and shift times are interpreted in this IANA time zone; instants are stored in UTC.
    org_timezone: str = "UTC"
    attendance_late_grace_minutes: int = Field(default=15, ge=0, le=240)
    # Overtime threshold (worked minutes per day) for attendance recorded without a shift.
    attendance_default_overtime_threshold_minutes: int = Field(default=480, gt=0, le=1440)

    # Leave. Weekend days use Python weekday numbers (Monday=0 ... Sunday=6); JSON list in .env, e.g. [5, 6].
    leave_weekend_days: list[int] = [5, 6]
    # How many days in the past (org time zone) a leave request may start.
    leave_max_backdate_days: int = Field(default=30, ge=0, le=366)

    # Payroll. ISO 4217 code recorded on every payroll record and payslip.
    payroll_currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    # Off by default: no salary is deducted for leave or absence unless explicitly enabled.
    # Unpaid leave = APPROVED leave of a leave type with is_paid = false.
    payroll_deduct_unpaid_leave: bool = False
    # Recorded absence = attendance records with status ABSENT. Missing records are never treated as absence.
    payroll_deduct_recorded_absence: bool = False

    # AI attrition model. Artifacts and registered datasets live only in these local directories; the API
    # never accepts filesystem paths. Neither directory is created at startup.
    ml_artifact_dir: Path = BACKEND_DIR / "ml" / "models"
    ml_dataset_dir: Path = BACKEND_DIR / "ml" / "datasets"
    ml_random_state: int = Field(default=42, ge=0)
    ml_min_training_rows: int = Field(default=50, ge=20)
    ml_min_class_count: int = Field(default=10, ge=5)
    # A trained model can be activated only if its held-out ROC-AUC reaches this value.
    ml_activation_min_roc_auc: float = Field(default=0.65, ge=0.5, le=1.0)
    # Risk score (0-100) at or above which a prediction is MEDIUM / HIGH.
    ml_risk_medium_threshold: float = Field(default=40, gt=0, lt=100)
    ml_risk_high_threshold: float = Field(default=70, gt=0, le=100)
    # Registered workforce datasets (CSV). Files are stored in ml_dataset_dir under server-generated names.
    dataset_max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

    # Risk monitoring (reads persisted predictions only). Score units are the 0-100 risk score.
    # An alert is raised when the latest score reaches this value after being below it (or with no baseline).
    risk_alert_score_threshold: float = Field(default=70, gt=0, le=100)
    # A change of at least this many points between comparable predictions is a significant change.
    risk_change_threshold_points: float = Field(default=20, gt=0, le=100)
    # A latest prediction older than this is reported as stale (needs a refreshed prediction).
    risk_stale_prediction_days: int = Field(default=90, ge=1, le=730)
    # Upper bound on employees examined by one monitoring run.
    risk_monitoring_max_batch: int = Field(default=500, ge=1, le=5000)

    # Workforce analytics (aggregate only; history comes from captured snapshots, never reconstructed).
    workforce_forecast_min_months: int = Field(default=6, ge=3, le=36)
    workforce_forecast_max_horizon_months: int = Field(default=12, ge=1, le=36)
    workforce_stability_window_months: int = Field(default=12, ge=3, le=36)
    # The latest snapshot must be at most this old for a stability score to be produced.
    workforce_snapshot_max_age_days: int = Field(default=45, ge=7, le=365)
    # Below this average active headcount (over the snapshots in the window) no stability score is produced: one
    # joiner or leaver would swing every component, so the department is reported as insufficient data instead.
    workforce_stability_min_average_headcount: int = Field(default=10, ge=1, le=1000)

    # Recommendation rules (deterministic, versioned; see app/services/recommendation_rules.py).
    recommendation_turnover_review_threshold: float = Field(default=0.20, gt=0, le=2)  # annualized
    recommendation_stability_review_below: float = Field(default=60, gt=0, le=100)
    recommendation_alert_cluster_min: int = Field(default=3, ge=2, le=1000)
    recommendation_max_alerts_per_evaluation: int = Field(default=500, ge=1, le=5000)

    # Workforce activity feed (read-only, assembled on demand from existing records).
    activity_feed_max_window_days: int = Field(default=31, ge=1, le=366)
    # Maximum items merged per request; beyond this the feed reports truncated=true.
    activity_feed_max_items: int = Field(default=5000, ge=100, le=50000)
    # Active risk alerts first detected longer ago than this are reported as awaiting review.
    activity_alert_review_days: int = Field(default=14, ge=1, le=365)
    # A department with active employees but no attendance records in this many days has an attendance data gap.
    activity_attendance_gap_days: int = Field(default=14, ge=1, le=90)
    # Incremental updates (polling, not push). Maximum items per /updates response.
    activity_updates_max_page_size: int = Field(default=100, ge=1, le=500)
    # /updates only returns items recorded at least this long ago, so transactions still committing are not skipped.
    activity_updates_settle_seconds: int = Field(default=5, ge=0, le=300)
    # Cursors older than this are rejected (410) and the client reloads the feed. Also the default look-back.
    activity_updates_cursor_max_age_hours: int = Field(default=72, ge=1, le=744)
    # Polling interval suggested to clients (seconds).
    activity_poll_interval_seconds: int = Field(default=20, ge=5, le=600)

    # Reports and exports (CSV / XLSX / PDF). A report with more rows than the limit is refused (422), never cut.
    report_max_rows: int = Field(default=5000, ge=100, le=50000)
    report_pdf_max_rows: int = Field(default=1000, ge=10, le=5000)
    # Longest date range for date-filtered reports (attendance, leave).
    report_max_range_days: int = Field(default=366, ge=1, le=1096)

    # Audit-log queries and exports: longest date window per request (default window is the last 30 days).
    audit_query_max_range_days: int = Field(default=366, ge=1, le=3660)

    # Risk-alert review deadlines (hours from first detection until the alert should be acknowledged),
    # per existing alert priority. Must satisfy HIGH <= MEDIUM <= LOW.
    alert_review_sla_hours_high: int = Field(default=24, ge=1, le=720)
    alert_review_sla_hours_medium: int = Field(default=72, ge=1, le=720)
    alert_review_sla_hours_low: int = Field(default=168, ge=1, le=720)
    # An OPEN alert is "approaching" its deadline once this percentage of its review window has elapsed.
    alert_review_approaching_percent: int = Field(default=75, ge=1, le=99)
    # Upper bound on alerts examined by one escalation evaluation.
    alert_escalation_max_batch: int = Field(default=500, ge=1, le=5000)
    # Roles notified when an alert is escalated (only ADMIN and HR are authorized reviewers organization-wide).
    alert_escalation_recipient_roles: list[str] = ["ADMIN", "HR"]
    # Whether the APPROACHING stage is recorded and notified (OVERDUE always is).
    alert_escalation_notify_approaching: bool = True

    @field_validator("alert_escalation_recipient_roles")
    @classmethod
    def _validate_escalation_roles(cls, value: list[str]) -> list[str]:
        roles = [v.strip().upper() for v in value]
        if not roles or len(set(roles)) != len(roles) or any(r not in ("ADMIN", "HR") for r in roles):
            raise ValueError('ALERT_ESCALATION_RECIPIENT_ROLES must be a non-empty list of distinct "ADMIN"/"HR" values')
        return sorted(roles)

    @model_validator(mode="after")
    def _report_limits_ordered(self) -> "Settings":
        if self.report_pdf_max_rows > self.report_max_rows:
            raise ValueError("REPORT_PDF_MAX_ROWS must not exceed REPORT_MAX_ROWS")
        return self

    @model_validator(mode="after")
    def _alert_sla_ordered(self) -> "Settings":
        if not self.alert_review_sla_hours_high <= self.alert_review_sla_hours_medium <= self.alert_review_sla_hours_low:
            raise ValueError("Alert review SLA hours must satisfy HIGH <= MEDIUM <= LOW")
        return self

    @field_validator("db_password")
    @classmethod
    def _require_db_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("DB_PASSWORD must not be empty")
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def _require_strong_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret.strip().lower() in UNSAFE_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY is a known placeholder value")
        if len(secret) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(f"JWT_SECRET_KEY must be at least {JWT_SECRET_MIN_LENGTH} characters")
        if len(set(secret)) < 10:
            raise ValueError("JWT_SECRET_KEY is not random enough")
        return value

    @field_validator("org_timezone")
    @classmethod
    def _require_known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("ORG_TIMEZONE must be an IANA time zone name such as UTC or Asia/Kolkata") from None
        return value

    @field_validator("leave_weekend_days")
    @classmethod
    def _validate_weekend_days(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value) or any(day < 0 or day > 6 for day in value) or len(value) > 6:
            raise ValueError("LEAVE_WEEKEND_DAYS must be distinct weekday numbers 0-6 (Monday=0), at most 6")
        return sorted(value)

    @model_validator(mode="after")
    def _risk_thresholds_ordered(self) -> "Settings":
        if not self.ml_risk_medium_threshold < self.ml_risk_high_threshold:
            raise ValueError("ML_RISK_MEDIUM_THRESHOLD must be below ML_RISK_HIGH_THRESHOLD")
        return self

    @field_validator("environment", mode="before")
    @classmethod
    def _normalize_environment(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @property
    def is_deployed(self) -> bool:
        """Staging or production: real users and data, so development conveniences are refused."""
        return self.environment in ("staging", "production")

    @property
    def docs_enabled(self) -> bool:
        return self.api_docs_enabled if self.api_docs_enabled is not None else not self.is_deployed

    @model_validator(mode="after")
    def _deployed_settings_are_strict(self) -> "Settings":
        if not self.is_deployed:
            return self
        if not self.cors_origins or any(origin.strip() == "*" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must list explicit origins (no '*') in staging and production")
        if self.environment == "production" and any(not origin.startswith("https://") for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must use https:// origins in production")
        if not self.allowed_hosts or any(host.strip() in ("*", "") for host in self.allowed_hosts):
            raise ValueError("ALLOWED_HOSTS must list explicit host names (no '*') in staging and production")
        if not self.auth_rate_limit_enabled:
            raise ValueError("AUTH_RATE_LIMIT_ENABLED cannot be false in staging and production")
        return self

    @property
    def org_tz(self) -> ZoneInfo:
        return ZoneInfo(self.org_timezone)

    @model_validator(mode="after")
    def _jwt_secret_fits_algorithm(self) -> "Settings":
        # RFC 7518 §3.2: an HMAC key should be at least as long as the hash output.
        required_bytes = JWT_MIN_KEY_BYTES[self.jwt_algorithm]
        if len(self.jwt_secret_key.get_secret_value().encode("utf-8")) < required_bytes:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {required_bytes} bytes for {self.jwt_algorithm}"
            )
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="mysql+pymysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


class ConfigurationError(RuntimeError):
    """Raised when settings are missing or unsafe. The message never includes input values."""


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Pydantic error strings can echo raw inputs (including secrets), so report field names only.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']).upper() or 'SETTINGS'}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigurationError(
            f"Invalid configuration ({problems}). See backend/.env.example."
        ) from None
