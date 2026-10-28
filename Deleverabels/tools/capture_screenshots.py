"""Genuine screenshots of the running application in Microsoft Edge (headless), driven over the Chrome DevTools
Protocol with the `websockets` package already installed in the backend virtualenv (no new dependency).

Prerequisites (see Deleverabels/README.md):
  1. tools/demo_server.py is running (the unmodified API on 127.0.0.1:8000 with the disposable synthetic database);
  2. the production frontend build is served by `npm run preview` on http://localhost:4173 (proxies /api to :8000).
The script signs in through the real login form as each demo role, visits pages, opens a few detail dialogs and saves
full-page PNGs (1440 px wide). It never types or shows anything but synthetic demo data; the password field is masked.
"""

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP = "http://localhost:4173"
OUT = Path(__file__).resolve().parents[1]
SESSION = json.loads((Path(tempfile.gettempdir()) / "workforce_demo_session.json").read_text())
WIDTH, HEIGHT = 1440, 900


class Browser:
    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    async def send(self, method, **params):
        self.next_id += 1
        my_id = self.next_id
        await self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == my_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, expression):
        result = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")

    async def settle(self, extra=0.8):
        """Wait until loading skeletons/spinners are gone (max ~10 s), then let charts finish animating."""
        for _ in range(50):
            busy = await self.js("document.readyState !== 'complete' || !!document.querySelector('.animate-pulse, .animate-spin')")
            if not busy:
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(extra)

    async def goto(self, path, extra=0.8):
        await self.send("Page.navigate", url=f"{APP}{path}")
        await asyncio.sleep(0.6)
        await self.settle(extra)

    async def click(self, text, selector="button, a, [role=tab], tr, li"):
        found = await self.js(f"""(() => {{
            const items = [...document.querySelectorAll({json.dumps(selector)})];
            const hit = items.find((el) => el.offsetParent !== null && el.textContent.trim().includes({json.dumps(text)}));
            if (hit) {{ hit.scrollIntoView({{block: 'center'}}); hit.click(); return true; }}
            return false; }})()""")
        if not found:
            raise RuntimeError(f"nothing to click containing {text!r}")
        await asyncio.sleep(0.4)
        await self.settle()

    async def click_first_row(self):
        clicked = await self.js("""(() => { const row = document.querySelector('tbody tr[tabindex], tbody tr.cursor-pointer');
            if (row) { row.click(); return true; } return false; })()""")
        if not clicked:
            raise RuntimeError("no clickable table row")
        await asyncio.sleep(0.4)
        await self.settle()

    async def login(self, role):
        await self.js("sessionStorage.clear()")
        await self.goto("/login")
        email = SESSION["accounts"][role]
        await self.js(f"""(() => {{
            const set = (el, v) => {{ Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
                                      el.dispatchEvent(new Event('input', {{bubbles: true}})); }};
            set(document.querySelector('input[type=email]'), {json.dumps(email)});
            set(document.querySelector('input[type=password]'), {json.dumps(SESSION['password'])});
            document.querySelector('form button[type=submit]').click(); }})()""")
        for _ in range(40):
            await asyncio.sleep(0.25)
            if not (await self.js("location.pathname")).startswith("/login"):
                break
        else:
            raise RuntimeError(f"sign-in as {role} did not complete")
        await self.settle()

    async def shot(self, folder, name):
        metrics = await self.send("Page.getLayoutMetrics")
        height = min(int(metrics["cssContentSize"]["height"]), 4000)
        dialog = await self.js("!!document.querySelector('[role=dialog]')")
        if not dialog:  # full page; dialogs are captured at the viewport size
            await self.send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=max(HEIGHT, height), deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.5)
        data = await self.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        await self.send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        path = OUT / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data["data"]))
        print(f"  saved {path.relative_to(OUT)} ({path.stat().st_size // 1024} KB)")

    async def close_dialog(self):
        await self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        await asyncio.sleep(0.4)


S = "06_UI_Screenshots_Demo"


async def journey(b: Browser) -> None:
    await b.send("Page.enable")
    await b.send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)

    # Authentication
    await b.goto("/login", extra=1.5)
    await b.shot(f"{S}/Authentication", "01_login_page.png")
    await b.js("""(() => { const set = (el, v) => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles: true})); };
        set(document.querySelector('input[type=email]'), 'unknown.person@example.com');
        set(document.querySelector('input[type=password]'), 'not-the-password');
        document.querySelector('form button[type=submit]').click(); })()""")
    await asyncio.sleep(1.2)
    await b.shot(f"{S}/Authentication", "02_login_invalid_credentials.png")
    await b.goto("/payroll")  # signed out: protected page redirects to sign-in
    await b.shot(f"{S}/Authentication", "03_protected_route_redirects_to_login.png")

    # HR: dashboards and most HR pages
    await b.login("HR")
    await b.goto("/", extra=2.0)
    await b.shot(f"{S}/Dashboard", "01_main_dashboard_hr.png")
    await b.goto("/employees")
    await b.shot(f"{S}/Employee_Management", "01_employee_list.png")
    await b.click("Aisha Khan", "tr")
    await b.shot(f"{S}/Employee_Management", "02_employee_detail.png")
    await b.close_dialog()
    await b.goto("/departments")
    await b.shot(f"{S}/Employee_Management", "03_departments.png")
    await b.goto("/attendance")
    await b.click("All records", "[role=tab], button")
    await b.shot(f"{S}/Attendance_Leave", "02_attendance_team_overview_hr.png")
    await b.goto("/payroll")
    await b.click("approved", "button")  # last month's period card (calculated, approved and partly paid)
    await b.shot(f"{S}/Payroll", "01_payroll_periods_and_records.png")
    await b.click_first_row()
    await b.shot(f"{S}/Payroll", "02_payroll_record_detail.png")
    await b.close_dialog()
    await b.goto("/risk")
    await b.shot(f"{S}/AI_Risk_Monitoring", "01_attrition_risk_alerts.png")
    await b.click_first_row()
    await b.shot(f"{S}/AI_Risk_Monitoring", "02_risk_alert_detail.png")
    await b.close_dialog()
    await b.goto("/analytics", extra=2.5)
    await b.shot(f"{S}/Analytics_Forecasting", "01_workforce_analytics_forecast_stability.png")
    await b.goto("/recommendations")
    await b.shot(f"{S}/Interventions_Tasks", "03_recommendations.png")
    await b.goto("/interventions")
    await b.shot(f"{S}/Interventions_Tasks", "01_interventions.png")
    await b.click_first_row()
    await b.shot(f"{S}/Interventions_Tasks", "02_intervention_detail_history.png")
    await b.close_dialog()
    await b.goto("/tasks")
    await b.shot(f"{S}/Interventions_Tasks", "04_tasks.png")
    await b.click_first_row()
    await b.shot(f"{S}/Interventions_Tasks", "05_task_detail.png")
    await b.close_dialog()
    await b.goto("/performance?tab=cycles")
    await b.shot(f"{S}/Performance_Reviews", "04_review_cycles_hr.png")
    await b.goto("/notifications")
    await b.shot(f"{S}/Notifications_Search", "01_notifications.png")
    await b.goto("/search?q=Engineer")
    await b.shot(f"{S}/Notifications_Search", "02_global_search.png")
    await b.goto("/activity", extra=1.5)
    await b.shot(f"{S}/Reports_Audit", "01_activity_feed_monitoring_status.png")
    await b.goto("/reports")
    await b.shot(f"{S}/Reports_Audit", "02_reports_and_exports.png")
    await b.goto("/audit")
    await b.shot(f"{S}/Reports_Audit", "03_audit_logs.png")

    # MANAGER: department-scoped dashboard, review queue, reviewer view
    await b.login("MANAGER")
    await b.goto("/", extra=2.0)
    await b.shot(f"{S}/Dashboard", "02_dashboard_manager_department_scope.png")
    await b.goto("/leave")
    await b.click("Review queue", "[role=tab], button")
    await b.shot(f"{S}/Attendance_Leave", "05_leave_review_queue_manager.png")
    await b.goto("/performance")
    await b.shot(f"{S}/Performance_Reviews", "01_goals_manager.png")
    await b.goto("/performance?tab=reviews")
    await b.shot(f"{S}/Performance_Reviews", "02_reviews_manager.png")
    await b.click("Aisha Khan", "tr")
    await b.shot(f"{S}/Performance_Reviews", "03_review_detail_reviewer_evaluation.png")
    await b.close_dialog()

    # EMPLOYEE: self-service
    await b.login("EMPLOYEE")
    await b.goto("/", extra=2.0)
    await b.shot(f"{S}/Dashboard", "03_dashboard_employee_self_service.png")
    await b.goto("/attendance")
    await b.shot(f"{S}/Attendance_Leave", "01_my_attendance_employee.png")
    await b.goto("/leave")
    await b.shot(f"{S}/Attendance_Leave", "03_my_leave_requests_employee.png")
    await b.click("Balances", "[role=tab], button")
    await b.shot(f"{S}/Attendance_Leave", "04_leave_balances_employee.png")
    await b.goto("/payslips")
    await b.shot(f"{S}/Payroll", "03_my_payslips_employee.png")
    await b.click("Payslip", "button")
    await b.shot(f"{S}/Payroll", "04_payslip_dialog_employee.png")
    await b.close_dialog()
    await b.goto("/risk")
    await b.shot(f"{S}/AI_Risk_Monitoring", "04_risk_page_not_available_to_employee.png")

    # ADMIN: datasets and model status
    await b.login("ADMIN")
    await b.goto("/datasets")
    await b.shot(f"{S}/AI_Risk_Monitoring", "03_datasets_and_model_status_admin.png")
    await b.click_first_row()
    await b.shot(f"{S}/AI_Risk_Monitoring", "05_dataset_versions_admin.png")
    await b.close_dialog()


async def main() -> None:
    profile = tempfile.mkdtemp(prefix="edge-demo-")
    edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--remote-debugging-port=9333",
                             f"--user-data-dir={profile}", f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json/list").read())
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:  # noqa: BLE001 - Edge still starting
                time.sleep(0.2)
        else:
            sys.exit("Edge did not start")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            await journey(Browser(ws))
    finally:
        edge.terminate()
        edge.wait(timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
