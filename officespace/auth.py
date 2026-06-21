from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Self
from urllib import error, request

from .constants import (
    DEFAULT_USER_AGENT,
    MOBILE_QR_USER_AGENT,
)
from .qr import decode_qr_link_image_file, extract_qr_link_details
from .tokens import (
    decode_jwt_payload,
    token_is_expired,
    token_is_stale,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthInputs:
    domain: str | None
    auth_token: str | None
    qr_image_file: str | None
    auth_config_file: str | None
    max_token_age_seconds: int = 24 * 60 * 60


class AuthConfigurationError(ValueError):
    pass


class OfficeSpaceAuthContext:
    def __init__(
        self,
        domain: str,
        *,
        auth_config_file: str | None = None,
        auth_token: str | None = None,
        registration_token: str | None = None,
        max_token_age_seconds: int = 24 * 60 * 60,
        timeout_seconds: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.auth_config_path = (
            Path(auth_config_file).expanduser() if auth_config_file else None
        )
        self.auth_token = auth_token
        self.logged_auth_token: str | None = None
        self.registration_token = registration_token
        self.max_token_age_seconds = max_token_age_seconds
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    @classmethod
    def from_auth_inputs(
        cls,
        auth: AuthInputs,
        *,
        timeout_seconds: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> Self:
        if not any((auth.auth_token, auth.qr_image_file, auth.auth_config_file)):
            raise AuthConfigurationError(
                "provide at least one auth input: auth_token, qr_image_file, or auth_config_file"
            )

        auth_token = auth.auth_token
        if auth_token:
            payload = decode_jwt_payload(auth_token)
            if not any(key in payload for key in ("exp", "iat", "sub")):
                raise AuthConfigurationError(
                    "auth_token must be an existing OfficeSpace auth token. QR registration is only supported via qr_image_file."
                )

        domain = auth.domain
        registration_token = None

        if auth.qr_image_file:
            qr_image_path = Path(auth.qr_image_file).expanduser()
            if not qr_image_path.exists():
                raise AuthConfigurationError(
                    f"QR image file {qr_image_path} does not exist."
                )

            try:
                qr_link = decode_qr_link_image_file(qr_image_path)
            except RuntimeError as exc:
                raise AuthConfigurationError(str(exc)) from exc

            qr_domain, registration_token = extract_qr_link_details(qr_link)
            if domain and qr_domain and qr_domain != domain:
                raise AuthConfigurationError(
                    f"QR link domain {qr_domain} does not match domain {domain}."
                )
            if not domain and qr_domain:
                domain = qr_domain

        if not domain and auth.auth_config_file:
            try:
                cached_auth_config = cls._read_auth_config(auth.auth_config_file)
            except RuntimeError as exc:
                raise AuthConfigurationError(str(exc)) from exc

            cached_domain = cached_auth_config.get("domain") if cached_auth_config else None
            if isinstance(cached_domain, str) and cached_domain:
                domain = cached_domain

        if not domain:
            raise AuthConfigurationError(
                "OfficeSpace domain is required, either directly, from QR-image registration, or from the auth config cache."
            )

        return cls(
            domain=domain,
            auth_config_file=auth.auth_config_file,
            auth_token=auth_token,
            registration_token=registration_token,
            max_token_age_seconds=auth.max_token_age_seconds,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )

    @staticmethod
    def _read_auth_config(
        auth_config_file: str | Path | None,
    ) -> dict[str, Any] | None:
        if not auth_config_file:
            return None

        auth_config_path = Path(auth_config_file).expanduser()
        if not auth_config_path.exists():
            return None

        try:
            config = json.loads(auth_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Unable to read auth config file {auth_config_path}: {exc}"
            ) from exc

        if not isinstance(config, dict):
            raise RuntimeError(
                f"Auth config file {auth_config_path} must contain a top-level mapping."
            )

        return config

    def _current_auth_token(self) -> str | None:
        token = self.auth_token
        if token and token_is_expired(token):
            token = None

        if token:
            return token

        return self.load_cached_auth_token()

    def ensure_auth_token(self) -> str:
        token = self._current_auth_token()
        if not token:
            if not self.registration_token:
                raise RuntimeError(
                    "No valid cached auth token found. Run register to create auth.json or set OFFICESPACE_AUTH_TOKEN."
                )

            return self.register_auth_token()

        self.auth_token = token

        if token == self.logged_auth_token:
            return token

        exp = decode_jwt_payload(token).get("exp")
        if not isinstance(exp, (int, float)):
            logger.info("Auth token acquired.")
            self.logged_auth_token = token
            return token

        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        remaining_days = max(
            0,
            int((expires_at - datetime.now(timezone.utc)).total_seconds() // 86400),
        )
        logger.info(
            "Auth token valid until %s (%sd left).",
            expires_at.isoformat(),
            remaining_days,
        )
        self.logged_auth_token = token
        return token

    def register_auth_token(self) -> str:
        if not self.registration_token:
            raise RuntimeError(
                "No registration token available. Run register with a QR image."
            )

        logger.info("Using registration token for auth registration.")
        try:
            token = self._request_auth_token(
                self.registration_token,
                missing_token_message=(
                    "Auth registration exchange completed without returning an auth token."
                ),
            )
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Auth registration exchange failed with HTTP {exc.code}: {body_text}"
            ) from exc

        self.auth_token = token
        self.save_cached_auth_token(token)
        return self.ensure_auth_token()

    def refresh_auth_token(self) -> str:
        token = self._current_auth_token()
        if not token:
            return self.ensure_auth_token()

        self.auth_token = token
        if not token_is_stale(token, max_age_seconds=self.max_token_age_seconds):
            return self.ensure_auth_token()

        logger.info(
            "Cached auth token is older than the configured max token age (%ss) - refreshing.",
            self.max_token_age_seconds,
        )

        try:
            refreshed_token = self._request_auth_token(
                token,
                missing_token_message=(
                    "Auth token refresh completed without returning an auth token."
                ),
            )
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise RuntimeError(
                    "Auth token was rejected with HTTP 401. Run register again to create a new auth token."
                ) from exc
            raise RuntimeError(
                f"Auth token refresh failed with HTTP {exc.code}: {body_text}"
            ) from exc

        self.auth_token = refreshed_token
        self.save_cached_auth_token(refreshed_token)
        return self.ensure_auth_token()

    def load_cached_auth_token(self) -> str | None:
        config = self._read_auth_config(self.auth_config_path)
        if config is None:
            return None

        token = config.get("authToken")
        if not isinstance(token, str) or not token:
            return None

        if token_is_expired(token):
            return None

        return token

    def save_cached_auth_token(self, token: str) -> None:
        if not self.auth_config_path:
            return

        payload = decode_jwt_payload(token)
        config: dict[str, Any] = {"authToken": token, "domain": self.domain}
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

    def _request_auth_token(
        self,
        source_token: str,
        *,
        missing_token_message: str,
    ) -> str:
        auth_url = f"{self.base_url}/ossmobile/auth"
        auth_request = request.Request(
            auth_url,
            data=b"",
            headers={
                "Accept": "*/*",
                "Authorization": f"Bearer {source_token}",
                "User-Agent": MOBILE_QR_USER_AGENT,
            },
            method="POST",
        )

        with request.urlopen(auth_request, timeout=self.timeout_seconds) as response:
            authorization_header = (
                response.headers.get("Authorization")
                or response.headers.get("authorization")
            )

        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise RuntimeError(missing_token_message)

        return authorization_header.split(" ", 1)[1]
    def build_seat_url(self, *, floor_id: str, seat_id: str) -> str:
        return f"{self.base_url}/visual-directory/floors/{floor_id}/seats/{seat_id}/max"