from __future__ import annotations

from dataclasses import dataclass
import json
from http import cookiejar
from pathlib import Path
from typing import Any, Self
from urllib import error, request

from .constants import (
    CSRF_PATTERNS,
    DEFAULT_USER_AGENT,
    MOBILE_AUTH_USER_AGENT,
    MOBILE_QR_USER_AGENT,
)
from .helpers import derive_subdomain, extract_qr_link_details
from .tokens import decode_jwt_payload, token_is_expired


@dataclass(frozen=True)
class AuthInputs:
    subdomain: str | None
    session_cookie: str | None
    mobile_bearer_token: str | None
    qr_token: str | None
    qr_link: str | None
    auth_config_file: str | None


@dataclass(frozen=True)
class _ResolvedAuthContext:
    subdomain: str
    qr_token: str | None


class AuthConfigurationError(ValueError):
    pass


def _resolve_auth_context(auth: AuthInputs) -> _ResolvedAuthContext:
    subdomain = auth.subdomain
    qr_token = auth.qr_token
    qr_link = auth.qr_link

    if (
        not auth.session_cookie
        and not auth.mobile_bearer_token
        and not qr_token
        and not qr_link
        and not auth.auth_config_file
    ):
        raise AuthConfigurationError(
            "provide at least one auth input: session_cookie, mobile_bearer_token, "
            "qr_token, qr_link, or auth_config_file"
        )

    if qr_token and qr_token.startswith("officespacemobile://"):
        qr_link = qr_token
        qr_token = None

    if qr_link:
        qr_domain, qr_link_token = extract_qr_link_details(qr_link)
        qr_token = qr_token or qr_link_token
        if subdomain and qr_domain:
            expected_domain = f"{subdomain}.officespacesoftware.com"
            if qr_domain != expected_domain:
                raise AuthConfigurationError(
                    f"QR link domain {qr_domain} does not match subdomain {subdomain}."
                )
        elif not subdomain and qr_domain:
            subdomain = derive_subdomain(qr_domain)

    if not subdomain:
        raise AuthConfigurationError(
            "OfficeSpace subdomain is required, either directly or via qr_link."
        )

    return _ResolvedAuthContext(subdomain=subdomain, qr_token=qr_token)


class OfficeSpaceAuthContext:
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
    ) -> Self:
        resolved_auth = _resolve_auth_context(auth)
        return cls(
            subdomain=resolved_auth.subdomain,
            session_cookie=auth.session_cookie,
            auth_config_file=auth.auth_config_file,
            mobile_bearer_token=auth.mobile_bearer_token,
            qr_token=resolved_auth.qr_token,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )

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
            "The seat page format may have changed."
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

    def build_seat_url(self, *, floor_id: str, seat_id: str) -> str:
        return f"{self.base_url}/visual-directory/floors/{floor_id}/seats/{seat_id}/max"