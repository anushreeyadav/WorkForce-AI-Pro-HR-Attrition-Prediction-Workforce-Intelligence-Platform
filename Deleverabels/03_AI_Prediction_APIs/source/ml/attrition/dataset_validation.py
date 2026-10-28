"""Deterministic validation of an attrition training dataset against the feature schema in features.py.

Works on raw strings (exactly as read from the CSV) so nothing is silently coerced: an empty cell is
"missing", an unparseable number is "not_numeric", never zero or an absence. The report contains only
counts, column names, row numbers and issue codes; it never contains cell values.

Outcome classes:
- fatal_errors: the dataset cannot be used for training (status REJECTED).
- warnings: needs human review but does not block training.
- missing values in nullable features: valid; the model imputes them with a missing-indicator.

Duplicate policy: exact duplicate rows are reported (never removed from the stored file). Training removes
them in memory and counts them; the dataset is ineligible if fewer than the minimum distinct rows remain.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ml.attrition.features import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    FEATURES,
    FORBIDDEN_COLUMNS,
    TARGET,
)

MAX_ROW_ERRORS = 50
CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,50}$")
HIGH_MISSINGNESS = 0.5
HIGH_DUPLICATE_SHARE = 0.1
# Fragments that mark a column as sensitive or identifying even if its exact name is not listed.
SENSITIVE_FRAGMENTS = ("name", "email", "phone", "password", "gender", "sex", "birth", "religion", "ethnic",
                       "race", "nationality", "disab", "health", "pregnan", "marital", "salary", "address")


@dataclass(frozen=True)
class ValidationConfig:
    min_rows: int = 50
    min_class_count: int = 10


def _classify_columns(columns: list[str]) -> tuple[list[str], list[str], list[str]]:
    normalized = [c.strip().lower() for c in columns]
    allowed = set(FEATURE_COLUMNS) | {TARGET}
    prohibited = sorted({c for c in normalized if c in FORBIDDEN_COLUMNS
                         or (c not in allowed and any(f in c for f in SENSITIVE_FRAGMENTS))})
    unknown = sorted({c for c in normalized if c not in allowed and c not in prohibited})
    missing = sorted(allowed - set(normalized))
    return prohibited, unknown, missing


def _parse_number(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def validate_dataset(frame: pd.DataFrame, config: ValidationConfig, now: datetime,
                     known_categories: dict[str, set[str]] | None = None) -> dict:
    """frame must contain only strings (read with dtype=str, keep_default_na=False)."""
    report: dict = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "validated_at": now.isoformat(),
        "rows_total": int(len(frame)),
        "columns": [str(c) for c in frame.columns],
        "fatal_errors": [],
        "warnings": [],
    }
    fatal, warnings = report["fatal_errors"], report["warnings"]

    prohibited, unknown, missing = _classify_columns([str(c) for c in frame.columns])
    if prohibited:
        fatal.append(f"Prohibited columns (identifiers or sensitive characteristics): {', '.join(prohibited)}")
    if unknown:
        fatal.append(f"Columns outside the approved feature allowlist: {', '.join(unknown)}")
    if missing:
        fatal.append(f"Missing required columns: {', '.join(missing)}")
    if len(set(c.strip().lower() for c in frame.columns)) != len(frame.columns):
        fatal.append("Duplicate column names")
    if fatal:
        report.update(rows_valid=0, rows_invalid=report["rows_total"], training_eligible=False,
                      row_errors=[], row_error_total=0)
        return report

    data = frame.rename(columns=lambda c: c.strip().lower())
    row_errors: list[dict] = []
    error_total = 0
    invalid_rows: set[int] = set()
    missing_by_feature = {name: 0 for name in FEATURE_COLUMNS}
    not_numeric = {f.name: 0 for f in FEATURES if f.kind == "numeric"}
    out_of_range = {f.name: 0 for f in FEATURES if f.kind == "numeric"}
    invalid_category = {f.name: 0 for f in FEATURES if f.kind == "categorical"}
    unknown_category = {f.name: 0 for f in FEATURES if f.kind == "categorical"}

    def flag(index: int, field: str, issue: str) -> None:
        nonlocal error_total
        error_total += 1
        invalid_rows.add(index)
        if len(row_errors) < MAX_ROW_ERRORS:
            row_errors.append({"row": index + 1, "field": field, "issue": issue})  # 1-based data row

    for spec in FEATURES:
        column = data[spec.name].astype(str).str.strip()
        for index, raw in enumerate(column):
            if raw == "":
                missing_by_feature[spec.name] += 1
                if not spec.nullable:
                    flag(index, spec.name, "missing_required")
                continue
            if spec.kind == "numeric":
                value = _parse_number(raw)
                if value is None:
                    not_numeric[spec.name] += 1
                    flag(index, spec.name, "not_numeric")
                elif (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
                    out_of_range[spec.name] += 1
                    flag(index, spec.name, "out_of_range")
            else:
                if not CATEGORY_PATTERN.fullmatch(raw):
                    invalid_category[spec.name] += 1
                    flag(index, spec.name, "invalid_category")
                elif known_categories and spec.name in known_categories and raw not in known_categories[spec.name]:
                    unknown_category[spec.name] += 1

    target = data[TARGET].astype(str).str.strip()
    for index, raw in enumerate(target):
        if raw not in ("0", "1"):
            flag(index, TARGET, "missing_target" if raw == "" else "invalid_target")

    rows_total = report["rows_total"]
    valid_mask = [i not in invalid_rows for i in range(rows_total)]
    valid = data.loc[valid_mask, FEATURE_COLUMNS + [TARGET]].apply(lambda s: s.astype(str).str.strip())
    duplicate_rows = int(valid.duplicated().sum())
    distinct = valid.drop_duplicates()
    distribution = {"0": int((distinct[TARGET] == "0").sum()), "1": int((distinct[TARGET] == "1").sum())}

    report.update(
        rows_valid=int(sum(valid_mask)),
        rows_invalid=len(invalid_rows),
        missing_by_feature=missing_by_feature,
        not_numeric_by_feature=not_numeric,
        out_of_range_by_feature=out_of_range,
        invalid_category_by_feature=invalid_category,
        unknown_category_by_feature=unknown_category,
        duplicate_rows=duplicate_rows,
        distinct_valid_rows=int(len(distinct)),
        target_distribution=distribution,
        row_errors=row_errors,
        row_error_total=error_total,
    )

    if rows_total == 0:
        fatal.append("The dataset has no data rows")
    if invalid_rows:
        fatal.append(f"{len(invalid_rows)} row(s) contain invalid values ({error_total} problem(s)); fix and upload a new version")
    if len(distinct) < config.min_rows:
        fatal.append(f"At least {config.min_rows} distinct valid rows are required (found {len(distinct)})")
    if min(distribution.values()) == 0:
        fatal.append("Both target classes (attrited = 0 and 1) are required")
    elif min(distribution.values()) < config.min_class_count:
        fatal.append(f"Each target class needs at least {config.min_class_count} distinct rows")

    if duplicate_rows:
        share = duplicate_rows / max(rows_total, 1)
        message = f"{duplicate_rows} exact duplicate row(s); training uses distinct rows only"
        warnings.append(message + (" (more than 10% of rows: review the export)" if share > HIGH_DUPLICATE_SHARE else ""))
    for name, count in missing_by_feature.items():
        if rows_total and count / rows_total > HIGH_MISSINGNESS:
            warnings.append(f"{name}: {count} of {rows_total} values missing (over 50%)")
    for name, count in unknown_category.items():
        if count:
            warnings.append(f"{name}: {count} value(s) not matching current records; treated as unknown categories")

    report["training_eligible"] = not fatal
    return report
