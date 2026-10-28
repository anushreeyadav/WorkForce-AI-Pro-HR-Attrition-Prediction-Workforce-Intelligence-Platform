"""Sample reports from the application's real export endpoints (GET /api/reports/*, /api/audit-logs/export) against the
running demo server (tools/demo_server.py). Signs in through POST /api/auth/login as HR and as a MANAGER, follows the
report catalog the API returns for each role, and saves every file under the name the server sends
(Content-Disposition). All content is synthetic demo data. Prints a manifest; never prints the password or tokens."""

import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000/api"
OUT = Path(__file__).resolve().parents[1] / "05_Analytics_Reports"
SESSION = json.loads((Path(tempfile.gettempdir()) / "workforce_demo_session.json").read_text())


def login(client, role):
    response = client.post(f"{API}/auth/login", json={"email": SESSION["accounts"][role], "password": SESSION["password"]})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def save(client, headers, path, params, folder):
    response = client.get(f"{API}{path}", headers=headers, params=params)
    if response.status_code != 200:
        return {"path": path, "params": params, "status": response.status_code, "detail": response.text[:200]}
    name = re.search(r'filename="([^"]+)"', response.headers["content-disposition"]).group(1)
    target = OUT / folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return {"file": f"{folder}/{name}", "endpoint": path, "params": params, "status": 200,
            "rows": response.headers.get("x-report-rows"), "bytes": len(response.content),
            "content_type": response.headers["content-type"]}


def main() -> None:
    manifest = []
    with httpx.Client(timeout=60) as client:
        hr = login(client, "HR")
        catalog = client.get(f"{API}/reports/catalog", headers=hr).json()
        periods = client.get(f"{API}/payroll/periods", headers=hr, params={"page_size": 24}).json()["items"]
        calculated = next(p for p in periods if sum(p["record_counts"].values()))
        window = {"date_from": (date.today() - timedelta(days=30)).isoformat(), "date_to": date.today().isoformat()}
        # Leave requests in the demo are upcoming: the leave report covers requests overlapping the next 45 days.
        ahead = {"date_from": date.today().isoformat(), "date_to": (date.today() + timedelta(days=45)).isoformat()}
        for report in catalog["reports"]:
            for fmt in report["formats"]:
                params = {"format": fmt}
                if "period_id" in report["filters"]:
                    params["period_id"] = calculated["id"]
                if report["id"] == "attendance":
                    params.update(window)
                elif report["id"] == "leave":
                    params.update(ahead)
                manifest.append({"role": "HR", "report": report["id"],
                                 **save(client, hr, report["path"].removeprefix("/api"), params, "organization_hr")})
        for fmt in ("csv", "pdf"):
            manifest.append({"role": "HR", "report": "audit_log",
                             **save(client, hr, "/audit-logs/export", {"format": fmt, **window}, "organization_hr")})
        manager = login(client, "MANAGER")
        for report in client.get(f"{API}/reports/catalog", headers=manager).json()["reports"]:
            extra = window if report["id"] == "attendance" else ahead if report["id"] == "leave" else {}
            params = {"format": "csv", **extra}
            manifest.append({"role": "MANAGER", "report": report["id"],
                             **save(client, manager, report["path"].removeprefix("/api"), params, "department_manager")})
        denied = client.get(f"{API}/reports/payroll", headers=manager, params={"format": "csv", "period_id": calculated["id"]})
        manifest.append({"role": "MANAGER", "report": "payroll", "status": denied.status_code,
                         "note": "Refused: payroll exports are ADMIN/HR only"})
    (OUT / "report_manifest.json").write_text(json.dumps(manifest, indent=2))
    for item in manifest:
        print(item.get("file") or item.get("note") or item.get("detail"), "|", item["status"], "| rows:", item.get("rows"))


if __name__ == "__main__":
    main()
