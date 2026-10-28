"""Audit-log sanitization, coverage, scoped query API and compliance export. Synthetic data, in-memory SQLite."""

import csv
import io
import re
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.main import app
from app.models import (
    AuditLog,
    Recommendation,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationScope,
    RecommendationStatus,
    User,
    UserRole,
)
from app.models.base import utcnow
from app.services import audit_catalog, audit_query
from app.services.audit import REDACTED, record_audit, redact
from tests.conftest import TEST_PASSWORD
from tests.test_report_export import assert_valid_pdf
from tests.test_workforce_activity_api import add, ago, org  # noqa: F401  (pytest fixtures)

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE
BASE = "/api/audit-logs"
APP_DIR = Path(__file__).resolve().parents[1] / "app"


def log(db, action, entity_type, entity_id, actor_id=None, details=None, created_at=None) -> AuditLog:
    row = AuditLog(actor_user_id=actor_id, action=action, entity_type=entity_type,
                   entity_id=None if entity_id is None else str(entity_id), details=details)
    row.created_at = created_at or ago(1)
    db.add(row)
    db.commit()
    return row


def listing(client, headers, **params) -> dict:
    response = client.get(BASE, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- Sanitization -------------------------------------------------------------------------------------------


def test_redaction_is_recursive_and_case_insensitive() -> None:
    details = {"changes": {"Phone": {"from": "1", "to": "2"}, "status": {"from": "A", "to": "B"}},
               "items": [{"net_pay": 10, "id": 1}, {"nested": {"Reviewer_Notes": "x", "features": {"a": 1}}}],
               "risk_score": 81.2, "description": "Engineering team"}
    assert redact(details) == {
        "changes": {"Phone": REDACTED, "status": {"from": "A", "to": "B"}},
        "items": [{"net_pay": REDACTED, "id": 1}, {"nested": {"Reviewer_Notes": REDACTED, "features": REDACTED}}],
        "risk_score": REDACTED, "description": "Engineering team"}  # organizational descriptions are kept


@pytest.mark.parametrize("details", [{"password": "x"}, {"changes": {"jwt_token": "x"}}, {"items": [{"API_KEY": "x"}]},
                                     {"deep": [{"deeper": {"authorization": "Bearer x"}}]}])
def test_credential_keys_are_rejected_through_record_audit(db, details) -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        record_audit(db, actor=None, action="auth.login_failed", entity_type="user", entity_id=None, details=details)


def test_record_audit_stores_redacted_values(db) -> None:
    record_audit(db, actor=None, action="employee.updated", entity_type="employee", entity_id=1,
                 details={"changes": {"work_email": {"from": "a@x", "to": "b@x"}, "salary": 5}})
    db.commit()
    stored = db.scalar(select(AuditLog))
    assert stored.details == {"changes": {"work_email": REDACTED, "salary": REDACTED}}


def test_read_side_sanitizes_legacy_rows_and_withholds_free_text() -> None:
    assert audit_query.sanitize_details("attendance.corrected", {"reason": "Hospital visit", "changes": {}}) == {
        "reason": audit_query.WITHHELD, "changes": {}}
    assert audit_query.sanitize_details("ai.attrition.training_rejected", {"reason": "no_rows"}) == {"reason": "no_rows"}
    assert audit_query.sanitize_details("payroll.record.paid", {"net_pay": "100.00"}) == {"net_pay": REDACTED}


# --- Coverage ----------------------------------------------------------------------------------------------------


def test_every_emitted_action_is_catalogued() -> None:
    literal = re.compile(r'action="([a-z_.]+)"')
    constants = re.compile(r'^[A-Z_]+_ACTION = "([a-z_.]+)"', re.M)
    dataset_actions = re.compile(r'"(dataset\.[a-z_]+)"')
    found = set()
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(literal.findall(text)) | set(constants.findall(text)) | set(dataset_actions.findall(text))
    # f-string actions: intervention events, recommendation decisions, alert outcomes
    found |= {f"intervention.{e}" for e in ("updated", "assigned", "started", "completed", "cancelled")}
    found |= {f"recommendation.{a}" for a in ("accepted", "dismissed", "completed")}
    found |= {"risk.alert_resolved", "risk.alert_dismissed"}
    found |= {f"task.{e}" for e in ("updated", "assigned", "started", "blocked", "unblocked", "completed", "cancelled",
                                     "reopened")}
    found |= {f"performance.cycle_{e}" for e in ("opened", "reviews_started", "completed", "cancelled")}
    missing = found - set(audit_catalog.ACTIONS)
    assert not missing, missing
    stale = set(audit_catalog.ACTIONS) - found
    assert not stale, stale  # every catalog entry is actually written somewhere


def test_registration_and_login_are_audited_without_credentials(client, db, make_user) -> None:
    response = client.post("/api/auth/register", json={"full_name": "New Person", "email": "new.person@example.com",
                                                       "password": TEST_PASSWORD})
    assert response.status_code == 201
    new_id = response.json()["id"]
    ok = client.post("/api/auth/login", json={"email": "new.person@example.com", "password": TEST_PASSWORD})
    token = ok.json()["access_token"]
    client.post("/api/auth/login", json={"email": "new.person@example.com", "password": "Wrong-Password-123!"})
    client.post("/api/auth/login", json={"email": "nobody.here@example.com", "password": TEST_PASSWORD})
    make_user(email="inactive.person@example.com", is_active=False)
    client.post("/api/auth/login", json={"email": "inactive.person@example.com", "password": TEST_PASSWORD})
    rows = [(r.action, r.actor_user_id, r.entity_id, r.details) for r in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    inactive_id = db.scalar(select(User.id).where(User.email == "inactive.person@example.com"))
    assert rows == [
        ("auth.registered", new_id, str(new_id), {"role": "EMPLOYEE"}),
        ("auth.login_succeeded", new_id, str(new_id), None),
        ("auth.login_failed", new_id, str(new_id), {"reason": "invalid_password"}),
        ("auth.login_failed", None, None, {"reason": "unknown_account"}),
        ("auth.login_failed", inactive_id, str(inactive_id), {"reason": "inactive_account"}),
    ]
    text = str(rows)
    for secret in (TEST_PASSWORD, "Wrong-Password", token, "nobody.here", "new.person@"):
        assert secret not in text


def test_login_audit_failure_issues_no_token(client, db, make_user, monkeypatch, session_factory) -> None:
    from app.api import auth

    user = make_user(email="audit.fail@example.com")
    monkeypatch.setattr(auth, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    raw = TestClient(app, raise_server_exceptions=False)
    response = raw.post("/api/auth/login", json={"email": "audit.fail@example.com", "password": TEST_PASSWORD})
    assert response.status_code == 500 and "access_token" not in response.text
    with session_factory() as fresh:
        assert fresh.get(User, user.id).last_login_at is None
        assert fresh.scalar(select(func.count(AuditLog.id))) == 0


def test_registration_rolls_back_when_audit_fails(client, monkeypatch, session_factory) -> None:
    from app.api import auth

    monkeypatch.setattr(auth, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.post("/api/auth/register", json={"full_name": "No Audit", "email": "no.audit@example.com",
                                                "password": TEST_PASSWORD}).status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(User.id)).where(User.email == "no.audit@example.com")) == 0


def test_one_audit_row_per_action(client, db, org, add) -> None:
    alert = add.alert(org["eng2"], created_at=ago(30))
    before = db.scalar(select(func.count(AuditLog.id)))
    assert client.post(f"/api/risk-monitoring/alerts/{alert.id}/acknowledge", json={}, headers=org["hr"]).status_code == 200
    client.post(f"/api/risk-monitoring/alerts/{alert.id}/acknowledge", json={}, headers=org["hr"])  # no-op repeat
    db.expire_all()
    actions = list(db.scalars(select(AuditLog.action).where(AuditLog.id > before)))
    assert actions == ["risk.alert_acknowledged"]


# --- Scoped query API ---------------------------------------------------------------------------------------------


@pytest.fixture
def records(db, org, add):
    """One audit row per entity type, in Engineering (manager's department) and in Sales."""
    hr_id = db.scalar(select(User.id).where(User.email == "hr.activity@example.com"))
    eng_dept, sales_dept = org["eng"].department_id, org["sales"].department_id
    eng_att, sales_att = add.attendance(org["eng"]), add.attendance(org["sales"])
    eng_leave, sales_leave = add.leave(org["eng"]), add.leave(org["sales"])
    eng_alert, sales_alert, self_alert = add.alert(org["eng2"]), add.alert(org["sales"]), add.alert(org["manager"])
    eng_int, self_int = add.intervention(org["eng"]), add.intervention(org["manager"])
    dept_rec = Recommendation(rule_id="AGG_X", rule_version="rules-v1", scope=RecommendationScope.DEPARTMENT,
                              category=RecommendationCategory.DATA_QUALITY, priority=RecommendationPriority.LOW,
                              status=RecommendationStatus.AVAILABLE, department_id=eng_dept, title="t",
                              suggested_action="s", explanation="e", evidence={}, evidence_window="2026-10",
                              evaluated_at=utcnow(), dedup_key="k1")
    db.add(dept_rec)
    db.commit()
    rows = {
        "eng_employee": log(db, "employee.status_changed", "employee", org["eng"].id, hr_id,
                            {"changes": {"status": {"from": "ACTIVE", "to": "ON_LEAVE"}}, "reason": "Private matter"}),
        "sales_employee": log(db, "employee.updated", "employee", org["sales"].id, hr_id),
        "eng_department": log(db, "department.updated", "department", eng_dept, hr_id),
        "sales_department": log(db, "department.updated", "department", sales_dept, hr_id),
        "eng_attendance": log(db, "attendance.corrected", "attendance", eng_att.id, hr_id, {"reason": "Badge outage"}),
        "sales_attendance": log(db, "attendance.corrected", "attendance", sales_att.id, hr_id),
        "eng_leave": log(db, "leave.approved", "leave_request", eng_leave.id, org["manager"].user_id),
        "sales_leave": log(db, "leave.approved", "leave_request", sales_leave.id, hr_id),
        "eng_alert": log(db, "risk.alert_acknowledged", "risk_alert", eng_alert.id, org["eng2"].user_id),
        "sales_alert": log(db, "risk.alert_acknowledged", "risk_alert", sales_alert.id, hr_id),
        "self_alert": log(db, "risk.alert_created", "risk_alert", self_alert.id, hr_id),
        "eng_intervention": log(db, "intervention.created", "intervention", eng_int.id, hr_id),
        "self_intervention": log(db, "intervention.created", "intervention", self_int.id, hr_id),
        "dept_recommendation": log(db, "recommendation.accepted", "recommendation", dept_rec.id, hr_id),
        "payroll": log(db, "payroll.record.paid", "payroll_record", 1, hr_id, {"net_pay": "91234.00"}),
        "dataset": log(db, "dataset.downloaded", "dataset", 1, hr_id),
        "auth": log(db, "auth.login_failed", "user", None, None, {"reason": "unknown_account"}),
        "monitoring": log(db, "risk.monitoring_run", "risk_monitoring", "run", hr_id),
    }
    return {name: row.id for name, row in rows.items()}


def ids(body) -> set[int]:
    return {item["id"] for item in body["items"]}


def test_access_by_role(client, org, records) -> None:
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers=org["eng_h"]).status_code == 403
    assert client.get(f"{BASE}/catalog", headers=org["eng_h"]).status_code == 403
    for who in ("hr", "admin"):
        assert set(records.values()) <= ids(listing(client, org[who], page_size=100))


def test_manager_sees_only_department_linked_records(client, org, records) -> None:
    body = listing(client, org["manager_h"], page_size=100)
    expected = {records[k] for k in ("eng_employee", "eng_department", "eng_attendance", "eng_leave", "eng_alert",
                                     "eng_intervention", "dept_recommendation")}
    assert ids(body) == expected and body["total"] == len(expected)
    assert body["scope"] == {"department_id": org["eng"].department_id, "department_linked_only": True}
    assert client.get(BASE, headers=org["manager_h"], params={"department_id": org["sales"].department_id}).status_code == 404
    for name in ("payroll", "dataset", "auth", "monitoring", "self_alert", "self_intervention"):
        filtered = listing(client, org["manager_h"], entity_id=str(records[name]))
        assert records[name] not in ids(filtered)
    hr_eng = listing(client, org["hr"], department_id=org["eng"].department_id, page_size=100)
    assert ids(hr_eng) == expected | {records["self_alert"], records["self_intervention"]}  # HR is not the subject


def test_manager_sees_actors_only_inside_department(client, org, records) -> None:
    items = {i["id"]: i for i in listing(client, org["manager_h"], page_size=100)["items"]}
    assert items[records["eng_leave"]]["actor_user_id"] == org["manager"].user_id  # themselves
    assert items[records["eng_alert"]]["actor_user_id"] == org["eng2"].user_id  # department member
    hidden = items[records["eng_department"]]
    assert hidden["actor_user_id"] is None and hidden["actor_hidden"] is True  # HR user outside the department
    hr_items = {i["id"]: i for i in listing(client, org["hr"], page_size=100)["items"]}
    assert hr_items[records["eng_department"]]["actor_user_id"] is not None


def test_responses_are_sanitized(client, org, records) -> None:
    items = {i["id"]: i for i in listing(client, org["hr"], page_size=100)["items"]}
    assert set(items[records["eng_employee"]]) == {"id", "created_at", "actor_user_id", "actor_hidden", "action",
                                                   "category", "description", "entity_type", "entity_id", "details"}
    assert items[records["eng_employee"]]["details"]["reason"] == audit_query.WITHHELD
    assert items[records["eng_attendance"]]["details"]["reason"] == audit_query.WITHHELD
    assert items[records["payroll"]]["details"] == {"net_pay": REDACTED}
    assert items[records["eng_employee"]]["category"] == "employee"
    text = client.get(BASE, headers=org["hr"], params={"page_size": 100}).text
    for secret in ("Private matter", "Badge outage", "91234", "password", "token"):
        assert secret not in text


def test_filters_and_deterministic_order(client, db, org, records) -> None:
    same = ago(2)
    for n in range(5):
        log(db, "shift.created", "shift", n + 1, None, None, created_at=same)
    newest = listing(client, org["hr"], action="shift.created")["items"]
    oldest = listing(client, org["hr"], action="shift.created", sort="oldest")["items"]
    assert [i["id"] for i in newest] == sorted((i["id"] for i in newest), reverse=True)
    assert [i["id"] for i in oldest] == [i["id"] for i in reversed(newest)]
    pages = [listing(client, org["hr"], category="shift", page=p, page_size=2)["items"] for p in (1, 2, 3)]
    assert [i["id"] for p in pages for i in p] == [i["id"] for i in newest]
    assert {i["action"] for i in listing(client, org["hr"], category="payroll")["items"]} == {"payroll.record.paid"}
    assert ids(listing(client, org["hr"], entity_type="dataset")) == {records["dataset"]}
    assert listing(client, org["hr"], actor_user_id=org["eng2"].user_id)["total"] == 1
    old = log(db, "shift.updated", "shift", 1, created_at=utcnow() - timedelta(days=45))
    assert old.id not in ids(listing(client, org["hr"], page_size=100))  # default 30-day window
    from_date = (utcnow() - timedelta(days=50)).date().isoformat()
    assert old.id in ids(listing(client, org["hr"], date_from=from_date, page_size=100))


@pytest.mark.parametrize("params", [
    {"action": "user.deleted"}, {"category": "secrets"}, {"entity_type": "users_table"}, {"entity_id": "1;DROP"},
    {"date_from": "2026-05-02", "date_to": "2026-05-01"}, {"date_from": "2020-01-01", "date_to": "2026-01-01"},
    {"page_size": 101}, {"page": 0}, {"sort": "random"}, {"actor_user_id": 0}, {"order_by": "id"}, {"department_id": -1},
])
def test_invalid_query_parameters(client, org, params) -> None:
    response = client.get(BASE, headers=org["hr"], params=params)
    assert response.status_code == 422
    for value in params.values():
        if len(str(value)) > 4:
            assert str(value) not in response.text


def test_listing_writes_nothing_and_queries_are_bounded(client, db, org, records, count_queries) -> None:
    before = db.scalar(select(func.count(AuditLog.id)))

    def measure() -> int:
        start = len(count_queries())
        listing(client, org["manager_h"], page_size=100)
        return len(count_queries()) - start

    small = measure()
    for n in range(30):
        log(db, "employee.updated", "employee", org["eng"].id, org["eng2"].user_id)
    db.expire_all()
    assert measure() == small
    assert db.scalar(select(func.count(AuditLog.id))) == before + 30


def test_catalog(client, org) -> None:
    body = client.get(f"{BASE}/catalog", headers=org["manager_h"]).json()
    visible = {a["action"] for a in body["actions"] if a["visible_to_you"]}
    assert "leave.approved" in visible and "payroll.record.paid" not in visible and "auth.login_failed" not in visible
    assert set(body["categories"]) == set(audit_catalog.CATEGORIES)


# --- Export ------------------------------------------------------------------------------------------------


def test_export_permissions_formats_and_headers(client, db, org, records) -> None:
    assert client.get(f"{BASE}/export", headers=org["manager_h"]).status_code == 403
    assert client.get(f"{BASE}/export", headers=org["eng_h"]).status_code == 403
    for fmt, media in (("csv", "text/csv; charset=utf-8"),
                       ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                       ("pdf", "application/pdf")):
        response = client.get(f"{BASE}/export", headers=org["hr"], params={"format": fmt})
        assert response.status_code == 200 and response.headers["content-type"] == media
        assert re.match(r'^attachment; filename="audit-log_organization_[0-9_-]+\.' + fmt + '"$',
                        response.headers["content-disposition"])
        assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
        if fmt == "xlsx":
            sheet = ZipFile(io.BytesIO(response.content)).read("xl/worksheets/sheet1.xml").decode()
            assert "<f>" not in sheet and "Private matter" not in sheet
        if fmt == "pdf":
            assert assert_valid_pdf(response.content) >= 1


def test_export_content_is_sanitized_and_formula_safe(client, db, org, records) -> None:
    log(db, "dataset.downloaded", "dataset", "=HYPERLINK(1)", None, {"version": 1})
    response = client.get(f"{BASE}/export", headers=org["hr"], params={"format": "csv"})
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["Audit ID", "Recorded at", "Actor user ID", "Action", "Category", "Description", "Entity type",
                       "Entity ID", "Details (sanitized)"]
    assert "'=HYPERLINK(1)" in [r[7] for r in rows]
    text = response.content.decode("utf-8-sig")
    for secret in ("Private matter", "Badge outage", "91234"):
        assert secret not in text
    assert int(response.headers["x-report-rows"]) == len(rows) - 1


def test_export_is_audited_once_without_recursion(client, db, org, records) -> None:
    first = client.get(f"{BASE}/export", headers=org["hr"], params={"category": "employee"})
    db.expire_all()
    exports = list(db.scalars(select(AuditLog).where(AuditLog.action == "audit.report_exported")))
    assert len(exports) == 1 and exports[0].actor_user_id is not None
    assert exports[0].details["rows"] == int(first.headers["x-report-rows"]) == 2
    assert exports[0].details["filters"] == {"category": "employee"} and "91234" not in str(exports[0].details)
    second = client.get(f"{BASE}/export", headers=org["hr"], params={"category": "audit"})
    assert int(second.headers["x-report-rows"]) == 1  # the first export, not itself
    db.expire_all()
    assert db.scalar(select(func.count()).where(AuditLog.action == "audit.report_exported")) == 2


def test_export_row_limit_and_audit_failure(client, db, org, records, monkeypatch, session_factory) -> None:
    monkeypatch.setenv("REPORT_MAX_ROWS", "100")
    monkeypatch.setenv("REPORT_PDF_MAX_ROWS", "100")
    get_settings.cache_clear()
    for n in range(100):
        log(db, "shift.updated", "shift", n + 1)
    too_many = client.get(f"{BASE}/export", headers=org["hr"])
    assert too_many.status_code == 422 and "100 rows" in too_many.text
    assert client.get(f"{BASE}/export", headers=org["hr"], params={"category": "shift"}).status_code == 200

    from app.api import audit_logs as api

    monkeypatch.setattr(api, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
    raw = TestClient(app, raise_server_exceptions=False)
    failed = raw.get(f"{BASE}/export", headers=org["hr"], params={"category": "employee"})
    assert failed.status_code == 500 and b"employee.status_changed" not in failed.content
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count()).where(AuditLog.action == "audit.report_exported")) == 1
