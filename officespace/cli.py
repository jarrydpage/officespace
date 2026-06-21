from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys

import click

from .auth import AuthConfigurationError, AuthInputs, OfficeSpaceAuthContext
from .booker import OfficeSpaceDeskBooker
from .helpers import parse_schedule_arg
from .logging import configure_logging


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliAuthOptions:
    inputs: AuthInputs
    timeout_seconds: int


def auth_options(command: click.Callable) -> click.Callable:
    command = click.option(
        "--timeout-seconds",
        default=30,
        show_default=True,
        type=int,
        help="HTTP timeout in seconds.",
    )(command)
    command = click.option(
        "--auth-config-file",
        envvar="OFFICESPACE_AUTH_CONFIG_FILE",
        help="JSON file used to cache and reload the auth token.",
    )(command)
    command = click.option(
        "--auth-token",
        envvar="OFFICESPACE_AUTH_TOKEN",
        help="Existing OfficeSpace auth token loaded directly instead of using the auth cache.",
    )(command)
    return click.option(
        "--domain",
        envvar="OFFICESPACE_DOMAIN",
        help="OfficeSpace domain, for example 'inpex.officespacesoftware.com'.",
    )(command)


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
        raise click.UsageError(str(exc)) from exc


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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    pass


@cli.command("token")
@auth_options
def token_command(
    domain: str | None,
    auth_token: str | None,
    auth_config_file: str | None,
    timeout_seconds: int,
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


@cli.command("register")
@auth_options
@click.option(
    "--qr-image-file",
    envvar="OFFICESPACE_QR_IMAGE_FILE",
    help="PNG image file containing the OfficeSpace QR code used for auth registration.",
)
def register_command(
    domain: str | None,
    auth_token: str | None,
    auth_config_file: str | None,
    timeout_seconds: int,
    qr_image_file: str | None,
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


@cli.command("book")
@click.option(
    "--employee-id",
    help="Employee ID to book for. If omitted, the script resolves currentUser.linkedEmployee.id.",
)
@click.option("--floor-id", required=True, help="Floor ID from the seat URL.")
@click.option("--seat-id", required=True, help="Seat ID to book.")
@click.option(
    "--site-id",
    envvar="OFFICESPACE_SITE_ID",
    help="Optional site ID override. If omitted, the library resolves it from the seat.",
)
@click.option(
    "--date",
    "booking_date",
    help="Booking date in YYYY-MM-DD format.",
)
@click.option(
    "--schedule",
    help="Comma-separated days to book across the allowed site window, for example monday,tuesday,wednesday.",
)
@click.option(
    "--check-in",
    help="Optional local check-in time override in HH:MM format.",
)
@click.option(
    "--check-out",
    help="Optional local check-out time override in HH:MM format.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the prepared GraphQL request without sending it.",
)
@auth_options
def book_command(
    employee_id: str | None,
    floor_id: str,
    seat_id: str,
    site_id: str | None,
    booking_date: str | None,
    schedule: str | None,
    check_in: str | None,
    check_out: str | None,
    dry_run: bool,
    domain: str | None,
    auth_token: str | None,
    auth_config_file: str | None,
    timeout_seconds: int,
) -> int:
    if bool(booking_date) == bool(schedule):
        raise click.UsageError("provide exactly one of --date or --schedule")
    if bool(check_in) != bool(check_out):
        raise click.UsageError("provide both --check-in and --check-out together")

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


def main(argv: list[str] | None = None) -> int:
    configure_logging()

    try:
        result = cli.main(args=argv or sys.argv[1:], standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code

    return int(result or 0)