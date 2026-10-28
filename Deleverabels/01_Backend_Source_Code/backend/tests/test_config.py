import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import ConfigurationError, Settings, get_settings
from tests.conftest import TEST_ENV

STRONG_SECRET = "Zq8-kP2_vX9!mW4#tR7$yB1^nC6&hJ3*"


def make_settings(**overrides) -> Settings:
    values = {
        "db_host": "127.0.0.1",
        "db_port": 3306,
        "db_name": "workforce_ai_pro",
        "db_user": "workforce_app",
        "db_password": "dev-password",
        "jwt_secret_key": STRONG_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_settings_load() -> None:
    settings = make_settings()
    assert settings.jwt_secret_key.get_secret_value() == STRONG_SECRET


def test_missing_jwt_secret_fails_startup_without_leaking_values(monkeypatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY")
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()

    message = str(exc_info.value)
    assert "JWT_SECRET_KEY" in message
    assert TEST_ENV["DB_PASSWORD"] not in message


@pytest.mark.parametrize("secret", ["change-me", "CHANGE-ME", "secret", "your-secret-key"])
def test_placeholder_jwt_secret_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make_settings(jwt_secret_key=secret)


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        make_settings(jwt_secret_key="Short-But-Random-9x")


def test_low_entropy_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="not random enough"):
        make_settings(jwt_secret_key="a" * 64)


@pytest.mark.parametrize("algorithm, min_bytes", [("HS384", 48), ("HS512", 64)])
def test_jwt_secret_must_fit_algorithm(algorithm: str, min_bytes: int) -> None:
    with pytest.raises(ValidationError, match=f"at least {min_bytes} bytes for {algorithm}"):
        make_settings(jwt_algorithm=algorithm, jwt_secret_key=STRONG_SECRET)  # 32 bytes
    long_secret = (STRONG_SECRET * 3)[:min_bytes]
    assert make_settings(jwt_algorithm=algorithm, jwt_secret_key=long_secret).jwt_algorithm == algorithm


@pytest.mark.parametrize("algorithm", ["none", "RS256", "hs256"])
def test_jwt_algorithm_allow_list(algorithm: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_algorithm=algorithm)


def test_token_lifetime_defaults_to_30_minutes_and_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES")
    assert make_settings().access_token_expire_minutes == 30
    for invalid in (0, 1441):
        with pytest.raises(ValidationError):
            make_settings(access_token_expire_minutes=invalid)


def test_empty_db_password_rejected() -> None:
    with pytest.raises(ValidationError, match="DB_PASSWORD must not be empty"):
        make_settings(db_password="")


def test_secrets_hidden_in_repr() -> None:
    settings = make_settings(db_password="visible?")
    text = repr(settings) + str(settings)
    assert "visible?" not in text
    assert STRONG_SECRET not in text


@pytest.mark.parametrize(
    "user, password",
    [
        ("workforce_app", "p@ss:w/rd#%?&=+"),
        ("hr@user", "spaces and 'quotes' \"too\""),
        ("workforce_app", "üñíçødé-€"),
    ],
)
def test_database_url_handles_special_characters(user: str, password: str) -> None:
    url = make_settings(db_user=user, db_password=password).database_url

    assert url.username == user
    assert url.password == password
    assert url.host == "127.0.0.1"
    assert url.port == 3306
    assert url.database == "workforce_ai_pro"
    assert url.query == {"charset": "utf8mb4"}

    # The rendered string must parse back to the same credentials (correct escaping).
    reparsed = make_url(url.render_as_string(hide_password=False))
    assert reparsed.username == user
    assert reparsed.password == password

    # Default rendering masks the password.
    assert password not in str(url)


@pytest.mark.parametrize("zone", ["Mars/Olympus", "IST", "+05:30", ""])
def test_invalid_org_timezone_rejected(zone: str) -> None:
    with pytest.raises(ValidationError, match="ORG_TIMEZONE must be an IANA time zone"):
        make_settings(org_timezone=zone)


def test_org_timezone_default_and_valid_values(monkeypatch) -> None:
    monkeypatch.delenv("ORG_TIMEZONE")
    assert make_settings().org_timezone == "UTC"
    assert str(make_settings(org_timezone="Asia/Kolkata").org_tz) == "Asia/Kolkata"


@pytest.mark.parametrize("days", [[7], [-1], [5, 5], [0, 1, 2, 3, 4, 5, 6]])
def test_invalid_leave_weekend_days_rejected(days) -> None:
    with pytest.raises(ValidationError, match="LEAVE_WEEKEND_DAYS"):
        make_settings(leave_weekend_days=days)


def test_leave_defaults(monkeypatch) -> None:
    settings = make_settings(leave_weekend_days=[6, 5])
    assert settings.leave_weekend_days == [5, 6]  # normalized
    assert settings.leave_max_backdate_days == 30


@pytest.mark.parametrize("medium, high", [(70, 40), (50, 50)])
def test_risk_thresholds_must_be_ordered(medium, high) -> None:
    with pytest.raises(ValidationError, match="ML_RISK_MEDIUM_THRESHOLD must be below"):
        make_settings(ml_risk_medium_threshold=medium, ml_risk_high_threshold=high)


def test_ml_defaults_point_inside_backend_ml(monkeypatch) -> None:
    for name in ("ML_ARTIFACT_DIR", "ML_DATASET_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = make_settings()
    assert settings.ml_artifact_dir.parts[-2:] == ("ml", "models")
    assert settings.ml_dataset_dir.parts[-2:] == ("ml", "datasets")
    assert (settings.ml_risk_medium_threshold, settings.ml_risk_high_threshold) == (40, 70)


@pytest.mark.parametrize(
    "field, value",
    [("risk_alert_score_threshold", 0), ("risk_alert_score_threshold", 101), ("risk_change_threshold_points", 0),
     ("risk_stale_prediction_days", 0), ("risk_monitoring_max_batch", 0), ("risk_monitoring_max_batch", 5001)],
)
def test_risk_monitoring_settings_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_risk_monitoring_defaults() -> None:
    settings = make_settings()
    assert (settings.risk_alert_score_threshold, settings.risk_change_threshold_points,
            settings.risk_stale_prediction_days, settings.risk_monitoring_max_batch) == (70, 20, 90, 500)


@pytest.mark.parametrize("field, value", [("workforce_forecast_min_months", 2), ("workforce_forecast_max_horizon_months", 0),
                                          ("workforce_forecast_max_horizon_months", 37), ("workforce_stability_window_months", 2),
                                          ("workforce_snapshot_max_age_days", 6),
                                          ("workforce_stability_min_average_headcount", 0),
                                          ("workforce_stability_min_average_headcount", 1001)])
def test_workforce_settings_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


@pytest.mark.parametrize("field, value", [("recommendation_turnover_review_threshold", 0),
                                          ("recommendation_stability_review_below", 101),
                                          ("recommendation_alert_cluster_min", 1),
                                          ("recommendation_max_alerts_per_evaluation", 0)])
def test_recommendation_settings_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


@pytest.mark.parametrize("field, value", [("activity_feed_max_window_days", 0), ("activity_feed_max_window_days", 367),
                                          ("activity_feed_max_items", 99), ("activity_feed_max_items", 50001),
                                          ("activity_alert_review_days", 0), ("activity_attendance_gap_days", 91)])
def test_activity_settings_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_activity_settings_defaults() -> None:
    settings = make_settings()
    assert (settings.activity_feed_max_window_days, settings.activity_feed_max_items,
            settings.activity_alert_review_days, settings.activity_attendance_gap_days) == (31, 5000, 14, 14)


@pytest.mark.parametrize("overrides", [
    {"alert_review_sla_hours_high": 0}, {"alert_review_sla_hours_low": 721}, {"alert_review_sla_hours_medium": -5},
    {"alert_review_approaching_percent": 0}, {"alert_review_approaching_percent": 100},
    {"alert_escalation_max_batch": 0}, {"alert_escalation_max_batch": 5001},
    {"alert_review_sla_hours_high": 48, "alert_review_sla_hours_medium": 24},  # HIGH must not exceed MEDIUM
    {"alert_review_sla_hours_medium": 200, "alert_review_sla_hours_low": 100},
    {"alert_escalation_recipient_roles": []}, {"alert_escalation_recipient_roles": ["MANAGER"]},
    {"alert_escalation_recipient_roles": ["EMPLOYEE"]}, {"alert_escalation_recipient_roles": ["HR", "hr"]},
])
def test_alert_sla_settings_validated(overrides) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_alert_sla_settings_defaults_and_env(monkeypatch) -> None:
    settings = make_settings()
    assert (settings.alert_review_sla_hours_high, settings.alert_review_sla_hours_medium,
            settings.alert_review_sla_hours_low, settings.alert_review_approaching_percent,
            settings.alert_escalation_max_batch, settings.alert_escalation_recipient_roles,
            settings.alert_escalation_notify_approaching) == (24, 72, 168, 75, 500, ["ADMIN", "HR"], True)
    monkeypatch.setenv("ALERT_ESCALATION_RECIPIENT_ROLES", '["hr"]')
    monkeypatch.setenv("ALERT_REVIEW_SLA_HOURS_HIGH", "8")
    assert make_settings().alert_escalation_recipient_roles == ["HR"]
    assert make_settings().alert_review_sla_hours_high == 8



@pytest.mark.parametrize("field, value", [("activity_updates_max_page_size", 0), ("activity_updates_max_page_size", 501),
                                          ("activity_updates_settle_seconds", -1), ("activity_updates_settle_seconds", 301),
                                          ("activity_updates_cursor_max_age_hours", 0),
                                          ("activity_updates_cursor_max_age_hours", 745),
                                          ("activity_poll_interval_seconds", 4), ("activity_poll_interval_seconds", 601)])
def test_activity_polling_settings_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_activity_polling_defaults() -> None:
    settings = make_settings()
    assert (settings.activity_updates_max_page_size, settings.activity_updates_settle_seconds,
            settings.activity_updates_cursor_max_age_hours, settings.activity_poll_interval_seconds) == (100, 5, 72, 20)


@pytest.mark.parametrize("overrides", [{"report_max_rows": 99}, {"report_max_rows": 50001}, {"report_pdf_max_rows": 9},
                                       {"report_pdf_max_rows": 5001}, {"report_max_range_days": 0},
                                       {"report_max_range_days": 1097},
                                       {"report_max_rows": 500, "report_pdf_max_rows": 501}])
def test_report_settings_validated(overrides) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_report_settings_defaults() -> None:
    settings = make_settings()
    assert (settings.report_max_rows, settings.report_pdf_max_rows, settings.report_max_range_days) == (5000, 1000, 366)


@pytest.mark.parametrize("value", [0, 3661])
def test_audit_query_range_validated(value) -> None:
    with pytest.raises(ValidationError):
        make_settings(audit_query_max_range_days=value)


def test_audit_query_range_default() -> None:
    assert make_settings().audit_query_max_range_days == 366
