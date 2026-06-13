from __future__ import annotations

import os
from pathlib import Path

from officespace.auth import AuthConfigurationError
from officespace import (
    OfficeSpaceDeskBooker,
    RunConfigurationError,
    format_booking_summary,
    load_run_config,
)


CONFIG_PATH = Path(__file__).with_name("run.toml")
mode = "dry-run"

env_mode = os.environ.get("OFFICESPACE_MODE")
if env_mode is not None:
    mode = env_mode.strip().lower()

if mode not in {"dry-run", "book"}:
    raise SystemExit("OFFICESPACE_MODE must be one of: dry-run, book.")


try:
    config = load_run_config(CONFIG_PATH)
    booker = OfficeSpaceDeskBooker.from_auth_inputs(
        config.auth_inputs,
        timeout_seconds=config.timeout_seconds,
    )
    booking = config.booking
    prepared = booker.prepare_booking_request(
        employee_id=booking.employee_id,
        floor_id=booking.floor_id,
        seat_id=booking.seat_id,
        site_id=booking.site_id,
        booking_date=booking.booking_date,
        schedule=booking.schedule,
    )
    if mode == "book":
        result = booker.send_booking_request(prepared)
        print(format_booking_summary(result, prepared))
    else:
        print(prepared)
except (AuthConfigurationError, RunConfigurationError) as exc:
    raise SystemExit(str(exc)) from exc