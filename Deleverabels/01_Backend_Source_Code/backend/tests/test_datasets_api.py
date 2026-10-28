"""Dataset registry API. All uploaded data is SYNTHETIC."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.main import app, create_app
from app.models import AuditLog, Dataset, DatasetStatus, DatasetVersion, UserRole
from app.models.base import utcnow
from tests.conftest import TEST_ENV
from tests.synthetic_attrition import synthetic_dataset

ADMIN, HR, MANAGER, EMPLOYEE = UserRole.ADMIN, UserRole.HR, UserRole.MANAGER, UserRole.EMPLOYEE


def csv_bytes(**kwargs) -> bytes:
    return synthetic_dataset(**kwargs).to_csv(index=False).encode()


def upload(client, headers, content: bytes, name="Synthetic data", filename="data.csv"):
    return client.post("/api/datasets", headers=headers, data={"display_name": name},
                       files={"file": (filename, content, "text/csv")})


def stored_files(ml_dirs) -> list:
    return sorted(p.name for p in ml_dirs["datasets"].iterdir())


@pytest.fixture
def admin(headers_for):
    return headers_for(ADMIN, "dataset.admin@example.com")


# --- Upload ---------------------------------------------------------------------------------------


def test_upload_registers_version_one(client, db, admin, ml_dirs) -> None:
    content = csv_bytes()
    response = upload(client, admin, content, filename="Jane Doe salaries.csv")
    assert response.status_code == 201
    body = response.json()
    version = body["latest_version"]
    assert (body["display_name"], body["purpose"], body["version_count"]) == ("Synthetic data", "ATTRITION_TRAINING", 1)
    assert (version["version_number"], version["status"], version["training_eligible"]) == (1, "UPLOADED", False)
    assert (version["row_count"], version["column_count"], version["size_bytes"]) == (200, 7, len(content))

    # Stored under a server-generated name; neither the original filename nor server paths are kept or returned.
    files = stored_files(ml_dirs)
    assert len(files) == 1 and files[0].endswith(".csv") and len(files[0]) == 36
    row = db.scalar(select(DatasetVersion))
    assert version["sha256"] == row.sha256
    for leak in ("Jane", "salaries", row.storage_key, str(ml_dirs["datasets"])):
        assert leak not in response.text
    log = db.scalar(select(AuditLog).where(AuditLog.action == "dataset.uploaded"))
    assert set(log.details) == {"version", "sha256", "rows", "columns", "size_bytes"}


@pytest.mark.parametrize(
    "filename, content, status, detail",
    [
        ("data.xlsx", b"a,b\n1,2\n", 415, "Only .csv files are accepted"),
        ("data.csv", b"PK\x03\x04\x00\x00binary", 422, "binary data"),
        ("data.csv", "a,b\né,1\n".encode("latin-1"), 422, "not valid UTF-8"),
        ("data.csv", b"a,b\n1,2,3\n", 422, "expected 2 fields"),
        ("data.csv", b"", 422, "empty"),
    ],
    ids=["extension", "binary", "encoding", "ragged", "empty"],
)
def test_bad_uploads_leave_nothing_behind(client, db, admin, ml_dirs, filename, content, status, detail) -> None:
    response = upload(client, admin, content, filename=filename)
    assert response.status_code == status
    assert detail in response.json()["detail"]
    assert stored_files(ml_dirs) == []
    assert db.scalar(select(func.count(Dataset.id))) == 0


def test_filename_cannot_choose_location(client, admin, ml_dirs) -> None:
    response = upload(client, admin, csv_bytes(), filename="../../escape.csv")
    assert response.status_code == 201
    assert not (ml_dirs["datasets"].parent / "escape.csv").exists()
    assert not any("escape" in name for name in stored_files(ml_dirs))


@pytest.mark.parametrize("name", ["", "x", "<script>", "a" * 101])
def test_display_name_validated(client, admin, ml_dirs, name) -> None:
    assert upload(client, admin, csv_bytes(), name=name).status_code == 422
    assert stored_files(ml_dirs) == []


def test_upload_size_limit_in_handler(client, admin, ml_dirs, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("DATASET_MAX_UPLOAD_BYTES", "2048")
    get_settings.cache_clear()
    response = upload(client, admin, csv_bytes())
    assert response.status_code == 413
    assert stored_files(ml_dirs) == []


def test_oversized_request_rejected_before_parsing() -> None:
    settings = Settings(_env_file=None, dataset_max_upload_bytes=2048, **{
        k.lower(): v for k, v in TEST_ENV.items() if k in ("DB_USER", "DB_PASSWORD", "JWT_SECRET_KEY")})
    small_app = create_app(settings)
    response = TestClient(small_app).post("/api/datasets", files={"file": ("x.csv", b"a" * 200_000, "text/csv")},
                                          data={"display_name": "Too big"})
    # 413 even without a token: the body is refused before authentication or multipart parsing.
    assert response.status_code == 413


def test_identical_content_rejected(client, db, admin, ml_dirs) -> None:
    content = csv_bytes()
    first = upload(client, admin, content).json()
    again = upload(client, admin, content, name="Same again")
    assert again.status_code == 409
    assert again.json()["detail"] == f"Identical content is already registered as dataset {first['id']} version 1"
    assert len(stored_files(ml_dirs)) == 1
    assert db.scalar(select(func.count(Dataset.id))) == 1


# --- Validation ----------------------------------------------------------------------------------


def test_validation_flow_and_report(client, db, admin, headers_for, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes()).json()["id"]
    response = client.post(f"/api/datasets/{dataset_id}/validate", headers=admin)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["training_eligible"], body["version_number"]) == ("VALIDATED", True, 1)
    assert body["report"]["rows_total"] == 200 and body["report"]["fatal_errors"] == []

    # Validated versions are immutable: no revalidation against the same schema.
    again = client.post(f"/api/datasets/{dataset_id}/validate", headers=admin)
    assert again.status_code == 409
    # HR can read the report (metadata only).
    report = client.get(f"/api/datasets/{dataset_id}/validation", headers=headers_for(HR))
    assert report.status_code == 200 and report.json()["status"] == "VALIDATED"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "dataset.validated"))
    assert log.details["rows_total"] == 200 and log.details["fatal_errors"] == 0


def test_rejected_dataset_report_has_no_cell_values(client, db, admin, ml_dirs) -> None:
    frame = synthetic_dataset().astype(object)
    frame.loc[3, "department"] = "SENTINEL-XYZ!"
    frame.loc[4, "tenure_months"] = "PRIVATE-VALUE"
    dataset_id = upload(client, admin, frame.to_csv(index=False).encode()).json()["id"]

    body = client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).json()
    assert (body["status"], body["training_eligible"]) == ("REJECTED", False)
    assert {"row": 4, "field": "department", "issue": "invalid_category"} in body["report"]["row_errors"]
    responses = [client.get(path, headers=admin).text for path in (
        "/api/datasets", f"/api/datasets/{dataset_id}", f"/api/datasets/{dataset_id}/validation",
        f"/api/datasets/{dataset_id}/versions")]
    for text in responses + [str(body)]:
        assert "SENTINEL-XYZ" not in text and "PRIVATE-VALUE" not in text
    assert db.scalar(select(AuditLog).where(AuditLog.action == "dataset.rejected")) is not None
    # A rejected version may be re-run (deterministically the same result).
    assert client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).json()["report"]["row_errors"] == body["report"]["row_errors"]


def test_tampered_file_fails_validation(client, admin, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes()).json()["id"]
    stored = next(ml_dirs["datasets"].glob("*.csv"))
    stored.write_bytes(stored.read_bytes().replace(b"1", b"2", 1))
    body = client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).json()
    assert body["status"] == "REJECTED"
    assert body["report"]["integrity_error"] is True
    assert body["report"]["fatal_errors"] == ["Stored dataset file failed the integrity check"]


def test_stale_validation_can_be_recovered(client, db, admin, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes()).json()["id"]
    version = db.scalar(select(DatasetVersion))
    version.status, version.validation_started_at = DatasetStatus.VALIDATING, utcnow()
    db.commit()
    assert client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).status_code == 409  # in progress

    version.validation_started_at = utcnow() - timedelta(minutes=30)
    db.commit()
    assert client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).json()["status"] == "VALIDATED"


def test_validation_audit_failure_does_not_leave_validating(client, admin, ml_dirs, monkeypatch, session_factory) -> None:
    from app.api import datasets as datasets_api

    dataset_id = upload(client, admin, csv_bytes()).json()["id"]

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(datasets_api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(f"/api/datasets/{dataset_id}/validate", headers=admin)
    assert response.status_code == 500
    with session_factory() as fresh:
        version = fresh.scalar(select(DatasetVersion))
        assert (version.status, version.validation_report) == (DatasetStatus.UPLOADED, None)


def test_upload_audit_failure_removes_stored_file(client, admin, ml_dirs, monkeypatch, session_factory) -> None:
    from app.api import datasets as datasets_api

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(datasets_api, "record_audit", failing_audit)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/datasets", headers=admin, data={"display_name": "Will fail"},
        files={"file": ("data.csv", csv_bytes(), "text/csv")})
    assert response.status_code == 500
    assert stored_files(ml_dirs) == []
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count(DatasetVersion.id))) == 0


# --- Versions -------------------------------------------------------------------------------------


def test_new_versions_are_immutable_additions(client, db, admin, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes(seed=0)).json()["id"]
    client.post(f"/api/datasets/{dataset_id}/validate", headers=admin)
    v1_before = client.get(f"/api/datasets/{dataset_id}/versions", headers=admin).json()[0]

    response = client.post(f"/api/datasets/{dataset_id}/versions", headers=admin,
                           files={"file": ("v2.csv", csv_bytes(seed=1), "text/csv")})
    assert response.status_code == 201
    assert (response.json()["version_number"], response.json()["status"]) == (2, "UPLOADED")

    versions = client.get(f"/api/datasets/{dataset_id}/versions", headers=admin).json()
    assert [v["version_number"] for v in versions] == [1, 2]
    assert versions[0] == v1_before  # version 1 untouched
    detail = client.get(f"/api/datasets/{dataset_id}", headers=admin).json()
    assert (detail["version_count"], detail["latest_version"]["version_number"]) == (2, 2)
    report_v1 = client.get(f"/api/datasets/{dataset_id}/validation", params={"version": 1}, headers=admin).json()
    assert report_v1["status"] == "VALIDATED"

    duplicate = client.post(f"/api/datasets/{dataset_id}/versions", headers=admin,
                            files={"file": ("v3.csv", csv_bytes(seed=0), "text/csv")})
    assert duplicate.status_code == 409
    assert len(stored_files(ml_dirs)) == 2
    assert client.post("/api/datasets/9999/versions", headers=admin,
                       files={"file": ("x.csv", csv_bytes(seed=5), "text/csv")}).status_code == 404


def test_concurrent_version_number_conflict_cleans_up(client, admin, ml_dirs, monkeypatch) -> None:
    """Simulate losing a race for the next version number: the unique constraint rejects the insert,
    the request gets 409 and the stored file is removed."""
    from app.services import datasets as datasets_service

    dataset_id = upload(client, admin, csv_bytes(seed=0)).json()["id"]
    original = datasets_service.register

    def racing_register(*args, **kwargs):
        version = original(*args, **kwargs)
        version.version_number = 1  # another request already took this number
        return version

    monkeypatch.setattr(datasets_service, "register", racing_register)
    response = client.post(f"/api/datasets/{dataset_id}/versions", headers=admin,
                           files={"file": ("v2.csv", csv_bytes(seed=1), "text/csv")})
    assert response.status_code == 409
    assert len(stored_files(ml_dirs)) == 1  # only version 1's file


# --- Archive, download, listing ---------------------------------------------------------------------


def test_archive_keeps_history(client, db, admin, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes(seed=0)).json()["id"]
    client.post(f"/api/datasets/{dataset_id}/versions", headers=admin, files={"file": ("v2.csv", csv_bytes(seed=1), "text/csv")})
    response = client.post(f"/api/datasets/{dataset_id}/archive", headers=admin)
    assert response.status_code == 200
    assert (response.json()["versions_archived"], response.json()["versions_referenced_by_models"]) == (2, 0)

    assert client.post(f"/api/datasets/{dataset_id}/archive", headers=admin).status_code == 409
    assert client.post(f"/api/datasets/{dataset_id}/validate", headers=admin).status_code == 409
    assert client.post(f"/api/datasets/{dataset_id}/versions", headers=admin,
                       files={"file": ("v3.csv", csv_bytes(seed=2), "text/csv")}).status_code == 409
    assert len(stored_files(ml_dirs)) == 2  # nothing deleted
    assert client.get("/api/datasets", headers=admin).json()["total"] == 0
    archived = client.get("/api/datasets", params={"include_archived": "true"}, headers=admin).json()
    assert archived["items"][0]["latest_version"]["status"] == "ARCHIVED"


def test_download_admin_only_verified_and_audited(client, db, admin, headers_for, ml_dirs) -> None:
    content = csv_bytes()
    dataset_id = upload(client, admin, content).json()["id"]
    response = client.get(f"/api/datasets/{dataset_id}/download", headers=admin)
    assert response.status_code == 200 and response.content == content
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == f'attachment; filename="dataset-{dataset_id}-v1.csv"'
    assert db.scalar(select(AuditLog).where(AuditLog.action == "dataset.downloaded")) is not None
    assert client.get(f"/api/datasets/{dataset_id}/download", headers=headers_for(HR)).status_code == 403

    stored = next(ml_dirs["datasets"].glob("*.csv"))
    stored.write_bytes(b"tampered")
    assert client.get(f"/api/datasets/{dataset_id}/download", headers=admin).status_code == 409


def test_list_filters_and_pagination(client, admin, ml_dirs) -> None:
    ids = [upload(client, admin, csv_bytes(seed=s), name=f"Data {s}").json()["id"] for s in range(3)]
    client.post(f"/api/datasets/{ids[0]}/validate", headers=admin)
    page = client.get("/api/datasets", params={"page_size": 2}, headers=admin).json()
    assert (page["total"], page["pages"], len(page["items"])) == (3, 2, 2)
    validated = client.get("/api/datasets", params={"status": "VALIDATED"}, headers=admin).json()
    assert [d["id"] for d in validated["items"]] == [ids[0]]
    assert client.get("/api/datasets", params={"path": "/etc"}, headers=admin).status_code == 422


# --- Permissions ---------------------------------------------------------------------------------


def test_role_matrix(client, headers_for, admin, ml_dirs) -> None:
    dataset_id = upload(client, admin, csv_bytes()).json()["id"]
    reads = [f"/api/datasets", f"/api/datasets/{dataset_id}", f"/api/datasets/{dataset_id}/versions",
             f"/api/datasets/{dataset_id}/validation"]
    writes = [("post", f"/api/datasets/{dataset_id}/validate"), ("post", f"/api/datasets/{dataset_id}/archive"),
              ("get", f"/api/datasets/{dataset_id}/download")]
    for role in (HR, MANAGER, EMPLOYEE):
        headers = headers_for(role)
        for path in reads:
            assert client.get(path, headers=headers).status_code == (200 if role == HR else 403), (role, path)
        for method, path in writes:
            assert getattr(client, method)(path, headers=headers).status_code == 403, (role, path)
        assert upload(client, headers, csv_bytes(seed=9)).status_code == 403
        assert client.post(f"/api/datasets/{dataset_id}/versions", headers=headers,
                           files={"file": ("x.csv", csv_bytes(seed=8), "text/csv")}).status_code == 403
    assert len(stored_files(ml_dirs)) == 1


def test_requires_authentication(client) -> None:
    for method, path in [("get", "/api/datasets"), ("post", "/api/datasets"), ("get", "/api/datasets/1"),
                         ("post", "/api/datasets/1/validate"), ("get", "/api/datasets/1/download")]:
        assert getattr(client, method)(path).status_code == 401, path
