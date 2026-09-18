from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import ROUTINE_MESSAGES
from core.models.habit import Habit
from core.models.routine import PeriodOfDay, Routine
from core.schemas.routine import CreateRoutineRequest, RoutineResponse

_DAY_NAMES = {
    0: "SU",
    1: "M",
    2: "T",
    3: "W",
    4: "TH",
    5: "F",
    6: "S",
}


def _fmt_days(day_ints: list[int]) -> str:
    """Convert a list of day integers to a readable string, e.g. 'M, W, F'."""
    return ", ".join(_DAY_NAMES[d] for d in sorted(day_ints))


class RoutineService:
    @staticmethod
    async def _check_name_collision(db: AsyncSession, user_id: str, name: str) -> None:
        name_collision_result = await db.execute(
            select(Routine).where(
                Routine.user_id == user_id,
                func.lower(Routine.name) == name.strip().lower(),
            )
        )
        if name_collision_result.first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ROUTINE_MESSAGES.DUPLICATE_NAME.format(name=name.strip()),
            )

    @staticmethod
    async def _check_period_overlap(
        db: AsyncSession, user_id: str, period: PeriodOfDay, frequency: list[int]
    ) -> None:
        overlap_check_result = await db.execute(
            select(Routine).where(
                Routine.user_id == user_id,
                Routine.period_of_day == period,
                Routine.frequency.overlap(frequency),
            )
        )
        conflicting_routine = overlap_check_result.scalars().first()

        if conflicting_routine:
            overlapping_days = set(frequency) & set(conflicting_routine.frequency or [])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ROUTINE_MESSAGES.DUPLICATE_PERIOD_ON_DAY.format(
                    period=period.value,
                    days=_fmt_days(list(overlapping_days)),
                ),
            )

    @staticmethod
    async def _check_habit_conflicts(
        db: AsyncSession,
        user_id: str,
        habits: list,
        period: PeriodOfDay,
        frequency: list[int],
    ) -> None:
        for habit_input in habits:
            habit_days = sorted(set(habit_input.days_of_week))

            if not set(habit_days).issubset(set(frequency)):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=ROUTINE_MESSAGES.HABIT_DAYS_OUT_OF_RANGE.format(
                        habit_name=habit_input.name
                    ),
                )

            conflict_query = (
                select(Habit)
                .join(Routine, Habit.routine_id == Routine.id)
                .where(
                    Habit.user_id == user_id,
                    func.lower(Habit.name) == habit_input.name.strip().lower(),
                    Routine.period_of_day == period,
                    Routine.frequency.overlap(habit_days),
                )
                .options(selectinload(Habit.routine))
            )
            conflicting_habit = (await db.execute(conflict_query)).scalars().first()

            if conflicting_habit and conflicting_habit.routine:
                overlapping_habit_days = set(habit_days) & set(
                    conflicting_habit.routine.frequency or []
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ROUTINE_MESSAGES.HABIT_CONFLICT.format(
                        habit_name=habit_input.name,
                        period=period.value,
                        days=_fmt_days(list(overlapping_habit_days)),
                    ),
                )

    @staticmethod
    async def _persist_routine_and_habits(
        db: AsyncSession,
        user_id: str,
        name: str,
        time_of_day: str,
        period: PeriodOfDay,
        frequency: list[int],
        habits: list,
    ) -> Routine:
        new_routine = Routine(
            user_id=user_id,
            name=name.strip(),
            time_of_day=time_of_day,
            period_of_day=period,
            frequency=frequency,
        )
        db.add(new_routine)
        # Flush so the routine gets its UUID before we attach habits to it.
        await db.flush()

        for habit_input in habits:
            new_habit = Habit(
                user_id=user_id,
                routine_id=new_routine.id,
                name=habit_input.name.strip(),
                reminder_time=habit_input.reminder_time,
                days_of_week=sorted(set(habit_input.days_of_week)),
            )
            db.add(new_habit)

        await db.commit()

        refreshed_result = await db.execute(
            select(Routine)
            .where(Routine.id == new_routine.id)
            .options(selectinload(Routine.habits))
        )
        return refreshed_result.scalar_one()

    async def create_routine(
        self,
        user_id: str,
        payload: CreateRoutineRequest,
        db: AsyncSession,
    ) -> RoutineResponse:
        """Create a new routine for the authenticated user.

        Orchestrates validation and persistence by calling static helper methods.
        """
        frequency: list[int] = sorted(set(payload.frequency))
        period: PeriodOfDay = payload.period_of_day

        await self._check_name_collision(db, user_id, payload.name)
        await self._check_period_overlap(db, user_id, period, frequency)
        await self._check_habit_conflicts(
            db, user_id, payload.habits, period, frequency
        )

        routine = await self._persist_routine_and_habits(
            db=db,
            user_id=user_id,
            name=payload.name,
            time_of_day=payload.time_of_day,
            period=period,
            frequency=frequency,
            habits=payload.habits,
        )

        return RoutineResponse.model_validate(routine)
