from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.habit import CreateHabitRequest, HabitResponse
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
