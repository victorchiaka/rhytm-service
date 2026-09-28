import re
from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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


class HabitActivityLogShape(BaseModel):
    id: UUID
    habit_id: UUID
    activity_date: date
    created_at: datetime

    class Config:
        from_attributes = True


class HabitShape(BaseModel):
    id: UUID
    name: str
    reminder_time: str | None = None
    days_of_week: list[int]
    activity_logs: list[HabitActivityLogShape] = Field(default=[], exclude=True)
    activity: "ActivityLogResponse | None" = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HabitResponse(HabitShape):
    pass


class DeleteHabitResponse(BaseModel):
    message: str


class ActivityWeek(BaseModel):
    week: int
    days: list[int]
    total: int


class ActivityLogResponse(BaseModel):
    habit_id: UUID
    scope: str
    from_: str = Field(alias="from")
    to: str
    weeks: list[ActivityWeek]

    class Config:
        populate_by_name = True


class SyncActionEnum(str, Enum):
    CHECK_IN = "CHECK_IN"
    UNDO_CHECK_IN = "UNDO_CHECK_IN"


class SyncActivityItem(BaseModel):
    habit_id: UUID
    date: str  # YYYY-MM-DD
    action: SyncActionEnum


class SyncActivityRequest(BaseModel):
    activities: list[SyncActivityItem]


class SyncActivityResponse(BaseModel):
    message: str
    processed: int


class CheckInRequest(BaseModel):
    date: str | None = None  # YYYY-MM-DD. Defaults to today if not provided.
