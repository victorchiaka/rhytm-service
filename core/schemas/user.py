from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    full_name: str
    email: EmailStr


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


class UserResponse(BaseModel):
    id: UUID
    full_name: str
    email: EmailStr
    plan: str
    revenuecat_id: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
