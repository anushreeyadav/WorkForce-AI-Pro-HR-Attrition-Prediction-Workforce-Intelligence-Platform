"""Report and export API over SYNTHETIC records (in-memory SQLite; never a real database)."""

import csv
import io
import re
from datetime import timedelta
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    AlertStatus,
    Attendance,
    AttendanceStatus,
    AuditLog,
    Employee,
    LeaveRequestStatus,
    Notification,
    PayrollRecord,
    RiskAlert,
    UserRole,
)
from tests.test_report_export import assert_valid_pdf, pdf_streams
from tests.test_workforce_activity_api import (  # noqa: F401  (pytest fixtures)
    SECRET_NOTE,
    SECRET_REASON,
    SECRET_REVIEW,
    SENTINEL_SCORE,
    add,
    ago,
    org,
    today,
)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/reports"
REPORTS = ("employees", "attendance", "leave", "payroll", "attrition", "workforce", "activity")
FILENAME = re.compile(r'^attachment; filename="[A-Za-z0-9._-]+\.(csv|xlsx|pdf)"$')


def params_for(report: str, period_id: int = 1, **extra) -> dict:
    return {"period_id": period_id, **extra} if report == "payroll" else extra


def get_csv(client, report, headers, **params) -> list[list[str]]:
    response = client.get(f"{BASE}/{report}", headers=headers, params={"format": "csv", **params})
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


@pytest.fixture
def payroll_period(client, org, make_profile):
    for employee in (org["eng"], org["eng2"], org["sales"]):
        make_profile(employee, base_salary="91234.00")
    period = client.post("/api/payroll/periods", json={"year": 2026, "month": 3}, headers=org["hr"]).json()
    assert client.post(f"/api/payroll/periods/{period['id']}/calculate", json={}, headers=org["hr"]).status_code == 200
    return period


# --- Catalog and access ---------------------------------------------------------------------------------


def test_catalog_lists_only_permitted_reports(client, org) -> None:
    assert client.get(f"{BASE}/catalog").status_code == 401
    ids = {who: [r["id"] for r in client.get(f"{BASE}/catalog", headers=org[who]).json()["reports"]]
           for who in ("admin", "hr", "manager_h", "eng_h")}
    assert ids["admin"] == ids["hr"] == list(REPORTS)
    assert ids["manager_h"] == [r for r in REPORTS if r != "payroll"]
    assert ids["eng_h"] == []
    body = client.get(f"{BASE}/catalog", headers=org["manager_h"]).text
    for word in ("Edith", "Engineering", "Sales", "EMP0"):
        assert word not in body  # no employee or department data


@pytest.mark.parametrize("report", REPORTS)
def test_report_permissions(client, org, report, payroll_period) -> None:
    params = params_for(report, period_id=payroll_period["id"])
    assert client.get(f"{BASE}/{report}", params=params).status_code == 401
    assert client.get(f"{BASE}/{report}", params=params, headers=org["eng_h"]).status_code == 403
    for who in ("admin", "hr"):
        assert client.get(f"{BASE}/{report}", params=params, headers=org[who]).status_code == 200
    manager = client.get(f"{BASE}/{report}", params=params, headers=org["manager_h"])
    assert manager.status_code == (403 if report == "payroll" else 200)
    if report != "payroll":
        other = {**params, "department_id": org["sales"].department_id}
        assert client.get(f"{BASE}/{report}", params=other, headers=org["manager_h"]).status_code == 404
    assert client.get(f"{BASE}/{report}", params={**params, "department_id": 999}, headers=org["hr"]).status_code == 404


@pytest.mark.parametrize("report", REPORTS)
@pytest.mark.parametrize("fmt, media", [("csv", "text/csv; charset=utf-8"),
                                        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                                        ("pdf", "application/pdf")])
def test_formats_headers_and_filenames(client, org, report, fmt, media, payroll_period) -> None:
    response = client.get(f"{BASE}/{report}", headers=org["hr"],
                          params=params_for(report, period_id=payroll_period["id"], format=fmt))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == media
    assert FILENAME.match(response.headers["content-disposition"]) and report in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    if fmt == "xlsx":
        assert ZipFile(io.BytesIO(response.content)).testzip() is None
    if fmt == "pdf":
        assert assert_valid_pdf(response.content) >= 1


@pytest.mark.parametrize("report, params", [
    ("employees", {"format": "docx"}), ("employees", {"status": "RETIRED"}), ("employees", {"unknown": "1"}),
    ("employees", {"department_id": 0}), ("attendance", {"date_from": "2026-05-02", "date_to": "2026-05-01"}),
    ("attendance", {"date_from": "2020-01-01", "date_to": "2026-01-01"}), ("attendance", {"date_from": "not-a-date"}),
    ("leave", {"status": "MAYBE"}), ("payroll", {}), ("payroll", {"period_id": "x"}),
    ("activity", {"category": "payroll"}), ("activity", {"date_from": "2026-01-01", "date_to": "2026-03-01"}),
    ("workforce", {"date_from": "2015-01-01", "date_to": "2026-01-01"}),
])
def test_invalid_filters(client, org, report, params) -> None:
    response = client.get(f"{BASE}/{report}", headers=org["hr"], params=params)
    assert response.status_code == 422
    for value in params.values():
        if len(str(value)) > 3:
            assert str(value) not in response.text  # input is not echoed


def test_payroll_period_must_exist(client, org) -> None:
    assert client.get(f"{BASE}/payroll", headers=org["hr"], params={"period_id": 999}).status_code == 404


# --- Content, scope and privacy -------------------------------------------------------------------------------


def test_employee_report_columns_scope_and_injection(client, org, db) -> None:
    org["eng2"].full_name = '=HYPERLINK("http://evil.example","x")'
    org["eng2"].phone = "+15550001"
    db.commit()
    rows = get_csv(client, "employees", org["hr"])
    assert rows[0] == ["Employee code", "Full name", "Department", "Designation", "Employment status", "Date of joining"]
    assert len(rows) == 5 and "'=HYPERLINK(\"http://evil.example\",\"x\")" in [r[1] for r in rows]
    manager = get_csv(client, "employees", org["manager_h"])
    assert {r[2] for r in manager[1:]} == {"Engineering"} and len(manager) == 4
    xlsx = client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "xlsx"}).content
    sheet = ZipFile(io.BytesIO(xlsx)).read("xl/worksheets/sheet1.xml").decode()
    assert "'=HYPERLINK" in sheet and "<f>" not in sheet
    for fmt in ("csv", "xlsx", "pdf"):
        raw = client.get(f"{BASE}/employees", headers=org["hr"], params={"format": fmt}).content
        text = raw.decode("utf-8", "ignore") + "".join(pdf_streams(raw) if fmt == "pdf" else [])
        assert "@corp.example.com" not in text and "+15550001" not in text and "password" not in text.lower()


def test_attendance_report_matches_the_attendance_api(client, org, add, db) -> None:
    for n, employee in enumerate((org["eng"], org["eng2"], org["sales"], org["eng"], org["sales"])):
        record = add.attendance(employee, created_at=ago(30), status=AttendanceStatus.LATE if n % 2 else AttendanceStatus.PRESENT)
        record.work_date = today() - timedelta(days=n)
        record.note = SECRET_NOTE
    db.commit()
    start, end = (today() - timedelta(days=5)).isoformat(), today().isoformat()
    for who in ("hr", "manager_h"):
        rows = get_csv(client, "attendance", org[who], date_from=start, date_to=end)
        api = client.get("/api/attendance", headers=org[who], params={"date_from": start, "date_to": end, "page_size": 100}).json()
        expected = [(i["employee"]["employee_code"], i["work_date"], i["status"]) for i in api["items"]]
        assert [(r[0], r[3], r[4]) for r in rows[1:]] == expected and len(expected) == (5 if who == "hr" else 3)
        assert SECRET_NOTE not in str(rows)
    empty = get_csv(client, "attendance", org["hr"], date_from="2019-01-01", date_to="2019-01-31")
    assert len(empty) == 1  # headers only


def test_attendance_dates_default_and_bounds(client, org, monkeypatch) -> None:
    monkeypatch.setenv("REPORT_MAX_RANGE_DAYS", "10")
    get_settings.cache_clear()
    assert client.get(f"{BASE}/attendance", headers=org["hr"], params={"date_from": (today() - timedelta(days=9)).isoformat()}).status_code == 200
    long = client.get(f"{BASE}/attendance", headers=org["hr"], params={"date_from": (today() - timedelta(days=10)).isoformat()})
    assert long.status_code == 422 and "10 days" in long.text


def test_leave_report_matches_api_without_reasons(client, org, add) -> None:
    add.leave(org["eng"], status=LeaveRequestStatus.APPROVED, reviewed_at=ago(1))
    add.leave(org["sales"], status=LeaveRequestStatus.REJECTED, reviewed_at=ago(1))
    start, end = today().isoformat(), (today() + timedelta(days=30)).isoformat()
    for who in ("hr", "manager_h"):
        rows = get_csv(client, "leave", org[who], date_from=start, date_to=end)
        api = client.get("/api/leave/requests", headers=org[who], params={"date_from": start, "date_to": end}).json()
        assert [(r[0], r[7]) for r in rows[1:]] == [(i["employee"]["employee_code"], i["status"]) for i in api["items"]]
    for fmt in ("csv", "xlsx", "pdf"):
        raw = client.get(f"{BASE}/leave", headers=org["hr"], params={"format": fmt, "date_from": start, "date_to": end}).content
        text = raw.decode("utf-8", "ignore") + "".join(pdf_streams(raw) if fmt == "pdf" else [])
        assert SECRET_REASON not in text and SECRET_REVIEW not in text


def test_payroll_report_values_audit_and_privacy(client, org, db, payroll_period) -> None:
    before = db.scalar(select(func.count(AuditLog.id)))
    rows = get_csv(client, "payroll", org["hr"], period_id=payroll_period["id"])
    api = client.get(f"/api/payroll/periods/{payroll_period['id']}/records", headers=org["hr"]).json()["items"]
    assert rows[0] == ["Employee code", "Full name", "Department", "Period", "Status", "Currency", "Gross earnings",
                       "Total deductions", "Net pay"]
    assert [(r[0], r[6], r[8]) for r in rows[1:]] == [
        (i["employee"]["employee_code"], f"{float(i['gross_earnings']):.2f}", f"{float(i['net_pay']):.2f}") for i in api]
    assert {r[3] for r in rows[1:]} == {"2026-03"}
    eng_only = get_csv(client, "payroll", org["hr"], period_id=payroll_period["id"], department_id=org["eng"].department_id)
    assert len(eng_only) == 3
    db.expire_all()
    audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "payroll.report_exported")))
    assert db.scalar(select(func.count(AuditLog.id))) == before + 2 and len(audits) == 2
    assert audits[0].details == {"format": "csv", "rows": 3, "department_id": None, "status": None}
    assert "91234" not in str([a.details for a in audits])  # no salary values in audit metadata


def test_attrition_report_is_aggregate_and_excludes_self(client, org, add, staff, db) -> None:
    hr_employee, hr_employee_h = staff(HR, department="Sales", full_name="Hana Hr")
    add.alert(org["eng"], created_at=ago(2))
    add.alert(hr_employee, created_at=ago(2))
    rows = get_csv(client, "attrition", hr_employee_h)
    totals = {r[0]: r for r in rows[1:]}
    assert set(totals) == {"Engineering", "Sales", "All departments"}
    assert totals["All departments"][5] == "1" and totals["Sales"][5] == "0"  # own HIGH estimate excluded
    assert get_csv(client, "attrition", org["hr"])[-1][5] == "2"
    manager = get_csv(client, "attrition", org["manager_h"])
    assert [r[0] for r in manager[1:]] == ["Engineering"]
    for fmt in ("csv", "xlsx", "pdf"):
        raw = client.get(f"{BASE}/attrition", headers=org["hr"], params={"format": fmt}).content
        text = raw.decode("utf-8", "ignore") + "".join(pdf_streams(raw) if fmt == "pdf" else [])
        for word in (SENTINEL_SCORE, "987", "654.5", "Edith", "Hana", "tenure", "overtime", SECRET_REVIEW):
            assert word not in text, word


def test_workforce_report_matches_trends_api(client, org, add) -> None:
    eng = org["eng"].department_id
    add.snapshot(today().replace(day=1) - timedelta(days=1), {eng: 3, org["sales"].department_id: 1})
    rows = get_csv(client, "workforce", org["manager_h"])
    trends = client.get("/api/workforce-analytics/trends", headers=org["manager_h"],
                        params={"date_from": rows[1][0] + "-01", "date_to": today().isoformat()}).json()["data"]["monthly"]
    assert [(r[0], r[1], r[4]) for r in rows[1:]] == [
        (m["month"], "yes" if m["observed"] else "no", "" if m["active_employees"] is None else str(m["active_employees"]))
        for m in trends]
    assert len(rows) == 13


def test_activity_report_matches_feed_and_hides_self_items(client, org, add) -> None:
    add.attendance(org["eng"], created_at=ago(2))
    add.intervention(org["eng2"], created_at=ago(2))
    add.alert(org["manager"], created_at=ago(2))
    for who in ("hr", "manager_h"):
        rows = get_csv(client, "activity", org[who])
        feed_items = client.get("/api/workforce-activity/feed", headers=org[who], params={"page_size": 100}).json()["items"]
        assert [r[9] for r in rows[1:]] == [i["event_id"] for i in feed_items]
    manager_rows = get_csv(client, "activity", org["manager_h"])
    about_manager = [r for r in manager_rows[1:] if r[7] == str(org["manager"].id)]
    assert {r[2] for r in about_manager} == {"employee_record"}  # own risk items hidden
    assert "risk_alert" in {r[2] for r in get_csv(client, "activity", org["hr"])[1:]}
    raw = client.get(f"{BASE}/activity", headers=org["hr"]).text
    assert SECRET_NOTE not in raw and SENTINEL_SCORE not in raw


# --- Limits and writes ---------------------------------------------------------------------------------


def test_row_limits_refuse_oversized_reports(client, org, make_employee, monkeypatch) -> None:
    monkeypatch.setenv("REPORT_MAX_ROWS", "100")
    monkeypatch.setenv("REPORT_PDF_MAX_ROWS", "10")
    get_settings.cache_clear()
    for _ in range(6):
        make_employee(org["eng"].department)
    assert client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "pdf"}).status_code == 200  # 10 rows
    make_employee(org["eng"].department)  # 11 rows: over the PDF limit
    assert client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "pdf"}).status_code == 422
    assert client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "csv"}).status_code == 200
    for _ in range(91):
        make_employee(org["eng"].department)
    pdf = client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "pdf"})
    assert pdf.status_code == 422 and "10 rows" in pdf.text
    assert client.get(f"{BASE}/employees", headers=org["hr"], params={"format": "csv"}).status_code == 422  # 102 > 100
    assert len(get_csv(client, "employees", org["hr"], department_id=org["sales"].department_id)) == 2


def test_reports_do_not_write(client, org, add, db, payroll_period) -> None:
    add.attendance(org["eng"], created_at=ago(3))
    add.alert(org["eng2"], created_at=ago(3))
    tables = (AuditLog, Notification, RiskAlert, Attendance, Employee, PayrollRecord)

    def counts():
        db.expire_all()
        return [db.scalar(select(func.count()).select_from(t)) for t in tables]

    before = counts()
    for report in REPORTS:
        if report == "payroll":
            continue
        for fmt in ("csv", "xlsx", "pdf"):
            for who in ("hr", "manager_h"):
                assert client.get(f"{BASE}/{report}", headers=org[who], params={"format": fmt}).status_code == 200
    client.get(f"{BASE}/catalog", headers=org["hr"])
    assert counts() == before
    assert db.get(RiskAlert, 1).status == AlertStatus.OPEN


def test_report_audit_failure_rolls_back_payroll_export(client, org, payroll_period, monkeypatch, session_factory) -> None:
    from fastapi.testclient import TestClient

    from app.api import reports as api
    from app.main import app

    def failing(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(api, "record_audit", failing)
    raw = TestClient(app, raise_server_exceptions=False)
    response = raw.get(f"{BASE}/payroll", headers=org["hr"], params={"period_id": payroll_period["id"]})
    assert response.status_code == 500 and b"91234" not in response.content  # no export without its audit row
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count()).where(AuditLog.action == "payroll.report_exported")) == 0


def test_no_model_code_runs(client, org, add, monkeypatch) -> None:
    from app.services import attrition

    def forbidden(*args, **kwargs):
        raise AssertionError("no model code in reports")

    for name in ("predict", "train_model", "load_active", "extract_features", "activate_model"):
        monkeypatch.setattr(attrition, name, forbidden)
    add.alert(org["eng"], created_at=ago(2))
    for report in ("attrition", "workforce", "activity"):
        assert client.get(f"{BASE}/{report}", headers=org["hr"]).status_code == 200
