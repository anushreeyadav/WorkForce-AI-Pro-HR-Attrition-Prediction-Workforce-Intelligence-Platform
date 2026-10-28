"""Attrition API. Every model here is trained on SYNTHETIC data in a temporary directory; no metric in
these tests says anything about real-world performance."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import (
    Attendance,
    AttendanceStatus,
    AttritionModelVersion,
    AttritionPrediction,
    AuditLog,
    Employee,
    EmploymentStatus,
    ModelVersionStatus,
    UserRole,
)
from tests.conftest import local
from tests.synthetic_attrition import synthetic_dataset

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


def register_dataset(client, admin, validate=True, **kwargs) -> int:
    """Upload SYNTHETIC data through the dataset registry (and validate it); returns the dataset id."""
    content = synthetic_dataset(**kwargs).to_csv(index=False).encode()
    response = client.post("/api/datasets", headers=admin, data={"display_name": "Synthetic test data"},
                           files={"file": ("synthetic.csv", content, "text/csv")})
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    if validate:
        assert client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).status_code == 200
    return dataset_id


def train(client, headers, dataset_id, version=None):
    body = {"dataset_id": dataset_id} if version is None else {"dataset_id": dataset_id, "version": version}
    return client.post("/api/ai/attrition/train", json=body, headers=headers)


def activate(client, headers, model_version):
    return client.post(f"/api/ai/attrition/models/{model_version}/activate", headers=headers)


@pytest.fixture
def active_model(client, headers_for, ml_dirs):
    admin = headers_for(ADMIN, "model.admin@example.com")
    response = train(client, admin, register_dataset(client, admin))
    assert response.status_code == 201, response.text
    model = response.json()["model"]
    assert model["status"] == "CANDIDATE"
    activated = activate(client, admin, model["model_version"])
    assert activated.status_code == 200
    return activated.json()


@pytest.fixture
def team(staff):
    """Engineering: manager + two employees. Sales: one employee."""
    manager, manager_h = staff(MANAGER, department="Engineering")
    eng1, _ = staff(department="Engineering", date_of_joining=date(2024, 1, 15))
    eng2, _ = staff(department="Engineering")
    sales, _ = staff(department="Sales")
    return {"manager": manager, "manager_h": manager_h, "eng1": eng1, "eng2": eng2, "sales": sales}


def predict(client, headers, employee_id):
    return client.post(f"/api/ai/attrition/predict/{employee_id}", headers=headers)


# --- No model ---------------------------------------------------------------------------------------


def test_no_model_returns_503_and_never_invents_scores(client, db, headers_for, team, ml_dirs) -> None:
    hr = headers_for(HR)
    response = predict(client, hr, team["eng1"].id)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_active_model"
    batch = client.post("/api/ai/attrition/predict-batch", json={"employee_ids": [team["eng1"].id]}, headers=hr)
    assert batch.status_code == 503
    assert db.scalar(select(func.count(AttritionPrediction.id))) == 0

    status_body = client.get("/api/ai/attrition/model-status", headers=hr).json()
    assert (status_body["available"], status_body["active_model"]) == (False, None)
    assert status_body["monitoring"]["unavailable_requests_total"] == 2
    assert status_body["feature_schema_version"] == "attrition-features-v1"
    assert "performance_score_recent" in status_body["future_features"]


# --- Training ---------------------------------------------------------------------------------------


def test_training_creates_candidate_with_dataset_traceability(client, db, headers_for, ml_dirs) -> None:
    import json

    admin = headers_for(ADMIN)
    dataset_id = register_dataset(client, admin)
    version = client.get(f"/api/datasets/{dataset_id}/versions", headers=admin).json()[0]

    response = train(client, admin, dataset_id)
    assert response.status_code == 201
    body = response.json()
    model = body["model"]
    # Training never activates a model.
    assert (model["status"], model["activated_at"], body["gate_failures"]) == ("CANDIDATE", None, [])
    assert model["training_rows"] == 200 and model["evaluation"]["production_validated"] is False
    assert model["evaluation"]["metrics"]["roc_auc"] >= 0.65
    assert body["dataset"] == {"dataset_id": dataset_id, "version": 1, "sha256": version["sha256"]}
    assert model["dataset_version_id"] == version["id"]
    assert client.get("/api/ai/attrition/model-status", headers=admin).json()["available"] is False

    artifact_dir = ml_dirs["artifacts"] / model["model_version"]
    assert json.loads((artifact_dir / "metadata.json").read_text())["dataset"] == body["dataset"]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "ai.attrition.model_trained"))
    assert set(log.details) == {"status", "training_rows", "roc_auc", "dataset_id", "dataset_version", "dataset_sha256"}
    # The dataset now shows which model used it.
    used = client.get(f"/api/datasets/{dataset_id}/versions", headers=admin).json()[0]["used_by_models"]
    assert used == [model["model_version"]]


def test_explicit_activation_retires_previous(client, db, headers_for, ml_dirs) -> None:
    admin = headers_for(ADMIN)
    first = train(client, admin, register_dataset(client, admin, seed=0)).json()["model"]
    second = train(client, admin, register_dataset(client, admin, seed=1)).json()["model"]
    assert activate(client, admin, first["model_version"]).status_code == 200
    status_body = client.get("/api/ai/attrition/model-status", headers=admin).json()
    assert status_body["active_model"]["model_version"] == first["model_version"]

    response = activate(client, admin, second["model_version"])
    assert response.status_code == 200 and response.json()["status"] == "ACTIVE"
    db.expire_all()
    statuses = {m.model_version: m.status for m in db.scalars(select(AttritionModelVersion))}
    assert statuses == {first["model_version"]: ModelVersionStatus.RETIRED,
                        second["model_version"]: ModelVersionStatus.ACTIVE}
    assert activate(client, admin, first["model_version"]).status_code == 409
    assert activate(client, admin, "lr-20990101000000-00000000").status_code == 404


def test_model_failing_gate_is_rejected_and_cannot_be_activated(client, headers_for, ml_dirs) -> None:
    admin = headers_for(ADMIN)
    body = train(client, admin, register_dataset(client, admin, signal=False)).json()
    assert body["model"]["status"] == "REJECTED"
    assert any("ROC-AUC" in reason for reason in body["gate_failures"])
    assert activate(client, admin, body["model"]["model_version"]).status_code == 409
    assert client.get("/api/ai/attrition/model-status", headers=admin).json()["available"] is False


@pytest.mark.parametrize("body", [{"dataset": "synthetic.csv"}, {"dataset_id": "../x.csv"}, {"dataset_id": 0},
                                  {"dataset_id": 1, "activate": True}, {"dataset_id": 1, "path": "/tmp/x.csv"}, {}])
def test_train_accepts_only_registered_dataset_ids(client, headers_for, ml_dirs, body) -> None:
    assert client.post("/api/ai/attrition/train", json=body, headers=headers_for(ADMIN)).status_code == 422


def test_training_refuses_ineligible_datasets(client, db, headers_for, ml_dirs) -> None:
    admin = headers_for(ADMIN)
    assert train(client, admin, 9999).status_code == 404

    unvalidated = register_dataset(client, admin, validate=False, seed=2)
    response = train(client, admin, unvalidated)
    assert response.status_code == 409
    assert response.json()["detail"]["errors"] == ["Only VALIDATED, training-eligible dataset versions can be used"]

    one_class = synthetic_dataset(seed=3).assign(attrited=0).to_csv(index=False).encode()
    rejected = client.post("/api/datasets", headers=admin, data={"display_name": "One class"},
                           files={"file": ("x.csv", one_class, "text/csv")}).json()["id"]
    client.post(f"/api/datasets/{rejected}/validate", headers=admin)
    assert train(client, admin, rejected).status_code == 409

    archived = register_dataset(client, admin, seed=4)
    client.post(f"/api/datasets/{archived}/archive", headers=admin)
    assert train(client, admin, archived).json()["detail"]["errors"] == ["Archived datasets cannot be used for training"]

    assert db.scalar(select(func.count(AttritionModelVersion.id))) == 0
    reasons = [log.details["reason"] for log in db.scalars(
        select(AuditLog).where(AuditLog.action == "ai.attrition.training_rejected").order_by(AuditLog.id))]
    assert reasons == ["not_found", "not_eligible", "not_eligible", "archived"]


def test_training_refuses_tampered_dataset_file(client, db, headers_for, ml_dirs) -> None:
    admin = headers_for(ADMIN)
    dataset_id = register_dataset(client, admin)
    stored = next(ml_dirs["datasets"].glob("*.csv"))
    stored.write_bytes(stored.read_bytes() + b"1,0.1,10,0,30,1,1\n")
    response = train(client, admin, dataset_id)
    assert response.status_code == 409
    assert response.json()["detail"]["errors"] == ["Stored dataset file failed the integrity check"]
    assert db.scalar(select(func.count(AttritionModelVersion.id))) == 0


@pytest.mark.parametrize("role", [HR, MANAGER, EMPLOYEE])
def test_only_admin_trains_or_activates(client, headers_for, ml_dirs, role) -> None:
    headers = headers_for(role)
    assert train(client, headers, 1).status_code == 403
    assert activate(client, headers, "lr-20260101000000-abcdef12").status_code == 403


# --- Predictions ------------------------------------------------------------------------------------


def test_prediction_uses_real_features_and_is_persisted(client, db, headers_for, team, active_model, leave_clock,
                                                        monkeypatch) -> None:
    from app.api import attrition as attrition_api

    employee = team["eng1"]
    today = date(2026, 3, 2)  # fixed "today" (org time zone) shared with the leave clock
    monkeypatch.setattr(attrition_api, "utcnow", leave_clock)
    for offset, status in ((1, AttendanceStatus.LATE), (2, AttendanceStatus.PRESENT), (3, AttendanceStatus.LATE),
                           (4, AttendanceStatus.PRESENT), (5, AttendanceStatus.ABSENT)):
        day = today - timedelta(days=offset)
        db.add(Attendance(employee_id=employee.id, work_date=day, status=status, break_minutes=0,
                          check_in=None if status == AttendanceStatus.ABSENT else local(day.year, day.month, day.day, 9)))
    db.add(Attendance(employee_id=employee.id, work_date=today - timedelta(days=200), status=AttendanceStatus.LATE,
                      break_minutes=0, check_in=local(2025, 8, 14, 9)))  # outside the 90-day window
    db.commit()
    before = db.scalar(select(func.count(Attendance.id)))

    response = predict(client, headers_for(HR), employee.id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["features"] == {"tenure_months": 25, "late_rate_90d": 0.5, "avg_overtime_minutes_90d": None,
                                "recorded_absence_days_90d": 1, "attendance_days_recorded_90d": 5,
                                "department": str(employee.department_id)}
    assert body["missing_inputs"] == ["avg_overtime_minutes_90d"]
    assert 0 <= float(body["risk_score"]) <= 100
    expected = "HIGH" if float(body["risk_score"]) >= 70 else "MEDIUM" if float(body["risk_score"]) >= 40 else "LOW"
    assert body["risk_category"] == expected
    assert body["model_version"] == active_model["model_version"]
    assert body["limitations"] and "not a prediction of any individual's decision" in body["limitations"][0]

    stored = db.scalar(select(AttritionPrediction))
    assert stored.employee_id == employee.id and stored.features == body["features"]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "ai.attrition.predicted"))
    assert log.details == {"employee_id": employee.id, "model_version": active_model["model_version"]}
    # Nothing else changed.
    db.expire_all()
    assert db.scalar(select(func.count(Attendance.id))) == before
    assert db.get(Employee, employee.id).status == EmploymentStatus.ACTIVE


def test_prediction_guards(client, db, headers_for, staff, team, active_model) -> None:
    hr_employee, hr_self = staff(HR)
    assert predict(client, hr_self, hr_employee.id).status_code == 403  # not about yourself
    assert predict(client, headers_for(HR), 99999).status_code == 404
    leaver, _ = staff(status=EmploymentStatus.INACTIVE)
    assert predict(client, headers_for(ADMIN), leaver.id).status_code == 409


def test_manager_department_scope(client, team, active_model) -> None:
    headers = team["manager_h"]
    assert predict(client, headers, team["eng1"].id).status_code == 201
    hidden = predict(client, headers, team["sales"].id)
    assert hidden.status_code == 404 and hidden.json() == {"detail": "Employee not found"}
    assert client.get(f"/api/ai/attrition/history/{team['sales'].id}", headers=headers).status_code == 404
    assert client.get(f"/api/ai/attrition/history/{team['eng1'].id}", headers=headers).json()["total"] == 1
    assert predict(client, headers, team["manager"].id).status_code == 403
    assert client.get("/api/ai/attrition/summary", headers=headers).status_code == 403
    assert client.get("/api/ai/attrition/model-status", headers=headers).status_code == 403


def test_employees_have_no_access_to_any_risk_data(client, staff, team, active_model, headers_for) -> None:
    employee, own_headers = staff(department="Engineering")
    predict(client, headers_for(HR), employee.id)
    for method, path, body in [
        ("post", f"/api/ai/attrition/predict/{employee.id}", None),
        ("post", "/api/ai/attrition/predict-batch", {"employee_ids": [employee.id]}),
        ("get", f"/api/ai/attrition/history/{employee.id}", None),
        ("get", "/api/ai/attrition/summary", None),
        ("get", "/api/ai/attrition/model-status", None),
    ]:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, headers=own_headers, **kwargs).status_code == 403, path
    # Ordinary employee endpoints never carry risk information.
    profile = client.get(f"/api/employees/{employee.id}", headers=own_headers).text.lower()
    assert "risk" not in profile and "attrition" not in profile


def test_batch_prediction(client, db, headers_for, team, staff, active_model) -> None:
    leaver, _ = staff(status=EmploymentStatus.INACTIVE)
    hr = headers_for(HR)
    ids = [team["eng1"].id, team["eng2"].id, team["sales"].id, leaver.id, 99999]
    body = client.post("/api/ai/attrition/predict-batch", json={"employee_ids": ids}, headers=hr).json()
    assert {p["employee"]["id"] for p in body["predictions"]} == {team["eng1"].id, team["eng2"].id, team["sales"].id}
    assert {(s["employee_id"], s["reason"]) for s in body["skipped"]} == {
        (leaver.id, "Not a current employee"), (99999, "Not found")}
    stored = db.scalars(select(AttritionPrediction)).all()
    assert len(stored) == 3 and {p.batch_id for p in stored} == {body["batch_id"]}
    log = db.scalar(select(AuditLog).where(AuditLog.action == "ai.attrition.batch_predicted"))
    assert log.details == {"requested": 5, "predicted": 3, "skipped": 2, "model_version": active_model["model_version"]}

    # Managers: other departments look like unknown ids.
    manager_body = client.post("/api/ai/attrition/predict-batch", json={"employee_ids": [team["eng1"].id, team["sales"].id]},
                               headers=team["manager_h"]).json()
    assert [p["employee"]["id"] for p in manager_body["predictions"]] == [team["eng1"].id]
    assert manager_body["skipped"] == [{"employee_id": team["sales"].id, "reason": "Not found"}]


@pytest.mark.parametrize("body", [{"employee_ids": []}, {"employee_ids": list(range(1, 102))}, {"employee_ids": [1, 1]},
                                  {"employee_ids": [0]}, {"employee_ids": ["x"]}, {}, {"employee_ids": [1], "force": True}])
def test_batch_validation(client, headers_for, active_model, body) -> None:
    assert client.post("/api/ai/attrition/predict-batch", json=body, headers=headers_for(HR)).status_code == 422


def test_history_and_summary(client, headers_for, team, active_model) -> None:
    hr = headers_for(HR)
    for _ in range(3):
        predict(client, hr, team["eng1"].id)
    predict(client, hr, team["sales"].id)

    history = client.get(f"/api/ai/attrition/history/{team['eng1'].id}", params={"page_size": 2}, headers=hr).json()
    assert (history["total"], history["pages"], len(history["items"])) == (3, 2, 2)
    ids = [p["id"] for p in history["items"]]
    assert ids == sorted(ids, reverse=True)

    summary = client.get("/api/ai/attrition/summary", headers=hr).json()
    assert summary["employees_with_predictions"] == 2  # latest per employee, not per prediction
    assert sum(summary["by_category"].values()) == 2
    assert summary["model_versions"] == [active_model["model_version"]]
    engineering = client.get("/api/ai/attrition/summary", params={"department_id": team["eng1"].department_id}, headers=hr).json()
    assert engineering["employees_with_predictions"] == 1

    status_body = client.get("/api/ai/attrition/model-status", headers=hr).json()
    assert status_body["available"] is True
    assert status_body["monitoring"]["predictions_total"] == 4
    assert sum(status_body["monitoring"]["predictions_by_category_active_model"].values()) == 4


def test_audit_failure_rolls_back_prediction(client, headers_for, team, active_model, monkeypatch, session_factory) -> None:
    from app.api import attrition as attrition_api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(attrition_api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/ai/attrition/predict/{team['eng1'].id}", headers=headers_for(HR))
    assert response.status_code == 500
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(AttritionPrediction.id))) == 0


def test_tampered_artifact_makes_model_unavailable(client, headers_for, team, active_model, ml_dirs) -> None:
    from app.services import attrition as attrition_service

    model_file = ml_dirs["artifacts"] / active_model["model_version"] / "model.joblib"
    model_file.write_bytes(model_file.read_bytes() + b"x")
    attrition_service.clear_cache()
    response = predict(client, headers_for(HR), team["eng1"].id)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "artifact_error"
    status_body = client.get("/api/ai/attrition/model-status", headers=headers_for(ADMIN)).json()
    assert status_body["available"] is False and "checksum" in status_body["reason"]


def test_endpoints_require_authentication(client) -> None:
    for method, path in [("post", "/api/ai/attrition/predict/1"), ("post", "/api/ai/attrition/predict-batch"),
                         ("get", "/api/ai/attrition/history/1"), ("get", "/api/ai/attrition/summary"),
                         ("get", "/api/ai/attrition/model-status"), ("post", "/api/ai/attrition/train"),
                         ("post", "/api/ai/attrition/models/x/activate")]:
        assert getattr(client, method)(path).status_code == 401, path
