from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.habit import (
    CreateHabitRequest,
    DeleteHabitResponse,
    HabitResponse,
    UpdateHabitRequest,
)
from core.security import get_current_user
from core.services.habit import HabitService
from db.database import get_db

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
        payload=payload,
        db=db,
    )


@habits_router.get("/all")
async def get_all(
    current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await habit_service.get_all(user_id=current_user["user_id"], db=db)


@habits_router.get(
    "/today", status_code=status.HTTP_200_OK, description="Fetches Today's habits"
)
async def get_today_habits(
    day: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[HabitResponse]:
    return await habit_service.get_by_day(
        user_id=current_user["user_id"], day_digit=day, db=db
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
        user_id=current_user["user_id"], habit_id=id, db=db
    )


@habits_router.put(
    "/{id}",
    status_code=status.HTTP_200_OK,
    description=(
        "Update an existing habit. "
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
        payload=payload,
        db=db,
    )


@habits_router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
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
