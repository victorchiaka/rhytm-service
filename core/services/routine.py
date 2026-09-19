from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import ROUTINE_MESSAGES
from core.models.habit import Habit
from core.models.routine import PeriodOfDay, Routine
from core.schemas.routine import CreateRoutineRequest, RoutineResponse
from core.utils import fmt_days


class RoutineService:
    @staticmethod
    async def _check_name_collision(db: AsyncSession, user_id: str, name: str) -> None:
        result = await db.execute(
            select(Routine).where(
                Routine.user_id == user_id,
                func.lower(Routine.name) == name.strip().lower(),
            )
        )
        if result.first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ROUTINE_MESSAGES.DUPLICATE_NAME.format(name=name.strip()),
            )

    PERIOD_TIME_BOUNDS = {
        PeriodOfDay.MORNING: ("00:00", "11:00"),
        PeriodOfDay.AFTERNOON: ("12:00", "15:00"),
        PeriodOfDay.EVENING: ("16:00", "21:00"),
    }

    @classmethod
    def _validate_period_time_range(cls, period: PeriodOfDay, time_str: str) -> None:
        """Ensure input time_of_day (HH:MM) falls within period_of_day boundaries."""
        start_str, end_str = cls.PERIOD_TIME_BOUNDS[period]
        h, m = map(int, time_str.split(":"))
        time_mins = h * 60 + m

        sh, sm = map(int, start_str.split(":"))
        start_mins = sh * 60 + sm

        eh, em = map(int, end_str.split(":"))
        end_mins = eh * 60 + em

        if not (start_mins <= time_mins <= end_mins):
            # Format friendly string e.g. "12:00 AM" or "00:00"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ROUTINE_MESSAGES.INVALID_PERIOD_TIME.format(
                    period=period.value,
                    start=start_str,
                    end=end_str,
                ),
            )

    @staticmethod
    async def _check_period_routine_limit(
        db: AsyncSession, user_id: str, period: PeriodOfDay, max_limit: int = 3
    ) -> None:
        """Cap the maximum number of routines allowed per period (default 3)."""
        result = await db.execute(
            select(func.count(Routine.id)).where(
                Routine.user_id == user_id,
                Routine.period_of_day == period,
            )
        )
        count = result.scalar() or 0
        if count >= max_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ROUTINE_MESSAGES.MAX_ROUTINES_PER_PERIOD.format(
                    max_count=max_limit
                ),
            )

    @staticmethod
    async def _check_routine_time_spacing(
        db: AsyncSession,
        user_id: str,
        time_str: str,
        frequency: list[int],
        num_habits: int = 0,
        estimated_mins_per_habit: int = 15,
    ) -> None:
        """Ensure routine does not physically overlap with existing routines on overlapping days based on start time + estimated duration (15m per habit, min 30m)."""
        h, m = map(int, time_str.split(":"))
        target_start = h * 60 + m
        # Duration based on habit count, minimum 30 minutes
        target_duration = max(30, num_habits * estimated_mins_per_habit)
        target_end = target_start + target_duration

        result = await db.execute(
            select(Routine)
            .options(selectinload(Routine.habits))
            .where(
                Routine.user_id == user_id,
                Routine.frequency.overlap(frequency),
            )
        )
        existing_routines = result.scalars().all()

        for existing in existing_routines:
            eh, em = map(int, existing.time_of_day.split(":"))
            existing_start = eh * 60 + em
            existing_habit_count = len(existing.habits) if existing.habits else 0
            existing_duration = max(30, existing_habit_count * estimated_mins_per_habit)
            existing_end = existing_start + existing_duration

            # Check interval overlap: max(starts) < min(ends)
            if max(target_start, existing_start) < min(target_end, existing_end):
                overlapping_days = set(frequency) & set(existing.frequency or [])
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ROUTINE_MESSAGES.ROUTINE_DURATION_OVERLAP.format(
                        existing_name=existing.name,
                        days=fmt_days(list(overlapping_days)),
                    ),
                )

    @staticmethod
    async def _validate_habits_for_routine(
        db: AsyncSession,
        user_id: str,
        habit_ids: list[UUID],
        period: PeriodOfDay,
        frequency: list[int],
    ) -> list[Habit]:
        """Fetch habits by ID and validate each is eligible to join the routine."""
        habits: list[Habit] = []

        for habit_id in habit_ids:
            result = await db.execute(
                select(Habit)
                .options(selectinload(Habit.routines))
                .where(
                    Habit.id == habit_id,
                    Habit.user_id == user_id,
                )
            )
            habit = result.scalars().first()

            if not habit:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ROUTINE_MESSAGES.HABIT_NOT_FOUND.format(habit_id=habit_id),
                )

            # Check for conflict: habit cannot be in another routine in the same period on overlapping days
            for existing_routine in habit.routines:
                if existing_routine.period_of_day == period:
                    overlapping_days = set(frequency) & set(
                        existing_routine.frequency or []
                    )
                    if overlapping_days:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=ROUTINE_MESSAGES.HABIT_CONFLICT.format(
                                habit_name=habit.name,
                                period=period.value,
                                days=fmt_days(list(overlapping_days)),
                            ),
                        )

            # Validate that the habit's scheduled days fall within routine's frequency boundary
            if not set(habit.days_of_week).issubset(set(frequency)):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=ROUTINE_MESSAGES.HABIT_DAYS_OUT_OF_RANGE.format(
                        habit_name=habit.name
                    ),
                )

            habits.append(habit)

        return habits

    @staticmethod
    async def _persist_routine_and_habits(
        db: AsyncSession,
        user_id: str,
        name: str,
        time_of_day: str,
        period: PeriodOfDay,
        frequency: list[int],
        habits: list[Habit],
    ) -> Routine:
        new_routine = Routine(
            user_id=user_id,
            name=name.strip(),
            time_of_day=time_of_day,
            period_of_day=period,
            frequency=frequency,
        )
        new_routine.habits.extend(habits)

        db.add(new_routine)
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
        """Create a new routine and link existing habits to it.

        Validates the routine name, period/day conflicts, and each habit's
        eligibility before persisting.
        """
        frequency: list[int] = sorted(set(payload.frequency))
        period: PeriodOfDay = payload.period_of_day

        await self._check_name_collision(db, user_id, payload.name)
        self._validate_period_time_range(period, payload.time_of_day)
        await self._check_period_routine_limit(db, user_id, period, max_limit=3)

        habits = await self._validate_habits_for_routine(
            db, user_id, payload.habits, period, frequency
        )

        await self._check_routine_time_spacing(
            db,
            user_id,
            payload.time_of_day,
            frequency,
            num_habits=len(habits),
        )

        routine = await self._persist_routine_and_habits(
            db=db,
            user_id=user_id,
            name=payload.name,
            time_of_day=payload.time_of_day,
            period=period,
            frequency=frequency,
            habits=habits,
        )

        return RoutineResponse.model_validate(routine)
