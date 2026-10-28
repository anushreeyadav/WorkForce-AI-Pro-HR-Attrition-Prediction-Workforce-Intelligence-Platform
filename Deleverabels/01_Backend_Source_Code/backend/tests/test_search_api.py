"""Advanced search: module filters, the CRITICAL feed filter and global search. Synthetic data, in-memory SQLite."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Goal,
    GoalStatus,
    Intervention,
    InterventionCategory,
    InterventionPriority,
    InterventionStatus,
    PerformanceReview,
    ReviewCycle,
    ReviewCycleStatus,
    ReviewStatus,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
    User,
    UserRole,
)
from tests.test_datasets_api import csv_bytes, upload
from tests.test_reports_api import payroll_period  # noqa: F401  (pytest fixture)
from tests.test_workforce_activity_api import SENTINEL_SCORE, add, ago, org, today  # noqa: F401  (pytest fixtures)

SECRET = "ZZ-confidential-description-ZZ"


def ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def task(db, title, department, assignee=None, priority=TaskPriority.MEDIUM) -> Task:
    row = Task(title=title, description=SECRET, task_type=TaskType.FOLLOW_UP, priority=priority, status=TaskStatus.OPEN,
               department_id=department, assignee_employee_id=assignee.id if assignee else None, version=1)
    db.add(row)
    db.commit()
    return row


def intervention(db, employee, title, assignee_user_id=None) -> Intervention:
    row = Intervention(employee_id=employee.id, category=InterventionCategory.MANAGER_CHECK_IN, title=title,
                       description=SECRET, priority=InterventionPriority.MEDIUM, status=InterventionStatus.OPEN,
                       assigned_to_user_id=assignee_user_id, version=1)
    db.add(row)
    db.commit()
    return row


def goal(db, employee, title) -> Goal:
    row = Goal(employee_id=employee.id, title=title, category="BUSINESS", measurable_target="Measured outcome",
               start_date=today(), target_date=today() + timedelta(days=30), progress_percent=0,
               status=GoalStatus.NOT_STARTED, manager_feedback=SECRET, version=1)
    db.add(row)
    db.commit()
    return row


# --- Module filters ------------------------------------------------------------------------------------


def test_department_keyword_filter(client, org, make_department) -> None:
    make_department("Customer Success", is_active=False)
    listing = lambda **p: [d["name"] for d in client.get("/api/departments", headers=org["eng_h"], params=p).json()["items"]]  # noqa: E731
    assert listing(q="ENGIN") == ["Engineering"]  # case-insensitive substring
    assert listing(q="s", is_active=True) == ["Sales"]
    assert listing(q="%") == [] and listing(q="_") == []  # wildcards match literally
    assert client.get("/api/departments", headers=org["eng_h"], params={"q": "x" * 101}).status_code == 422


def test_dataset_keyword_filter(client, headers_for, ml_dirs) -> None:
    admin = headers_for(UserRole.ADMIN, "admin.search@example.com")
    for seed, name in enumerate(("Q1 attrition export", "Leaver survey 2026")):
        assert upload(client, admin, csv_bytes(seed=seed), name=name).status_code == 201
    names = [d["display_name"] for d in client.get("/api/datasets", headers=admin, params={"q": "attrition"}).json()["items"]]
    assert names == ["Q1 attrition export"]


def test_payroll_records_employee_filter(client, org, payroll_period) -> None:
    path = f"/api/payroll/periods/{payroll_period['id']}/records"
    rows = client.get(path, headers=org["hr"], params={"employee_id": org["eng"].id}).json()["items"]
    assert [r["employee"]["id"] for r in rows] == [org["eng"].id]
    assert client.get(path, headers=org["manager_h"], params={"employee_id": org["eng"].id}).status_code == 403
    assert client.get(path, headers=org["hr"], params={"employee_id": "1 OR 1=1"}).status_code == 422


def test_risk_alert_detection_date_range(client, db, org, add) -> None:
    old = add.alert(org["eng"], created_at=ago(24 * 10))
    recent = add.alert(org["eng2"], created_at=ago(2))
    params = {"detected_from": (today() - timedelta(days=3)).isoformat(), "detected_to": today().isoformat()}
    assert ids(client.get("/api/risk-monitoring/alerts", headers=org["hr"], params=params)) == [recent.id]
    assert old.id in ids(client.get("/api/risk-monitoring/alerts", headers=org["hr"],
                                    params={"detected_to": (today() - timedelta(days=5)).isoformat()}))
    reversed_range = {"detected_from": today().isoformat(), "detected_to": (today() - timedelta(days=1)).isoformat()}
    assert client.get("/api/risk-monitoring/alerts", headers=org["hr"], params=reversed_range).status_code == 422
    sales = add.alert(org["sales"], created_at=ago(1))
    assert sales.id not in ids(client.get("/api/risk-monitoring/alerts", headers=org["manager_h"], params=params))


def test_intervention_keyword_and_assignee_filters(client, db, org) -> None:
    hr_id = db.scalar(select(User.id).where(User.email == "hr.activity@example.com"))
    a = intervention(db, org["eng"], "Workload Review follow-up", assignee_user_id=hr_id)
    intervention(db, org["eng2"], "Career conversation")
    hidden = intervention(db, org["sales"], "Workload review for Sales")
    assert ids(client.get("/api/interventions", headers=org["manager_h"], params={"q": "workload"})) == [a.id]
    assert set(ids(client.get("/api/interventions", headers=org["hr"], params={"q": "WORKLOAD"}))) == {a.id, hidden.id}
    assert ids(client.get("/api/interventions", headers=org["hr"], params={"assigned_to_user_id": hr_id})) == [a.id]
    assert ids(client.get("/api/interventions", headers=org["hr"], params={"q": SECRET})) == []  # descriptions not searched


def test_task_and_goal_keyword_filters(client, db, org) -> None:
    eng_dept = org["eng"].department_id
    mine = task(db, "Prepare 100% onboarding pack", eng_dept, assignee=org["eng"])
    task(db, "Prepare offboarding", eng_dept)
    assert ids(client.get("/api/tasks", headers=org["hr"], params={"q": "100%"})) == [mine.id]  # % is literal
    assert ids(client.get("/api/tasks", headers=org["eng_h"], params={"q": "prepare"})) == [mine.id]  # own tasks only
    own_goal = goal(db, org["eng"], "Learn Kubernetes")
    goal(db, org["sales"], "Learn negotiation")
    assert ids(client.get("/api/performance/goals", headers=org["eng_h"], params={"q": "learn"})) == [own_goal.id]
    assert len(ids(client.get("/api/performance/goals", headers=org["hr"], params={"q": "LEARN"}))) == 2


@pytest.mark.parametrize("path, params", [
    ("/api/tasks", {"sort": "id; DROP TABLE tasks"}), ("/api/tasks", {"sort": "title"}),
    ("/api/employees", {"sort_by": "work_email"}), ("/api/employees", {"sort_by": "1; --"}),
    ("/api/tasks", {"q": "x" * 101}), ("/api/interventions", {"q": ""}), ("/api/performance/goals", {"unknown": "1"}),
    ("/api/tasks", {"due_from": "2026-05-02", "due_to": "2026-05-01"}), ("/api/tasks", {"page_size": 101}),
    ("/api/recommendations", {"priority": "URGENT"}),
])
def test_invalid_filters_and_sorts_are_rejected(client, org, path, params) -> None:
    assert client.get(path, headers=org["hr"], params=params).status_code == 422


def test_keyword_injection_is_harmless(client, db, org) -> None:
    task(db, "Quarterly check", org["eng"].department_id)
    for term in ("' OR 1=1 --", '"; DROP TABLE tasks; --', "%' OR '%'='", "!%_"):
        assert ids(client.get("/api/tasks", headers=org["hr"], params={"q": term})) == []
    assert db.scalar(select(Task.id)) is not None  # table intact


# --- CRITICAL priority in the activity feed ---------------------------------------------------------------


def test_feed_critical_priority_filter(client, db, org, add, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVITY_UPDATES_SETTLE_SECONDS", "0")  # the recommendation was stored a moment ago
    get_settings.cache_clear()
    add.alert(org["eng2"], created_at=ago(30))  # HIGH and overdue -> a CRITICAL rules-v2 recommendation
    add.attendance(org["eng"], created_at=ago(1))
    assert client.post("/api/recommendations/evaluate", headers=org["hr"], json={"strategy": "rules-v2"}).status_code == 200
    feed = lambda **p: client.get("/api/workforce-activity/feed", headers=org["hr"], params={"page_size": 100, **p})  # noqa: E731
    critical = feed(priority="CRITICAL").json()["items"]
    assert critical and {(i["category"], i["priority"]) for i in critical} == {("recommendation", "CRITICAL")}
    high = feed(priority="HIGH").json()["items"]
    assert high and "CRITICAL" not in {i["priority"] for i in high}
    assert {i["priority"] for i in feed(priority=["HIGH", "CRITICAL"]).json()["items"]} == {"HIGH", "CRITICAL"}
    assert all(i["priority"] is not None for i in critical + high)  # sources without a priority are excluded
    summary = client.get("/api/workforce-activity/summary", headers=org["hr"], params={"priority": "CRITICAL"}).json()
    assert summary["total"] == len(critical)
    updates = client.get("/api/workforce-activity/updates", headers=org["hr"], params={"priority": "CRITICAL"}).json()
    assert updates["items"] and {i["priority"] for i in updates["items"]} == {"CRITICAL"}
    for bad in ("critical", "URGENT", "' OR 1=1"):
        assert feed(priority=bad).status_code == 422


# --- Global search ------------------------------------------------------------------------------------------


def search(client, headers, expected=200, **params) -> dict:
    response = client.get("/api/search", headers=headers, params=params)
    assert response.status_code == expected, response.text
    return response.json()


def found(body, kind) -> set[int]:
    return {r["id"] for r in body["results"] if r["type"] == kind}


@pytest.fixture
def records(db, org, add):
    eng_dept, sales_dept = org["eng"].department_id, org["sales"].department_id
    cycle = ReviewCycle(name="Phoenix cycle", period_start=today() - timedelta(days=30), period_end=today(),
                        self_assessment_deadline=today(), manager_review_deadline=today() + timedelta(days=5),
                        status=ReviewCycleStatus.OPEN, version=1)
    db.add(cycle)
    db.flush()
    eng_review = PerformanceReview(cycle_id=cycle.id, employee_id=org["eng"].id, reviewer_user_id=org["manager"].user_id,
                                   status=ReviewStatus.ASSIGNED, self_assessment=SECRET, version=1)
    sales_review = PerformanceReview(cycle_id=cycle.id, employee_id=org["sales"].id,
                                     reviewer_user_id=db.scalar(select(User.id).where(User.email == "hr.activity@example.com")),
                                     status=ReviewStatus.ASSIGNED, version=1)
    db.add_all([eng_review, sales_review])
    db.commit()
    alert = add.alert(org["eng2"], created_at=ago(30))
    return {
        "eng_task": task(db, "Phoenix launch checklist", eng_dept, assignee=org["eng"]),
        "sales_task": task(db, "Phoenix sales deck", sales_dept),
        "eng_intervention": intervention(db, org["eng2"], "Phoenix workload check"),
        "manager_intervention": intervention(db, org["manager"], "Phoenix support for manager"),
        "eng_goal": goal(db, org["eng"], "Phoenix certification"),
        "sales_goal": goal(db, org["sales"], "Phoenix quota"),
        "cycle": cycle, "eng_review": eng_review, "sales_review": sales_review, "alert": alert,
    }


def test_global_search_respects_each_module_scope(client, db, org, records) -> None:
    r = records
    hr = search(client, org["hr"], q="phoenix", limit_per_type=20)
    assert found(hr, "task") == {r["eng_task"].id, r["sales_task"].id}
    assert found(hr, "intervention") == {r["eng_intervention"].id, r["manager_intervention"].id}
    assert found(hr, "goal") == {r["eng_goal"].id, r["sales_goal"].id}
    assert found(hr, "performance_review") == {r["eng_review"].id, r["sales_review"].id}
    assert found(hr, "review_cycle") == {r["cycle"].id} and hr["unavailable_types"] == []

    manager = search(client, org["manager_h"], q="phoenix", limit_per_type=20)
    assert found(manager, "task") == {r["eng_task"].id}
    assert found(manager, "intervention") == {r["eng_intervention"].id}  # never about the manager themselves
    assert found(manager, "goal") == {r["eng_goal"].id}
    assert found(manager, "performance_review") == {r["eng_review"].id}

    employee = search(client, org["eng_h"], q="phoenix", limit_per_type=20)
    assert found(employee, "task") == {r["eng_task"].id} and found(employee, "goal") == {r["eng_goal"].id}
    assert found(employee, "performance_review") == {r["eng_review"].id}
    assert set(employee["unavailable_types"]) == {"intervention", "recommendation"}
    assert not found(employee, "intervention") and not found(employee, "recommendation")


def test_global_search_employees_and_departments(client, org) -> None:
    hr = search(client, org["hr"], q="Engineer", types=["employee", "department"], limit_per_type=20)
    assert found(hr, "employee") == {org["eng"].id, org["eng2"].id}
    assert found(hr, "department") == {org["eng"].department_id}
    assert found(search(client, org["manager_h"], q="Sam Seller", types="employee"), "employee") == set()
    assert found(search(client, org["eng_h"], q="Ezra", types="employee"), "employee") == set()  # self only
    assert found(search(client, org["eng_h"], q="Edith", types="employee"), "employee") == {org["eng"].id}
    email_search = search(client, org["hr"], q="corp.example.com", types="employee")
    assert email_search["results"] == []  # contact details are not searchable here


def test_global_search_never_returns_confidential_content(client, org, records) -> None:
    assert client.post("/api/recommendations/evaluate", headers=org["hr"], json={"strategy": "rules-v2"}).status_code == 200
    body = client.get("/api/search", headers=org["hr"], params={"q": "phoenix", "limit_per_type": 20}).text
    for secret in (SECRET, SENTINEL_SCORE, "@corp.example.com", "risk_score", "self_assessment", "manager_private_notes"):
        assert secret not in body
    assert search(client, org["hr"], q=SECRET)["results"] == []  # descriptions, feedback and review text are not searched
    recs = search(client, org["hr"], q="supportive", types="recommendation")
    assert recs["results"] and all(set(r["summary"]) == {"rule_version", "category", "priority", "status", "scope"}
                                   for r in recs["results"])
    assert "risk_alert" not in client.get("/api/search", headers=org["hr"], params={"q": "phoenix"}).text


def test_global_search_returns_each_record_once(client, org, records) -> None:
    keys = [(r["type"], r["id"]) for r in search(client, org["hr"], q="phoenix", limit_per_type=20)["results"]]
    assert keys and len(keys) == len(set(keys))
    types = [k[0] for k in keys]
    assert types == sorted(types, key=["employee", "department", "task", "intervention", "recommendation", "goal",
                                       "review_cycle", "performance_review"].index)  # grouped in type order


def test_global_search_ordering_limits_and_dedup(client, db, org) -> None:
    dept = org["eng"].department_id
    substring = task(db, "Annual Plan review", dept)
    prefix = task(db, "Plan B", dept)
    exact = task(db, "plan", dept)
    tie = task(db, "Plan B", dept)
    body = search(client, org["hr"], q="PLAN", types="task", limit_per_type=10)
    assert [r["id"] for r in body["results"]] == [exact.id, prefix.id, tie.id, substring.id]  # exact, prefix, substring; id ties
    assert body == search(client, org["hr"], q="PLAN", types="task", limit_per_type=10)  # deterministic
    limited = search(client, org["hr"], q="plan", types="task", limit_per_type=2)
    assert len(limited["results"]) == 2 and limited["truncated_types"] == ["task"] and limited["counts"] == {"task": 2}
    assert search(client, org["hr"], q="nothing-matches-this")["results"] == []
    assert all(r["resource"] == f"/api/tasks/{r['id']}" for r in body["results"])


@pytest.mark.parametrize("params", [{}, {"q": "a"}, {"q": "x" * 101}, {"q": "ok", "types": "payroll"},
                                    {"q": "ok", "types": "risk_alert"}, {"q": "ok", "limit_per_type": 0},
                                    {"q": "ok", "limit_per_type": 21}, {"q": "ok", "sort": "id"}])
def test_global_search_validation(client, org, params) -> None:
    assert client.get("/api/search", headers=org["hr"], params=params).status_code == 422


def test_global_search_requires_authentication_and_handles_injection(client, org, records) -> None:
    assert client.get("/api/search", params={"q": "phoenix"}).status_code == 401
    for term in ("' OR 1=1 --", "%%", "__", '") UNION SELECT password_hash FROM users --'):
        assert search(client, org["hr"], q=term)["results"] == []


def test_global_search_query_count_is_bounded(client, db, org, count_queries) -> None:
    def measure() -> int:
        start = len(count_queries())
        search(client, org["hr"], q="bulk", limit_per_type=5)
        return len(count_queries()) - start

    task(db, "bulk one", org["eng"].department_id)
    small = measure()
    for n in range(25):
        task(db, f"bulk item {n}", org["eng"].department_id)
    assert measure() == small
