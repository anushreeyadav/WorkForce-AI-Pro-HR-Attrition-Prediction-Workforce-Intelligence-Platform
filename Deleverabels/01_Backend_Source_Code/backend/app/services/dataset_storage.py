"""Dataset file handling: structural CSV inspection, atomic storage, verified reads, orphan cleanup.

Files live only in ML_DATASET_DIR as <storage_key>.csv, where storage_key is a server-generated uuid4 hex.
Writing: <key>.partial is created exclusively, fully written and fsynced, then hard-linked to <key>.csv
(a link never overwrites) and the partial removed, so a half-written file never has a .csv name.
Reading: the path is rebuilt from the key, must not be a symbolic link, and its SHA-256 must match the
registry before any parsing.
"""

import csv
import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

MAX_ROWS = 200_000
MAX_COLUMNS = 50
MAX_FIELD_LENGTH = 200
HEADER_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ \-]{0,63}$")
KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ORPHAN_PATTERN = re.compile(r"^([0-9a-f]{32})\.(csv|partial)$")


class DatasetFileError(Exception):
    """A rejected upload or unusable stored file. Messages never contain file contents or server paths."""

    def __init__(self, message: str, status: int = 422, code: str = "invalid_file") -> None:
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@dataclass(frozen=True)
class CsvShape:
    rows: int
    columns: int


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def inspect_csv(content: bytes) -> CsvShape:
    """Structural checks on the actual content (not the filename or MIME type)."""
    if not content:
        raise DatasetFileError("The file is empty")
    if b"\x00" in content:
        raise DatasetFileError("The file contains binary data; only UTF-8 CSV text is accepted")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DatasetFileError("The file is not valid UTF-8 text") from None

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if not header or all(not cell.strip() for cell in header):
            raise DatasetFileError("The file has no header row")
        if len(header) > MAX_COLUMNS:
            raise DatasetFileError(f"Too many columns (maximum {MAX_COLUMNS})")
        if any(not HEADER_PATTERN.fullmatch(cell.strip()) for cell in header):
            raise DatasetFileError("Column names may contain only letters, digits, spaces, _ and - (max 64 characters)")
        rows = 0
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue  # blank line
            if len(row) != len(header):
                raise DatasetFileError(f"Line {line_number}: expected {len(header)} fields, found {len(row)}")
            if any(len(cell) > MAX_FIELD_LENGTH for cell in row):
                raise DatasetFileError(f"Line {line_number}: a field exceeds {MAX_FIELD_LENGTH} characters")
            rows += 1
            if rows > MAX_ROWS:
                raise DatasetFileError(f"Too many rows (maximum {MAX_ROWS})")
    except csv.Error:
        raise DatasetFileError("The file is not a well-formed CSV") from None
    return CsvShape(rows=rows, columns=len(header))


def _base(storage_dir: Path) -> Path:
    base = Path(storage_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def path_for(storage_dir: Path, storage_key: str) -> Path:
    if not KEY_PATTERN.fullmatch(storage_key):
        raise DatasetFileError("Invalid storage key", status=500, code="integrity_error")
    base = Path(storage_dir).resolve()
    path = base / f"{storage_key}.csv"
    if path.parent != base:
        raise DatasetFileError("Invalid storage key", status=500, code="integrity_error")
    return path


def store(storage_dir: Path, content: bytes) -> str:
    """Atomically store content under a fresh key; returns the key. Never overwrites anything."""
    base = _base(storage_dir)
    key = uuid.uuid4().hex
    partial, final = base / f"{key}.partial", base / f"{key}.csv"
    try:
        with open(partial, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(partial, final)  # fails if final exists; never replaces a file
    finally:
        partial.unlink(missing_ok=True)
    return key


def remove(storage_dir: Path, storage_key: str) -> None:
    """Compensating cleanup for a file whose database registration failed."""
    path_for(storage_dir, storage_key).unlink(missing_ok=True)


def read_verified(storage_dir: Path, storage_key: str, expected_sha256: str) -> bytes:
    path = path_for(storage_dir, storage_key)
    if path.is_symlink():
        raise DatasetFileError("Stored dataset file failed the integrity check", status=409, code="integrity_error")
    if not path.is_file():
        raise DatasetFileError("Stored dataset file is missing", status=409, code="integrity_error")
    content = path.read_bytes()
    if sha256_bytes(content) != expected_sha256:
        raise DatasetFileError("Stored dataset file failed the integrity check", status=409, code="integrity_error")
    return content


def cleanup_orphans(storage_dir: Path, registered_keys: set[str], now: datetime,
                    min_age: timedelta = timedelta(hours=1)) -> dict[str, int]:
    """Remove abandoned files older than min_age: *.partial files, and *.csv files with no registry row.

    Only server-named files are considered; registered datasets are never removed.
    """
    base = Path(storage_dir).resolve()
    removed = {"partial": 0, "unregistered": 0}
    if not base.is_dir():
        return removed
    cutoff = now.timestamp() - min_age.total_seconds()
    for entry in base.iterdir():
        match = ORPHAN_PATTERN.fullmatch(entry.name)
        if not match or entry.is_symlink() or not entry.is_file() or entry.stat().st_mtime > cutoff:
            continue
        key, suffix = match.groups()
        if suffix == "partial":
            entry.unlink(missing_ok=True)
            removed["partial"] += 1
        elif key not in registered_keys:
            entry.unlink(missing_ok=True)
            removed["unregistered"] += 1
    return removed
