"""Recommendation engine (rules-v1 compatibility and rules-v2) over SYNTHETIC records (in-memory SQLite)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AlertPriority,
    AttritionPrediction,
    AuditLog,
    Goal,
    GoalStatus,
    Intervention,
    InterventionCategory,
    InterventionPriority,
    InterventionStatus,
    Notification,
    PerformanceReview,
    Recommendation,
    RecommendationStatus,
    ReviewCycle,
    ReviewCycleStatus,
    ReviewStatus,
    RiskAlert,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
    User,
)
from app.models.base import utcnow
from tests.test_workforce_activity_api import SENTINEL_SCORE, add, ago, org, today  # noqa: F401  (pytest fixtures)

BASE = "/api/recommendations"
V2 = {"strategy": "rules-v2"}


def evaluate(client, headers, expected=200, **body) -> dict:
    response = client.post(f"{BASE}/evaluate", headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def recs(db, **filters) -> list[Recommendation]:
    db.expire_all()
    stmt = select(Recommendation).order_by(Recommendation.id)
    for key, value in filters.items():
        stmt = stmt.where(getattr(Recommendation, key) == value)
    return list(db.scalars(stmt))


def notes(db, title=None) -> list[Notification]:
    db.expire_all()
    stmt = select(Notification).where(Notification.category == "recommendation")
    if title:
        stmt = stmt.where(Notification.title == title)
    return list(db.scalars(stmt))


@pytest.fixture
def hr_id(db):
    return db.scalar(select(User.id).where(User.email == "hr.activity@example.com"))


def intervention_for(db, employee, alert=None, due_days=None, priority=InterventionPriority.MEDIUM) -> Intervention:
    row = Intervention(employee_id=employee.id, risk_alert_id=alert.id if alert else None,
                       category=InterventionCategory.MANAGER_CHECK_IN, title="Check-in", priority=priority,
                       status=InterventionStatus.OPEN, version=1,
                       due_date=None if due_days is None else today() + timedelta(days=due_days),
                       active_alert_key=f"{alert.id}:MANAGER_CHECK_IN" if alert else None)
    db.add(row)
    db.commit()
    return row


# --- Compatibility ------------------------------------------------------------------------------------


def test_rules_v1_remains_the_default(client, db, org, add) -> None:
    add.alert(org["eng2"], created_at=ago(30))
    body = evaluate(client, org["hr"])
    assert body["rule_version"] == "rules-v1" and body["ranked"] == [] and body["superseded"] == 0
    rows = recs(db)
    assert rows and {r.rule_version for r in rows} == {"rules-v1"}
    assert all(r.rank_score is None and r.evidence_fingerprint is None for r in rows)
    read = client.get(f"{BASE}/{rows[0].id}", headers=org["hr"]).json()
    assert read["rank_score"] is None and "rules-v2" not in read["disclaimer"]
    assert not notes(db)  # rules-v1 evaluation never notifies
    assert client.post(f"{BASE}/evaluate", headers=org["hr"], json={"strategy": "rules-v3"}).status_code == 422


# --- rules-v2 end to end ------------------------------------------------------------------------------


def test_v2_alert_recommendation_ranked_notified_once_and_deduplicated(client, db, org, add) -> None:
    alert = add.alert(org["eng2"], created_at=ago(30))  # HIGH and past its 24h review deadline
    body = evaluate(client, org["hr"], **V2)
    assert body["rule_version"] == "rules-v2"
    top = body["ranked"][0]
    assert (top["rule_id"], top["priority"], top["outcome"]) == ("V2_ALERT_SUPPORT_REVIEW", "CRITICAL", "created")
    assert top["rank_score"] == 88 and [r["rank_score"] for r in body["ranked"]] == sorted(
        (r["rank_score"] for r in body["ranked"]), reverse=True)
    rec = next(r for r in recs(db, rule_version="rules-v2") if r.rule_id == "V2_ALERT_SUPPORT_REVIEW")
    assert rec.risk_alert_id == alert.id and rec.priority_reasons[1].startswith("Raised to CRITICAL")
    critical = notes(db, "Critical recommendation to review")
    assert [n.user_id for n in critical] == [org["manager"].user_id]  # never the employee or the actor
    again = evaluate(client, org["hr"], **V2)
    assert again["created"] == 0 and again["already_recorded"] == len(body["ranked"])
    assert again["ranked"][0]["outcome"] == "already_recorded" and again["ranked"][0]["recommendation_id"] == rec.id
    assert len(notes(db)) == 1 and len(recs(db, rule_version="rules-v2")) == body["created"]


def test_changed_evidence_supersedes_and_gone_evidence_expires(client, db, org, add) -> None:
    alert = add.alert(org["eng2"], created_at=ago(30))
    evaluate(client, org["hr"], **V2)
    [first] = recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")
    alert.priority = AlertPriority.MEDIUM
    db.commit()
    body = evaluate(client, org["hr"], **V2)
    assert body["superseded"] == 1
    old, new = recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")
    assert (old.status, old.superseded_by_id, new.status, new.priority.value) == (
        RecommendationStatus.SUPERSEDED, new.id, RecommendationStatus.AVAILABLE, "MEDIUM")
    intervention_for(db, org["eng2"], alert)  # now addressed: the rule no longer fires
    evaluate(client, org["hr"], **V2)
    assert recs(db, id=new.id)[0].status == RecommendationStatus.SUPERSEDED and recs(db, id=new.id)[0].superseded_by_id is None
    actions = [(a.entity_id, a.action, (a.details or {}).get("reason")) for a in db.scalars(
        select(AuditLog).where(AuditLog.action == "recommendation.superseded").order_by(AuditLog.id))]
    assert actions == [(str(first.id), "recommendation.superseded", "evidence_changed"),
                       (str(new.id), "recommendation.superseded", "evidence_no_longer_current")]
    for action in ("accept", "dismiss"):
        body = {"reason": "Not needed"} if action == "dismiss" else {}
        assert client.post(f"{BASE}/{new.id}/{action}", headers=org["hr"], json=body).status_code == 409


def test_accepted_is_being_addressed_and_dismissed_is_not_resurrected(client, db, org, add) -> None:
    alert = add.alert(org["eng2"], created_at=ago(30))
    evaluate(client, org["hr"], **V2)
    [rec] = recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")
    assert client.post(f"{BASE}/{rec.id}/accept", headers=org["hr"], json={}).status_code == 200
    assert [n.title for n in notes(db)][-1] == "Recommendation accepted for follow-up"
    alert.priority = AlertPriority.MEDIUM
    db.commit()
    body = evaluate(client, org["hr"], **V2)
    assert body["being_addressed"] == 1 and len(recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")) == 1
    alert.priority = AlertPriority.HIGH
    db.commit()
    assert client.post(f"{BASE}/{rec.id}/dismiss", headers=org["hr"], json={"reason": "Handled elsewhere"}).status_code == 200
    assert evaluate(client, org["hr"], **V2)["created"] == 0  # same evidence as the dismissed record


def test_unusable_prediction_yields_refresh_only(client, db, org, add) -> None:
    alert = add.alert(org["eng2"], created_at=ago(2))
    prediction = db.get(AttritionPrediction, alert.latest_prediction_id)
    prediction.predicted_at = utcnow() - timedelta(days=400)
    db.commit()
    body = evaluate(client, org["hr"], **V2)
    rule_ids = {r["rule_id"] for r in body["ranked"]}
    assert "V2_PREDICTION_REFRESH" in rule_ids and "V2_ALERT_SUPPORT_REVIEW" not in rule_ids
    assert any(o["rule_id"] == "V2_ALERT_SUPPORT_REVIEW" and o["status"] == "INSUFFICIENT_DATA" for o in body["not_actionable"])


def test_follow_up_goal_task_and_review_rules(client, db, org, add) -> None:
    intervention_for(db, org["eng"], due_days=-20, priority=InterventionPriority.HIGH)
    db.add(Goal(employee_id=org["eng2"].id, title="Ship guide", category="BUSINESS", measurable_target="Published",
                start_date=today() - timedelta(days=60), target_date=today() + timedelta(days=5), progress_percent=20,
                status=GoalStatus.AT_RISK, version=1))
    db.add(Goal(employee_id=org["manager"].id, title="Own goal", category="BUSINESS", measurable_target="Done",
                start_date=today() - timedelta(days=60), target_date=today() - timedelta(days=5), progress_percent=20,
                status=GoalStatus.IN_PROGRESS, version=1))
    db.add(Task(title="Audit prep", task_type=TaskType.COMPLIANCE, priority=TaskPriority.CRITICAL, status=TaskStatus.OPEN,
                department_id=org["eng"].department_id, due_date=today() - timedelta(days=4), version=1))
    cycle = ReviewCycle(name="H2", period_start=today() - timedelta(days=90), period_end=today(),
                        self_assessment_deadline=today() - timedelta(days=10), manager_review_deadline=today() - timedelta(days=3),
                        status=ReviewCycleStatus.IN_PROGRESS, version=1)
    db.add(cycle)
    db.flush()
    db.add(PerformanceReview(cycle_id=cycle.id, employee_id=org["eng"].id, reviewer_user_id=org["manager"].user_id,
                             status=ReviewStatus.ASSIGNED, version=1))
    db.commit()
    body = evaluate(client, org["manager_h"], **V2)
    found = {(r["rule_id"], r["subject"], r["priority"]) for r in body["ranked"]}
    eng_dept = org["eng"].department_id
    assert ("V2_INTERVENTION_FOLLOW_UP", f"employee:{org['eng'].id}", "CRITICAL") in found
    assert ("V2_GOAL_CHECK_IN", f"employee:{org['eng2'].id}", "MEDIUM") in found
    assert ("V2_TASK_FOLLOW_UP", f"department:{eng_dept}", "HIGH") in found
    assert ("V2_REVIEW_FOLLOW_UP", f"department:{eng_dept}", "MEDIUM") in found
    assert not any(subject == f"employee:{org['manager'].id}" for _, subject, _ in found)  # never about the caller
    hr = evaluate(client, org["hr"], **V2)
    assert any(r["subject"] == f"employee:{org['manager'].id}" and r["rule_id"] == "V2_GOAL_CHECK_IN" for r in hr["ranked"])
    task_rec = recs(db, rule_id="V2_TASK_FOLLOW_UP")[0]
    assert task_rec.employee_id is None and set(task_rec.evidence) >= {"overdue_high_or_critical_tasks"}


# --- Access and privacy -------------------------------------------------------------------------------------


def test_access_boundaries(client, db, org, add) -> None:
    add.alert(org["sales"], created_at=ago(30))
    add.alert(org["eng2"], created_at=ago(30))
    add.alert(org["manager"], created_at=ago(30))
    assert client.post(f"{BASE}/evaluate", headers=org["eng_h"], json=V2).status_code == 403
    assert client.post(f"{BASE}/evaluate", headers=org["manager_h"],
                       json={**V2, "department_id": org["sales"].department_id}).status_code == 404
    manager = evaluate(client, org["manager_h"], **V2)
    assert {r["subject"] for r in manager["ranked"] if r["subject"].startswith("employee")} == {f"employee:{org['eng2'].id}"}
    evaluate(client, org["hr"], **V2)
    manager_view = client.get(BASE, headers=org["manager_h"], params={"rule_version": "rules-v2", "page_size": 100}).json()
    assert manager_view["total"] >= 1
    assert all(r["employee_id"] in (None, org["eng2"].id) and r["scope"] != "ORGANIZATION" for r in manager_view["items"])
    sales_rec = recs(db, employee_id=org["sales"].id)[0]
    assert client.get(f"{BASE}/{sales_rec.id}", headers=org["manager_h"]).status_code == 404
    assert client.get(f"{BASE}/{sales_rec.id}/history", headers=org["manager_h"]).status_code == 404
    for path in ("", f"/{sales_rec.id}", "/summary"):
        assert client.get(f"{BASE}{path}", headers=org["eng_h"]).status_code == 403
    responses = [client.get(BASE, headers=h, params={"page_size": 100}) for h in (org["hr"], org["manager_h"])]
    for response in responses:
        for secret in (SENTINEL_SCORE, "Edith", "Ezra", "Mona", "Sam Seller", "tenure", "features"):
            assert secret not in response.text, secret
        # The sentinel feature values (987, 654.5) are checked as values, not as digit substrings: timestamps carry
        # microseconds, and "987" occurs in them by chance (a pre-existing flaky assertion, fixed in Sub-prompt 31).
        assert not _values(response.json()) & {987, 654.5, "987", "654.5"}


def _values(node) -> set:
    if isinstance(node, dict):
        return set().union(*(_values(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_values(v) for v in node)) if node else set()
    return {node} if isinstance(node, (int, float, str)) and not isinstance(node, bool) else set()


def test_truncated_sources_do_not_expire_records(client, db, org, add) -> None:
    add.alert(org["eng"], created_at=ago(30))
    add.alert(org["eng2"], created_at=ago(30))
    evaluate(client, org["hr"], **V2)
    assert len(recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW", status=RecommendationStatus.AVAILABLE)) == 2
    body = evaluate(client, org["hr"], max_alerts=1, **V2)
    assert body["truncated_sources"] == ["alerts"] and body["superseded"] == 0
    assert len(recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW", status=RecommendationStatus.AVAILABLE)) == 2


# --- Lifecycle, linking, history ---------------------------------------------------------------------------


def test_link_task_and_history(client, db, org, add, hr_id) -> None:
    add.alert(org["eng2"], created_at=ago(30))
    evaluate(client, org["hr"], **V2)
    [rec] = recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")
    task = client.post("/api/tasks", headers=org["manager_h"], json={"title": "Follow-up conversation"}).json()
    sales_task = client.post("/api/tasks", headers=org["hr"], json={"title": "Sales follow-up",
                                                                     "department_id": org["sales"].department_id}).json()
    link = lambda h, tid, code: client.post(f"{BASE}/{rec.id}/link-task", headers=h, json={"task_id": tid}).status_code == code  # noqa: E731
    assert link(org["manager_h"], task["id"], 409)  # not accepted yet
    client.post(f"{BASE}/{rec.id}/accept", headers=org["manager_h"], json={})
    assert link(org["manager_h"], sales_task["id"], 404)  # task outside the manager's scope
    assert link(org["hr"], sales_task["id"], 422)  # different department
    assert link(org["manager_h"], task["id"], 200)
    assert link(org["manager_h"], task["id"], 200)  # repeat: no-op
    assert link(org["hr"], sales_task["id"], 409)  # already linked elsewhere
    assert recs(db, id=rec.id)[0].task_id == task["id"]
    assert db.get(Task, task["id"]).status == TaskStatus.OPEN  # linking never changes the task
    history = client.get(f"{BASE}/{rec.id}/history", headers=org["hr"]).json()
    assert [e["action"] for e in history["events"]] == ["recommendation.generated", "recommendation.accepted",
                                                        "recommendation.task_linked"]
    assert history["rule_version"] == "rules-v2"
    manager_history = client.get(f"{BASE}/{rec.id}/history", headers=org["manager_h"]).json()
    generated = manager_history["events"][0]
    assert generated["actor_user_id"] is None and generated["actor_hidden"] is True  # HR actor outside the department
    assert sum(1 for a in db.scalars(select(AuditLog.action)) if a == "recommendation.task_linked") == 1


def test_critical_recommendation_creates_high_intervention_only_on_request(client, db, org, add) -> None:
    add.alert(org["eng2"], created_at=ago(30))
    evaluate(client, org["hr"], **V2)
    [rec] = recs(db, rule_id="V2_ALERT_SUPPORT_REVIEW")
    assert db.scalar(select(func.count(Intervention.id))) == 0  # never created automatically
    client.post(f"{BASE}/{rec.id}/accept", headers=org["hr"], json={})
    response = client.post(f"{BASE}/{rec.id}/create-intervention", headers=org["hr"], json={})
    assert response.status_code == 200
    intervention = db.scalar(select(Intervention))
    assert intervention.priority == InterventionPriority.HIGH and response.json()["intervention_id"] == intervention.id


def test_list_sorting_filters_and_summary(client, db, org, add) -> None:
    add.alert(org["eng2"], created_at=ago(30))
    add.alert(org["eng"], created_at=ago(2), priority=AlertPriority.LOW)
    evaluate(client, org["hr"])
    evaluate(client, org["hr"], **V2)
    assert len(notes(db)) == 1  # only the CRITICAL recommendation notifies; the LOW one does not
    ranked = client.get(BASE, headers=org["hr"], params={"sort": "rank", "page_size": 100}).json()["items"]
    scores = [r["rank_score"] for r in ranked if r["rank_score"] is not None]
    assert scores == sorted(scores, reverse=True) and ranked[-1]["rank_score"] is None  # rules-v1 records last
    only_v2 = client.get(BASE, headers=org["hr"], params={"rule_version": "rules-v2", "page_size": 100}).json()["items"]
    assert only_v2 and {r["rule_version"] for r in only_v2} == {"rules-v2"}
    assert all("not a probability" in r["disclaimer"] for r in only_v2)
    summary = client.get(f"{BASE}/summary", headers=org["hr"]).json()
    assert set(summary["by_status"]) >= {"SUPERSEDED"} and summary["active_by_priority"]["CRITICAL"] == 1
    for params in ({"sort": "random"}, {"rule_version": "rules-v9"}, {"priority": "URGENT"}):
        assert client.get(BASE, headers=org["hr"], params=params).status_code == 422


# --- Bounds, failures, models -----------------------------------------------------------------------------


def test_query_count_does_not_grow_with_alerts(client, org, add, make_employee, count_queries) -> None:
    def measure() -> int:
        start = len(count_queries())
        evaluate(client, org["hr"], **V2)
        return len(count_queries()) - start

    add.alert(org["eng2"], created_at=ago(30))
    measure()  # first run creates records
    baseline = measure()
    for _ in range(6):
        add.alert(make_employee(org["eng"].department), created_at=ago(2), priority=AlertPriority.LOW)
    measure()
    assert measure() == baseline  # steady state: same number of queries with 7x the alerts


def test_audit_failure_rolls_back_evaluation(client, db, org, add, monkeypatch, session_factory) -> None:
    from app.api import recommendations as api

    add.alert(org["eng2"], created_at=ago(30))
    monkeypatch.setattr(api, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(f"{BASE}/evaluate", headers=org["hr"], json=V2).status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(Recommendation.id))) == 0
        assert fresh.scalar(select(func.count(Notification.id))) == 0


def test_no_model_code_and_no_changes_to_sources(client, db, org, add, monkeypatch) -> None:
    from app.services import attrition

    def forbidden(*args, **kwargs):
        raise AssertionError("no model code in recommendation evaluation")

    for name in ("predict", "train_model", "load_active", "extract_features", "activate_model"):
        monkeypatch.setattr(attrition, name, forbidden)
    alert = add.alert(org["eng2"], created_at=ago(30))
    before = (db.get(RiskAlert, alert.id).status, db.scalar(select(func.count(AttritionPrediction.id))))
    evaluate(client, org["hr"], **V2)
    db.expire_all()
    assert (db.get(RiskAlert, alert.id).status, db.scalar(select(func.count(AttritionPrediction.id)))) == before
    assert db.scalar(select(func.count(Task.id))) == 0 and db.scalar(select(func.count(Intervention.id))) == 0
