from __future__ import annotations

import logging
import os
from pathlib import Path

from officespace import (
    AuthConfigurationError,
    OfficeSpaceAuthContext,
    OfficeSpaceDeskBooker,
    RunConfig,
    RunConfigurationError,
)
from officespace.logging import configure_logging


CONFIG_PATH = Path(__file__).with_name("run.toml")
mode = "dry-run"
logger = logging.getLogger(__name__)

env_mode = os.environ.get("OFFICESPACE_MODE")
if env_mode is not None:
    mode = env_mode.strip().lower()

if mode not in {"dry-run", "book"}:
    raise SystemExit("OFFICESPACE_MODE must be one of: dry-run, book.")


try:
    configure_logging()
    config = RunConfig.load(CONFIG_PATH)
    booking = config.booking
    logger.info(
        "Desk booking run started for floor %s seat %s.",
        booking.floor_id,
        booking.seat_id,
    )
    auth_context = OfficeSpaceAuthContext.from_auth_inputs(config.auth_inputs)
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