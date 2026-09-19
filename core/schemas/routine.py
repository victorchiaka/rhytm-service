from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from core.models.routine import PeriodOfDay
from core.schemas.habit import HabitResponse


class CreateRoutineRequest(BaseModel):
    name: str
    time_of_day: str
    period_of_day: PeriodOfDay
    frequency: set[int]
    habits: list[UUID]

    @field_validator("habits")
    @classmethod
    def validate_habits(cls, v: list[UUID]) -> list[UUID]:
        if not v:
            raise ValueError("You must add at least one habit to create a routine.")
        return v

    @field_validator("time_of_day")
    @classmethod
    def validate_time(cls, v: str) -> str:
        import re
        if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", v):
            raise ValueError("Time of day must be in HH:MM format.")
        return v

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: set[int]) -> set[int]:
        if not v:
            raise ValueError("Select at least one day.")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Invalid day selected.")
        return v


class RoutineResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    time_of_day: str
    period_of_day: str
    frequency: list[int]
    habits: list[HabitResponse]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
