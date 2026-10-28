from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import Base, UTCDateTime, utcnow

# Keys that must never be written to the audit trail (case-insensitive substring match).
SENSITIVE_KEY_PARTS = ("password", "secret", "token", "authorization", "api_key", "apikey", "hash")


def _find_sensitive_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS):
                return str(key)
            if found := _find_sensitive_key(nested):
                return found
    elif isinstance(value, list):
        for item in value:
            if found := _find_sensitive_key(item):
                return found
    return None


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Attribute is "details" because "metadata" is reserved on declarative classes.
    details: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, nullable=False, index=True
    )

    @validates("details")
    def _reject_sensitive_details(
        self, key: str, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if sensitive := _find_sensitive_key(value):
            raise ValueError(f"Audit metadata must not contain sensitive key {sensitive!r}")
        return value

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r}>"

# Activity-feed query indexes (see README, "Feed query indexes"): each serves a range/keyset filter on this
# timestamp with ORDER BY (timestamp, id) LIMIT n. InnoDB secondary indexes include the primary key.
Index("ix_audit_logs_action_created_at", AuditLog.action, AuditLog.created_at)
