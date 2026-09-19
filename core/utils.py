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
