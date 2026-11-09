import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import ROUTINE_MESSAGES
from core.models.habit import Habit
from core.models.routine import Deletion, DeletionMode, PeriodOfDay, Routine
from core.schemas.habit import HabitResponse
from core.schemas.routine import (
    CreateRoutineRequest,
    OtherRoutineInfo,
    RoutineResponse,
    SharedHabitInfo,
)
from core.utils import check_reminder_before_routine, fmt_days, is_time_in_period


class RoutineService:
    @staticmethod
    async def _check_name_collision(
        db: AsyncSession,
        user_id: str,
        name: str,
        exclude_routine_id: UUID | None = None,
    ) -> None:
        query = select(Routine).where(
            Routine.user_id == user_id,
            func.lower(Routine.name) == name.strip().lower(),
            Routine.deleted_at.is_(None),
        )
        if exclude_routine_id:
            query = query.where(Routine.id != exclude_routine_id)

        result = await db.execute(query)
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
        db: AsyncSession,
        user_id: str,
        period: PeriodOfDay,
        max_limit: int = 3,
        exclude_routine_id: UUID | None = None,
    ) -> None:
        """Cap the maximum number of routines allowed per period (default 3)."""
        query = select(func.count(Routine.id)).where(
            Routine.user_id == user_id,
            Routine.period_of_day == period,
            Routine.deleted_at.is_(None),
        )
        if exclude_routine_id:
            query = query.where(Routine.id != exclude_routine_id)

        result = await db.execute(query)
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
        exclude_routine_id: UUID | None = None,
    ) -> None:
        """Ensure routine does not physically overlap with existing routines on overlapping days based on start time + estimated duration (15m per habit, min 30m)."""
        h, m = map(int, time_str.split(":"))
        target_start = h * 60 + m
        target_duration = max(30, num_habits * estimated_mins_per_habit)
        target_end = target_start + target_duration

        query = (
            select(Routine)
            .options(selectinload(Routine.habits.and_(Habit.deleted_at.is_(None))))
            .where(
                Routine.user_id == user_id,
                Routine.frequency.overlap(frequency),
                Routine.deleted_at.is_(None),
            )
        )
        if exclude_routine_id:
            query = query.where(Routine.id != exclude_routine_id)

        result = await db.execute(query)
        existing_routines = result.scalars().all()

        for existing in existing_routines:
            eh, em = map(int, existing.time_of_day.split(":"))
            existing_start = eh * 60 + em
            existing_habit_count = len(existing.habits)
            existing_duration = max(30, existing_habit_count * estimated_mins_per_habit)
            existing_end = existing_start + existing_duration

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
        time_of_day: str,
        exclude_routine_id: UUID | None = None,
    ) -> list[Habit]:
        """Fetch habits by ID and validate each is eligible to join the routine."""
        habits: list[Habit] = []
        freq_set = set(frequency)
        period_val = period.value if hasattr(period, "value") else str(period)

        result = await db.execute(
            select(Habit)
            .options(selectinload(Habit.routines.and_(Routine.deleted_at.is_(None))))
            .where(
                Habit.id.in_(habit_ids),
                Habit.user_id == user_id,
                Habit.deleted_at.is_(None),
            )
        )
        habit_map = {h.id: h for h in result.scalars().all()}

        for habit_id in habit_ids:
            habit = habit_map.get(habit_id)
            if not habit:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ROUTINE_MESSAGES.HABIT_NOT_FOUND.format(habit_id=habit_id),
                )
            habits.append(habit)

            conflict = next(
                (
                    r
                    for r in habit.routines
                    if r.id != exclude_routine_id
                    and r.period_of_day == period
                    and (set(r.frequency or []) & freq_set)
                ),
                None,
            )
            if conflict:
                overlap = freq_set & set(conflict.frequency or [])
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ROUTINE_MESSAGES.HABIT_CONFLICT.format(
                        habit_name=habit.name,
                        period=period_val,
                        days=fmt_days(list(overlap)),
                    ),
                )

            if habit.reminder_time:
                if not is_time_in_period(period, habit.reminder_time):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=ROUTINE_MESSAGES.HABIT_REMINDER_OUT_OF_PERIOD.format(
                            habit_name=habit.name,
                            reminder_time=habit.reminder_time,
                            period=(
                                period.value
                                if hasattr(period, "value")
                                else str(period)
                            ),
                        ),
                    )

                is_earlier, diff_mins, start_str = check_reminder_before_routine(
                    time_of_day, habit.reminder_time
                )
                if is_earlier:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=ROUTINE_MESSAGES.HABIT_REMINDER_TOO_EARLY.format(
                            habit_name=habit.name,
                            diff_mins=diff_mins,
                            start=start_str,
                        ),
                    )

            if not set(habit.days_of_week).issubset(set(frequency)):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=ROUTINE_MESSAGES.HABIT_DAYS_OUT_OF_RANGE.format(
                        habit_name=habit.name
                    ),
                )

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
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
        )
        return refreshed_result.scalar_one()

    async def create_routine(
        self,
        user_id: str,
        payload: CreateRoutineRequest,
        db: AsyncSession,
    ) -> dict:
        """Create a new routine and link existing habits to it."""
        frequency: list[int] = sorted(set(payload.frequency))
        period: PeriodOfDay = payload.period_of_day

        await self._check_name_collision(db, user_id, payload.name)
        self._validate_period_time_range(period, payload.time_of_day)
        await self._check_period_routine_limit(db, user_id, period, max_limit=3)

        habits = await self._validate_habits_for_routine(
            db, user_id, payload.habits, period, frequency, payload.time_of_day
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

        return {
            "message": ROUTINE_MESSAGES.CREATED,
            "routine": RoutineResponse.model_validate(routine).model_dump(),
        }

    async def get_all(self, user_id: str, db: AsyncSession) -> list[RoutineResponse]:
        result = await db.execute(
            select(Routine)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
            .where(Routine.user_id == user_id, Routine.deleted_at.is_(None))
        )
        routines = result.scalars().all()
        return [RoutineResponse.model_validate(r) for r in routines]

    async def get_by_day(
        self, user_id: str, day_digit: int, db: AsyncSession
    ) -> list[RoutineResponse]:
        invalid_day = HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ROUTINE_MESSAGES.INVALID_DAY_DIGIT,
        )

        if day_digit not in range(7):
            raise invalid_day

        result = await db.execute(
            select(Routine)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
            .where(
                Routine.user_id == user_id,
                Routine.frequency.contains([day_digit]),
                Routine.deleted_at.is_(None),
            )
        )
        routines = result.scalars().all()
        return [RoutineResponse.model_validate(r) for r in routines]

    async def get_routine(
        self, user_id: str, routine_id: str, db: AsyncSession
    ) -> RoutineResponse:
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ROUTINE_MESSAGES.NOT_FOUND,
        )

        try:
            routine_uuid = UUID(routine_id)
        except (ValueError, TypeError):
            raise not_found

        result = await db.execute(
            select(Routine)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
            .where(
                Routine.id == routine_uuid,
                Routine.user_id == user_id,
                Routine.deleted_at.is_(None),
            )
        )
        routine = result.scalar_one_or_none()
        if not routine:
            raise not_found

        return RoutineResponse.model_validate(routine)

    async def update_routine(
        self,
        user_id: str,
        id: str,
        payload: CreateRoutineRequest,
        db: AsyncSession,
    ) -> dict:
        """Update an existing routine and re-evaluate all guardrail checks."""
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ROUTINE_MESSAGES.NOT_FOUND,
        )

        try:
            routine_uuid = UUID(id)
        except (ValueError, TypeError):
            raise not_found

        result = await db.execute(
            select(Routine)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
            .where(
                Routine.id == routine_uuid,
                Routine.user_id == user_id,
                Routine.deleted_at.is_(None),
            )
        )
        routine = result.scalars().first()
        if not routine:
            raise not_found

        frequency: list[int] = sorted(set(payload.frequency))
        period: PeriodOfDay = payload.period_of_day

        await self._check_name_collision(
            db, user_id, payload.name, exclude_routine_id=routine_uuid
        )
        self._validate_period_time_range(period, payload.time_of_day)
        await self._check_period_routine_limit(
            db, user_id, period, max_limit=3, exclude_routine_id=routine_uuid
        )

        habits = await self._validate_habits_for_routine(
            db,
            user_id,
            payload.habits,
            period,
            frequency,
            payload.time_of_day,
            exclude_routine_id=routine_uuid,
        )

        await self._check_routine_time_spacing(
            db,
            user_id,
            payload.time_of_day,
            frequency,
            num_habits=len(habits),
            exclude_routine_id=routine_uuid,
        )

        routine.name = payload.name.strip()
        routine.time_of_day = payload.time_of_day
        routine.period_of_day = period
        routine.frequency = frequency
        routine.habits = habits

        await db.commit()

        refreshed_result = await db.execute(
            select(Routine)
            .where(Routine.id == routine.id)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs)
            )
        )
        updated_routine = refreshed_result.scalar_one()

        return {
            "message": ROUTINE_MESSAGES.UPDATED,
            "routine": RoutineResponse.model_validate(updated_routine).model_dump(),
        }

    async def check_routine_deletion(
        self, user_id: str, routine_id: str, db: AsyncSession, rdb: Redis
    ) -> dict:
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ROUTINE_MESSAGES.NOT_FOUND,
        )

        try:
            routine_uuid = UUID(routine_id)
        except (ValueError, TypeError):
            raise not_found

        result = await db.execute(
            select(Routine)
            .options(
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.routines.and_(Routine.deleted_at.is_(None))),
                selectinload(
                    Routine.habits.and_(Habit.deleted_at.is_(None))
                ).selectinload(Habit.activity_logs),
            )
            .where(
                Routine.id == routine_uuid,
                Routine.user_id == user_id,
                Routine.deleted_at.is_(None),
            )
        )
        routine = result.scalar_one_or_none()
        if not routine:
            raise not_found

        exclusive_habits: list[HabitResponse] = []
        shared_habits: list[SharedHabitInfo] = []
        exclusive_habit_ids: list[str] = []

        for habit in routine.habits:
            active_other_routines = [r for r in habit.routines if r.id != routine.id]
            habit_resp = HabitResponse.model_validate(habit)
            if not active_other_routines:
                exclusive_habits.append(habit_resp)
                exclusive_habit_ids.append(str(habit.id))
            else:
                other_info = [
                    OtherRoutineInfo(id=r.id, name=r.name)
                    for r in active_other_routines
                ]
                shared_habits.append(
                    SharedHabitInfo(habit=habit_resp, other_routines=other_info)
                )

        token = uuid4().hex
        snapshot_payload = {
            "routine_id": str(routine.id),
            "user_id": user_id,
            "exclusive_habit_ids": exclusive_habit_ids,
        }
        await rdb.set(f"delete_token:{token}", json.dumps(snapshot_payload), ex=300)

        return {
            "message": ROUTINE_MESSAGES.DELETE_CHECK_SUCCESS,
            "routine_id": routine.id,
            "routine_name": routine.name,
            "exclusive_habits": [h.model_dump() for h in exclusive_habits],
            "shared_habits": [s.model_dump() for s in shared_habits],
            "token": token,
        }

    async def confirm_routine_deletion(
        self,
        user_id: str,
        routine_id: str,
        mode: str,
        token: str | None,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ROUTINE_MESSAGES.NOT_FOUND,
        )
        invalid_mode = HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid deletion mode.",
        )
        invalid_token_422 = HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ROUTINE_MESSAGES.INVALID_DELETE_TOKEN,
        )
        invalid_token_400 = HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ROUTINE_MESSAGES.INVALID_DELETE_TOKEN,
        )

        try:
            routine_uuid = UUID(routine_id)
        except (ValueError, TypeError):
            raise not_found

        if mode not in (DeletionMode.ROUTINE_ONLY.value, DeletionMode.EXCLUSIVE.value):
            raise invalid_mode

        if mode == DeletionMode.EXCLUSIVE.value:
            if not token:
                raise invalid_token_422
            raw_snapshot = await rdb.get(f"delete_token:{token}")
            if not raw_snapshot:
                raise invalid_token_400
            snapshot = json.loads(raw_snapshot)
            if (
                snapshot.get("routine_id") != str(routine_uuid)
                or snapshot.get("user_id") != user_id
            ):
                raise invalid_token_400

        result = await db.execute(
            select(Routine)
            .options(
                selectinload(Routine.habits).selectinload(
                    Habit.routines.and_(Routine.deleted_at.is_(None))
                ),
                selectinload(Routine.habits).selectinload(Habit.activity_logs),
            )
            .where(
                Routine.id == routine_uuid,
                Routine.user_id == user_id,
            )
            .with_for_update()
        )
        routine = result.scalar_one_or_none()
        if not routine:
            raise not_found

        if routine.deleted_at is not None:
            if routine.deletion_id:
                del_result = await db.execute(
                    select(Deletion).where(Deletion.id == routine.deletion_id)
                )
                deletion_rec = del_result.scalar_one_or_none()
                if deletion_rec:
                    deleted_habits = [
                        HabitResponse.model_validate(h)
                        for h in routine.habits
                        if h.deletion_id == deletion_rec.id
                    ]
                    return {
                        "message": ROUTINE_MESSAGES.DELETION_CONFIRMED,
                        "deletion_id": deletion_rec.id,
                        "routine_id": routine.id,
                        "mode": deletion_rec.mode.value,
                        "habits_deleted_count": len(deleted_habits),
                        "deleted_habits": [h.model_dump() for h in deleted_habits],
                        "undo_deadline": deletion_rec.deadline,
                    }
            raise not_found

        now_utc = datetime.now(timezone.utc)
        deadline = now_utc + timedelta(seconds=14)

        deletion_rec = Deletion(
            user_id=UUID(user_id),
            routine_id=routine_uuid,
            mode=DeletionMode(mode),
            deadline=deadline,
        )
        db.add(deletion_rec)
        await db.flush()

        routine.deleted_at = now_utc
        routine.deletion_id = deletion_rec.id

        deleted_habits: list[HabitResponse] = []

        if mode == DeletionMode.EXCLUSIVE.value:
            for habit in routine.habits:
                if habit.deleted_at is not None:
                    continue
                other_active = [r for r in habit.routines if r.id != routine.id]
                if not other_active:
                    habit.deleted_at = now_utc
                    habit.deletion_id = deletion_rec.id
                    deleted_habits.append(HabitResponse.model_validate(habit))

        await db.commit()

        if token:
            await rdb.delete(f"delete_token:{token}")

        return {
            "message": ROUTINE_MESSAGES.DELETION_CONFIRMED,
            "deletion_id": deletion_rec.id,
            "routine_id": routine.id,
            "mode": mode,
            "habits_deleted_count": len(deleted_habits),
            "deleted_habits": [h.model_dump() for h in deleted_habits],
            "undo_deadline": deadline,
        }

    async def undo_deletion(
        self, user_id: str, deletion_id: str, db: AsyncSession
    ) -> dict:
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ROUTINE_MESSAGES.DELETION_NOT_FOUND,
        )
        undo_expired = HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ROUTINE_MESSAGES.UNDO_EXPIRED,
        )

        try:
            del_uuid = UUID(deletion_id)
        except (ValueError, TypeError):
            raise not_found

        del_result = await db.execute(
            select(Deletion).where(
                Deletion.id == del_uuid,
                Deletion.user_id == UUID(user_id),
            )
        )
        deletion_rec = del_result.scalar_one_or_none()
        if not deletion_rec:
            raise not_found

        now_utc = datetime.now(timezone.utc)
        if now_utc > deletion_rec.deadline:
            raise undo_expired

        r_result = await db.execute(
            select(Routine)
            .options(selectinload(Routine.habits).selectinload(Habit.activity_logs))
            .where(Routine.deletion_id == del_uuid)
        )
        routines = r_result.scalars().all()
        for routine in routines:
            routine.deleted_at = None
            routine.deletion_id = None

        h_result = await db.execute(select(Habit).where(Habit.deletion_id == del_uuid))
        habits = h_result.scalars().all()
        for habit in habits:
            habit.deleted_at = None
            habit.deletion_id = None

        await db.delete(deletion_rec)
        await db.commit()

        if routines:
            restored_routine = routines[0]
            refreshed = (
                await db.execute(
                    select(Routine)
                    .options(
                        selectinload(
                            Routine.habits.and_(Habit.deleted_at.is_(None))
                        ).selectinload(Habit.activity_logs)
                    )
                    .where(Routine.id == restored_routine.id)
                )
            ).scalar_one()
            return {
                "message": ROUTINE_MESSAGES.UNDO_SUCCESS,
                "restored_routine": RoutineResponse.model_validate(
                    refreshed
                ).model_dump(),
            }
        else:
            raise not_found
