import uuid

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db.database import Base


class Habit(Base):
    __tablename__ = "habits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    routine_id = Column(
        UUID(as_uuid=True),
        ForeignKey("routines.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(Text, nullable=False)
    reminder_time = Column(Text, nullable=True)
    days_of_week = Column(ARRAY(Integer), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="habits")
    routine = relationship("Routine", back_populates="habits")
    activity_logs = relationship(
        "ActivityLog",
        back_populates="habit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("idx_habits_routine", "routine_id"),)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    habit_id = Column(
        UUID(as_uuid=True), ForeignKey("habits.id", ondelete="CASCADE"), nullable=False
    )
    activity_date = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    habit = relationship("Habit", back_populates="activity_logs")

    __table_args__ = (
        UniqueConstraint("habit_id", "activity_date", name="idx_activity_lookup"),
    )
