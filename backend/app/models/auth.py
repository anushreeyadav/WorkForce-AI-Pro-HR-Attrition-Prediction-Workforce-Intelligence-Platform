"""Server-side record of revoked access tokens (Sub-prompt 31).

Access tokens stay stateless JWTs; this table only lists tokens that were explicitly ended before their expiry
(logout). A row holds the token's unique id (jti), whose account it was and when it would have expired anyway, never
the token itself. Rows whose expires_at has passed can be deleted: the signature check already rejects those tokens.
Revoking every session of an account at once (deactivation, role change, "revoke sessions") uses
users.token_version instead, so it needs no row per token.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
