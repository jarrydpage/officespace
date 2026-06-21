from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from .auth import OfficeSpaceAuthContext
from .client import OfficeSpaceClient
from .constants import DAY_NAME_TO_INDEX
from .helpers import day_index_for_date
from .models import (
    CurrentUserBookings,
    CurrentUserEmployee,
    PreparedBookingRequest,
    SeatBookingMutationResult,
    SeatSiteDetails,
    SiteBookingWindow,
)
from .queries import (
    CREATE_BOOKING_MUTATION,
    CURRENT_USER_QUERY,
    MY_BOOKINGS_QUERY,
    SEAT_SITE_QUERY,
    SITE_BOOKING_WINDOW_QUERY,
)


logger = logging.getLogger(__name__)


class OfficeSpaceDeskBooker:
    def __init__(
        self,
        *,
        auth_context: OfficeSpaceAuthContext,
        floor_id: str,
        seat_id: str,
        site_id: str | None = None,
    ) -> None:
        self.auth = auth_context
        self.floor_id = str(floor_id)
        self.seat_id = str(seat_id)
        self.seat_url = self.auth.build_seat_url(floor_id=self.floor_id, seat_id=self.seat_id)
        self.current_user_employee_id: str | None = None
        self.site_id = str(site_id) if site_id is not None else None
        self.site_booking_window: SiteBookingWindow | None = None
        self.client = OfficeSpaceClient(auth_context=self.auth)

    def prepare_booking_request(
        self,
        *,
        employee_id: str | None,
        booking_date: str | None,
        schedule: list[str] | None,
        check_in: str | None = None,
        check_out: str | None = None,
    ) -> PreparedBookingRequest:
        resolved_employee_id = employee_id or self.fetch_current_user_employee_id()
        resolved_site_id = self.ensure_site_id()
        requested_booking_dates = self.resolve_booking_dates(
            booking_date=booking_date,
            schedule=schedule,
        )
        existing_booking_dates = self.fetch_existing_booking_dates(
            booking_dates=requested_booking_dates,
        )
        skipped_existing_dates = [
            requested_booking_date
            for requested_booking_date in requested_booking_dates
            if requested_booking_date in existing_booking_dates
        ]
        resolved_booking_dates = [
            requested_booking_date
            for requested_booking_date in requested_booking_dates
            if requested_booking_date not in existing_booking_dates
        ]
        schedule_times = self.resolve_booking_times(
            booking_dates=resolved_booking_dates,
            check_in=check_in,
            check_out=check_out,
        )
        payload: dict[str, Any] | None = None
        if resolved_booking_dates:
            first_check_in, _ = schedule_times[resolved_booking_dates[0]]
            at_time = f"{resolved_booking_dates[0]}T{first_check_in}"
            payload = {
                "operationName": "CreateBookingSeries",
                "variables": {
                    "employeeId": str(resolved_employee_id),
                    "siteId": resolved_site_id,
                    "seatId": self.seat_id,
                    "schedule": [
                        {
                            "date": f"{resolved_booking_date}T12:00:00",
                            "checkIn": schedule_times[resolved_booking_date][0],
                            "checkOut": schedule_times[resolved_booking_date][1],
                        }
                        for resolved_booking_date in resolved_booking_dates
                    ],
                    "id": "",
                    "atTime": at_time,
                },
                "query": CREATE_BOOKING_MUTATION,
            }
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": self.auth.base_url,
            "Referer": self.seat_url,
            "User-Agent": self.auth.user_agent,
            "X-Page-Context": "visual-directory-floors-seats-max",
        }
        return PreparedBookingRequest(
            url=f"{self.auth.base_url}/graphql",
            headers=headers,
            payload=payload,
            requested_dates=requested_booking_dates,
            scheduled_dates=resolved_booking_dates,
            skipped_existing_dates=skipped_existing_dates,
        )

    def ensure_site_id(self) -> str:
        if self.site_id:
            return self.site_id

        resolved_site_id = self.fetch_site_id()
        self.site_id = resolved_site_id
        return resolved_site_id

    def fetch_current_user_employee_id(self) -> str:
        if self.current_user_employee_id:
            return self.current_user_employee_id

        employee = self.client.execute_operation(
            operation={
                "operationName": "CurrentUserLinkedEmployee",
                "variables": {},
                "query": CURRENT_USER_QUERY,
            },
            referer=f"{self.auth.base_url}/visual-directory/home",
            page_context="visual-directory-home",
            error_prefix="Current user query failed",
            parser=CurrentUserEmployee.from_graphql_operation,
        )
        self.current_user_employee_id = employee.employee_id
        return self.current_user_employee_id

    def fetch_existing_booking_dates(
        self,
        *,
        booking_dates: list[str],
    ) -> set[str]:
        if not booking_dates:
            return set()

        existing_bookings = self.client.execute_operation(
            operation={
                "operationName": "MyBookings",
                "variables": {
                    "periodStart": f"{min(booking_dates)}T00:00:00",
                    "periodEnd": f"{max(booking_dates)}T23:59:59",
                    "notRejected": True,
                },
                "query": MY_BOOKINGS_QUERY,
            },
            referer=f"{self.auth.base_url}/visual-directory/home/bookings",
            page_context="visual-directory-home-bookings",
            error_prefix="My bookings query failed",
            parser=CurrentUserBookings.from_graphql_operation,
        )
        return existing_bookings.booking_dates() & set(booking_dates)

    def resolve_booking_dates(
        self,
        *,
        booking_date: str | None,
        schedule: list[str] | None,
    ) -> list[str]:
        if booking_date and schedule:
            raise RuntimeError("Provide either a single booking date or schedule, not both.")

        if booking_date:
            self.validate_booking_date(booking_date=booking_date)
            return [booking_date]

        if not schedule:
            raise RuntimeError("Provide a booking date or schedule to create a booking.")

        schedule_indexes = self.normalize_schedule(schedule)
        window = self.fetch_site_booking_window()
        resolved_dates: list[str] = []
        current_date = window.bookable_from
        while current_date <= window.bookable_until:
            if day_index_for_date(current_date) in schedule_indexes:
                resolved_dates.append(current_date.isoformat())
            current_date += timedelta(days=1)

        if not resolved_dates:
            raise RuntimeError(
                "No booking dates matched the requested schedule within the allowed site window "
                f"{window.bookable_from.isoformat()} to {window.bookable_until.isoformat()}."
            )

        return resolved_dates

    def normalize_schedule(self, schedule: list[str]) -> set[int]:
        normalized_schedule: set[int] = set()
        for entry in schedule:
            normalized_entry = entry.strip().lower()
            if normalized_entry not in DAY_NAME_TO_INDEX:
                supported_days = ", ".join(DAY_NAME_TO_INDEX)
                raise RuntimeError(
                    f"Unsupported schedule day {entry!r}. Use one of: {supported_days}."
                )
            normalized_schedule.add(DAY_NAME_TO_INDEX[normalized_entry])

        return normalized_schedule

    def validate_booking_date(
        self,
        *,
        booking_date: str,
    ) -> None:
        window = self.fetch_site_booking_window()

        try:
            requested_date = date.fromisoformat(booking_date)
        except ValueError as exc:
            raise RuntimeError(
                f"Booking date {booking_date!r} must use YYYY-MM-DD format."
            ) from exc

        if window.bookable_from <= requested_date <= window.bookable_until:
            return

        raise RuntimeError(
            "Requested booking date "
            f"{booking_date} is outside the allowed site window "
            f"{window.bookable_from.isoformat()} to {window.bookable_until.isoformat()} "
            f"(raw values: {window.raw_bookable_from!r} to {window.raw_bookable_until!r})."
        )

    def fetch_site_booking_window(self) -> SiteBookingWindow:
        if self.site_booking_window is not None:
            return self.site_booking_window

        site_id = self.ensure_site_id()

        window = self.client.execute_operation(
            operation={
                "operationName": "SiteBookingWindow",
                "variables": {"ids": [site_id]},
                "query": SITE_BOOKING_WINDOW_QUERY,
            },
            referer=f"{self.auth.base_url}/visual-directory/home",
            page_context="visual-directory-home",
            error_prefix="Site booking window query failed",
            parser=lambda envelope: SiteBookingWindow.from_graphql_operation(
                envelope,
                fallback_site_id=site_id,
            ),
        )

        self.site_booking_window = window
        logger.info("Site allows booking through %s.", window.bookable_until.isoformat())
        return window

    def fetch_site_id(self) -> str:
        seat_details = self.client.execute_operation(
            operation={
                "operationName": "SeatSite",
                "variables": {"ids": [self.seat_id]},
                "query": SEAT_SITE_QUERY,
            },
            referer=self.seat_url,
            page_context="visual-directory-floors-seats-max",
            error_prefix="Seat site query failed",
            parser=lambda envelope: SeatSiteDetails.from_graphql_operation(
                envelope,
                expected_seat_id=self.seat_id,
                expected_floor_id=self.floor_id,
            ),
        )

        return seat_details.site_id

    def resolve_booking_times(
        self,
        *,
        booking_dates: list[str],
        check_in: str | None,
        check_out: str | None,
    ) -> dict[str, tuple[str, str]]:
        if not booking_dates:
            return {}

        if bool(check_in) != bool(check_out):
            raise RuntimeError("Provide both check_in and check_out overrides together.")

        if check_in and check_out:
            return {booking_date: (check_in, check_out) for booking_date in booking_dates}

        window = self.fetch_site_booking_window()
        resolved_times: dict[str, tuple[str, str]] = {}
        for booking_date in booking_dates:
            booking_day = date.fromisoformat(booking_date)
            try:
                resolved_times[booking_date] = window.operating_hours_for_date(booking_day)
            except KeyError as exc:
                raise RuntimeError(
                    f"No operating hours were returned for booking date {booking_date}."
                ) from exc

        return resolved_times

    def send_booking_request(self, prepared: PreparedBookingRequest) -> dict[str, Any]:
        if not prepared.payload:
            booking_result = SeatBookingMutationResult(
                payload={"affectedSeats": [], "bookings": [], "errors": []},
                bookings=[],
                error_messages=[],
                seat_label=None,
                floor_label=None,
            )
            for line in booking_result.summary_lines(prepared):
                logger.info("%s", line)
            return booking_result.payload

        operation = self.client.request_operation(
            url=prepared.url,
            headers=prepared.headers,
            operation=prepared.payload,
            error_prefix="Booking request",
        )
        booking_result = SeatBookingMutationResult.from_graphql_operation(operation)

        for line in booking_result.summary_lines(prepared):
            logger.info("%s", line)

        return booking_result.payload