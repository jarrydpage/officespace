from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Any

from .constants import OFFICESPACE_LOCAL_DATETIME_FORMAT
from .helpers import day_index_for_date, parse_local_datetime


@dataclass
class PreparedBookingRequest:
    url: str
    headers: dict[str, str]
    payload: list[dict[str, Any]]
    requested_dates: list[str] = field(default_factory=list)
    scheduled_dates: list[str] = field(default_factory=list)
    skipped_existing_dates: list[str] = field(default_factory=list)

    @property
    def redacted_headers(self) -> dict[str, str]:
        redacted = dict(self.headers)
        if "Cookie" in redacted:
            redacted["Cookie"] = "_huddle_session=<redacted>"
        if "X-CSRF-Token" in redacted:
            redacted["X-CSRF-Token"] = "<redacted>"
        return redacted

    def __str__(self) -> str:
        return json.dumps(
            {
                "url": self.url,
                "headers": self.redacted_headers,
                "payload": self.payload,
            },
            indent=2,
            sort_keys=True,
        )


@dataclass
class SiteBookingWindow:
    site_id: str
    bookable_from: date
    bookable_until: date
    raw_bookable_from: str
    raw_bookable_until: str
    operating_hours_by_day: dict[int, tuple[str, str]]

    @classmethod
    def from_graphql_site(
        cls,
        site: Any,
        *,
        fallback_site_id: str,
    ) -> SiteBookingWindow:
        if not isinstance(site, dict):
            raise RuntimeError(
                f"Site booking window query did not return site {fallback_site_id}."
            )

        raw_bookable_from = site.get("deskBookableFrom")
        raw_bookable_until = site.get("deskBookableUntil")
        if not raw_bookable_from or not raw_bookable_until:
            raise RuntimeError(
                "Site booking window query did not return deskBookableFrom/deskBookableUntil "
                f"for site {fallback_site_id}."
            )

        try:
            return cls(
                site_id=str(site.get("id") or fallback_site_id),
                bookable_from=datetime.strptime(
                    raw_bookable_from, OFFICESPACE_LOCAL_DATETIME_FORMAT
                ).date(),
                bookable_until=datetime.strptime(
                    raw_bookable_until, OFFICESPACE_LOCAL_DATETIME_FORMAT
                ).date(),
                raw_bookable_from=raw_bookable_from,
                raw_bookable_until=raw_bookable_until,
                operating_hours_by_day=cls.parse_operating_hours(
                    site.get("operatingDaysConfig")
                ),
            )
        except ValueError as exc:
            raise RuntimeError(
                "Unable to parse site booking window values: "
                f"{raw_bookable_from!r}, {raw_bookable_until!r}"
            ) from exc

    @staticmethod
    def parse_operating_hours(
        operating_days_config: Any,
    ) -> dict[int, tuple[str, str]]:
        if not isinstance(operating_days_config, dict):
            raise RuntimeError("Site booking window query did not return operatingDaysConfig.")

        calendar_start_day = operating_days_config.get("calendarStartDay")
        operating_hours = operating_days_config.get("operatingHours")
        if not isinstance(calendar_start_day, int):
            raise RuntimeError(
                "Site booking window query did not return operatingDaysConfig.calendarStartDay."
            )
        if not isinstance(operating_hours, list) or not operating_hours:
            raise RuntimeError(
                "Site booking window query did not return operatingDaysConfig.operatingHours."
            )

        day_hours: dict[int, tuple[str, str]] = {}
        for offset, operating_hour in enumerate(operating_hours):
            if not isinstance(operating_hour, dict):
                continue

            start_at = operating_hour.get("startAt")
            end_at = operating_hour.get("endAt")
            if not isinstance(start_at, str) or not isinstance(end_at, str):
                continue

            calendar_day = (calendar_start_day + offset) % 7
            day_index = (calendar_day - 1) % 7
            day_hours[day_index] = (start_at, end_at)

        if not day_hours:
            raise RuntimeError("Site booking window query returned no usable operating hours.")

        return day_hours

    def operating_hours_for_date(self, booking_date: date) -> tuple[str, str]:
        return self.operating_hours_by_day[day_index_for_date(booking_date)]


@dataclass(frozen=True)
class SeatSiteDetails:
    seat_id: str
    floor_id: str
    site_id: str

    @classmethod
    def from_graphql_seat(
        cls,
        seat: Any,
        *,
        expected_seat_id: str,
        expected_floor_id: str,
    ) -> SeatSiteDetails:
        if not isinstance(seat, dict):
            raise RuntimeError(f"Seat site query did not return seat {expected_seat_id}.")

        resolved_seat_id = str(seat.get("id") or expected_seat_id)
        if resolved_seat_id != str(expected_seat_id):
            raise RuntimeError(
                f"Seat site query returned seat {resolved_seat_id}, expected {expected_seat_id}."
            )

        floor = seat.get("floor")
        floor_id = str(
            floor.get("id") if isinstance(floor, dict) and floor.get("id") else seat.get("floorId") or ""
        )
        if floor_id and floor_id != str(expected_floor_id):
            raise RuntimeError(
                f"Seat site query returned floor {floor_id} for seat {expected_seat_id}, expected {expected_floor_id}."
            )

        site_id = None
        if isinstance(floor, dict):
            site = floor.get("site")
            if isinstance(site, dict):
                site_id = site.get("id")

        if not site_id:
            raise RuntimeError(
                f"Seat site query did not return floor.site.id for seat {expected_seat_id}."
            )

        return cls(
            seat_id=resolved_seat_id,
            floor_id=str(expected_floor_id),
            site_id=str(site_id),
        )


@dataclass(frozen=True)
class CurrentUserEmployee:
    employee_id: str

    @classmethod
    def from_graphql_current_user(cls, current_user: Any) -> CurrentUserEmployee:
        if not isinstance(current_user, dict):
            raise RuntimeError("Current user query did not return currentUser.")

        linked_employee = current_user.get("linkedEmployee")
        employee_id = linked_employee.get("id") if isinstance(linked_employee, dict) else None
        if not employee_id:
            raise RuntimeError("Current user query did not return linkedEmployee.id.")

        return cls(employee_id=str(employee_id))


@dataclass(frozen=True)
class CurrentUserBooking:
    booking_id: str
    booking_date: date
    raw_local_check_in_time: str

    @classmethod
    def from_graphql_booking(cls, booking: Any) -> CurrentUserBooking | None:
        if not isinstance(booking, dict):
            return None
        if booking.get("__typename") != "SeatOpenBooking" or booking.get("isCanceled"):
            return None

        local_check_in = booking.get("localCheckInTime")
        check_in_dt = parse_local_datetime(local_check_in)
        if not check_in_dt or not isinstance(local_check_in, str):
            return None

        return cls(
            booking_id=str(booking.get("id") or ""),
            booking_date=check_in_dt.date(),
            raw_local_check_in_time=local_check_in,
        )


@dataclass(frozen=True)
class CurrentUserBookings:
    bookings: list[CurrentUserBooking]

    @classmethod
    def from_graphql_current_user(cls, current_user: Any) -> CurrentUserBookings:
        if not isinstance(current_user, dict):
            raise RuntimeError("My bookings query did not return currentUser.")

        raw_bookings = current_user.get("bookings")
        if not isinstance(raw_bookings, list):
            raise RuntimeError("My bookings query did not return currentUser.bookings.")

        bookings = [
            booking
            for raw_booking in raw_bookings
            if (booking := CurrentUserBooking.from_graphql_booking(raw_booking)) is not None
        ]
        return cls(bookings=bookings)

    def booking_dates(self) -> set[str]:
        return {booking.booking_date.isoformat() for booking in self.bookings}