import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(16))
ALGORITHM = os.getenv("ALGORITHM", "HS256")

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password=password.encode(), salt=bcrypt.gensalt()).decode()


def check_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        password=password.encode(), hashed_password=hashed_password.encode()
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_tokens(user_id: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    access = jwt.encode(
        {
            "sub": user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "jti": str(uuid.uuid4()),
        },
        key=SECRET_KEY,
        algorithm=ALGORITHM,
    )
    refresh = jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(days=30),
            "jti": str(uuid.uuid4()),
        },
        key=SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return (access, refresh)


def decode_token(token: str, expected_type: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    return None if payload.get("type") != expected_type else payload


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Mapping[str, Any]:
    from sqlalchemy import select

    from core.messages import USER_MESSAGES
    from core.models.user import User

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=USER_MESSAGES.INVALID_TOKEN
    )

    if not credentials:
        raise unauthorized

    payload = decode_token(credentials.credentials, "access")
    if payload is None:
        raise unauthorized
    user_id = payload.get("sub")
    if user_id is None:
        raise unauthorized
    try:
        uid = UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        raise unauthorized

    cols = [c for c in User.__table__.c if c.name != "password"]

    query = await db.execute(select(*cols).where(User.id == uid))
    user = query.mappings().one_or_none()
    if user is None:
        raise unauthorized

    return user


async def require_pro_user(
    current_user: Mapping[str, Any] = Depends(current_user),
) -> Mapping[str, Any]:
    from core.messages import USER_MESSAGES

    if current_user.get("subscription_status") != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=USER_MESSAGES.USER_NOT_PRO,
        )
    return current_user
