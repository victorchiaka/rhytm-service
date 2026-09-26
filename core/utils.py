import os
import smtplib
from datetime import datetime, timezone
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
