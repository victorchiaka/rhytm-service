from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.schemas.habit import ActivityWeek, HabitResponse
from core.schemas.routine import RoutineResponse


class SignupRequest(BaseModel):
    full_name: str
    email: EmailStr
    timezone: str | None = None  # IANA identifier, e.g. "Asia/Dubai"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            return None  # fall back to UTC
        return v


class CompleteSignupRequest(BaseModel):
    email: EmailStr
    otp: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


class LogoutRequest(BaseModel):
    refresh_token: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserActivityLogResponse(BaseModel):
    user_id: UUID
    scope: str
    from_: str = Field(alias="from")
    to: str
    weeks: list[ActivityWeek]

    class Config:
        populate_by_name = True


class UserResponse(BaseModel):
    id: UUID
    full_name: str
    email: EmailStr
    plan: str | None = None
    subscription_status: str = "basic"
    revenuecat_id: str | None = None
    timezone: str | None = None
    plan_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    routines: list[RoutineResponse] = []
    habits: list[HabitResponse] = []
    activity: UserActivityLogResponse | None = None

    class Config:
        from_attributes = True
