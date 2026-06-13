from __future__ import annotations

import json
from datetime import date, timedelta
from http import cookiejar
from pathlib import Path
from typing import Any
from urllib import error, request

from .auth import AuthInputs, decode_jwt_payload, resolve_auth_context, token_is_expired
from .constants import (
    CSRF_PATTERNS,
    DAY_NAME_TO_INDEX,
    DEFAULT_USER_AGENT,
    MOBILE_AUTH_USER_AGENT,
    MOBILE_QR_USER_AGENT,
    OFFICESPACE_LOCAL_DATETIME_FORMAT,
)
from .helpers import day_index_for_date, parse_local_datetime
from .models import (
    CurrentUserBookings,
    CurrentUserEmployee,
    PreparedBookingRequest,
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


class OfficeSpaceDeskBooker:
    def __init__(
        self,
        subdomain: str,
        session_cookie: str | None = None,
        *,
        auth_config_file: str | None = None,
        mobile_bearer_token: str | None = None,
        qr_token: str | None = None,
        timeout_seconds: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = f"https://{subdomain}.officespacesoftware.com"
        self.auth_config_path = Path(auth_config_file).expanduser() if auth_config_file else None
        self.session_cookie = session_cookie
        self.mobile_bearer_token = mobile_bearer_token
        self.current_user_employee_id: str | None = None
        self.seat_site_ids: dict[tuple[str, str], str] = {}
        self.site_booking_windows: dict[str, SiteBookingWindow] = {}
        self.qr_token = qr_token
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    @classmethod
    def from_auth_inputs(
        cls,
        auth: AuthInputs,
        *,
        timeout_seconds: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> OfficeSpaceDeskBooker:
        resolved_auth = resolve_auth_context(auth)
        return cls(
            subdomain=resolved_auth.subdomain,
            session_cookie=auth.session_cookie,
            auth_config_file=auth.auth_config_file,
            mobile_bearer_token=auth.mobile_bearer_token,
            qr_token=resolved_auth.qr_token,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )

    def create_booking(
        self,
        *,
        employee_id: str | None,
        floor_id: str,
        seat_id: str,
        site_id: str | None,
        booking_date: str | None,
        schedule: list[str] | None,
        check_in: str | None = None,
        check_out: str | None = None,
        csrf_token: str | None = None,
    ) -> dict[str, Any]:
        prepared = self.prepare_booking_request(
            employee_id=employee_id,
            floor_id=floor_id,
            seat_id=seat_id,
            site_id=site_id,
            booking_date=booking_date,
            schedule=schedule,
            check_in=check_in,
            check_out=check_out,
            csrf_token=csrf_token,
        )
        return self.send_booking_request(prepared)

    def prepare_booking_request(
        self,
        *,
        employee_id: str | None,
        floor_id: str,
        seat_id: str,
        site_id: str | None,
        booking_date: str | None,
        schedule: list[str] | None,
        check_in: str | None = None,
        check_out: str | None = None,
        csrf_token: str | None = None,
    ) -> PreparedBookingRequest:
        token = csrf_token or self.fetch_csrf_token(floor_id=floor_id, seat_id=seat_id)
        resolved_employee_id = employee_id or self.fetch_current_user_employee_id(csrf_token=token)
        resolved_site_id = self.resolve_site_id(
            site_id=site_id,
            floor_id=floor_id,
            seat_id=seat_id,
            csrf_token=token,
        )
        requested_booking_dates = self.resolve_booking_dates(
            site_id=resolved_site_id,
            booking_date=booking_date,
            schedule=schedule,
            csrf_token=token,
        )
        existing_booking_dates = self.fetch_existing_booking_dates(
            booking_dates=requested_booking_dates,
            csrf_token=token,
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
            site_id=resolved_site_id,
            booking_dates=resolved_booking_dates,
            check_in=check_in,
            check_out=check_out,
            csrf_token=token,
        )
        seat_url = self.build_seat_url(floor_id=floor_id, seat_id=seat_id)
        payload: list[dict[str, Any]] = []
        if resolved_booking_dates:
            first_check_in, _ = schedule_times[resolved_booking_dates[0]]
            at_time = f"{resolved_booking_dates[0]}T{first_check_in}"
            payload = [
                {
                    "operationName": "CreateBookingSeries",
                    "variables": {
                        "employeeId": str(resolved_employee_id),
                        "siteId": resolved_site_id,
                        "seatId": str(seat_id),
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
            ]
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": seat_url,
            "User-Agent": self.user_agent,
            "X-CSRF-Token": token,
            "X-Page-Context": "visual-directory-floors-seats-max",
            "Cookie": f"_huddle_session={self.ensure_session_cookie()}",
        }
        return PreparedBookingRequest(
            url=f"{self.base_url}/graphql",
            headers=headers,
            payload=payload,
            requested_dates=requested_booking_dates,
            scheduled_dates=resolved_booking_dates,
            skipped_existing_dates=skipped_existing_dates,
        )

    def resolve_site_id(
        self,
        *,
        site_id: str | None,
        floor_id: str,
        seat_id: str,
        csrf_token: str,
    ) -> str:
        if site_id is not None:
            return str(site_id)

        return self.fetch_seat_site_id(
            floor_id=floor_id,
            seat_id=seat_id,
            csrf_token=csrf_token,
        )

    def fetch_existing_booking_dates(
        self,
        *,
        booking_dates: list[str],
        csrf_token: str,
    ) -> set[str]:
        if not booking_dates:
            return set()

        operations = self.post_graphql_operations(
            operations=[
                {
                    "operationName": "MyBookings",
                    "variables": {
                        "periodStart": f"{min(booking_dates)}T00:00:00",
                        "periodEnd": f"{max(booking_dates)}T23:59:59",
                        "notRejected": True,
                    },
                    "query": MY_BOOKINGS_QUERY,
                }
            ],
            csrf_token=csrf_token,
            referer=f"{self.base_url}/visual-directory/home/bookings",
            page_context="visual-directory-home-bookings",
            error_prefix="My bookings query failed",
        )

        current_user = operations[0].get("data", {}).get("currentUser")
        existing_bookings = CurrentUserBookings.from_graphql_current_user(current_user)
        return existing_bookings.booking_dates() & set(booking_dates)

    def resolve_booking_dates(
        self,
        *,
        site_id: str | None,
        booking_date: str | None,
        schedule: list[str] | None,
        csrf_token: str,
    ) -> list[str]:
        if booking_date and schedule:
            raise RuntimeError("Provide either a single booking date or schedule, not both.")

        if booking_date:
            self.validate_booking_date(
                site_id=site_id,
                booking_date=booking_date,
                csrf_token=csrf_token,
            )
            return [booking_date]

        if not schedule:
            raise RuntimeError("Provide a booking date or schedule to create a booking.")

        if site_id is None:
            raise RuntimeError("Site ID is required when resolving booking dates from schedule.")

        schedule_indexes = self.normalize_schedule(schedule)
        window = self.fetch_site_booking_window(site_id=site_id, csrf_token=csrf_token)
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
        site_id: str | None,
        booking_date: str,
        csrf_token: str,
    ) -> None:
        if site_id is None:
            return

        window = self.fetch_site_booking_window(site_id=site_id, csrf_token=csrf_token)

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

    def fetch_site_booking_window(self, *, site_id: str, csrf_token: str) -> SiteBookingWindow:
        cached_window = self.site_booking_windows.get(site_id)
        if cached_window:
            return cached_window

        operations = self.post_graphql_operations(
            operations=[
                {
                    "operationName": "SiteBookingWindow",
                    "variables": {"ids": [site_id]},
                    "query": SITE_BOOKING_WINDOW_QUERY,
                }
            ],
            csrf_token=csrf_token,
            referer=f"{self.base_url}/visual-directory/home",
            page_context="visual-directory-home",
            error_prefix="Site booking window query failed",
        )

        sites = operations[0].get("data", {}).get("sites")
        site = sites[0] if isinstance(sites, list) and sites else None
        window = SiteBookingWindow.from_graphql_site(site, fallback_site_id=site_id)

        self.site_booking_windows[site_id] = window
        return window

    def fetch_seat_site_id(self, *, floor_id: str, seat_id: str, csrf_token: str) -> str:
        cache_key = (str(floor_id), str(seat_id))
        cached_site_id = self.seat_site_ids.get(cache_key)
        if cached_site_id:
            return cached_site_id

        seat_url = self.build_seat_url(floor_id=floor_id, seat_id=seat_id)
        operations = self.post_graphql_operations(
            operations=[
                {
                    "operationName": "SeatSite",
                    "variables": {"ids": [str(seat_id)]},
                    "query": SEAT_SITE_QUERY,
                }
            ],
            csrf_token=csrf_token,
            referer=seat_url,
            page_context="visual-directory-floors-seats-max",
            error_prefix="Seat site query failed",
        )

        seats = operations[0].get("data", {}).get("seats")
        seat = seats[0] if isinstance(seats, list) and seats else None
        seat_details = SeatSiteDetails.from_graphql_seat(
            seat,
            expected_seat_id=str(seat_id),
            expected_floor_id=str(floor_id),
        )

        resolved_site_id = seat_details.site_id
        self.seat_site_ids[cache_key] = resolved_site_id
        return resolved_site_id

    def resolve_booking_times(
        self,
        *,
        site_id: str | None,
        booking_dates: list[str],
        check_in: str | None,
        check_out: str | None,
        csrf_token: str,
    ) -> dict[str, tuple[str, str]]:
        if not booking_dates:
            return {}

        if bool(check_in) != bool(check_out):
            raise RuntimeError("Provide both check_in and check_out overrides together.")

        if check_in and check_out:
            return {booking_date: (check_in, check_out) for booking_date in booking_dates}

        if site_id is None:
            raise RuntimeError(
                "Site ID is required to derive booking times from site operating hours."
            )

        window = self.fetch_site_booking_window(site_id=site_id, csrf_token=csrf_token)
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

    def fetch_current_user_employee_id(self, *, csrf_token: str) -> str:
        if self.current_user_employee_id:
            return self.current_user_employee_id

        operations = self.post_graphql_operations(
            operations=[
                {
                    "operationName": "CurrentUserLinkedEmployee",
                    "variables": {},
                    "query": CURRENT_USER_QUERY,
                }
            ],
            csrf_token=csrf_token,
            referer=f"{self.base_url}/visual-directory/home",
            page_context="visual-directory-home",
            error_prefix="Current user query failed",
        )

        current_user = operations[0].get("data", {}).get("currentUser")
        employee = CurrentUserEmployee.from_graphql_current_user(current_user)
        self.current_user_employee_id = employee.employee_id
        return self.current_user_employee_id

    def post_graphql_operations(
        self,
        *,
        operations: list[dict[str, Any]],
        csrf_token: str,
        referer: str,
        page_context: str,
        error_prefix: str,
    ) -> list[dict[str, Any]]:
        graphql_request = request.Request(
            f"{self.base_url}/graphql",
            data=json.dumps(operations).encode("utf-8"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Cookie": f"_huddle_session={self.ensure_session_cookie()}",
                "Origin": self.base_url,
                "Referer": referer,
                "User-Agent": self.user_agent,
                "X-CSRF-Token": csrf_token,
                "X-Page-Context": page_context,
            },
            method="POST",
        )

        try:
            with request.urlopen(graphql_request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{error_prefix} with HTTP {exc.code}: {body_text}") from exc

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{error_prefix} returned invalid JSON: {raw_response}") from exc

        if not isinstance(parsed, list) or not parsed:
            raise RuntimeError(f"Unexpected GraphQL response shape: {parsed!r}")

        first_operation = parsed[0]
        if first_operation.get("errors"):
            raise RuntimeError(json.dumps(first_operation["errors"], indent=2, sort_keys=True))

        return parsed

    def fetch_csrf_token(self, *, floor_id: str, seat_id: str) -> str:
        seat_url = self.build_seat_url(floor_id=floor_id, seat_id=seat_id)
        seat_request = request.Request(
            seat_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Cookie": f"_huddle_session={self.ensure_session_cookie()}",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )

        with request.urlopen(seat_request, timeout=self.timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")

        for pattern in CSRF_PATTERNS:
            match = pattern.search(html)
            if match:
                return match.group(1)

        raise RuntimeError(
            "Unable to locate a CSRF token on the seat page. "
            "Pass --csrf-token explicitly if the page format has changed."
        )

    def ensure_session_cookie(self) -> str:
        if self.session_cookie:
            return self.session_cookie

        self.session_cookie = self.exchange_mobile_bearer_for_session()
        return self.session_cookie

    def ensure_mobile_bearer_token(self) -> str:
        if self.mobile_bearer_token and not token_is_expired(self.mobile_bearer_token):
            return self.mobile_bearer_token

        cached_token = self.load_cached_mobile_bearer_token()
        if cached_token:
            self.mobile_bearer_token = cached_token
            return cached_token

        if not self.qr_token:
            raise RuntimeError(
                "No valid cached mobile bearer token found. Set OFFICESPACE_QR_TOKEN "
                "for the first run or to refresh the cache."
            )

        self.mobile_bearer_token = self.exchange_qr_token_for_mobile_bearer()
        self.save_cached_mobile_bearer_token(self.mobile_bearer_token)
        return self.mobile_bearer_token

    def load_cached_mobile_bearer_token(self) -> str | None:
        if not self.auth_config_path or not self.auth_config_path.exists():
            return None

        try:
            config = json.loads(self.auth_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Unable to read auth config file {self.auth_config_path}: {exc}"
            ) from exc

        token = config.get("mobileBearerToken")
        if not isinstance(token, str) or not token:
            return None

        if token_is_expired(token):
            return None

        return token

    def save_cached_mobile_bearer_token(self, token: str) -> None:
        if not self.auth_config_path:
            return

        payload = decode_jwt_payload(token)
        config: dict[str, Any] = {"mobileBearerToken": token}
        for key in ("exp", "iat", "sub"):
            if key in payload:
                config[key] = payload[key]

        try:
            self.auth_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.auth_config_path.write_text(
                json.dumps(config, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(
                f"Unable to write auth config file {self.auth_config_path}: {exc}"
            ) from exc

    def clear_cached_mobile_bearer_token(self) -> None:
        if not self.auth_config_path or not self.auth_config_path.exists():
            return

        try:
            self.auth_config_path.unlink()
        except OSError:
            pass

    def exchange_qr_token_for_mobile_bearer(self) -> str:
        pairing_session = self.session_cookie or self.bootstrap_session_cookie()
        auth_url = f"{self.base_url}/ossmobile/auth"
        auth_request = request.Request(
            auth_url,
            data=b"",
            headers={
                "Accept": "*/*",
                "Authorization": f"Bearer {self.qr_token}",
                "Cookie": f"_huddle_session={pairing_session}",
                "User-Agent": MOBILE_QR_USER_AGENT,
            },
            method="POST",
        )

        try:
            with request.urlopen(auth_request, timeout=self.timeout_seconds) as response:
                authorization_header = (
                    response.headers.get("Authorization")
                    or response.headers.get("authorization")
                )
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"QR auth exchange failed with HTTP {exc.code}: {body_text}"
            ) from exc

        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise RuntimeError(
                "QR auth exchange completed without returning a mobile bearer token."
            )

        return authorization_header.split(" ", 1)[1]

    def bootstrap_session_cookie(self) -> str:
        bootstrap_url = f"{self.base_url}/visual-directory"
        cookies = cookiejar.CookieJar()
        opener = request.build_opener(request.HTTPCookieProcessor(cookies))
        bootstrap_request = request.Request(
            bootstrap_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": MOBILE_QR_USER_AGENT,
            },
            method="GET",
        )

        try:
            with opener.open(bootstrap_request, timeout=self.timeout_seconds):
                pass
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Unable to bootstrap a pairing session with HTTP {exc.code}: {body_text}"
            ) from exc

        for cookie in cookies:
            if cookie.name == "_huddle_session":
                return cookie.value

        raise RuntimeError("Unable to bootstrap a pairing _huddle_session cookie.")

    def exchange_mobile_bearer_for_session(self, *, allow_refresh: bool = True) -> str:
        auth_url = f"{self.base_url}/ossmobile/auth"
        cookies = cookiejar.CookieJar()
        opener = request.build_opener(request.HTTPCookieProcessor(cookies))
        auth_request = request.Request(
            auth_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Authorization": f"Bearer {self.ensure_mobile_bearer_token()}",
                "User-Agent": MOBILE_AUTH_USER_AGENT,
            },
            method="GET",
        )

        try:
            with opener.open(auth_request, timeout=self.timeout_seconds):
                pass
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401 and allow_refresh and self.qr_token:
                self.mobile_bearer_token = None
                self.clear_cached_mobile_bearer_token()
                return self.exchange_mobile_bearer_for_session(allow_refresh=False)
            raise RuntimeError(
                f"Mobile auth exchange failed with HTTP {exc.code}: {body_text}"
            ) from exc

        for cookie in cookies:
            if cookie.name == "_huddle_session":
                return cookie.value

        raise RuntimeError("Mobile auth exchange completed without returning _huddle_session.")

    def send_booking_request(self, prepared: PreparedBookingRequest) -> dict[str, Any]:
        if not prepared.payload:
            return {"affectedSeats": [], "bookings": [], "errors": []}

        body = json.dumps(prepared.payload).encode("utf-8")
        graphql_request = request.Request(
            prepared.url,
            data=body,
            headers=prepared.headers,
            method="POST",
        )

        try:
            with request.urlopen(graphql_request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GraphQL request failed with HTTP {exc.code}: {body_text}"
            ) from exc

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Booking response was not valid JSON: {raw_response}") from exc

        if not isinstance(parsed, list) or not parsed:
            raise RuntimeError(f"Unexpected GraphQL response shape: {parsed!r}")

        operation = parsed[0]
        if operation.get("errors"):
            raise RuntimeError(json.dumps(operation["errors"], indent=2, sort_keys=True))

        mutation_payload = operation.get("data", {}).get("createSeatBookingSeries")
        if not mutation_payload:
            raise RuntimeError(f"Missing createSeatBookingSeries response: {operation!r}")

        return mutation_payload

    def build_seat_url(self, *, floor_id: str, seat_id: str) -> str:
        return f"{self.base_url}/visual-directory/floors/{floor_id}/seats/{seat_id}/max"