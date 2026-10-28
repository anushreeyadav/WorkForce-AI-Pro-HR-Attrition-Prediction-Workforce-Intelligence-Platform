from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EmploymentStatus, UserRole
from app.schemas.common import PaginationParams, trimmed


class UserRead(BaseModel):
    """Public user representation. Deliberately has no password_hash field, so it can never be serialized."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    role: UserRole
    is_active: bool


# --- Account administration (ADMIN only; see app/api/users.py) ---------------------------------------------------------


class UserAdminRead(UserRead):
    """What an administrator needs to manage a login. Never the password hash, tokens or token ids."""

    last_login_at: datetime | None
    created_at: datetime
    employee_id: int | None = None
    employee_status: EmploymentStatus | None = None


class UserListParams(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    q: trimmed(1, 100) | None = Field(default=None, description="Matches name or email (case-insensitive substring)")
    role: UserRole | None = None
    is_active: bool | None = None


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class UserRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole
