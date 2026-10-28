"""Recommendation API over SYNTHETIC alerts, predictions and snapshots. No model artifacts are involved."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AlertPriority,
    AlertStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Intervention,
    InterventionStatus,
    ModelVersionStatus,
    Notification,
    Recommendation,
    RecommendationStatus,
    RiskAlert,
    RiskCategory,
    RiskEventType,
    UserRole,
)
from app.models.base import utcnow
from ml.attrition.features import FEATURE_SCHEMA_VERSION

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/recommendations"
SENTINEL_FEATURES = {"tenure_months": 987, "avg_overtime_minutes_90d": 654.5, "late_rate_90d": 0.321,
                     "recorded_absence_days_90d": 7, "attendance_days_recorded_90d": 60, "department": "1"}


@pytest.fixture
def org(staff, headers_for):
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng, _ = staff(department="Engineering")
    eng2, _ = staff(department="Engineering")
    sales, _ = staff(department="Sales")
    return {"manager": manager, "manager_h": manager_h, "eng": eng, "eng2": eng2, "sales": sales,
            "hr": headers_for(HR, "hr.recs@example.com"), "admin": headers_for(ADMIN, "admin.recs@example.com")}


@pytest.fixture
def make_alert(db):
    models: dict[str, AttritionModelVersion] = {}

    def _make(employee, top="avg_overtime_minutes_90d", schema=FEATURE_SCHEMA_VERSION, days_ago=1, status=AlertStatus.OPEN):
        if schema not in models:
            models[schema] = AttritionModelVersion(
                model_version=f"lr-20260101000000-{len(models):08x}", feature_schema_version=schema,
                status=ModelVersionStatus.ACTIVE, trained_at=utcnow(), dataset_fingerprint="0" * 64,
                artifact_sha256="0" * 64, training_rows=100, evaluation={})
            db.add(models[schema])
            db.flush()
        prediction = AttritionPrediction(
            employee_id=employee.id, model_version_id=models[schema].id, feature_schema_version=schema,
            risk_score=Decimal("81.25"), risk_category=RiskCategory.HIGH, features=SENTINEL_FEATURES,
            explanation={"factors": [{"feature": top, "direction": "raises", "weight": 4.5678}], "missing_inputs": []},
            predicted_at=utcnow() - timedelta(days=days_ago))
        db.add(prediction)
        db.flush()
        alert = RiskAlert(employee_id=employee.id, trigger_type=RiskEventType.THRESHOLD_CROSSED, priority=AlertPriority.HIGH,
                          status=status, latest_prediction_id=prediction.id, latest_score=Decimal("81.25"),
                          latest_category=RiskCategory.HIGH, model_version=models[schema].model_version, event_count=1,
                          first_detected_at=utcnow(), last_observed_at=utcnow())
        db.add(alert)
        db.commit()
        return alert

    return _make


def evaluate(client, headers, **body):
    response = client.post(f"{BASE}/evaluate", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def recs(db, **filters) -> list[Recommendation]:
    db.expire_all()
    stmt = select(Recommendation).order_by(Recommendation.id)
    for key, value in filters.items():
        stmt = stmt.where(getattr(Recommendation, key) == value)
    return list(db.scalars(stmt))


def act(client, headers, rid, action, **payload):
    return client.post(f"{BASE}/{rid}/{action}", json=payload, headers=headers)


# --- Evaluation ---------------------------------------------------------------------------------


def test_evaluation_uses_existing_records_only(client, db, org, make_alert, monkeypatch) -> None:
    from app.services import attrition as attrition_service
    from ml.attrition import predictor, training

    def forbidden(*args, **kwargs):
        raise AssertionError("evaluation must not train or call a model")

    monkeypatch.setattr(attrition_service, "load_active", forbidden)
    monkeypatch.setattr(predictor, "predict", forbidden)
    monkeypatch.setattr(training, "train", forbidden)
    alert = make_alert(org["eng"])
    before = (db.scalar(select(func.count(AttritionPrediction.id))), db.scalar(select(func.count(Intervention.id))))

    result = evaluate(client, org["hr"])
    assert result["rule_version"] == "rules-v1" and "Not a trained model" in result["note"]
    by_rule = {r.rule_id: r for r in recs(db)}
    assert set(by_rule) == {"AGG_SNAPSHOT_FRESHNESS", "IND_ALERT_CHECKIN", "IND_WORKLOAD_REVIEW"}
    assert by_rule["IND_ALERT_CHECKIN"].risk_alert_id == alert.id and by_rule["IND_ALERT_CHECKIN"].priority.value == "HIGH"
    assert {"AGG_TURNOVER_REVIEW", "AGG_LOW_STABILITY"} <= {s["rule_id"] for s in result["not_actionable"]}

    db.expire_all()
    assert (db.scalar(select(func.count(AttritionPrediction.id))), db.scalar(select(func.count(Intervention.id)))) == before
    assert db.get(RiskAlert, alert.id).status == AlertStatus.OPEN
    assert db.scalar(select(func.count(Notification.id))) == 0
    log = db.scalar(select(AuditLog).where(AuditLog.action == "recommendation.evaluated"))
    assert log.details["created"] == 3 and log.details["rule_version"] == "rules-v1"


def test_reevaluation_is_idempotent_and_dismissed_stays_dismissed(client, db, org, make_alert) -> None:
    make_alert(org["eng"])
    evaluate(client, org["hr"])
    checkin = recs(db, rule_id="IND_ALERT_CHECKIN")[0]
    act(client, org["hr"], checkin.id, "dismiss", reason="Already discussed in the last one-to-one")
    again = evaluate(client, org["hr"])
    assert (again["created"], again["already_recorded"]) == (0, 3)
    assert len(recs(db)) == 3
    assert recs(db, rule_id="IND_ALERT_CHECKIN")[0].status == RecommendationStatus.DISMISSED


@pytest.mark.parametrize("kwargs, expected_rules", [
    ({"schema": "attrition-features-v0"}, {"IND_ALERT_CHECKIN"}),
    ({"days_ago": 200}, {"IND_ALERT_CHECKIN"}),
    ({"top": "late_rate_90d"}, {"IND_ALERT_CHECKIN"}),
    ({"top": "tenure_months"}, {"IND_ALERT_CHECKIN", "IND_CAREER_CONVERSATION"}),
    ({"status": AlertStatus.RESOLVED}, set()),
])
def test_individual_rule_inputs(client, db, org, make_alert, kwargs, expected_rules) -> None:
    make_alert(org["eng"], **kwargs)
    evaluate(client, org["hr"], include_individual=True)
    assert {r.rule_id for r in recs(db) if r.rule_id.startswith("IND_")} == expected_rules


def test_existing_intervention_suppresses_checkin(client, db, org, make_alert) -> None:
    alert = make_alert(org["eng"])
    client.post("/api/interventions", headers=org["hr"], json={
        "employee_id": org["eng"].id, "risk_alert_id": alert.id, "category": "MANAGER_CHECK_IN", "title": "Check-in"})
    result = evaluate(client, org["hr"])
    assert "IND_ALERT_CHECKIN" not in {r.rule_id for r in recs(db)}
    assert any(s["rule_id"] == "IND_ALERT_CHECKIN" and s["status"] == "SUPPRESSED" for s in result["not_actionable"])


def test_aggregate_turnover_rule_needs_denominator(client, db, org) -> None:
    from datetime import date

    from app.models import AuditLog as Log
    from app.models import Department, WorkforceSnapshot, WorkforceSnapshotDepartment

    engineering = db.scalar(select(Department).where(Department.name == "Engineering"))
    today = utcnow().date()
    for days in (40, 10):
        snap = WorkforceSnapshot(snapshot_date=today - timedelta(days=days), captured_at=utcnow(), period_start=date(2000, 1, 1),
                                 total_employees=4, active_employees=4, on_leave_employees=0, inactive_employees=0,
                                 joiners_in_period=0, recorded_departures_in_period=0, source="synthetic", data_quality={})
        db.add(snap)
        db.flush()
        db.add(WorkforceSnapshotDepartment(snapshot_id=snap.id, department_id=engineering.id, department_name="Engineering",
                                           total_employees=4, active_employees=4, on_leave_employees=0, inactive_employees=0,
                                           joiners_in_period=0, recorded_departures_in_period=0))
    db.add(Log(action="employee.status_changed", entity_type="employee", entity_id=str(org["eng2"].id),
               details={"changes": {"status": {"from": "ACTIVE", "to": "INACTIVE"}}}, created_at=utcnow() - timedelta(days=20)))
    db.commit()

    evaluate(client, org["hr"], department_id=engineering.id, include_individual=False)
    turnover = recs(db, rule_id="AGG_TURNOVER_REVIEW")
    assert len(turnover) == 1 and turnover[0].department_id == engineering.id
    assert turnover[0].evidence["annualized_turnover"] > 0.2
    assert not recs(db, rule_id="AGG_SNAPSHOT_FRESHNESS")  # organization rule not evaluated for a department


# --- Lifecycle ------------------------------------------------------------------------------------


@pytest.fixture
def checkin(client, db, org, make_alert):
    alert = make_alert(org["eng"])
    evaluate(client, org["hr"])
    rec = recs(db, rule_id="IND_ALERT_CHECKIN")[0]
    return {"id": rec.id, "alert": alert}


def test_lifecycle(client, db, org, checkin) -> None:
    hr, rid = org["hr"], checkin["id"]
    assert act(client, hr, rid, "complete", outcome="Too early to complete").status_code == 409
    accepted = act(client, hr, rid, "accept")
    assert (accepted.json()["status"], accepted.json()["version"]) == ("ACCEPTED", 2)
    assert act(client, hr, rid, "accept").json()["version"] == 2  # idempotent
    assert act(client, hr, rid, "complete").status_code == 422
    done = act(client, hr, rid, "complete", outcome="Check-in held; follow-up agreed")
    assert done.json()["status"] == "COMPLETED" and done.json()["reviewed_by_user_id"] is not None
    assert act(client, hr, rid, "complete", outcome="Repeat request again").json()["version"] == done.json()["version"]
    assert act(client, hr, rid, "dismiss", reason="Too late to dismiss").status_code == 409
    assert act(client, hr, rid, "accept").status_code == 409
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.entity_type == "recommendation",
                                                                     AuditLog.entity_id == str(rid)))]
    assert actions == ["recommendation.accepted", "recommendation.completed"]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "recommendation.accepted"))
    assert log.details == {"rule_id": "IND_ALERT_CHECKIN", "rule_version": "rules-v1", "from_status": "AVAILABLE",
                           "to_status": "ACCEPTED", "scope": "INDIVIDUAL"}
    db.expire_all()
    assert db.get(RiskAlert, checkin["alert"].id).status == AlertStatus.OPEN


def test_dismiss_rules(client, org, checkin) -> None:
    hr, rid = org["hr"], checkin["id"]
    assert act(client, hr, rid, "dismiss").status_code == 422
    assert act(client, hr, rid, "dismiss", reason="no").status_code == 422
    assert act(client, hr, rid, "dismiss", reason="Handled through the team retro").json()["status"] == "DISMISSED"
    assert act(client, hr, rid, "dismiss", reason="Second request again").status_code == 200


def test_version_conflicts(client, org, checkin, monkeypatch, session_factory) -> None:
    from app.services import recommendations as service

    assert act(client, org["hr"], checkin["id"], "accept", expected_version=9).status_code == 409
    original = service.change

    def other_reviewer_first(db, rec, allowed_from, values):
        with session_factory() as other:
            row = other.get(Recommendation, rec.id)
            row.version += 1
            row.status, row.dismissal_reason = RecommendationStatus.DISMISSED, "Dismissed by another reviewer"
            other.commit()
        return original(db, rec, allowed_from, values)

    monkeypatch.setattr(service, "change", other_reviewer_first)
    assert act(client, org["hr"], checkin["id"], "accept").status_code == 409
    with session_factory() as fresh:
        assert fresh.get(Recommendation, checkin["id"]).status == RecommendationStatus.DISMISSED


# --- Intervention linking --------------------------------------------------------------------------


def test_create_intervention_explicitly(client, db, org, checkin) -> None:
    hr, rid = org["hr"], checkin["id"]
    assert act(client, hr, rid, "create-intervention").status_code == 409  # must be accepted first
    act(client, hr, rid, "accept")
    bad_assignee = act(client, hr, rid, "create-intervention", assigned_to_user_id=org["eng"].user_id)
    assert bad_assignee.status_code == 422

    response = act(client, hr, rid, "create-intervention", assigned_to_user_id=org["manager"].user_id)
    assert response.status_code == 200
    linked = response.json()["intervention_id"]
    intervention = db.get(Intervention, linked)
    assert (intervention.category.value, intervention.risk_alert_id, intervention.status) == (
        "MANAGER_CHECK_IN", checkin["alert"].id, InterventionStatus.OPEN)
    assert act(client, hr, rid, "create-intervention").json()["intervention_id"] == linked  # repeat
    assert act(client, hr, rid, "create-intervention", intervention_id=linked + 100).status_code == 409
    assert db.scalar(select(func.count(Intervention.id))) == 1
    db.expire_all()
    assert db.get(RiskAlert, checkin["alert"].id).status == AlertStatus.OPEN
    assert {n.user_id for n in db.scalars(select(Notification))} == {org["manager"].user_id}


def test_link_existing_intervention_rules(client, db, org, checkin) -> None:
    hr = org["hr"]
    act(client, hr, checkin["id"], "accept")
    other = client.post("/api/interventions", headers=hr, json={"employee_id": org["eng2"].id, "category": "RECOGNITION",
                                                                "title": "Team recognition"}).json()["id"]
    assert act(client, hr, checkin["id"], "create-intervention", intervention_id=other).status_code == 422
    assert act(client, hr, checkin["id"], "create-intervention", intervention_id=99999).status_code == 404
    same = client.post("/api/interventions", headers=hr, json={"employee_id": org["eng"].id, "category": "RECOGNITION",
                                                               "title": "Recognise release work"}).json()["id"]
    assert act(client, hr, checkin["id"], "create-intervention", intervention_id=same).json()["intervention_id"] == same


def test_duplicate_active_intervention_for_alert_and_category(client, db, org, checkin) -> None:
    hr = org["hr"]
    client.post("/api/interventions", headers=hr, json={"employee_id": org["eng"].id, "risk_alert_id": checkin["alert"].id,
                                                        "category": "MANAGER_CHECK_IN", "title": "Manual check-in"})
    act(client, hr, checkin["id"], "accept")
    response = act(client, hr, checkin["id"], "create-intervention")
    assert response.status_code == 409
    assert recs(db, id=checkin["id"])[0].intervention_id is None


def test_aggregate_recommendations_cannot_create_interventions(client, db, org) -> None:
    evaluate(client, org["hr"], include_individual=False)
    freshness = recs(db, rule_id="AGG_SNAPSHOT_FRESHNESS")[0]
    act(client, org["hr"], freshness.id, "accept")
    assert act(client, org["hr"], freshness.id, "create-intervention").status_code == 422


# --- Permissions and privacy -------------------------------------------------------------------------


def test_manager_scope(client, db, org, make_alert) -> None:
    make_alert(org["eng"])
    make_alert(org["sales"])
    make_alert(org["manager"])  # the manager's own alert
    evaluate(client, org["hr"])
    headers = org["manager_h"]
    visible = client.get(BASE, headers=headers).json()["items"]
    assert {r["employee_id"] for r in visible} == {org["eng"].id}  # not Sales, not self, no organization-level
    sales_rec = recs(db, employee_id=org["sales"].id)[0]
    org_rec = recs(db, rule_id="AGG_SNAPSHOT_FRESHNESS")[0]
    for rid in (sales_rec.id, org_rec.id):
        assert client.get(f"{BASE}/{rid}", headers=headers).status_code == 404
        assert act(client, headers, rid, "accept").status_code == 404
    assert client.post(f"{BASE}/evaluate", json={"department_id": sales_rec.employee.department_id}, headers=headers).status_code == 404
    own = evaluate(client, headers)
    assert own["aggregate_subjects_examined"] == 1  # pinned to their department
    assert sum(client.get(f"{BASE}/summary", headers=headers).json()["by_status"].values()) == len(visible)


def test_nobody_sees_recommendations_about_themselves(client, db, staff, make_alert, headers_for) -> None:
    hr_employee, hr_self = staff(HR)
    make_alert(hr_employee)
    evaluate(client, headers_for(ADMIN))
    rec = recs(db, employee_id=hr_employee.id)[0]
    assert client.get(f"{BASE}/{rec.id}", headers=hr_self).status_code == 404
    assert all(r["employee_id"] != hr_employee.id for r in client.get(BASE, headers=hr_self).json()["items"])
    assert db.scalar(select(Notification).where(Notification.user_id == hr_employee.user_id)) is None


def test_employees_have_no_access(client, staff, org) -> None:
    _, employee_h = staff(department="Engineering")
    for method, path in [("get", BASE), ("get", f"{BASE}/summary"), ("post", f"{BASE}/evaluate"), ("get", f"{BASE}/1"),
                         ("post", f"{BASE}/1/accept"), ("post", f"{BASE}/1/create-intervention")]:
        kwargs = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(path, headers=employee_h, **kwargs).status_code == 403, path


def test_no_feature_values_or_weights_leak(client, db, org, make_alert) -> None:
    make_alert(org["eng"])
    evaluate(client, org["hr"])
    texts = [client.get(BASE, headers=org["hr"]).text, client.get(f"{BASE}/summary", headers=org["hr"]).text]
    texts += [str(a.details) for a in db.scalars(select(AuditLog).where(AuditLog.entity_type == "recommendation"))]
    for text in texts:
        for leak in ("987", "654.5", "0.321", "4.5678", "81.25", "late_rate", org["eng"].full_name):
            assert leak not in text, leak


def test_validation_and_filters(client, db, org, make_alert) -> None:
    make_alert(org["eng"])
    evaluate(client, org["hr"])
    hr = org["hr"]
    assert client.get(BASE, params={"scope": "INDIVIDUAL"}, headers=hr).json()["total"] == 2
    assert client.get(BASE, params={"rule_id": "IND_ALERT_CHECKIN"}, headers=hr).json()["total"] == 1
    for params in ({"status": "DONE"}, {"rule_id": "bad id"}, {"page_size": 101}, {"model": "x"}):
        assert client.get(BASE, params=params, headers=hr).status_code == 422
    for payload in ({"max_alerts": 0}, {"department_id": 0}, {"train": True}):
        assert client.post(f"{BASE}/evaluate", json=payload, headers=hr).status_code == 422
    assert client.post(f"{BASE}/evaluate", json={"department_id": 9999}, headers=hr).status_code == 404


def test_audit_failure_rolls_back(client, db, org, checkin, make_alert, monkeypatch, session_factory) -> None:
    from app.api import recommendations as api

    make_alert(org["sales"])  # without the rollback, evaluating Sales would add 2 recommendations

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(api, "record_audit", failing_audit)
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(f"{BASE}/{checkin['id']}/accept", json={}, headers=org["hr"]).status_code == 500
    assert raw.post(f"{BASE}/evaluate", json={"department_id": org["sales"].department_id}, headers=org["hr"]).status_code == 500
    with session_factory() as fresh:
        assert fresh.get(Recommendation, checkin["id"]).status == RecommendationStatus.AVAILABLE
        assert fresh.scalar(select(func.count(Recommendation.id))) == 3


def test_requires_authentication(client) -> None:
    for method, path in [("get", BASE), ("get", f"{BASE}/summary"), ("post", f"{BASE}/evaluate"), ("get", f"{BASE}/1")]:
        assert getattr(client, method)(path).status_code == 401, path
