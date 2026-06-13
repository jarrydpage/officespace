from __future__ import annotations

from datetime import date, datetime
from urllib import parse as urlparse

from .constants import OFFICESPACE_LOCAL_DATETIME_FORMAT


def extract_qr_link_details(qr_link: str) -> tuple[str | None, str]:
    parsed = urlparse.urlparse(qr_link)
    if parsed.scheme != "officespacemobile" or parsed.netloc != "huddle":
        raise RuntimeError("QR link must use the officespacemobile://huddle format.")

    params = urlparse.parse_qs(parsed.query)
    domain = params.get("domain", [None])[0]
    token = params.get("token", [None])[0]
    if not token:
        raise RuntimeError("QR link did not contain a token parameter.")

    return domain, token


def derive_subdomain(domain: str) -> str:
    suffix = ".officespacesoftware.com"
    if domain.endswith(suffix):
        return domain[: -len(suffix)]
    return domain.split(".", 1)[0]


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