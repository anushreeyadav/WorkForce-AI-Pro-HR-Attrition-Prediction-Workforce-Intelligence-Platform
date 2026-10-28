"""HR intervention workflow. All employees, alerts and notes are SYNTHETIC test data."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.interventions import _today
from app.main import app
from app.models import (
    AlertPriority,
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Employee,
    EmploymentStatus,
    Intervention,
    InterventionCategory,
    InterventionEvent,
    InterventionPriority,
    InterventionStatus,
    ModelVersionStatus,
    Notification,
    RiskAlert,
    RiskCategory,
    RiskEventType,
    UserRole,
)
from app.models.base import utcnow
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/interventions"


@pytest.fixture
def org(staff, headers_for):
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng, eng_h = staff(department="Engineering")
    sales, _ = staff(department="Sales")
    sales_manager, sales_manager_h = staff(MANAGER, department="Sales")
    return {"manager": manager, "manager_h": manager_h, "eng": eng, "eng_h": eng_h, "sales": sales,
            "sales_manager": sales_manager, "sales_manager_h": sales_manager_h, "hr": headers_for(HR, "hr.lead@example.com")}


@pytest.fixture
def make_alert(db):
    model = {}

    def _make(employee, status=AlertStatus.OPEN) -> RiskAlert:
        if "row" not in model:
            model["row"] = AttritionModelVersion(model_version="lr-20260101000000-0000beef", feature_schema_version=FEATURE_SCHEMA_VERSION,
                                                 status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                                                 artifact_sha256="0" * 64, training_rows=100, evaluation={})
            db.add(model["row"])
            db.flush()
        prediction = AttritionPrediction(employee_id=employee.id, model_version_id=model["row"].id,
                                         feature_schema_version=FEATURE_SCHEMA_VERSION, risk_score=Decimal("80"),
                                         risk_category=RiskCategory.HIGH, features={}, predicted_at=utcnow())
        db.add(prediction)
        db.flush()
        alert = RiskAlert(employee_id=employee.id, trigger_type=RiskEventType.THRESHOLD_CROSSED, priority=AlertPriority.HIGH,
                          status=status, latest_prediction_id=prediction.id, latest_score=Decimal("80"),
                          latest_category=RiskCategory.HIGH, model_version=model["row"].model_version, event_count=1,
                          first_detected_at=utcnow(), last_observed_at=utcnow())
        db.add(alert)
        db.commit()
        return alert

    return _make


def body(employee, **overrides) -> dict:
    return {"employee_id": employee.id, "category": "WORKLOAD_REVIEW", "title": "Review sprint workload",
            "description": "Agree priorities for the next release.", "priority": "HIGH", **overrides}


def create(client, headers, employee, **overrides):
    return client.post(BASE, json=body(employee, **overrides), headers=headers)


def act(client, headers, iid, action, **payload):
    return client.post(f"{BASE}/{iid}/{action}", json=payload, headers=headers)


def events(db, iid) -> list[InterventionEvent]:
    db.expire_all()
    return list(db.scalars(select(InterventionEvent).where(InterventionEvent.intervention_id == iid).order_by(InterventionEvent.version)))


# --- Create ---------------------------------------------------------------------------------------


def test_create_with_assignment_history_audit_and_notification(client, db, org) -> None:
    due = (_today() + timedelta(days=14)).isoformat()
    response = create(client, org["hr"], org["eng"], assigned_to_user_id=org["manager"].user_id, due_date=due)
    assert response.status_code == 201, response.text
    data = response.json()
    assert (data["status"], data["version"], data["priority"], data["due_date"]) == ("OPEN", 1, "HIGH", due)
    assert data["employee"]["id"] == org["eng"].id and data["is_overdue"] is False

    assert [(e.event_type.value, e.version) for e in events(db, data["id"])] == [("CREATED", 1)]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "intervention.created"))
    assert log.details == {"employee_id": org["eng"].id, "risk_alert_id": None, "category": "WORKLOAD_REVIEW",
                           "priority": "HIGH", "assigned_to_user_id": org["manager"].user_id}
    notes = db.scalars(select(Notification)).all()
    assert [(n.user_id, n.message) for n in notes] == [
        (org["manager"].user_id, f"Intervention #{data['id']} (HIGH priority) has been assigned to you.")]
    assert all(org["eng"].user_id != n.user_id for n in notes)  # the employee is never notified


@pytest.mark.parametrize(
    "overrides",
    [{"category": "PERFORMANCE_WARNING"}, {"title": "ab"}, {"title": "x" * 151}, {"description": "d" * 1001},
     {"priority": "URGENT"}, {"status": "COMPLETED"}, {"version": 5}, {"created_by_user_id": 1},
     {"idempotency_key": "short"}, {"idempotency_key": "bad key with spaces!"}, {"employee_id": 0}],
    ids=["category", "title-short", "title-long", "description-long", "priority", "status", "version", "created-by",
         "key-short", "key-chars", "employee-zero"],
)
def test_create_validation(client, org, overrides) -> None:
    assert create(client, org["hr"], org["eng"], **overrides).status_code == 422


@pytest.mark.parametrize("days", [-1, 367])
def test_due_date_bounds(client, org, days) -> None:
    due = (_today() + timedelta(days=days)).isoformat()
    assert create(client, org["hr"], org["eng"], due_date=due).status_code == 422


def test_idempotent_create(client, db, org) -> None:
    payload = {"idempotency_key": "req-0001-abcdef", "assigned_to_user_id": org["manager"].user_id}
    first = create(client, org["hr"], org["eng"], **payload)
    again = create(client, org["hr"], org["eng"], **payload)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert db.scalar(select(func.count(Intervention.id))) == 1
    assert db.scalar(select(func.count(Notification.id))) == 1  # no duplicate notification on retry
    assert create(client, org["manager_h"], org["eng"], idempotency_key="req-0001-abcdef").status_code == 409


# --- Alert linking --------------------------------------------------------------------------------


def test_alert_linking_scope_and_duplicates(client, db, org, make_alert) -> None:
    eng_alert, sales_alert = make_alert(org["eng"]), make_alert(org["sales"])
    linked = create(client, org["manager_h"], org["eng"], risk_alert_id=eng_alert.id)
    assert linked.status_code == 201 and linked.json()["risk_alert_id"] == eng_alert.id

    mismatch = create(client, org["hr"], org["sales"], risk_alert_id=eng_alert.id)
    assert mismatch.status_code == 422
    hidden = create(client, org["manager_h"], org["eng"], risk_alert_id=sales_alert.id)
    missing = create(client, org["manager_h"], org["eng"], risk_alert_id=99999)
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()

    duplicate = create(client, org["hr"], org["eng"], risk_alert_id=eng_alert.id)
    assert duplicate.status_code == 409
    other_purpose = create(client, org["hr"], org["eng"], risk_alert_id=eng_alert.id, category="CAREER_DEVELOPMENT")
    assert other_purpose.status_code == 201
    # Once the first is finished, the same alert and category may be used again.
    act(client, org["hr"], linked.json()["id"], "cancel", reason="Superseded by a broader plan")
    assert create(client, org["hr"], org["eng"], risk_alert_id=eng_alert.id).status_code == 201


def test_completing_never_touches_the_alert(client, db, org, make_alert) -> None:
    alert = make_alert(org["eng"])
    iid = create(client, org["hr"], org["eng"], risk_alert_id=alert.id).json()["id"]
    act(client, org["hr"], iid, "start")
    act(client, org["hr"], iid, "complete", outcome="Workload rebalanced across the team")
    db.expire_all()
    stored = db.get(RiskAlert, alert.id)
    assert (stored.status, stored.resolved_at, stored.resolution_reason) == (AlertStatus.OPEN, None, None)
    assert db.get(Employee, org["eng"].id).status == EmploymentStatus.ACTIVE


def test_active_alert_key_unique_at_database_level(db, org, make_alert) -> None:
    alert = make_alert(org["eng"])
    for _ in range(2):
        db.add(Intervention(employee_id=org["eng"].id, risk_alert_id=alert.id, category=InterventionCategory.RECOGNITION,
                            title="Duplicate", priority=InterventionPriority.LOW, status=InterventionStatus.OPEN,
                            version=1, active_alert_key=f"{alert.id}:RECOGNITION"))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        db.commit()
    db.rollback()


def test_check_constraints(db, org) -> None:
    db.add(Intervention(employee_id=org["eng"].id, category=InterventionCategory.RECOGNITION, title="No outcome",
                        priority=InterventionPriority.LOW, status=InterventionStatus.COMPLETED, version=1))
    with pytest.raises(IntegrityError, match="CHECK"):
        db.commit()
    db.rollback()


# --- Workflow -------------------------------------------------------------------------------------


@pytest.fixture
def iid(client, org):
    return create(client, org["hr"], org["eng"]).json()["id"]


def test_full_workflow_and_history(client, db, org, iid) -> None:
    hr = org["hr"]
    assert act(client, hr, iid, "complete", outcome="Not started yet").status_code == 409  # OPEN cannot complete
    started = act(client, hr, iid, "start")
    assert (started.json()["status"], started.json()["version"]) == ("IN_PROGRESS", 2)
    assert act(client, hr, iid, "start").json()["version"] == 2  # idempotent
    note = client.post(f"{BASE}/{iid}/notes", json={"note": "Met with the team lead; agreed two tasks to move."}, headers=hr)
    assert note.status_code == 201 and note.json()["event_type"] == "NOTE_ADDED"
    assert act(client, hr, iid, "complete").status_code == 422  # outcome required
    assert act(client, hr, iid, "complete", outcome="ok").status_code == 422
    done = act(client, hr, iid, "complete", outcome="Two tasks reassigned; workload now balanced")
    assert (done.json()["status"], done.json()["outcome"]) == ("COMPLETED", "Two tasks reassigned; workload now balanced")
    assert act(client, hr, iid, "complete", outcome="Second attempt at completing").json()["version"] == done.json()["version"]
    assert act(client, hr, iid, "start").status_code == 409  # never silently reactivated
    assert act(client, hr, iid, "cancel", reason="Changed my mind").status_code == 409
    assert client.patch(f"{BASE}/{iid}", json={"title": "New title"}, headers=hr).status_code == 409
    # Follow-up notes are still allowed after completion.
    assert client.post(f"{BASE}/{iid}/notes", json={"note": "Check-in after a month: fine."}, headers=hr).status_code == 201

    history = client.get(f"{BASE}/{iid}/history", headers=hr).json()["items"]
    assert [(h["version"], h["event_type"]) for h in history] == [
        (1, "CREATED"), (2, "STARTED"), (3, "NOTE_ADDED"), (4, "COMPLETED"), (5, "NOTE_ADDED")]
    assert history[3]["from_status"] == "IN_PROGRESS" and history[3]["to_status"] == "COMPLETED"


def test_cancel_requires_reason(client, org, iid) -> None:
    assert act(client, org["hr"], iid, "cancel").status_code == 422
    assert act(client, org["hr"], iid, "cancel", reason="no").status_code == 422
    cancelled = act(client, org["hr"], iid, "cancel", reason="Employee transferred to another team")
    assert cancelled.json()["status"] == "CANCELLED"
    assert act(client, org["hr"], iid, "cancel", reason="Repeat request here").json()["version"] == cancelled.json()["version"]
    assert act(client, org["hr"], iid, "start").status_code == 409


def test_patch_rules(client, db, org, iid) -> None:
    hr = org["hr"]
    updated = client.patch(f"{BASE}/{iid}", json={"priority": "LOW", "description": None}, headers=hr)
    assert updated.status_code == 200
    assert (updated.json()["priority"], updated.json()["description"], updated.json()["version"]) == ("LOW", None, 2)
    assert events(db, iid)[-1].details == {"fields": ["description", "priority"]}
    noop = client.patch(f"{BASE}/{iid}", json={"priority": "LOW"}, headers=hr)
    assert noop.json()["version"] == 2
    assert client.patch(f"{BASE}/{iid}", json={"title": None}, headers=hr).status_code == 422
    assert client.patch(f"{BASE}/{iid}", json={}, headers=hr).status_code == 422
    assert client.patch(f"{BASE}/{iid}", json={"status": "COMPLETED"}, headers=hr).status_code == 422
    assert client.patch(f"{BASE}/{iid}", json={"priority": "HIGH", "expected_version": 1}, headers=hr).status_code == 409
    log = db.scalar(select(AuditLog).where(AuditLog.action == "intervention.updated"))
    assert log.details == {"fields": ["description", "priority"]}  # field names only, never text


def test_concurrent_update_is_rejected(client, db, org, iid, monkeypatch, session_factory) -> None:
    from app.services import interventions as service

    original = service.apply_change

    def someone_else_first(db_, intervention, *args, **kwargs):
        with session_factory() as other:
            row = other.get(Intervention, intervention.id)
            row.version, row.priority = row.version + 1, InterventionPriority.LOW
            other.commit()
        return original(db_, intervention, *args, **kwargs)

    monkeypatch.setattr(service, "apply_change", someone_else_first)
    response = act(client, org["hr"], iid, "start")
    assert response.status_code == 409
    with session_factory() as fresh:
        row = fresh.get(Intervention, iid)
        assert (row.status, row.priority) == (InterventionStatus.OPEN, InterventionPriority.LOW)


# --- Assignment -----------------------------------------------------------------------------------


def test_assignment_rules(client, db, org, iid, staff, headers_for) -> None:
    hr = org["hr"]
    inactive_hr, _ = staff(HR)
    from app.models import User

    user = db.get(User, inactive_hr.user_id)
    user.is_active = False
    db.commit()
    for user_id in (org["sales_manager"].user_id, org["eng"].user_id, org["sales"].user_id, inactive_hr.user_id, 99999):
        assert act(client, hr, iid, "assign", user_id=user_id).status_code == 422, user_id

    assigned = act(client, hr, iid, "assign", user_id=org["manager"].user_id)
    assert assigned.status_code == 200 and assigned.json()["assigned_to_user_id"] == org["manager"].user_id
    repeat = act(client, hr, iid, "assign", user_id=org["manager"].user_id)
    assert repeat.json()["version"] == assigned.json()["version"]
    assert db.scalar(select(func.count(Notification.id)).where(Notification.user_id == org["manager"].user_id)) == 1
    log = db.scalar(select(AuditLog).where(AuditLog.action == "intervention.assigned"))
    assert log.details == {"from_user_id": None, "to_user_id": org["manager"].user_id}
    # The manager can see and work on it, and hand it to HR.
    hr_user = db.scalar(select(AuditLog.actor_user_id).where(AuditLog.action == "intervention.created"))
    assert act(client, org["manager_h"], iid, "assign", user_id=hr_user).status_code == 200


# --- Notes, permissions, isolation ----------------------------------------------------------------


def test_note_text_stays_out_of_audit_and_notifications(client, db, org, iid) -> None:
    secret = "Discussed a confidential personal matter"
    client.post(f"{BASE}/{iid}/notes", json={"note": secret}, headers=org["hr"])
    log = db.scalar(select(AuditLog).where(AuditLog.action == "intervention.note_added"))
    assert set(log.details) == {"version"}
    for text in [str(a.details) for a in db.scalars(select(AuditLog))] + [n.message for n in db.scalars(select(Notification))]:
        assert secret not in text and "Review sprint workload" not in text


def test_manager_scope_and_no_leaks(client, db, org) -> None:
    eng_id = create(client, org["hr"], org["eng"]).json()["id"]
    sales_id = create(client, org["hr"], org["sales"]).json()["id"]
    headers = org["manager_h"]
    assert [i["id"] for i in client.get(BASE, headers=headers).json()["items"]] == [eng_id]
    assert client.get(BASE, params={"department_id": org["sales"].department_id}, headers=headers).json()["total"] == 0
    assert client.get(BASE, params={"employee_id": org["sales"].id}, headers=headers).json()["total"] == 0
    hidden, missing = client.get(f"{BASE}/{sales_id}", headers=headers), client.get(f"{BASE}/99999", headers=headers)
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
    for action, payload in (("start", {}), ("cancel", {"reason": "Not my department"}), ("notes", {"note": "x"})):
        assert act(client, headers, sales_id, action, **payload).status_code == 404
    assert client.get(f"{BASE}/{sales_id}/history", headers=headers).status_code == 404
    out_of_scope = create(client, headers, org["sales"])
    assert out_of_scope.status_code == 404 and out_of_scope.json() == {"detail": "Employee not found"}
    assert sum(client.get(f"{BASE}/summary", headers=headers).json()["by_status"].values()) == 1


def test_nobody_handles_interventions_about_themselves(client, db, org, staff) -> None:
    hr_employee, hr_self = staff(HR, department="Engineering")
    assert create(client, hr_self, hr_employee).status_code == 404
    iid = create(client, org["hr"], hr_employee).json()["id"]
    assert client.get(f"{BASE}/{iid}", headers=hr_self).status_code == 404
    assert client.get(BASE, headers=hr_self).json()["total"] == 0
    assert act(client, org["hr"], iid, "assign", user_id=hr_employee.user_id).status_code == 422


def test_employees_have_no_access(client, org, iid) -> None:
    headers = org["eng_h"]  # the employee the intervention is about
    for method, path in [("get", BASE), ("post", BASE), ("get", f"{BASE}/{iid}"), ("patch", f"{BASE}/{iid}"),
                         ("post", f"{BASE}/{iid}/start"), ("post", f"{BASE}/{iid}/notes"), ("get", f"{BASE}/{iid}/history"),
                         ("get", f"{BASE}/summary"), ("get", f"{BASE}/categories")]:
        kwargs = {"json": {}} if method in ("post", "patch") else {}
        assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 403, path


def test_list_filters_summary_and_overdue(client, db, org) -> None:
    hr = org["hr"]
    first = create(client, hr, org["eng"], priority="LOW").json()["id"]
    create(client, hr, org["sales"], category="RECOGNITION")
    row = db.get(Intervention, first)
    row.due_date = _today() - timedelta(days=3)  # became overdue
    db.commit()
    assert [i["id"] for i in client.get(BASE, params={"overdue": "true"}, headers=hr).json()["items"]] == [first]
    assert client.get(BASE, params={"category": "RECOGNITION"}, headers=hr).json()["total"] == 1
    assert client.get(BASE, params={"page_size": 1}, headers=hr).json()["pages"] == 2
    assert client.get(BASE, params={"due_from": "2026-12-01", "due_to": "2026-01-01"}, headers=hr).status_code == 422
    assert client.get(BASE, params={"status": "DONE"}, headers=hr).status_code == 422
    assert client.get(f"{BASE}/{first}", headers=hr).json()["is_overdue"] is True
    summary = client.get(f"{BASE}/summary", headers=hr).json()
    assert (summary["overdue"], summary["by_status"]["OPEN"], summary["active_by_priority"]["LOW"]) == (1, 2, 1)
    categories = client.get(f"{BASE}/categories", headers=hr).json()
    assert "not personalised or AI-generated" in categories["note"] and len(categories["categories"]) == 7


def test_audit_failure_rolls_back(client, org, iid, monkeypatch, session_factory) -> None:
    from app.api import interventions as api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(BASE, json=body(org["eng"], assigned_to_user_id=org["manager"].user_id), headers=org["hr"]).status_code == 500
    assert raw.post(f"{BASE}/{iid}/start", json={}, headers=org["hr"]).status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(Intervention.id))) == 1
        assert fresh.get(Intervention, iid).status == InterventionStatus.OPEN
        assert fresh.scalar(select(func.count(InterventionEvent.id))) == 1
        assert fresh.scalar(select(func.count(Notification.id))) == 0


def test_requires_authentication(client) -> None:
    for method, path in [("get", BASE), ("post", BASE), ("get", f"{BASE}/1"), ("get", f"{BASE}/summary")]:
        assert getattr(client, method)(path).status_code == 401, path
