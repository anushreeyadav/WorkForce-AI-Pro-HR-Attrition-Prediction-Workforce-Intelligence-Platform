"""Dataset registry operations: registration, versioning, validation runs, training resolution.

Database changes go into the caller's session (the router commits once, with the audit row). When a file
was stored but the database transaction then fails, the router removes the file (compensating cleanup);
anything left behind by a crash is removed later by dataset_storage.cleanup_orphans.
"""

import io
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AttritionModelVersion,
    Dataset,
    DatasetPurpose,
    DatasetStatus,
    DatasetVersion,
    Department,
    User,
)
from app.services import dataset_storage as storage
from ml.attrition.dataset_validation import ValidationConfig, validate_dataset
from ml.attrition.features import FEATURE_SCHEMA_VERSION

STALE_VALIDATION = timedelta(minutes=15)


class DatasetConflictError(Exception):
    def __init__(self, message: str, code: str = "conflict") -> None:
        super().__init__(message)
        self.message, self.code = message, code


def find_by_checksum(db: Session, sha256: str) -> DatasetVersion | None:
    return db.scalar(select(DatasetVersion).where(DatasetVersion.sha256 == sha256))


def register(db: Session, settings: Settings, actor: User, content: bytes, *, dataset: Dataset | None = None,
             display_name: str | None = None) -> DatasetVersion:
    """Inspect, store and register content as a new dataset (version 1) or a new version of `dataset`.

    The caller must commit, or call storage.remove(storage_key) if the commit fails.
    """
    shape = storage.inspect_csv(content)
    checksum = storage.sha256_bytes(content)
    existing = find_by_checksum(db, checksum)
    if existing is not None:
        raise DatasetConflictError(
            f"Identical content is already registered as dataset {existing.dataset_id} version {existing.version_number}",
            code="duplicate_content")

    if dataset is None:
        dataset = Dataset(display_name=display_name, purpose=DatasetPurpose.ATTRITION_TRAINING,
                          created_by_user_id=actor.id)
        db.add(dataset)
        db.flush()
        number = 1
    else:
        if dataset.archived_at is not None:
            raise DatasetConflictError("Archived datasets cannot receive new versions", code="archived")
        number = (db.scalar(select(func.max(DatasetVersion.version_number))
                            .where(DatasetVersion.dataset_id == dataset.id)) or 0) + 1

    key = storage.store(settings.ml_dataset_dir, content)
    version = DatasetVersion(dataset_id=dataset.id, version_number=number, storage_key=key, sha256=checksum,
                             size_bytes=len(content), row_count=shape.rows, column_count=shape.columns,
                             status=DatasetStatus.UPLOADED, training_eligible=False, uploaded_by_user_id=actor.id)
    db.add(version)
    return version


def read_frame(settings: Settings, version: DatasetVersion) -> pd.DataFrame:
    """Checksum-verified read, all cells as raw strings (no silent type coercion)."""
    content = storage.read_verified(settings.ml_dataset_dir, version.storage_key, version.sha256)
    return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig",
                       skip_blank_lines=True)


def validation_config(settings: Settings) -> ValidationConfig:
    return ValidationConfig(min_rows=settings.ml_min_training_rows, min_class_count=settings.ml_min_class_count)


def known_categories(db: Session) -> dict[str, set[str]]:
    return {"department": {str(i) for i in db.scalars(select(Department.id))}}


def can_validate(version: DatasetVersion, now: datetime) -> bool:
    if version.status in (DatasetStatus.UPLOADED, DatasetStatus.REJECTED):
        return True
    if version.status == DatasetStatus.VALIDATED:
        return version.schema_version != FEATURE_SCHEMA_VERSION  # only after a schema change
    if version.status == DatasetStatus.VALIDATING:
        started = version.validation_started_at
        return started is None or now - started > STALE_VALIDATION  # recover an abandoned run
    return False


def claim_for_validation(db: Session, version: DatasetVersion, now: datetime) -> DatasetStatus:
    """Conditional transition to VALIDATING; returns the previous status for compensation."""
    previous = version.status
    changed = db.execute(
        update(DatasetVersion).where(DatasetVersion.id == version.id, DatasetVersion.status == previous)
        .values(status=DatasetStatus.VALIDATING, validation_started_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise DatasetConflictError("This dataset version is being validated or changed concurrently")
    return previous


def apply_report(db: Session, version: DatasetVersion, report: dict, now: datetime) -> None:
    eligible = bool(report["training_eligible"])
    db.execute(
        update(DatasetVersion).where(DatasetVersion.id == version.id, DatasetVersion.status == DatasetStatus.VALIDATING)
        .values(status=DatasetStatus.VALIDATED if eligible else DatasetStatus.REJECTED, training_eligible=eligible,
                validation_report=report, schema_version=report["schema_version"], validated_at=now)
        .execution_options(synchronize_session=False)
    )


def run_validation(db: Session, settings: Settings, version: DatasetVersion, now: datetime) -> dict:
    try:
        frame = read_frame(settings, version)
    except storage.DatasetFileError as exc:
        report = {"schema_version": FEATURE_SCHEMA_VERSION, "validated_at": now.isoformat(),
                  "fatal_errors": [exc.message], "warnings": [], "training_eligible": False,
                  "integrity_error": True}
        apply_report(db, version, report, now)
        return report
    report = validate_dataset(frame, validation_config(settings), now, known_categories(db))
    apply_report(db, version, report, now)
    return report


def revert_validation(db: Session, version_id: int, previous: DatasetStatus) -> None:
    db.execute(update(DatasetVersion).where(DatasetVersion.id == version_id,
                                            DatasetVersion.status == DatasetStatus.VALIDATING)
               .values(status=previous).execution_options(synchronize_session=False))


def models_using(db: Session, version_ids: list[int]) -> dict[int, list[str]]:
    usage: dict[int, list[str]] = {vid: [] for vid in version_ids}
    if version_ids:
        for vid, model_version in db.execute(
            select(AttritionModelVersion.dataset_version_id, AttritionModelVersion.model_version)
            .where(AttritionModelVersion.dataset_version_id.in_(version_ids))
            .order_by(AttritionModelVersion.trained_at)
        ):
            usage[vid].append(model_version)
    return usage


def resolve_for_training(db: Session, dataset_id: int, version_number: int | None) -> DatasetVersion:
    """Server-side eligibility: the registry decides, never the client."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise DatasetConflictError("Dataset not found", code="not_found")
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    stmt = (stmt.where(DatasetVersion.version_number == version_number) if version_number is not None
            else stmt.order_by(DatasetVersion.version_number.desc()).limit(1))
    version = db.scalar(stmt)
    if version is None:
        raise DatasetConflictError("Dataset version not found", code="not_found")
    if dataset.archived_at is not None or version.status == DatasetStatus.ARCHIVED:
        raise DatasetConflictError("Archived datasets cannot be used for training", code="archived")
    if version.status != DatasetStatus.VALIDATED or not version.training_eligible:
        raise DatasetConflictError("Only VALIDATED, training-eligible dataset versions can be used", code="not_eligible")
    if version.schema_version != FEATURE_SCHEMA_VERSION:
        raise DatasetConflictError("Dataset was validated against a different feature schema; revalidate it",
                                   code="schema_mismatch")
    return version
