"""Incremental activity updates (cursor polling) and monitoring status over SYNTHETIC records."""

import base64
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    AlertPriority,
    AlertStatus,
    Attendance,
    AttendanceStatus,
    AttritionPrediction,
    AuditLog,
    LeaveRequest,
    LeaveRequestStatus,
    Notification,
    RiskAlert,
    RiskAlertEscalation,
    WorkforceSnapshot,
)
from app.models.base import utcnow
from app.services import activity_cursor
from app.services import activity_feed as feed
from tests.test_config import make_settings
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

BASE = "/api/workforce-activity"


@pytest.fixture(autouse=True)
def _no_settle_delay(monkeypatch):
    monkeypatch.setenv("ACTIVITY_UPDATES_SETTLE_SECONDS", "0")
    get_settings.cache_clear()


def poll(client, headers, **params) -> dict:
    response = client.get(f"{BASE}/updates", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def drain(client, headers, cursor=None, **params) -> tuple[list[dict], str, int]:
    """Follow next_cursor until has_more is false. Returns (items, final cursor, number of calls)."""
    items, calls = [], 0
    while True:
        body = poll(client, headers, **({"cursor": cursor} if cursor else {}), **params)
        items += body["items"]
        cursor, calls = body["next_cursor"], calls + 1
        if not body["has_more"]:
            return items, cursor, calls
        assert calls < 200


# --- Cursor encoding --------------------------------------------------------------------------------


SETTINGS = make_settings()
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
KEY = (NOW - timedelta(hours=1), 3, 42, 1)


def test_cursor_round_trip_and_binding() -> None:
    cursor = activity_cursor.encode(KEY, 7, "f" * 16, SETTINGS)
    assert activity_cursor.decode(cursor, 7, "f" * 16, NOW, SETTINGS) == KEY
    with pytest.raises(activity_cursor.CursorError, match="different user"):
        activity_cursor.decode(cursor, 8, "f" * 16, NOW, SETTINGS)
    with pytest.raises(activity_cursor.CursorError, match="filter"):
        activity_cursor.decode(cursor, 7, "e" * 16, NOW, SETTINGS)
    with pytest.raises(activity_cursor.CursorExpired):
        activity_cursor.decode(cursor, 7, "f" * 16, NOW + timedelta(hours=72), SETTINGS)
    other_secret = make_settings(jwt_secret_key="another-strong-secret-0123456789-ABCDEFGHIJ")
    with pytest.raises(activity_cursor.CursorError):  # secret rotation invalidates cursors
        activity_cursor.decode(cursor, 7, "f" * 16, NOW, other_secret)


def _forge(payload: dict, signature: bytes = b"0" * 16) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


@pytest.mark.parametrize("cursor", [
    "", "abc", "a.b.c", "!!!.???", "x" * 600,
    _forge({"v": 1, "t": 0, "s": 0, "i": 0, "a": 0, "u": 7, "f": "f" * 16}),  # valid shape, wrong signature
])
def test_malformed_or_forged_cursors_are_rejected(cursor) -> None:
    with pytest.raises(activity_cursor.CursorError):
        activity_cursor.decode(cursor, 7, "f" * 16, NOW, SETTINGS)


def test_tampered_payload_or_signature_is_rejected() -> None:
    cursor = activity_cursor.encode(KEY, 7, "f" * 16, SETTINGS)
    body, signature = cursor.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["i"] = 41  # move the position
    edited = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=").decode()
    for bad in (f"{edited}.{signature}", f"{body}.{signature[:-2]}AA", f"{body[:-1]}{'A' if body[-1] != 'A' else 'B'}.{signature}"):
        with pytest.raises(activity_cursor.CursorError):
            activity_cursor.decode(bad, 7, "f" * 16, NOW, SETTINGS)


def test_correctly_signed_but_ill_typed_payload_is_rejected() -> None:
    import hashlib
    import hmac

    for payload in ({"v": 1, "t": "soon", "s": 0, "i": 0, "a": 0, "u": 7, "f": "f" * 16},
                    {"v": 2, "t": 0, "s": 0, "i": 0, "a": 0, "u": 7, "f": "f" * 16},
                    {"v": 1, "t": 10 ** 30, "s": 0, "i": 0, "a": 0, "u": 7, "f": "f" * 16},
                    {"v": 1, "s": 0, "i": 0, "a": 0, "u": 7, "f": "f" * 16}):
        raw = json.dumps(payload).encode()
        signature = hmac.new(activity_cursor._signing_key(SETTINGS), raw, hashlib.sha256).digest()[:16]
        with pytest.raises(activity_cursor.CursorError):
            activity_cursor.decode(_forge(payload, signature), 7, "f" * 16, NOW, SETTINGS)


# --- Cursor validation over the API ----------------------------------------------------------------------


def test_bad_cursors_return_clear_errors(client, org) -> None:
    for cursor in ("not-a-cursor", "eyJ2IjoxfQ.AAAA", "x" * 513):
        response = client.get(f"{BASE}/updates", headers=org["hr"], params={"cursor": cursor})
        assert response.status_code == 422 and cursor not in response.text
    hr_cursor = poll(client, org["hr"])["next_cursor"]
    admin = client.get(f"{BASE}/updates", headers=org["admin"], params={"cursor": hr_cursor})
    assert admin.status_code == 422 and "different user" in admin.text
    other_filter = client.get(f"{BASE}/updates", headers=org["hr"], params={"cursor": hr_cursor, "category": "leave"})
    assert other_filter.status_code == 422
    other_scope = client.get(f"{BASE}/updates", headers=org["hr"],
                             params={"cursor": hr_cursor, "department_id": org["eng"].department_id})
    assert other_scope.status_code == 422


def test_expired_cursor_returns_410(client, org, monkeypatch) -> None:
    cursor = poll(client, org["hr"])["next_cursor"]
    from app.api import workforce_activity as api

    monkeypatch.setattr(api, "utcnow", lambda: utcnow() + timedelta(hours=73))
    response = client.get(f"{BASE}/updates", headers=org["hr"], params={"cursor": cursor})
    assert response.status_code == 410 and "reload" in response.text


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"category": "payroll"}, {"kind": "X"},
                                    {"priority": "URGENT"}, {"page": 1}, {"since": "now"}, {"department_id": 0},
                                    {"date_from": "2026-10-10", "date_to": "2026-10-01"}])
def test_invalid_update_parameters(client, org, params) -> None:
    assert client.get(f"{BASE}/updates", headers=org["hr"], params=params).status_code == 422


def test_page_size_is_capped_by_setting(client, org, add, monkeypatch) -> None:
    monkeypatch.setenv("ACTIVITY_UPDATES_MAX_PAGE_SIZE", "3")
    get_settings.cache_clear()
    for hours in range(1, 8):
        add.attendance(org["eng"], created_at=ago(hours))
    body = poll(client, org["hr"], category="attendance", limit=50)
    assert body["page_size"] == 3 and body["count"] == 3 and body["has_more"]


# --- Ordering, paging, new events ---------------------------------------------------------------------------


def test_drained_updates_match_the_feed_in_order_without_duplicates(client, org, add) -> None:
    same = ago(5)
    for _ in range(7):
        add.attendance(org["eng"], created_at=same)
    add.leave(org["eng"], created_at=same, status=LeaveRequestStatus.APPROVED, reviewed_at=same)
    add.alert(org["eng2"], created_at=same, acknowledged_at=same, status=AlertStatus.ACKNOWLEDGED)
    add.intervention(org["eng2"], created_at=same)
    add.leave(org["sales"], created_at=ago(3), status=LeaveRequestStatus.CANCELLED, cancelled_at=ago(2))

    items, _, calls = drain(client, org["hr"], limit=4)
    ids = [i["event_id"] for i in items]
    assert len(ids) == len(set(ids)) and calls > 3
    feed_ids = {i["event_id"] for i in client.get(f"{BASE}/feed", headers=org["hr"], params={"page_size": 100}).json()["items"]}
    assert set(ids) == feed_ids  # same items as the feed (default windows cover the same records here)
    times = [i["recorded_at"] for i in items]
    assert times == sorted(times)  # oldest first
    tied = [i for i in items if i["recorded_at"] == items[0]["recorded_at"] or i["recorded_at"] == same.isoformat()]
    order = list(feed.SOURCES)  # source ranks follow the catalogue order
    ranks = [order.index(i["category"]) for i in tied]
    assert ranks == sorted(ranks)  # ties ordered by source, then record id, then action
    attendance = [i["source_id"] for i in tied if i["category"] == "attendance"]
    assert attendance == sorted(attendance)  # numeric, not lexical
    assert drain(client, org["hr"], limit=4)[0] == items  # repeatable


def test_new_events_between_polls_are_returned_once(client, org, add) -> None:
    add.attendance(org["eng"], created_at=ago(2))
    _, cursor, _ = drain(client, org["hr"], category="attendance")
    assert poll(client, org["hr"], cursor=cursor, category="attendance")["items"] == []
    new = add.attendance(org["eng2"], created_at=utcnow())  # stored after the previous poll, as in production
    body = poll(client, org["hr"], cursor=cursor, category="attendance")
    assert [i["event_id"] for i in body["items"]] == [f"attendance:{new.id}:checked_in"] and not body["has_more"]
    assert poll(client, org["hr"], cursor=body["next_cursor"], category="attendance")["items"] == []


def test_record_stored_late_behind_the_cursor_is_not_returned(client, org, add) -> None:
    """Documented limitation: a record whose recorded_at is earlier than an already-issued cursor (a transaction
    that committed later than the settle delay) is not returned by /updates; /feed still lists it."""
    _, cursor, _ = drain(client, org["hr"], category="attendance")
    late = add.attendance(org["eng"], created_at=ago(1))
    assert poll(client, org["hr"], cursor=cursor, category="attendance")["items"] == []
    listed = client.get(f"{BASE}/feed", headers=org["hr"], params={"category": "attendance"}).json()["items"]
    assert [i["source_id"] for i in listed] == [late.id]


def test_settle_delay_holds_back_the_newest_records(client, org, add, monkeypatch) -> None:
    monkeypatch.setenv("ACTIVITY_UPDATES_SETTLE_SECONDS", "60")
    get_settings.cache_clear()
    old = add.attendance(org["eng"], created_at=ago(1))
    recent = add.attendance(org["eng2"], created_at=utcnow() - timedelta(seconds=10))
    body = poll(client, org["hr"], category="attendance")
    assert [i["source_id"] for i in body["items"]] == [old.id]
    assert datetime.fromisoformat(body["settled_until"]) < recent.created_at


def test_feed_cursor_continues_from_the_feed(client, org, add) -> None:
    add.attendance(org["eng"], created_at=ago(3))
    feed_body = client.get(f"{BASE}/feed", headers=org["hr"], params={"category": "attendance"}).json()
    assert feed_body["total"] == 1
    assert poll(client, org["hr"], cursor=feed_body["updates_cursor"], category="attendance")["items"] == []
    new = add.attendance(org["eng2"], created_at=utcnow())
    items = poll(client, org["hr"], cursor=feed_body["updates_cursor"], category="attendance")["items"]
    assert [i["source_id"] for i in items] == [new.id]
    mismatch = client.get(f"{BASE}/updates", headers=org["hr"], params={"cursor": feed_body["updates_cursor"]})
    assert mismatch.status_code == 422  # issued for category=attendance only


def test_rows_dropped_after_fetch_do_not_stall_or_skip(client, org, db) -> None:
    for minutes in range(10, 4, -1):  # six incomplete status audits, then one complete one
        db.add(AuditLog(actor_user_id=None, action="employee.status_changed", entity_type="employee",
                        entity_id=str(org["eng"].id), details={"changes": {}}, created_at=ago(minutes / 60)))
    db.add(AuditLog(actor_user_id=None, action="employee.status_changed", entity_type="employee",
                    entity_id=str(org["eng"].id), details={"changes": {"status": {"from": "ACTIVE", "to": "ON_LEAVE"}}},
                    created_at=ago(1 / 60)))
    db.commit()
    items, _, calls = drain(client, org["hr"], category="employee_status", limit=2)
    assert [i["description"] for i in items] == ["Employment status changed from ACTIVE to ON_LEAVE"]
    assert calls >= 3  # pages with only dropped rows still advance the cursor


def test_date_filters_bound_updates(client, org, add) -> None:
    yesterday = today() - timedelta(days=1)
    tz = get_settings().org_tz
    inside = add.attendance(org["eng"], created_at=datetime.combine(yesterday, datetime.min.time(), tzinfo=tz) + timedelta(hours=9))
    add.attendance(org["eng2"], created_at=ago(0.01))
    items = poll(client, org["hr"], category="attendance", date_from=yesterday.isoformat(),
                 date_to=yesterday.isoformat())["items"]
    assert [i["source_id"] for i in items] == [inside.id]
    too_long = client.get(f"{BASE}/updates", headers=org["hr"],
                          params={"date_from": (today() - timedelta(days=40)).isoformat()})
    assert too_long.status_code == 422


# --- Scope and privacy -------------------------------------------------------------------------------------


def test_updates_respect_scope_and_self_exclusion(client, org, add) -> None:
    add.attendance(org["eng"], created_at=ago(2))
    add.attendance(org["sales"], created_at=ago(2))
    add.alert(org["manager"], created_at=ago(2))
    add.intervention(org["manager"], created_at=ago(2))
    manager_items, _, _ = drain(client, org["manager_h"])
    assert {i["department_id"] for i in manager_items} == {org["eng"].department_id}
    assert not {i["category"] for i in manager_items} & feed.SELF_EXCLUDED
    hr_items, _, _ = drain(client, org["hr"])
    assert {"risk_alert", "intervention"} <= {i["category"] for i in hr_items}
    sales = org["sales"].department_id
    assert client.get(f"{BASE}/updates", headers=org["manager_h"], params={"department_id": sales}).status_code == 404
    assert client.get(f"{BASE}/updates", headers=org["eng_h"]).status_code == 403
    assert client.get(f"{BASE}/status", headers=org["eng_h"]).status_code == 403
    assert client.get(f"{BASE}/updates").status_code == 401 and client.get(f"{BASE}/status").status_code == 401
    assert client.get(f"{BASE}/status", headers=org["manager_h"], params={"department_id": sales}).status_code == 404


def test_no_sensitive_values_in_updates_or_status(client, org, add) -> None:
    add.leave(org["eng"], created_at=ago(3), status=LeaveRequestStatus.REJECTED, reviewed_at=ago(2))
    add.alert(org["eng2"], created_at=ago(3), status=AlertStatus.RESOLVED, resolved_at=ago(2))
    add.intervention(org["eng2"], created_at=ago(3))
    bodies = [client.get(f"{BASE}/updates", headers=h, params={"limit": 100}).text for h in (org["hr"], org["manager_h"])]
    bodies += [client.get(f"{BASE}/status", headers=h).text for h in (org["hr"], org["manager_h"])]
    for body in bodies:
        for word in (SECRET_REASON, SECRET_NOTE, SECRET_REVIEW, SENTINEL_SCORE, "987", "654.5", "Edith", "Ezra", "Mona",
                     "Sam Seller", "@corp.example.com", "EMP0", "risk_score", "features", "salary"):
            assert word not in body, word


# --- Read-only, bounded, no models ------------------------------------------------------------------------------


def test_polling_and_status_never_write(client, org, add, db) -> None:
    add.attendance(org["eng"], created_at=ago(2))
    add.alert(org["eng2"], created_at=ago(30))
    tables = (AuditLog, Notification, RiskAlert, RiskAlertEscalation, Attendance, LeaveRequest, WorkforceSnapshot)

    def counts():
        db.expire_all()
        return [db.scalar(select(func.count()).select_from(t)) for t in tables]

    before = counts()
    for headers in (org["hr"], org["manager_h"], org["admin"]):
        _, cursor, _ = drain(client, headers)
        poll(client, headers, cursor=cursor)
        assert client.get(f"{BASE}/status", headers=headers).status_code == 200
    assert counts() == before


def test_query_count_is_bounded(client, org, add, count_queries) -> None:
    def measure(path, **params) -> int:
        start = len(count_queries())
        assert client.get(f"{BASE}{path}", headers=org["hr"], params=params).status_code == 200
        return len(count_queries()) - start

    add.attendance(org["eng"], created_at=ago(2))
    small = (measure("/updates", limit=5), measure("/status"))
    for n in range(12):
        add.attendance(org["eng2"], created_at=ago(1 + n / 100))
        add.leave(org["sales"], created_at=ago(1), status=LeaveRequestStatus.APPROVED, reviewed_at=ago(0.5))
        add.alert(org["sales"], created_at=ago(2))
        add.intervention(org["eng"], created_at=ago(2))
    assert (measure("/updates", limit=5), measure("/status")) == small


def test_no_model_code_runs(client, org, add, db, monkeypatch) -> None:
    from app.services import attrition

    def forbidden(*args, **kwargs):
        raise AssertionError("no model code during polling")

    for name in ("predict", "train_model", "load_active", "extract_features", "activate_model"):
        monkeypatch.setattr(attrition, name, forbidden)
    add.alert(org["eng2"], created_at=ago(2))
    before = db.scalar(select(func.count(AttritionPrediction.id)))
    drain(client, org["hr"])
    client.get(f"{BASE}/status", headers=org["hr"])
    assert db.scalar(select(func.count(AttritionPrediction.id))) == before


# --- Monitoring status ----------------------------------------------------------------------------------------------


def test_status_with_no_data(client, org) -> None:
    body = client.get(f"{BASE}/status", headers=org["hr"]).json()
    assert body["snapshot"]["status"] == "insufficient_data" and body["snapshot"]["latest_snapshot_date"] is None
    assert body["activity"]["status"] == "available"  # the employee records just created
    assert body["risk_alerts"]["active_total"] == 0
    assert body["completeness"]["feed_truncation_possible"] is False
    assert "not real-time" in body["note"] and body["polling"]["recommended_interval_seconds"] == 20
    assert datetime.fromisoformat(body["server_time"]).tzinfo is not None


def test_status_activity_insufficient_when_nothing_recent(client, org, db) -> None:
    from app.models import Employee

    for employee in db.scalars(select(Employee)):
        employee.created_at = ago(24 * 60)
    db.commit()
    assert client.get(f"{BASE}/status", headers=org["hr"]).json()["activity"] == {
        "status": "insufficient_data", "latest_recorded_at": None, "lookback_days": 31}


def test_status_snapshot_freshness(client, org, add) -> None:
    eng = org["eng"].department_id
    add.snapshot(today() - timedelta(days=60), {eng: 3})
    body = client.get(f"{BASE}/status", headers=org["hr"]).json()["snapshot"]
    assert (body["status"], body["age_days"]) == ("stale", 60)
    add.snapshot(today() - timedelta(days=2), {org["sales"].department_id: 1})
    assert client.get(f"{BASE}/status", headers=org["hr"]).json()["snapshot"]["status"] == "available"
    manager = client.get(f"{BASE}/status", headers=org["manager_h"]).json()["snapshot"]
    assert manager["status"] == "stale" and manager["latest_snapshot_date"] == (today() - timedelta(days=60)).isoformat()


def test_status_alert_counts_use_sla_and_scope(client, org, add) -> None:
    add.alert(org["eng"], created_at=ago(30), priority=AlertPriority.HIGH)  # overdue
    add.alert(org["eng2"], created_at=ago(60), priority=AlertPriority.MEDIUM)  # approaching (54h+)
    add.alert(org["sales"], created_at=ago(1), priority=AlertPriority.LOW)  # on track
    add.alert(org["manager"], created_at=ago(500), status=AlertStatus.ACKNOWLEDGED, acknowledged_at=ago(1))
    add.alert(org["sales"], created_at=ago(5), status=AlertStatus.RESOLVED, resolved_at=ago(1))  # not active
    hr = client.get(f"{BASE}/status", headers=org["hr"]).json()["risk_alerts"]
    assert hr["active_total"] == 4 and hr["by_priority"] == {"LOW": 1, "MEDIUM": 1, "HIGH": 2}
    assert hr["by_sla_state"] == {"ON_TRACK": 1, "APPROACHING": 1, "OVERDUE": 1, "ACKNOWLEDGED": 1}
    manager = client.get(f"{BASE}/status", headers=org["manager_h"]).json()["risk_alerts"]
    assert manager["by_sla_state"] == {"ON_TRACK": 0, "APPROACHING": 1, "OVERDUE": 1, "ACKNOWLEDGED": 0}  # own alert hidden


def test_status_reports_possible_truncation(client, org, db, monkeypatch) -> None:
    monkeypatch.setenv("ACTIVITY_FEED_MAX_ITEMS", "100")
    get_settings.cache_clear()
    created = ago(2)
    db.add_all([Attendance(employee_id=org["eng"].id, work_date=date(2021, 1, 1) + timedelta(days=n), check_in=created,
                           status=AttendanceStatus.PRESENT, created_at=created) for n in range(100)])
    db.commit()
    completeness = client.get(f"{BASE}/status", headers=org["hr"]).json()["completeness"]
    assert completeness["feed_truncation_possible"] and completeness["categories_at_or_over_cap"] == ["attendance"]
    assert client.get(f"{BASE}/feed", headers=org["hr"]).json()["truncated"] is True
    manager = client.get(f"{BASE}/status", headers=org["manager_h"]).json()["completeness"]
    assert manager["categories_at_or_over_cap"] == ["attendance"]
    sales = client.get(f"{BASE}/status", headers=org["hr"], params={"department_id": org["sales"].department_id}).json()
    assert sales["completeness"]["feed_truncation_possible"] is False
    items, _, _ = drain(client, org["hr"], category="attendance", limit=100)  # updates page through everything
    assert len(items) == 100


def test_escalations_flow_through_updates_once(client, org, add) -> None:
    add.alert(org["eng"], created_at=ago(30))
    _, cursor, _ = drain(client, org["hr"], category="risk_escalation")
    assert client.post("/api/risk-monitoring/escalations/evaluate", json={}, headers=org["hr"]).status_code == 200
    client.post("/api/risk-monitoring/escalations/evaluate", json={}, headers=org["admin"])
    body = poll(client, org["hr"], cursor=cursor, category="risk_escalation")
    assert [i["action"] for i in body["items"]] == ["overdue"]
    everything, _, _ = drain(client, org["hr"])
    assert all(i["source_type"] != "audit_logs" or i["category"] == "employee_status" for i in everything)


def test_frontier_prevents_skipping_when_rows_are_dropped(client, org, add, db) -> None:
    """limit=2: the status unit fetches 3 rows (2 incomplete, dropped), so its 4th row is unknown. An attendance
    item recorded later must not be returned ahead of it, or the cursor would jump past that row."""
    def status_audit(minutes_ago, complete):
        changes = {"status": {"from": "ACTIVE", "to": "ON_LEAVE"}} if complete else {}
        db.add(AuditLog(actor_user_id=None, action="employee.status_changed", entity_type="employee",
                        entity_id=str(org["eng"].id), details={"changes": changes}, created_at=ago(minutes_ago / 60)))

    status_audit(50, False)
    status_audit(40, False)
    status_audit(30, True)
    status_audit(20, True)
    db.commit()
    later = add.attendance(org["eng"], created_at=ago(10 / 60))
    items, _, _ = drain(client, org["hr"], category=["employee_status", "attendance"], limit=2)
    assert [i["category"] for i in items] == ["employee_status", "employee_status", "attendance"]
    assert items[-1]["source_id"] == later.id
