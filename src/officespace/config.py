from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from officespace.auth import AuthInputs


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
    auth_inputs: AuthInputs
    booking: BookingConfig

    @classmethod
    def load(
        cls,
        config_path: str | Path,
    ) -> RunConfig:
        resolved_config_path = Path(config_path).expanduser()
        try:
            raw_config = tomllib.loads(
                resolved_config_path.read_text(encoding="utf-8")
            ) or {}
        except FileNotFoundError:
            raw_config = {}
        except OSError as exc:
            raise RunConfigurationError(
                f"Unable to read config file {resolved_config_path}: {exc}"
            ) from exc
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise RunConfigurationError(
                f"Unable to parse TOML config file {resolved_config_path}: {exc}"
            ) from exc

        if not isinstance(raw_config, dict):
            raise RunConfigurationError("Config file must contain a top-level mapping.")

        return parse_run_config(raw_config, config_dir=resolved_config_path.parent)


class RunConfigurationError(ValueError):
    pass


def _env_string(name: str, fallback: Any = None) -> Any:
    value = os.getenv(name)
    if value is None:
        return fallback

    stripped = value.strip()
    return stripped or None


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

    auth_token = os.getenv("OFFICESPACE_AUTH_TOKEN", auth_config.get("auth_token"))

    max_token_age_minutes = os.getenv(
        "OFFICESPACE_MAX_TOKEN_AGE",
        auth_config.get("max_token_age", 24 * 60),
    )

    try:
        max_token_age_minutes = int(max_token_age_minutes)
        if max_token_age_minutes < 0:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise RunConfigurationError(
            "auth.max_token_age must be a non-negative integer minutes value."
        ) from exc

    max_token_age_seconds = max_token_age_minutes * 60

    return AuthInputs(
        domain=os.getenv("OFFICESPACE_DOMAIN", auth_config.get("domain")),
        auth_token=auth_token,
        qr_image_file=None,
        auth_config_file=resolved_auth_config_file,
        max_token_age_seconds=max_token_age_seconds,
    )


def parse_run_config(
    config: dict[str, Any],
    *,
    config_dir: str | Path = ".",
) -> RunConfig:
    resolved_config_dir = Path(config_dir)
    auth_config = config.get("auth") or {}
    if not isinstance(auth_config, dict):
        raise RunConfigurationError("auth must be a mapping.")

    booking_config = config.get("booking") or {}
    if not isinstance(booking_config, dict):
        raise RunConfigurationError("booking must be a mapping.")

    booking_date = _env_string(
        "OFFICESPACE_BOOKING_DATE",
        booking_config.get("booking_date"),
    )

    schedule = booking_config.get("schedule")
    env_schedule = _env_string("OFFICESPACE_SCHEDULE")
    if env_schedule is not None:
        error_message = (
            'OFFICESPACE_SCHEDULE must be a JSON array such as ["monday", "tuesday"].'
        )
        try:
            schedule = json.loads(env_schedule)
        except json.JSONDecodeError as exc:
            raise RunConfigurationError(error_message) from exc
        if not isinstance(schedule, list):
            raise RunConfigurationError(error_message)

    if schedule is not None and not isinstance(schedule, list):
        raise RunConfigurationError("booking.schedule must be a list when provided.")

    if schedule is not None:
        normalized_schedule = []
        for entry in schedule:
            normalized_entry = str(entry).strip()
            if normalized_entry:
                normalized_schedule.append(normalized_entry)
        schedule = normalized_schedule or None

    if bool(booking_date) == bool(schedule):
        raise RunConfigurationError(
            "Set exactly one of booking.booking_date or booking.schedule."
        )

    floor_id = _env_string("OFFICESPACE_FLOOR_ID", booking_config.get("floor_id"))
    seat_id = _env_string("OFFICESPACE_SEAT_ID", booking_config.get("seat_id"))
    if floor_id is None or seat_id is None:
        raise RunConfigurationError("booking.floor_id and booking.seat_id are required.")

    site_id = _env_string("OFFICESPACE_SITE_ID", booking_config.get("site_id"))
    resolved_site_id = None
    if site_id is not None:
        resolved_site_id = str(site_id)

    employee_id = _env_string(
        "OFFICESPACE_EMPLOYEE_ID",
        booking_config.get("employee_id"),
    )
    resolved_employee_id = None
    if employee_id is not None:
        resolved_employee_id = str(employee_id)

    resolved_booking_date = None
    if booking_date is not None:
        resolved_booking_date = str(booking_date)

    booking = BookingConfig(
        employee_id=resolved_employee_id,
        site_id=resolved_site_id,
        floor_id=str(floor_id),
        seat_id=str(seat_id),
        booking_date=resolved_booking_date,
        schedule=schedule,
    )

    return RunConfig(
        auth_inputs=_load_auth_inputs(auth_config, config_dir=resolved_config_dir),
        booking=booking,
    )