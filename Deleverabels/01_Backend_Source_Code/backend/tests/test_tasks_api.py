"""Workforce task management over SYNTHETIC records (in-memory SQLite)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AuditLog,
    EmploymentStatus,
    Intervention,
    InterventionStatus,
    Notification,
    Task,
    TaskEvent,
    TaskStatus,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.services import tasks as service
from tests.test_workforce_activity_api import add, org, today  # noqa: F401  (pytest fixtures)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/tasks"
SECRET_TITLE = "ZZ confidential title ZZ"
SECRET_TEXT = "ZZ-private-task-text-ZZ"


def create(client, headers, expected=201, **body) -> dict:
    response = client.post(BASE, headers=headers, json={"title": SECRET_TITLE, **body})
    assert response.status_code == expected, response.text
    return response.json()


def act(client, headers, task_id, action, expected=200, **body) -> dict:
    response = client.post(f"{BASE}/{task_id}/transition", headers=headers, json={"action": action, **body})
    assert response.status_code == expected, response.text
    return response.json()


def notes(db, user_id=None) -> list[Notification]:
    db.expire_all()
    stmt = select(Notification).where(Notification.category == "task")
    if user_id is not None:
        stmt = stmt.where(Notification.user_id == user_id)
    return list(db.scalars(stmt.order_by(Notification.id)))


def events(db, task_id) -> list[TaskEvent]:
    db.expire_all()
    return list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.version)))


def audits(db, task_id) -> list[AuditLog]:
    db.expire_all()
    return list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "task", AuditLog.entity_id == str(task_id))
                           .order_by(AuditLog.id)))


def force(db, task_id, task_status: TaskStatus, **fields) -> None:
    """Put a task into a status directly (test setup only), with the fields its constraints require."""
    task = db.get(Task, task_id)
    defaults = {TaskStatus.COMPLETED: {"completed_at": utcnow()},
                TaskStatus.CANCELLED: {"cancelled_at": utcnow(), "cancellation_reason": "Not needed"},
                TaskStatus.BLOCKED: {"block_reason": "OTHER"}}.get(task_status, {})
    for key, value in {**defaults, **fields, "status": task_status}.items():
        setattr(task, key, value)
    db.commit()


# --- Creation and validation --------------------------------------------------------------------------------


def test_create_assign_notify_and_audit(client, db, org) -> None:
    task = create(client, org["hr"], assignee_employee_id=org["eng"].id, priority="HIGH", task_type="TRAINING",
                  description=SECRET_TEXT, due_date=(today() + timedelta(days=5)).isoformat())
    assert (task["status"], task["version"], task["department_id"], task["assignee"]["id"]) == (
        "OPEN", 1, org["eng"].department_id, org["eng"].id)
    assert task["can_manage"] is True and task["is_overdue"] is False
    [event] = events(db, task["id"])
    assert (event.event_type.value, event.to_status.value, event.version) == ("CREATED", "OPEN", 1)
    [audit] = audits(db, task["id"])
    assert audit.action == "task.created" and audit.details["assignee_employee_id"] == org["eng"].id
    [note] = notes(db)
    assert note.user_id == org["eng"].user_id and note.message == f"Task #{task['id']} (HIGH priority) has been assigned to you."
    text = str([audit.details, event.details, note.title, note.message])
    assert SECRET_TITLE not in text and SECRET_TEXT not in text


@pytest.mark.parametrize("body", [
    {"title": "ab"}, {"title": "x" * 151}, {"description": "x" * 2001}, {"priority": "URGENT"}, {"task_type": "PARTY"},
    {"status": "COMPLETED"}, {"version": 3}, {"assignee_employee_id": 0}, {"assignee_employee_id": "abc"},
    {"due_date": "2020-01-01"}, {"due_date": "2099-01-01"}, {"intervention_id": 1, "recommendation_id": 1},
    {"idempotency_key": "short"}, {"completion_summary": "done"},
])
def test_create_validation(client, org, body) -> None:
    response = client.post(BASE, headers=org["hr"], json={"title": "Valid title", **body})
    assert response.status_code == 422, response.text


def test_who_may_create(client, db, org, staff) -> None:
    assert client.post(BASE, json={"title": "Valid title"}).status_code == 401
    assert client.post(BASE, headers=org["eng_h"], json={"title": "Valid title"}).status_code == 403
    manager_task = create(client, org["manager_h"])
    assert manager_task["department_id"] == org["eng"].department_id  # pinned to own department
    create(client, org["manager_h"], 404, department_id=org["sales"].department_id)
    create(client, org["manager_h"], 404, assignee_employee_id=org["sales"].id)
    create(client, org["hr"], 404, department_id=999)
    create(client, org["hr"], 404, assignee_employee_id=999)
    _, lone_manager = staff(MANAGER, department="Ops")
    lone = db.scalar(select(User).where(User.role == MANAGER).order_by(User.id.desc()))
    from app.models import Employee

    db.execute(Employee.__table__.delete().where(Employee.user_id == lone.id))
    db.commit()
    assert client.post(BASE, headers=lone_manager, json={"title": "Valid title"}).status_code == 403


def test_cross_department_assignment_needs_hr(client, org) -> None:
    hr_task = create(client, org["hr"], department_id=org["eng"].department_id, assignee_employee_id=org["sales"].id)
    assert (hr_task["department_id"], hr_task["assignee"]["id"]) == (org["eng"].department_id, org["sales"].id)
    response = client.post(f"{BASE}/{hr_task['id']}/assign", headers=org["manager_h"],
                           json={"assignee_employee_id": org["eng2"].id})
    assert response.status_code == 200  # manager of the task's department may reassign within it


def test_ineligible_assignees(client, db, org, make_employee, make_user) -> None:
    org["eng2"].status = EmploymentStatus.INACTIVE
    db.commit()
    assert "Inactive" in create(client, org["hr"], 422, assignee_employee_id=org["eng2"].id)["detail"]
    no_login = make_employee(org["eng"].department)
    assert "no active account" in create(client, org["hr"], 422, assignee_employee_id=no_login.id)["detail"]
    disabled = make_user(email="disabled.user@example.com", is_active=False)
    no_login.user_id = disabled.id
    db.commit()
    create(client, org["hr"], 422, assignee_employee_id=no_login.id)


def test_idempotent_create(client, db, org) -> None:
    first = create(client, org["hr"], assignee_employee_id=org["eng"].id, idempotency_key="task-key-0001")
    response = client.post(BASE, headers=org["hr"], json={"title": "Different", "idempotency_key": "task-key-0001"})
    assert response.status_code == 200 and response.json()["id"] == first["id"]
    assert len(events(db, first["id"])) == 1 and len(notes(db)) == 1 and len(audits(db, first["id"])) == 1
    other = client.post(BASE, headers=org["admin"], json={"title": "Different", "idempotency_key": "task-key-0001"})
    assert other.status_code == 409


# --- Visibility -----------------------------------------------------------------------------------------------


def test_visibility_by_role(client, org, staff) -> None:
    eng_task = create(client, org["hr"], assignee_employee_id=org["eng"].id)
    eng_unassigned = create(client, org["hr"], department_id=org["eng"].department_id)
    sales_task = create(client, org["hr"], assignee_employee_id=org["sales"].id)
    org_task = create(client, org["hr"])
    manager_elsewhere = create(client, org["hr"], department_id=org["sales"].department_id,
                               assignee_employee_id=org["manager"].id)

    def ids(headers):
        return {t["id"] for t in client.get(BASE, headers=headers, params={"page_size": 100}).json()["items"]}

    assert ids(org["hr"]) == ids(org["admin"]) == {eng_task["id"], eng_unassigned["id"], sales_task["id"], org_task["id"],
                                                   manager_elsewhere["id"]}
    assert ids(org["manager_h"]) == {eng_task["id"], eng_unassigned["id"], manager_elsewhere["id"]}
    assert ids(org["eng_h"]) == {eng_task["id"]}
    for hidden in (sales_task, org_task, eng_unassigned):
        assert client.get(f"{BASE}/{hidden['id']}", headers=org["eng_h"]).status_code == 404
    assert client.get(f"{BASE}/{sales_task['id']}", headers=org["manager_h"]).status_code == 404
    detail = client.get(f"{BASE}/{manager_elsewhere['id']}", headers=org["manager_h"]).json()
    assert detail["can_manage"] is False  # assignee only, not manager of the Sales task
    assert client.get(f"{BASE}/999999", headers=org["hr"]).status_code == 404
    assert client.get(f"{BASE}/abc", headers=org["hr"]).status_code == 422


# --- Editing and assignment -----------------------------------------------------------------------------------


def test_patch_rules(client, db, org) -> None:
    task = create(client, org["manager_h"], assignee_employee_id=org["eng"].id)
    tid = task["id"]
    assert client.patch(f"{BASE}/{tid}", headers=org["eng_h"], json={"priority": "LOW"}).status_code == 403
    for body in ({"status": "COMPLETED"}, {"assignee_employee_id": 1}, {"title": None}, {"priority": "NOPE"},
                 {"description": "x" * 2001}, {"due_date": "2020-01-01"}):
        assert client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json=body).status_code == 422, body
    unchanged = client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json={"priority": "MEDIUM"}).json()
    assert unchanged["version"] == 1 and len(events(db, tid)) == 1  # no-op
    due = (today() + timedelta(days=3)).isoformat()
    updated = client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json={"due_date": due, "description": SECRET_TEXT})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json={"due_date": due})  # repeat: nothing new
    assert [n.title for n in notes(db, org["eng"].user_id)] == ["Task assigned to you", "Task due date changed"]
    assert audits(db, tid)[-1].details == {"fields": ["description", "due_date"]}
    stale = client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json={"priority": "LOW", "expected_version": 1})
    assert stale.status_code == 409
    force(db, tid, TaskStatus.COMPLETED)
    assert client.patch(f"{BASE}/{tid}", headers=org["manager_h"], json={"priority": "LOW"}).status_code == 409


def test_assignment_rules(client, db, org) -> None:
    task = create(client, org["manager_h"], assignee_employee_id=org["eng"].id)
    tid = task["id"]
    assert client.post(f"{BASE}/{tid}/assign", headers=org["eng_h"],
                       json={"assignee_employee_id": org["eng"].id}).status_code == 403  # no self-(re)assignment
    assert client.post(f"{BASE}/{tid}/assign", headers=org["manager_h"],
                       json={"assignee_employee_id": org["sales"].id}).status_code == 404
    moved = client.post(f"{BASE}/{tid}/assign", headers=org["manager_h"], json={"assignee_employee_id": org["eng2"].id})
    assert moved.status_code == 200 and moved.json()["assignee"]["id"] == org["eng2"].id
    client.post(f"{BASE}/{tid}/assign", headers=org["manager_h"], json={"assignee_employee_id": org["eng2"].id})  # repeat
    assert [e.event_type.value for e in events(db, tid)] == ["CREATED", "ASSIGNED"]
    assert events(db, tid)[1].details == {"from_employee_id": org["eng"].id, "to_employee_id": org["eng2"].id}
    assert len(notes(db, org["eng2"].user_id)) == 1
    assert client.get(f"{BASE}/{tid}", headers=org["eng_h"]).status_code == 404  # former assignee loses access
    force(db, tid, TaskStatus.CANCELLED)
    assert client.post(f"{BASE}/{tid}/assign", headers=org["manager_h"],
                       json={"assignee_employee_id": org["eng"].id}).status_code == 409


# --- Transitions ---------------------------------------------------------------------------------------------

EXPECTED = {  # (current status, action) -> resulting status, "noop", or "conflict"
    ("OPEN", "start"): "IN_PROGRESS", ("OPEN", "block"): "BLOCKED", ("OPEN", "unblock"): "conflict",
    ("OPEN", "complete"): "COMPLETED", ("OPEN", "cancel"): "CANCELLED", ("OPEN", "reopen"): "noop",
    ("IN_PROGRESS", "start"): "noop", ("IN_PROGRESS", "block"): "BLOCKED", ("IN_PROGRESS", "unblock"): "noop",
    ("IN_PROGRESS", "complete"): "COMPLETED", ("IN_PROGRESS", "cancel"): "CANCELLED", ("IN_PROGRESS", "reopen"): "conflict",
    ("BLOCKED", "start"): "conflict", ("BLOCKED", "block"): "noop", ("BLOCKED", "unblock"): "IN_PROGRESS",
    ("BLOCKED", "complete"): "conflict", ("BLOCKED", "cancel"): "CANCELLED", ("BLOCKED", "reopen"): "conflict",
    ("COMPLETED", "start"): "conflict", ("COMPLETED", "block"): "conflict", ("COMPLETED", "unblock"): "conflict",
    ("COMPLETED", "complete"): "noop", ("COMPLETED", "cancel"): "conflict", ("COMPLETED", "reopen"): "OPEN",
    ("CANCELLED", "start"): "conflict", ("CANCELLED", "block"): "conflict", ("CANCELLED", "unblock"): "conflict",
    ("CANCELLED", "complete"): "conflict", ("CANCELLED", "cancel"): "noop", ("CANCELLED", "reopen"): "OPEN",
}
EXTRA = {"block": {"block_reason": "WAITING_ON_INFORMATION"}, "cancel": {"cancellation_reason": "No longer required"}}


def test_transition_table_is_complete() -> None:
    assert {(s.value, a) for s in TaskStatus for a in service.TRANSITIONS} == set(EXPECTED)


@pytest.mark.parametrize("current, action", sorted(EXPECTED))
def test_every_transition(client, db, org, current, action) -> None:
    task = create(client, org["hr"], assignee_employee_id=org["eng"].id)
    force(db, task["id"], TaskStatus(current))
    before = len(events(db, task["id"]))
    response = client.post(f"{BASE}/{task['id']}/transition", headers=org["hr"], json={"action": action, **EXTRA.get(action, {})})
    outcome = EXPECTED[(current, action)]
    if outcome == "conflict":
        assert response.status_code == 409 and len(events(db, task["id"])) == before
    elif outcome == "noop":
        assert response.status_code == 200 and response.json()["status"] == current and len(events(db, task["id"])) == before
    else:
        assert response.status_code == 200 and response.json()["status"] == outcome
        latest = events(db, task["id"])[-1]
        assert (latest.from_status.value, latest.to_status.value) == (current, outcome)


@pytest.mark.parametrize("body", [{"action": "block"}, {"action": "cancel"}, {"action": "cancel", "cancellation_reason": "no"},
                                  {"action": "block", "block_reason": "BORED"},
                                  {"action": "start", "completion_summary": "x"}, {"action": "finish"},
                                  {"action": "complete", "completion_summary": "x" * 2001}, {"status": "COMPLETED"},
                                  {"action": "block", "block_reason": "OTHER", "cancellation_reason": "Not needed"}])
def test_transition_payload_validation(client, org, body) -> None:
    task = create(client, org["hr"])
    assert client.post(f"{BASE}/{task['id']}/transition", headers=org["hr"], json=body).status_code == 422


def test_assignee_lifecycle_history_audit_and_notifications(client, db, org) -> None:
    task = create(client, org["manager_h"], assignee_employee_id=org["eng"].id)
    tid = task["id"]
    act(client, org["eng_h"], tid, "start")
    blocked = act(client, org["eng_h"], tid, "block", block_reason="WAITING_ON_APPROVAL", block_details=SECRET_TEXT)
    assert (blocked["block_reason"], blocked["block_details"]) == ("WAITING_ON_APPROVAL", SECRET_TEXT)
    act(client, org["eng_h"], tid, "unblock")
    done = act(client, org["eng_h"], tid, "complete", completion_summary="Delivered the agreed training plan")
    assert done["completed_at"] is not None and done["completion_summary"] == "Delivered the agreed training plan"
    assert done["block_reason"] is None
    act(client, org["eng_h"], tid, "complete")  # repeat: no-op
    act(client, org["eng_h"], tid, "reopen", 403)
    act(client, org["eng_h"], tid, "cancel", 403, cancellation_reason="Not needed")
    reopened = act(client, org["manager_h"], tid, "reopen")
    assert reopened["completed_at"] is None and reopened["completion_summary"] is None and reopened["status"] == "OPEN"
    cancelled = act(client, org["manager_h"], tid, "cancel", cancellation_reason="Duplicate of another task")
    assert cancelled["cancelled_at"] is not None

    history = events(db, tid)
    assert [e.event_type.value for e in history] == ["CREATED", "STARTED", "BLOCKED", "UNBLOCKED", "COMPLETED",
                                                     "REOPENED", "CANCELLED"]
    assert [e.version for e in history] == list(range(1, 8)) and cancelled["version"] == 7
    assert history[2].details == {"block_reason": "WAITING_ON_APPROVAL"}
    assert [a.action for a in audits(db, tid)] == ["task.created", "task.started", "task.blocked", "task.unblocked",
                                                   "task.completed", "task.reopened", "task.cancelled"]
    manager_notes = [n.title for n in notes(db, org["manager"].user_id)]
    assert manager_notes == ["Task blocked", "Task completed"]  # creator informed; never the actor
    assert [n.title for n in notes(db, org["eng"].user_id)] == ["Task assigned to you", "Task reopened", "Task cancelled"]
    text = str([a.details for a in audits(db, tid)] + [e.details for e in history] + [n.message for n in notes(db)])
    for secret in (SECRET_TEXT, SECRET_TITLE, "Delivered", "Duplicate of"):
        assert secret not in text
    page = client.get(f"{BASE}/{tid}/history", headers=org["eng_h"], params={"page_size": 3}).json()
    assert page["total"] == 7 and [e["version"] for e in page["items"]] == [1, 2, 3]
    detail = client.get(f"{BASE}/{tid}", headers=org["eng_h"]).json()
    assert [e["version"] for e in detail["recent_history"]] == [7, 6, 5, 4, 3, 2, 1]


def test_non_assignee_employee_cannot_act(client, org, staff) -> None:
    other, other_h = staff(department="Engineering")
    task = create(client, org["hr"], assignee_employee_id=org["eng"].id)
    assert client.post(f"{BASE}/{task['id']}/transition", headers=other_h, json={"action": "start"}).status_code == 404
    assert client.get(f"{BASE}/{task['id']}/history", headers=other_h).status_code == 404


def test_stale_version_and_concurrent_change(client, db, org) -> None:
    task = create(client, org["hr"])
    act(client, org["hr"], task["id"], "start", 409, expected_version=5)
    stale = db.get(Task, task["id"])
    act(client, org["hr"], task["id"], "start")
    with pytest.raises(service.ConcurrentChange):
        service.apply_change(db, stale, db.get(User, 1), utcnow(), service.TRANSITIONS["block"].event,
                             {"status": TaskStatus.BLOCKED, "block_reason": "OTHER"})
    db.rollback()


def test_audit_failure_rolls_back_transition(client, db, org, monkeypatch, session_factory) -> None:
    from app.api import tasks as api

    task = create(client, org["hr"], assignee_employee_id=org["eng"].id)

    def failing(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(api, "record_audit", failing)
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(f"{BASE}/{task['id']}/transition", headers=org["hr"],
                    json={"action": "cancel", "cancellation_reason": "Not needed"}).status_code == 500
    assert raw.post(BASE, headers=org["hr"], json={"title": "Will not exist"}).status_code == 500
    with session_factory() as fresh:
        assert fresh.get(Task, task["id"]).status == TaskStatus.OPEN
        assert fresh.scalar(select(func.count(TaskEvent.id))) == 1
        assert fresh.scalar(select(func.count(Task.id))) == 1
        assert fresh.scalar(select(func.count(Notification.id)).where(Notification.category == "task")) == 1


# --- Listing and summary --------------------------------------------------------------------------------------


def test_filters_sorting_pagination_and_overdue(client, db, org) -> None:
    low = create(client, org["hr"], priority="LOW", assignee_employee_id=org["eng"].id,
                 due_date=(today() + timedelta(days=10)).isoformat())
    critical = create(client, org["hr"], priority="CRITICAL", task_type="COMPLIANCE")
    late = create(client, org["hr"], priority="HIGH", assignee_employee_id=org["eng2"].id)
    force(db, late["id"], TaskStatus.IN_PROGRESS, due_date=today() - timedelta(days=1))
    done_late = create(client, org["hr"])
    force(db, done_late["id"], TaskStatus.COMPLETED, due_date=today() - timedelta(days=3))

    def ids(**params):
        response = client.get(BASE, headers=org["hr"], params={"page_size": 100, **params})
        assert response.status_code == 200, response.text
        return [t["id"] for t in response.json()["items"]]

    assert ids(overdue=True) == [late["id"]]
    assert late["id"] not in ids(overdue=False) and done_late["id"] in ids(overdue=False)
    assert ids(priority="CRITICAL") == [critical["id"]] and ids(task_type="COMPLIANCE") == [critical["id"]]
    assert ids(status="COMPLETED") == [done_late["id"]]
    assert ids(assignee_employee_id=org["eng"].id) == [low["id"]]
    assert ids(sort="priority")[:2] == [critical["id"], late["id"]]
    assert ids(sort="due") == [done_late["id"], late["id"], low["id"], critical["id"]]  # no due date last
    assert ids(due_from=today().isoformat(), due_to=(today() + timedelta(days=30)).isoformat()) == [low["id"]]
    assert ids(status="BLOCKED") == []
    mine = client.get(BASE, headers=org["eng_h"], params={"assigned_to_me": True}).json()
    assert [t["id"] for t in mine["items"]] == [low["id"]]
    pages = [client.get(BASE, headers=org["hr"], params={"page": p, "page_size": 3, "sort": "created"}).json()["items"]
             for p in (1, 2)]
    assert len({t["id"] for page in pages for t in page}) == 4 and pages[0][0]["id"] == done_late["id"]
    for params in ({"sort": "random"}, {"page_size": 101}, {"page": 0}, {"status": "DONE"},
                   {"due_from": "2026-05-02", "due_to": "2026-05-01"}, {"unknown": 1}):
        assert client.get(BASE, headers=org["hr"], params=params).status_code == 422


def test_summary_is_scoped(client, db, org) -> None:
    create(client, org["hr"], assignee_employee_id=org["eng"].id, priority="CRITICAL")
    late = create(client, org["hr"], assignee_employee_id=org["sales"].id)
    force(db, late["id"], TaskStatus.BLOCKED, due_date=today() - timedelta(days=2))
    create(client, org["hr"], department_id=org["eng"].department_id)
    hr = client.get(f"{BASE}/summary", headers=org["hr"]).json()
    assert hr["by_status"] == {"OPEN": 2, "IN_PROGRESS": 0, "BLOCKED": 1, "COMPLETED": 0, "CANCELLED": 0}
    assert hr["overdue"] == 1 and hr["unassigned_active"] == 1 and hr["active_by_priority"]["CRITICAL"] == 1
    manager = client.get(f"{BASE}/summary", headers=org["manager_h"]).json()
    assert manager["by_status"]["OPEN"] == 2 and manager["overdue"] == 0
    employee = client.get(f"{BASE}/summary", headers=org["eng_h"]).json()
    assert sum(employee["by_status"].values()) == 1 and employee["assigned_to_me_active"] == 1


# --- Sources ---------------------------------------------------------------------------------------------------


def test_tasks_linked_to_interventions(client, db, org, add) -> None:
    eng_intervention = add.intervention(org["eng2"])
    sales_intervention = add.intervention(org["sales"])
    self_intervention = add.intervention(org["manager"])
    linked = create(client, org["manager_h"], intervention_id=eng_intervention.id,
                    assignee_employee_id=org["manager"].id)
    assert linked["intervention_id"] == eng_intervention.id and linked["department_id"] == org["eng"].department_id
    create(client, org["manager_h"], 404, intervention_id=sales_intervention.id)
    create(client, org["manager_h"], 404, intervention_id=self_intervention.id)  # about themselves
    create(client, org["hr"], 404, intervention_id=999)
    create(client, org["hr"], 422, intervention_id=eng_intervention.id, department_id=org["sales"].department_id)
    employee_assignee = create(client, org["hr"], 422, intervention_id=eng_intervention.id,
                               assignee_employee_id=org["eng"].id)
    assert "cannot see the linked record" in employee_assignee["detail"]
    hidden = create(client, org["hr"], intervention_id=self_intervention.id)
    assert client.get(f"{BASE}/{hidden['id']}", headers=org["manager_h"]).status_code == 404  # about the manager
    act(client, org["manager_h"], linked["id"], "complete")
    db.expire_all()
    assert db.get(Intervention, eng_intervention.id).status == InterventionStatus.OPEN  # untouched
    assert client.get(BASE, headers=org["hr"], params={"intervention_id": eng_intervention.id}).json()["total"] == 1


def test_tasks_linked_to_recommendations(client, db, org, add) -> None:
    add.alert(org["eng2"])
    assert client.post("/api/recommendations/evaluate", json={}, headers=org["hr"]).status_code == 200
    from app.models import Recommendation, RecommendationScope

    rec = db.scalar(select(Recommendation).where(Recommendation.scope == RecommendationScope.INDIVIDUAL))
    task = create(client, org["manager_h"], recommendation_id=rec.id, idempotency_key="rec-task-0001")
    assert task["recommendation_id"] == rec.id and task["department_id"] == org["eng"].department_id
    again = client.post(BASE, headers=org["manager_h"], json={"title": SECRET_TITLE, "recommendation_id": rec.id,
                                                              "idempotency_key": "rec-task-0001"})
    assert again.status_code == 200 and again.json()["id"] == task["id"]
    create(client, org["eng_h"], 403, recommendation_id=rec.id)
    create(client, org["manager_h"], 404, recommendation_id=999)


# --- Audit visibility -----------------------------------------------------------------------------------------


def test_task_audit_records_follow_department_scope(client, org) -> None:
    eng = create(client, org["hr"], assignee_employee_id=org["eng"].id)
    sales = create(client, org["hr"], assignee_employee_id=org["sales"].id)
    manager_ids = {i["entity_id"] for i in client.get("/api/audit-logs", headers=org["manager_h"],
                                                      params={"entity_type": "task"}).json()["items"]}
    assert manager_ids == {str(eng["id"])}
    hr_ids = {i["entity_id"] for i in client.get("/api/audit-logs", headers=org["hr"],
                                                 params={"entity_type": "task"}).json()["items"]}
    assert hr_ids == {str(eng["id"]), str(sales["id"])}
