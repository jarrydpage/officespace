from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from officespace.auth import AuthConfigurationError, AuthInputs, OfficeSpaceAuthContext
from officespace.booker import OfficeSpaceDeskBooker
from officespace.utils.helpers import parse_schedule_arg
from officespace.utils.logging import configure_logging


logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
cli = app


@app.callback()
def configure_app_logging() -> None:
    configure_logging()

DomainOption = Annotated[
    str | None,
    typer.Option(
        envvar="OFFICESPACE_DOMAIN",
        help="OfficeSpace domain, for example 'inpex.officespacesoftware.com'.",
    ),
]
AuthTokenOption = Annotated[
    str | None,
    typer.Option(
        envvar="OFFICESPACE_AUTH_TOKEN",
        help="Existing OfficeSpace auth token loaded directly instead of using the auth cache.",
    ),
]
AuthConfigFileOption = Annotated[
    str | None,
    typer.Option(
        envvar="OFFICESPACE_AUTH_CONFIG_FILE",
        help="JSON file used to cache and reload the auth token.",
    ),
]
TimeoutSecondsOption = Annotated[
    int,
    typer.Option(
        "--timeout-seconds",
        help="HTTP timeout in seconds.",
        show_default=True,
    ),
]
QrImageFileOption = Annotated[
    str | None,
    typer.Option(
        envvar="OFFICESPACE_QR_IMAGE_FILE",
        help="PNG image file containing the OfficeSpace QR code used for auth registration.",
    ),
]
EmployeeIdOption = Annotated[
    str | None,
    typer.Option(
        help="Employee ID to book for. If omitted, the script resolves currentUser.linkedEmployee.id.",
    ),
]
FloorIdOption = Annotated[
    str,
    typer.Option(help="Floor ID from the seat URL."),
]
SeatIdOption = Annotated[
    str,
    typer.Option(help="Seat ID to book."),
]
SiteIdOption = Annotated[
    str | None,
    typer.Option(
        envvar="OFFICESPACE_SITE_ID",
        help="Optional site ID override. If omitted, the library resolves it from the seat.",
    ),
]
BookingDateOption = Annotated[
    str | None,
    typer.Option("--date", help="Booking date in YYYY-MM-DD format."),
]
ScheduleOption = Annotated[
    str | None,
    typer.Option(
        help="Comma-separated days to book across the allowed site window, for example monday,tuesday,wednesday.",
    ),
]
CheckInOption = Annotated[
    str | None,
    typer.Option(help="Optional local check-in time override in HH:MM format."),
]
CheckOutOption = Annotated[
    str | None,
    typer.Option(help="Optional local check-out time override in HH:MM format."),
]
DryRunOption = Annotated[
    bool,
    typer.Option(help="Print the prepared GraphQL request without sending it."),
]


@dataclass(frozen=True)
class CliAuthOptions:
    inputs: AuthInputs
    timeout_seconds: int


def build_cli_auth_options(
    *,
    domain: str | None,
    auth_token: str | None,
    auth_config_file: str | None,
    timeout_seconds: int,
    qr_image_file: str | None = None,
) -> CliAuthOptions:
    resolved_auth_config_file = auth_config_file or str(Path("auth.json"))

    return CliAuthOptions(
        inputs=AuthInputs(
            domain=domain,
            auth_token=auth_token,
            qr_image_file=qr_image_file,
            auth_config_file=resolved_auth_config_file,
        ),
        timeout_seconds=timeout_seconds,
    )


def resolve_auth_context(auth: CliAuthOptions) -> OfficeSpaceAuthContext:
    try:
        return OfficeSpaceAuthContext.from_auth_inputs(
            auth.inputs,
            timeout_seconds=auth.timeout_seconds,
        )
    except AuthConfigurationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def resolve_booker(
    auth: CliAuthOptions,
    *,
    floor_id: str,
    seat_id: str,
    site_id: str | None = None,
) -> OfficeSpaceDeskBooker:
    auth_context = resolve_auth_context(auth)
    token = auth_context.refresh_auth_token()
    auth_context.log_auth_token_status(token)
    return OfficeSpaceDeskBooker(
        auth_context=auth_context,
        floor_id=floor_id,
        seat_id=seat_id,
        site_id=site_id,
    )


@app.command("token")
def token_command(
    domain: DomainOption = None,
    auth_token: AuthTokenOption = None,
    auth_config_file: AuthConfigFileOption = None,
    timeout_seconds: TimeoutSecondsOption = 30,
) -> int:
    auth = build_cli_auth_options(
        domain=domain,
        auth_token=auth_token,
        auth_config_file=auth_config_file,
        timeout_seconds=timeout_seconds,
    )
    auth_context = resolve_auth_context(auth)
    token = auth_context.refresh_auth_token()
    auth_context.log_auth_token_status(token)
    logger.info(
        json.dumps(
            {"authToken": token},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


@app.command("register")
def register_command(
    domain: DomainOption = None,
    auth_token: AuthTokenOption = None,
    auth_config_file: AuthConfigFileOption = None,
    timeout_seconds: TimeoutSecondsOption = 30,
    qr_image_file: QrImageFileOption = None,
) -> int:
    resolved_qr_image_file = qr_image_file
    if resolved_qr_image_file is None:
        default_qr_image_path = Path("qr.png")
        if default_qr_image_path.exists():
            resolved_qr_image_file = str(default_qr_image_path)

    auth = build_cli_auth_options(
        domain=domain,
        auth_token=auth_token,
        auth_config_file=auth_config_file,
        timeout_seconds=timeout_seconds,
        qr_image_file=resolved_qr_image_file,
    )
    auth_context = resolve_auth_context(auth)
    token = auth_context.register_auth_token()
    auth_context.log_auth_token_status(token)

    result = {
        "status": "ok",
        "authConfigFile": str(auth_context.auth_config_path)
        if auth_context.auth_config_path is not None
        else None,
    }
    logger.info(json.dumps(result, indent=2, sort_keys=True))
    return 0


@app.command("book")
def book_command(
    employee_id: EmployeeIdOption = None,
    floor_id: FloorIdOption = ...,
    seat_id: SeatIdOption = ...,
    site_id: SiteIdOption = None,
    booking_date: BookingDateOption = None,
    schedule: ScheduleOption = None,
    check_in: CheckInOption = None,
    check_out: CheckOutOption = None,
    dry_run: DryRunOption = False,
    domain: DomainOption = None,
    auth_token: AuthTokenOption = None,
    auth_config_file: AuthConfigFileOption = None,
    timeout_seconds: TimeoutSecondsOption = 30,
) -> int:
    if bool(booking_date) == bool(schedule):
        raise typer.BadParameter("provide exactly one of --date or --schedule")
    if bool(check_in) != bool(check_out):
        raise typer.BadParameter("provide both --check-in and --check-out together")

    auth = build_cli_auth_options(
        domain=domain,
        auth_token=auth_token,
        auth_config_file=auth_config_file,
        timeout_seconds=timeout_seconds,
    )
    booker = resolve_booker(
        auth,
        floor_id=floor_id,
        seat_id=seat_id,
        site_id=site_id,
    )

    parsed_schedule = parse_schedule_arg(schedule)

    prepared = booker.prepare_booking_request(
        employee_id=employee_id,
        booking_date=booking_date,
        schedule=parsed_schedule,
        check_in=check_in,
        check_out=check_out,
    )

    if dry_run:
        logger.info("%s", prepared)
        return 0

    booker.send_booking_request(prepared)

    return 0