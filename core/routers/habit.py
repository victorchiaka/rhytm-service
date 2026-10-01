from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.habit import (
    ActivityLogResponse,
    CheckInRequest,
    CreateHabitRequest,
    DeleteHabitResponse,
    HabitResponse,
    SyncActivityRequest,
    SyncActivityResponse,
    UpdateHabitRequest,
)
from core.security import get_current_user
from core.services.habit import HabitService
from db.database import get_db
from db.rdb import get_rdb

habits_router = APIRouter(prefix="/habits", tags=["Habits"])

habit_service = HabitService()


@habits_router.post(
    "/new",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Create a standalone habit. Checks for time slot conflicts "
        "(e.g., cannot schedule multiple habits at exactly the same time on the same day)."
    ),
)
async def create_habit(
    payload: CreateHabitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HabitResponse:
    return await habit_service.create_habit(
        user_id=current_user["user_id"],
        subscription_status=current_user["subscription_status"],
        payload=payload,
        db=db,
    )


@habits_router.get("/all")
async def get_all(
    current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await habit_service.get_all(
        user_id=current_user["user_id"],
        subscription_status=current_user["subscription_status"],
        db=db,
    )


@habits_router.get(
    "/today", status_code=status.HTTP_200_OK, description="Fetches Today's habits"
)
async def get_today_habits(
    day: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[HabitResponse]:
    return await habit_service.get_by_day(
        user_id=current_user["user_id"],
        day_digit=day,
        subscription_status=current_user["subscription_status"],
        db=db,
    )


@habits_router.post(
    "/sync-activity",
    status_code=status.HTTP_200_OK,
    description="Bulk sync activity items from local cache",
)
async def sync_activity(
    payload: SyncActivityRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SyncActivityResponse:
    return await habit_service.sync_activity(
        user_id=current_user["user_id"], payload=payload, db=db
    )


@habits_router.get(
    "/{id}", status_code=status.HTTP_200_OK, description="Fetches a single habit"
)
async def get_habit(
    id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HabitResponse:
    return await habit_service.get_habit(
        user_id=current_user["user_id"],
        habit_id=id,
        subscription_status=current_user["subscription_status"],
        db=db,
    )


@habits_router.get(
    "/{id}/activity",
    status_code=status.HTTP_200_OK,
    description="Fetches habit activity log in a dense week format",
)
async def get_habit_activity(
    id: str,
    scope: str,
    date: str | None = None,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActivityLogResponse:
    return await habit_service.get_activity_log(
        habit_id=id,
        user_id=current_user["user_id"],
        scope=scope,
        target_date=date,
        db=db,
    )


@habits_router.post(
    "/{id}/checkin",
    status_code=status.HTTP_200_OK,
    description="Record a single check-in for a habit",
)
async def check_in_habit(
    id: str,
    payload: CheckInRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await habit_service.check_in(
        habit_id=id, user_id=current_user["user_id"], payload=payload, db=db, rdb=rdb
    )


@habits_router.delete(
    "/{id}/checkin",
    status_code=status.HTTP_200_OK,
    description="Undo a single check-in for a habit within 5 minutes of check-in",
)
async def undo_check_in_habit(
    id: str,
    payload: CheckInRequest,
    undo_token: str = Query(..., description="Undo token returned from habit check-in"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rdb: Redis = Depends(get_rdb),
):
    return await habit_service.undo_check_in(
        habit_id=id,
        user_id=current_user["user_id"],
        payload=payload,
        undo_token=undo_token,
        db=db,
        rdb=rdb,
    )


@habits_router.put(
    "/{id}",
    status_code=status.HTTP_200_OK,
    description=(
        "Update an existing habit."
        "Evaluates standalone reminder time conflicts and frequency compatibility "
        "across all attached routines."
    ),
)
async def update_habit(
    id: str,
    payload: UpdateHabitRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HabitResponse:
    return await habit_service.update_habit(
        habit_id=id,
        user_id=current_user["user_id"],
        subscription_status=current_user["subscription_status"],
        payload=payload,
        db=db,
    )


@habits_router.delete(
    "/{id}",
    status_code=status.HTTP_200_OK,
    description="Delete a habit. Routines left without habits are deleted with it.",
)
async def delete_habit(
    id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteHabitResponse:
    return await habit_service.delete_habit(
        habit_id=id, user_id=current_user["user_id"], db=db
    )
