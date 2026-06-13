from __future__ import annotations

from typing import Any

from .helpers import format_date_label, parse_local_datetime
from .models import PreparedBookingRequest


def extract_payload_schedule_dates(prepared: PreparedBookingRequest) -> list[str]:
    if not prepared.payload:
        return []

    schedule = prepared.payload[0].get("variables", {}).get("schedule", [])
    return [item.get("date", "").split("T", 1)[0] for item in schedule if item.get("date")]


def format_booking_summary(
    mutation_payload: dict[str, Any], prepared: PreparedBookingRequest
) -> str:
    payload_dates = extract_payload_schedule_dates(prepared)
    requested_dates = prepared.requested_dates or payload_dates
    scheduled_dates = prepared.scheduled_dates or payload_dates

    skipped_existing_dates = prepared.skipped_existing_dates

    bookings = mutation_payload.get("bookings") or []
    errors = mutation_payload.get("errors") or []
    affected_seats = mutation_payload.get("affectedSeats") or []
    affected_seat = affected_seats[0] if affected_seats else {}
    seat_label = affected_seat.get("label")
    floor_label = affected_seat.get("floor", {}).get("label") if isinstance(affected_seat, dict) else None

    booked_dates: set[str] = set()
    booked_lines: list[str] = []
    for booking in bookings:
        local_check_in = booking.get("localCheckInTime")
        local_check_out = booking.get("localCheckOutScheduled")
        check_in_dt = parse_local_datetime(local_check_in)
        check_out_dt = parse_local_datetime(local_check_out)

        if check_in_dt:
            booked_dates.add(check_in_dt.date().isoformat())

        if check_in_dt and check_out_dt:
            booked_lines.append(
                f"- {check_in_dt.strftime('%a, %b %d %I:%M %p')} to "
                f"{check_out_dt.strftime('%a, %b %d %I:%M %p')}"
            )
        else:
            booked_lines.append(
                f"- {local_check_in or 'Unknown start'} to {local_check_out or 'Unknown end'}"
            )

    failed_dates = [requested_date for requested_date in scheduled_dates if requested_date not in booked_dates]

    if skipped_existing_dates:
        lines = [f"Booked {len(bookings)} new date(s) of {len(requested_dates)} requested date(s)."]
    else:
        lines = [f"Booked {len(bookings)} of {len(requested_dates)} requested date(s)."]
    if seat_label:
        desk_line = f"Desk: {seat_label}"
        if floor_label:
            desk_line += f" • {floor_label}"
        lines.append(desk_line)

    if booked_lines:
        lines.append("Booked:")
        lines.extend(booked_lines)

    if skipped_existing_dates:
        lines.append("Already booked:")
        for skipped_existing_date in skipped_existing_dates:
            lines.append(f"- {format_date_label(skipped_existing_date)}")

    if failed_dates:
        lines.append("Unavailable:")
        shared_error_message = errors[0].get("message") if errors else "This desk is not available."
        if len(errors) == len(failed_dates):
            for failed_date, error_info in zip(failed_dates, errors):
                lines.append(
                    f"- {format_date_label(failed_date)}: "
                    f"{error_info.get('message') or shared_error_message}"
                )
        else:
            for failed_date in failed_dates:
                lines.append(f"- {format_date_label(failed_date)}: {shared_error_message}")

    if errors and not failed_dates and not booked_lines and not skipped_existing_dates:
        lines.append("Errors:")
        for error_info in errors:
            lines.append(f"- {error_info.get('message') or 'Unknown booking error.'}")

    return "\n".join(lines)