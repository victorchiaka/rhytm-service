import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class CreateHabitInput(BaseModel):
    name: str
    reminder_time: str | None = None
    days_of_week: set[int]

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: set[int]) -> set[int]:
        if not v:
            raise ValueError("Select at least one day.")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Invalid day selected.")
        return v

    @field_validator("reminder_time")
    @classmethod
    def validate_time(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", v):
            raise ValueError("Reminder time must be in HH:MM format.")
        return v


class CreateHabitRequest(CreateHabitInput):
    pass


class UpdateHabitRequest(CreateHabitInput):
    pass


class HabitResponse(BaseModel):
    id: UUID
    name: str
    reminder_time: str | None = None
    days_of_week: list[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
