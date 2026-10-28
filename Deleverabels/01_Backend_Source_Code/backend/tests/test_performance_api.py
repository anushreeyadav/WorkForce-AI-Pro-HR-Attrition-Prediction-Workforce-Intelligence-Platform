"""Performance management (cycles, goals, reviews) over SYNTHETIC records (in-memory SQLite)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    AuditLog,
    EmploymentStatus,
    Notification,
    PerformanceEvent,
    PerformanceReview,
    ReviewCycle,
    ReviewCycleStatus,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.services import performance as service
from tests.test_workforce_activity_api import org, today  # noqa: F401  (pytest fixtures)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/performance"
SELF_TEXT = "ZZ-self-assessment-text-ZZ"
SUMMARY_TEXT = "ZZ-manager-summary-text-ZZ"
PRIVATE_TEXT = "ZZ-private-manager-note-ZZ"


def dates(**overrides) -> dict:
    t = today()
    base = {"period_start": (t - timedelta(days=60)).isoformat(), "period_end": (t + timedelta(days=30)).isoformat(),
            "self_assessment_deadline": (t + timedelta(days=10)).isoformat(),
            "manager_review_deadline": (t + timedelta(days=20)).isoformat()}
    return {**base, **overrides}


def post(client, path, headers, expected=200, **body) -> dict:
    response = client.post(f"{BASE}{path}", headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def patch(client, path, headers, expected=200, **body) -> dict:
    response = client.patch(f"{BASE}{path}", headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def get(client, path, headers, expected=200, **params) -> dict:
    response = client.get(f"{BASE}{path}", headers=headers, params=params)
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture
def people(db, org):
    ids = {u.email: u.id for u in db.scalars(select(User))}
    return {**org, "hr_id": ids["hr.activity@example.com"], "admin_id": ids["admin.activity@example.com"],
            "manager_id": org["manager"].user_id}


def new_cycle(client, headers, name="FY2026 H2", open_it=True, **overrides) -> dict:
    cycle = post(client, "/cycles", headers, 201, name=name, **dates(**overrides))
    if open_it:
        cycle = post(client, f"/cycles/{cycle['id']}/transition", headers, action="open")
    return cycle


@pytest.fixture
def review(client, people):
    cycle = new_cycle(client, people["hr"])
    created = post(client, "/reviews", people["hr"], 201, cycle_id=cycle["id"], employee_id=people["eng"].id,
                   reviewer_user_id=people["manager_id"])
    return {"cycle": cycle, "review": created, "id": created["id"]}


def events(db, **subject) -> list[PerformanceEvent]:
    db.expire_all()
    column, value = next(iter(subject.items()))
    return list(db.scalars(select(PerformanceEvent).where(getattr(PerformanceEvent, column) == value)
                           .order_by(PerformanceEvent.version)))


def notes(db, user_id) -> list[str]:
    db.expire_all()
    return [n.title for n in db.scalars(select(Notification).where(Notification.user_id == user_id,
                                                                   Notification.category == "performance")
                                        .order_by(Notification.id))]


def set_deadlines(db, cycle_id, self_days: int, manager_days: int) -> None:
    cycle = db.get(ReviewCycle, cycle_id)
    cycle.self_assessment_deadline = today() + timedelta(days=self_days)
    cycle.manager_review_deadline = today() + timedelta(days=manager_days)
    db.commit()


# --- Cycles ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [
    {"period_start": "2026-12-31", "period_end": "2026-01-01"},
    {"self_assessment_deadline": "2000-01-01"},
    {"manager_review_deadline": "2000-01-01"},
    {"period_start": "2020-01-01", "period_end": "2026-12-31"},
    {"name": "ab"}, {"name": "x" * 121}, {"description": "x" * 2001}, {"status": "OPEN"},
])
def test_cycle_validation(client, people, overrides) -> None:
    body = {"name": "Valid cycle", **dates(), **overrides}
    assert client.post(f"{BASE}/cycles", headers=people["hr"], json=body).status_code == 422


def test_cycle_create_permissions_visibility_and_duplicates(client, db, people) -> None:
    for who in ("manager_h", "eng_h"):
        assert client.post(f"{BASE}/cycles", headers=people[who], json={"name": "Nope", **dates()}).status_code == 403
    draft = new_cycle(client, people["hr"], name="Draft cycle", open_it=False)
    assert client.post(f"{BASE}/cycles", headers=people["admin"], json={"name": "Draft cycle", **dates()}).status_code == 409
    opened = new_cycle(client, people["hr"], name="Open cycle")
    assert get(client, f"/cycles/{draft['id']}", people["manager_h"], 404)
    assert [c["id"] for c in get(client, "/cycles", people["manager_h"])["items"]] == [opened["id"]]
    assert get(client, "/cycles", people["eng_h"])["total"] == 0  # no review or goal in it yet
    post(client, "/reviews", people["hr"], 201, cycle_id=opened["id"], employee_id=people["eng"].id,
         reviewer_user_id=people["manager_id"])
    assert [c["id"] for c in get(client, "/cycles", people["eng_h"])["items"]] == [opened["id"]]
    [created] = events(db, cycle_id=draft["id"])
    assert created.event_type.value == "CYCLE_CREATED"


CYCLE_EXPECTED = {
    ("DRAFT", "open"): "OPEN", ("DRAFT", "start_reviews"): "conflict", ("DRAFT", "complete"): "conflict",
    ("DRAFT", "cancel"): "CANCELLED",
    ("OPEN", "open"): "noop", ("OPEN", "start_reviews"): "IN_PROGRESS", ("OPEN", "complete"): "conflict",
    ("OPEN", "cancel"): "CANCELLED",
    ("IN_PROGRESS", "open"): "conflict", ("IN_PROGRESS", "start_reviews"): "noop", ("IN_PROGRESS", "complete"): "COMPLETED",
    ("IN_PROGRESS", "cancel"): "CANCELLED",
    ("COMPLETED", "open"): "conflict", ("COMPLETED", "start_reviews"): "conflict", ("COMPLETED", "complete"): "noop",
    ("COMPLETED", "cancel"): "conflict",
    ("CANCELLED", "open"): "conflict", ("CANCELLED", "start_reviews"): "conflict", ("CANCELLED", "complete"): "conflict",
    ("CANCELLED", "cancel"): "noop",
}


def test_cycle_transition_table_is_complete() -> None:
    assert {(s.value, a) for s in ReviewCycleStatus for a in service.CYCLE_TRANSITIONS} == set(CYCLE_EXPECTED)


@pytest.mark.parametrize("current, action", sorted(CYCLE_EXPECTED))
def test_every_cycle_transition(client, db, people, current, action) -> None:
    cycle = new_cycle(client, people["hr"], open_it=False)
    row = db.get(ReviewCycle, cycle["id"])
    row.status = ReviewCycleStatus(current)
    row.cancellation_reason = "Setup" if current == "CANCELLED" else None
    db.commit()
    before = len(events(db, cycle_id=cycle["id"]))
    body = {"action": action, **({"cancellation_reason": "Budget freeze"} if action == "cancel" else {})}
    response = client.post(f"{BASE}/cycles/{cycle['id']}/transition", headers=people["hr"], json=body)
    outcome = CYCLE_EXPECTED[(current, action)]
    if outcome == "conflict":
        assert response.status_code == 409 and len(events(db, cycle_id=cycle["id"])) == before
    elif outcome == "noop":
        assert response.status_code == 200 and len(events(db, cycle_id=cycle["id"])) == before
    else:
        assert response.status_code == 200 and response.json()["status"] == outcome


def test_cycle_rules(client, db, people, review) -> None:
    cid = review["cycle"]["id"]
    assert client.post(f"{BASE}/cycles/{cid}/transition", headers=people["hr"], json={"action": "cancel"}).status_code == 422
    assert client.post(f"{BASE}/cycles/{cid}/transition", headers=people["manager_h"],
                       json={"action": "start_reviews"}).status_code == 403
    post(client, f"/cycles/{cid}/transition", people["hr"], action="start_reviews")
    complete = client.post(f"{BASE}/cycles/{cid}/transition", headers=people["hr"], json={"action": "complete"})
    assert complete.status_code == 409 and "1 review" in complete.text
    patch(client, f"/cycles/{cid}", people["hr"], 409, period_start=(today() - timedelta(days=90)).isoformat())
    patch(client, f"/cycles/{cid}", people["hr"], 422, manager_review_deadline=(today() - timedelta(days=90)).isoformat())
    patch(client, f"/cycles/{cid}", people["manager_h"], 403, description="x")
    updated = patch(client, f"/cycles/{cid}", people["hr"], description="Second half review",
                    manager_review_deadline=(today() + timedelta(days=25)).isoformat())
    patch(client, f"/cycles/{cid}", people["hr"], 409, description="Stale", expected_version=1)
    assert events(db, cycle_id=cid)[-1].details == {"fields": ["description", "manager_review_deadline"]}
    assert updated["version"] == 4  # created, opened, reviews started, updated
    post(client, f"/cycles/{cid}/transition", people["hr"], action="cancel", cancellation_reason="Reorganisation")
    patch(client, f"/cycles/{cid}", people["hr"], 409, description="Too late")
    patch(client, f"/reviews/{review['id']}", people["eng_h"], 409, self_assessment=SELF_TEXT)  # frozen


# --- Goals -----------------------------------------------------------------------------------------------------

GOAL = {"title": "Ship onboarding guide", "measurable_target": "Guide published and used by 5 new hires",
        "target_value": "5", "unit": "hires"}


def goal_body(**overrides) -> dict:
    return {**GOAL, "start_date": today().isoformat(), "target_date": (today() + timedelta(days=60)).isoformat(),
            **overrides}


@pytest.mark.parametrize("overrides", [{"progress_percent": 101}, {"progress_percent": -1}, {"title": "x" * 151},
                                       {"target_value": "-1"}, {"start_date": "2030-01-01"}, {"status": "COMPLETED"},
                                       {"measurable_target": "ab"}, {"category": "FUN"}, {"employee_id": 0}])
def test_goal_validation(client, people, overrides) -> None:
    assert client.post(f"{BASE}/goals", headers=people["eng_h"], json=goal_body(**overrides)).status_code == 422


def test_goal_ownership_and_scope(client, db, people) -> None:
    own = post(client, "/goals", people["eng_h"], 201, **goal_body())
    assert own["employee"]["id"] == people["eng"].id and own["status"] == "NOT_STARTED" and own["can_manage"] is False
    post(client, "/goals", people["eng_h"], 404, **goal_body(employee_id=people["eng2"].id))
    by_manager = post(client, "/goals", people["manager_h"], 201, **goal_body(employee_id=people["eng2"].id))
    assert by_manager["can_manage"] is True and notes(db, people["eng2"].user_id) == ["New goal"]
    post(client, "/goals", people["manager_h"], 404, **goal_body(employee_id=people["sales"].id))
    post(client, "/goals", people["hr"], 201, **goal_body(employee_id=people["sales"].id))
    people["eng2"].status = EmploymentStatus.INACTIVE
    db.commit()
    post(client, "/goals", people["hr"], 422, **goal_body(employee_id=people["eng2"].id))
    ids = lambda h: {g["id"] for g in get(client, "/goals", people[h], page_size=100)["items"]}  # noqa: E731
    assert ids("eng_h") == {own["id"]}
    assert ids("manager_h") == {own["id"], by_manager["id"]}
    assert len(ids("hr")) == 3
    get(client, f"/goals/{by_manager['id']}", people["eng_h"], 404)


def test_goal_updates_completion_and_history(client, db, people) -> None:
    goal = post(client, "/goals", people["eng_h"], 201, **goal_body())
    gid = goal["id"]
    patch(client, f"/goals/{gid}", people["eng_h"], 422, status="COMPLETED")  # progress is 0
    patch(client, f"/goals/{gid}", people["eng_h"], 422, start_date=(today() + timedelta(days=90)).isoformat())
    patch(client, f"/goals/{gid}", people["eng_h"], 422, progress_percent=None)
    for field, value in (("manager_feedback", "Good work"), ("employee_id", people["eng2"].id), ("status", "CANCELLED")):
        patch(client, f"/goals/{gid}", people["eng_h"], 403, **{field: value})
    patch(client, f"/goals/{gid}", people["eng_h"], progress_percent=40, status="IN_PROGRESS")
    patch(client, f"/goals/{gid}", people["eng_h"], status="AT_RISK")
    assert notes(db, people["manager_id"]) == ["Goal status changed"]  # employee's manager informed
    patch(client, f"/goals/{gid}", people["eng_h"], progress_percent=100)
    done = patch(client, f"/goals/{gid}", people["eng_h"], status="COMPLETED")
    assert done["completed_at"] is not None
    patch(client, f"/goals/{gid}", people["eng_h"], 409, title="Edited after completion")
    patch(client, f"/goals/{gid}", people["eng_h"], status="COMPLETED")  # no-op
    reopened = patch(client, f"/goals/{gid}", people["manager_h"], status="IN_PROGRESS", progress_percent=90,
                     manager_feedback="Please add the FAQ section")
    assert reopened["completed_at"] is None and reopened["manager_feedback"] == "Please add the FAQ section"
    patch(client, f"/goals/{gid}", people["manager_h"], 409, progress_percent=95, expected_version=2)
    history = events(db, goal_id=gid)
    assert [e.event_type.value for e in history] == ["GOAL_CREATED", "GOAL_STATUS_CHANGED", "GOAL_STATUS_CHANGED",
                                                     "GOAL_PROGRESS_CHANGED", "GOAL_STATUS_CHANGED", "GOAL_STATUS_CHANGED"]
    assert [e.version for e in history] == list(range(1, 7))
    assert history[3].details == {"fields": ["progress_percent"], "progress": {"from": 40, "to": 100}}
    page = get(client, f"/goals/{gid}/history", people["eng_h"], page_size=2)
    assert page["total"] == 6 and [e["version"] for e in page["items"]] == [1, 2]
    assert "Goal updated" in notes(db, people["eng"].user_id)
    audit_text = str([a.details for a in db.scalars(select(AuditLog).where(AuditLog.entity_type == "performance_goal"))])
    assert "FAQ" not in audit_text and "onboarding" not in audit_text


def test_managers_cannot_manage_their_own_goals(client, people) -> None:
    own = post(client, "/goals", people["manager_h"], 201, **goal_body())
    assert own["can_manage"] is False
    patch(client, f"/goals/{own['id']}", people["manager_h"], 403, manager_feedback="Self praise")
    patch(client, f"/goals/{own['id']}", people["hr"], manager_feedback="Reviewed by HR")


def test_goals_cannot_join_closed_cycles(client, db, people) -> None:
    draft = new_cycle(client, people["hr"], name="Draft goals", open_it=False)
    post(client, "/goals", people["eng_h"], 404, **goal_body(cycle_id=draft["id"]))
    row = db.get(ReviewCycle, draft["id"])
    row.status, row.cancellation_reason = ReviewCycleStatus.CANCELLED, "Setup"
    db.commit()
    post(client, "/goals", people["hr"], 409, **goal_body(employee_id=people["eng"].id, cycle_id=draft["id"]))


# --- Reviews ---------------------------------------------------------------------------------------------------


def test_review_creation_rules(client, db, people, staff) -> None:
    cycle = new_cycle(client, people["hr"])
    body = {"cycle_id": cycle["id"], "employee_id": people["eng"].id, "reviewer_user_id": people["manager_id"]}
    created = post(client, "/reviews", people["manager_h"], 201, **body)
    assert created["status"] == "ASSIGNED" and created["your_role"] == "reviewer"
    assert notes(db, people["eng"].user_id) == ["Performance review opened"]
    assert notes(db, people["manager_id"]) == []  # the actor is never notified
    post(client, "/reviews", people["hr"], 409, **body)  # one review per employee per cycle
    reviewer_cases = [(people["eng2"].user_id, 422),  # an EMPLOYEE cannot review
                      (people["sales"].user_id, 422)]
    for reviewer, code in reviewer_cases:
        post(client, "/reviews", people["hr"], code, cycle_id=cycle["id"], employee_id=people["eng2"].id,
             reviewer_user_id=reviewer)
    sales_manager, _ = staff(MANAGER, department="Sales")
    post(client, "/reviews", people["hr"], 422, cycle_id=cycle["id"], employee_id=people["eng2"].id,
         reviewer_user_id=sales_manager.user_id)  # manager of another department
    post(client, "/reviews", people["manager_h"], 404, cycle_id=cycle["id"], employee_id=people["sales"].id,
         reviewer_user_id=people["manager_id"])
    post(client, "/reviews", people["manager_h"], 403, cycle_id=cycle["id"], employee_id=people["manager"].id,
         reviewer_user_id=people["hr_id"])  # own review
    post(client, "/reviews", people["hr"], 422, cycle_id=cycle["id"], employee_id=people["manager"].id,
         reviewer_user_id=people["manager_id"])  # no self-review
    post(client, "/reviews", people["eng_h"], 404, **{**body, "employee_id": people["eng2"].id})
    draft = new_cycle(client, people["hr"], name="Draft reviews", open_it=False)
    post(client, "/reviews", people["hr"], 409, cycle_id=draft["id"], employee_id=people["eng2"].id,
         reviewer_user_id=people["hr_id"])
    post(client, "/reviews", people["manager_h"], 404, cycle_id=draft["id"], employee_id=people["eng2"].id,
         reviewer_user_id=people["manager_id"])
    people["eng2"].status = EmploymentStatus.INACTIVE
    db.commit()
    post(client, "/reviews", people["hr"], 422, cycle_id=cycle["id"], employee_id=people["eng2"].id,
         reviewer_user_id=people["hr_id"])


def test_full_review_workflow_with_confidentiality(client, db, people, review) -> None:
    rid = review["id"]
    patch(client, f"/reviews/{rid}", people["eng_h"], self_assessment="Draft " + SELF_TEXT)
    assert get(client, f"/reviews/{rid}", people["manager_h"])["self_assessment"] is None  # draft not shared
    submitted = post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"], self_assessment=SELF_TEXT)
    assert submitted["status"] == "SELF_SUBMITTED"
    post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"])  # repeat: no-op
    assert notes(db, people["manager_id"]).count("Self-assessment submitted") == 1
    patch(client, f"/reviews/{rid}", people["eng_h"], 409, self_assessment="Changed my mind")
    patch(client, f"/reviews/{rid}", people["manager_h"], overall_rating=3, manager_summary=SUMMARY_TEXT,
          strengths=["Clear communication"], improvement_areas=["Estimation"], manager_private_notes=PRIVATE_TEXT)
    patch(client, f"/reviews/{rid}", people["manager_h"], overall_rating=4)
    employee_view = get(client, f"/reviews/{rid}", people["eng_h"])
    assert employee_view["manager_evaluation_visible"] is False and employee_view["overall_rating"] is None
    assert employee_view["manager_summary"] is None and employee_view["manager_private_notes"] is None
    assert employee_view["self_assessment"] == SELF_TEXT
    reviewer_view = get(client, f"/reviews/{rid}", people["manager_h"])
    assert reviewer_view["overall_rating"] == 4 and reviewer_view["manager_private_notes"] == PRIVATE_TEXT
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], 422, manager_summary=None)
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], development_goals=["Lead a design review"])
    assert notes(db, people["hr_id"]) == ["Manager review submitted"]
    post(client, f"/reviews/{rid}/finalize", people["manager_h"], 403)
    final = post(client, f"/reviews/{rid}/finalize", people["hr"])
    post(client, f"/reviews/{rid}/finalize", people["hr"])  # repeat: no-op
    assert final["status"] == "FINALIZED" and final["finalized_at"] is not None
    assert notes(db, people["eng"].user_id)[-1] == "Performance review finalized"
    assert notes(db, people["eng"].user_id).count("Performance review finalized") == 1
    after = get(client, f"/reviews/{rid}", people["eng_h"])
    assert (after["overall_rating"], after["rating_label"]) == (4, "Exceeds expectations")
    assert after["manager_summary"] == SUMMARY_TEXT and after["manager_private_notes"] is None
    patch(client, f"/reviews/{rid}", people["manager_h"], 409, overall_rating=5)
    employee_history = get(client, f"/reviews/{rid}/history", people["eng_h"], page_size=100)["items"]
    assert all("rating" not in (e["details"] or {}) for e in employee_history)
    reviewer_history = get(client, f"/reviews/{rid}/history", people["manager_h"], page_size=100)["items"]
    assert [e["event_type"] for e in reviewer_history] == ["REVIEW_CREATED", "REVIEW_UPDATED",
                                                          "SELF_ASSESSMENT_SUBMITTED", "REVIEW_UPDATED", "REVIEW_UPDATED",
                                                          "MANAGER_REVIEW_SUBMITTED", "REVIEW_FINALIZED"]
    assert reviewer_history[4]["details"]["rating"] == {"from": 3, "to": 4}
    audit = list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "performance_review").order_by(AuditLog.id)))
    assert [a.action for a in audit] == ["performance.review_created", "performance.review_updated",
                                         "performance.self_assessment_submitted", "performance.review_updated",
                                         "performance.review_updated", "performance.manager_review_submitted",
                                         "performance.review_finalized"]
    text = str([a.details for a in audit]) + str([n.message for n in db.scalars(select(Notification))])
    for secret in (SELF_TEXT, SUMMARY_TEXT, PRIVATE_TEXT, "Clear communication", "'to': 4", "rating': 4"):
        assert secret not in text


def test_authorized_correction_reopens_finalized_review(client, db, people, review) -> None:
    rid = review["id"]
    post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"], self_assessment=SELF_TEXT)
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], overall_rating=2, manager_summary=SUMMARY_TEXT)
    post(client, f"/reviews/{rid}/finalize", people["hr"])
    post(client, f"/reviews/{rid}/reopen", people["manager_h"], 403, reason="Rating entered incorrectly")
    post(client, f"/reviews/{rid}/reopen", people["hr"], 422, reason="short")
    reopened = post(client, f"/reviews/{rid}/reopen", people["hr"], reason="Rating entered incorrectly")
    assert reopened["status"] == "SELF_SUBMITTED" and reopened["finalized_at"] is None
    assert reopened["reopen_reason"] == "Rating entered incorrectly"
    assert get(client, f"/reviews/{rid}", people["eng_h"])["reopen_reason"] is None
    post(client, f"/reviews/{rid}/reopen", people["hr"], 409, reason="Rating entered incorrectly")
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], overall_rating=3)
    corrected = post(client, f"/reviews/{rid}/finalize", people["hr"])
    assert corrected["overall_rating"] == 3 and corrected["version"] == 7  # created, 2 submits, final, reopen, submit, final
    assert "performance.review_reopened" in {a.action for a in db.scalars(select(AuditLog))}


def test_field_permissions_and_separation(client, db, people, review, staff) -> None:
    rid = review["id"]
    patch(client, f"/reviews/{rid}", people["eng_h"], 403, overall_rating=5)
    patch(client, f"/reviews/{rid}", people["eng_h"], 403, manager_private_notes="mine")
    patch(client, f"/reviews/{rid}", people["eng_h"], 403, reviewer_user_id=people["hr_id"])
    patch(client, f"/reviews/{rid}", people["manager_h"], 403, self_assessment="Written for them")
    patch(client, f"/reviews/{rid}", people["hr"], 403, overall_rating=5)  # HR is not the reviewer
    post(client, f"/reviews/{rid}/submit-self-assessment", people["manager_h"], 403)
    post(client, f"/reviews/{rid}/submit-manager-review", people["eng_h"], 403, overall_rating=5)
    patch(client, f"/reviews/{rid}", people["hr"], 422, reviewer_user_id=people["admin_id"], overall_rating=3)
    moved = patch(client, f"/reviews/{rid}", people["manager_h"], reviewer_user_id=people["hr_id"])
    assert moved["reviewer_user_id"] == people["hr_id"] and notes(db, people["hr_id"])[-1] == "Performance review assigned to you"
    patch(client, f"/reviews/{rid}", people["hr"], 422, reviewer_user_id=people["eng2"].user_id)
    # The department manager can still see the review, but not the private notes, now that HR is the reviewer.
    patch(client, f"/reviews/{rid}", people["hr"], manager_private_notes=PRIVATE_TEXT)
    view = get(client, f"/reviews/{rid}", people["manager_h"])
    assert view["your_role"] == "department_manager" and view["manager_private_notes"] is None
    _, outsider_manager = staff(MANAGER, department="Sales")
    get(client, f"/reviews/{rid}", outsider_manager, 404)
    _, colleague = staff(department="Engineering")
    get(client, f"/reviews/{rid}", colleague, 404)


@pytest.mark.parametrize("body", [{"overall_rating": 0}, {"overall_rating": 6}, {"strengths": ["x"] * 11},
                                  {"strengths": ["x" * 301]}, {"manager_summary": "x" * 8001},
                                  {"manager_private_notes": "x" * 2001}, {"status": "FINALIZED"}, {"version": 9}])
def test_review_input_validation(client, people, review, body) -> None:
    assert client.patch(f"{BASE}/reviews/{review['id']}", headers=people["manager_h"], json=body).status_code == 422


def test_deadlines(client, db, people, review) -> None:
    rid, cid = review["id"], review["cycle"]["id"]
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], 409, overall_rating=3,
         manager_summary=SUMMARY_TEXT)  # self-assessment still possible
    set_deadlines(db, cid, -1, 5)
    patch(client, f"/reviews/{rid}", people["eng_h"], 409, self_assessment=SELF_TEXT)
    post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"], 409, self_assessment=SELF_TEXT)
    submitted = post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], overall_rating=3,
                     manager_summary=SUMMARY_TEXT)
    assert submitted["status"] == "MANAGER_SUBMITTED" and submitted["self_submitted_at"] is None
    set_deadlines(db, cid, -3, -1)
    other = post(client, "/reviews", people["hr"], 201, cycle_id=cid, employee_id=people["eng2"].id,
                 reviewer_user_id=people["manager_id"])
    patch(client, f"/reviews/{other['id']}", people["manager_h"], 409, overall_rating=3)


def test_hr_cannot_review_or_finalize_their_own_review(client, db, people, staff) -> None:
    hr_employee, hr_h = staff(HR, department="Engineering")
    cycle = new_cycle(client, people["admin"], name="HR cycle")
    post(client, "/reviews", people["admin"], 422, cycle_id=cycle["id"], employee_id=hr_employee.id,
         reviewer_user_id=hr_employee.user_id)
    own = post(client, "/reviews", people["admin"], 201, cycle_id=cycle["id"], employee_id=hr_employee.id,
               reviewer_user_id=people["manager_id"])
    assert get(client, f"/reviews/{own['id']}", hr_h)["your_role"] == "employee"
    post(client, f"/reviews/{own['id']}/cancel", hr_h, 403, reason="Not needed")
    set_deadlines(db, cycle["id"], -1, 5)
    post(client, f"/reviews/{own['id']}/submit-manager-review", people["manager_h"], overall_rating=4,
         manager_summary=SUMMARY_TEXT)
    post(client, f"/reviews/{own['id']}/finalize", hr_h, 403)
    post(client, f"/reviews/{own['id']}/finalize", people["admin"])


def test_cancel_and_cycle_completion(client, db, people, review) -> None:
    rid, cid = review["id"], review["cycle"]["id"]
    post(client, f"/reviews/{rid}/cancel", people["hr"], 422, reason="no")
    cancelled = post(client, f"/reviews/{rid}/cancel", people["hr"], reason="Employee transferred")
    post(client, f"/reviews/{rid}/cancel", people["hr"], reason="Employee transferred")  # no-op
    assert cancelled["status"] == "CANCELLED" and len(events(db, review_id=rid)) == 2
    assert get(client, f"/reviews/{rid}", people["eng_h"])["cancellation_reason"] is None
    post(client, f"/cycles/{cid}/transition", people["hr"], action="start_reviews")
    assert post(client, f"/cycles/{cid}/transition", people["hr"], action="complete")["status"] == "COMPLETED"
    post(client, f"/reviews/{rid}/reopen", people["hr"], 409, reason="Too late to change now")


# --- Lists, summary, concurrency, rollback ----------------------------------------------------------------------


def test_review_lists_filters_and_summary(client, db, people, review, staff) -> None:
    cid = review["cycle"]["id"]
    second = post(client, "/reviews", people["hr"], 201, cycle_id=cid, employee_id=people["eng2"].id,
                  reviewer_user_id=people["manager_id"])
    sales_review = post(client, "/reviews", people["hr"], 201, cycle_id=cid, employee_id=people["sales"].id,
                        reviewer_user_id=people["hr_id"])
    set_deadlines(db, cid, -1, 5)
    post(client, f"/reviews/{second['id']}/submit-manager-review", people["manager_h"], overall_rating=5,
         manager_summary=SUMMARY_TEXT)
    post(client, f"/reviews/{second['id']}/finalize", people["hr"])

    def ids(headers, **params):
        params.setdefault("page_size", 100)
        return [r["id"] for r in get(client, "/reviews", headers, **params)["items"]]

    assert set(ids(people["hr"])) == {review["id"], second["id"], sales_review["id"]}
    assert set(ids(people["manager_h"])) == {review["id"], second["id"]}
    assert ids(people["eng_h"]) == [review["id"]]
    assert ids(people["hr"], status="FINALIZED") == [second["id"]]
    assert ids(people["hr"], department_id=people["sales"].department_id) == [sales_review["id"]]
    assert set(ids(people["manager_h"], to_review=True)) == {review["id"], second["id"]}
    assert ids(people["eng_h"], mine=True) == [review["id"]]
    pages = [ids(people["hr"], page=p, page_size=2) for p in (1, 2)]
    assert len({i for p in pages for i in p}) == 3
    for params in ({"status": "DONE"}, {"page_size": 101}, {"sort": "x"}, {"cycle_id": 0}):
        assert client.get(f"{BASE}/reviews", headers=people["hr"], params=params).status_code == 422
    hr = get(client, "/summary", people["hr"], cycle_id=cid)
    assert hr["reviews_by_status"]["FINALIZED"] == 1 and hr["finalized_rating_distribution"]["5"] == 1
    manager = get(client, "/summary", people["manager_h"])
    assert sum(manager["reviews_by_status"].values()) == 2 and manager["reviews_awaiting_you"] == 1
    employee = get(client, "/summary", people["eng_h"])
    assert employee["finalized_rating_distribution"] is None and sum(employee["reviews_by_status"].values()) == 1
    assert employee["rating_scale"]["3"] == "Meets expectations"


def test_stale_versions_and_concurrent_change(client, db, people, review) -> None:
    rid = review["id"]
    patch(client, f"/reviews/{rid}", people["eng_h"], 409, self_assessment=SELF_TEXT, expected_version=7)
    post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"], 409, self_assessment=SELF_TEXT,
         expected_version=7)
    stale = db.get(PerformanceReview, rid)
    patch(client, f"/reviews/{rid}", people["eng_h"], self_assessment=SELF_TEXT)
    with pytest.raises(service.ConcurrentChange):
        service.apply_change(db, stale, db.get(User, people["hr_id"]), utcnow(),
                             service.PerformanceEventType.REVIEW_UPDATED, {"self_assessment": "x"})
    db.rollback()


def test_audit_failure_rolls_back(client, db, people, review, monkeypatch, session_factory) -> None:
    from app.api import performance as api

    rid = review["id"]
    post(client, f"/reviews/{rid}/submit-self-assessment", people["eng_h"], self_assessment=SELF_TEXT)
    post(client, f"/reviews/{rid}/submit-manager-review", people["manager_h"], overall_rating=3, manager_summary=SUMMARY_TEXT)
    monkeypatch.setattr(api, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post(f"{BASE}/reviews/{rid}/finalize", headers=people["hr"], json={}).status_code == 500
    with session_factory() as fresh:
        assert fresh.get(PerformanceReview, rid).status.value == "MANAGER_SUBMITTED"
        assert fresh.scalar(select(func.count(PerformanceEvent.id)).where(PerformanceEvent.review_id == rid)) == 3  # created, two submissions
        titles = [n.title for n in fresh.scalars(select(Notification))]
        assert "Performance review finalized" not in titles


def test_performance_audit_rows_follow_department_scope(client, people, review) -> None:
    sales_cycle_review = post(client, "/reviews", people["hr"], 201, cycle_id=review["cycle"]["id"],
                              employee_id=people["sales"].id, reviewer_user_id=people["hr_id"])
    seen = {i["entity_id"] for i in client.get("/api/audit-logs", headers=people["manager_h"],
                                              params={"entity_type": "performance_review"}).json()["items"]}
    assert seen == {str(review["id"])} and str(sales_cycle_review["id"]) not in seen
