import enum
import uuid

from sqlalchemy import ARRAY, Column, DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db.database import Base


class PeriodOfDay(enum.Enum):
    MORNING = "Morning"
    AFTERNOON = "Afternoon"
    EVENING = "Evening"


class Routine(Base):
    __tablename__ = "routines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(Text, nullable=False)
    time_of_day = Column(Text, nullable=False)
    period_of_day = Column(
        Enum(
            PeriodOfDay,
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    # Days of the week this routine runs.
    # Integers 0–6 where 0 = Sunday, 1 = Monday … 6 = Saturday.
    # Matches the same convention used on Habit.days_of_week.
    frequency = Column(ARRAY(Integer), nullable=False, server_default="{}")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="routines")
    habits = relationship("Habit", back_populates="routine", passive_deletes=True)
