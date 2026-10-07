"""Per-request snapshot of a user's habits and routines, fed to the model as context."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.models.habit import Habit
from core.models.routine import Routine
from core.utils import fmt_days


async def build_user_context(user_id: str, db: AsyncSession) -> str:
    habits = (
        (
            await db.execute(
                select(Habit)
                .where(Habit.user_id == user_id, Habit.deleted_at.is_(None))
                .order_by(Habit.name)
            )
        )
        .scalars()
        .all()
    )
    routines = (
        (
            await db.execute(
                select(Routine)
                .options(selectinload(Routine.habits))
                .where(Routine.user_id == user_id, Routine.deleted_at.is_(None))
                .order_by(Routine.time_of_day)
            )
        )
        .scalars()
        .all()
    )
    return _render(habits, routines)


def _render(habits: list[Habit], routines: list[Routine]) -> str:
    habit_lines = [
        f"- {h.name} | reminder {h.reminder_time or 'none'} | days {fmt_days(h.days_of_week)}"
        for h in habits
    ]
    routine_lines = [
        f"- {r.name} | {r.time_of_day} {r.period_of_day.value} | days {fmt_days(r.frequency) or '-'} "
        f"| habits: {', '.join(h.name for h in r.habits if h.deleted_at is None)}"
        for r in routines
    ]
    return "\n".join(
        [
            "# User context",
            f"## Habits ({len(habits)})",
            *(habit_lines or ["- none"]),
            f"## Routines ({len(routines)})",
            *(routine_lines or ["- none"]),
            "## Limits",
            "- Max 3 routines per period of day (Morning, Afternoon, Evening).",
            "- A habit's reminder time must sit inside the routine's period and not before the routine starts.",
            "- A habit can only join routines that run on all of its days.",
        ]
    )
