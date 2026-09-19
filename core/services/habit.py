from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.messages import HABIT_MESSAGES
from core.models.habit import Habit
from core.schemas.habit import CreateHabitRequest, HabitResponse
from core.utils import fmt_days


class HabitService:
    async def create_habit(
        self, user_id: str, payload: CreateHabitRequest, db: AsyncSession
    ) -> HabitResponse:
        habit_days = sorted(set(payload.days_of_week))

        if payload.reminder_time:
            conflict_query = select(Habit).where(
                Habit.user_id == user_id,
                Habit.reminder_time == payload.reminder_time,
                Habit.days_of_week.overlap(habit_days),
            )
            conflicting_habit = (await db.execute(conflict_query)).scalars().first()

            if conflicting_habit:
                overlapping_days = set(habit_days) & set(conflicting_habit.days_of_week)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=HABIT_MESSAGES.HABIT_TIME_CONFLICT.format(
                        time=payload.reminder_time,
                        days=fmt_days(list(overlapping_days)),
                    ),
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
