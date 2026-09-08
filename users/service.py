import json
import logging
import secrets
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from users.messages import USER_MESSAGES
from users.models import Session, User
from users.schemas import UserResponse
from users.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)

logger = logging.getLogger(__name__)

SIGNUP_OTP_EXPIRY_SECONDS = 600
RESET_PASSWORD_OTP_EXPIRY_SECONDS = 600


class UserService:
    async def initiate_signup(
        self, email: str, full_name: str, db: AsyncSession, rdb: Redis
    ) -> dict:
        normalized_email = email.lower()
        result = await db.execute(select(User).where(User.email == normalized_email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.USER_EXISTS,
            )

        otp = f"{secrets.randbelow(10000):04d}"
        redis_key = f"signup_otp:{normalized_email}"
        signup_data = {
            "full_name": full_name,
            "email": normalized_email,
            "otp": otp,
        }
        await rdb.set(redis_key, json.dumps(signup_data), ex=SIGNUP_OTP_EXPIRY_SECONDS)

        # Log OTP to console for now
        # TODO: Set up email OTP sending service (SMTP / SendGrid / Resend / AWS SES)
        logger.info(f"[SIGNUP OTP] OTP for {normalized_email} is: {otp}")

        return {"message": USER_MESSAGES.OTP_SENT}

    async def complete_signup(
        self,
        email: str,
        otp: str,
        password: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        normalized_email = email.lower()
        redis_key = f"signup_otp:{normalized_email}"
        cached_data = await rdb.get(redis_key)
        if not cached_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        data = json.loads(cached_data)

        if data.get("otp") != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        result = await db.execute(select(User).where(User.email == normalized_email))
        if result.scalar_one_or_none():
            await rdb.delete(redis_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.USER_EXISTS,
            )

        new_user = User(
            full_name=data["full_name"],
            email=normalized_email,
            password=hash_password(password),
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        await rdb.delete(redis_key)

        access_token, refresh_token = self._issue_tokens(new_user.id, new_user.email)
        await self._persist_session(db, new_user.id, refresh_token)

        return {
            "message": USER_MESSAGES.ACCOUNT_CREATED,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": UserResponse.model_validate(new_user).model_dump(),
        }

    async def login(self, email: str, password: str, db: AsyncSession) -> dict:
        normalized_email = email.lower()

        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_CREDENTIALS,
            )

        access_token, refresh_token = self._issue_tokens(user.id, user.email)
        await self._persist_session(db, user.id, refresh_token)

        return {
            "message": USER_MESSAGES.LOGIN_SUCCESS,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": UserResponse.model_validate(user).model_dump(),
        }

    async def request_password_reset(
        self, email: str, db: AsyncSession, rdb: Redis
    ) -> dict:
        normalized_email = email.lower()
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        otp = f"{secrets.randbelow(10000):04d}"
        redis_key = f"reset_password_otp:{normalized_email}"
        reset_data = {
            "email": normalized_email,
            "otp": otp,
        }
        await rdb.set(
            redis_key, json.dumps(reset_data), ex=RESET_PASSWORD_OTP_EXPIRY_SECONDS
        )

        # Log OTP to console for now
        # TODO: Set up email OTP sending service (SMTP / SendGrid / Resend / AWS SES)
        logger.info(f"[RESET PASSWORD OTP] OTP for {normalized_email} is: {otp}")

        return {"message": USER_MESSAGES.OTP_SENT}

    async def reset_password(
        self,
        email: str,
        otp: str,
        new_password: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        normalized_email = email.lower()
        redis_key = f"reset_password_otp:{normalized_email}"
        cached_data = await rdb.get(redis_key)
        if not cached_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        data = json.loads(cached_data)

        if data.get("otp") != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if not user:
            await rdb.delete(redis_key)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        user.password = hash_password(new_password)
        await db.commit()
        await rdb.delete(redis_key)

        return {"message": USER_MESSAGES.PASSWORD_RESET_SUCCESS}

    async def logout(
        self,
        access_token: str,
        refresh_token: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        try:
            access_payload = decode_token(access_token, expected_type="access")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        try:
            refresh_payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        now = datetime.now(UTC)
        refresh_exp_ts = refresh_payload.get("exp")
        if not refresh_exp_ts or datetime.fromtimestamp(refresh_exp_ts, tz=UTC) <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        token_hash = hash_token(refresh_token)
        result = await db.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        session_entry = result.scalar_one_or_none()
        if not session_entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        if session_entry.expires_at <= now:
            await db.delete(session_entry)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        await db.delete(session_entry)
        await db.commit()

        access_exp = access_payload.get("exp")
        if access_exp and access_exp > now.timestamp():
            ttl = int(access_exp - now.timestamp())
            await rdb.set(f"blacklisted_token:{access_token}", "blacklisted", ex=ttl)

        return {"message": USER_MESSAGES.LOGOUT_SUCCESS}

    async def refresh_tokens(self, refresh_token: str, db: AsyncSession) -> dict:
        try:
            refresh_payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        now = datetime.now(UTC)
        refresh_exp_ts = refresh_payload.get("exp")
        if not refresh_exp_ts or datetime.fromtimestamp(refresh_exp_ts, tz=UTC) <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        token_hash = hash_token(refresh_token)
        result = await db.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        session_entry = result.scalar_one_or_none()
        if not session_entry:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        if session_entry.expires_at <= now:
            await db.delete(session_entry)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        await db.delete(session_entry)

        user_id = refresh_payload.get("sub")
        email = refresh_payload.get("email")

        new_access_token, new_refresh_token = self._issue_tokens(user_id, email)
        await self._persist_session(db, session_entry.user_id, new_refresh_token)

        return {
            "message": USER_MESSAGES.TOKEN_REFRESH_SUCCESS,
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
        }

    async def get_profile(self, user_id: str, db: AsyncSession) -> dict:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )
        return UserResponse.model_validate(user).model_dump()

    async def delete_account(
        self, user_id: str, access_token: str, db: AsyncSession, rdb: Redis
    ) -> None:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        await db.execute(delete(Session).where(Session.user_id == user.id))
        await db.delete(user)
        await db.commit()

        try:
            access_payload = decode_token(access_token, expected_type="access")
            access_exp = access_payload.get("exp")
            now = datetime.now(UTC)
            if access_exp and access_exp > now.timestamp():
                ttl = int(access_exp - now.timestamp())
                await rdb.set(f"blacklisted_token:{access_token}", "blacklisted", ex=ttl)
        except jwt.PyJWTError:
            pass

    @staticmethod
    def _issue_tokens(user_id, email) -> tuple[str, str]:
        """Create a fresh access + refresh token pair."""
        token_payload = {"sub": str(user_id), "email": email}
        access_token = create_token(data=token_payload, token_type="access")
        refresh_token = create_token(data=token_payload, token_type="refresh")
        return access_token, refresh_token

    @staticmethod
    async def _persist_session(db: AsyncSession, user_id, refresh_token: str) -> None:
        """Decode the refresh token and store a hashed session row."""
        decoded_refresh = decode_token(refresh_token, expected_type="refresh")
        refresh_exp = datetime.fromtimestamp(decoded_refresh["exp"], tz=UTC)

        session_entry = Session(
            user_id=user_id,
            refresh_token_hash=hash_token(refresh_token),
            expires_at=refresh_exp,
        )
        db.add(session_entry)
        await db.commit()
