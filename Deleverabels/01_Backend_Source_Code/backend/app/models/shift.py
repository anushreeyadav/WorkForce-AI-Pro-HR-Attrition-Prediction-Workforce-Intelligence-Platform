from datetime import time

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, Time, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

MINUTES_PER_DAY = 24 * 60
MAX_BREAK_MINUTES = 240


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


class Shift(TimestampMixin, Base):
    """A named working window. Times are wall-clock times in the organization time zone (ORG_TIMEZONE).

    If end_time is earlier than start_time the shift is overnight and ends on the following day.
    """

    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("start_time <> end_time", name="distinct_times"),
        CheckConstraint(
            f"break_minutes >= 0 AND break_minutes <= {MAX_BREAK_MINUTES}", name="break_minutes_range"
        ),
        CheckConstraint(
            f"overtime_threshold_minutes > 0 AND overtime_threshold_minutes <= {MINUTES_PER_DAY}",
            name="overtime_threshold_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Worked minutes per day after which overtime accrues.
    overtime_threshold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=480)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    @property
    def is_overnight(self) -> bool:
        return self.end_time < self.start_time

    @property
    def duration_minutes(self) -> int:
        """Scheduled span from start to end, including any break."""
        return (_minutes(self.end_time) - _minutes(self.start_time)) % MINUTES_PER_DAY

    @property
    def scheduled_work_minutes(self) -> int:
        """Scheduled span minus the shift's break."""
        return self.duration_minutes - self.break_minutes

    def __repr__(self) -> str:
        return f"<Shift id={self.id} name={self.name!r}>"
