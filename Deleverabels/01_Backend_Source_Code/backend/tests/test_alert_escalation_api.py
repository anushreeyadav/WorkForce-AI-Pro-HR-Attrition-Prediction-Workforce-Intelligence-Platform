"""Risk-alert review deadlines, escalation and notification priority over SYNTHETIC alerts."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.main import app
from app.models import (
    AlertPriority,
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    EscalationStage,
    ModelVersionStatus,
    Notification,
    RiskAlert,
    RiskAlertEscalation,
    RiskCategory,
    RiskEventType,
    User,
    UserRole,
)
from app.models.base import utcnow
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
EVALUATE = "/api/risk-monitoring/escalations/evaluate"
SCORE = "81.37"
NOTE = "ZZ-private-reviewer-note-ZZ"


def ago(hours: float):
    return utcnow() - timedelta(hours=hours)


@pytest.fixture
def org(staff, headers_for, db):
    manager, manager_h = staff(MANAGER, department="Engineering", full_name="Mona Manager")
    eng, eng_h = staff(department="Engineering", full_name="Edith Engineer")
    sales, _ = staff(department="Sales", full_name="Sam Seller")
    hr = headers_for(HR, "hr.sla@example.com")
    admin = headers_for(ADMIN, "admin.sla@example.com")
    ids = {u.email: u.id for u in db.scalars(select(User))}
    return {"manager": manager, "manager_h": manager_h, "eng": eng, "eng_h": eng_h, "sales": sales, "hr": hr,
            "admin": admin, "hr_id": ids["hr.sla@example.com"], "admin_id": ids["admin.sla@example.com"],
            "manager_user_id": manager.user_id}


@pytest.fixture
def make_alert(db):
    models: list[AttritionModelVersion] = []

    def _make(employee, hours_ago=30.0, priority=AlertPriority.HIGH, status=AlertStatus.OPEN, **fields):
        if not models:
            models.append(AttritionModelVersion(
                model_version="lr-20260101000000-sla", feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                artifact_sha256="0" * 64, training_rows=100, evaluation={}))
            db.add(models[0])
            db.flush()
        prediction = AttritionPrediction(
            employee_id=employee.id, model_version_id=models[0].id, feature_schema_version=FEATURE_SCHEMA_VERSION,
            risk_score=Decimal(SCORE), risk_category=RiskCategory.HIGH, features={"tenure_months": 987},
            explanation={"factors": [], "missing_inputs": []}, predicted_at=utcnow())
        db.add(prediction)
        db.flush()
        detected = ago(hours_ago)
        alert = RiskAlert(employee_id=employee.id, trigger_type=RiskEventType.THRESHOLD_CROSSED, priority=priority,
                          status=status, latest_prediction_id=prediction.id, latest_score=Decimal(SCORE),
                          latest_category=RiskCategory.HIGH, model_version=models[0].model_version, event_count=1,
                          first_detected_at=detected, last_observed_at=detected, reviewer_notes=NOTE, **fields)
        db.add(alert)
        db.commit()
        return alert

    return _make


def evaluate(client, headers, **body) -> dict:
    response = client.post(EVALUATE, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def count(db, model) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


def notifications(db, category=None) -> list[Notification]:
    db.expire_all()
    stmt = select(Notification).order_by(Notification.user_id)
    if category:
        stmt = stmt.where(Notification.category == category)
    return list(db.scalars(stmt))


# --- Access -----------------------------------------------------------------------------------------


def test_requires_authentication_and_denies_employees(client, org) -> None:
    assert client.post(EVALUATE, json={}).status_code == 401
    assert client.post(EVALUATE, json={}, headers=org["eng_h"]).status_code == 403
    for who in ("admin", "hr", "manager_h"):
        assert client.post(EVALUATE, json={}, headers=org[who]).status_code == 200


@pytest.mark.parametrize("body", [{"limit": 0}, {"limit": 5001}, {"after_alert_id": -1}, {"department_id": 0},
                                  {"unknown": 1}, {"employee_id": 1}])
def test_invalid_requests_are_rejected(client, org, body) -> None:
    assert client.post(EVALUATE, json=body, headers=org["hr"]).status_code == 422


# --- Escalation policy -------------------------------------------------------------------------------


def test_overdue_alert_escalates_once_to_admin_and_hr(client, db, org, make_user, make_alert) -> None:
    make_user(email="inactive.hr@example.com", role=HR, is_active=False)
    alert = make_alert(org["eng"], hours_ago=30)
    updated_at = alert.updated_at
    body = evaluate(client, org["hr"])
    assert (body["examined_alerts"], body["overdue"], body["notifications_created"]) == (1, 1, 2)
    assert [(e["alert_id"], e["stage"], e["priority"], e["recipient_count"]) for e in body["new_escalations"]] == [
        (alert.id, "OVERDUE", "HIGH", 2)]
    sent = notifications(db, "risk_alert_overdue")
    assert sorted(n.user_id for n in sent) == sorted([org["hr_id"], org["admin_id"]])
    assert {n.message for n in sent} == {
        f"Risk alert #{alert.id} (HIGH priority) has not been acknowledged and is past its review deadline."}
    assert {(n.entity_type, n.entity_id) for n in sent} == {("risk_alert", str(alert.id))}

    again = evaluate(client, org["admin"])
    assert (again["new_escalations"], again["already_escalated"], again["notifications_created"]) == ([], 1, 0)
    assert count(db, RiskAlertEscalation) == 1 and len(notifications(db)) == 2
    db.refresh(alert)  # escalation never changes the alert
    assert (alert.status, alert.priority, alert.assigned_to_user_id, alert.acknowledged_at, alert.updated_at) == (
        AlertStatus.OPEN, AlertPriority.HIGH, None, None, updated_at)


def test_approaching_then_overdue_are_separate_stages(client, db, org, make_alert) -> None:
    alert = make_alert(org["eng"], hours_ago=20)  # HIGH: 24h window, approaching from 18h
    body = evaluate(client, org["hr"])
    assert body["approaching"] == 1 and body["new_escalations"][0]["stage"] == "APPROACHING"
    assert len(notifications(db, "risk_alert_due_soon")) == 2
    assert evaluate(client, org["hr"])["new_escalations"] == []

    alert.first_detected_at = ago(25)  # time passes
    db.commit()
    body = evaluate(client, org["hr"])
    assert [e["stage"] for e in body["new_escalations"]] == ["OVERDUE"]
    assert len(notifications(db, "risk_alert_overdue")) == 2
    assert {(e.alert_id, e.stage) for e in db.scalars(select(RiskAlertEscalation))} == {
        (alert.id, EscalationStage.APPROACHING), (alert.id, EscalationStage.OVERDUE)}
    assert evaluate(client, org["hr"])["notifications_created"] == 0


@pytest.mark.parametrize("priority, hours, expected", [
    (AlertPriority.HIGH, 23, "approaching"), (AlertPriority.MEDIUM, 23, "on_track"), (AlertPriority.MEDIUM, 73, "overdue"),
    (AlertPriority.LOW, 73, "on_track"), (AlertPriority.LOW, 127, "approaching"), (AlertPriority.LOW, 169, "overdue"),
])
def test_deadline_depends_on_priority(client, org, make_alert, priority, hours, expected) -> None:
    make_alert(org["eng"], hours_ago=hours, priority=priority)
    assert evaluate(client, org["hr"])[expected] == 1


def test_acknowledged_resolved_and_dismissed_alerts_are_not_escalated(client, db, org, make_alert, make_employee) -> None:
    department = org["eng"].department
    make_alert(org["eng"], hours_ago=500, status=AlertStatus.ACKNOWLEDGED, acknowledged_at=ago(1))
    make_alert(make_employee(department), hours_ago=500, status=AlertStatus.RESOLVED, resolved_at=ago(1),
               resolution_reason="Reviewed")
    make_alert(make_employee(department), hours_ago=500, status=AlertStatus.DISMISSED, resolved_at=ago(1),
               resolution_reason="Reviewed")
    make_alert(make_employee(department), hours_ago=1)
    body = evaluate(client, org["hr"])
    assert (body["examined_alerts"], body["acknowledged"], body["on_track"], body["overdue"]) == (2, 1, 1, 0)
    assert body["new_escalations"] == [] and count(db, RiskAlertEscalation) == 0 and notifications(db) == []


def test_approaching_stage_can_be_disabled(client, db, org, make_alert, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_ESCALATION_NOTIFY_APPROACHING", "false")
    get_settings.cache_clear()
    make_alert(org["eng"], hours_ago=20)
    make_alert(org["sales"], hours_ago=30)
    body = evaluate(client, org["hr"])
    assert body["approaching_not_notified"] == 1 and [e["stage"] for e in body["new_escalations"]] == ["OVERDUE"]


# --- Recipients ----------------------------------------------------------------------------------------


def test_monitored_employee_is_never_notified(client, db, org, staff, make_alert) -> None:
    hr_employee, hr_employee_h = staff(HR, department="Sales")
    alert = make_alert(hr_employee)
    assert evaluate(client, hr_employee_h)["examined_alerts"] == 0  # nobody evaluates alerts about themselves
    body = evaluate(client, org["admin"])
    assert body["new_escalations"][0]["recipient_count"] == 2
    recipients = {n.user_id for n in notifications(db)}
    assert hr_employee.user_id not in recipients and recipients == {org["hr_id"], org["admin_id"]}
    assert all(n.entity_id == str(alert.id) for n in notifications(db))


def test_no_eligible_recipient_records_nothing(client, db, org, make_alert, make_user, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_ESCALATION_RECIPIENT_ROLES", '["ADMIN"]')
    get_settings.cache_clear()
    db.get(User, org["admin_id"]).is_active = False
    db.commit()
    alert = make_alert(org["eng"])
    body = evaluate(client, org["hr"])
    assert body["no_eligible_recipient_alert_ids"] == [alert.id] and body["new_escalations"] == []
    assert count(db, RiskAlertEscalation) == 0 and notifications(db) == []

    new_admin = make_user(email="new.admin@example.com", role=ADMIN)
    body = evaluate(client, org["hr"])
    assert body["new_escalations"][0]["recipient_count"] == 1
    assert [n.user_id for n in notifications(db)] == [new_admin.id]  # HR is not a configured recipient here


# --- Scope ------------------------------------------------------------------------------------------------


def test_manager_scope(client, db, org, make_alert) -> None:
    eng_alert = make_alert(org["eng"])
    make_alert(org["sales"])
    manager_alert = make_alert(org["manager"])  # about the manager: never in their own evaluation
    body = evaluate(client, org["manager_h"])
    assert body["examined_alerts"] == 1 and [e["alert_id"] for e in body["new_escalations"]] == [eng_alert.id]
    assert org["manager_user_id"] not in {n.user_id for n in notifications(db)}  # reviewers are ADMIN/HR only
    sales = org["sales"].department_id
    assert client.post(EVALUATE, json={"department_id": sales}, headers=org["manager_h"]).status_code == 404
    assert client.post(EVALUATE, json={"department_id": 9999}, headers=org["hr"]).status_code == 404
    hr = evaluate(client, org["hr"], department_id=sales)
    assert hr["examined_alerts"] == 1 and len(hr["new_escalations"]) == 1
    assert [e["alert_id"] for e in evaluate(client, org["hr"])["new_escalations"]] == [manager_alert.id]


# --- Bounds, idempotency, transactions -----------------------------------------------------------------


def test_bounded_batches_with_cursor(client, db, org, make_alert, make_employee, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_ESCALATION_MAX_BATCH", "2")
    get_settings.cache_clear()
    alerts = [make_alert(make_employee(org["eng"].department)) for _ in range(5)]
    seen, cursor, calls = [], 0, 0
    while cursor is not None:
        body = evaluate(client, org["hr"], limit=100, after_alert_id=cursor)
        assert body["examined_alerts"] <= 2
        seen += [e["alert_id"] for e in body["new_escalations"]]
        cursor, calls = body["next_after_alert_id"], calls + 1
    assert seen == [a.id for a in alerts] and calls == 3
    assert count(db, RiskAlertEscalation) == 5 and len(notifications(db)) == 10


def test_query_count_is_bounded(client, org, make_alert, make_employee, count_queries) -> None:
    def measure() -> int:
        start = len(count_queries())
        evaluate(client, org["hr"])
        return len(count_queries()) - start

    make_alert(org["eng"], hours_ago=1)
    small = measure()
    for _ in range(8):
        make_alert(make_employee(org["eng"].department), hours_ago=1)
    assert measure() == small  # no per-alert queries when nothing is escalated


def test_concurrent_escalation_conflict_keeps_nothing(client, db, org, make_alert, monkeypatch) -> None:
    from app.services import alert_sla

    make_alert(org["eng"])
    evaluate(client, org["hr"])
    monkeypatch.setattr(alert_sla, "recorded_stages", lambda db, ids: set())  # simulate a race past the pre-check
    response = client.post(EVALUATE, json={}, headers=org["admin"])
    assert response.status_code == 409
    assert count(db, RiskAlertEscalation) == 1 and len(notifications(db)) == 2
    assert db.scalar(select(func.count()).where(AuditLog.action == "risk.escalation_evaluated")) == 1


def test_audit_failure_rolls_back(client, db, org, make_alert, monkeypatch, session_factory) -> None:
    from app.api import risk_monitoring as api

    make_alert(org["eng"])

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(EVALUATE, json={}, headers=org["hr"]).status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(RiskAlertEscalation.id))) == 0
        assert fresh.scalar(select(func.count(Notification.id))) == 0


def test_audit_rows_are_minimal(client, db, org, make_alert) -> None:
    alert = make_alert(org["eng"])
    evaluate(client, org["hr"])
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action.like("risk.%"))))
    assert {r.action for r in rows} == {"risk.alert_review_escalated", "risk.escalation_evaluated"}
    escalated = next(r for r in rows if r.action == "risk.alert_review_escalated")
    assert escalated.entity_id == str(alert.id)
    assert set(escalated.details) == {"escalation_id", "stage", "priority", "review_due_at", "recipient_count"}
    text = " ".join(str(r.details) for r in rows) + " ".join(n.message + n.title for n in notifications(db))
    for word in (SCORE, "987", NOTE, "Edith", "Mona", "Sam", "@", "score", "feature"):
        assert word not in text, word


def test_evaluation_never_calls_models_or_creates_alerts(client, db, org, make_alert, monkeypatch) -> None:
    from app.services import attrition

    def forbidden(*args, **kwargs):
        raise AssertionError("model code must not run during escalation evaluation")

    for name in ("predict", "train_model", "load_active", "extract_features", "activate_model"):
        monkeypatch.setattr(attrition, name, forbidden)
    make_alert(org["eng"])
    before = (count(db, RiskAlert), count(db, AttritionPrediction))
    evaluate(client, org["hr"])
    assert (count(db, RiskAlert), count(db, AttritionPrediction)) == before


# --- Read-only SLA status ---------------------------------------------------------------------------------


def test_alert_responses_include_sla_without_writing(client, db, org, make_alert) -> None:
    overdue = make_alert(org["eng"], hours_ago=30)
    make_alert(org["sales"], hours_ago=1, priority=AlertPriority.LOW)
    before = (count(db, AuditLog), count(db, Notification), count(db, RiskAlertEscalation))
    detail = client.get(f"/api/risk-monitoring/alerts/{overdue.id}", headers=org["hr"]).json()
    assert detail["sla"]["state"] == "OVERDUE" and detail["sla"]["review_window_hours"] == 24
    listing = client.get("/api/risk-monitoring/alerts", headers=org["hr"]).json()
    assert {i["sla"]["state"] for i in listing["items"]} == {"OVERDUE", "ON_TRACK"}
    summary = client.get("/api/risk-monitoring/summary", headers=org["hr"]).json()
    assert summary["active_alerts_by_sla_state"] == {"ON_TRACK": 1, "APPROACHING": 0, "OVERDUE": 1, "ACKNOWLEDGED": 0}
    manager = client.get("/api/risk-monitoring/summary", headers=org["manager_h"]).json()
    assert manager["active_alerts_by_sla_state"]["OVERDUE"] == 1 and manager["active_alerts_by_sla_state"]["ON_TRACK"] == 0
    assert (count(db, AuditLog), count(db, Notification), count(db, RiskAlertEscalation)) == before


# --- Notifications ---------------------------------------------------------------------------------------


def test_notification_priority_and_ordering(client, db, org, make_alert) -> None:
    make_alert(org["eng"], hours_ago=20)  # approaching -> HIGH notification
    evaluate(client, org["hr"])
    overdue = make_alert(org["sales"], hours_ago=30)  # overdue -> URGENT notification
    evaluate(client, org["hr"])
    db.add(Notification(user_id=org["hr_id"], category="leave", title="Leave request", message="A request is waiting.",
                        created_at=utcnow() + timedelta(minutes=5)))  # newest, but NORMAL
    db.commit()

    recent = client.get("/api/notifications/me", headers=org["hr"]).json()["items"]
    assert [n["category"] for n in recent] == ["leave", "risk_alert_overdue", "risk_alert_due_soon"]  # unchanged default
    assert [n["priority"] for n in recent] == ["NORMAL", "URGENT", "HIGH"]
    by_priority = client.get("/api/notifications/me", params={"sort": "priority"}, headers=org["hr"]).json()["items"]
    assert [n["category"] for n in by_priority] == ["risk_alert_overdue", "risk_alert_due_soon", "leave"]

    urgent = by_priority[0]
    read = client.post(f"/api/notifications/{urgent['id']}/read", headers=org["hr"]).json()
    assert read["is_read"] and read["priority"] == "URGENT"
    after = client.get("/api/notifications/me", params={"sort": "priority"}, headers=org["hr"]).json()["items"]
    assert [n["category"] for n in after] == ["risk_alert_due_soon", "leave", "risk_alert_overdue"]  # unread first
    unread = client.get("/api/notifications/me", params={"sort": "priority", "unread_only": True}, headers=org["hr"]).json()
    assert unread["total"] == 2
    db.refresh(overdue)
    assert overdue.status == AlertStatus.OPEN and overdue.acknowledged_at is None  # reading never acknowledges
    assert client.get("/api/notifications/me", params={"sort": "urgent"}, headers=org["hr"]).status_code == 422
    other = client.post(f"/api/notifications/{urgent['id']}/read", headers=org["admin"])
    assert other.status_code == 404  # still only your own notifications


def test_notification_ordering_ties_are_stable(client, db, org) -> None:
    same = utcnow()
    for _ in range(4):
        db.add(Notification(user_id=org["hr_id"], category="risk_alert", title="t", message="m", created_at=same))
    db.commit()
    pages = [client.get("/api/notifications/me", params={"sort": "priority", "page": p, "page_size": 2},
                        headers=org["hr"]).json()["items"] for p in (1, 2)]
    ids = [n["id"] for page in pages for n in page]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 4


# --- Activity feed integration ------------------------------------------------------------------------------


def test_escalations_appear_once_in_the_activity_feed(client, db, org, make_alert) -> None:
    eng_alert = make_alert(org["eng"])
    make_alert(org["sales"], priority=AlertPriority.MEDIUM, hours_ago=100)
    make_alert(org["manager"])
    evaluate(client, org["hr"])
    evaluate(client, org["hr"])  # idempotent: still one row per alert and stage
    escalations = {e.alert_id: e for e in db.scalars(select(RiskAlertEscalation))}
    assert len(escalations) == 3

    feed = client.get("/api/workforce-activity/feed", params={"category": "risk_escalation"}, headers=org["hr"]).json()
    assert feed["total"] == 3
    assert {i["event_id"] for i in feed["items"]} == {f"risk_alert_escalation:{e.id}:overdue" for e in escalations.values()}
    assert {i["kind"] for i in feed["items"]} == {"RISK_EVENT"} and {i["priority"] for i in feed["items"]} == {"HIGH", "MEDIUM"}
    everything = client.get("/api/workforce-activity/feed", params={"page_size": 100}, headers=org["hr"]).json()
    assert all(i["source_type"] != "audit_logs" for i in everything["items"])  # escalation audits are not re-listed
    summary = client.get("/api/workforce-activity/summary", params={"kind": "RISK_EVENT"}, headers=org["hr"]).json()
    assert summary["by_category"]["risk_escalation"] == 3

    manager = client.get("/api/workforce-activity/feed", params={"category": "risk_escalation"}, headers=org["manager_h"]).json()
    assert [i["employee_id"] for i in manager["items"]] == [eng_alert.employee_id]  # own department, never self
    text = client.get("/api/workforce-activity/feed", headers=org["hr"]).text
    for word in (SCORE, NOTE, "Edith", "Mona", "Sam"):
        assert word not in text
