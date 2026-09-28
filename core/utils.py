import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

load_dotenv()

templates = Jinja2Templates(directory="core/templates")

DAY_NAMES = {
    0: "SU",
    1: "M",
    2: "T",
    3: "W",
    4: "TH",
    5: "F",
    6: "S",
}

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
MTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT")


def fmt_days(day_ints: list[int]) -> str:
    """Convert a list of day integers to a readable string, e.g. 'M, W, F'."""
    return ", ".join(DAY_NAMES[d] for d in sorted(day_ints))


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
            "year": datetime.now(timezone.utc).year,
        }
    )
    msg = EmailMessage()
    msg.set_content(f"Your otp is {otp}\n")
    msg.add_alternative(html, subtype="html")
    msg["Subject"] = "Verify your email"
    msg["From"] = EMAIL_SENDER
    msg["To"] = to_mail

    with smtplib.SMTP_SSL(str(MTP_HOST)) as smtp:
        smtp.login(str(EMAIL_SENDER), str(EMAIL_PASSWORD))
        smtp.send_message(msg)


def send_welcome_mail(to_mail: str, name: str):
    html = templates.get_template("welcome_mail.html").render(
        {
            "name": name,
            "year": datetime.now(timezone.utc).year,
        }
    )
    msg = EmailMessage()
    msg.set_content(f"Welcome to Rhytm, {name}!\n")
    msg.add_alternative(html, subtype="html")
    msg["Subject"] = "Welcome to Rhytm"
    msg["From"] = EMAIL_SENDER
    msg["To"] = to_mail

    with smtplib.SMTP_SSL(str(MTP_HOST)) as smtp:
        smtp.login(str(EMAIL_SENDER), str(EMAIL_PASSWORD))
        smtp.send_message(msg)


def generate_activity_grid(
    habit_id: str, activity_dates: set[date], scope: str = "MONTH"
) -> dict:
    """Generate a dense grid (ActivityLogResponse dict format) from raw dates."""
    base_date = datetime.now(timezone.utc).date()
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
        week_start_dt = datetime(
            current_date.year, current_date.month, current_date.day, tzinfo=timezone.utc
        )
        week_timestamp = int(week_start_dt.timestamp())

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
            {"week": week_timestamp, "days": current_week_days, "total": total}
        )

    return {
        "habit_id": str(habit_id),
        "scope": scope,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "weeks": weeks,
    }


def attach_activity(habits: list, subscription_status: str = "basic") -> list:
    """Attach the dense activity grid to a list of HabitResponse-like objects.

    Subscribed users get YEAR scope, basic users get MONTH.
    """
    scope = "YEAR" if subscription_status not in ("basic", None) else "MONTH"
    for habit in habits:
        dates = {log.activity_date for log in habit.activity_logs}
        habit.activity = generate_activity_grid(habit.id, dates, scope)
    return habits
