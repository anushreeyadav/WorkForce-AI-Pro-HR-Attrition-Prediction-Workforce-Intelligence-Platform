import math
import re
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def trimmed(min_length: int, max_length: int) -> type[str]:
    """String type that strips surrounding whitespace before length checks."""
    return Annotated[  # type: ignore[return-value]
        str, StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length)
    ]


# Letters (any script), digits, spaces and common punctuation; must start with a letter or digit.
_DISPLAY_NAME_PATTERN = re.compile(r"^[^\W_][\w &'.,/()+\-]*$")


def display_name(label: str, min_length: int = 2, max_length: int = 100) -> type[str]:
    """Trimmed name with collapsed internal whitespace and a restricted character set."""

    def _validate(value: str) -> str:
        value = " ".join(value.split())
        if not _DISPLAY_NAME_PATTERN.fullmatch(value):
            raise ValueError(f"{label} may contain letters, digits, spaces and & ' . , / ( ) + -")
        return value

    return Annotated[trimmed(min_length, max_length), AfterValidator(_validate)]  # type: ignore[return-value]


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PaginationParams) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=math.ceil(total / params.page_size) if total else 0,
        )
