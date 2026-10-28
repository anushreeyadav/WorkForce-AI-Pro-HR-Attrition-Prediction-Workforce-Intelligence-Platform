"""Disposable local server for browser and load tests (QA tooling, not part of the application).

Runs the UNMODIFIED FastAPI app on 127.0.0.1 with SYNTHETIC data:
- default: a throwaway SQLite file in the system temp folder (deleted and recreated on every start);
- --mysql: the disposable MySQL test database identified by backend/tests/mysql_guard.py (WFAI_MYSQL_TEST_URL and
  WFAI_MYSQL_TEST_MARKER; the schema must already be migrated and the users table empty). Nothing else is accepted.
The real backend/.env and any real database are never used: DB_* settings get placeholders for this process only.

Seeding goes through the application's HTTP API as the role a real user would use. Two kinds of history the API can
only record "now" are inserted directly: past attendance rows and (optionally) a past workforce snapshot series.
The first ADMIN is the documented manual provisioning step (root README).

Options:
  --no-model          do not train or activate a model (the no-active-model state stays visible)
  --employees N       extra synthetic employees (records only, no logins) for load tests (default 0)
  --attendance-days N days of past attendance per employee (default 30)
  --port N            default 8000
  --alert-threshold X risk-alert score threshold for this process (default 70, the production default)
The demo password is random per run and written, with the account e-mails, to a session file in the system temp
folder (never inside the project). Rate limits are raised for this development process only, because seeding and
the harnesses sign in many times from one address.
"""

import argparse
import json
import os
import random
import secrets
import sys
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORK = Path(tempfile.gettempdir()) / "wfai_qa"
SESSION_FILE = WORK / "session.json"

parser = argparse.ArgumentParser()
parser.add_argument("--mysql", action="store_true")
parser.add_argument("--no-model", action="store_true")
parser.add_argument("--employees", type=int, default=0)
parser.add_argument("--attendance-days", type=int, default=30)
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--access-minutes", type=int, default=120)
# Lower than the production default (70) only so the small synthetic population reliably produces risk alerts.
parser.add_argument("--alert-threshold", type=float, default=70)
args = parser.parse_args()

WORK.mkdir(exist_ok=True)
for sub in ("models", "datasets"):
    target = WORK / sub
    if target.exists():
        for f in sorted(target.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
    target.mkdir(exist_ok=True)
DB_FILE = WORK / "qa.sqlite"
if DB_FILE.exists():
    DB_FILE.unlink()

os.environ.update({
    "ENVIRONMENT": "development", "DB_HOST": "127.0.0.1", "DB_PORT": "3306", "DB_NAME": "not-used-qa",
    "DB_USER": "not-used-qa", "DB_PASSWORD": "not-used-qa-placeholder", "JWT_SECRET_KEY": secrets.token_urlsafe(48),
    "ORG_TIMEZONE": "Asia/Kolkata", "ML_ARTIFACT_DIR": str(WORK / "models"), "ML_DATASET_DIR": str(WORK / "datasets"),
    "CORS_ORIGINS": '["http://localhost:4173", "http://127.0.0.1:4173"]',
    "ACCESS_TOKEN_EXPIRE_MINUTES": str(args.access_minutes),
    "AUTH_LOGIN_MAX_ATTEMPTS_PER_CLIENT": "100000", "AUTH_REGISTER_MAX_PER_CLIENT": "100000",
    "AUTH_LOGIN_MAX_FAILURES": "1000", "RISK_ALERT_SCORE_THRESHOLD": str(args.alert_threshold),
})
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Attendance, AttendanceStatus, Base, User, UserRole  # noqa: E402
from synthetic_attrition import synthetic_dataset  # noqa: E402

if args.mysql:
    import mysql_guard  # backend/tests/mysql_guard.py: positive identification or refusal

    try:
        engine = mysql_guard.verified_engine()
    except mysql_guard.NotDisposableError as exc:
        raise SystemExit(f"Refusing to use MySQL: {exc}") from None
    DB_LABEL = "disposable MySQL test database (verified by marker)"
else:
    engine = create_engine(f"sqlite:///{DB_FILE}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    DB_LABEL = f"disposable SQLite file {DB_FILE}"

Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _get_db():
    with Session() as session:
        yield session


app.dependency_overrides[get_db] = _get_db
api = TestClient(app, base_url="http://127.0.0.1")
PASSWORD = "Qa-" + secrets.token_urlsafe(18)
TODAY = date.today()
TZ = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")


def call(method, path, headers=None, expected=(200, 201), **kwargs):
    response = api.request(method, f"/api{path}", headers=headers, **kwargs)
    if response.status_code not in ((expected,) if isinstance(expected, int) else expected):
        raise SystemExit(f"seeding step failed: {method} {path} -> {response.status_code} {response.text[:300]}")
    return response.json() if response.content and "json" in response.headers.get("content-type", "") else response


def login(email):
    token = call("POST", "/auth/login", json={"email": email, "password": PASSWORD})["access_token"]
    return {"Authorization": f"Bearer {token}"}


def register(name, email):
    return call("POST", "/auth/register", json={"full_name": name, "email": email, "password": PASSWORD}, expected=201)


def next_weekday(offset):
    day = TODAY + timedelta(days=offset)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


# --- Accounts (first ADMIN = documented manual step; every other role through the ADMIN API) ---------------------
admin_user = register("Quinn Admin (qa)", "qa.admin@example.com")
with Session() as s:
    s.get(User, admin_user["id"]).role = UserRole.ADMIN
    s.commit()
admin = login("qa.admin@example.com")

PEOPLE = {
    "People Operations": [("Hana HR (qa)", "qa.hr@example.com", "HR", "HR Business Partner")],
    "Engineering": [("Milo Manager (qa)", "qa.eng.manager@example.com", "MANAGER", "Engineering Manager"),
                    ("Ava Patel", "qa.ava@example.com", "EMPLOYEE", "Senior Engineer"),
                    ("Bo Lindqvist", "qa.bo@example.com", "EMPLOYEE", "Software Engineer"),
                    ("Cyrus Obi", "qa.cyrus@example.com", "EMPLOYEE", "QA Engineer"),
                    ("Dana Reyes", "qa.dana@example.com", "EMPLOYEE", "DevOps Engineer")],
    "Sales": [("Sam Sales-Lead (qa)", "qa.sales.manager@example.com", "MANAGER", "Sales Manager"),
              ("Eli Novak", "qa.eli@example.com", "EMPLOYEE", "Account Executive"),
              ("Fern Ito", "qa.fern@example.com", "EMPLOYEE", "Sales Associate")],
}
users, departments, employees = {}, {}, {}
for people in PEOPLE.values():
    for name, email, role, _ in people:
        users[email] = register(name, email)
        if role != "EMPLOYEE":
            call("PATCH", f"/users/{users[email]['id']}/role", admin, json={"role": role})
hr = login("qa.hr@example.com")
for index, (department, people) in enumerate(PEOPLE.items()):
    departments[department] = call("POST", "/departments", admin, json={"name": department}, expected=201)
    for n, (name, email, _, designation) in enumerate(people):
        employees[email] = call("POST", "/employees", admin if department == "People Operations" else hr, json={
            "employee_code": f"QA-{index + 1}{n + 1:02d}", "full_name": name.replace(" (qa)", ""), "work_email": email,
            "department_id": departments[department]["id"], "designation": designation,
            "date_of_joining": (TODAY - timedelta(days=150 + 61 * (index * 5 + n))).isoformat(),
            "user_id": users[email]["id"]}, expected=201)
# Extra synthetic employee records for load tests (no logins), spread over three departments.
extra_departments = [departments["Engineering"]["id"], departments["Sales"]["id"],
                     call("POST", "/departments", admin, json={"name": "Operations"}, expected=201)["id"]]
for n in range(args.employees):
    employees[f"bulk{n}"] = call("POST", "/employees", hr, json={
        "employee_code": f"QB-{n:05d}", "full_name": f"Synthetic Person {n:05d}", "work_email": f"qa.bulk{n}@example.com",
        "department_id": extra_departments[n % 3], "designation": "Associate",
        "date_of_joining": (TODAY - timedelta(days=60 + (n * 37) % 2000)).isoformat()}, expected=201)

shift = call("POST", "/shifts", hr, json={"name": "General", "start_time": "09:00:00", "end_time": "18:00:00",
                                          "break_minutes": 60, "overtime_threshold_minutes": 480}, expected=201)
# Past attendance (direct inserts, synthetic): the API records attendance only "now".
with Session() as s:
    rng = random.Random(7)
    for key, employee in employees.items():
        for back in range(1, args.attendance_days + 1):
            day = TODAY - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            if rng.random() < 0.04:
                s.add(Attendance(employee_id=employee["id"], shift_id=shift["id"], work_date=day, break_minutes=0,
                                 status=AttendanceStatus.ABSENT))
                continue
            late = rng.random() < 0.1
            start = datetime.combine(day, time(9, 25 if late else rng.randint(0, 10)), tzinfo=TZ)
            worked = 480 + max(0, int(rng.gauss(20, 20)))
            s.add(Attendance(employee_id=employee["id"], shift_id=shift["id"], work_date=day,
                             check_in=start.astimezone(UTC), check_out=(start + timedelta(minutes=worked + 60)).astimezone(UTC),
                             break_minutes=60, work_minutes=worked, overtime_minutes=max(0, worked - 480),
                             status=AttendanceStatus.LATE if late else AttendanceStatus.PRESENT))
        s.commit()

# Leave catalogue (HR) so leave workflows can run.
annual = call("POST", "/leave/types", hr, json={"code": "ANNUAL", "name": "Annual Leave", "is_paid": True}, expected=201)
call("POST", "/leave/policies", hr, json={"leave_type_id": annual["id"], "annual_allowance_days": 18,
                                          "effective_from": date(TODAY.year, 1, 1).isoformat()}, expected=201)

# Payroll catalogue and profiles (HR); periods are left to the tests.
hra = call("POST", "/payroll/components", hr, json={"code": "HRA", "name": "House Rent Allowance", "kind": "EARNING",
                                                    "calculation_method": "PERCENT_OF_BASE", "display_order": 10}, expected=201)
for i, (email, employee) in enumerate(employees.items()):
    call("POST", "/payroll/profiles", admin if email == "qa.hr@example.com" else hr, json={
        "employee_id": employee["id"], "base_salary": str(40000 + 2500 * (i % 7)),
        "effective_from": date(TODAY.year, 1, 1).isoformat(),
        "components": [{"component_id": hra["id"], "percentage": "40"}]}, expected=201)

# Performance cycle (open) so reviews can be created.
cycle = call("POST", "/performance/cycles", hr, json={"name": f"QA cycle {TODAY.year}",
             "period_start": (TODAY - timedelta(days=90)).isoformat(), "period_end": (TODAY + timedelta(days=30)).isoformat(),
             "self_assessment_deadline": (TODAY + timedelta(days=10)).isoformat(),
             "manager_review_deadline": (TODAY + timedelta(days=25)).isoformat()}, expected=201)
call("POST", f"/performance/cycles/{cycle['id']}/transition", hr, json={"action": "open", "expected_version": cycle["version"]})

model_version = None
if not args.no_model:
    dataset = call("POST", "/datasets", admin, data={"display_name": "Synthetic attrition set (qa)"},
                   files={"file": ("synthetic.csv", synthetic_dataset(n=400, seed=5).to_csv(index=False).encode(), "text/csv")},
                   expected=201)
    call("POST", f"/datasets/{dataset['id']}/validate", admin)
    model_version = call("POST", "/ai/attrition/train", admin, json={"dataset_id": dataset["id"]}, expected=201)["model"]["model_version"]
    call("POST", f"/ai/attrition/models/{model_version}/activate", admin)

SESSION_FILE.write_text(json.dumps({
    "password": PASSWORD, "base_url": f"http://127.0.0.1:{args.port}", "model_version": model_version,
    "accounts": {"ADMIN": "qa.admin@example.com", "HR": "qa.hr@example.com", "MANAGER": "qa.eng.manager@example.com",
                 "MANAGER_SALES": "qa.sales.manager@example.com", "EMPLOYEE": "qa.ava@example.com",
                 "EMPLOYEE_2": "qa.bo@example.com"},
    "employees": {k: v["id"] for k, v in employees.items() if not k.startswith("bulk")},
    "bulk_employee_ids": [v["id"] for k, v in employees.items() if k.startswith("bulk")],
    "departments": {k: v["id"] for k, v in departments.items()}, "shift_id": shift["id"],
    "leave_type_id": annual["id"], "cycle_id": cycle["id"], "database": "mysql" if args.mysql else "sqlite",
}), encoding="utf-8")
print(f"QA data ready: {len(employees)} employees, model: {model_version or 'none (no active model)'}; {DB_LABEL}.")
print(f"Serving the unmodified app on http://127.0.0.1:{args.port} (Ctrl+C to stop).", flush=True)

import uvicorn  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
