from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .constants import OFFICESPACE_LOCAL_DATETIME_FORMAT
from .helpers import day_index_for_date, format_date_label, parse_local_datetime


class GraphQLModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class GraphQLOperationError(GraphQLModel):
    code: str | None = None
    fieldName: str | None = None
    message: str | None = None


class GraphQLOperationEnvelope(GraphQLModel):
    data: dict[str, Any] | None = None
    errors: list[GraphQLOperationError] = Field(default_factory=list)


GRAPHQL_OPERATION_LIST_ADAPTER = TypeAdapter(list[GraphQLOperationEnvelope])
GraphQLDataModel = TypeVar("GraphQLDataModel", bound=BaseModel)


def graphql_errors_to_json(errors: list[GraphQLOperationError]) -> str:
    return json.dumps(
        [error.model_dump(by_alias=True, exclude_none=True) for error in errors],
        indent=2,
        sort_keys=True,
    )


def parse_graphql_operations(payload: Any, *, error_prefix: str) -> list[GraphQLOperationEnvelope]:
    try:
        operations = GRAPHQL_OPERATION_LIST_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise RuntimeError(f"{error_prefix} returned invalid GraphQL response: {exc}") from exc

    if not operations:
        raise RuntimeError(f"{error_prefix} returned no operations.")

    return operations


def extract_graphql_data(
    operation: GraphQLOperationEnvelope | Any,
    *,
    model_type: type[GraphQLDataModel],
    error_prefix: str,
) -> GraphQLDataModel:
    try:
        envelope = (
            operation
            if isinstance(operation, GraphQLOperationEnvelope)
            else GraphQLOperationEnvelope.model_validate(operation)
        )
    except ValidationError as exc:
        raise RuntimeError(f"{error_prefix} returned invalid operation payload: {exc}") from exc

    if envelope.errors:
        raise RuntimeError(graphql_errors_to_json(envelope.errors))

    if envelope.data is None:
        raise RuntimeError(f"{error_prefix} did not return data.")

    try:
        return model_type.model_validate(envelope.data)
    except ValidationError as exc:
        raise RuntimeError(f"{error_prefix} returned invalid data payload: {exc}") from exc


@dataclass
class PreparedBookingRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any] | None
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

    def payload_schedule_dates(self) -> list[str]:
        if not self.payload:
            return []

        schedule = self.payload.get("variables", {}).get("schedule", [])
        return [item.get("date", "").split("T", 1)[0] for item in schedule if item.get("date")]


class GraphQLSeatBookingMutationBookingPayload(GraphQLModel):
    localCheckInTime: str | None = None
    localCheckOutScheduled: str | None = None


class GraphQLAffectedSeatFloor(GraphQLModel):
    label: str | None = None


class GraphQLAffectedSeat(GraphQLModel):
    label: str | None = None
    floor: GraphQLAffectedSeatFloor | None = None


class GraphQLSeatBookingMutationPayloadData(GraphQLModel):
    affectedSeats: list[GraphQLAffectedSeat] = Field(default_factory=list)
    bookings: list[GraphQLSeatBookingMutationBookingPayload] = Field(default_factory=list)
    errors: list[GraphQLOperationError] = Field(default_factory=list)


class GraphQLSeatBookingMutationData(GraphQLModel):
    createSeatBookingSeries: GraphQLSeatBookingMutationPayloadData | None = None


class SeatBookingMutationBooking(GraphQLModel):
    booking_date: str | None
    summary_line: str

    @classmethod
    def from_graphql_booking(
        cls, booking: GraphQLSeatBookingMutationBookingPayload
    ) -> SeatBookingMutationBooking:
        local_check_in = booking.localCheckInTime
        local_check_out = booking.localCheckOutScheduled
        check_in_dt = parse_local_datetime(local_check_in)
        check_out_dt = parse_local_datetime(local_check_out)

        booking_date = check_in_dt.date().isoformat() if check_in_dt else None
        if check_in_dt and check_out_dt:
            summary_line = (
                f"- {check_in_dt.strftime('%a, %b %d %I:%M %p')} to "
                f"{check_out_dt.strftime('%a, %b %d %I:%M %p')}"
            )
        else:
            summary_line = (
                f"- {local_check_in or 'Unknown start'} to {local_check_out or 'Unknown end'}"
            )

        return cls(booking_date=booking_date, summary_line=summary_line)


class SeatBookingMutationResult(GraphQLModel):
    payload: dict[str, Any]
    bookings: list[SeatBookingMutationBooking]
    error_messages: list[str]
    seat_label: str | None
    floor_label: str | None

    @classmethod
    def from_graphql_operation(
        cls, operation: GraphQLOperationEnvelope | Any
    ) -> SeatBookingMutationResult:
        data = extract_graphql_data(
            operation,
            model_type=GraphQLSeatBookingMutationData,
            error_prefix="Booking request",
        )
        mutation_payload = data.createSeatBookingSeries
        if mutation_payload is None:
            raise RuntimeError("Missing createSeatBookingSeries response payload.")

        bookings = [
            SeatBookingMutationBooking.from_graphql_booking(raw_booking)
            for raw_booking in mutation_payload.bookings
        ]

        error_messages = [
            error_info.message or "Unknown booking error."
            for error_info in mutation_payload.errors
        ]

        affected_seat = mutation_payload.affectedSeats[0] if mutation_payload.affectedSeats else None
        seat_label = affected_seat.label if affected_seat else None
        floor_label = affected_seat.floor.label if affected_seat and affected_seat.floor else None

        return cls(
            payload=mutation_payload.model_dump(by_alias=True, exclude_none=True),
            bookings=bookings,
            error_messages=error_messages,
            seat_label=seat_label,
            floor_label=floor_label,
        )

    def summary_lines(self, prepared: PreparedBookingRequest) -> list[str]:
        payload_dates = prepared.payload_schedule_dates()
        requested_dates = prepared.requested_dates or payload_dates
        scheduled_dates = prepared.scheduled_dates or payload_dates
        skipped_existing_dates = prepared.skipped_existing_dates

        booked_dates = {booking.booking_date for booking in self.bookings if booking.booking_date}
        failed_dates = [
            requested_date for requested_date in scheduled_dates if requested_date not in booked_dates
        ]

        if skipped_existing_dates:
            lines = [
                f"Booked {len(self.bookings)} new date(s) of {len(requested_dates)} requested date(s)."
            ]
        else:
            lines = [f"Booked {len(self.bookings)} of {len(requested_dates)} requested date(s)."]

        if self.seat_label:
            desk_line = f"Desk: {self.seat_label}"
            if self.floor_label:
                desk_line += f" • {self.floor_label}"
            lines.append(desk_line)

        if self.bookings:
            lines.append("Booked:")
            lines.extend(booking.summary_line for booking in self.bookings)

        if skipped_existing_dates:
            lines.append("Already booked:")
            for skipped_existing_date in skipped_existing_dates:
                lines.append(f"- {format_date_label(skipped_existing_date)}")

        if failed_dates:
            lines.append("Unavailable:")
            shared_error_message = (
                self.error_messages[0] if self.error_messages else "This desk is not available."
            )
            if len(self.error_messages) == len(failed_dates):
                for failed_date, error_message in zip(failed_dates, self.error_messages):
                    lines.append(f"- {format_date_label(failed_date)}: {error_message}")
            else:
                for failed_date in failed_dates:
                    lines.append(f"- {format_date_label(failed_date)}: {shared_error_message}")

        if self.error_messages and not failed_dates and not self.bookings and not skipped_existing_dates:
            lines.append("Errors:")
            for error_message in self.error_messages:
                lines.append(f"- {error_message}")

        return lines


class GraphQLOperatingHour(GraphQLModel):
    endAt: str | None = None
    startAt: str | None = None


class GraphQLOperatingDaysConfig(GraphQLModel):
    calendarStartDay: int | None = None
    operatingHours: list[GraphQLOperatingHour] | None = None


class GraphQLSiteBookingWindowSite(GraphQLModel):
    deskBookableFrom: str | None = None
    deskBookableUntil: str | None = None
    id: str | None = None
    operatingDaysConfig: GraphQLOperatingDaysConfig | None = None


class GraphQLSiteBookingWindowData(GraphQLModel):
    sites: list[GraphQLSiteBookingWindowSite] | None = None


class SiteBookingWindow(GraphQLModel):
    site_id: str
    bookable_from: date
    bookable_until: date
    raw_bookable_from: str
    raw_bookable_until: str
    operating_hours_by_day: dict[int, tuple[str, str]]

    @classmethod
    def from_graphql_operation(
        cls,
        operation: GraphQLOperationEnvelope | Any,
        *,
        fallback_site_id: str,
    ) -> SiteBookingWindow:
        data = extract_graphql_data(
            operation,
            model_type=GraphQLSiteBookingWindowData,
            error_prefix="Site booking window query",
        )
        if not data.sites:
            raise RuntimeError(
                f"Site booking window query did not return site {fallback_site_id}."
            )

        site = data.sites[0]

        raw_bookable_from = site.deskBookableFrom
        raw_bookable_until = site.deskBookableUntil
        if not raw_bookable_from or not raw_bookable_until:
            raise RuntimeError(
                "Site booking window query did not return deskBookableFrom/deskBookableUntil "
                f"for site {fallback_site_id}."
            )

        try:
            return cls(
                site_id=str(site.id or fallback_site_id),
                bookable_from=datetime.strptime(
                    raw_bookable_from, OFFICESPACE_LOCAL_DATETIME_FORMAT
                ).date(),
                bookable_until=datetime.strptime(
                    raw_bookable_until, OFFICESPACE_LOCAL_DATETIME_FORMAT
                ).date(),
                raw_bookable_from=raw_bookable_from,
                raw_bookable_until=raw_bookable_until,
                operating_hours_by_day=cls.parse_operating_hours(site.operatingDaysConfig),
            )
        except ValueError as exc:
            raise RuntimeError(
                "Unable to parse site booking window values: "
                f"{raw_bookable_from!r}, {raw_bookable_until!r}"
            ) from exc

    @staticmethod
    def parse_operating_hours(
        operating_days_config: GraphQLOperatingDaysConfig | None,
    ) -> dict[int, tuple[str, str]]:
        if operating_days_config is None:
            raise RuntimeError("Site booking window query did not return operatingDaysConfig.")

        calendar_start_day = operating_days_config.calendarStartDay
        operating_hours = operating_days_config.operatingHours
        if calendar_start_day is None:
            raise RuntimeError(
                "Site booking window query did not return operatingDaysConfig.calendarStartDay."
            )
        if not operating_hours:
            raise RuntimeError(
                "Site booking window query did not return operatingDaysConfig.operatingHours."
            )

        day_hours: dict[int, tuple[str, str]] = {}
        for offset, operating_hour in enumerate(operating_hours):
            start_at = operating_hour.startAt
            end_at = operating_hour.endAt
            if not start_at or not end_at:
                continue

            calendar_day = (calendar_start_day + offset) % 7
            day_index = (calendar_day - 1) % 7
            day_hours[day_index] = (start_at, end_at)

        if not day_hours:
            raise RuntimeError("Site booking window query returned no usable operating hours.")

        return day_hours

    def operating_hours_for_date(self, booking_date: date) -> tuple[str, str]:
        return self.operating_hours_by_day[day_index_for_date(booking_date)]


class GraphQLSeatSiteFloorSite(GraphQLModel):
    id: str | None = None


class GraphQLSeatSiteFloor(GraphQLModel):
    id: str | None = None
    site: GraphQLSeatSiteFloorSite | None = None


class GraphQLSeatSiteSeat(GraphQLModel):
    floor: GraphQLSeatSiteFloor | None = None
    floorId: str | None = None
    id: str | None = None


class GraphQLSeatSiteData(GraphQLModel):
    seats: list[GraphQLSeatSiteSeat] | None = None


class SeatSiteDetails(GraphQLModel):
    seat_id: str
    floor_id: str
    site_id: str

    @classmethod
    def from_graphql_operation(
        cls,
        operation: GraphQLOperationEnvelope | Any,
        *,
        expected_seat_id: str,
        expected_floor_id: str,
    ) -> SeatSiteDetails:
        data = extract_graphql_data(
            operation,
            model_type=GraphQLSeatSiteData,
            error_prefix="Seat site query",
        )
        if not data.seats:
            raise RuntimeError(f"Seat site query did not return seat {expected_seat_id}.")

        seat = data.seats[0]

        resolved_seat_id = str(seat.id or expected_seat_id)
        if resolved_seat_id != str(expected_seat_id):
            raise RuntimeError(
                f"Seat site query returned seat {resolved_seat_id}, expected {expected_seat_id}."
            )

        floor_id = str(
            seat.floor.id if seat.floor and seat.floor.id else seat.floorId or ""
        )
        if floor_id and floor_id != str(expected_floor_id):
            raise RuntimeError(
                f"Seat site query returned floor {floor_id} for seat {expected_seat_id}, expected {expected_floor_id}."
            )

        site_id = seat.floor.site.id if seat.floor and seat.floor.site else None

        if not site_id:
            raise RuntimeError(
                f"Seat site query did not return floor.site.id for seat {expected_seat_id}."
            )

        return cls(
            seat_id=resolved_seat_id,
            floor_id=str(expected_floor_id),
            site_id=str(site_id),
        )


class GraphQLLinkedEmployee(GraphQLModel):
    id: str | None = None


class GraphQLCurrentUserEmployeePayload(GraphQLModel):
    linkedEmployee: GraphQLLinkedEmployee | None = None


class GraphQLCurrentUserEmployeeData(GraphQLModel):
    currentUser: GraphQLCurrentUserEmployeePayload | None = None


class CurrentUserEmployee(GraphQLModel):
    employee_id: str

    @classmethod
    def from_graphql_operation(
        cls, operation: GraphQLOperationEnvelope | Any
    ) -> CurrentUserEmployee:
        data = extract_graphql_data(
            operation,
            model_type=GraphQLCurrentUserEmployeeData,
            error_prefix="Current user query",
        )
        return cls.from_graphql_current_user(data.currentUser)

    @classmethod
    def from_graphql_current_user(
        cls, current_user: GraphQLCurrentUserEmployeePayload | Any
    ) -> CurrentUserEmployee:
        try:
            payload = (
                current_user
                if isinstance(current_user, GraphQLCurrentUserEmployeePayload)
                else GraphQLCurrentUserEmployeePayload.model_validate(current_user)
            )
        except ValidationError as exc:
            raise RuntimeError("Current user query did not return currentUser.") from exc

        if payload is None:
            raise RuntimeError("Current user query did not return currentUser.")

        linked_employee = payload.linkedEmployee
        employee_id = linked_employee.id if linked_employee else None
        if not employee_id:
            raise RuntimeError("Current user query did not return linkedEmployee.id.")

        return cls(employee_id=str(employee_id))


class GraphQLCurrentUserBookingPayload(GraphQLModel):
    id: str | None = None
    isCanceled: bool = False
    localCheckInTime: str | None = None
    typename: str | None = Field(default=None, alias="__typename")


class GraphQLCurrentUserBookingsPayload(GraphQLModel):
    bookings: list[GraphQLCurrentUserBookingPayload] | None = None


class GraphQLCurrentUserBookingsData(GraphQLModel):
    currentUser: GraphQLCurrentUserBookingsPayload | None = None


class CurrentUserBooking(GraphQLModel):
    booking_id: str
    booking_date: date
    raw_local_check_in_time: str

    @classmethod
    def from_graphql_booking(
        cls, booking: GraphQLCurrentUserBookingPayload | Any
    ) -> CurrentUserBooking | None:
        try:
            payload = (
                booking
                if isinstance(booking, GraphQLCurrentUserBookingPayload)
                else GraphQLCurrentUserBookingPayload.model_validate(booking)
            )
        except ValidationError:
            return None

        if payload.typename != "SeatOpenBooking" or payload.isCanceled:
            return None

        local_check_in = payload.localCheckInTime
        check_in_dt = parse_local_datetime(local_check_in)
        if not check_in_dt or not local_check_in:
            return None

        return cls(
            booking_id=str(payload.id or ""),
            booking_date=check_in_dt.date(),
            raw_local_check_in_time=local_check_in,
        )


class CurrentUserBookings(GraphQLModel):
    bookings: list[CurrentUserBooking] = Field(default_factory=list)

    @classmethod
    def from_graphql_operation(
        cls, operation: GraphQLOperationEnvelope | Any
    ) -> CurrentUserBookings:
        data = extract_graphql_data(
            operation,
            model_type=GraphQLCurrentUserBookingsData,
            error_prefix="My bookings query",
        )
        return cls.from_graphql_current_user(data.currentUser)

    @classmethod
    def from_graphql_current_user(
        cls, current_user: GraphQLCurrentUserBookingsPayload | Any
    ) -> CurrentUserBookings:
        try:
            payload = (
                current_user
                if isinstance(current_user, GraphQLCurrentUserBookingsPayload)
                else GraphQLCurrentUserBookingsPayload.model_validate(current_user)
            )
        except ValidationError as exc:
            raise RuntimeError("My bookings query did not return currentUser.") from exc

        if payload is None:
            raise RuntimeError("My bookings query did not return currentUser.")

        raw_bookings = payload.bookings
        if raw_bookings is None:
            raise RuntimeError("My bookings query did not return currentUser.bookings.")

        bookings = [
            booking
            for raw_booking in raw_bookings
            if (booking := CurrentUserBooking.from_graphql_booking(raw_booking)) is not None
        ]
        return cls(bookings=bookings)

    def booking_dates(self) -> set[str]:
        return {booking.booking_date.isoformat() for booking in self.bookings}