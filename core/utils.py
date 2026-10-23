import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import resend
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")

templates = Jinja2Templates(directory="core/templates")

DAY_NAMES = {
    0: "M",
    1: "T",
    2: "W",
    3: "TH",
    4: "F",
    5: "S",
    6: "SU",
}


def fmt_days(day_ints: list[int]) -> str:
    """Convert a list of day integers to a readable string, e.g. 'M, W, F'."""
    return ", ".join(DAY_NAMES[d] for d in sorted(day_ints))


def today_in(tz_name: str | None = None) -> date:
    """Current date in the given IANA timezone; falls back to UTC."""
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now(UTC).date()


PERIOD_BOUNDS = {
    "Morning": (0, 660),  # 00:00 - 11:00
    "Afternoon": (720, 900),  # 12:00 - 15:00
    "Evening": (960, 1260),  # 16:00 - 21:00
}


def is_time_in_period(period: object, time_str: str) -> bool:
    """Check if HH:MM string falls within period_of_day bounds."""
    key = period.value if hasattr(period, "value") else str(period)
    bounds = PERIOD_BOUNDS.get(key)
    if not bounds:
        return True
    h, m = map(int, time_str.split(":"))
    return bounds[0] <= h * 60 + m <= bounds[1]


def check_reminder_before_routine(
    routine_time_str: str, reminder_time_str: str
) -> tuple[bool, int, str]:
    """Check if habit reminder_time is earlier than routine start time.

    Returns (is_earlier, diff_mins, routine_start_str).
    """
    rh, rm = map(int, routine_time_str.split(":"))
    start_mins = rh * 60 + rm

    hh, hm = map(int, reminder_time_str.split(":"))
    rem_mins = hh * 60 + hm

    if rem_mins < start_mins:
        return True, start_mins - rem_mins, f"{rh:02d}:{rm:02d}"

    return False, 0, f"{rh:02d}:{rm:02d}"


def send_email_otp(to_mail: str, name: str, otp: str, expiry_minutes: int = 10):
    html = templates.get_template("otp_mail.html").render(
        {
            "otp": otp,
            "name": name,
            "expiry_minutes": expiry_minutes,
            "year": datetime.now(UTC).year,
        }
    )
    resend.Emails.send(
        {
            "from": EMAIL_SENDER,
            "to": to_mail,
            "subject": "Verify your email",
            "html": html,
            "text": f"Your otp is {otp}\n",
        }
    )


def send_reset_password_email(
    to_mail: str, otp: str, expiry_minutes: int = 10, name: str | None = None
):
    html = templates.get_template("reset_password_mail.html").render(
        {
            "otp": otp,
            "expiry_minutes": expiry_minutes,
            "name": name,
            "year": datetime.now(UTC).year,
        }
    )
    resend.Emails.send(
        {
            "from": EMAIL_SENDER,
            "to": to_mail,
            "subject": "Reset your password",
            "html": html,
            "text": f"Your password reset code is {otp}\n",
        }
    )


def send_welcome_mail(to_mail: str, name: str):
    html = templates.get_template("welcome_mail.html").render(
        {
            "name": name,
            "year": datetime.now(UTC).year,
        }
    )
    resend.Emails.send(
        {
            "from": EMAIL_SENDER,
            "to": to_mail,
            "subject": "Welcome to Rhytm",
            "html": html,
            "text": f"Welcome to Rhytm, {name}!\n",
        }
    )


def generate_activity_grid(
    habit_id: str,
    activity_dates: set[date],
    scope: str = "MONTH",
    base_date: date | None = None,
    tz: str | None = None,
) -> dict:
    """Generate a dense grid (ActivityLogResponse dict format) from raw dates."""
    base_date = base_date or today_in(tz)
    scope = scope.upper()

    if scope == "MONTH":
        from_date = base_date.replace(day=1)
        next_month = from_date.replace(day=28) + timedelta(days=4)
        to_date = next_month - timedelta(days=next_month.day)
    else:  # YEAR
        from_date = base_date.replace(month=1, day=1)
        to_date = base_date.replace(month=12, day=31)

    grid_start_date = from_date - timedelta(days=from_date.weekday())
    grid_end_date = to_date + timedelta(days=6 - to_date.weekday())

    weeks = []
    current_date = grid_start_date

    while current_date <= grid_end_date:
        current_week_days = []
        week_start = current_date.isoformat()  # Monday of this week row

        total = 0
        for _ in range(7):
            if (
                current_date < from_date
                or current_date > to_date
                or current_date > base_date
            ):
                val = -1
            elif current_date in activity_dates:
                val = 1
                total += 1
            else:
                val = 0
            current_week_days.append(val)
            current_date += timedelta(days=1)

        weeks.append(
            {"week": week_start, "days": current_week_days, "total": total}
        )

    return {
        "habit_id": str(habit_id),
        "scope": scope,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "weeks": weeks,
    }


def get_activity_window(
    subscription_status: str = "basic", tz: str | None = None
) -> tuple[date, date]:
    """Return the (from_date, to_date) window for activity grids.

    Subscribed users get YEAR scope, basic users get MONTH.
    """
    base_date = today_in(tz)
    if subscription_status not in ("basic", None):
        return base_date.replace(month=1, day=1), base_date.replace(month=12, day=31)
    from_date = base_date.replace(day=1)
    next_month = from_date.replace(day=28) + timedelta(days=4)
    to_date = next_month - timedelta(days=next_month.day)
    return from_date, to_date


def attach_activity(
    habits: list, subscription_status: str = "basic", tz: str | None = None
) -> list:
    """Attach the dense activity grid to a list of HabitResponse-like objects.

    Subscribed users get YEAR scope, basic users get MONTH.
    """
    scope = "YEAR" if subscription_status not in ("basic", None) else "MONTH"
    for habit in habits:
        dates = {log.activity_date for log in habit.activity_logs}
        habit.activity = generate_activity_grid(habit.id, dates, scope, tz=tz)
    return habits


def generate_user_activity_grid(
    user_id: str | UUID,
    activity_dates: set[date],
    scope: str = "MONTH",
    base_date: date | None = None,
    tz: str | None = None,
) -> dict:
    """Generate a dense user grid (UserActivityLogResponse dict format) from raw dates."""
    base_date = base_date or today_in(tz)
    scope = scope.upper()

    if scope == "MONTH":
        from_date = base_date.replace(day=1)
        next_month = from_date.replace(day=28) + timedelta(days=4)
        to_date = next_month - timedelta(days=next_month.day)
    else:  # YEAR
        from_date = base_date.replace(month=1, day=1)
        to_date = base_date.replace(month=12, day=31)

    grid_start_date = from_date - timedelta(days=from_date.weekday())
    grid_end_date = to_date + timedelta(days=6 - to_date.weekday())

    weeks = []
    current_date = grid_start_date

    while current_date <= grid_end_date:
        current_week_days = []
        week_start = current_date.isoformat()  # Monday of this week row

        total = 0
        for _ in range(7):
            if (
                current_date < from_date
                or current_date > to_date
                or current_date > base_date
            ):
                val = -1
            elif current_date in activity_dates:
                val = 1
                total += 1
            else:
                val = 0
            current_week_days.append(val)
            current_date += timedelta(days=1)

        weeks.append(
            {"week": week_start, "days": current_week_days, "total": total}
        )

    return {
        "user_id": str(user_id),
        "scope": scope,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "weeks": weeks,
    }


def attach_user_activity(user, subscription_status: str = "basic") -> dict:
    """Attach the dense activity grid to user response.

    Subscribed users get YEAR scope, basic users get MONTH.
    """
    scope = "YEAR" if subscription_status not in ("basic", None) else "MONTH"
    tz = getattr(user, "timezone", None)
    from_date, to_date = get_activity_window(subscription_status, tz)
    all_logs = getattr(user, "activity_logs", [])
    dates = {
        log.activity_date
        for log in all_logs
        if from_date <= log.activity_date <= to_date
    }
    grid = generate_user_activity_grid(user.id, dates, scope, tz=tz)
    user.activity = grid
    return grid


