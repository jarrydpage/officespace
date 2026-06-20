from __future__ import annotations

from datetime import date, datetime

from .constants import OFFICESPACE_LOCAL_DATETIME_FORMAT


def parse_local_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.strptime(value, OFFICESPACE_LOCAL_DATETIME_FORMAT)
    except ValueError:
        return None


def format_date_label(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%a, %b %d %Y")
    except ValueError:
        return value


def day_index_for_date(value: date) -> int:
    return value.timetuple().tm_wday


def parse_schedule_arg(schedule_arg: str | None) -> list[str] | None:
    if schedule_arg is None:
        return None

    schedule = [entry.strip() for entry in schedule_arg.split(",") if entry.strip()]
    if not schedule:
        raise RuntimeError("Schedule must contain at least one day name.")

    return schedule