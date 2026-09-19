from uuid import UUID

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
    "/{routine_id}",
    status_code=status.HTTP_200_OK,
    description=(
        "Update an existing routine. "
        "Re-evaluates name uniqueness, period boundaries, duration overlaps, "
        "and attached habits compatibility."
    ),
)
async def update_routine(
    routine_id: UUID,
    payload: UpdateRoutineRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoutineResponse:
    return await routine_service.update_routine(
        user_id=current_user["user_id"],
        routine_id=routine_id,
        payload=payload,
        db=db,
    )
