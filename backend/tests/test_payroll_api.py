"""Payroll API. All amounts are illustrative test values. March 2026 has 22 working days (Mon-Fri)."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.main import app
from app.models import (
    Attendance,
    AttendanceStatus,
    AuditLog,
    CalculationMethod as M,
    LeaveRequest,
    LeaveRequestStatus,
    Notification,
    PayrollLineItem,
    PayrollPeriod,
    PayrollRecord,
    PayrollStatus,
    SalaryComponentKind as K,
    UserRole,
)
from tests.conftest import constraint_violation, local

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
D = Decimal


@pytest.fixture
def payroll(client, db, staff, headers_for, make_component, make_profile):
    """One Engineering employee on a 40,000 base with HRA 40%, PF 12% of base, withholding 10% of gross."""
    hra = make_component("HRA", K.EARNING, M.PERCENT_OF_BASE, display_order=10)
    pf = make_component("PF", K.DEDUCTION, M.PERCENT_OF_BASE)
    tax = make_component("WITHHOLDING", K.TAX, M.PERCENT_OF_GROSS)
    employee, employee_headers = staff(department="Engineering")
    profile = make_profile(employee, "40000.00", components=((hra, "40"), (pf, "12"), (tax, "10")))
    hr = headers_for(HR)
    period = client.post("/api/payroll/periods", json={"year": 2026, "month": 3}, headers=hr).json()
    return {"employee": employee, "employee_headers": employee_headers, "profile": profile, "hr": hr,
            "period_id": period["id"], "components": {"hra": hra, "pf": pf, "tax": tax}}


def calculate(client, headers, period_id, **body):
    return client.post(f"/api/payroll/periods/{period_id}/calculate", json=body, headers=headers)


def only_record(db, employee) -> PayrollRecord:
    db.expire_all()
    return db.scalar(select(PayrollRecord).where(PayrollRecord.employee_id == employee.id))


# --- Components --------------------------------------------------------------------------------


def test_create_component_and_audit(client, headers_for, db) -> None:
    response = client.post("/api/payroll/components", headers=headers_for(HR), json={
        "code": "hra", "name": "House Rent Allowance", "kind": "EARNING", "calculation_method": "PERCENT_OF_BASE"})
    assert response.status_code == 201
    assert (response.json()["code"], response.json()["is_active"]) == ("HRA", True)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "payroll.component.created"))
    assert log.details == {"code": "HRA", "kind": "EARNING", "calculation_method": "PERCENT_OF_BASE"}


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "BONUS", "name": "Bonus", "kind": "EARNING", "calculation_method": "PERCENT_OF_GROSS"},
        {"code": "BASIC", "name": "Basic", "kind": "EARNING", "calculation_method": "FIXED"},
        {"code": "1X", "name": "Bad", "kind": "EARNING", "calculation_method": "FIXED"},
        {"code": "LOAN", "name": "Loan", "kind": "LOAN", "calculation_method": "FIXED"},
        {"code": "LOAN", "name": "Loan", "kind": "DEDUCTION", "calculation_method": "FIXED", "amount": 5},
    ],
    ids=["earning-percent-of-gross", "reserved-basic", "bad-code", "bad-kind", "extra-field"],
)
def test_component_validation_422(client, headers_for, payload) -> None:
    assert client.post("/api/payroll/components", json=payload, headers=headers_for(ADMIN)).status_code == 422


def test_component_duplicates_and_update(client, headers_for, make_component) -> None:
    hra = make_component("HRA", name="House Rent")
    headers = headers_for(ADMIN)
    dup_code = client.post("/api/payroll/components", headers=headers, json={
        "code": "HRA", "name": "Other", "kind": "EARNING", "calculation_method": "FIXED"})
    assert dup_code.json() == {"detail": "A component with this code already exists"}
    dup_name = client.post("/api/payroll/components", headers=headers, json={
        "code": "OTHER", "name": "house rent", "kind": "EARNING", "calculation_method": "FIXED"})
    assert dup_name.json() == {"detail": "A component with this name already exists"}

    assert client.patch(f"/api/payroll/components/{hra.id}", json={"code": "RENT"}, headers=headers).status_code == 422
    assert client.patch(f"/api/payroll/components/{hra.id}", json={"kind": "DEDUCTION"}, headers=headers).status_code == 422
    updated = client.patch(f"/api/payroll/components/{hra.id}", json={"is_active": False}, headers=headers).json()
    assert updated["is_active"] is False
    assert client.get("/api/payroll/components", params={"is_active": "false"}, headers=headers).json()["total"] == 1


# --- Profiles ----------------------------------------------------------------------------------


def test_create_profile_and_audit_has_no_amounts(client, db, headers_for, staff, make_component) -> None:
    hra = make_component("HRA", method=M.PERCENT_OF_BASE)
    employee, _ = staff()
    response = client.post("/api/payroll/profiles", headers=headers_for(HR), json={
        "employee_id": employee.id, "base_salary": "52000.50", "effective_from": "2026-01-01",
        "components": [{"component_id": hra.id, "percentage": "40"}]})
    assert response.status_code == 201
    body = response.json()
    assert body["base_salary"] == "52000.50"  # money is serialized as an exact string
    assert body["components"] == [{"component_id": hra.id, "code": "HRA", "name": "Hra", "kind": "EARNING",
                                   "calculation_method": "PERCENT_OF_BASE", "amount": None, "percentage": "40.00"}]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "payroll.profile.created"))
    assert log.details == {"employee_id": employee.id, "effective_from": "2026-01-01", "component_count": 1}
    assert "52000" not in str(log.details)


@pytest.mark.parametrize(
    "overrides, components",
    [
        ({"base_salary": "0"}, []),
        ({"base_salary": "100.555"}, []),
        ({"base_salary": "10000000000.00"}, []),
        ({"effective_from": "2026-05-01", "effective_to": "2026-04-30"}, []),
        ({}, [{"amount": "100", "percentage": "5"}]),
        ({}, [{}]),
        ({}, [{"percentage": "100.5"}]),
        ({}, [{"amount": "-1"}]),
        ({"employee_id": 9999}, []),
        ({"net_pay": "1"}, []),
    ],
    ids=["zero-base", "three-decimals", "too-large", "inverted-dates", "amount-and-percent", "no-value",
         "percent-over-100", "negative-amount", "unknown-employee", "extra-field"],
)
def test_profile_validation_422(client, headers_for, staff, make_component, overrides, components) -> None:
    fixed = make_component("TRANSPORT")
    employee, _ = staff()
    payload = {"employee_id": employee.id, "base_salary": "30000", "effective_from": "2026-01-01",
               "components": [{"component_id": fixed.id, **c} for c in components], **overrides}
    assert client.post("/api/payroll/profiles", json=payload, headers=headers_for(HR)).status_code == 422


def test_profile_component_method_must_match(client, headers_for, staff, make_component) -> None:
    fixed = make_component("TRANSPORT")
    percent = make_component("HRA", method=M.PERCENT_OF_BASE)
    inactive = make_component("OLD", is_active=False)
    employee, _ = staff()
    headers = headers_for(HR)

    def post(component, **value):
        return client.post("/api/payroll/profiles", headers=headers, json={
            "employee_id": employee.id, "base_salary": "30000", "effective_from": "2026-01-01",
            "components": [{"component_id": component.id, **value}]})

    assert post(fixed, percentage="5").json() == {"detail": "TRANSPORT is FIXED: provide amount"}
    assert post(percent, amount="5").json() == {"detail": "HRA is percentage-based: provide percentage"}
    assert post(inactive, amount="5").status_code == 422
    duplicate = client.post("/api/payroll/profiles", headers=headers, json={
        "employee_id": employee.id, "base_salary": "30000", "effective_from": "2026-01-01",
        "components": [{"component_id": fixed.id, "amount": "1"}, {"component_id": fixed.id, "amount": "2"}]})
    assert duplicate.status_code == 422


def test_profile_overlap_and_update(client, db, headers_for, staff, make_profile, make_component) -> None:
    employee, _ = staff()
    profile = make_profile(employee, "30000.00", effective_to=date(2026, 6, 30))
    headers = headers_for(HR)
    body = {"employee_id": employee.id, "base_salary": "35000", "components": []}
    assert client.post("/api/payroll/profiles", json={**body, "effective_from": "2026-06-01"}, headers=headers).status_code == 409
    assert client.post("/api/payroll/profiles", json={**body, "effective_from": "2026-07-01"}, headers=headers).status_code == 201

    bonus = make_component("BONUS")
    response = client.patch(f"/api/payroll/profiles/{profile.id}", headers=headers, json={
        "base_salary": "31000", "components": [{"component_id": bonus.id, "amount": "500"}]})
    assert response.status_code == 200
    assert (response.json()["base_salary"], len(response.json()["components"])) == ("31000.00", 1)
    log = db.scalar(select(AuditLog).where(AuditLog.action == "payroll.profile.updated"))
    assert log.details == {"employee_id": employee.id, "fields": ["base_salary", "components"]}

    extend = client.patch(f"/api/payroll/profiles/{profile.id}", json={"effective_to": None}, headers=headers)
    assert extend.status_code == 409  # would overlap the July profile


def test_hr_cannot_create_or_edit_own_profile(client, staff, make_profile, headers_for) -> None:
    hr_employee, hr_headers = staff(HR)
    response = client.post("/api/payroll/profiles", headers=hr_headers, json={
        "employee_id": hr_employee.id, "base_salary": "99999", "effective_from": "2026-01-01"})
    assert response.status_code == 403
    assert response.json() == {"detail": "You cannot edit your own payroll"}
    profile = make_profile(hr_employee)
    assert client.patch(f"/api/payroll/profiles/{profile.id}", json={"base_salary": "99999"}, headers=hr_headers).status_code == 403
    assert client.patch(f"/api/payroll/profiles/{profile.id}", json={"base_salary": "51000"}, headers=headers_for(ADMIN)).status_code == 200


def test_employee_endpoints_never_expose_salary(client, payroll) -> None:
    body = client.get(f"/api/employees/{payroll['employee'].id}", headers=payroll["hr"]).text
    for word in ("salary", "40000", "net_pay", "payroll"):
        assert word not in body


# --- Periods -----------------------------------------------------------------------------------


def test_period_creation_and_duplicates(client, db, headers_for) -> None:
    headers = headers_for(HR)
    response = client.post("/api/payroll/periods", json={"year": 2028, "month": 2}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert (body["start_date"], body["end_date"]) == ("2028-02-01", "2028-02-29")
    assert body["record_counts"] == {"CALCULATED": 0, "APPROVED": 0, "PAID": 0}
    assert client.post("/api/payroll/periods", json={"year": 2028, "month": 2}, headers=headers).status_code == 409
    for bad in ({"year": 2028, "month": 13}, {"year": 1999, "month": 1}, {"year": 2028}):
        assert client.post("/api/payroll/periods", json=bad, headers=headers).status_code == 422
    assert db.scalar(select(AuditLog).where(AuditLog.action == "payroll.period.created")).details == {"year": 2028, "month": 2}


def test_period_unique_constraint(db) -> None:
    for _ in range(2):
        db.add(PayrollPeriod(year=2026, month=4, start_date=date(2026, 4, 1), end_date=date(2026, 4, 30)))
    with constraint_violation("unique", "UNIQUE", "uq_payroll_periods_year"):
        db.commit()
    db.rollback()


# --- Calculation -------------------------------------------------------------------------------


def test_calculate_creates_itemized_record(client, db, payroll) -> None:
    response = calculate(client, payroll["hr"], payroll["period_id"])
    assert response.status_code == 200
    summary = response.json()
    assert (summary["created"], summary["recalculated"], summary["skipped"]) == (1, 0, [])

    record = client.get(f"/api/payroll/records/{summary['record_ids'][0]}", headers=payroll["hr"]).json()
    assert record["status"] == "CALCULATED" and record["currency"] == "INR"
    assert [(i["code"], i["amount"]) for i in record["earnings"]] == [("BASIC", "40000.00"), ("HRA", "16000.00")]
    assert [(i["code"], i["amount"]) for i in record["deductions"]] == [("PF", "4800.00"), ("WITHHOLDING", "5600.00")]
    assert (record["gross_earnings"], record["total_deductions"], record["net_pay"]) == ("56000.00", "10400.00", "45600.00")
    assert record["warnings"] == []
    withholding = record["deductions"][1]
    assert (withholding["rate"], withholding["basis"]) == ("10.00", "56000.00")

    log = db.scalar(select(AuditLog).where(AuditLog.action == "payroll.period.calculated"))
    assert log.details == {"created": 1, "recalculated": 0, "skipped": 0}  # counts only, no amounts


def test_repeat_calculation_replaces_without_duplicates(client, db, payroll) -> None:
    first = calculate(client, payroll["hr"], payroll["period_id"]).json()
    again = calculate(client, payroll["hr"], payroll["period_id"]).json()
    assert (again["created"], again["recalculated"]) == (0, 1)
    assert again["record_ids"] == first["record_ids"]
    assert db.scalar(select(func.count(PayrollRecord.id))) == 1
    assert db.scalar(select(func.count(PayrollLineItem.id))) == 4  # replaced, not appended
    assert only_record(db, payroll["employee"]).net_pay == D("45600.00")


def test_recalculation_picks_up_profile_changes(client, db, payroll, headers_for) -> None:
    calculate(client, payroll["hr"], payroll["period_id"])
    client.patch(f"/api/payroll/profiles/{payroll['profile'].id}", json={"base_salary": "50000"}, headers=headers_for(ADMIN))
    calculate(client, payroll["hr"], payroll["period_id"])
    assert only_record(db, payroll["employee"]).gross_earnings == D("70000.00")


def test_approved_and_paid_records_are_never_recalculated(client, db, payroll, headers_for) -> None:
    record_id = calculate(client, payroll["hr"], payroll["period_id"]).json()["record_ids"][0]
    client.post(f"/api/payroll/records/{record_id}/approve", headers=payroll["hr"])
    client.patch(f"/api/payroll/profiles/{payroll['profile'].id}", json={"base_salary": "99000"}, headers=headers_for(ADMIN))

    again = calculate(client, payroll["hr"], payroll["period_id"]).json()
    assert again["skipped"] == [{"employee_id": payroll["employee"].id, "reason": "Payroll already approved"}]
    record = only_record(db, payroll["employee"])
    assert (record.status, record.base_salary) == (PayrollStatus.APPROVED, D("40000.00"))


def test_skips_are_explicit(client, payroll, staff, make_profile, make_component) -> None:
    no_profile, _ = staff()
    inactive, _ = staff(status="INACTIVE")
    make_profile(inactive)
    two_profiles, _ = staff()
    make_profile(two_profiles, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15))
    make_profile(two_profiles, effective_from=date(2026, 3, 16))
    loan = make_component("LOAN", K.DEDUCTION)
    negative, _ = staff()
    make_profile(negative, "1000.00", components=((loan, "1000.01"),))

    response = calculate(client, payroll["hr"], payroll["period_id"], employee_ids=[
        payroll["employee"].id, no_profile.id, inactive.id, two_profiles.id, negative.id, 9999]).json()
    reasons = {s["employee_id"]: s["reason"] for s in response["skipped"]}
    assert response["created"] == 1
    assert reasons == {
        9999: "Employee not found",
        no_profile.id: "No active salary profile for this period",
        inactive.id: "Employee is inactive",
        two_profiles.id: "Multiple salary profiles in this period; proration is not supported",
        negative.id: "Deductions exceed gross earnings; net pay would be negative",
    }


def test_calculate_validation(client, payroll) -> None:
    hr = payroll["hr"]
    assert calculate(client, hr, 9999).status_code == 404
    for body in ({"employee_ids": []}, {"employee_ids": [1, 1]}, {"employee_ids": [0]},
                 {"net_pay": "1"}, {"status": "APPROVED"}):
        assert calculate(client, hr, payroll["period_id"], **body).status_code == 422


@pytest.mark.simulated_concurrent_writer
def test_concurrent_approval_during_recalculation_is_rejected(client, db, payroll, monkeypatch, session_factory) -> None:
    """Another transaction approves the record after this calculation loaded it as CALCULATED.

    The conditional UPDATE (WHERE status = 'CALCULATED') matches nothing, so the whole calculation is
    rejected with 409 and the approved record is left exactly as approved.
    """
    from app.services import payroll as payroll_service

    record_id = calculate(client, payroll["hr"], payroll["period_id"]).json()["record_ids"][0]
    original = payroll_service.gather_facts

    def approve_concurrently(*args, **kwargs):
        with session_factory() as other:
            other.get(PayrollRecord, record_id).status = PayrollStatus.APPROVED
            other.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(payroll_service, "gather_facts", approve_concurrently)
    response = calculate(client, payroll["hr"], payroll["period_id"])
    assert response.status_code == 409
    assert response.json() == {"detail": "Payroll changed during calculation; please retry"}
    record = only_record(db, payroll["employee"])
    assert record.status == PayrollStatus.APPROVED and record.net_pay == D("45600.00")
    assert db.scalar(select(func.count(PayrollLineItem.id))) == 4


def test_record_constraints(db, payroll) -> None:
    base = dict(period_id=payroll["period_id"], employee_id=payroll["employee"].id, profile_id=payroll["profile"].id,
                status=PayrollStatus.CALCULATED, currency="INR", base_salary=D("1"), calculated_at=local(2026, 3, 31))
    # Each attempt in its own savepoint, so a failure undoes only that attempt (not the fixture's data).
    with constraint_violation("check", mysql_name="ck_payroll_records_net_is_gross_minus_deductions"):
        with db.begin_nested():  # net != gross - deductions
            db.add(PayrollRecord(**base, gross_earnings=D("100"), total_deductions=D("10"), net_pay=D("80")))
    with constraint_violation("unique", "UNIQUE"):
        with db.begin_nested():
            for _ in range(2):
                db.add(PayrollRecord(**base, gross_earnings=D("100"), total_deductions=D("10"), net_pay=D("90")))


# --- Attendance and leave integration ------------------------------------------------------------


def add_attendance(db, employee, day, status=AttendanceStatus.PRESENT, closed=True):
    db.add(Attendance(employee_id=employee.id, work_date=date(2026, 3, day), status=status,
                      check_in=None if status == AttendanceStatus.ABSENT else local(2026, 3, day, 9),
                      check_out=local(2026, 3, day, 18) if closed and status != AttendanceStatus.ABSENT else None,
                      break_minutes=0))
    db.commit()


def add_leave(db, employee, leave_type, policy, start, end, status=LeaveRequestStatus.APPROVED, days=1):
    db.add(LeaveRequest(employee_id=employee.id, leave_type_id=leave_type.id, policy_id=policy.id,
                        start_date=start, end_date=end, working_days=days, reason="Test leave", status=status))
    db.commit()


def warnings_for(client, payroll) -> tuple[list[str], dict]:
    record_id = calculate(client, payroll["hr"], payroll["period_id"]).json()["record_ids"][0]
    record = client.get(f"/api/payroll/records/{record_id}", headers=payroll["hr"]).json()
    return record["warnings"], {d["code"]: d for d in record["deductions"]}


def test_missing_attendance_is_not_absence(client, payroll) -> None:
    warnings, deductions = warnings_for(client, payroll)  # no attendance rows at all
    assert warnings == [] and set(deductions) == {"PF", "WITHHOLDING"}


def test_recorded_absence_warns_by_default_and_deducts_when_enabled(client, db, payroll, monkeypatch) -> None:
    add_attendance(db, payroll["employee"], 10, AttendanceStatus.ABSENT)
    add_attendance(db, payroll["employee"], 11, AttendanceStatus.ABSENT)
    warnings, deductions = warnings_for(client, payroll)
    assert warnings == ["2 recorded ABSENT day(s) not deducted (rule disabled)"]
    assert "ABSENCE" not in deductions

    monkeypatch.setenv("PAYROLL_DEDUCT_RECORDED_ABSENCE", "true")
    get_settings.cache_clear()
    warnings, deductions = warnings_for(client, payroll)
    assert warnings == []
    absence = deductions["ABSENCE"]
    # 40000 x 2 / 22 = 3636.3636... -> 3636.36
    assert (absence["amount"], absence["quantity"], absence["calculation_method"]) == ("3636.36", "2.00", "PER_WORKING_DAY")


def test_unpaid_leave_only_when_approved_and_enabled(client, db, payroll, monkeypatch, make_leave_type, make_policy) -> None:
    unpaid = make_leave_type("UNPAID", is_paid=False)
    unpaid_policy = make_policy(unpaid)
    paid = make_leave_type("ANNUAL")
    paid_policy = make_policy(paid)
    employee = payroll["employee"]
    add_leave(db, employee, unpaid, unpaid_policy, date(2026, 3, 13), date(2026, 3, 16), days=2)  # Fri-Mon = 2
    add_leave(db, employee, unpaid, unpaid_policy, date(2026, 3, 20), date(2026, 3, 20), status=LeaveRequestStatus.PENDING)
    add_leave(db, employee, paid, paid_policy, date(2026, 3, 23), date(2026, 3, 24), days=2)
    add_leave(db, employee, unpaid, unpaid_policy, date(2026, 3, 30), date(2026, 4, 2), days=4)  # 2 days in March

    warnings, deductions = warnings_for(client, payroll)
    assert warnings == ["4 day(s) of approved unpaid leave not deducted (rule disabled)"]

    monkeypatch.setenv("PAYROLL_DEDUCT_UNPAID_LEAVE", "true")
    get_settings.cache_clear()
    _, deductions = warnings_for(client, payroll)
    assert (deductions["UNPAID_LEAVE"]["quantity"], deductions["UNPAID_LEAVE"]["amount"]) == ("4.00", "7272.73")


def test_data_quality_warnings_and_no_side_effects(client, db, payroll, make_leave_type, make_policy) -> None:
    employee = payroll["employee"]
    employee.date_of_joining = date(2026, 3, 9)
    db.commit()
    add_attendance(db, employee, 10, closed=False)
    annual = make_leave_type("ANNUAL")
    add_leave(db, employee, annual, make_policy(annual), date(2026, 3, 12), date(2026, 3, 12))
    add_attendance(db, employee, 12)
    before = (db.scalar(select(func.count(Attendance.id))), db.scalar(select(func.count(LeaveRequest.id))))

    warnings, _ = warnings_for(client, payroll)
    assert warnings == [
        "Joined on 2026-03-09; no proration applied",
        "1 attendance record(s) without check-out",
        "1 day(s) with attendance recorded during approved leave",
    ]
    db.expire_all()
    assert (db.scalar(select(func.count(Attendance.id))), db.scalar(select(func.count(LeaveRequest.id)))) == before


def test_inactive_component_ignored_with_warning(client, db, payroll) -> None:
    payroll["components"]["hra"].is_active = False
    db.commit()
    warnings, _ = warnings_for(client, payroll)
    assert warnings == ["Inactive component HRA ignored"]
    assert only_record(db, payroll["employee"]).gross_earnings == D("40000.00")


# --- Approval and payment -----------------------------------------------------------------------


def test_status_workflow(client, db, payroll, headers_for) -> None:
    hr = payroll["hr"]
    record_id = calculate(client, hr, payroll["period_id"]).json()["record_ids"][0]

    early = client.post(f"/api/payroll/records/{record_id}/mark-paid", json={}, headers=hr)
    assert early.status_code == 409
    assert early.json() == {"detail": "Only APPROVED payroll can be marked paid (current: CALCULATED)"}

    approved = client.post(f"/api/payroll/records/{record_id}/approve", headers=hr)
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    assert client.post(f"/api/payroll/records/{record_id}/approve", headers=hr).status_code == 409
    note = db.scalar(select(Notification).where(Notification.user_id == payroll["employee"].user_id))
    assert (note.title, note.message) == ("Payslip available", "Your payslip for 2026-03 is available.")

    paid = client.post(f"/api/payroll/records/{record_id}/mark-paid", json={"payment_reference": "RUN-2026-03"},
                       headers=headers_for(ADMIN))
    assert paid.status_code == 200
    assert (paid.json()["status"], paid.json()["payment_reference"]) == ("PAID", "RUN-2026-03")
    assert client.post(f"/api/payroll/records/{record_id}/mark-paid", json={}, headers=hr).status_code == 409
    assert client.post(f"/api/payroll/records/{record_id}/approve", headers=hr).status_code == 409

    actions = [log.action for log in db.scalars(select(AuditLog).where(AuditLog.entity_type == "payroll_record"))]
    assert actions == ["payroll.record.approved", "payroll.record.paid"]
    paid_log = db.scalar(select(AuditLog).where(AuditLog.action == "payroll.record.paid"))
    assert paid_log.details == {"employee_id": payroll["employee"].id, "period_id": payroll["period_id"],
                                "has_payment_reference": True}


@pytest.mark.parametrize("reference", ["123456789012", "1234 5678 9012", "<script>", "x" * 101])
def test_payment_reference_rejects_account_numbers_and_junk(client, payroll, headers_for, reference) -> None:
    record_id = calculate(client, payroll["hr"], payroll["period_id"]).json()["record_ids"][0]
    client.post(f"/api/payroll/records/{record_id}/approve", headers=payroll["hr"])
    response = client.post(f"/api/payroll/records/{record_id}/mark-paid", json={"payment_reference": reference},
                           headers=headers_for(ADMIN))
    assert response.status_code == 422


def test_no_self_approval_or_self_payment(client, staff, make_profile, headers_for) -> None:
    hr_employee, hr_self = staff(HR)
    make_profile(hr_employee)
    admin = headers_for(ADMIN)
    period_id = client.post("/api/payroll/periods", json={"year": 2026, "month": 3}, headers=admin).json()["id"]
    record_id = calculate(client, hr_self, period_id).json()["record_ids"][0]  # calculating is mechanical; allowed

    response = client.post(f"/api/payroll/records/{record_id}/approve", headers=hr_self)
    assert response.status_code == 403
    assert response.json() == {"detail": "You cannot approve your own payroll"}
    assert client.post(f"/api/payroll/records/{record_id}/approve", headers=admin).status_code == 200
    assert client.post(f"/api/payroll/records/{record_id}/mark-paid", json={}, headers=hr_self).status_code == 403


def test_audit_failure_rolls_back_approval(client, payroll, monkeypatch, session_factory) -> None:
    from app.api import payroll as payroll_api

    record_id = calculate(client, payroll["hr"], payroll["period_id"]).json()["record_ids"][0]

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(payroll_api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/payroll/records/{record_id}/approve", headers=payroll["hr"])
    assert response.status_code == 500
    with session_factory() as fresh:
        record = fresh.get(PayrollRecord, record_id)
        assert (record.status, record.approved_by_user_id) == (PayrollStatus.CALCULATED, None)
        assert fresh.scalar(select(Notification)) is None


def test_audit_failure_rolls_back_calculation(client, payroll, monkeypatch, session_factory) -> None:
    from app.api import payroll as payroll_api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(payroll_api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/payroll/periods/{payroll['period_id']}/calculate", json={}, headers=payroll["hr"])
    assert response.status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(PayrollRecord.id))) == 0
        assert fresh.scalar(select(func.count(PayrollLineItem.id))) == 0


# --- Permissions and self-service -----------------------------------------------------------------

ADMIN_ENDPOINTS = [
    ("get", "/api/payroll/components"), ("post", "/api/payroll/components"), ("patch", "/api/payroll/components/1"),
    ("get", "/api/payroll/profiles"), ("post", "/api/payroll/profiles"), ("get", "/api/payroll/profiles/1"),
    ("patch", "/api/payroll/profiles/1"), ("get", "/api/payroll/periods"), ("post", "/api/payroll/periods"),
    ("get", "/api/payroll/periods/1"), ("post", "/api/payroll/periods/1/calculate"),
    ("get", "/api/payroll/periods/1/records"), ("get", "/api/payroll/records/1"),
    ("post", "/api/payroll/records/1/approve"), ("post", "/api/payroll/records/1/mark-paid"),
]


@pytest.mark.parametrize("role", [MANAGER, EMPLOYEE])
def test_manager_and_employee_have_no_payroll_admin_access(client, headers_for, role) -> None:
    headers = headers_for(role)
    for method, path in ADMIN_ENDPOINTS:
        kwargs = {"json": {}} if method in ("post", "patch") else {}
        assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 403, path


def test_admin_endpoints_require_authentication(client) -> None:
    for method, path in ADMIN_ENDPOINTS + [("get", "/api/payroll/me"), ("get", "/api/payroll/me/1/payslip")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_hr_and_admin_can_list(client, payroll, headers_for) -> None:
    calculate(client, payroll["hr"], payroll["period_id"])
    for headers in (payroll["hr"], headers_for(ADMIN)):
        listing = client.get(f"/api/payroll/periods/{payroll['period_id']}/records", headers=headers).json()
        assert listing["total"] == 1 and listing["items"][0]["net_pay"] == "45600.00"
    periods = client.get("/api/payroll/periods", headers=payroll["hr"]).json()
    assert periods["items"][0]["record_counts"] == {"CALCULATED": 1, "APPROVED": 0, "PAID": 0}


def test_employee_sees_only_own_approved_payroll(client, payroll, staff, make_profile) -> None:
    other, other_headers = staff(department="Sales")
    make_profile(other, "25000.00")
    hr, mine = payroll["hr"], payroll["employee_headers"]
    result = calculate(client, hr, payroll["period_id"]).json()
    own_id, other_id = sorted(result["record_ids"])

    assert client.get("/api/payroll/me", headers=mine).json()["total"] == 0  # CALCULATED is not visible yet
    assert client.get(f"/api/payroll/me/{own_id}/payslip", headers=mine).status_code == 404

    for record_id in (own_id, other_id):
        client.post(f"/api/payroll/records/{record_id}/approve", headers=hr)
    listing = client.get("/api/payroll/me", headers=mine).json()
    assert [r["id"] for r in listing["items"]] == [own_id]

    other_slip = client.get(f"/api/payroll/me/{other_id}/payslip", headers=mine)
    assert other_slip.status_code == 404
    assert "25000" not in other_slip.text
    assert client.get(f"/api/payroll/me/{other_id}/payslip", headers=other_headers).status_code == 200


def test_payslip_content(client, payroll) -> None:
    hr = payroll["hr"]
    record_id = calculate(client, hr, payroll["period_id"]).json()["record_ids"][0]
    client.post(f"/api/payroll/records/{record_id}/approve", headers=hr)
    response = client.get(f"/api/payroll/me/{record_id}/payslip", headers=payroll["employee_headers"])
    assert response.status_code == 200
    slip = response.json()
    assert set(slip) == {"record_id", "employee", "period", "currency", "earnings", "deductions", "gross_earnings",
                         "total_deductions", "net_pay", "status", "generated_at", "note"}
    assert slip["employee"] == {"employee_code": payroll["employee"].employee_code,
                                "full_name": payroll["employee"].full_name, "department": "Engineering",
                                "designation": "Engineer"}
    assert (slip["period"]["year"], slip["period"]["month"], slip["status"]) == (2026, 3, "APPROVED")
    assert (slip["gross_earnings"], slip["total_deductions"], slip["net_pay"]) == ("56000.00", "10400.00", "45600.00")
    for forbidden in ("password", "hash", "token", "bank", "account", "warnings", "user_id", "email"):
        assert forbidden not in response.text.lower()


def test_self_service_requires_linked_employee(client, headers_for) -> None:
    headers = headers_for(EMPLOYEE)
    assert client.get("/api/payroll/me", headers=headers).json() == {"detail": "No employee profile is linked to your account"}
    assert client.get("/api/payroll/me/1/payslip", headers=headers).status_code == 404
    assert client.get("/api/payroll/me", params={"employee_id": 1}, headers=headers).status_code == 422


def test_pdf_export_not_offered(client, payroll) -> None:
    # No PDF library is installed; only the structured payslip endpoint exists.
    assert not any("pdf" in path for path in app.openapi()["paths"])
