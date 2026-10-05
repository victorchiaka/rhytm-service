"""Declarations of the tools exposed to the model, and their execution."""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.ai.base import Tool, ToolCall, ToolContext
from core.messages import HABIT_MESSAGES, ROUTINE_MESSAGES
from core.models.habit import Habit
from core.models.routine import PeriodOfDay, Routine
from core.schemas.habit import CreateHabitRequest, UpdateHabitRequest
from core.schemas.routine import CreateRoutineRequest, RoutineResponse
from core.services.habit import HabitService
from core.services.routine import RoutineService
from core.utils import (
    PERIOD_BOUNDS,
    check_reminder_before_routine,
    fmt_days,
    is_time_in_period,
)

logger = logging.getLogger(__name__)

DAY_RANGE = "days must be numbers 0-6 (0=Sunday, 6=Saturday)"
MAX_ROUTINES_PER_PERIOD = 3
HABIT_MINS = 15  # duration estimate per habit, same as the routine service


@dataclass(frozen=True)
class Plan:
    """A routine the model is proposing, normalized and ready to validate."""

    time_of_day: str
    days: list[int]
    habits: list[str]
    name: str = ""
    period: str | None = None  # explicit period_of_day, when the model picked one
    reminder_time: str | None = None


CHECK_ROUTINE_PLAN = Tool(
    name="check_routine_plan",
    description=(
        "Dry-runs a routine the user is considering: every conflict with their current "
        "routines and habits, plus free windows. Creates nothing. Call it as soon as the "
        "user names a time or days, before you propose the final plan."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Proposed routine name."},
            "time_of_day": {
                "type": "string",
                "description": "Start time: 24h HH:MM or natural, e.g. '5am' or '17:30'.",
            },
            "frequency": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 6},
                "description": "Days the routine runs: 0=Sunday ... 6=Saturday.",
            },
            "habits": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Habit names it would contain.",
            },
            "period_of_day": {
                "type": "string",
                "enum": ["Morning", "Afternoon", "Evening"],
                "description": "Optional. Derived from time_of_day when omitted.",
            },
            "reminder_time": {
                "type": "string",
                "description": "Optional reminder for habits it would create.",
            },
        },
        "required": ["time_of_day", "frequency", "habits"],
    },
)

SMART_CREATE_ROUTINE = Tool(
    name="smart_create_routine",
    description=(
        "Creates a routine for the user, reusing habits that already exist by name and "
        "creating the missing ones. Only call this after the user confirmed a plan whose "
        "check came back clean. Returns created:false with conflicts instead of creating "
        "when something clashes."
    ),
    parameters={
        **CHECK_ROUTINE_PLAN.parameters,
        "required": ["name", "time_of_day", "frequency", "habits"],
    },
)

UPDATE_ROUTINE = Tool(
    name="update_routine",
    description=(
        "Changes an existing routine by name. Omitted fields keep their current value. "
        "Returns updated:false with conflicts when the change would clash - nothing is "
        "applied in that case."
    ),
    parameters={
        "type": "object",
        "properties": {
            "routine": {
                "type": "string",
                "description": "Name (or id) of the routine to change.",
            },
            "name": {"type": "string", "description": "New routine name."},
            "time_of_day": {
                "type": "string",
                "description": "New start time: 24h HH:MM or natural, e.g. '5am'.",
            },
            "frequency": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 6},
                "description": "New days: 0=Sunday ... 6=Saturday.",
            },
            "habits": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full new habit list; missing ones are created.",
            },
            "period_of_day": {
                "type": "string",
                "enum": ["Morning", "Afternoon", "Evening"],
                "description": "Optional. Derived from time_of_day when omitted.",
            },
            "reminder_time": {
                "type": "string",
                "description": "Optional reminder for habits it would create.",
            },
        },
        "required": ["routine"],
    },
)

UPDATE_HABIT = Tool(
    name="update_habit",
    description=(
        "Changes an existing habit by name: its name, days or reminder. Omitted fields "
        "keep their current value. Returns updated:false with conflicts when the change "
        "would clash with its routines or other habits - nothing is applied then."
    ),
    parameters={
        "type": "object",
        "properties": {
            "habit": {
                "type": "string",
                "description": "Name (or id) of the habit to change.",
            },
            "name": {"type": "string", "description": "New habit name."},
            "days_of_week": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 6},
                "description": "New days: 0=Sunday ... 6=Saturday.",
            },
            "reminder_time": {
                "type": "string",
                "description": "New reminder (HH:MM or '5am'). Empty string removes it.",
            },
        },
        "required": ["habit"],
    },
)

CHECK_AVAILABLE_SLOTS = Tool(
    name="check_available_slots",
    description="Returns busy and free windows per day for the user's current routines.",
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 6},
                "description": "Optional. Defaults to every day.",
            }
        },
    },
)

TOOLS: tuple[Tool, ...] = (
    CHECK_ROUTINE_PLAN,
    SMART_CREATE_ROUTINE,
    UPDATE_ROUTINE,
    UPDATE_HABIT,
    CHECK_AVAILABLE_SLOTS,
)

Handler = Callable[[dict, ToolContext, AsyncSession], Awaitable[dict]]


async def _check_routine_plan(args: dict, ctx: ToolContext, db: AsyncSession) -> dict:
    return await _plan_report(_plan_from_args(args), ctx, db)


async def _smart_create_routine(args: dict, ctx: ToolContext, db: AsyncSession) -> dict:
    plan = _plan_from_args(args)
    if not plan.name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The routine needs a name."
        )
    report = await _plan_report(plan, ctx, db)
    if not report["ok"]:
        return {"created": False, **report}

    habit_ids = [
        await _resolve_habit_id(
            habit_name,
            days=plan.days,
            reminder=plan.reminder_time,
            ctx=ctx,
            db=db,
        )
        for habit_name in plan.habits
    ]
    routine = await RoutineService().create_routine(
        ctx.user_id,
        CreateRoutineRequest(
            name=plan.name,
            time_of_day=plan.time_of_day,
            period_of_day=PeriodOfDay(report["period_of_day"]),
            frequency=plan.days,
            habits=habit_ids,
        ),
        db,
    )
    return {"created": True, **_routine_view(routine)}


async def _update_routine(args: dict, ctx: ToolContext, db: AsyncSession) -> dict:
    current = await _find_routine(str(args.get("routine") or "").strip(), ctx, db)
    plan = Plan(
        name=str(args.get("name") or current.name).strip(),
        time_of_day=_hhmm(args.get("time_of_day") or current.time_of_day),
        period=_period_arg(args.get("period_of_day")),
        days=_days(args.get("frequency"), default=current.frequency),
        habits=_habit_names(args)
        or [habit.name for habit in current.habits if habit.deleted_at is None],
        reminder_time=_reminder_arg(args, default=None),
    )
    report = await _plan_report(plan, ctx, db, exclude=current.id)
    if not report["ok"]:
        return {"updated": False, **report}

    habit_ids = [
        await _resolve_habit_id(
            habit_name,
            days=plan.days,
            reminder=plan.reminder_time,
            ctx=ctx,
            db=db,
        )
        for habit_name in plan.habits
    ]
    routine = await RoutineService().update_routine(
        ctx.user_id,
        str(current.id),
        CreateRoutineRequest(
            name=plan.name,
            time_of_day=plan.time_of_day,
            period_of_day=PeriodOfDay(report["period_of_day"]),
            frequency=plan.days,
            habits=habit_ids,
        ),
        db,
    )
    return {"updated": True, **_routine_view(routine)}


async def _update_habit(args: dict, ctx: ToolContext, db: AsyncSession) -> dict:
    current = await _find_habit(str(args.get("habit") or "").strip(), ctx, db)
    name = str(args.get("name") or current.name).strip()
    days = _days(args.get("days_of_week"), default=current.days_of_week)
    reminder = _reminder_arg(args, default=current.reminder_time)

    conflicts = _habit_update_conflicts(
        current,
        name=name,
        days=days,
        reminder=reminder,
        habits=await _active_habits(ctx, db),
    )
    if conflicts:
        return {"updated": False, "ok": False, "conflicts": conflicts}

    habit = await HabitService().update_habit(
        str(current.id),
        ctx.user_id,
        ctx.subscription_status,
        UpdateHabitRequest(name=name, days_of_week=days, reminder_time=reminder),
        db,
    )
    return {
        "updated": True,
        "ok": True,
        "habit": {
            "habit_id": str(habit.id),
            "name": habit.name,
            "reminder_time": habit.reminder_time,
            "days_of_week": habit.days_of_week,
        },
    }


async def _check_available_slots(
    args: dict, ctx: ToolContext, db: AsyncSession
) -> dict:
    wanted = _days(args.get("days"), default=range(7))
    routines = await _active_routines(ctx, db)

    busy_by_day: dict[int, list[tuple[int, int, str]]] = {day: [] for day in wanted}
    for routine in routines:
        for day in set(routine.frequency) & set(wanted):
            busy_by_day[day].append((*_busy_window(routine), routine.name))

    return {
        "days": {
            str(day): {
                "busy": [
                    {"from": _clock(start), "to": _clock(end), "routine": name}
                    for start, end, name in sorted(busy)
                ],
                "free": {
                    period: windows
                    for period, bounds in PERIOD_BOUNDS.items()
                    if (windows := _free_windows(bounds, busy))
                },
            }
            for day, busy in busy_by_day.items()
        }
    }


# --- plan validation -------------------------------------------------------


async def _plan_report(
    plan: Plan, ctx: ToolContext, db: AsyncSession, *, exclude: UUID | None = None
) -> dict:
    """Dry-run a routine: every clash with the user's data, plus where the free slots are."""
    routines = await _active_routines(ctx, db)
    habits = await _active_habits(ctx, db)
    period, period_conflict = _resolve_period(plan.time_of_day, plan.period)

    conflicts: list[str] = [period_conflict] if period_conflict else []
    if plan.name and any(
        routine.name.casefold() == plan.name.casefold() and routine.id != exclude
        for routine in routines
    ):
        conflicts.append(ROUTINE_MESSAGES.DUPLICATE_NAME.format(name=plan.name))
    if period and (
        sum(
            1
            for routine in routines
            if routine.period_of_day.value == period and routine.id != exclude
        )
        >= MAX_ROUTINES_PER_PERIOD
    ):
        conflicts.append(
            ROUTINE_MESSAGES.MAX_ROUTINES_PER_PERIOD.format(
                max_count=MAX_ROUTINES_PER_PERIOD
            )
        )
    conflicts += _overlap_conflicts(plan, routines, exclude)
    conflicts += _habit_conflicts(plan, period, habits, exclude)
    conflicts += _plan_reminder_conflicts(plan, period, habits)

    conflicts = list(dict.fromkeys(conflicts))
    return {
        "ok": not conflicts,
        "period_of_day": period,
        "conflicts": conflicts,
        "free_windows": _free_for_plan(plan, routines, period),
    }


def _plan_from_args(args: dict) -> Plan:
    """Read a proposed routine from the model's args, normalizing any natural time."""
    habits = _habit_names(args)
    if not habits:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Add at least one habit to the routine.",
        )
    return Plan(
        name=str(args.get("name") or "").strip(),
        time_of_day=_hhmm(args.get("time_of_day")),
        period=_period_arg(args.get("period_of_day")),
        days=_days(args.get("frequency")),
        habits=habits,
        reminder_time=_reminder_arg(args, default=None),
    )


def _resolve_period(
    time_of_day: str, explicit: str | None
) -> tuple[str | None, str | None]:
    """(period, conflict): an explicit period must contain the time, else derive one."""
    if explicit:
        if is_time_in_period(explicit, time_of_day):
            return explicit, None
        start, end = PERIOD_BOUNDS[explicit]
        return None, ROUTINE_MESSAGES.INVALID_PERIOD_TIME.format(
            period=explicit, start=_clock(start), end=_clock(end)
        )
    try:
        return _period_for(time_of_day), None
    except HTTPException as exc:
        return None, str(exc.detail)


def _overlap_conflicts(
    plan: Plan, routines: list[Routine], exclude: UUID | None
) -> list[str]:
    start = _minutes(plan.time_of_day)
    end = start + _duration(len(plan.habits))
    conflicts = []
    for routine in routines:
        if routine.id == exclude:
            continue
        shared = set(plan.days) & set(routine.frequency or ())
        if not shared:
            continue
        other_start, other_end = _busy_window(routine)
        if max(start, other_start) < min(end, other_end):
            conflicts.append(
                ROUTINE_MESSAGES.ROUTINE_DURATION_OVERLAP.format(
                    existing_name=routine.name, days=fmt_days(sorted(shared))
                )
            )
    return conflicts


def _habit_conflicts(
    plan: Plan, period: str | None, habits: list[Habit], exclude: UUID | None
) -> list[str]:
    """Clashes with habits that already exist; habits the plan would create are built to fit."""
    if not period:
        return []
    by_name = {habit.name.casefold(): habit for habit in habits}
    conflicts: list[str] = []
    for name in plan.habits:
        habit = by_name.get(name.casefold())
        if habit is None:
            continue
        if not set(habit.days_of_week) <= set(plan.days):
            conflicts.append(
                ROUTINE_MESSAGES.HABIT_DAYS_OUT_OF_RANGE.format(habit_name=habit.name)
            )
        for routine in habit.routines:
            shared = set(plan.days) & set(routine.frequency or ())
            if (
                routine.deleted_at is None
                and routine.id != exclude
                and routine.period_of_day.value == period
                and shared
            ):
                conflicts.append(
                    ROUTINE_MESSAGES.HABIT_CONFLICT.format(
                        habit_name=habit.name,
                        period=period,
                        days=fmt_days(sorted(shared)),
                    )
                )
        if habit.reminder_time:
            conflicts += _reminder_issues(
                habit.name, habit.reminder_time, period, plan.time_of_day
            )
    return conflicts


def _plan_reminder_conflicts(
    plan: Plan, period: str | None, habits: list[Habit]
) -> list[str]:
    """Guards the reminder the plan would set on habits it has to create."""
    reminder = plan.reminder_time
    if not reminder:
        return []
    conflicts: list[str] = []
    if period:
        conflicts += _reminder_issues(
            ", ".join(plan.habits), reminder, period, plan.time_of_day
        )
    existing = {name.casefold() for name in plan.habits} & {
        habit.name.casefold() for habit in habits
    }
    clash = next(
        (
            habit
            for habit in habits
            if habit.name.casefold() not in existing
            and habit.reminder_time == reminder
            and set(habit.days_of_week) & set(plan.days)
        ),
        None,
    )
    if clash:
        conflicts.append(
            HABIT_MESSAGES.HABIT_TIME_CONFLICT.format(
                time=reminder,
                days=fmt_days(sorted(set(clash.days_of_week) & set(plan.days))),
            )
        )
    return conflicts


def _reminder_issues(
    name: str, reminder: str, period: str, time_of_day: str
) -> list[str]:
    conflicts: list[str] = []
    if not is_time_in_period(period, reminder):
        start, end = PERIOD_BOUNDS[period]
        conflicts.append(
            ROUTINE_MESSAGES.HABIT_REMINDER_OUT_OF_PERIOD.format(
                habit_name=name, reminder_time=reminder, period=period
            )
            + f" ({_clock(start)}-{_clock(end)})."
        )
    is_earlier, diff_mins, start_str = check_reminder_before_routine(
        time_of_day, reminder
    )
    if is_earlier:
        conflicts.append(
            ROUTINE_MESSAGES.HABIT_REMINDER_TOO_EARLY.format(
                habit_name=name, diff_mins=diff_mins, start=start_str
            )
        )
    return conflicts


def _habit_update_conflicts(
    habit: Habit,
    *,
    name: str,
    days: list[int],
    reminder: str | None,
    habits: list[Habit],
) -> list[str]:
    """What would block changing this habit: other habits, and the routines it belongs to."""
    conflicts: list[str] = []
    if reminder:
        for other in habits:
            shared = set(days) & set(other.days_of_week)
            if other.id != habit.id and other.reminder_time == reminder and shared:
                conflicts.append(
                    HABIT_MESSAGES.HABIT_TIME_CONFLICT.format(
                        time=reminder, days=fmt_days(sorted(shared))
                    )
                )
    for routine in habit.routines:
        if routine.deleted_at is not None:
            continue
        if not set(days) <= set(routine.frequency or ()):
            conflicts.append(
                HABIT_MESSAGES.HABIT_DAYS_EXCEED_ROUTINE.format(
                    habit_name=name, routine_name=routine.name
                )
            )
        if not reminder:
            continue
        period = routine.period_of_day.value
        if not is_time_in_period(period, reminder):
            conflicts.append(
                HABIT_MESSAGES.REMINDER_OUT_OF_PERIOD.format(
                    habit_name=name,
                    reminder_time=reminder,
                    routine_name=routine.name,
                    period=period,
                )
            )
        is_earlier, diff_mins, start_str = check_reminder_before_routine(
            routine.time_of_day, reminder
        )
        if is_earlier:
            conflicts.append(
                HABIT_MESSAGES.REMINDER_TOO_EARLY.format(
                    habit_name=name,
                    routine_name=routine.name,
                    diff_mins=diff_mins,
                    start=start_str,
                )
            )
    return list(dict.fromkeys(conflicts))


def _free_for_plan(
    plan: Plan, routines: list[Routine], period: str | None
) -> dict[str, list[str]]:
    """Windows in the plan's period that stay free on every day it would run."""
    if not period:
        return {}
    busy = [
        (*_busy_window(routine), routine.name)
        for routine in routines
        if set(plan.days) & set(routine.frequency or ())
    ]
    windows = _free_windows(PERIOD_BOUNDS[period], busy)
    return {period: windows} if windows else {}


# --- lookups ---------------------------------------------------------------


async def _active_routines(ctx: ToolContext, db: AsyncSession) -> list[Routine]:
    return (
        (
            await db.execute(
                select(Routine)
                .options(selectinload(Routine.habits))
                .where(Routine.user_id == ctx.user_id, Routine.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


async def _active_habits(ctx: ToolContext, db: AsyncSession) -> list[Habit]:
    return (
        (
            await db.execute(
                select(Habit)
                .options(selectinload(Habit.routines))
                .where(Habit.user_id == ctx.user_id, Habit.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


async def _find_routine(key: str, ctx: ToolContext, db: AsyncSession) -> Routine:
    routines = await _active_routines(ctx, db)
    wanted, wanted_id = key.casefold(), _uuid(key)
    routine = next(
        (r for r in routines if r.id == wanted_id or r.name.casefold() == wanted), None
    )
    if routine is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No active routine named '{key}'. Existing: "
            + (", ".join(r.name for r in routines) or "none")
            + ".",
        )
    return routine


async def _find_habit(key: str, ctx: ToolContext, db: AsyncSession) -> Habit:
    habits = await _active_habits(ctx, db)
    wanted, wanted_id = key.casefold(), _uuid(key)
    habit = next(
        (h for h in habits if h.id == wanted_id or h.name.casefold() == wanted), None
    )
    if habit is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No active habit named '{key}'. Existing: "
            + (", ".join(h.name for h in habits) or "none")
            + ".",
        )
    return habit


async def _resolve_habit_id(
    name: str,
    *,
    days: list[int],
    reminder: str | None,
    ctx: ToolContext,
    db: AsyncSession,
) -> UUID:
    existing = (
        (
            await db.execute(
                select(Habit).where(
                    Habit.user_id == ctx.user_id,
                    func.lower(Habit.name) == name.lower(),
                    Habit.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return existing.id

    habit = await HabitService().create_habit(
        ctx.user_id,
        ctx.subscription_status,
        CreateHabitRequest(name=name, days_of_week=days, reminder_time=reminder),
        db,
    )
    return habit.id


# --- execution -------------------------------------------------------------


_HANDLERS: dict[str, Handler] = {
    CHECK_ROUTINE_PLAN.name: _check_routine_plan,
    SMART_CREATE_ROUTINE.name: _smart_create_routine,
    UPDATE_ROUTINE.name: _update_routine,
    UPDATE_HABIT.name: _update_habit,
    CHECK_AVAILABLE_SLOTS.name: _check_available_slots,
}


async def execute_tool(call: ToolCall, ctx: ToolContext, db: AsyncSession) -> dict:
    """Runs a tool for the model: `{"result": ...}` on success, `{"error": ...}` on failure."""
    handler = _HANDLERS.get(call.name)
    if handler is None:
        return {"error": f"Unknown tool '{call.name}'."}
    try:
        return {"result": await handler(call.args, ctx, db)}
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except ValidationError as exc:
        return {"error": "; ".join(err["msg"] for err in exc.errors())}
    except Exception:
        logger.exception("Tool %s failed", call.name)
        return {"error": "The tool could not run. Please try again."}


# --- primitives ------------------------------------------------------------


_HHMM_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


def _hhmm(raw: Any) -> str:
    """Normalize whatever the user said into zero-padded 24-hour HH:MM ('5am' -> 05:00)."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A start time is required: 24-hour HH:MM, e.g. 06:00 or '5am'.",
        )
    match = _HHMM_RE.match(text)
    if not match:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{text}' is not a time. Use 24-hour HH:MM (e.g. 05:30) or a time like '5am'.",
        )
    hour, minute, meridiem = int(match[1]), int(match[2] or 0), (match[3] or "").lower()
    if (
        minute > 59
        or (meridiem and not 1 <= hour <= 12)
        or (not meridiem and hour > 23)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"'{text}' is not a valid time."
        )
    if meridiem:
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    return f"{hour:02d}:{minute:02d}"


def _period_arg(raw: Any) -> str | None:
    if not raw:
        return None
    try:
        return PeriodOfDay(str(raw)).value
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "period_of_day must be Morning, Afternoon or Evening.",
        )


def _reminder_arg(args: dict, *, default: str | None) -> str | None:
    """Omitted keeps the current reminder; an empty value removes it."""
    if "reminder_time" not in args:
        return default
    raw = args.get("reminder_time")
    return None if raw is None or not str(raw).strip() else _hhmm(raw)


def _habit_names(args: dict) -> list[str]:
    return list(
        dict.fromkeys(
            name for raw in args.get("habits") or [] if (name := str(raw).strip())
        )
    )


def _routine_view(routine: RoutineResponse) -> dict:
    return {
        "routine_id": str(routine.id),
        "name": routine.name,
        "time_of_day": routine.time_of_day,
        "period_of_day": routine.period_of_day,
        "frequency": routine.frequency,
        "habits": [habit.name for habit in routine.habits],
    }


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _days(raw: Any, default: Any = ()) -> list[int]:
    if raw is None:
        raw = default
    try:
        days = sorted({int(day) for day in raw})
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, DAY_RANGE)
    if not days or any(day not in range(7) for day in days):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, DAY_RANGE)
    return days


def _minutes(clock: str) -> int:
    try:
        hours, minutes = map(int, str(clock).split(":"))
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"'{clock}' must be in HH:MM format."
        )
    return hours * 60 + minutes


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _duration(habit_count: int) -> int:
    """How long the routine occupies its slot: 15m per habit, 30m floor."""
    return max(30, habit_count * HABIT_MINS)


def _period_for(time_of_day: str) -> str:
    """Derive period of day from a start time, rejecting times outside every period."""
    minutes = _minutes(time_of_day)
    for period, (start, end) in PERIOD_BOUNDS.items():
        if start <= minutes <= end:
            return period
    windows = ", ".join(
        f"{period} {_clock(start)}-{_clock(end)}"
        for period, (start, end) in PERIOD_BOUNDS.items()
    )
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        f"'{time_of_day}' falls outside the allowed periods: {windows}.",
    )


def _busy_window(routine: Routine) -> tuple[int, int]:
    """A routine's slot, the way the routine service estimates it."""
    start = _minutes(routine.time_of_day)
    habits = len([habit for habit in routine.habits if habit.deleted_at is None])
    return start, start + _duration(habits)


def _free_windows(
    bounds: tuple[int, int], busy: list[tuple[int, int, str]]
) -> list[str]:
    """Cut the busy intervals out of a period's bounds, leaving free HH:MM-HH:MM windows."""
    period_start, period_end = bounds
    windows: list[tuple[int, int]] = []
    cursor = period_start
    for start, end, _ in sorted(busy):
        if end <= cursor:
            continue
        if start >= period_end:
            break
        if start > cursor:
            windows.append((cursor, start))
        cursor = max(cursor, end)
        if cursor >= period_end:
            break
    if cursor < period_end:
        windows.append((cursor, period_end))
    return [f"{_clock(start)}-{_clock(end)}" for start, end in windows]
