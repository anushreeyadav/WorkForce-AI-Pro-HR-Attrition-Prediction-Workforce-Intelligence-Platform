"""Dataset validator and storage layer (no API). All data is SYNTHETIC."""

import io
import os
import time
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.services import dataset_storage as storage
from ml.attrition.dataset_validation import MAX_ROW_ERRORS, ValidationConfig, validate_dataset
from tests.synthetic_attrition import synthetic_dataset

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
CONFIG = ValidationConfig(min_rows=50, min_class_count=10)


def as_strings(frame: pd.DataFrame) -> pd.DataFrame:
    """Round-trip through CSV exactly as the service reads stored files."""
    return pd.read_csv(io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False)


def validate(frame: pd.DataFrame, known=None) -> dict:
    return validate_dataset(as_strings(frame), CONFIG, NOW, known)


# --- Validator ------------------------------------------------------------------------------------


def test_valid_dataset_report() -> None:
    report = validate(synthetic_dataset())
    assert report["training_eligible"] is True and report["fatal_errors"] == []
    assert (report["rows_total"], report["rows_valid"], report["rows_invalid"]) == (200, 200, 0)
    assert report["missing_by_feature"]["late_rate_90d"] == 20  # valid: nullable feature
    assert report["target_distribution"] == {"0": 82, "1": 118}
    assert report["schema_version"] == "attrition-features-v1"
    assert report["validated_at"] == NOW.isoformat()


def test_deterministic() -> None:
    assert validate(synthetic_dataset()) == validate(synthetic_dataset())


@pytest.mark.parametrize(
    "column, message",
    [
        ("full_name", "Prohibited columns"),
        ("work_email", "Prohibited columns"),
        ("employee_code", "Prohibited columns"),
        ("date_of_birth", "Prohibited columns"),
        ("manager_name", "Prohibited columns"),          # caught by sensitive fragment, not an exact name
        ("previous_salary_band", "Prohibited columns"),
        ("team_size", "outside the approved feature allowlist"),  # unfamiliar is not assumed safe
    ],
)
def test_column_allowlist(column, message) -> None:
    report = validate(synthetic_dataset().assign(**{column: "x"}))
    assert report["training_eligible"] is False
    assert any(message in error and column in error for error in report["fatal_errors"])


def test_missing_required_column() -> None:
    report = validate(synthetic_dataset().drop(columns=["department"]))
    assert report["fatal_errors"] == ["Missing required columns: department"]


def test_invalid_values_are_reported_never_coerced() -> None:
    frame = synthetic_dataset().astype(object)
    frame.loc[0, "tenure_months"] = "SECRET-VALUE-42"  # not_numeric
    frame.loc[1, "late_rate_90d"] = 1.5             # out_of_range
    frame.loc[2, "tenure_months"] = ""              # missing_required
    frame.loc[3, "department"] = "R&D Team!"        # invalid_category
    frame.loc[4, "attrited"] = "yes"                # invalid_target
    frame.loc[5, "recorded_absence_days_90d"] = "inf"
    report = validate(frame)

    assert report["training_eligible"] is False
    assert report["rows_invalid"] == 6 and report["row_error_total"] == 6
    assert report["row_errors"] == [
        {"row": 1, "field": "tenure_months", "issue": "not_numeric"},
        {"row": 3, "field": "tenure_months", "issue": "missing_required"},
        {"row": 2, "field": "late_rate_90d", "issue": "out_of_range"},
        {"row": 6, "field": "recorded_absence_days_90d", "issue": "not_numeric"},
        {"row": 4, "field": "department", "issue": "invalid_category"},
        {"row": 5, "field": "attrited", "issue": "invalid_target"},
    ]
    assert report["not_numeric_by_feature"]["tenure_months"] == 1
    # Raw values never appear in the report.
    assert "SECRET-VALUE-42" not in str(report) and "R&D Team!" not in str(report)


def test_row_error_list_is_capped_but_counted() -> None:
    frame = synthetic_dataset().assign(tenure_months="bad")
    report = validate(frame)
    assert len(report["row_errors"]) == MAX_ROW_ERRORS
    assert report["row_error_total"] == 200


def test_duplicates_reported_not_removed() -> None:
    data = synthetic_dataset()
    frame = pd.concat([data, data.head(30)], ignore_index=True)
    report = validate(frame)
    assert report["rows_total"] == 230 and report["duplicate_rows"] == 30
    assert report["distinct_valid_rows"] == 200
    assert report["training_eligible"] is True
    assert any("30 exact duplicate row(s)" in w and "more than 10%" in w for w in report["warnings"])


def test_too_few_distinct_rows_after_duplicates() -> None:
    small = synthetic_dataset().head(30)
    report = validate(pd.concat([small, small], ignore_index=True))  # 60 rows, 30 distinct
    assert report["training_eligible"] is False
    assert "At least 50 distinct valid rows are required (found 30)" in report["fatal_errors"]


@pytest.mark.parametrize(
    "frame, message",
    [
        (synthetic_dataset().assign(attrited=1), "Both target classes"),
        (pd.concat([synthetic_dataset().query("attrited == 0"), synthetic_dataset().query("attrited == 1").head(5)]),
         "Each target class needs at least 10"),
        (synthetic_dataset().head(0), "no data rows"),
    ],
)
def test_class_and_size_rules(frame, message) -> None:
    report = validate(frame)
    assert report["training_eligible"] is False
    assert any(message in error for error in report["fatal_errors"])


def test_missingness_and_unknown_category_warnings() -> None:
    frame = synthetic_dataset()
    frame.loc[frame.index[:150], "avg_overtime_minutes_90d"] = None
    report = validate(frame, known={"department": {"1"}})
    assert report["training_eligible"] is True
    assert "avg_overtime_minutes_90d: 150 of 200 values missing (over 50%)" in report["warnings"]
    assert any(w.startswith("department:") and "unknown categories" in w for w in report["warnings"])


# --- CSV inspection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "empty"),
        (b"a,b\n1,\x002\n", "binary data"),
        ("tenure_months,department\n1,é".encode("latin-1"), "not valid UTF-8"),
        (b"a,b\n1,2,3\n", "Line 2: expected 2 fields, found 3"),
        (b'a,b\n"unterminated,2\n', "not a well-formed CSV"),
        (b"\n\n", "no header row"),
        (b"name@example.com,b\n1,2\n", "Column names may contain only"),
        (("a,b\n1," + "x" * 201 + "\n").encode(), "exceeds 200 characters"),
        ((",".join(f"c{i}" for i in range(51)) + "\n").encode(), "Too many columns"),
    ],
    ids=["empty", "binary", "encoding", "ragged", "malformed", "no-header", "header-pii", "long-field", "columns"],
)
def test_inspect_rejects_bad_files(content, message) -> None:
    with pytest.raises(storage.DatasetFileError, match=message):
        storage.inspect_csv(content)


def test_inspect_counts_rows_and_accepts_bom() -> None:
    shape = storage.inspect_csv("﻿a,b\n1,2\n\n3,4\n".encode("utf-8"))
    assert (shape.rows, shape.columns) == (2, 2)


def test_row_limit(monkeypatch) -> None:
    monkeypatch.setattr(storage, "MAX_ROWS", 3)
    with pytest.raises(storage.DatasetFileError, match="Too many rows"):
        storage.inspect_csv(b"a\n1\n2\n3\n4\n")


# --- Storage -------------------------------------------------------------------------------------


def test_store_and_verified_read(tmp_path) -> None:
    key = storage.store(tmp_path, b"a,b\n1,2\n")
    assert len(key) == 32 and (tmp_path / f"{key}.csv").is_file()
    assert not list(tmp_path.glob("*.partial"))
    assert storage.read_verified(tmp_path, key, storage.sha256_bytes(b"a,b\n1,2\n")) == b"a,b\n1,2\n"


def test_tampered_missing_and_symlinked_files(tmp_path) -> None:
    key = storage.store(tmp_path, b"a\n1\n")
    good = storage.sha256_bytes(b"a\n1\n")
    (tmp_path / f"{key}.csv").write_bytes(b"a\n2\n")
    with pytest.raises(storage.DatasetFileError, match="integrity check"):
        storage.read_verified(tmp_path, key, good)
    with pytest.raises(storage.DatasetFileError, match="missing"):
        storage.read_verified(tmp_path, "0" * 32, good)

    outside = tmp_path.parent / "outside.csv"
    outside.write_bytes(b"a\n1\n")
    link_key = "f" * 32
    try:
        os.symlink(outside, tmp_path / f"{link_key}.csv")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted for this user on this platform")
    with pytest.raises(storage.DatasetFileError, match="integrity check"):
        storage.read_verified(tmp_path, link_key, good)


@pytest.mark.parametrize("key", ["../evil", "ABCDEF" * 6, "0" * 31, "0" * 32 + "/x", ""])
def test_storage_keys_are_validated(tmp_path, key) -> None:
    with pytest.raises(storage.DatasetFileError):
        storage.path_for(tmp_path, key)


def test_cleanup_removes_only_old_orphans(tmp_path) -> None:
    registered = storage.store(tmp_path, b"a\n1\n")
    orphan = storage.store(tmp_path, b"a\n2\n")
    partial = tmp_path / ("9" * 32 + ".partial")
    partial.write_bytes(b"half")
    fresh_orphan = storage.store(tmp_path, b"a\n3\n")
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep me")
    old = time.time() - 7200
    for path in (tmp_path / f"{registered}.csv", tmp_path / f"{orphan}.csv", partial, unrelated):
        os.utime(path, (old, old))

    removed = storage.cleanup_orphans(tmp_path, {registered}, datetime.now(UTC), timedelta(hours=1))
    assert removed == {"partial": 1, "unregistered": 1}
    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {f"{registered}.csv", f"{fresh_orphan}.csv", "notes.txt"}


def test_symlinked_file_refused_on_every_platform(tmp_path, monkeypatch) -> None:
    """Covers the symlink branch even where creating real symlinks is not permitted (e.g. Windows)."""
    from pathlib import Path

    key = storage.store(tmp_path, b"a\n1\n")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == f"{key}.csv" or real_is_symlink(self))
    with pytest.raises(storage.DatasetFileError, match="integrity check"):
        storage.read_verified(tmp_path, key, storage.sha256_bytes(b"a\n1\n"))
