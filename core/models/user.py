import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from core.models.habit import Habit
from core.models.routine import Routine
from db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    password = Column(Text, nullable=False)
    plan = Column(Text, nullable=True, server_default=None)
    subscription_status = Column(
        Text, nullable=False, server_default="basic", default="basic"
    )
    revenuecat_id = Column(Text, unique=True, nullable=True)
    stripe_customer_id = Column(Text, unique=True, nullable=True, index=True)
    plan_expires_at = Column(DateTime(timezone=True), nullable=True)
    timezone = Column(Text, nullable=True)  # IANA identifier, e.g. "Asia/Dubai"
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    routines = relationship(
        "Routine",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    habits = relationship(
        "Habit",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    conversations = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    activity_logs = relationship(
        "UserActivityLog",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash = Column(Text, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="sessions")


class UserActivityLog(Base):
    __tablename__ = "user_activity_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_date = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user = relationship("User", back_populates="activity_logs")

    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_user_activity_lookup"),
    )
