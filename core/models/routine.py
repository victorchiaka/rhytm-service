import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Table, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from db.database import Base

routine_habits = Table(
    "routine_habits",
    Base.metadata,
    Column(
        "routine_id",
        UUID(as_uuid=True),
        ForeignKey("routines.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "habit_id",
        UUID(as_uuid=True),
        ForeignKey("habits.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class PeriodOfDay(enum.Enum):
    MORNING = "Morning"
    AFTERNOON = "Afternoon"
    EVENING = "Evening"


class DeletionMode(enum.Enum):
    ROUTINE_ONLY = "routine_only"
    EXCLUSIVE = "exclusive"


class Deletion(Base):
    __tablename__ = "deletions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    routine_id = Column(UUID(as_uuid=True), nullable=False)
    mode = Column(
        Enum(
            DeletionMode,
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    deadline = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deletion_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deletions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="routines")
    habits = relationship("Habit", secondary=routine_habits, back_populates="routines")
    deletion = relationship("Deletion", foreign_keys=[deletion_id])