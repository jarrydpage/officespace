from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from officespace.auth import AuthConfigurationError, OfficeSpaceAuthContext
from officespace.booker import OfficeSpaceDeskBooker
from officespace.config import RunConfig, RunConfigurationError
from officespace.utils.logging import configure_logging


logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
cli = app

ConfigPathOption = Annotated[
    Path | None,
    typer.Option(
        help="Path to the runner config TOML file.",
    ),
]


def main(*, config_path: str | Path | None = None) -> int:
    resolved_config_path = Path(config_path or "officespace.toml")

    mode = "dry-run"
    env_mode = os.environ.get("OFFICESPACE_MODE")
    if env_mode is not None:
        mode = env_mode.strip().lower()

    if mode not in {"dry-run", "book"}:
        raise SystemExit("OFFICESPACE_MODE must be one of: dry-run, book.")

    try:
        config = RunConfig.load(resolved_config_path)
        booking = config.booking
        logger.info(
            "Desk booking run started for floor %s seat %s.",
            booking.floor_id,
            booking.seat_id,
        )
        auth_context = OfficeSpaceAuthContext.from_inputs(config.auth_inputs)
        token = auth_context.refresh_auth_token()
        auth_context.log_auth_token_status(token)
        booker = OfficeSpaceDeskBooker(
            auth_context=auth_context,
            floor_id=booking.floor_id,
            seat_id=booking.seat_id,
            site_id=booking.site_id,
        )
        prepared = booker.prepare_booking_request(
            employee_id=booking.employee_id,
            booking_date=booking.booking_date,
            schedule=booking.schedule,
        )
        if mode == "book":
            booker.send_booking_request(prepared)
        else:
            logger.info("%s", prepared)
    except (AuthConfigurationError, RunConfigurationError) as exc:
        raise SystemExit(str(exc)) from exc

    return 0


@app.callback(invoke_without_command=True)
def run_command(config_path: ConfigPathOption = None) -> None:
    configure_logging()
    raise typer.Exit(main(config_path=config_path))