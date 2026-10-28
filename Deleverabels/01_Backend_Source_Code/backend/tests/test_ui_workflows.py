"""UI workflows added in Sub-prompt 30, replayed with exactly the requests the React pages send.

Each test follows one page's journey (Interventions, Risk -> Interventions, Payroll, Performance, Datasets, Activity) with
the same paths, bodies and query parameters the page builds (cleanParams drops empty values), as the role that would
use it, and checks the backend outcome. Synthetic records in in-memory SQLite only; datasets in temporary directories.
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.models import AuditLog, Employee, SalaryComponentKind, UserRole
from tests.test_datasets_api import csv_bytes
from tests.test_recommendations_api import make_alert  # noqa: F401  (pytest fixture)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


def ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def user_id(db, employee) -> int:
    return db.scalar(select(Employee.user_id).where(Employee.id == employee.id))


# --- Interventions.jsx ---------------------------------------------------------------------------------------------------


def test_intervention_lifecycle_as_the_page_sends_it(client, db, staff) -> None:
    hr_emp, hr = staff(HR, department="People")
    target, _ = staff(EMPLOYEE, department="Engineering")
    # InterventionForm: description and due date left empty are dropped; "Assign to me" is on by default.
    created = ok(client.post("/api/interventions", headers=hr, json={
        "employee_id": target.id, "category": "MANAGER_CHECK_IN", "priority": "MEDIUM", "title": "Career conversation",
        "assigned_to_user_id": user_id(db, hr_emp)}), 201)
    assert (created["status"], created["assigned_to_user_id"]) == ("OPEN", user_id(db, hr_emp))
    iid, version = created["id"], created["version"]

    started = ok(client.post(f"/api/interventions/{iid}/start", headers=hr, json={"expected_version": version}))
    assert started["status"] == "IN_PROGRESS"
    ok(client.post(f"/api/interventions/{iid}/notes", headers=hr, json={"note": "Agreed a follow-up in two weeks."}), 201)
    current = ok(client.get(f"/api/interventions/{iid}", headers=hr))  # the page reloads after a note
    stale = client.post(f"/api/interventions/{iid}/complete", headers=hr, json={"expected_version": started["version"],
                                                                                "outcome": "Done and dusted"})
    assert stale.status_code == 409  # the dialog showed an older version: reported, not overwritten
    done = ok(client.post(f"/api/interventions/{iid}/complete", headers=hr, json={"expected_version": current["version"],
                                                                                  "outcome": "Follow-up held; workload adjusted."}))
    assert done["status"] == "COMPLETED" and done["outcome"].startswith("Follow-up held")
    history = ok(client.get(f"/api/interventions/{iid}/history", headers=hr, params={"page_size": 50}))
    assert [e["event_type"] for e in history["items"]] == ["CREATED", "STARTED", "NOTE_ADDED", "COMPLETED"]

    second = ok(client.post("/api/interventions", headers=hr, json={
        "employee_id": target.id, "category": "RECOGNITION", "priority": "LOW", "title": "Recognition", "due_date":
        (date.today() + timedelta(days=7)).isoformat()}), 201)
    cancelled = ok(client.post(f"/api/interventions/{second['id']}/cancel", headers=hr,
                               json={"expected_version": second["version"], "reason": "Already covered elsewhere"}))
    assert cancelled["status"] == "CANCELLED"


def test_manager_creates_and_takes_ownership_only_in_own_department(client, db, staff) -> None:
    manager, manager_h = staff(MANAGER, department="Engineering")
    own, _ = staff(EMPLOYEE, department="Engineering")
    other, _ = staff(EMPLOYEE, department="Sales")
    hr_emp, hr = staff(HR, department="People")
    created = ok(client.post("/api/interventions", headers=manager_h, json={
        "employee_id": own.id, "category": "WORKLOAD_REVIEW", "priority": "HIGH", "title": "Workload check",
        "assigned_to_user_id": user_id(db, manager)}), 201)
    assert created["assigned_to_user_id"] == user_id(db, manager)
    refused = client.post("/api/interventions", headers=manager_h, json={
        "employee_id": other.id, "category": "WORKLOAD_REVIEW", "priority": "HIGH", "title": "Cross department"})
    assert refused.status_code == 404
    # "Assign to me" by HR on the manager's intervention.
    hr_owned = ok(client.post(f"/api/interventions/{created['id']}/assign", headers=hr,
                              json={"expected_version": created["version"], "user_id": user_id(db, hr_emp)}))
    assert hr_owned["assigned_to_user_id"] == user_id(db, hr_emp)
    assert client.post("/api/interventions", headers=staff(EMPLOYEE, department="Engineering")[1], json={
        "employee_id": own.id, "category": "RECOGNITION", "priority": "LOW", "title": "Not allowed"}).status_code == 403


def test_plan_intervention_from_a_risk_alert(client, db, staff, make_alert) -> None:
    hr_emp, hr = staff(HR, department="People")
    target, _ = staff(EMPLOYEE, department="Engineering")
    alert = make_alert(target)
    # Risk.jsx -> /interventions?alert=<id>&employee=<employee id> -> InterventionForm with risk_alert_id.
    body = {"employee_id": target.id, "category": "MANAGER_CHECK_IN", "priority": "MEDIUM", "title": "Check-in after alert",
            "risk_alert_id": alert.id, "assigned_to_user_id": user_id(db, hr_emp)}
    created = ok(client.post("/api/interventions", headers=hr, json=body), 201)
    assert created["risk_alert_id"] == alert.id
    duplicate = client.post("/api/interventions", headers=hr, json=body)
    assert duplicate.status_code == 409  # one active intervention per alert and category; the form shows the message
    assert client.get(f"/api/risk-monitoring/alerts/{alert.id}", headers=hr).json()["status"] == "OPEN"  # unchanged


# --- Payroll.jsx -----------------------------------------------------------------------------------------------------------


def test_payroll_period_calculation_approval_and_payment(client, db, staff, make_component, make_profile) -> None:
    hr_emp, hr = staff(HR, department="People")
    worker, worker_h = staff(EMPLOYEE, department="Engineering")
    allowance = make_component("ALLOWANCE", SalaryComponentKind.EARNING)
    make_profile(worker, "40000.00", components=((allowance, "1000.00"),))
    make_profile(hr_emp, "50000.00")
    period = ok(client.post("/api/payroll/periods", headers=hr, json={"year": 2026, "month": 3}), 201)  # NewPeriod
    result = ok(client.post(f"/api/payroll/periods/{period['id']}/calculate", headers=hr, json={}))  # Calculate
    assert (result["created"], result["skipped"]) == (2, [])
    records = ok(client.get(f"/api/payroll/periods/{period['id']}/records", headers=hr, params={"page": 1, "page_size": 20}))
    by_employee = {r["employee"]["id"]: r for r in records["items"]}
    own_record, worker_record = by_employee[hr_emp.id]["id"], by_employee[worker.id]["id"]

    detail = ok(client.get(f"/api/payroll/records/{worker_record}", headers=hr))  # RecordDetail
    assert detail["status"] == "CALCULATED" and detail["earnings"]
    assert client.post(f"/api/payroll/records/{own_record}/approve", headers=hr, json={}).status_code == 403  # never self
    ok(client.post(f"/api/payroll/records/{worker_record}/approve", headers=hr, json={}))
    assert ok(client.get("/api/payroll/me", headers=worker_h))["total"] == 1  # approved: visible as a payslip
    paid = ok(client.post(f"/api/payroll/records/{worker_record}/mark-paid", headers=hr, json={"payment_reference": "RUN-2026-03"}))
    assert (paid["status"], paid["payment_reference"]) == ("PAID", "RUN-2026-03")
    recalculated = ok(client.post(f"/api/payroll/periods/{period['id']}/calculate", headers=hr, json={}))
    assert any(s["employee_id"] == worker.id and "paid" in s["reason"] for s in recalculated["skipped"])
    counts = ok(client.get("/api/payroll/periods", headers=hr, params={"page_size": 24}))["items"][0]["record_counts"]
    # Every status is listed, zeros included (the page shows only the non-zero ones).
    assert {k: n for k, n in counts.items() if n} == {"CALCULATED": 1, "PAID": 1}


# --- Performance.jsx -------------------------------------------------------------------------------------------------------


def _dates() -> dict:
    t = date.today()
    return {"period_start": (t - timedelta(days=60)).isoformat(), "period_end": (t + timedelta(days=30)).isoformat(),
            "self_assessment_deadline": (t + timedelta(days=10)).isoformat(),
            "manager_review_deadline": (t + timedelta(days=20)).isoformat()}


def test_review_cycle_review_evaluation_and_finalization(client, db, staff) -> None:
    hr_emp, hr = staff(HR, department="People")
    manager, manager_h = staff(MANAGER, department="Engineering")
    employee, employee_h = staff(EMPLOYEE, department="Engineering")
    # CycleForm (description left empty is dropped), then CycleDetail "Open cycle".
    cycle = ok(client.post("/api/performance/cycles", headers=hr, json={"name": "FY2026 review", **_dates()}), 201)
    cycle = ok(client.post(f"/api/performance/cycles/{cycle['id']}/transition", headers=hr,
                           json={"action": "open", "expected_version": cycle["version"]}))
    assert cycle["status"] == "OPEN"
    # ReviewForm as the department manager with reviewer "Me".
    review = ok(client.post("/api/performance/reviews", headers=manager_h, json={
        "cycle_id": cycle["id"], "employee_id": employee.id, "reviewer_user_id": user_id(db, manager)}), 201)
    rid = review["id"]
    ok(client.post(f"/api/performance/reviews/{rid}/submit-self-assessment", headers=employee_h,
                   json={"self_assessment": "Delivered the migration project.", "expected_version": review["version"]}))
    # Evaluation: the page always sends every evaluation field (empty lists / nulls included) and the version.
    current = ok(client.get(f"/api/performance/reviews/{rid}", headers=manager_h))
    body = {"manager_summary": "Strong delivery.", "manager_private_notes": "Discuss promotion next cycle.", "overall_rating": 4,
            "strengths": ["Ownership", "Mentoring"], "improvement_areas": [], "development_goals": ["Lead a design review"],
            "training_recommendations": [], "expected_version": current["version"]}
    saved = ok(client.patch(f"/api/performance/reviews/{rid}", headers=manager_h, json=body))
    submitted = ok(client.post(f"/api/performance/reviews/{rid}/submit-manager-review", headers=manager_h,
                               json={**body, "expected_version": saved["version"]}))
    assert submitted["status"] == "MANAGER_SUBMITTED"
    hidden = ok(client.get(f"/api/performance/reviews/{rid}", headers=employee_h))
    assert hidden["overall_rating"] is None and hidden["manager_private_notes"] is None
    # The reviewer is not HR: AdminActions are not shown to them, and the API agrees.
    assert client.post(f"/api/performance/reviews/{rid}/finalize", headers=manager_h,
                       json={"expected_version": submitted["version"]}).status_code == 403
    finalized = ok(client.post(f"/api/performance/reviews/{rid}/finalize", headers=hr, json={"expected_version": submitted["version"]}))
    assert finalized["status"] == "FINALIZED"
    shared = ok(client.get(f"/api/performance/reviews/{rid}", headers=employee_h))
    assert shared["overall_rating"] == 4 and shared["manager_private_notes"] is None  # private notes stay private
    reopened = ok(client.post(f"/api/performance/reviews/{rid}/reopen", headers=hr,
                              json={"expected_version": finalized["version"], "reason": "Rating entered for the wrong goal."}))
    assert reopened["status"] == "SELF_SUBMITTED"
    cancelled = ok(client.post(f"/api/performance/reviews/{rid}/cancel", headers=hr,
                               json={"expected_version": reopened["version"], "reason": "Employee changed role."}))
    assert cancelled["status"] == "CANCELLED"
    cycle = ok(client.post(f"/api/performance/cycles/{cycle['id']}/transition", headers=hr,
                           json={"action": "start_reviews", "expected_version": cycle["version"]}))
    cycle = ok(client.post(f"/api/performance/cycles/{cycle['id']}/transition", headers=hr,
                           json={"action": "complete", "expected_version": cycle["version"]}))
    assert cycle["status"] == "COMPLETED"
    audit = " ".join(str(r.details) for r in db.scalars(select(AuditLog)))
    assert "Discuss promotion" not in audit and "Strong delivery" not in audit


def test_review_form_refuses_ineligible_reviewers_and_non_staff(client, db, staff) -> None:
    hr_emp, hr = staff(HR, department="People")
    employee, employee_h = staff(EMPLOYEE, department="Engineering")
    colleague, _ = staff(EMPLOYEE, department="Engineering")
    cycle = ok(client.post("/api/performance/cycles", headers=hr, json={"name": "H2 review", **_dates()}), 201)
    ok(client.post(f"/api/performance/cycles/{cycle['id']}/transition", headers=hr, json={"action": "open"}))
    # "Someone else" picked from the employee list: a plain employee is not an eligible reviewer.
    refused = client.post("/api/performance/reviews", headers=hr, json={
        "cycle_id": cycle["id"], "employee_id": employee.id, "reviewer_user_id": user_id(db, colleague)})
    assert refused.status_code == 422
    assert client.post("/api/performance/reviews", headers=employee_h, json={
        "cycle_id": cycle["id"], "employee_id": colleague.id, "reviewer_user_id": user_id(db, hr_emp)}).status_code == 404
    assert client.post(f"/api/performance/cycles/{cycle['id']}/transition", headers=hr,
                       json={"action": "cancel"}).status_code == 422  # CycleDetail requires a reason for cancel


# --- Datasets.jsx ----------------------------------------------------------------------------------------------------------


def test_dataset_upload_validate_and_model_status(client, headers_for, ml_dirs) -> None:
    admin, hr = headers_for(ADMIN), headers_for(HR)
    status = ok(client.get("/api/ai/attrition/model-status", headers=hr))  # ModelStatus card
    assert status["available"] is False and status["active_model"] is None and status["reason"]
    # UploadDialog sends multipart FormData with file + display_name.
    created = ok(client.post("/api/datasets", headers=admin, data={"display_name": "Synthetic workforce"},
                             files={"file": ("workforce.csv", csv_bytes(), "text/csv")}), 201)
    assert created["latest_version"]["status"] == "UPLOADED"
    report = ok(client.post(f"/api/datasets/{created['id']}/validate", headers=admin, params={"version": 1}))
    assert (report["status"], report["training_eligible"]) == ("VALIDATED", True)
    assert {"rows_total", "rows_valid", "rows_invalid", "fatal_errors", "warnings"} <= set(report["report"])
    version = ok(client.post(f"/api/datasets/{created['id']}/versions", headers=admin,
                             files={"file": ("workforce.csv", csv_bytes(n=150, seed=1), "text/csv")}), 201)
    assert version["version_number"] == 2
    # HR sees the page read-only; the upload and validate controls are ADMIN-only, as in the API.
    assert client.post("/api/datasets", headers=hr, data={"display_name": "Not allowed"},
                       files={"file": ("x.csv", csv_bytes(), "text/csv")}).status_code == 403
    assert client.post(f"/api/datasets/{created['id']}/validate", headers=hr, params={"version": 2}).status_code == 403
    assert status["active_model"] is None  # nothing above trained or activated a model


# --- Activity.jsx ----------------------------------------------------------------------------------------------------------


def test_activity_status_and_cursor_recovery_contract(client, staff, headers_for, monkeypatch) -> None:
    from app.core.config import get_settings

    hr = headers_for(HR)
    manager, manager_h = staff(MANAGER, department="Engineering")
    status = ok(client.get("/api/workforce-activity/status", headers=hr, params={"department_id": manager.department_id}))
    assert {"snapshot", "activity", "risk_alerts", "polling"} <= set(status)
    assert status["polling"]["recommended_interval_seconds"] > 0
    assert ok(client.get("/api/workforce-activity/status", headers=manager_h))["scope"]["department_id"] == manager.department_id

    cursor = ok(client.get("/api/workforce-activity/feed", headers=hr))["updates_cursor"]
    first = ok(client.get("/api/workforce-activity/updates", headers=hr, params={"cursor": cursor}))
    assert {"items", "next_cursor", "has_more", "settled_until", "recommended_poll_seconds"} <= set(first)
    # A cursor used with other filters (or by another user) is refused with 422; the page reloads the feed.
    assert client.get("/api/workforce-activity/updates", headers=hr, params={"cursor": cursor, "category": "leave"}).status_code == 422
    assert client.get("/api/workforce-activity/updates", headers=manager_h, params={"cursor": cursor}).status_code == 422
    # An expired cursor is 410; the page reloads the feed for a fresh one.
    monkeypatch.setenv("ACTIVITY_UPDATES_CURSOR_MAX_AGE_HOURS", "1")
    get_settings.cache_clear()
    from app.api import workforce_activity
    from app.models.base import utcnow

    later = utcnow() + timedelta(hours=2)
    monkeypatch.setattr(workforce_activity, "utcnow", lambda: later)
    assert client.get("/api/workforce-activity/updates", headers=hr, params={"cursor": first["next_cursor"]}).status_code == 410
