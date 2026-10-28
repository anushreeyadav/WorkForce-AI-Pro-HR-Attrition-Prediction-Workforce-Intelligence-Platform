from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from app.schemas.common import PaginationParams
from app.services.notifications import priority_of


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    title: str
    message: str
    entity_type: str | None
    entity_id: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime

    @computed_field
    @property
    def priority(self) -> Literal["URGENT", "HIGH", "NORMAL"]:
        """Derived from the category for display/sorting; additive field, not stored."""
        return priority_of(self.category)


class NotificationListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    unread_only: bool = False
    # "recent" (default, unchanged): newest first. "priority": unread first, then URGENT > HIGH > NORMAL,
    # then newest, then id (stable tie-breaker).
    sort: Literal["recent", "priority"] = "recent"
