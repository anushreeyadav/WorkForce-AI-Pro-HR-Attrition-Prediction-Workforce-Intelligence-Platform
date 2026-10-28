"""Browser workflow tests (QA tooling): the production frontend build in headless Microsoft Edge, driven over the
Chrome DevTools Protocol with the `websockets` package already in the backend virtualenv (no new dependency).

Prerequisites (see qa/README.md):
  1. qa/local_server.py --no-model --alert-threshold 5   (unmodified API, disposable database, synthetic data)
  2. in frontend: npm run build && npx vite preview --port 4173 --strictPort   (serves dist/ WITH the CSP header)
Then: backend\\.venv\\Scripts\\python.exe qa\\browser_tests.py --out <dir>

How it acts like a user: clicks are trusted mouse events at the element's position (Input.dispatchMouseEvent) and text is
typed with Input.insertText. Two kinds of control are set through the DOM instead, and are reported as such: <select>
options and date inputs (native value setter + input/change event), and file inputs (DOM.setFileInputFiles).
Each test is PASS or FAIL with its error; API calls made by a test are labelled "API setup" or "API check" and are not
counted as browser evidence. The run also records CSP violations, uncaught page errors and 5xx API responses.
Screenshots contain synthetic data only; password fields are masked and tokens are never written anywhere.
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import httpx
import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP = "http://127.0.0.1:4173"
ROOT = Path(__file__).resolve().parents[1]
SESSION = json.loads((Path(tempfile.gettempdir()) / "wfai_qa" / "session.json").read_text(encoding="utf-8"))
API = httpx.Client(base_url=SESSION["base_url"] + "/api", timeout=120)
DESKTOP = {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False}
PHONE = {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}
CSP_PROBE = ("window.__csp = []; document.addEventListener('securitypolicyviolation', "
             "(e) => window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline')));")


def api_token(role: str, password: str | None = None) -> dict[str, str]:
    response = API.post("/auth/login", json={"email": SESSION["accounts"][role], "password": password or SESSION["password"]})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def weekday(offset: int) -> date:
    day = date.today() + timedelta(days=offset)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class Page:
    def __init__(self, ws, out: Path):
        self.ws, self.out, self.next_id = ws, out, 0
        self.pending: dict[int, asyncio.Future] = {}
        self.page_errors, self.server_errors, self.csp, self.downloads = [], [], [], []
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for raw in self.ws:
            message = json.loads(raw)
            if "id" in message and message["id"] in self.pending:
                self.pending.pop(message["id"]).set_result(message)
                continue
            method, params = message.get("method"), message.get("params", {})
            if method == "Runtime.exceptionThrown":
                self.page_errors.append(params["exceptionDetails"].get("exception", {}).get("description", "error")[:300])
            elif method == "Log.entryAdded" and "Content Security Policy" in params["entry"].get("text", ""):
                self.csp.append(params["entry"]["text"][:300])
            elif method == "Network.responseReceived":
                r = params["response"]
                if "/api/" in r["url"] and r["status"] >= 500:
                    self.server_errors.append(f"{r['status']} {r['url'].split('?')[0]}")
            elif method == "Browser.downloadProgress" and params.get("state") == "completed":
                self.downloads.append(params["guid"])

    async def send(self, method, **params):
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        message = await asyncio.wait_for(future, 60)
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error']}")
        return message.get("result", {})

    async def js(self, expression):
        result = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"page script failed: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    async def wait(self, expression, timeout=12.0, what=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.js(f"(() => {{ try {{ return !!({expression}) }} catch (e) {{ return false }} }})()"):
                return
            await asyncio.sleep(0.15)
        raise AssertionError(f"timed out waiting for {what or expression}")

    async def idle(self):
        await self.wait("document.readyState === 'complete' && !document.querySelector('.animate-pulse, .animate-spin')",
                        what="page to finish loading")
        await asyncio.sleep(0.3)

    async def collect_csp(self):
        found = await self.js("window.__csp || []") or []
        self.csp.extend(found)

    async def goto(self, path):
        await self.collect_csp()
        await self.send("Page.navigate", url=APP + path)
        await asyncio.sleep(0.4)
        await self.idle()

    async def path(self):
        return await self.js("location.pathname")

    async def text(self):
        return await self.js("document.body.innerText")

    async def expect_text(self, needle, timeout=12.0):
        await self.wait(f"document.body.innerText.includes({json.dumps(needle)})", timeout, what=f"text {needle!r}")

    # -- trusted input -----------------------------------------------------------------------------------------
    async def _point(self, finder: str, what: str):
        point = await self.js(f"""(() => {{ const el = ({finder}); if (!el) return null;
            el.scrollIntoView({{block: 'center', inline: 'center'}}); const r = el.getBoundingClientRect();
            return {{x: r.left + r.width / 2, y: r.top + r.height / 2}}; }})()""")
        if not point:
            raise AssertionError(f"not found: {what}")
        return point

    async def tap(self, finder: str, what: str):
        p = await self._point(finder, what)
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", type=kind, x=p["x"], y=p["y"], button="left", clickCount=1)
        await asyncio.sleep(0.35)

    @staticmethod
    def scope_js(scope):
        return {"dialog": "document.querySelector('[role=dialog]')", None: "document"}.get(
            scope, f"[...document.querySelectorAll('section')].find((s) => s.querySelector('h2')?.textContent.trim() === {json.dumps(scope)})")

    async def click(self, text, selector="button, a, [role=tab], [role=button], tr[tabindex], li", scope=None, exact=False):
        match = "t === want" if exact else "t === want || t.includes(want)"
        finder = f"""(() => {{ const root = {self.scope_js(scope)}; if (!root) return null; const want = {json.dumps(text)};
            const items = [...root.querySelectorAll({json.dumps(selector)})].filter((el) => el.getClientRects().length && !el.disabled);
            const norm = (el) => el.textContent.replace(/\\s+/g, ' ').trim();
            return items.find((el) => norm(el) === want) || items.find((el) => {{ const t = norm(el); return {match}; }}); }})()"""
        await self.tap(finder, f"clickable {text!r}")
        await self.idle()

    def control(self, label, scope=None):
        return f"""(() => {{ const root = {self.scope_js(scope)}; const want = {json.dumps(label)};
            const l = [...root.querySelectorAll('label')].find((x) => x.textContent.replace('*', '').trim() === want && x.control);
            return l ? l.control : null; }})()"""

    async def fill(self, label, value, scope=None):
        control = self.control(label, scope)
        kind = await self.js(f"({control})?.type")
        if kind is None:
            raise AssertionError(f"no field labelled {label!r}")
        if kind in ("date", "number", "month"):
            await self._set_value(control, value, "input")  # DOM-level (see module docstring)
            return
        await self.tap(control, f"field {label!r}")
        await self.js(f"(() => {{ const el = {control}; el.focus(); el.select(); }})()")
        await self.send("Input.insertText", text=value)
        await asyncio.sleep(0.15)

    async def _set_value(self, control, value, event):
        await self.js(f"""(() => {{ const el = {control}; const proto = Object.getPrototypeOf(el);
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(value)});
            el.dispatchEvent(new Event({json.dumps(event)}, {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
        await asyncio.sleep(0.2)

    async def choose(self, label, option_text, scope=None):
        control = self.control(label, scope)
        value = await self.js(f"""(() => {{ const el = {control}; if (!el) return null;
            const o = [...el.options].find((x) => x.textContent.trim() === {json.dumps(option_text)}) ||
                      [...el.options].find((x) => x.textContent.includes({json.dumps(option_text)}));
            return o ? o.value : null; }})()""")
        if value is None:
            raise AssertionError(f"no option {option_text!r} in {label!r}")
        await self._set_value(control, value, "change")
        await self.idle()

    async def upload(self, path: Path):
        document = await self.send("DOM.getDocument")
        node = await self.send("DOM.querySelector", nodeId=document["root"]["nodeId"], selector="input[type=file]")
        await self.send("DOM.setFileInputFiles", files=[str(path)], nodeId=node["nodeId"])
        await asyncio.sleep(0.3)

    async def shot(self, name):
        data = await self.send("Page.captureScreenshot", format="png")
        target = self.out / "screenshots" / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data["data"]))
        return target.relative_to(self.out).as_posix()

    async def viewport(self, metrics):
        await self.send("Emulation.setDeviceMetricsOverride", **metrics)
        await self.send("Emulation.setTouchEmulationEnabled", enabled=metrics["mobile"])
        await asyncio.sleep(0.3)

    async def sign_in(self, role, password=None):
        await self.goto("/login")  # on the app's origin first (about:blank has no sessionStorage)
        await self.js("sessionStorage.clear()")
        await self.goto("/login")
        await self.fill("Work email", SESSION["accounts"][role])
        await self.fill("Password", password or SESSION["password"])
        await self.click("Sign in", "button[type=submit]")
        await self.wait("location.pathname !== '/login'", what=f"sign-in as {role}")
        await self.idle()

    async def sign_out(self):
        await self.click("Sign out", "button")
        await self.wait("location.pathname === '/login'", what="sign-out")

    async def nav_labels(self):
        return await self.js("[...document.querySelectorAll('nav[aria-label=Main] a')].map((a) => a.textContent.trim())")

    async def no_page_overflow(self):
        return await self.js("document.documentElement.scrollWidth <= window.innerWidth + 1")


# --- Tests --------------------------------------------------------------------------------------------------------

async def t01_invalid_credentials(p: Page, notes):
    await p.goto("/login")
    await p.fill("Work email", SESSION["accounts"]["EMPLOYEE"])
    await p.fill("Password", "Definitely-Not-The-Password-1")
    await p.click("Sign in", "button[type=submit]")
    await p.expect_text("Invalid email or password.")
    assert await p.path() == "/login"
    assert await p.js("sessionStorage.getItem('wfp.accessToken')") is None
    notes.append(await p.shot("01_login_invalid_credentials"))


async def t02_login_logout_revokes_token(p: Page, notes):
    await p.sign_in("EMPLOYEE")
    assert await p.path() == "/"
    token = await p.js("sessionStorage.getItem('wfp.accessToken')")
    assert token, "no token after sign-in"
    notes.append(await p.shot("02_employee_dashboard"))
    await p.sign_out()
    assert await p.js("sessionStorage.getItem('wfp.accessToken')") is None
    status = API.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
    assert status == 401, f"API check: token still valid after sign-out ({status})"
    notes.append("API check: the signed-out token now gets 401 (server-side revocation)")


async def t03_expired_session(p: Page, notes):
    await p.sign_in("EMPLOYEE_2")
    me = API.post("/auth/login", json={"email": SESSION["accounts"]["EMPLOYEE_2"], "password": SESSION["password"]}).json()["user"]
    assert API.post(f"/users/{me['id']}/revoke-sessions", headers=api_token("ADMIN")).status_code == 200
    notes.append("API setup: ADMIN ended every session of the signed-in account")
    await p.click("Attendance", "nav[aria-label=Main] a")
    await p.wait("location.pathname === '/login'", what="redirect to sign-in after the session ended")
    await p.expect_text("Your session has ended. Please sign in again.")
    notes.append(await p.shot("03_session_ended"))


async def t04_role_navigation_and_forbidden_pages(p: Page, notes):
    expectations = {
        "EMPLOYEE": ({"Dashboard", "Leave", "Attendance", "My payslips"}, {"Payroll", "Audit logs", "Datasets", "Risk monitoring", "Reports"}),
        # MANAGER audit access is by design (department-scoped AUDIT_READ in app/core/permissions.py).
        "MANAGER": ({"Employees", "Leave", "Tasks", "Audit logs"}, {"Payroll", "Datasets"}),
        # HR reads datasets and model status by design (DATASET_READ); uploads and training are ADMIN only.
        "HR": ({"Employees", "Payroll", "Risk monitoring", "Reports", "Audit logs", "Datasets"}, set()),
        "ADMIN": ({"Datasets", "Audit logs", "Payroll"}, set()),
    }
    for role, (visible, hidden) in expectations.items():
        await p.sign_in(role)
        labels = set(await p.nav_labels())
        assert visible <= labels, f"{role} missing {visible - labels}"
        assert not hidden & labels, f"{role} sees {hidden & labels}"
        notes.append(f"{role}: {len(labels)} navigation items, none of {sorted(hidden) or '-'}")
    await p.sign_in("EMPLOYEE")
    await p.goto("/audit")
    await p.expect_text("Not available for your role")
    notes.append(await p.shot("04_employee_forbidden_audit"))
    assert API.get("/audit-logs", headers=api_token("EMPLOYEE")).status_code == 403
    notes.append("API check: the same EMPLOYEE gets 403 from GET /api/audit-logs")


async def t05_employee_management(p: Page, notes):
    await p.sign_in("HR")
    await p.goto("/employees")
    await p.click("New employee", "button")
    for label, value in (("Employee code", "QA-NEW-01"), ("Full name", "Nadia Quinn"),
                         ("Work email", "qa.nadia@example.com"), ("Designation", "Data Analyst")):
        await p.fill(label, value, scope="dialog")
    await p.choose("Department", "Engineering", scope="dialog")
    await p.fill("Date of joining", (date.today() - timedelta(days=3)).isoformat(), scope="dialog")
    await p.click("Save", "button", scope="dialog", exact=True)
    await p.expect_text("Nadia Quinn")
    notes.append(await p.shot("05_employee_created"))
    found = API.get("/employees/search", params={"q": "Nadia"}, headers=api_token("HR")).json()["items"]
    assert [e["employee_code"] for e in found] == ["QA-NEW-01"]
    notes.append("API check: the new employee record exists (QA-NEW-01)")


async def t06_attendance_check_in(p: Page, notes):
    await p.sign_in("EMPLOYEE_2")
    await p.goto("/attendance")
    await p.click("Check in", "button", exact=True)
    await p.choose("Shift", "General", scope="dialog")
    await p.click("Check in", "button", scope="dialog", exact=True)
    await p.wait("[...document.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Check out' && !b.disabled)",
                 what="Check out enabled")
    notes.append(await p.shot("06_checked_in"))
    today = API.get("/attendance/me", headers=api_token("EMPLOYEE_2")).json()["items"][0]
    assert today["check_in"] and today["work_date"] == date.today().isoformat()


async def t07_leave_request_and_manager_approval(p: Page, notes):
    day = weekday(10)
    await p.sign_in("EMPLOYEE")
    await p.goto("/leave")
    await p.click("Request leave", "button")
    await p.choose("Leave type", "Annual Leave", scope="dialog")
    await p.fill("First day", day.isoformat(), scope="dialog")
    await p.fill("Last day", day.isoformat(), scope="dialog")
    await p.fill("Reason", "Family event", scope="dialog")
    await p.click("Submit request", "button", scope="dialog")
    await p.expect_text("Pending")
    notes.append(await p.shot("07a_leave_requested"))
    await p.sign_in("MANAGER")
    await p.goto("/leave")
    await p.click("Review queue", "[role=tab], button", exact=True)
    await p.expect_text("Ava Patel")
    await p.click("Approve", "button", scope="Review queue", exact=True)
    await p.click("Approve", "button", scope="dialog", exact=True)
    await p.expect_text("Nothing to review")
    notes.append(await p.shot("07b_leave_approved_by_manager"))
    mine = API.get("/leave/requests/me", headers=api_token("EMPLOYEE")).json()["items"]
    assert [(r["start_date"], r["status"]) for r in mine] == [(day.isoformat(), "APPROVED")]


async def t08_payroll_calculate_and_approve(p: Page, notes):
    await p.sign_in("HR")
    await p.goto("/payroll")
    await p.click("New period", "button")
    await p.click("Create", "button", scope="dialog", exact=True)
    await p.click("Calculate", "button", exact=True)
    await p.click("Calculate", "button", scope="dialog", exact=True)
    await p.expect_text("Calculated:")
    await p.click("Bo Lindqvist", "tr[tabindex]")
    await p.click("Approve", "button", scope="dialog", exact=True)
    await p.click("Confirm", "button", scope="dialog", exact=True)
    await p.wait("document.querySelector('[role=dialog]')?.innerText.includes('Approved')", what="record approved")
    notes.append(await p.shot("08_payroll_record_approved"))
    periods = API.get("/payroll/periods", headers=api_token("HR")).json()["items"]
    records = API.get(f"/payroll/periods/{periods[0]['id']}/records", params={"page_size": 100}, headers=api_token("HR")).json()["items"]
    assert {r["employee"]["full_name"]: r["status"] for r in records}["Bo Lindqvist"] == "APPROVED"


async def t09_no_model_state_and_dataset_upload(p: Page, notes):
    await p.sign_in("ADMIN")
    await p.goto("/datasets")
    await p.expect_text("No usable model")
    notes.append(await p.shot("09a_no_active_model"))
    blocked = API.post(f"/ai/attrition/predict/{SESSION['employees']['qa.bo@example.com']}", headers=api_token("HR"))
    assert blocked.status_code == 503 and blocked.json()["detail"]["code"] == "no_active_model"
    notes.append("API check: prediction returns 503 no_active_model")
    sys.path.insert(0, str(ROOT / "backend" / "tests"))
    from synthetic_attrition import synthetic_dataset

    csv_path = p.out / "synthetic_upload.csv"
    csv_path.write_bytes(synthetic_dataset(n=300, seed=21).to_csv(index=False).encode())
    await p.click("Upload dataset", "button")
    await p.fill("Display name", "QA browser upload", scope="dialog")
    await p.upload(csv_path)
    await p.click("Upload", "button", scope="dialog", exact=True)
    await p.expect_text("QA browser upload")
    await p.click("Validate", "button", exact=True)
    await p.expect_text("Validated")
    notes.append(await p.shot("09b_dataset_validated"))
    csv_path.unlink()


async def t10_model_activation_and_alerts(p: Page, notes):
    admin, hr = api_token("ADMIN"), api_token("HR")
    dataset = API.get("/datasets", headers=admin).json()["items"][0]
    model = API.post("/ai/attrition/train", json={"dataset_id": dataset["id"]}, headers=admin).json()["model"]
    assert API.post(f"/ai/attrition/models/{model['model_version']}/activate", headers=admin).status_code == 200
    ids = [v for k, v in SESSION["employees"].items() if k != "qa.hr@example.com"]
    API.post("/ai/attrition/predict-batch", json={"employee_ids": ids}, headers=hr).raise_for_status()
    API.post("/risk-monitoring/run", json={}, headers=hr).raise_for_status()
    notes.append("API setup: synthetic model trained on the browser-uploaded dataset, activated, predictions and a "
                 "monitoring run (alert threshold lowered for this QA server only)")
    await p.sign_in("ADMIN")
    await p.goto("/datasets")
    await p.expect_text("Active model available")
    notes.append(await p.shot("10_model_available"))


async def t11_risk_alert_to_intervention(p: Page, notes):
    await p.sign_in("HR")
    await p.goto("/risk")
    open_row = "[...document.querySelectorAll('tbody tr[tabindex]')].find((r) => r.innerText.includes('Open'))"
    name = await p.js(f"({open_row}).children[1].innerText.split(' (')[0]")
    await p.tap(open_row, "an Open alert row")
    await p.idle()
    await p.click("Acknowledge", "button", scope="dialog", exact=True)
    await p.fill("Reviewer note", "Reviewed in the QA browser run", scope="dialog")
    await p.click("Confirm acknowledgement", "button", scope="dialog")
    await p.wait("!document.querySelector('[role=dialog]')", what="acknowledgement saved")
    row = f"[...document.querySelectorAll('tbody tr[tabindex]')].find((r) => r.innerText.includes({json.dumps(name)}))"
    await p.wait(f"({row}).innerText.includes('Acknowledged')", what="row shows Acknowledged")
    await p.tap(row, "the acknowledged alert row")
    await p.idle()
    await p.click("Plan intervention", "button", scope="dialog")
    await p.wait("location.pathname === '/interventions'", what="interventions page")
    await p.idle()
    await p.fill("Title", "Workload check-in (QA)", scope="dialog")
    await p.fill("Due date", weekday(7).isoformat(), scope="dialog")
    await p.click("Create", "button", scope="dialog", exact=True)
    await p.expect_text("Workload check-in (QA)")
    notes.append(await p.shot("11_intervention_from_alert"))
    items = API.get("/interventions", headers=api_token("HR")).json()["items"]
    assert any(i["title"] == "Workload check-in (QA)" and i.get("risk_alert_id") for i in items)


async def t12_performance_review(p: Page, notes):
    await p.sign_in("MANAGER")
    await p.goto("/performance")
    await p.click("Reviews", "[role=tab], button", exact=True)
    await p.click("New review", "button")
    await p.choose("Cycle", f"QA cycle {date.today().year}", scope="dialog")
    await p.fill("Find employee", "Cyrus", scope="dialog")
    await p.choose("Employee", "Cyrus Obi", scope="dialog")
    await p.click("Open review", "button", scope="dialog", exact=True)
    await p.expect_text("Cyrus Obi")
    notes.append(await p.shot("12_review_opened"))
    reviews = API.get("/performance/reviews", headers=api_token("MANAGER")).json()["items"]
    assert any(r["employee"]["full_name"] == "Cyrus Obi" for r in reviews)


async def t13_search_and_notifications(p: Page, notes):
    await p.sign_in("HR")
    await p.tap("document.getElementById('global-search')", "header search")
    await p.send("Input.insertText", text="Ava")
    await p.send("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
    await p.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
    await p.wait("location.pathname === '/search'", what="search page")
    await p.expect_text("Ava Patel")
    notes.append(await p.shot("13a_search"))
    await p.goto("/notifications")
    await p.expect_text("New attrition-risk alert")
    notes.append(await p.shot("13b_notifications"))


async def t14_report_download(p: Page, notes):
    downloads = p.out / "downloads"
    shutil.rmtree(downloads, ignore_errors=True)
    downloads.mkdir()
    await p.send("Browser.setDownloadBehavior", behavior="allow", downloadPath=str(downloads), eventsEnabled=True)
    await p.sign_in("HR")
    await p.goto("/reports")
    title = await p.js("document.querySelector('section h2')?.textContent.trim()")
    await p.choose("Format", "CSV", scope=title)
    await p.click("Download", "button", scope=title)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not [f for f in downloads.iterdir() if f.suffix == ".csv"]:
        await asyncio.sleep(0.3)
    files = [f for f in downloads.iterdir() if f.suffix == ".csv"]
    assert files and files[0].stat().st_size > 50, "no CSV downloaded"
    notes.append(f"downloaded {files[0].name} ({files[0].stat().st_size} bytes) from the '{title}' report")
    shutil.rmtree(downloads)


async def t15_audit_logs(p: Page, notes):
    await p.sign_in("ADMIN")
    await p.goto("/audit")
    await p.expect_text("Successful sign-in")
    notes.append(await p.shot("15_audit_logs"))
    text = await p.text()
    assert SESSION["password"] not in text and "Bearer" not in text


async def t16_change_password(p: Page, notes):
    new_password = "Qa-Changed-Passphrase-77"
    await p.sign_in("EMPLOYEE_2")
    await p.tap("document.querySelector('button[aria-label=\"Change password\"]')", "Change password button")
    await p.fill("Current password", "Wrong-Password-0000", scope="dialog")
    await p.fill("New password", new_password, scope="dialog")
    await p.fill("Confirm new password", new_password, scope="dialog")
    await p.click("Change password", "button", scope="dialog", exact=True)
    await p.wait("document.querySelector('[role=dialog] [role=alert]')?.innerText.includes('Current password is incorrect')",
                 what="wrong current password message")
    assert await p.path() != "/login", "a wrong current password must not end the session"
    await p.fill("Current password", SESSION["password"], scope="dialog")
    await p.click("Change password", "button", scope="dialog", exact=True)
    await p.wait("location.pathname === '/login'", what="sign-in page after the change")
    await p.expect_text("Your session has ended. Please sign in again.")
    notes.append(await p.shot("16_password_changed_signed_out"))
    await p.sign_in("EMPLOYEE_2", password=new_password)
    assert await p.path() == "/"
    notes.append("signed in again with the new password")


async def t17_mobile_viewport(p: Page, notes):
    await p.viewport(PHONE)
    try:
        await p.sign_in("EMPLOYEE")
        results = {}
        for path in ("/", "/leave", "/attendance", "/payslips", "/notifications", "/performance"):
            await p.goto(path)
            results[path] = await p.no_page_overflow()
        await p.tap("document.querySelector('button[aria-label=\"Open navigation\"]')", "Open navigation")
        await p.wait("document.querySelector('aside[aria-label=Navigation]')", what="navigation drawer")
        notes.append(await p.shot("17a_mobile_drawer"))
        await p.click("Leave", "aside[aria-label=Navigation] a")
        await p.wait("location.pathname === '/leave'", what="navigation from the drawer")
        notes.append(await p.shot("17b_mobile_leave"))
        await p.sign_in("HR")
        for path in ("/employees", "/payroll", "/risk", "/reports", "/audit"):
            await p.goto(path)
            results[path] = await p.no_page_overflow()
        notes.append(await p.shot("17c_mobile_hr_audit"))
        notes.append("no horizontal page overflow at 390 px: " + json.dumps(results))
        bad = [k for k, ok in results.items() if not ok]
        assert not bad, f"horizontal page overflow at 390 px on {bad}"
    finally:
        await p.viewport(DESKTOP)


TESTS = [t01_invalid_credentials, t02_login_logout_revokes_token, t03_expired_session,
         t04_role_navigation_and_forbidden_pages, t05_employee_management, t06_attendance_check_in,
         t07_leave_request_and_manager_approval, t08_payroll_calculate_and_approve,
         t09_no_model_state_and_dataset_upload, t10_model_activation_and_alerts, t11_risk_alert_to_intervention,
         t12_performance_review, t13_search_and_notifications, t14_report_download, t15_audit_logs,
         t16_change_password, t17_mobile_viewport]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    headers = dict(urllib.request.urlopen(APP + "/login").headers)
    profile = Path(tempfile.mkdtemp(prefix="wfai_edge_"))
    edge = subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={profile}",
                             "--no-first-run", "--disable-extensions", "--window-size=1440,900", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        for _ in range(50):
            try:
                targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json/list").read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:  # noqa: BLE001 - browser still starting
                time.sleep(0.2)
        version = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json/version").read())["Browser"]
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            p = Page(ws, out)
            for domain in ("Page", "Runtime", "Log", "Network"):
                await p.send(f"{domain}.enable")
            await p.send("Page.addScriptToEvaluateOnNewDocument", source=CSP_PROBE)
            await p.viewport(DESKTOP)
            for test in TESTS:
                if args.only and not any(test.__name__.startswith(o) for o in args.only.split(",")):
                    continue
                notes, started = [], time.monotonic()
                errors_before = len(p.server_errors), len(p.page_errors)
                try:
                    await test(p, notes)
                    status, error = "PASS", None
                except Exception as exc:  # noqa: BLE001 - every failure is reported, not hidden
                    status, error = "FAIL", f"{type(exc).__name__}: {exc}"
                    try:
                        notes.append(await p.shot(f"FAILED_{test.__name__}"))
                    except Exception:  # noqa: BLE001
                        pass
                    traceback.print_exc()
                new_5xx = p.server_errors[errors_before[0]:]
                if new_5xx and status == "PASS":
                    status, error = "FAIL", f"server errors during the test: {new_5xx}"
                results.append({"test": test.__name__, "status": status, "seconds": round(time.monotonic() - started, 1),
                                "error": error, "evidence": notes, "page_errors": p.page_errors[errors_before[1]:]})
                print(f"{status}  {test.__name__}" + (f"  -- {error}" if error else ""), flush=True)
            await p.collect_csp()
            p.reader.cancel()
    finally:
        edge.terminate()
        edge.wait(10)
        shutil.rmtree(profile, ignore_errors=True)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "browser": version, "app": APP,
        "database": SESSION["database"], "csp_header": headers.get("Content-Security-Policy"),
        "passed": sum(r["status"] == "PASS" for r in results), "failed": sum(r["status"] == "FAIL" for r in results),
        "csp_violations": sorted(set(p.csp)), "api_5xx_responses": p.server_errors, "uncaught_page_errors": p.page_errors,
        "results": results,
    }
    (out / "browser_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("passed", "failed", "csp_violations", "api_5xx_responses",
                                                "uncaught_page_errors")}, indent=2))
    return 0 if summary["failed"] == 0 and not summary["csp_violations"] and not p.server_errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
