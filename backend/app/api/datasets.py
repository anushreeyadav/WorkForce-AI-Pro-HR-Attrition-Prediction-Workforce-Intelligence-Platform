"""Workforce dataset registry: upload, validate, version, archive, (admin) download.

Responses carry metadata, counts and issue codes only; row contents are never returned except through the
ADMIN-only, audited download. Server paths and storage keys are never exposed.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, require_roles
from app.core import permissions
from app.core.config import get_settings
from app.models import Dataset, DatasetStatus, DatasetVersion, User
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.dataset import (
    ArchiveResult,
    DatasetListParams,
    DatasetName,
    DatasetRead,
    DatasetVersionRead,
    ValidationReportRead,
    VersionParams,
)
from app.services import dataset_storage as storage
from app.services import datasets as service
from app.services.audit import record_audit
from app.services.search import keyword_condition
from app.services.pagination import paginate

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

Admin = Annotated[User, Depends(require_roles(*permissions.DATASET_ADMIN))]
Reader = Annotated[User, Depends(require_roles(*permissions.DATASET_READ))]

NOT_FOUND = "Dataset not found"
_NAME = TypeAdapter(DatasetName)


def _error(code: int, detail) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _read_upload(upload: UploadFile, limit: int) -> bytes:
    """Read at most `limit` bytes; the filename only has to end in .csv (content is checked separately)."""
    if not (upload.filename or "").lower().endswith(".csv"):
        raise _error(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .csv files are accepted")
    chunks, total = [], 0
    while chunk := upload.file.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise _error(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {limit} byte upload limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _get_dataset(db: DbSession, dataset_id: int) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return dataset


def _get_version(db: DbSession, dataset: Dataset, number: int | None) -> DatasetVersion:
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).execution_options(populate_existing=True)
    stmt = (stmt.where(DatasetVersion.version_number == number) if number is not None
            else stmt.order_by(DatasetVersion.version_number.desc()).limit(1))
    version = db.scalar(stmt)
    if version is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Dataset version not found")
    return version


def _version_read(version: DatasetVersion, used_by: list[str]) -> DatasetVersionRead:
    return DatasetVersionRead.model_validate(version).model_copy(update={"used_by_models": used_by})


def _dataset_read(db: DbSession, dataset: Dataset) -> DatasetRead:
    latest = _get_version(db, dataset, None)
    count = db.scalar(select(func.count(DatasetVersion.id)).where(DatasetVersion.dataset_id == dataset.id)) or 0
    usage = service.models_using(db, [latest.id])
    return DatasetRead(id=dataset.id, display_name=dataset.display_name, purpose=dataset.purpose,
                       created_by_user_id=dataset.created_by_user_id, created_at=dataset.created_at,
                       archived_at=dataset.archived_at, version_count=count,
                       latest_version=_version_read(latest, usage[latest.id]))


def _register_and_commit(db: DbSession, actor: User, content: bytes, action: str, *, dataset: Dataset | None = None,
                         display_name: str | None = None) -> DatasetVersion:
    settings = get_settings()
    try:
        version = service.register(db, settings, actor, content, dataset=dataset, display_name=display_name)
    except storage.DatasetFileError as exc:
        db.rollback()
        raise _error(exc.status, exc.message) from None
    except service.DatasetConflictError as exc:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, exc.message) from None
    try:
        db.flush()
        record_audit(db, actor=actor, action=action, entity_type="dataset", entity_id=version.dataset_id,
                     details={"version": version.version_number, "sha256": version.sha256,
                              "rows": version.row_count, "columns": version.column_count,
                              "size_bytes": version.size_bytes})
        db.commit()
    except IntegrityError:
        db.rollback()
        storage.remove(settings.ml_dataset_dir, version.storage_key)  # compensating cleanup
        raise _error(status.HTTP_409_CONFLICT,
                     "Version could not be registered (identical content or a concurrent upload); retry") from None
    except Exception:
        db.rollback()
        storage.remove(settings.ml_dataset_dir, version.storage_key)  # never leave an unregistered file
        raise
    return version


# --- Upload and versions ------------------------------------------------------------------------


@router.post("", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
def upload_dataset(db: DbSession, actor: Admin, file: Annotated[UploadFile, File()],
                   display_name: Annotated[str, Form()]) -> DatasetRead:
    """Register a new dataset (version 1, status UPLOADED). Validation is a separate, explicit step;
    nothing is trained or activated automatically. The original filename is not stored."""
    try:
        name = _NAME.validate_python(display_name)
    except ValidationError:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT,
                     "display_name may contain letters, digits, spaces and & ' . , / ( ) + - (2-100 characters)") from None
    content = _read_upload(file, get_settings().dataset_max_upload_bytes)
    version = _register_and_commit(db, actor, content, "dataset.uploaded", display_name=name)
    return _dataset_read(db, _get_dataset(db, version.dataset_id))


@router.post("/{dataset_id}/versions", response_model=DatasetVersionRead, status_code=status.HTTP_201_CREATED)
def create_version(dataset_id: int, db: DbSession, actor: Admin, file: Annotated[UploadFile, File()]) -> DatasetVersionRead:
    """Add an immutable new version; earlier versions are never modified."""
    dataset = _get_dataset(db, dataset_id)
    content = _read_upload(file, get_settings().dataset_max_upload_bytes)
    version = _register_and_commit(db, actor, content, "dataset.version_created", dataset=dataset)
    return _version_read(_get_version(db, dataset, version.version_number), [])


@router.get("", response_model=Page[DatasetRead])
def list_datasets(params: Annotated[DatasetListParams, Query()], db: DbSession, _: Reader) -> Page[DatasetRead]:
    stmt = select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc())
    if not params.include_archived:
        stmt = stmt.where(Dataset.archived_at.is_(None))
    if params.q:
        stmt = stmt.where(keyword_condition(params.q, Dataset.display_name))
    if params.status is not None:
        latest = (select(func.max(DatasetVersion.id)).group_by(DatasetVersion.dataset_id))
        stmt = stmt.join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id).where(
            DatasetVersion.id.in_(latest), DatasetVersion.status == params.status)
    items, total = paginate(db, stmt, params)
    return Page[DatasetRead].build([_dataset_read(db, d) for d in items], total, params)


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: int, db: DbSession, _: Reader) -> DatasetRead:
    return _dataset_read(db, _get_dataset(db, dataset_id))


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersionRead])
def list_versions(dataset_id: int, db: DbSession, _: Reader) -> list[DatasetVersionRead]:
    dataset = _get_dataset(db, dataset_id)
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
                          .order_by(DatasetVersion.version_number)).all()
    usage = service.models_using(db, [v.id for v in versions])
    return [_version_read(v, usage[v.id]) for v in versions]


# --- Validation ---------------------------------------------------------------------------------


@router.post("/{dataset_id}/validate", response_model=ValidationReportRead)
def validate_version(dataset_id: int, params: Annotated[VersionParams, Query()], db: DbSession, actor: Admin) -> ValidationReportRead:
    """Run validation for UPLOADED or REJECTED versions (or VALIDATED ones after a feature-schema change).
    The same file and schema always produce the same result."""
    settings = get_settings()
    dataset = _get_dataset(db, dataset_id)
    version = _get_version(db, dataset, params.version)
    now = utcnow()
    if dataset.archived_at is not None or version.status == DatasetStatus.ARCHIVED:
        raise _error(status.HTTP_409_CONFLICT, "Archived datasets cannot be validated")
    if not service.can_validate(version, now):
        raise _error(status.HTTP_409_CONFLICT,
                     f"A {version.status.value} version cannot be revalidated against the same feature schema")

    try:
        previous = service.claim_for_validation(db, version, now)
        db.commit()
    except service.DatasetConflictError as exc:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, exc.message) from None

    try:
        report = service.run_validation(db, settings, version, now)
        outcome = "dataset.validated" if report["training_eligible"] else "dataset.rejected"
        record_audit(db, actor=actor, action=outcome, entity_type="dataset", entity_id=dataset.id,
                     details={"version": version.version_number, "schema_version": report["schema_version"],
                              "rows_total": report.get("rows_total"), "rows_invalid": report.get("rows_invalid"),
                              "fatal_errors": len(report["fatal_errors"]), "warnings": len(report["warnings"]),
                              "integrity_error": bool(report.get("integrity_error"))})
        db.commit()
    except Exception:
        # Compensate: never leave the version stuck in VALIDATING.
        db.rollback()
        service.revert_validation(db, version.id, previous)
        db.commit()
        raise
    version = _get_version(db, dataset, version.version_number)
    return ValidationReportRead(dataset_id=dataset.id, version_number=version.version_number, status=version.status,
                                training_eligible=version.training_eligible, report=version.validation_report)


@router.get("/{dataset_id}/validation", response_model=ValidationReportRead)
def get_validation(dataset_id: int, params: Annotated[VersionParams, Query()], db: DbSession, _: Reader) -> ValidationReportRead:
    dataset = _get_dataset(db, dataset_id)
    version = _get_version(db, dataset, params.version)
    return ValidationReportRead(dataset_id=dataset.id, version_number=version.version_number, status=version.status,
                                training_eligible=version.training_eligible, report=version.validation_report)


# --- Archive and download -------------------------------------------------------------------------


@router.post("/{dataset_id}/archive", response_model=ArchiveResult)
def archive_dataset(dataset_id: int, db: DbSession, actor: Admin) -> ArchiveResult:
    """Retire a dataset. Files and records are kept so models trained on it stay traceable."""
    dataset = _get_dataset(db, dataset_id)
    if dataset.archived_at is not None:
        raise _error(status.HTTP_409_CONFLICT, "Dataset is already archived")
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)).all()
    if any(v.status == DatasetStatus.VALIDATING for v in versions):
        raise _error(status.HTTP_409_CONFLICT, "A version is being validated; try again shortly")
    now = utcnow()
    referenced = sum(1 for used in service.models_using(db, [v.id for v in versions]).values() if used)
    dataset.archived_at, dataset.archived_by_user_id = now, actor.id
    for version in versions:
        version.status, version.training_eligible = DatasetStatus.ARCHIVED, False
    record_audit(db, actor=actor, action="dataset.archived", entity_type="dataset", entity_id=dataset.id,
                 details={"versions": len(versions), "referenced_by_models": referenced})
    db.commit()
    return ArchiveResult(dataset_id=dataset.id, archived_at=now, versions_archived=len(versions),
                         versions_referenced_by_models=referenced)


@router.get("/{dataset_id}/download")
def download_version(dataset_id: int, params: Annotated[VersionParams, Query()], db: DbSession, actor: Admin) -> Response:
    """ADMIN-only, audited, checksum-verified download for offline review."""
    dataset = _get_dataset(db, dataset_id)
    version = _get_version(db, dataset, params.version)
    try:
        content = storage.read_verified(get_settings().ml_dataset_dir, version.storage_key, version.sha256)
    except storage.DatasetFileError as exc:
        raise _error(exc.status, exc.message) from None
    record_audit(db, actor=actor, action="dataset.downloaded", entity_type="dataset", entity_id=dataset.id,
                 details={"version": version.version_number, "sha256": version.sha256})
    db.commit()
    return Response(content=content, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="dataset-{dataset.id}-v{version.version_number}.csv"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
