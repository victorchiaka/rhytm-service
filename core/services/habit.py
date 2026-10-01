from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import HABIT_MESSAGES, ROUTINE_MESSAGES
from core.models.habit import ActivityLog, Habit
from core.models.routine import Routine
from core.schemas.habit import (
    ActivityLogResponse,
    CheckInRequest,
    CreateHabitRequest,
    DeleteHabitResponse,
    HabitResponse,
    SyncActionEnum,
    SyncActivityRequest,
    SyncActivityResponse,
    UpdateHabitRequest,
)
from core.utils import (
    attach_activity,
    check_reminder_before_routine,
    fmt_days,
    is_time_in_period,
)


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
            Habit.deleted_at.is_(None),
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

        active_routines = [
            r for r in routines if getattr(r, "deleted_at", None) is None
        ]

        for routine in active_routines:
            routine_frequency_set = set(routine.frequency or [])

            if not habit_days_set.issubset(routine_frequency_set):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=HABIT_MESSAGES.HABIT_DAYS_EXCEED_ROUTINE.format(
                        habit_name=habit_name,
                        routine_name=routine.name,
                    ),
                )

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
        self,
        user_id: str,
        subscription_status: str,
        payload: CreateHabitRequest,
        db: AsyncSession,
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
        await db.refresh(new_habit, ["activity_logs"])
        habit = HabitResponse.model_validate(new_habit)
        attach_activity([habit], subscription_status)
        return habit

    async def get_all(
        self, user_id: str, subscription_status: str, db: AsyncSession
    ) -> list[HabitResponse]:
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.activity_logs))
            .where(Habit.user_id == user_id, Habit.deleted_at.is_(None))
        )
        habits = [HabitResponse.model_validate(h) for h in result.scalars().all()]
        return attach_activity(habits, subscription_status)

    async def get_by_day(
        self, user_id: str, day_digit: int, subscription_status: str, db: AsyncSession
    ) -> list[HabitResponse]:
        if day_digit not in range(7):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=HABIT_MESSAGES.INVALID_DAY_DIGIT,
            )
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.activity_logs))
            .where(
                Habit.user_id == user_id,
                Habit.days_of_week.contains([day_digit]),
                Habit.deleted_at.is_(None),
            )
        )
        habits = [HabitResponse.model_validate(h) for h in result.scalars().all()]
        return attach_activity(habits, subscription_status)

    async def get_habit(
        self, user_id: str, habit_id: str, subscription_status: str, db: AsyncSession
    ) -> HabitResponse:
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=HABIT_MESSAGES.NOT_FOUND,
            )
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.activity_logs))
            .where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        habit = result.scalar_one_or_none()
        if not habit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=HABIT_MESSAGES.NOT_FOUND,
            )
        habit_resp = HabitResponse.model_validate(habit)
        attach_activity([habit_resp], subscription_status)
        return habit_resp

    async def update_habit(
        self,
        habit_id: str,
        user_id: str,
        subscription_status: str,
        payload: UpdateHabitRequest,
        db: AsyncSession,
    ) -> HabitResponse:
        """Update an existing habit and evaluate standalone & routine compatibility guardrails."""
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=HABIT_MESSAGES.NOT_FOUND,
            )
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.routines), selectinload(Habit.activity_logs))
            .where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        habit = result.scalars().first()
        if not habit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=HABIT_MESSAGES.NOT_FOUND,
            )
        habit_days = sorted(set(payload.days_of_week))
        await self._check_reminder_conflict(
            db, user_id, payload.reminder_time, habit_days, exclude_habit_id=habit_uuid
        )
        if habit.routines:
            self._validate_habit_routines_compatibility(
                payload.name.strip(), habit_days, payload.reminder_time, habit.routines
            )
        habit.name = payload.name.strip()
        habit.reminder_time = payload.reminder_time
        habit.days_of_week = habit_days
        await db.commit()
        await db.refresh(habit, ["activity_logs"])
        habit_resp = HabitResponse.model_validate(habit)
        attach_activity([habit_resp], subscription_status)
        return habit_resp

    async def delete_habit(
        self, habit_id: str, user_id: str, db: AsyncSession
    ) -> DeleteHabitResponse:
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )
        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.routines).selectinload(Routine.habits))
            .where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        habit = result.scalars().first()
        if not habit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )
        # Routines that only have this habit in them are deleted with it
        active_routines = [r for r in habit.routines if r.deleted_at is None]
        orphaned_routines = [
            r
            for r in active_routines
            if len([h for h in r.habits if h.deleted_at is None]) <= 1
        ]

        for routine in orphaned_routines:
            await db.delete(routine)
        await db.delete(habit)
        await db.commit()

        return DeleteHabitResponse(
            message=(
                HABIT_MESSAGES.HABIT_AND_ROUTINES_DELETED
                if orphaned_routines
                else HABIT_MESSAGES.DELETED
            )
        )

    async def get_activity_log(
        self,
        habit_id: str,
        user_id: str,
        scope: str,
        target_date: str | None,
        db: AsyncSession,
    ) -> ActivityLogResponse:
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        habit_check = await db.execute(
            select(Habit.id).where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        if not habit_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        if target_date:
            try:
                base_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
                )
        else:
            base_date = datetime.now(timezone.utc).date()

        from core.utils import generate_activity_grid

        scope = scope.upper()
        if scope not in ("MONTH", "YEAR"):
            raise HTTPException(status_code=400, detail="Scope must be MONTH or YEAR")

        base_date = base_date or datetime.now(timezone.utc).date()
        if scope == "MONTH":
            from_date = base_date.replace(day=1)
            next_month = from_date.replace(day=28) + timedelta(days=4)
            to_date = next_month - timedelta(days=next_month.day)
        else:
            from_date = base_date.replace(month=1, day=1)
            to_date = base_date.replace(month=12, day=31)

        activity_query = await db.execute(
            select(ActivityLog.activity_date).where(
                ActivityLog.habit_id == habit_uuid,
                ActivityLog.activity_date >= from_date,
                ActivityLog.activity_date <= to_date,
            )
        )
        activity_dates = {row for row in activity_query.scalars().all()}

        grid_data = generate_activity_grid(habit_uuid, activity_dates, scope)
        return ActivityLogResponse(**grid_data)

    async def sync_activity(
        self, user_id: str, payload: SyncActivityRequest, db: AsyncSession
    ) -> SyncActivityResponse:
        habit_ids = list({a.habit_id for a in payload.activities})
        if not habit_ids:
            return SyncActivityResponse(message="Nothing to sync", processed=0)

        habit_check = await db.execute(
            select(Habit.id).where(Habit.id.in_(habit_ids), Habit.user_id == user_id)
        )
        valid_habit_ids = {h for h in habit_check.scalars().all()}

        processed = 0
        for activity in payload.activities:
            if activity.habit_id not in valid_habit_ids:
                continue

            try:
                act_date = date.fromisoformat(activity.date)
            except ValueError:
                continue

            existing = await db.execute(
                select(ActivityLog).where(
                    ActivityLog.habit_id == activity.habit_id,
                    ActivityLog.activity_date == act_date,
                )
            )
            existing_log = existing.scalars().first()

            if activity.action == SyncActionEnum.CHECK_IN:
                if not existing_log:
                    db.add(
                        ActivityLog(habit_id=activity.habit_id, activity_date=act_date)
                    )
                    processed += 1
            elif activity.action == SyncActionEnum.UNDO_CHECK_IN:
                if existing_log:
                    await db.delete(existing_log)
                    processed += 1

        await db.commit()
        return SyncActivityResponse(message="Sync successful", processed=processed)

    async def check_in(
        self,
        habit_id: str,
        user_id: str,
        payload: CheckInRequest,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        habit_check = await db.execute(
            select(Habit.id).where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        if not habit_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        if payload.date:
            try:
                act_date = date.fromisoformat(payload.date)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
                )
        else:
            act_date = datetime.now(UTC).date()

        existing = await db.execute(
            select(ActivityLog).where(
                ActivityLog.habit_id == habit_uuid,
                ActivityLog.activity_date == act_date,
            )
        )
        if not existing.scalars().first():
            db.add(ActivityLog(habit_id=habit_uuid, activity_date=act_date))
            await db.commit()

        undo_token = str(uuid4())
        cache_key = f"habit_undo:{user_id}:{habit_uuid}:{act_date.isoformat()}"
        await rdb.set(cache_key, undo_token, ex=300)

        return {
            "message": "Check-in successful",
            "date": act_date.isoformat(),
            "undo_token": undo_token,
            "undo_expires_in": 300,
        }

    async def undo_check_in(
        self,
        habit_id: str,
        user_id: str,
        payload: CheckInRequest,
        undo_token: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        try:
            habit_uuid = UUID(habit_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        habit_check = await db.execute(
            select(Habit.id).where(
                Habit.id == habit_uuid,
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        if not habit_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=HABIT_MESSAGES.NOT_FOUND
            )

        if payload.date:
            try:
                act_date = date.fromisoformat(payload.date)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
                )
        else:
            act_date = datetime.now(UTC).date()

        cache_key = f"habit_undo:{user_id}:{habit_uuid}:{act_date.isoformat()}"
        cached_token = await rdb.get(cache_key)

        if not cached_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=HABIT_MESSAGES.UNDO_EXPIRED,
            )

        cached_token_str = (
            cached_token.decode("utf-8")
            if isinstance(cached_token, bytes)
            else str(cached_token)
        )
        if cached_token_str != undo_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=HABIT_MESSAGES.INVALID_UNDO_TOKEN,
            )

        existing = await db.execute(
            select(ActivityLog).where(
                ActivityLog.habit_id == habit_uuid,
                ActivityLog.activity_date == act_date,
            )
        )
        existing_log = existing.scalars().first()
        if existing_log:
            await db.delete(existing_log)
            await db.commit()

        await rdb.delete(cache_key)

        return {"message": "Undo check-in successful", "date": act_date.isoformat()}
