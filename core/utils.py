DAY_NAMES = {
    0: "SU",
    1: "M",
    2: "T",
    3: "W",
    4: "TH",
    5: "F",
    6: "S",
}


def fmt_days(day_ints: list[int]) -> str:
    """Convert a list of day integers to a readable string, e.g. 'M, W, F'."""
    return ", ".join(DAY_NAMES[d] for d in sorted(day_ints))


PERIOD_BOUNDS = {
    "Morning": (0, 660),      # 00:00 - 11:00
    "Afternoon": (720, 900),  # 12:00 - 15:00
    "Evening": (960, 1260),   # 16:00 - 21:00
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
