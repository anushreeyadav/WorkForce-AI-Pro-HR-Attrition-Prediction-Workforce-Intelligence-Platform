"""Risk-monitoring API over SYNTHETIC persisted predictions (no model artifacts are needed or used)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Employee,
    EmploymentStatus,
    ModelVersionStatus,
    Notification,
    RiskAlert,
    RiskCategory,
    RiskEvent,
    UserRole,
)
from app.models.base import utcnow
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
FEATURES = {"tenure_months": 12, "late_rate_90d": None, "avg_overtime_minutes_90d": None,
            "recorded_absence_days_90d": 0, "attendance_days_recorded_90d": 0, "department": "1"}


@pytest.fixture
def make_model(db):
    counter = iter(range(1, 1000))

    def _make(schema: str = FEATURE_SCHEMA_VERSION, status=ModelVersionStatus.ACTIVE) -> AttritionModelVersion:
        n = next(counter)
        model = AttritionModelVersion(
            model_version=f"lr-2026010100000{n % 10}-{n:08x}", feature_schema_version=schema, status=status,
            trained_at=utcnow(), dataset_fingerprint="0" * 64, artifact_sha256="0" * 64, training_rows=100,
            evaluation={}, activated_at=utcnow())
        db.add(model)
        db.commit()
        return model

    return _make


@pytest.fixture
def model(make_model):
    return make_model()


def add_prediction(db, employee, model, score, days_ago=1.0, schema=FEATURE_SCHEMA_VERSION) -> AttritionPrediction:
    value = Decimal(str(score))
    category = RiskCategory.HIGH if value >= 70 else RiskCategory.MEDIUM if value >= 40 else RiskCategory.LOW
    prediction = AttritionPrediction(employee_id=employee.id, model_version_id=model.id, feature_schema_version=schema,
                                     risk_score=value, risk_category=category, features=FEATURES,
                                     predicted_at=utcnow() - timedelta(days=days_ago))
    db.add(prediction)
    db.commit()
    return prediction


def run(client, headers, **body):
    response = client.post("/api/risk-monitoring/run", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def counts(db) -> tuple[int, int, int]:
    db.expire_all()
    return (db.scalar(select(func.count(RiskEvent.id))), db.scalar(select(func.count(RiskAlert.id))),
            db.scalar(select(func.count(Notification.id))))


@pytest.fixture
def org(staff, headers_for):
    """Engineering: manager + 2 staff. Sales: 1 staff. One HR user with no employee record."""
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng1, _ = staff(department="Engineering")
    eng2, _ = staff(department="Engineering")
    sales, _ = staff(department="Sales")
    hr_h = headers_for(HR, "hr.reviewer@example.com")
    return {"manager": manager, "manager_h": manager_h, "eng1": eng1, "eng2": eng2, "sales": sales, "hr": hr_h}


# --- Monitoring runs ------------------------------------------------------------------------------


def test_threshold_crossing_creates_alert_and_notifies_hr_only(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 30, days_ago=10)
    add_prediction(db, org["eng1"], model, 78, days_ago=1)
    summary = run(client, org["hr"])
    assert (summary["new_events"], summary["alerts_created"], summary["alerts_updated"]) == (2, 1, 0)
    assert summary["examined_employees"] == 4 and summary["skipped"]["no_prediction"] == 3
    assert summary["active_model_available"] is True
    for identifying in (org["eng1"].employee_code, org["eng1"].full_name, org["eng1"].work_email):
        assert identifying not in str(summary)  # no identifying information in run summaries

    alert = db.scalar(select(RiskAlert))
    assert (alert.status, alert.priority.value, alert.trigger_type.value) == (AlertStatus.OPEN, "HIGH", "THRESHOLD_CROSSED")
    assert alert.latest_score == Decimal("78.00") and alert.event_count == 2
    events = db.scalars(select(RiskEvent).order_by(RiskEvent.id)).all()
    assert [e.event_type.value for e in events] == ["THRESHOLD_CROSSED", "SCORE_INCREASE"]
    assert all(e.alert_id == alert.id for e in events)

    hr_user_ids = {n.user_id for n in db.scalars(select(Notification))}
    assert hr_user_ids == {db.scalar(select(AuditLog.actor_user_id).where(AuditLog.action == "risk.monitoring_run"))}
    note = db.scalar(select(Notification))
    assert note.message == f"Risk alert #{alert.id} (HIGH priority) is ready for review."
    assert org["eng1"].full_name not in note.message and "78" not in note.message

    audit = db.scalar(select(AuditLog).where(AuditLog.action == "risk.alert_created"))
    assert audit.details == {"employee_id": org["eng1"].id, "trigger_type": "THRESHOLD_CROSSED",
                             "priority": "HIGH", "model_version": model.model_version}


def test_repeated_runs_do_not_duplicate(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 80)
    run(client, org["hr"])
    before = counts(db)
    again = run(client, org["hr"])
    assert (again["new_events"], again["alerts_created"], again["alerts_updated"]) == (0, 0, 0)
    assert counts(db) == before


def test_newer_prediction_updates_active_alert_without_new_alert(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 80, days_ago=5)
    run(client, org["hr"])
    newer = add_prediction(db, org["eng1"], model, 82, days_ago=1)
    _, alerts_before, notes_before = counts(db)
    summary = run(client, org["hr"])
    assert (summary["new_events"], summary["alerts_created"], summary["alerts_updated"]) == (0, 0, 1)
    alert = db.scalar(select(RiskAlert))
    assert alert.latest_prediction_id == newer.id and alert.event_count == 1
    assert counts(db)[1:] == (alerts_before, notes_before)  # no new alert or notification


def test_escalation_attaches_to_existing_alert(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 20, days_ago=10)
    add_prediction(db, org["eng1"], model, 45, days_ago=5)   # +25 to MEDIUM: alert
    run(client, org["hr"])
    alert = db.scalar(select(RiskAlert))
    assert (alert.priority.value, alert.trigger_type.value) == ("MEDIUM", "SCORE_INCREASE")

    add_prediction(db, org["eng1"], model, 75, days_ago=1)   # crossing + increase
    summary = run(client, org["hr"])
    assert (summary["alerts_created"], summary["alerts_updated"]) == (0, 1)
    db.expire_all()
    alert = db.scalar(select(RiskAlert))
    assert (alert.priority.value, alert.event_count) == ("HIGH", 3)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "risk.alert_escalated"))
    assert log.details == {"from_priority": "MEDIUM", "to_priority": "HIGH"}


def test_decrease_and_low_increase_are_events_only(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 60, days_ago=5)
    add_prediction(db, org["eng1"], model, 30, days_ago=1)   # decrease
    add_prediction(db, org["eng2"], model, 5, days_ago=5)
    add_prediction(db, org["eng2"], model, 30, days_ago=1)   # +25 but below medium
    summary = run(client, org["hr"])
    assert (summary["new_events"], summary["alerts_created"]) == (2, 0)
    assert {e.event_type.value for e in db.scalars(select(RiskEvent))} == {"SCORE_DECREASE", "SCORE_INCREASE"}


def test_decrease_does_not_resolve_active_alert(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 85, days_ago=5)
    run(client, org["hr"])
    add_prediction(db, org["eng1"], model, 30, days_ago=1)
    run(client, org["hr"])
    db.expire_all()
    alert = db.scalar(select(RiskAlert))
    assert alert.status == AlertStatus.OPEN and alert.latest_score == Decimal("30.00")  # snapshot updated, human decides


def test_stale_predictions_reported(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 30, days_ago=120)
    summary = run(client, org["hr"])
    assert (summary["stale_predictions"], summary["new_events"], summary["alerts_created"]) == (1, 1, 0)
    assert run(client, org["hr"])["new_events"] == 0  # one stale event per prediction


def test_incompatible_and_missing_predictions_are_skipped(client, db, org, make_model) -> None:
    old_schema = make_model(schema="attrition-features-v0")
    add_prediction(db, org["eng1"], old_schema, 95)
    add_prediction(db, org["eng2"], make_model(), 95, schema="attrition-features-v0")
    summary = run(client, org["hr"])
    assert summary["skipped"] == {"no_prediction": 2, "incompatible_model": 2}
    assert counts(db) == (0, 0, 0)


def test_model_versions_are_not_compared(client, db, org, make_model) -> None:
    first, second = make_model(), make_model()
    add_prediction(db, org["eng1"], first, 10, days_ago=10)
    add_prediction(db, org["eng1"], second, 80, days_ago=1)
    run(client, org["hr"])
    assert [e.event_type.value for e in db.scalars(select(RiskEvent))] == ["THRESHOLD_CROSSED"]  # no SCORE_INCREASE
    assert db.scalar(select(RiskEvent)).previous_prediction_id is None


def test_out_of_order_inserts_use_prediction_time(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 85, days_ago=1)     # inserted first, newest
    add_prediction(db, org["eng1"], model, 20, days_ago=10)    # inserted later, older
    run(client, org["hr"])
    alert = db.scalar(select(RiskAlert))
    assert alert.latest_score == Decimal("85.00")


def test_inactive_employees_not_examined(client, db, org, staff, model) -> None:
    leaver, _ = staff(status=EmploymentStatus.INACTIVE)
    add_prediction(db, leaver, model, 99)
    summary = run(client, org["hr"])
    assert summary["examined_employees"] == 4 and summary["alerts_created"] == 0


def test_bounded_runs_with_cursor(client, db, org, model) -> None:
    for employee in (org["eng1"], org["eng2"], org["sales"]):
        add_prediction(db, employee, model, 90)
    first = run(client, org["hr"], limit=2)
    assert first["examined_employees"] == 2 and first["next_after_employee_id"] is not None
    second = run(client, org["hr"], limit=2, after_employee_id=first["next_after_employee_id"])
    assert second["next_after_employee_id"] is None
    assert first["alerts_created"] + second["alerts_created"] == 3
    for bad in ({"limit": 0}, {"limit": 5001}, {"after_employee_id": -1}, {"employee_id": 1}):
        assert client.post("/api/risk-monitoring/run", json=bad, headers=org["hr"]).status_code == 422


def test_concurrent_run_cannot_duplicate(client, db, org, model, monkeypatch) -> None:
    """Simulate a second run that missed the first run's events in its pre-check: the unique dedup key
    rejects the duplicates and the whole second run is rolled back."""
    from app.services import risk_monitoring as service

    add_prediction(db, org["eng1"], model, 80)
    run(client, org["hr"])
    before = counts(db)
    monkeypatch.setattr(service, "existing_event_keys", lambda db, keys: set())
    monkeypatch.setattr(service, "active_alerts", lambda db, ids: {})
    response = client.post("/api/risk-monitoring/run", json={}, headers=org["hr"])
    assert response.status_code == 409
    assert counts(db) == before


def test_run_audit_failure_rolls_back_everything(client, org, model, db, monkeypatch, session_factory) -> None:
    from app.api import risk_monitoring as api

    add_prediction(db, org["eng1"], model, 80)

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post("/api/risk-monitoring/run", json={}, headers=org["hr"])
    assert response.status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(RiskEvent.id))) == 0
        assert fresh.scalar(select(func.count(RiskAlert.id))) == 0
        assert fresh.scalar(select(func.count(Notification.id))) == 0


def test_monitoring_changes_no_employee_data(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 99)
    run(client, org["hr"])
    db.expire_all()
    assert db.get(Employee, org["eng1"].id).status == EmploymentStatus.ACTIVE
    assert db.scalar(select(func.count(AttritionPrediction.id))) == 1  # no new predictions


# --- Alert lifecycle ------------------------------------------------------------------------------


@pytest.fixture
def alert_id(client, db, org, model):
    add_prediction(db, org["eng1"], model, 80, days_ago=5)
    run(client, org["hr"])
    return db.scalar(select(RiskAlert.id))


def act(client, headers, alert_id, action, **body):
    return client.post(f"/api/risk-monitoring/alerts/{alert_id}/{action}", json=body, headers=headers)


def test_lifecycle_and_idempotency(client, db, org, alert_id) -> None:
    hr = org["hr"]
    ack = act(client, hr, alert_id, "acknowledge", note="Looking into workload")
    assert ack.status_code == 200
    body = ack.json()
    assert (body["status"], body["reviewer_notes"]) == ("ACKNOWLEDGED", "Looking into workload")
    assert body["acknowledged_at"] and body["acknowledged_by_user_id"]
    assert act(client, hr, alert_id, "acknowledge").json()["acknowledged_at"] == body["acknowledged_at"]  # no-op

    assert act(client, hr, alert_id, "resolve", outcome="RESOLVED").status_code == 422          # reason required
    assert act(client, hr, alert_id, "resolve", outcome="RESOLVED", reason="ok").status_code == 422
    assert act(client, hr, alert_id, "resolve", outcome="CLOSED", reason="Discussed").status_code == 422
    resolved = act(client, hr, alert_id, "resolve", outcome="RESOLVED", reason="Discussed workload with manager")
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED"
    assert act(client, hr, alert_id, "resolve", outcome="RESOLVED", reason="Again please").status_code == 200  # no-op
    assert act(client, hr, alert_id, "resolve", outcome="DISMISSED", reason="Change of mind").status_code == 409
    assert act(client, hr, alert_id, "acknowledge").status_code == 409

    actions = [log.action for log in db.scalars(select(AuditLog).where(AuditLog.entity_type == "risk_alert",
                                                                         AuditLog.action != "risk.alert_created"))]
    assert actions == ["risk.alert_acknowledged", "risk.alert_resolved"]  # idempotent repeats add nothing


def test_dismiss_directly_from_open(client, org, alert_id) -> None:
    response = act(client, org["hr"], alert_id, "resolve", outcome="DISMISSED", reason="Known data issue")
    assert response.json()["status"] == "DISMISSED"


def test_resolved_alert_is_not_reopened_but_new_crossing_creates_new_alert(client, db, org, model, alert_id) -> None:
    act(client, org["hr"], alert_id, "resolve", outcome="RESOLVED", reason="Discussed and agreed actions")
    add_prediction(db, org["eng1"], model, 82, days_ago=3)      # still above: no new crossing
    assert run(client, org["hr"])["alerts_created"] == 0
    add_prediction(db, org["eng1"], model, 30, days_ago=2)
    run(client, org["hr"])
    add_prediction(db, org["eng1"], model, 77, days_ago=1)      # crosses again
    assert run(client, org["hr"])["alerts_created"] == 1
    statuses = [a.status for a in db.scalars(select(RiskAlert).order_by(RiskAlert.id))]
    assert statuses == [AlertStatus.RESOLVED, AlertStatus.OPEN]


@pytest.mark.simulated_concurrent_writer
def test_concurrent_reviewer_action_conflicts(client, org, alert_id, monkeypatch, session_factory) -> None:
    from app.services import risk_monitoring as service

    original = service.transition

    def other_reviewer_first(db, target_id, allowed_from, values):
        with session_factory() as other:
            alert = other.get(RiskAlert, target_id)
            alert.status, alert.resolution_reason = AlertStatus.DISMISSED, "Closed by another reviewer"
            other.commit()
        return original(db, target_id, allowed_from, values)

    monkeypatch.setattr(service, "transition", other_reviewer_first)
    response = act(client, org["hr"], alert_id, "acknowledge")
    assert response.status_code == 409
    with session_factory() as fresh:
        assert fresh.get(RiskAlert, alert_id).status == AlertStatus.DISMISSED


def test_reviewer_action_audit_failure_rolls_back(client, org, alert_id, monkeypatch, session_factory) -> None:
    from app.api import risk_monitoring as api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/risk-monitoring/alerts/{alert_id}/acknowledge", json={}, headers=org["hr"])
    assert response.status_code == 500
    with session_factory() as fresh:
        assert fresh.get(RiskAlert, alert_id).status == AlertStatus.OPEN


def test_reading_notification_does_not_touch_alert(client, db, org, alert_id, headers_for) -> None:
    hr_user = db.scalar(select(Notification.user_id))
    from app.core.security import create_access_token
    from tests.conftest import auth_header

    headers = auth_header(create_access_token(hr_user).token)
    note = client.get("/api/notifications/me", headers=headers).json()["items"][0]
    client.post(f"/api/notifications/{note['id']}/read", headers=headers)
    db.expire_all()
    assert db.get(RiskAlert, alert_id).status == AlertStatus.OPEN


def test_assignment_rules(client, db, org, staff, alert_id, headers_for) -> None:
    hr = org["hr"]
    sales_manager, _ = staff(MANAGER, department="Sales")
    plain_employee, _ = staff(department="Engineering")
    subject_user = org["eng1"].user_id

    for user_id in (sales_manager.user_id, plain_employee.user_id, subject_user, 99999):
        assert act(client, hr, alert_id, "assign", user_id=user_id).status_code == 422, user_id

    response = act(client, hr, alert_id, "assign", user_id=org["manager"].user_id)
    assert response.status_code == 200 and response.json()["assigned_to_user_id"] == org["manager"].user_id
    note = db.scalar(select(Notification).where(Notification.user_id == org["manager"].user_id))
    assert note.title == "Risk alert assigned to you"
    mine = client.get("/api/risk-monitoring/alerts", params={"assigned_to_me": "true"}, headers=org["manager_h"]).json()
    assert [a["id"] for a in mine["items"]] == [alert_id]
    assert act(client, org["manager_h"], alert_id, "assign", user_id=org["manager"].user_id).status_code == 403


# --- Permissions and isolation ----------------------------------------------------------------------


def test_manager_scope(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 80)
    add_prediction(db, org["sales"], model, 90)
    run(client, org["hr"])
    sales_alert = db.scalar(select(RiskAlert.id).where(RiskAlert.employee_id == org["sales"].id))
    eng_alert = db.scalar(select(RiskAlert.id).where(RiskAlert.employee_id == org["eng1"].id))
    headers = org["manager_h"]

    assert [a["id"] for a in client.get("/api/risk-monitoring/alerts", headers=headers).json()["items"]] == [eng_alert]
    assert client.get(f"/api/risk-monitoring/alerts/{sales_alert}", headers=headers).status_code == 404
    assert act(client, headers, sales_alert, "acknowledge").status_code == 404
    assert client.get(f"/api/risk-monitoring/employees/{org['sales'].id}/history", headers=headers).status_code == 404
    assert client.get(f"/api/risk-monitoring/employees/{org['eng1'].id}/history", headers=headers).json()["total"] == 1
    assert act(client, headers, eng_alert, "acknowledge").status_code == 200

    summary = client.get("/api/risk-monitoring/summary", headers=headers).json()
    assert sum(summary["alerts_by_status"].values()) == 1  # Sales is invisible, even in counts
    assert set(summary["active_alerts_by_department"]) == {str(org["eng1"].department_id)}
    assert client.post("/api/risk-monitoring/run", json={}, headers=headers).status_code == 403


def test_nobody_sees_alerts_about_themselves(client, db, staff, model, headers_for) -> None:
    hr_employee, hr_self = staff(HR)
    add_prediction(db, hr_employee, model, 90)
    run(client, headers_for(ADMIN))
    alert_id = db.scalar(select(RiskAlert.id))
    assert client.get("/api/risk-monitoring/alerts", headers=hr_self).json()["total"] == 0
    assert client.get(f"/api/risk-monitoring/alerts/{alert_id}", headers=hr_self).status_code == 404
    assert client.get(f"/api/risk-monitoring/employees/{hr_employee.id}/history", headers=hr_self).status_code == 404
    # The subject is not notified about their own alert.
    assert db.scalar(select(Notification).where(Notification.user_id == hr_employee.user_id)) is None


def test_employees_have_no_access(client, db, staff, org, alert_id) -> None:
    _, employee_h = staff(department="Engineering")
    for method, path in [("post", "/api/risk-monitoring/run"), ("get", "/api/risk-monitoring/alerts"),
                         ("get", f"/api/risk-monitoring/alerts/{alert_id}"),
                         ("post", f"/api/risk-monitoring/alerts/{alert_id}/acknowledge"),
                         ("post", f"/api/risk-monitoring/alerts/{alert_id}/resolve"),
                         ("get", f"/api/risk-monitoring/employees/{org['eng1'].id}/history"),
                         ("get", "/api/risk-monitoring/summary")]:
        kwargs = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(path, headers=employee_h, **kwargs).status_code == 403, path


def test_alert_list_filters_and_payload_privacy(client, db, org, model) -> None:
    add_prediction(db, org["eng1"], model, 80)
    add_prediction(db, org["eng2"], model, 75)
    run(client, org["hr"])
    hr = org["hr"]
    listing = client.get("/api/risk-monitoring/alerts", params={"page_size": 1}, headers=hr).json()
    assert (listing["total"], listing["pages"]) == (2, 2)
    assert client.get("/api/risk-monitoring/alerts", params={"employee_id": org["eng2"].id}, headers=hr).json()["total"] == 1
    assert client.get("/api/risk-monitoring/alerts", params={"status": "RESOLVED"}, headers=hr).json()["total"] == 0
    assert client.get("/api/risk-monitoring/alerts", params={"page_size": 101}, headers=hr).status_code == 422
    text = client.get("/api/risk-monitoring/alerts", headers=hr).text
    for forbidden in ("tenure_months", "features", "artifact", "late_rate"):
        assert forbidden not in text


def test_endpoints_require_authentication(client) -> None:
    for method, path in [("post", "/api/risk-monitoring/run"), ("get", "/api/risk-monitoring/alerts"),
                         ("get", "/api/risk-monitoring/summary"), ("get", "/api/risk-monitoring/employees/1/history")]:
        assert getattr(client, method)(path).status_code == 401, path
