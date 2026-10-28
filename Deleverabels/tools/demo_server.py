"""Disposable demo environment for screenshots and sample reports (evidence tooling, not part of the application).

What it does:
- Runs the UNMODIFIED WorkForce AI Pro FastAPI app (backend/app) against a throwaway SQLite file in a temporary folder.
  The real MySQL database is never contacted: settings get placeholder DB values and the session dependency is
  overridden for this process only. Model artifacts and datasets also go to the temporary folder.
- Seeds SYNTHETIC data. Almost everything is created through the application's own HTTP API, as the role a real
  user would use (registration, ADMIN role assignment, HR employee records, leave, payroll, dataset upload and
  validation, model training and activation, predictions, monitoring, interventions, tasks, reviews...).
  Only history that the API cannot create "in the past" is inserted directly and is labelled here:
    * 90 days of past attendance rows (check-in/out times), so the model's attendance features exist;
    * 11 month-end workforce snapshots for past months (today's snapshot is captured through the API).
- The model is trained on the repository's synthetic attrition generator (tests/synthetic_attrition.py). It is a
  DEMO model on generated data: its scores and evaluation numbers are not evidence of real-world performance.
- People, emails (example.com) and figures are fictitious. The demo password is random per run, kept only in this
  process and in a session file in the system temp folder (outside the deliverables), and is never printed.

Usage (from the project root, with the backend virtualenv):
    backend\\.venv\\Scripts\\python.exe Deleverabels\\tools\\demo_server.py
Then serve the frontend build (npm run preview, proxying /api to 127.0.0.1:8000) and run capture_screenshots.py.
"""

import json
import os
import secrets
import sys
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
WORK = Path(tempfile.gettempdir()) / "workforce_demo"
SESSION_FILE = Path(tempfile.gettempdir()) / "workforce_demo_session.json"
WORK.mkdir(exist_ok=True)
(WORK / "models").mkdir(exist_ok=True)
(WORK / "datasets").mkdir(exist_ok=True)
DB_FILE = WORK / "demo.sqlite"
if DB_FILE.exists():
    DB_FILE.unlink()  # always start from an empty disposable database

# Placeholder configuration for this process only (no .env is read for these values: environment variables win).
os.environ.update({
    "ENVIRONMENT": "development", "DB_HOST": "127.0.0.1", "DB_PORT": "3306", "DB_NAME": "not-used-demo",
    "DB_USER": "not-used-demo", "DB_PASSWORD": "not-used-demo-placeholder", "JWT_SECRET_KEY": secrets.token_urlsafe(48),
    "ORG_TIMEZONE": "Asia/Kolkata", "ML_ARTIFACT_DIR": str(WORK / "models"), "ML_DATASET_DIR": str(WORK / "datasets"),
    "CORS_ORIGINS": '["http://localhost:4173", "http://127.0.0.1:4173"]', "ACCESS_TOKEN_EXPIRE_MINUTES": "240",
    # Seeding registers ~16 accounts from one client in seconds; the production defaults would (correctly) refuse that.
    "AUTH_LOGIN_MAX_ATTEMPTS_PER_CLIENT": "1000", "AUTH_REGISTER_MAX_PER_CLIENT": "1000",
})
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Attendance, AttendanceStatus, Base, Department, Employee, WorkforceSnapshot, WorkforceSnapshotDepartment  # noqa: E402
from synthetic_attrition import synthetic_dataset  # noqa: E402

engine = create_engine(f"sqlite:///{DB_FILE}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _get_db():
    with Session() as session:
        yield session


app.dependency_overrides[get_db] = _get_db
api = TestClient(app, base_url="http://127.0.0.1:8000")
PASSWORD = "Demo-" + secrets.token_urlsafe(18)
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


# --- 1. Accounts: the first ADMIN is the documented manual operator step; every other role goes through the API ---
admin_user = register("Priya Admin (demo)", "admin@example.com")
with Session() as s:
    from app.models import User, UserRole
    s.get(User, admin_user["id"]).role = UserRole.ADMIN  # documented first-ADMIN provisioning step (README)
    s.commit()
admin = login("admin@example.com")

PEOPLE = {
    "People Operations": [("Hannah HR (demo)", "hr@example.com", "HR", "HR Business Partner")],
    "Engineering": [("Marcus Manager (demo)", "eng.manager@example.com", "MANAGER", "Engineering Manager"),
                    ("Aisha Khan", "aisha.khan@example.com", "EMPLOYEE", "Senior Engineer"),
                    ("Ben Ortega", "ben.ortega@example.com", "EMPLOYEE", "Software Engineer"),
                    ("Chen Wei", "chen.wei@example.com", "EMPLOYEE", "Software Engineer"),
                    ("Divya Nair", "divya.nair@example.com", "EMPLOYEE", "QA Engineer"),
                    ("Elias Brandt", "elias.brandt@example.com", "EMPLOYEE", "DevOps Engineer"),
                    ("Fatima Zahra", "fatima.zahra@example.com", "EMPLOYEE", "Data Engineer")],
    "Sales": [("Sofia Sales-Lead (demo)", "sales.manager@example.com", "MANAGER", "Sales Manager"),
              ("George Mills", "george.mills@example.com", "EMPLOYEE", "Account Executive"),
              ("Hiro Tanaka", "hiro.tanaka@example.com", "EMPLOYEE", "Account Executive"),
              ("Isla Moreno", "isla.moreno@example.com", "EMPLOYEE", "Sales Associate")],
    "Finance": [("Jonas Weber", "jonas.weber@example.com", "EMPLOYEE", "Financial Analyst"),
                ("Keiko Sato", "keiko.sato@example.com", "EMPLOYEE", "Accountant")],
}
users = {}
for department_people in PEOPLE.values():
    for name, email, role, _ in department_people:
        users[email] = register(name, email)
        if role != "EMPLOYEE":
            call("PATCH", f"/users/{users[email]['id']}/role", admin, json={"role": role})

hr = login("hr@example.com")
departments, employees = {}, {}
for index, (department, people) in enumerate(PEOPLE.items()):
    departments[department] = call("POST", "/departments", admin, json={"name": department,
                                   "description": f"{department} (synthetic demo department)"}, expected=201)
    for n, (name, email, _, designation) in enumerate(people):
        code = f"EMP-{index + 1}{n + 1:02d}"
        employees[email] = call("POST", "/employees", admin if department == "People Operations" else hr, json={
            "employee_code": code, "full_name": name.replace(" (demo)", ""), "work_email": email,
            "department_id": departments[department]["id"], "designation": designation,
            "date_of_joining": (TODAY - timedelta(days=200 + 97 * (index * 7 + n))).isoformat(),
            "user_id": users[email]["id"]}, expected=201)
heads = {e: login(e) for e in ("eng.manager@example.com", "sales.manager@example.com", "aisha.khan@example.com",
                               "ben.ortega@example.com", "george.mills@example.com", "jonas.weber@example.com")}
eng_mgr, sales_mgr = heads["eng.manager@example.com"], heads["sales.manager@example.com"]

# --- 2. Shifts and 90 days of past attendance (direct inserts: the API records attendance only "now") ------------
shift = call("POST", "/shifts", hr, json={"name": "General", "start_time": "09:00:00", "end_time": "18:00:00",
                                          "break_minutes": 60, "overtime_threshold_minutes": 480}, expected=201)
pressure = {"elias.brandt@example.com": (95, 0.45), "isla.moreno@example.com": (80, 0.40),
            "chen.wei@example.com": (60, 0.25), "hiro.tanaka@example.com": (40, 0.20)}  # (extra minutes, late share)
with Session() as s:
    rng = __import__("random").Random(7)
    for email, employee in employees.items():
        overtime, late_share = pressure.get(email, (10, 0.05))
        for back in range(1, 91):
            day = TODAY - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            if rng.random() < 0.04:
                s.add(Attendance(employee_id=employee["id"], shift_id=shift["id"], work_date=day, break_minutes=0,
                                 status=AttendanceStatus.ABSENT, note=None))
                continue
            late = rng.random() < late_share
            start = datetime.combine(day, time(9, 25 if late else rng.randint(0, 10)), tzinfo=TZ)
            worked = 480 + max(0, int(rng.gauss(overtime, 20)))
            end = start + timedelta(minutes=worked + 60)
            s.add(Attendance(employee_id=employee["id"], shift_id=shift["id"], work_date=day,
                             check_in=start.astimezone(UTC), check_out=end.astimezone(UTC), break_minutes=60,
                             work_minutes=worked, overtime_minutes=max(0, worked - 480),
                             status=AttendanceStatus.LATE if late else AttendanceStatus.PRESENT))
    s.commit()
call("POST", "/attendance/check-in", heads["aisha.khan@example.com"], json={"shift_id": shift["id"]}, expected=201)
call("POST", "/attendance/check-in", heads["ben.ortega@example.com"], json={"shift_id": shift["id"]}, expected=201)

# --- 3. Leave: types and policy (HR), requests (employees), decisions (managers) ----------------------------------
annual = call("POST", "/leave/types", hr, json={"code": "ANNUAL", "name": "Annual Leave", "is_paid": True}, expected=201)
sick = call("POST", "/leave/types", hr, json={"code": "SICK", "name": "Sick Leave", "is_paid": True}, expected=201)
for leave_type, allowance in ((annual, 18), (sick, 10)):
    call("POST", "/leave/policies", hr, json={"leave_type_id": leave_type["id"], "annual_allowance_days": allowance,
                                              "effective_from": date(TODAY.year, 1, 1).isoformat()}, expected=201)


def next_weekday(offset):
    day = TODAY + timedelta(days=offset)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


requests_made = []
for email, leave_type, start, end, reason in (
        ("aisha.khan@example.com", annual, 10, 12, "Family visit"), ("ben.ortega@example.com", annual, 20, 21, "Personal errands"),
        ("george.mills@example.com", sick, 3, 3, "Medical appointment"), ("jonas.weber@example.com", annual, 30, 34, "Holiday trip")):
    requests_made.append(call("POST", "/leave/requests", heads[email], json={
        "leave_type_id": leave_type["id"], "start_date": next_weekday(start), "end_date": next_weekday(end), "reason": reason}, expected=201))
call("POST", f"/leave/requests/{requests_made[0]['id']}/approve", eng_mgr, json={"comment": "Enjoy the time off"})
call("POST", f"/leave/requests/{requests_made[2]['id']}/approve", sales_mgr, json={})

# --- 4. Payroll (HR): components, profiles, last month's period calculated, approved and partly paid -------------
hra = call("POST", "/payroll/components", hr, json={"code": "HRA", "name": "House Rent Allowance", "kind": "EARNING",
                                                    "calculation_method": "PERCENT_OF_BASE", "display_order": 10}, expected=201)
pf = call("POST", "/payroll/components", hr, json={"code": "PF", "name": "Provident Fund", "kind": "DEDUCTION",
                                                   "calculation_method": "PERCENT_OF_BASE", "display_order": 20}, expected=201)
tax = call("POST", "/payroll/components", hr, json={"code": "TDS", "name": "Tax withholding", "kind": "TAX",
                                                    "calculation_method": "PERCENT_OF_GROSS", "display_order": 30}, expected=201)
for i, (email, employee) in enumerate(employees.items()):
    call("POST", "/payroll/profiles", admin if email == "hr@example.com" else hr, json={
        "employee_id": employee["id"], "base_salary": str(42000 + 3500 * (i % 6)),
        "effective_from": date(TODAY.year, 1, 1).isoformat(),
        "components": [{"component_id": hra["id"], "percentage": "40"}, {"component_id": pf["id"], "percentage": "12"},
                       {"component_id": tax["id"], "percentage": "10"}]}, expected=201)
last_month = (TODAY.replace(day=1) - timedelta(days=1))
period = call("POST", "/payroll/periods", hr, json={"year": last_month.year, "month": last_month.month}, expected=201)
call("POST", f"/payroll/periods/{period['id']}/calculate", hr, json={})
records = call("GET", f"/payroll/periods/{period['id']}/records", hr, params={"page_size": 100})["items"]
for i, record in enumerate(r for r in records if r["employee"]["id"] != employees["hr@example.com"]["id"]):
    call("POST", f"/payroll/records/{record['id']}/approve", hr, json={})
    if i % 2 == 0:
        call("POST", f"/payroll/records/{record['id']}/mark-paid", hr, json={"payment_reference": f"RUN-{last_month:%Y%m}"})
call("POST", "/payroll/periods", hr, json={"year": TODAY.year, "month": TODAY.month}, expected=201)

# --- 5. Workforce history: 11 past month-end snapshots (direct inserts, synthetic) + today's via the API ---------
with Session() as s:
    names = {d.id: d.name for d in s.scalars(select(Department))}
    base = {departments[d]["id"]: len(p) for d, p in PEOPLE.items()}
    for back in range(11, 0, -1):
        idx = TODAY.year * 12 + TODAY.month - 1 - back
        month_end = date((idx + 1) // 12, (idx + 1) % 12 + 1, 1) - timedelta(days=1)
        growth = {dep: max(1, n - (back // 4 if names[dep] != "Sales" else -(back // 5))) for dep, n in base.items()}
        snapshot = WorkforceSnapshot(snapshot_date=month_end, captured_at=datetime.combine(month_end, time(18), tzinfo=UTC),
                                     period_start=month_end.replace(day=1), total_employees=sum(growth.values()),
                                     active_employees=sum(growth.values()), on_leave_employees=0, inactive_employees=0,
                                     joiners_in_period=0, recorded_departures_in_period=0, source="synthetic_demo_history",
                                     data_quality={"note": "Synthetic demo history inserted directly"})
        s.add(snapshot)
        s.flush()
        for dep, n in growth.items():
            s.add(WorkforceSnapshotDepartment(snapshot_id=snapshot.id, department_id=dep, department_name=names[dep],
                                              total_employees=n, active_employees=n, on_leave_employees=0,
                                              inactive_employees=0, joiners_in_period=0, recorded_departures_in_period=0))
    s.commit()
call("POST", "/workforce-analytics/snapshots/capture", hr, json={}, expected=201)

# --- 6. AI: synthetic dataset upload + validation, demo model training + activation, predictions, monitoring -----
status_before = call("GET", "/ai/attrition/model-status", hr)
dataset = call("POST", "/datasets", admin, data={"display_name": "Synthetic attrition training set (demo)"},
               files={"file": ("synthetic.csv", synthetic_dataset(n=600, seed=11).to_csv(index=False).encode(), "text/csv")},
               expected=201)
call("POST", f"/datasets/{dataset['id']}/validate", admin)
trained = call("POST", "/ai/attrition/train", admin, json={"dataset_id": dataset["id"]}, expected=(200, 201))
call("POST", f"/ai/attrition/models/{trained['model']['model_version']}/activate", admin)
ids = [e["id"] for email, e in employees.items() if email != "hr@example.com"]
call("POST", "/ai/attrition/predict-batch", hr, json={"employee_ids": ids})
monitoring = call("POST", "/risk-monitoring/run", hr, json={})
alerts = call("GET", "/risk-monitoring/alerts", hr, params={"page_size": 100})["items"]
call("POST", "/risk-monitoring/escalations/evaluate", hr, json={})
call("POST", "/recommendations/evaluate", hr, json={"strategy": "rules-v2"})

# --- 7. Retention workflow, tasks, performance ------------------------------------------------------------------
hr_user_id = users["hr@example.com"]["id"]
if alerts:
    first = alerts[0]
    call("POST", f"/risk-monitoring/alerts/{first['id']}/acknowledge", hr, json={"note": "Reviewed in the weekly HR sync"})
    created = call("POST", "/interventions", hr, json={"employee_id": first["employee"]["id"], "risk_alert_id": first["id"],
                   "category": "MANAGER_CHECK_IN", "priority": "HIGH", "title": "Workload and career check-in",
                   "due_date": next_weekday(7), "assigned_to_user_id": hr_user_id}, expected=201)
    call("POST", f"/interventions/{created['id']}/start", hr, json={"expected_version": created["version"]})
    call("POST", f"/interventions/{created['id']}/notes", hr, json={"note": "Agreed to review on-call rota."}, expected=201)
call("POST", "/interventions", eng_mgr, json={"employee_id": employees["chen.wei@example.com"]["id"],
     "category": "CAREER_DEVELOPMENT", "priority": "MEDIUM", "title": "Mentoring plan", "due_date": next_weekday(14)}, expected=201)
for title, assignee, priority, who in (("Prepare onboarding checklist", "fatima.zahra@example.com", "MEDIUM", eng_mgr),
                                       ("Update on-call documentation", "elias.brandt@example.com", "HIGH", eng_mgr),
                                       ("Quarterly pipeline review", "hiro.tanaka@example.com", "HIGH", sales_mgr),
                                       ("Complete compliance training", "jonas.weber@example.com", "LOW", hr)):
    task = call("POST", "/tasks", who, json={"title": title, "priority": priority, "task_type": "FOLLOW_UP",
                "assignee_employee_id": employees[assignee]["id"], "due_date": next_weekday(5)}, expected=201)
call("POST", f"/tasks/{task['id']}/transition", hr, json={"action": "start", "expected_version": task["version"]})
cycle = call("POST", "/performance/cycles", hr, json={"name": f"H2 {TODAY.year} review", "description": "Synthetic demo cycle",
             "period_start": (TODAY - timedelta(days=120)).isoformat(), "period_end": (TODAY + timedelta(days=30)).isoformat(),
             "self_assessment_deadline": (TODAY + timedelta(days=10)).isoformat(),
             "manager_review_deadline": (TODAY + timedelta(days=25)).isoformat()}, expected=201)
call("POST", f"/performance/cycles/{cycle['id']}/transition", hr, json={"action": "open", "expected_version": cycle["version"]})
for email in ("aisha.khan@example.com", "ben.ortega@example.com", "chen.wei@example.com"):
    call("POST", "/performance/reviews", eng_mgr, json={"cycle_id": cycle["id"], "employee_id": employees[email]["id"],
                                                         "reviewer_user_id": users["eng.manager@example.com"]["id"]}, expected=201)
    call("POST", "/performance/goals", eng_mgr, json={"employee_id": employees[email]["id"], "cycle_id": cycle["id"],
         "title": "Deliver the platform migration milestone", "measurable_target": "Milestone accepted by product",
         "start_date": (TODAY - timedelta(days=30)).isoformat(), "target_date": (TODAY + timedelta(days=60)).isoformat(),
         "progress_percent": 40}, expected=201)
review = call("GET", "/performance/reviews", heads["aisha.khan@example.com"], params={"mine": "true"})["items"][0]
call("POST", f"/performance/reviews/{review['id']}/submit-self-assessment", heads["aisha.khan@example.com"],
     json={"self_assessment": "Led the API hardening work and mentored two engineers.", "expected_version": review["version"]})

SESSION_FILE.write_text(json.dumps({"password": PASSWORD, "accounts": {
    "ADMIN": "admin@example.com", "HR": "hr@example.com", "MANAGER": "eng.manager@example.com", "EMPLOYEE": "aisha.khan@example.com"}}))
print(f"Demo data ready: {len(employees)} employees in {len(departments)} departments, {len(alerts)} risk alerts, "
      f"model available before training: {status_before['available']}. Disposable database: {DB_FILE}")
print("Serving the unmodified app on http://127.0.0.1:8000 (Ctrl+C to stop).", flush=True)

if "--seed-only" not in sys.argv:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
