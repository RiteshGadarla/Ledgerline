from datetime import date, timedelta

# Fixed reference calendar so corpus generation stays deterministic across
# runs. Not a live holiday feed.
REFERENCE_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2024, 1, 26),
        date(2024, 3, 25),
        date(2024, 4, 11),
        date(2024, 8, 15),
        date(2024, 10, 2),
        date(2024, 11, 1),
        date(2024, 12, 25),
        date(2025, 1, 26),
        date(2025, 3, 14),
        date(2025, 8, 15),
        date(2025, 10, 2),
        date(2025, 12, 25),
    }
)


def is_business_day(day: date, holidays: frozenset[date] = REFERENCE_HOLIDAYS) -> bool:
    return day.weekday() < 5 and day not in holidays


def add_business_days(
    start: date, count: int, holidays: frozenset[date] = REFERENCE_HOLIDAYS
) -> date:
    step = 1 if count >= 0 else -1
    remaining = abs(count)
    current = start
    while remaining > 0:
        current += timedelta(days=step)
        if is_business_day(current, holidays):
            remaining -= 1
    return current
