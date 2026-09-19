from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import HABIT_MESSAGES, ROUTINE_MESSAGES
from core.models.habit import Habit
from core.schemas.habit import CreateHabitRequest, HabitResponse, UpdateHabitRequest
from core.utils import check_reminder_before_routine, fmt_days, is_time_in_period


class HabitService:
    @staticmethod
    async def _check_reminder_conflict(
        db: AsyncSession,
        user_id: str,
        reminder_time: str | None,
        habit_days: list[int],
        exclude_habit_id: UUID | None = None,
    ) -> None:
        """Verify standalone habit reminder time does not conflict with another habit on overlapping days."""
        if not reminder_time:
            return

        query = select(Habit).where(
            Habit.user_id == user_id,
            Habit.reminder_time == reminder_time,
            Habit.days_of_week.overlap(habit_days),
        )
        if exclude_habit_id:
            query = query.where(Habit.id != exclude_habit_id)

        conflicting_habit = (await db.execute(query)).scalars().first()
        if conflicting_habit:
            overlapping_days = set(habit_days) & set(conflicting_habit.days_of_week)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=HABIT_MESSAGES.HABIT_TIME_CONFLICT.format(
                    time=reminder_time,
                    days=fmt_days(list(overlapping_days)),
                ),
            )

    @staticmethod
    def _validate_habit_routines_compatibility(
        habit_name: str,
        habit_days: list[int],
        reminder_time: str | None,
        routines: list,
    ) -> None:
        """Validate habit days and reminder time against all routines it belongs to in a single O(N) pass."""
        habit_days_set = set(habit_days)
        seen_days_by_period: dict[str, dict[int, str]] = {}

        for routine in routines:
            routine_frequency_set = set(routine.frequency or [])

            # 1. Boundary Check: Habit days must remain a subset of every attached routine's frequency
            if not habit_days_set.issubset(routine_frequency_set):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=HABIT_MESSAGES.HABIT_DAYS_EXCEED_ROUTINE.format(
                        habit_name=habit_name,
                        routine_name=routine.name,
                    ),
                )

            # 2. Reminder Time Check: Habit reminder_time must fall within routine period_of_day & execution window
            period = routine.period_of_day
            period_key = period.value if hasattr(period, "value") else str(period)
            if reminder_time:
                if not is_time_in_period(period, reminder_time):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=HABIT_MESSAGES.REMINDER_OUT_OF_PERIOD.format(
                            habit_name=habit_name,
                            reminder_time=reminder_time,
                            routine_name=routine.name,
                            period=period_key,
                        ),
                    )

                is_earlier, diff_mins, start_str = check_reminder_before_routine(
                    routine.time_of_day, reminder_time
                )
                if is_earlier:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=HABIT_MESSAGES.REMINDER_TOO_EARLY.format(
                            habit_name=habit_name,
                            routine_name=routine.name,
                            diff_mins=diff_mins,
                            start=start_str,
                        ),
                    )

            # 3. Inter-Routine Conflict Check: Habit cannot belong to multiple routines in the same period on overlapping days
            active_days = habit_days_set & routine_frequency_set
            period_days_map = seen_days_by_period.setdefault(period_key, {})

            overlapping_days = [day for day in active_days if day in period_days_map]
            if overlapping_days:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ROUTINE_MESSAGES.HABIT_CONFLICT.format(
                        habit_name=habit_name,
                        period=period_key,
                        days=fmt_days(overlapping_days),
                    ),
                )

            for day in active_days:
                period_days_map[day] = routine.name

    async def create_habit(
        self, user_id: str, payload: CreateHabitRequest, db: AsyncSession
    ) -> HabitResponse:
        habit_days = sorted(set(payload.days_of_week))

        await self._check_reminder_conflict(
            db, user_id, payload.reminder_time, habit_days
        )

        new_habit = Habit(
            user_id=user_id,
            name=payload.name.strip(),
            reminder_time=payload.reminder_time,
            days_of_week=habit_days,
        )
        db.add(new_habit)
        await db.commit()
        await db.refresh(new_habit)

        return HabitResponse.model_validate(new_habit)

    async def update_habit(
        self,
        user_id: str,
        habit_id: UUID,
        payload: UpdateHabitRequest,
        db: AsyncSession,
    ) -> HabitResponse:
        """Update an existing habit and evaluate standalone & routine compatibility guardrails."""
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.routines))
            .where(Habit.id == habit_id, Habit.user_id == user_id)
        )
        habit = result.scalars().first()
        if not habit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=HABIT_MESSAGES.NOT_FOUND,
            )

        habit_days = sorted(set(payload.days_of_week))

        await self._check_reminder_conflict(
            db, user_id, payload.reminder_time, habit_days, exclude_habit_id=habit_id
        )

        if habit.routines:
            self._validate_habit_routines_compatibility(
                payload.name.strip(), habit_days, payload.reminder_time, habit.routines
            )

        habit.name = payload.name.strip()
        habit.reminder_time = payload.reminder_time
        habit.days_of_week = habit_days

        await db.commit()
        await db.refresh(habit)

        return HabitResponse.model_validate(habit)
