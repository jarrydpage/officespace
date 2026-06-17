from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from .auth import AuthInputs


@dataclass(frozen=True)
class BookingConfig:
    employee_id: str | None
    site_id: str | None
    floor_id: str
    seat_id: str
    booking_date: str | None
    schedule: list[str] | None


@dataclass(frozen=True)
class RunConfig:
    timeout_seconds: int
    auth_inputs: AuthInputs
    booking: BookingConfig


class RunConfigurationError(ValueError):
    pass


def _load_auth_inputs(
    auth_config: dict[str, Any],
    *,
    config_dir: Path,
) -> AuthInputs:
    auth_config_file = os.getenv(
        "OFFICESPACE_AUTH_CONFIG_FILE",
        auth_config.get("auth_config_file", "auth.json"),
    )

    auth_config_path = None
    if auth_config_file:
        auth_config_path = Path(str(auth_config_file)).expanduser()
        if not auth_config_path.is_absolute():
            auth_config_path = config_dir / auth_config_path

    resolved_auth_config_file = None
    if auth_config_path is not None:
        resolved_auth_config_file = str(auth_config_path)

    return AuthInputs(
        subdomain=os.getenv("OFFICESPACE_SUBDOMAIN", auth_config.get("subdomain")),
        session_cookie=os.getenv("OFFICESPACE_SESSION", auth_config.get("session_cookie")),
        mobile_bearer_token=os.getenv(
            "OFFICESPACE_MOBILE_BEARER",
            auth_config.get("mobile_bearer_token"),
        ),
        qr_token=os.getenv("OFFICESPACE_QR_TOKEN", auth_config.get("qr_token")),
        qr_link=os.getenv("OFFICESPACE_QR_LINK", auth_config.get("qr_link")),
        auth_config_file=resolved_auth_config_file,
    )


def load_run_config(config_path: str | Path) -> RunConfig:
    resolved_config_path = Path(config_path).expanduser()
    try:
        config_bytes = resolved_config_path.read_bytes()
    except OSError as exc:
        raise RunConfigurationError(
            f"Unable to read config file {resolved_config_path}: {exc}"
        ) from exc

    try:
        raw_config = tomllib.loads(config_bytes.decode("utf-8")) or {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RunConfigurationError(
            f"Unable to parse TOML config file {resolved_config_path}: {exc}"
        ) from exc

    if not isinstance(raw_config, dict):
        raise RunConfigurationError("Config file must contain a top-level mapping.")

    return parse_run_config(
        raw_config,
        config_dir=resolved_config_path.parent,
    )


def parse_run_config(
    config: dict[str, Any],
    *,
    config_dir: str | Path = ".",
) -> RunConfig:
    resolved_config_dir = Path(config_dir)
    auth_config = config.get("auth") or {}
    booking_config = config.get("booking") or {}

    if not isinstance(auth_config, dict):
        raise RunConfigurationError("auth must be a mapping.")

    if not isinstance(booking_config, dict):
        raise RunConfigurationError("booking must be a mapping.")

    try:
        timeout_seconds = int(config.get("timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise RunConfigurationError("timeout_seconds must be an integer.") from exc

    auth_inputs = _load_auth_inputs(
        auth_config,
        config_dir=resolved_config_dir,
    )

    booking_date = booking_config.get("booking_date")
    schedule = booking_config.get("schedule")

    if schedule is not None and not isinstance(schedule, list):
        raise RunConfigurationError("booking.schedule must be a list when provided.")

    if bool(booking_date) == bool(schedule):
        raise RunConfigurationError(
            "Set exactly one of booking.booking_date or booking.schedule."
        )

    floor_id = booking_config.get("floor_id")
    seat_id = booking_config.get("seat_id")
    if floor_id is None or seat_id is None:
        raise RunConfigurationError("booking.floor_id and booking.seat_id are required.")

    site_id = booking_config.get("site_id")
    resolved_site_id = None
    if site_id is not None:
        resolved_site_id = str(site_id)

    resolved_schedule = None
    if schedule is not None:
        resolved_schedule = [str(entry) for entry in schedule]

    booking = BookingConfig(
        employee_id=booking_config.get("employee_id"),
        site_id=resolved_site_id,
        floor_id=str(floor_id),
        seat_id=str(seat_id),
        booking_date=booking_date,
        schedule=resolved_schedule,
    )

    return RunConfig(
        timeout_seconds=timeout_seconds,
        auth_inputs=auth_inputs,
        booking=booking,
    )