from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Integer, String, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.employee import Employee


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Only a bcrypt hash is ever stored here; never expose it in API schemas.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False, default=UserRole.EMPLOYEE, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Carried in each access token ("ver" claim). Incrementing it ends every session of the account at once
    # (deactivation, role change, administrator "revoke sessions"); tokens with another value are rejected.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    employee: Mapped["Employee | None"] = relationship(back_populates="user", uselist=False)

    @validates("email")
    def _normalize_email(self, key: str, value: str) -> str:
        return value.strip().lower()

    def __repr__(self) -> str:
        return f"<User id={self.id} role={self.role}>"
