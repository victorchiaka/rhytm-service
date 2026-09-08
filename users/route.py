import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.rdb import get_rdb
from users.messages import USER_MESSAGES
from users.schemas import (
    CompleteSignupRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RequestPasswordResetRequest,
    ResetPasswordRequest,
    SignupRequest,
    UserResponse,
)
from users.security import decode_token
from users.service import UserService

bearer_scheme = HTTPBearer(auto_error=False)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])
users_router = APIRouter(prefix="/users", tags=["Users"])

user_service = UserService()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=USER_MESSAGES.UNAUTHORIZED,
        )
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=USER_MESSAGES.INVALID_TOKEN,
        )
    return {"user_id": payload["sub"], "access_token": credentials.credentials}


@auth_router.post("/signup", status_code=status.HTTP_200_OK)
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await user_service.initiate_signup(
        email=payload.email, full_name=payload.full_name, db=db, rdb=rdb
    )


@auth_router.post("/complete-signup", status_code=status.HTTP_201_CREATED)
async def complete_signup(
    payload: CompleteSignupRequest,
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await user_service.complete_signup(
        email=payload.email,
        otp=payload.otp,
        password=payload.password,
        db=db,
        rdb=rdb,
    )


@auth_router.post("/login", status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    return await user_service.login(
        email=payload.email, password=payload.password, db=db
    )


@auth_router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def request_password_reset(
    payload: RequestPasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await user_service.request_password_reset(
        email=payload.email, db=db, rdb=rdb
    )


@auth_router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await user_service.reset_password(
        email=payload.email,
        otp=payload.otp,
        new_password=payload.new_password,
        db=db,
        rdb=rdb,
    )


@auth_router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    payload: LogoutRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=USER_MESSAGES.UNAUTHORIZED,
        )
    return await user_service.logout(
        access_token=credentials.credentials,
        refresh_token=payload.refresh_token,
        db=db,
        rdb=rdb,
    )


@auth_router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh_tokens(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    return await user_service.refresh_tokens(
        refresh_token=payload.refresh_token, db=db
    )


@users_router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.get_profile(
        user_id=current_user["user_id"], db=db
    )


@users_router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    await user_service.delete_account(
        user_id=current_user["user_id"],
        access_token=current_user["access_token"],
        db=db,
        rdb=rdb,
    )
