"""Audit-trail helpers. Rows are added to the caller's session and committed with the change itself.

Metadata policy (README "Audit logs"):
- Credential-like keys (password, secret, token, authorization, api key, hash) are REJECTED by the model: storing
  them is a programming error and the whole transaction fails.
- Business-sensitive keys are REDACTED at write time, at any depth: the key is kept (so an investigator can see
  that, say, a phone number changed) but its value becomes "[redacted]". This covers risk scores,
  model features, salary amounts, bank details, contact details, names and free-text notes.
- The free-text `reason` of attendance corrections and employee status changes is the one documented exception:
  it is the stored justification for those changes. It is kept in the database but withheld from the audit
  query API and exports (see app/services/audit_query.py).
"""

from datetime import date, datetime, time
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User

REDACTED = "[redacted]"
# Exact key names (case-insensitive) whose VALUES are never stored or shown.
REDACT_KEYS = frozenset({
    # model output and inputs
    "risk_score", "latest_score", "current_score", "previous_score", "score", "features", "feature_values",
    # pay
    "salary", "base_salary", "gross_earnings", "total_deductions", "net_pay", "amount",
    # bank details
    "bank_account", "bank_name", "account_number", "iban", "ifsc", "swift",
    # contact details and names
    "phone", "email", "work_email", "full_name",
    # free text (organizational "description" fields of departments, leave types etc. are not personal and are kept)
    "note", "notes", "comment", "comments", "review_comment", "reviewer_notes", "resolution_reason",
    "explanation", "suggested_action", "dismissal_reason", "completion_outcome",
})


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def redact(value: Any) -> Any:
    """Copy of `value` with the values of REDACT_KEYS replaced, recursively through dicts and lists."""
    if isinstance(value, dict):
        return {key: (REDACTED if str(key).lower() in REDACT_KEYS else redact(nested)) for key, nested in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def apply_changes(obj: Any, changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Set each changed attribute on obj and return {field: {"from": old, "to": new}} for real changes."""
    diff: dict[str, dict[str, Any]] = {}
    for field, new_value in changes.items():
        old_value = getattr(obj, field)
        if old_value != new_value:
            setattr(obj, field, new_value)
            diff[field] = {"from": _jsonable(old_value), "to": _jsonable(new_value)}
    return diff


def record_audit(
    db: Session,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None,
    details: dict[str, Any] | None = None,
) -> None:
    """`actor` is None only for unauthenticated events (e.g. a failed login for an unknown account)."""
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor is not None else None,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            details=None if details is None else redact(details),
        )
    )
