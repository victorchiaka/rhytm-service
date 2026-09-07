import uuid

from sqlalchemy import Column, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    password = Column(Text, nullable=False)
    plan = Column(Text, nullable=False, default="basic")
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
