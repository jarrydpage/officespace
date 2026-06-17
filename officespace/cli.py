from __future__ import annotations

from dataclasses import dataclass
import json
import logging
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


pass_cli_auth = click.make_pass_decorator(CliAuthOptions)
def resolve_booker(
    auth: CliAuthOptions,
    *,
    floor_id: str,
    seat_id: str,
    site_id: str | None = None,
) -> OfficeSpaceDeskBooker:
    try:
        auth_context = OfficeSpaceAuthContext.from_auth_inputs(
            auth.inputs,
            timeout_seconds=auth.timeout_seconds,
        )
        return OfficeSpaceDeskBooker(
            auth_context=auth_context,
            floor_id=floor_id,
            seat_id=seat_id,
            site_id=site_id,
        )
    except AuthConfigurationError as exc:
        raise click.UsageError(str(exc)) from exc


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--subdomain",
    envvar="OFFICESPACE_SUBDOMAIN",
    help="OfficeSpace subdomain, for example 'inpex'.",
)
@click.option(
    "--session-cookie",
    envvar="OFFICESPACE_SESSION",
    help="Authenticated _huddle_session cookie value.",
)
@click.option(
    "--mobile-bearer-token",
    envvar="OFFICESPACE_MOBILE_BEARER",
    help="OfficeSpace mobile bearer token that can be exchanged at /ossmobile/auth.",
)
@click.option(
    "--qr-token",
    envvar="OFFICESPACE_QR_TOKEN",
    help="QR bootstrap token from the officespacemobile://huddle link.",
)
@click.option(
    "--qr-link",
    envvar="OFFICESPACE_QR_LINK",
    help="Full officespacemobile://huddle deep link from the QR code.",
)
@click.option(
    "--auth-config-file",
    envvar="OFFICESPACE_AUTH_CONFIG_FILE",
    help="JSON file used to cache and reload the mobile bearer token.",
)
@click.option(
    "--timeout-seconds",
    default=30,
    show_default=True,
    type=int,
    help="HTTP timeout in seconds.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    subdomain: str | None,
    session_cookie: str | None,
    mobile_bearer_token: str | None,
    qr_token: str | None,
    qr_link: str | None,
    auth_config_file: str | None,
    timeout_seconds: int,
) -> None:
    ctx.obj = CliAuthOptions(
        inputs=AuthInputs(
            subdomain=subdomain,
            session_cookie=session_cookie,
            mobile_bearer_token=mobile_bearer_token,
            qr_token=qr_token,
            qr_link=qr_link,
            auth_config_file=auth_config_file,
        ),
        timeout_seconds=timeout_seconds,
    )


@cli.command("bearer")
@pass_cli_auth
def bearer_command(auth: CliAuthOptions) -> int:
    try:
        auth_context = OfficeSpaceAuthContext.from_auth_inputs(
            auth.inputs,
            timeout_seconds=auth.timeout_seconds,
        )
    except AuthConfigurationError as exc:
        raise click.UsageError(str(exc)) from exc
    logger.info(
        json.dumps(
            {"mobileBearerToken": auth_context.ensure_mobile_bearer_token()},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


@cli.command("session")
@pass_cli_auth
def session_command(auth: CliAuthOptions) -> int:
    try:
        auth_context = OfficeSpaceAuthContext.from_auth_inputs(
            auth.inputs,
            timeout_seconds=auth.timeout_seconds,
        )
    except AuthConfigurationError as exc:
        raise click.UsageError(str(exc)) from exc
    logger.info(
        json.dumps(
            {"sessionCookie": auth_context.ensure_session_cookie()},
            indent=2,
            sort_keys=True,
        )
    )
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
@pass_cli_auth
def book_command(
    auth: CliAuthOptions,
    employee_id: str | None,
    floor_id: str,
    seat_id: str,
    site_id: str | None,
    booking_date: str | None,
    schedule: str | None,
    check_in: str | None,
    check_out: str | None,
    dry_run: bool,
) -> int:
    if bool(booking_date) == bool(schedule):
        raise click.UsageError("provide exactly one of --date or --schedule")
    if bool(check_in) != bool(check_out):
        raise click.UsageError("provide both --check-in and --check-out together")

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