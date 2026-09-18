from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator

from core.models.routine import PeriodOfDay


class CreateRoutineHabitInput(BaseModel):
    name: str
    reminder_time: Optional[str] = None
    days_of_week: set[int]

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, v: set[int]) -> set[int]:
        if not v:
            raise ValueError("Select at least one day.")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Invalid day selected.")
        return v


class CreateRoutineRequest(BaseModel):
    name: str
    time_of_day: str
    period_of_day: PeriodOfDay
    frequency: set[int]
    habits: list[CreateRoutineHabitInput] = []

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: set[int]) -> set[int]:
        if not v:
            raise ValueError("Select at least one day.")
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Invalid day selected.")
        return v


class HabitResponse(BaseModel):
    id: UUID
    name: str
    reminder_time: Optional[str] = None
    days_of_week: list[int]
    created_at: datetime

    class Config:
        from_attributes = True


class RoutineResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    time_of_day: str
    period_of_day: str
    frequency: list[int]
    habits: list[HabitResponse]
    created_at: datetime

    class Config:
        from_attributes = True
