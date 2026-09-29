import asyncio
import io
import json
import logging
import os
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import jwt
from dotenv import load_dotenv
from fastapi import HTTPException, Response, status
from fpdf import FPDF
from openpyxl import Workbook
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.messages import USER_MESSAGES
from core.models.habit import ActivityLog, Habit
from core.models.routine import Routine
from core.models.user import Session, User
from core.schemas.user import UserResponse
from core.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from core.utils import attach_activity, send_email_otp, send_welcome_mail
from db.database import AsyncSessionLocal

load_dotenv()

logger = logging.getLogger(__name__)

SIGNUP_OTP_EXPIRY_SECONDS = 600
RESET_PASSWORD_OTP_EXPIRY_SECONDS = 600

ENVIRONMENT = str(os.getenv("ENVIRONMENT"))

_DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

_MOCK_ROUTINE_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_MOCK_ROUTINE_2_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_MOCK_HABIT_1_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_MOCK_HABIT_2_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_MOCK_HABIT_3_ID = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
_MOCK_HABIT_4_ID = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
_MOCK_HABIT_5_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_MOCK_LOG_WINDOW_DAYS = 42


def _build_mock_export_data() -> dict:
    """Rich dev-only fixture shaped exactly like the real export queries."""
    morning_routine = SimpleNamespace(
        id=_MOCK_ROUTINE_ID,
        name="Morning Warmup",
        period_of_day="Morning",
        time_of_day="07:00",
        frequency=[0, 1, 2, 3, 4, 5, 6],
    )
    evening_routine = SimpleNamespace(
        id=_MOCK_ROUTINE_2_ID,
        name="Evening Wind-down",
        period_of_day="Evening",
        time_of_day="21:00",
        frequency=[1, 2, 3, 4, 5],
    )

    drink_water = SimpleNamespace(
        id=_MOCK_HABIT_1_ID,
        name="Drink Water",
        reminder_time="07:15",
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        routines=[morning_routine],
    )
    stretch = SimpleNamespace(
        id=_MOCK_HABIT_2_ID,
        name="Stretch",
        reminder_time="07:30",
        days_of_week=[1, 2, 3, 4, 5],
        routines=[morning_routine],
    )
    workout = SimpleNamespace(
        id=_MOCK_HABIT_3_ID,
        name="Home Workout",
        reminder_time="21:30",
        days_of_week=[1, 3, 5],
        routines=[evening_routine],
    )
    journal = SimpleNamespace(
        id=_MOCK_HABIT_4_ID,
        name="Journal",
        reminder_time="08:00",
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        routines=[],
    )
    read_pages = SimpleNamespace(
        id=_MOCK_HABIT_5_ID,
        name="Read 20 Pages",
        reminder_time="22:00",
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        routines=[],
    )

    habits = [drink_water, stretch, workout, journal, read_pages]

    logs = []
    today = datetime.now(UTC).date()
    for offset in range(_MOCK_LOG_WINDOW_DAYS):
        day = today - timedelta(days=offset)
        weekday = (day.weekday() + 1) % 7  # 0 = Sunday, matches Habit.days_of_week

        logs.append(SimpleNamespace(habit_id=drink_water.id, activity_date=day))
        if weekday in (1, 2, 3, 4, 5) and offset % 9 != 0:
            logs.append(SimpleNamespace(habit_id=stretch.id, activity_date=day))
        if weekday in (1, 3, 5) and offset % 14 != 0:
            logs.append(SimpleNamespace(habit_id=workout.id, activity_date=day))
        if offset % 3 != 0:
            logs.append(SimpleNamespace(habit_id=journal.id, activity_date=day))
        if offset % 4 != 0:
            logs.append(SimpleNamespace(habit_id=read_pages.id, activity_date=day))

    return {
        "routines": [morning_routine, evening_routine],
        "habits": habits,
        "logs": logs,
    }


MOCK_EXPORT_DATA = _build_mock_export_data()


class UserService:
    @staticmethod
    def _issue_tokens(user_id, email) -> tuple[str, str]:
        """Create a fresh access + refresh token pair."""
        token_payload = {"sub": str(user_id), "email": email}
        access_token = create_token(data=token_payload, token_type="access")
        refresh_token = create_token(data=token_payload, token_type="refresh")
        return access_token, refresh_token

    @staticmethod
    async def _persist_session(db: AsyncSession, user_id, refresh_token: str) -> None:
        """Decode the refresh token and store a hashed session row."""
        decoded_refresh = decode_token(refresh_token, expected_type="refresh")
        refresh_exp = datetime.fromtimestamp(decoded_refresh["exp"], tz=UTC)

        session_entry = Session(
            user_id=user_id,
            refresh_token_hash=hash_token(refresh_token),
            expires_at=refresh_exp,
        )
        db.add(session_entry)
        await db.commit()

    @staticmethod
    def _write_performance_sheet(ws, stats: dict) -> None:
        ws.append(["Metric", "Value"])
        if stats["member_since"]:
            ws.append(["Member since", str(stats["member_since"])])
        if stats["plan"]:
            ws.append(["Plan", str(stats["plan"])])
        if stats["plan_expires_at"]:
            ws.append(["Plan expires", str(stats["plan_expires_at"])])
        ws.append(["Routines", stats["routines"]])
        ws.append(["Habits", stats["habits"]])
        ws.append(["Standalone habits", stats["standalone_habits"]])
        ws.append(["Total lifetime check-ins", stats["total_checkins"]])
        ws.append(["Active days", stats["active_days"]])
        if stats["window_start"]:
            ws.append(
                ["Activity range", f"{stats['window_start']} to {stats['window_end']}"]
            )
        ws.append(["Check-ins (last 7 days)", stats["last_7"]])
        ws.append(["Check-ins (last 30 days)", stats["last_30"]])
        ws.append(["Current streak (days)", stats["current_streak"]])
        ws.append(["Longest streak (days)", stats["longest_streak"]])
        if stats["overall_completion"] is not None:
            ws.append(["Overall completion rate (%)", stats["overall_completion"]])

        ws.append([""])
        ws.append(
            [
                "Habit",
                "Check-ins",
                "Current streak (days)",
                "Longest streak (days)",
                "Completion (%)",
                "Last check-in",
            ]
        )
        for entry in stats["habit_stats"]:
            ws.append(
                [
                    entry["name"],
                    entry["checkins"],
                    entry["current_streak"],
                    entry["longest_streak"],
                    round(entry["completion"], 1)
                    if entry["completion"] is not None
                    else "",
                    str(entry["last_checkin"]) if entry["last_checkin"] else "",
                ]
            )

        ws.append([""])
        ws.append(["Day", "Check-ins"])
        for name, count in zip(_DAY_NAMES, stats["by_weekday"]):
            ws.append([name, count])

        ws.append([""])
        ws.append(["Month", "Check-ins"])
        for month_key, count in stats["by_month"]:
            ws.append([month_key, count])

    @staticmethod
    async def _generate_excel_bytes(
        user: User, routines: Routine, habits: Habit, logs: ActivityLog
    ):
        stats = UserService._build_performance_stats(user, routines, habits, logs)
        habits_by_routine, standalone_habits = UserService._group_habits(habits)
        wb = Workbook()

        ws_profile = wb.active
        ws_profile.title = "Profile summary"
        ws_profile.append(["Name", user.full_name])
        ws_profile.append(["Email", user.email])
        ws_profile.append(["Subscription", user.subscription_status])
        ws_profile.append(["Member since", user.created_at.strftime("%Y-%m-%d")])

        UserService._write_performance_sheet(wb.create_sheet("Performance", 1), stats)
        ws_routines = wb.create_sheet("Routines")
        ws_routines.append(["Routine name", "Period", "Time", "Days", "Habit", "Reminder", "Habit Days"])
        for r in routines:
            freq = UserService._format_days(r.frequency) if r.frequency else "None"
            for h in habits_by_routine.get(r.id, ()):
                days = UserService._format_days(h.days_of_week)
                ws_routines.append([r.name, UserService._period_label(r.period_of_day), r.time_of_day, freq, h.name, str(h.reminder_time or "None"), days])

        ws_habits = wb.create_sheet("Standalone Habits")
        ws_habits.append(["Habit name", "Reminder", "Days"])
        for h in standalone_habits:
            days = UserService._format_days(h.days_of_week)
            ws_habits.append([h.name, str(h.reminder_time or "None"), days])

        ws_logs = wb.create_sheet("Activity History")
        ws_logs.append(["Date", "Habit name"])
        habit_map = {h.id: h.name for h in habits}
        for log in logs:
            ws_logs.append(
                [
                    log.activity_date.strftime("%Y-%m-%d"),
                    habit_map.get(log.habit_id, "Unknown"),
                ]
            )

        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    @staticmethod
    def _format_days(days: list[int]) -> str:
        if not days:
            return "None"
        return ", ".join(_DAY_NAMES[d] for d in sorted(days))

    @staticmethod
    def _period_label(value) -> str:
        """Render PeriodOfDay enums as 'Morning' instead of 'PeriodOfDay.MORNING'."""
        return str(getattr(value, "value", value))

    @staticmethod
    def _group_habits(habits) -> tuple[dict[uuid.UUID, list], list]:
        """Split habits into a routine-id -> habits map and a standalone list."""
        by_routine: dict[uuid.UUID, list] = {}
        standalone = []
        for h in habits:
            h_routines = getattr(h, "routines", None) or []
            if not h_routines:
                standalone.append(h)
            for r in h_routines:
                by_routine.setdefault(r.id, []).append(h)
        return by_routine, standalone

    @staticmethod
    def _activity_streaks(dates: set[date], today: date) -> tuple[int, int]:
        """Return (current_streak, longest_streak) in consecutive days."""
        if not dates:
            return 0, 0

        current = 0
        if today in dates or (today - timedelta(days=1)) in dates:
            cursor = today if today in dates else today - timedelta(days=1)
            while cursor in dates:
                current += 1
                cursor -= timedelta(days=1)

        longest = 0
        run = 0
        previous = None
        for day in sorted(dates):
            run = run + 1 if previous is not None and (day - previous).days == 1 else 1
            longest = max(longest, run)
            previous = day
        return current, longest

    @staticmethod
    def _weekday_counts(
        window_start: date | None, window_end: date | None
    ) -> list[int] | None:
        """Occurrences of each weekday (index 0 = Sun) in [window_start, window_end]."""
        if window_start is None or window_end is None or window_end < window_start:
            return None
        total_days = (window_end - window_start).days + 1
        full_weeks, remainder = divmod(total_days, 7)
        counts = [full_weeks] * 7
        first = (window_start.weekday() + 1) % 7
        for offset in range(remainder):
            counts[(first + offset) % 7] += 1
        return counts

    @staticmethod
    def _build_performance_stats(user, routines, habits, logs) -> dict:
        today = datetime.now(UTC).date()
        cutoff_7 = today - timedelta(days=6)
        cutoff_30 = today - timedelta(days=29)

        log_counts: dict[uuid.UUID, int] = {}
        habit_dates: dict[uuid.UUID, set[date]] = {}
        by_weekday = [0] * 7
        by_month: dict[str, int] = {}
        all_dates: set[date] = set()
        last_7 = 0
        last_30 = 0

        for log in logs:
            day = log.activity_date
            habit_id = log.habit_id
            log_counts[habit_id] = log_counts.get(habit_id, 0) + 1
            habit_dates.setdefault(habit_id, set()).add(day)
            all_dates.add(day)
            by_weekday[(day.weekday() + 1) % 7] += 1
            month_key = f"{day.year:04d}-{day.month:02d}"
            by_month[month_key] = by_month.get(month_key, 0) + 1
            if day >= cutoff_30:
                last_30 += 1
                if day >= cutoff_7:
                    last_7 += 1

        window_start = min(all_dates) if all_dates else None
        window_end = max(all_dates) if all_dates else None
        current_streak, longest_streak = UserService._activity_streaks(all_dates, today)
        window_counts = UserService._weekday_counts(window_start, window_end)

        _, standalone_habits = UserService._group_habits(habits)
        habit_stats = []
        scheduled_total = 0
        completed_total = 0
        for h in habits:
            h_dates = habit_dates.get(h.id, set())
            days = {d for d in (getattr(h, "days_of_week", None) or []) if 0 <= d < 7}
            scheduled = (
                sum(window_counts[d] for d in days) if window_counts and days else 0
            )
            completion = (
                min(100.0, 100.0 * len(h_dates) / scheduled) if scheduled else None
            )
            h_current, h_longest = UserService._activity_streaks(h_dates, today)
            if scheduled:
                scheduled_total += scheduled
                completed_total += min(len(h_dates), scheduled)
            habit_stats.append(
                {
                    "name": h.name,
                    "checkins": log_counts.get(h.id, 0),
                    "current_streak": h_current,
                    "longest_streak": h_longest,
                    "completion": completion,
                    "last_checkin": max(h_dates) if h_dates else None,
                }
            )
        habit_stats.sort(key=lambda entry: (-entry["checkins"], entry["name"]))

        created_at = getattr(user, "created_at", None)
        expires_at = getattr(user, "plan_expires_at", None)
        overall_completion = (
            round(100.0 * completed_total / scheduled_total, 1)
            if scheduled_total
            else None
        )

        return {
            "total_checkins": len(logs),
            "routines": len(routines),
            "habits": len(habits),
            "standalone_habits": len(standalone_habits),
            "active_days": len(all_dates),
            "window_start": window_start,
            "window_end": window_end,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "last_7": last_7,
            "last_30": last_30,
            "overall_completion": overall_completion,
            "by_weekday": by_weekday,
            "by_month": sorted(by_month.items()),
            "habit_stats": habit_stats,
            "member_since": created_at.date() if isinstance(created_at, datetime) else created_at,
            "plan": getattr(user, "plan", None),
            "plan_expires_at": expires_at.date() if isinstance(expires_at, datetime) else expires_at,
        }

    @staticmethod
    def _habit_stat_line(entry: dict) -> str:
        if not entry["checkins"]:
            return f"{entry['name']}: 0 check-ins (no activity yet)"
        parts = [f"{entry['name']}: {entry['checkins']} check-ins"]
        parts.append(f"streak {entry['current_streak']}d/best {entry['longest_streak']}d")
        if entry["completion"] is not None:
            parts.append(f"completion {entry['completion']:.0f}%")
        if entry["last_checkin"]:
            parts.append(f"last {entry['last_checkin']}")
        return " | ".join(parts)

    @staticmethod
    async def _generate_pdf_bytes(user: User, routines: list[Routine], habits: list[Habit], logs: list[ActivityLog]):
        stats = UserService._build_performance_stats(user, routines, habits, logs)
        habits_by_routine, standalone_habits = UserService._group_habits(habits)
        pdf = FPDF()

        pdf.add_page()
        pdf.set_font("Arial", style="B", size=16)
        pdf.cell(200, 10, text="Rhytm Performance Export", ln=True, align="C")
        pdf.ln(10)

        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, text=f"Name: {user.full_name}", ln=True)
        pdf.cell(200, 10, text=f"Email: {user.email}", ln=True)
        pdf.cell(200, 10, text=f"Subscription: {user.subscription_status}", ln=True)
        if stats["member_since"]:
            pdf.cell(200, 10, text=f"Member since: {stats['member_since']}", ln=True)
        if stats["plan"]:
            plan_line = f"Plan: {stats['plan']}"
            if stats["plan_expires_at"]:
                plan_line += f" (expires: {stats['plan_expires_at']})"
            pdf.cell(200, 10, text=plan_line, ln=True)
        pdf.ln(5)

        pdf.set_font("Arial", style="B", size=14)
        pdf.cell(200, 10, text="Routines:", ln=True)
        pdf.set_font("Arial", size=12)
        for r in routines:
            freq = UserService._format_days(r.frequency) if r.frequency else "None"
            period = UserService._period_label(r.period_of_day)
            pdf.cell(200, 10, text=f"  {r.name} ({period}, {r.time_of_day}) - Days: {freq}", ln=True)
            for h in habits_by_routine.get(r.id, ()):
                days = UserService._format_days(getattr(h, "days_of_week", None) or [])
                pdf.cell(200, 10, text=f"    - {h.name} (Reminder: {h.reminder_time or 'None'}, Days: {days})", ln=True)
        pdf.ln(5)

        pdf.set_font("Arial", style="B", size=14)
        pdf.cell(200, 10, text="Standalone Habits:", ln=True)
        pdf.set_font("Arial", size=12)
        for h in standalone_habits:
            days = UserService._format_days(getattr(h, "days_of_week", None) or [])
            pdf.cell(200, 10, text=f"- {h.name} (Reminder: {h.reminder_time or 'None'}, Days: {days})", ln=True)
        pdf.ln(5)

        pdf.set_font("Arial", style="B", size=14)
        pdf.cell(200, 10, text="Performance Summary:", ln=True)
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, text=f"  Routines: {stats['routines']} | Habits: {stats['habits']} (Standalone: {stats['standalone_habits']})", ln=True)
        if stats["window_start"]:
            span = (stats["window_end"] - stats["window_start"]).days + 1
            pdf.cell(200, 10, text=f"  Active Days: {stats['active_days']} (of {span}-day activity range)", ln=True)
        pdf.cell(200, 10, text=f"  Check-ins (last 7 days): {stats['last_7']} | (last 30 days): {stats['last_30']}", ln=True)
        pdf.cell(200, 10, text=f"  Current Streak: {stats['current_streak']} days | Longest Streak: {stats['longest_streak']} days", ln=True)
        if stats["overall_completion"] is not None:
            pdf.cell(200, 10, text=f"  Overall Completion Rate: {stats['overall_completion']}%", ln=True)
        pdf.ln(5)

        pdf.set_font("Arial", style="B", size=14)
        pdf.cell(200, 10, text=f"Total Lifetime Check-ins: {stats['total_checkins']}", ln=True)
        pdf.ln(3)

        if stats["habit_stats"]:
            pdf.set_font("Arial", style="B", size=12)
            pdf.cell(200, 10, text="Check-ins by Habit:", ln=True)
            pdf.set_font("Arial", size=12)
            for entry in stats["habit_stats"]:
                pdf.cell(200, 10, text=f"  {UserService._habit_stat_line(entry)}", ln=True)
        pdf.ln(3)

        if stats["window_start"]:
            pdf.set_font("Arial", style="B", size=12)
            pdf.cell(200, 10, text="Activity by Day of Week:", ln=True)
            pdf.set_font("Arial", size=12)
            weekday_line = "  " + " | ".join(
                f"{name}: {count}" for name, count in zip(_DAY_NAMES, stats["by_weekday"])
            )
            pdf.cell(200, 10, text=weekday_line, ln=True)
            pdf.ln(3)

            pdf.set_font("Arial", style="B", size=12)
            pdf.cell(200, 10, text="Activity by Month:", ln=True)
            pdf.set_font("Arial", size=12)
            for month_key, count in stats["by_month"]:
                pdf.cell(200, 10, text=f"  {month_key}: {count} check-ins", ln=True)
            pdf.ln(3)

            pdf.set_font("Arial", size=12)
            pdf.cell(200, 10, text=f"Activity Range: {stats['window_start']} to {stats['window_end']}", ln=True)

        return pdf.output()

    @staticmethod
    async def _process_export_task(
        user_id: str, job_id: str, format: str, meta_key: str, rdb: Redis
    ):
        file_key = f"export_file:{user_id}:{job_id}"
        try:
            async with AsyncSessionLocal() as bg_db:
                user = (
                    await bg_db.execute(select(User).where(User.id == user_id))
                ).scalar_one()

                if ENVIRONMENT != "prod":
                    routines = MOCK_EXPORT_DATA["routines"]
                    habits = MOCK_EXPORT_DATA["habits"]
                    logs = MOCK_EXPORT_DATA["logs"]
                else:
                    routines = (
                        await bg_db.execute(
                            select(Routine).where(Routine.user_id == user_id)
                        )
                    ).scalars().all()
                    habits = (
                        await bg_db.execute(
                            select(Habit)
                            .where(Habit.user_id == user_id)
                            .options(selectinload(Habit.routines))
                        )
                    ).scalars().all()
                    logs = (
                        await bg_db.execute(
                            select(ActivityLog.habit_id, ActivityLog.activity_date)
                            .join(Habit, ActivityLog.habit_id == Habit.id)
                            .where(Habit.user_id == user_id)
                        )
                    ).all()

            if format == "excel":
                file_bytes = await UserService._generate_excel_bytes(
                    user, routines, habits, logs
                )
                filename = f"rhytm_export_{datetime.now(UTC).date()}.xlsx"
            else:
                file_bytes = await UserService._generate_pdf_bytes(user, routines, habits, logs)
                filename = f"rhytm_export_{datetime.now(UTC).date()}.pdf"
            await rdb.setex(name=file_key, time=300, value=file_bytes)
            await rdb.setex(
                name=meta_key,
                time=300,
                value=json.dumps(
                    {"status": "ready", "filename": filename, "format": format}
                ),
            )
        except Exception as e:
            logger.exception(f"Failed to generate {format} export for {user_id}")
            await rdb.setex(
                name=meta_key,
                time=300,
                value=json.dumps({"status": "failed", "error": str(e)}),
            )

    async def download_export(
        self, user_id: uuid.UUID, job_id: str, rdb: Redis
    ) -> dict:
        meta_key = f"export_meta:{user_id}:{job_id}"
        file_key = f"export_file:{user_id}:{job_id}"

        raw_meta_data = await rdb.get(meta_key)
        if not raw_meta_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_MESSAGES.EXPORT_NOT_FOUND)

        meta_data = json.loads(raw_meta_data)
        if meta_data.get("status") == "failed":
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=USER_MESSAGES.EXPORT_GENERATION_FAILED)

        if meta_data.get("status") == "processing":
            raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=USER_MESSAGES.EXPORT_STILL_PROCESSING)

        file_bytes = await rdb.get(file_key)
        if not file_bytes:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_MESSAGES.EXPORT_NOT_FOUND)

        await rdb.delete(meta_key)
        await rdb.delete(file_key)

        content_type = (
            "application/pdf" if meta_data.get("format") == "pdf" 
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        return Response(
            content=file_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename={meta_data.get('filename')}"
            },
        )

    async def initiate_signup(
        self, email: str, full_name: str, db: AsyncSession, rdb: Redis
    ) -> dict:
        normalized_email = email.lower()
        result = await db.execute(select(User).where(User.email == normalized_email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.USER_EXISTS,
            )

        otp = f"{secrets.randbelow(10000):04d}" if ENVIRONMENT == "prod" else "0000"

        redis_key = f"signup_otp:{normalized_email}"
        signup_data = {
            "full_name": full_name,
            "email": normalized_email,
            "otp": otp,
        }
        await rdb.set(redis_key, json.dumps(signup_data), ex=SIGNUP_OTP_EXPIRY_SECONDS)

        if ENVIRONMENT == "prod":
            send_email_otp(
                to_mail=normalized_email,
                name=full_name,
                otp=otp,
                expiry_minutes=SIGNUP_OTP_EXPIRY_SECONDS // 60,
            )
        else:
            # Log OTP to console for dev environment
            logger.info(f"[SIGNUP OTP] OTP for {normalized_email} is: {otp}")

        message = USER_MESSAGES.OTP_SENT if ENVIRONMENT == "prod" else "Dev otp is 0000"

        return {"message": message}

    async def complete_signup(
        self,
        email: str,
        otp: str,
        password: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        normalized_email = email.lower()
        redis_key = f"signup_otp:{normalized_email}"
        cached_data = await rdb.get(redis_key)
        if not cached_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        data = json.loads(cached_data)

        if data.get("otp") != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        result = await db.execute(select(User).where(User.email == normalized_email))
        if result.scalar_one_or_none():
            await rdb.delete(redis_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.USER_EXISTS,
            )

        new_user = User(
            full_name=data["full_name"],
            email=normalized_email,
            password=hash_password(password),
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # Fetch the user and return
        query = await db.execute(
            select(User)
            .options(
                selectinload(User.routines.and_(Routine.deleted_at.is_(None)))
                .selectinload(Routine.habits.and_(Habit.deleted_at.is_(None)))
                .selectinload(Habit.activity_logs),
                selectinload(User.habits.and_(Habit.deleted_at.is_(None))).selectinload(
                    Habit.activity_logs
                ),
            )
            .where(User.email == normalized_email)
        )
        user = query.scalar_one_or_none()

        await rdb.delete(redis_key)

        access_token, refresh_token = self._issue_tokens(user.id, user.email)
        await self._persist_session(db, user.id, refresh_token)

        if ENVIRONMENT == "prod":
            send_welcome_mail(to_mail=normalized_email, name=user.full_name)

        attach_activity(list(user.habits), user.subscription_status)
        for r in user.routines:
            attach_activity(list(r.habits), user.subscription_status)

        return {
            "message": USER_MESSAGES.ACCOUNT_CREATED,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": UserResponse.model_validate(user).model_dump(),
        }

    async def login(self, email: str, password: str, db: AsyncSession) -> dict:
        normalized_email = email.lower()

        result = await db.execute(
            select(User)
            .options(
                selectinload(User.routines.and_(Routine.deleted_at.is_(None)))
                .selectinload(Routine.habits.and_(Habit.deleted_at.is_(None)))
                .selectinload(Habit.activity_logs),
                selectinload(User.habits.and_(Habit.deleted_at.is_(None))).selectinload(
                    Habit.activity_logs
                ),
            )
            .where(User.email == normalized_email)
        )
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_CREDENTIALS,
            )

        access_token, refresh_token = self._issue_tokens(user.id, user.email)
        await self._persist_session(db, user.id, refresh_token)

        attach_activity(list(user.habits), user.subscription_status)
        for r in user.routines:
            attach_activity(list(r.habits), user.subscription_status)

        return {
            "message": USER_MESSAGES.LOGIN_SUCCESS,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": UserResponse.model_validate(user).model_dump(),
        }

    async def request_password_reset(
        self, email: str, db: AsyncSession, rdb: Redis
    ) -> dict:
        normalized_email = email.lower()
        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        otp = f"{secrets.randbelow(10000):04d}"
        redis_key = f"reset_password_otp:{normalized_email}"
        reset_data = {
            "email": normalized_email,
            "otp": otp,
        }
        await rdb.set(
            redis_key, json.dumps(reset_data), ex=RESET_PASSWORD_OTP_EXPIRY_SECONDS
        )

        # Log OTP to console for now
        # TODO: Set up email OTP sending service (SMTP / SendGrid / Resend / AWS SES)
        logger.info(f"[RESET PASSWORD OTP] OTP for {normalized_email} is: {otp}")

        return {"message": USER_MESSAGES.OTP_SENT}

    async def reset_password(
        self,
        email: str,
        otp: str,
        new_password: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        normalized_email = email.lower()
        redis_key = f"reset_password_otp:{normalized_email}"
        cached_data = await rdb.get(redis_key)
        if not cached_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        data = json.loads(cached_data)

        if data.get("otp") != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_OR_EXPIRED_OTP,
            )

        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()
        if not user:
            await rdb.delete(redis_key)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        user.password = hash_password(new_password)
        await db.commit()
        await rdb.delete(redis_key)

        return {"message": USER_MESSAGES.PASSWORD_RESET_SUCCESS}

    async def logout(
        self,
        access_token: str,
        refresh_token: str,
        db: AsyncSession,
        rdb: Redis,
    ) -> dict:
        try:
            access_payload = decode_token(access_token, expected_type="access")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        try:
            refresh_payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        now = datetime.now(UTC)
        refresh_exp_ts = refresh_payload.get("exp")
        if not refresh_exp_ts or datetime.fromtimestamp(refresh_exp_ts, tz=UTC) <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        token_hash = hash_token(refresh_token)
        result = await db.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        session_entry = result.scalar_one_or_none()
        if not session_entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        if session_entry.expires_at <= now:
            await db.delete(session_entry)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        await db.delete(session_entry)
        await db.commit()

        access_exp = access_payload.get("exp")
        if access_exp and access_exp > now.timestamp():
            ttl = int(access_exp - now.timestamp())
            await rdb.set(f"blacklisted_token:{access_token}", "blacklisted", ex=ttl)

        return {"message": USER_MESSAGES.LOGOUT_SUCCESS}

    async def refresh_tokens(self, refresh_token: str, db: AsyncSession) -> dict:
        try:
            refresh_payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        now = datetime.now(UTC)
        refresh_exp_ts = refresh_payload.get("exp")
        if not refresh_exp_ts or datetime.fromtimestamp(refresh_exp_ts, tz=UTC) <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.INVALID_TOKEN,
            )

        token_hash = hash_token(refresh_token)
        result = await db.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        session_entry = result.scalar_one_or_none()
        if not session_entry:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        if session_entry.expires_at <= now:
            await db.delete(session_entry)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=USER_MESSAGES.SESSION_EXPIRED,
            )

        await db.delete(session_entry)

        user_id = refresh_payload.get("sub")
        email = refresh_payload.get("email")

        new_access_token, new_refresh_token = self._issue_tokens(user_id, email)
        await self._persist_session(db, session_entry.user_id, new_refresh_token)

        return {
            "message": USER_MESSAGES.TOKEN_REFRESH_SUCCESS,
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
        }

    async def get_profile(self, user_id: str, db: AsyncSession) -> dict:
        result = await db.execute(
            select(User)
            .options(
                selectinload(User.routines.and_(Routine.deleted_at.is_(None)))
                .selectinload(Routine.habits.and_(Habit.deleted_at.is_(None)))
                .selectinload(Habit.activity_logs),
                selectinload(User.habits.and_(Habit.deleted_at.is_(None))).selectinload(
                    Habit.activity_logs
                ),
            )
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )
        attach_activity(list(user.habits), user.subscription_status)
        for r in user.routines:
            attach_activity(list(r.habits), user.subscription_status)

        return UserResponse.model_validate(user).model_dump()

    async def delete_account(
        self, user_id: str, access_token: str, db: AsyncSession, rdb: Redis
    ) -> None:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )

        await db.execute(delete(Session).where(Session.user_id == user.id))
        await db.delete(user)
        await db.commit()

        try:
            access_payload = decode_token(access_token, expected_type="access")
            access_exp = access_payload.get("exp")
            now = datetime.now(UTC)
            if access_exp and access_exp > now.timestamp():
                ttl = int(access_exp - now.timestamp())
                await rdb.set(
                    f"blacklisted_token:{access_token}", "blacklisted", ex=ttl
                )
        except jwt.PyJWTError:
            pass

    async def generate_export(
        self,
        user_id: str,
        format: str,
        db: AsyncSession,
        rdb: Redis,
    ):
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=USER_MESSAGES.USER_NOT_FOUND,
            )
        if user.subscription_status == "basic":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=USER_MESSAGES.USER_NOT_PRO
            )
        if format not in ["pdf", "excel"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=USER_MESSAGES.INVALID_EXPORT_FORMAT,
            )
        job_id = str(uuid.uuid4())
        meta_key = f"export_meta:{user_id}:{job_id}"
        await rdb.setex(
            name=meta_key,
            time=300,
            value=json.dumps({"status": "processing", "format": format}),
        )

        asyncio.create_task(
            UserService._process_export_task(user_id, job_id, format, meta_key, rdb)
        )

        return {"message": "Export generation started", "job_id": job_id, "status": "processing"}
