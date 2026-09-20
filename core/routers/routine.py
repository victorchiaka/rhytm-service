from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.routine import (
    CreateRoutineRequest,
    RoutineResponse,
    UpdateRoutineRequest,
)
from core.security import get_current_user
from core.services.routine import RoutineService
from db.database import get_db

routines_router = APIRouter(prefix="/routines", tags=["Routines"])

routine_service = RoutineService()


@routines_router.get("/all", status_code=status.HTTP_200_OK)
async def get_all(
    current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[RoutineResponse]:
    return await routine_service.get_all(user_id=current_user["user_id"], db=db)


@routines_router.get(
    "/today",
    status_code=status.HTTP_200_OK,
    description="Fetches routines scheduled on a given day",
)
async def get_today_routines(
    day: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RoutineResponse]:
    return await routine_service.get_by_day(
        user_id=current_user["user_id"], day_digit=day, db=db
    )


@routines_router.get(
    "/{id}", status_code=status.HTTP_200_OK, description="Fetches a single routine"
)
async def get_routine(
    id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoutineResponse:
    return await routine_service.get_routine(
        user_id=current_user["user_id"], routine_id=id, db=db
    )


@routines_router.post(
    "/new",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Create a routine with optional inline habits. "
        "Checks for duplicate names, period/day overlaps, "
        "and habit scheduling conflicts before saving."
    ),
)
async def create_routine(
    payload: CreateRoutineRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoutineResponse:
    return await routine_service.create_routine(
        user_id=current_user["user_id"],
        payload=payload,
        db=db,
    )


@routines_router.put(
    "/{id}",
    status_code=status.HTTP_200_OK,
    description=(
        "Update an existing routine. "
        "Re-evaluates name uniqueness, period boundaries, duration overlaps, "
        "and attached habits compatibility."
    ),
)
async def update_routine(
    id: str,
    payload: UpdateRoutineRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoutineResponse:
    return await routine_service.update_routine(
        id=id,
        user_id=current_user["user_id"],
        payload=payload,
        db=db,
    )
