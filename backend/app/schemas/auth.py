from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.core.security import BCRYPT_MAX_BYTES, password_byte_length
from app.schemas.user import UserRead

PASSWORD_MIN_LENGTH = 12


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _validate_new_password(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Password must not contain null characters")
    if not value.strip():
        raise ValueError("Password must not be blank")
    if password_byte_length(value) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password must be at most {BCRYPT_MAX_BYTES} bytes when UTF-8 encoded "
            "(fewer characters if it contains non-ASCII characters)"
        )
    return value


NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]

NewPassword = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=BCRYPT_MAX_BYTES),
    AfterValidator(_validate_new_password),
]


class RegisterRequest(BaseModel):
    # extra="forbid": a "role" (or any other unexpected field) is rejected, never silently honoured.
    model_config = ConfigDict(extra="forbid")

    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    email: NormalizedEmail
    password: NewPassword


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    # No strength rules at login (they apply at registration); just bound the input size.
    password: Annotated[str, Field(min_length=1, max_length=256)]


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: NewPassword


class ChangePasswordResponse(BaseModel):
    detail: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserRead


class LogoutResponse(BaseModel):
    detail: str
