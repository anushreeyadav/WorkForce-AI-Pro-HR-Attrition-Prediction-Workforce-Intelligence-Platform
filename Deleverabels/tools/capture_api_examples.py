"""Real request/response examples from the running demo API (tools/demo_server.py), saved as JSON for the API
documentation. Every exchange is a live call; access tokens are replaced with "<redacted>" before saving, and the demo
password is never written. All data is synthetic."""

import json
import tempfile
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000/api"
OUT = Path(__file__).resolve().parents[1]
SESSION = json.loads((Path(tempfile.gettempdir()) / "workforce_demo_session.json").read_text())


def record(client, store, name, method, path, headers=None, role=None, **kwargs):
    response = client.request(method, f"{API}{path}", headers=headers, **kwargs)
    body = response.json() if "json" in response.headers.get("content-type", "") else response.text[:500]
    if isinstance(body, dict) and "access_token" in body:
        body = {**body, "access_token": "<redacted>"}
    request = {"method": method, "path": f"/api{path}", "as": role or "anonymous"}
    if "params" in kwargs:
        request["query"] = kwargs["params"]
    if "json" in kwargs:
        request["json"] = {k: ("<demo password>" if k == "password" else v) for k, v in kwargs["json"].items()}
    store[name] = {"request": request, "status": response.status_code, "response": body}
    return response


def login(client, role):
    response = client.post(f"{API}/auth/login", json={"email": SESSION["accounts"][role], "password": SESSION["password"]})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def main() -> None:
    general, ai = {}, {}
    with httpx.Client(timeout=60) as client:
        record(client, general, "health", "GET", "/health")
        record(client, general, "login_success", "POST", "/auth/login",
               json={"email": SESSION["accounts"]["HR"], "password": SESSION["password"]})
        record(client, general, "login_wrong_password", "POST", "/auth/login",
               json={"email": SESSION["accounts"]["HR"], "password": "not-the-password"})
        hr, manager, employee, admin = (login(client, r) for r in ("HR", "MANAGER", "EMPLOYEE", "ADMIN"))
        record(client, general, "me", "GET", "/auth/me", hr, "HR")
        record(client, general, "missing_token_401", "GET", "/employees")
        record(client, general, "employee_list_paginated", "GET", "/employees", hr, "HR",
               params={"page": 1, "page_size": 2, "sort_by": "full_name"})
        record(client, general, "role_forbidden_403", "GET", "/payroll/periods", employee, "EMPLOYEE")
        record(client, general, "out_of_scope_404", "GET", "/employees/1", manager, "MANAGER")
        record(client, general, "validation_error_422", "GET", "/employees", hr, "HR", params={"page_size": 1000})
        record(client, general, "unknown_filter_422", "GET", "/tasks", hr, "HR", params={"bogus": "1"})
        record(client, general, "report_catalog_manager", "GET", "/reports/catalog", manager, "MANAGER")
        record(client, general, "users_admin_list", "GET", "/users", admin, "ADMIN", params={"page_size": 2})
        record(client, general, "users_forbidden_for_hr", "GET", "/users", hr, "HR")
        record(client, general, "search", "GET", "/search", employee, "EMPLOYEE", params={"q": "migration"})

        record(client, ai, "model_status", "GET", "/ai/attrition/model-status", hr, "HR")
        employees = client.get(f"{API}/employees", headers=hr, params={"page_size": 100}).json()["items"]
        target = next(e for e in employees if e["department"]["name"] == "Finance")
        record(client, ai, "predict_one", "POST", f"/ai/attrition/predict/{target['id']}", hr, "HR")
        record(client, ai, "prediction_history", "GET", f"/ai/attrition/history/{target['id']}", hr, "HR",
               params={"page_size": 2})
        record(client, ai, "predict_batch_with_unknown_id", "POST", "/ai/attrition/predict-batch", hr, "HR",
               json={"employee_ids": [target["id"], 999999]})
        record(client, ai, "predict_forbidden_for_employee", "POST", f"/ai/attrition/predict/{target['id']}", employee, "EMPLOYEE")
        record(client, ai, "risk_summary", "GET", "/ai/attrition/summary", hr, "HR")
        record(client, ai, "datasets", "GET", "/datasets", hr, "HR")
        dataset_id = client.get(f"{API}/datasets", headers=hr).json()["items"][0]["id"]
        record(client, ai, "dataset_validation_report", "GET", f"/datasets/{dataset_id}/validation", hr, "HR")
        record(client, ai, "revalidate_validated_409", "POST", f"/datasets/{dataset_id}/validate", admin, "ADMIN")
        record(client, ai, "train_forbidden_for_hr", "POST", "/ai/attrition/train", hr, "HR", json={"dataset_id": dataset_id})
        record(client, ai, "risk_alerts", "GET", "/risk-monitoring/alerts", hr, "HR", params={"page_size": 2})
        record(client, ai, "monitoring_run", "POST", "/risk-monitoring/run", hr, "HR", json={})

    for folder, data in (("07_API_Documentation/examples", general), ("03_AI_Prediction_APIs/examples", ai)):
        target_dir = OUT / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, exchange in data.items():
            (target_dir / f"{name}.json").write_text(json.dumps(exchange, indent=2, default=str))
        print(folder, {name: exchange["status"] for name, exchange in data.items()})


if __name__ == "__main__":
    main()
