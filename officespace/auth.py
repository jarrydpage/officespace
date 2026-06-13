from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import time
from typing import Any

from .helpers import derive_subdomain, extract_qr_link_details


@dataclass(frozen=True)
class AuthInputs:
    subdomain: str | None
    session_cookie: str | None
    mobile_bearer_token: str | None
    qr_token: str | None
    qr_link: str | None
    auth_config_file: str | None


@dataclass(frozen=True)
class ResolvedAuthContext:
    subdomain: str
    qr_token: str | None


class AuthConfigurationError(ValueError):
    pass


def resolve_auth_context(auth: AuthInputs) -> ResolvedAuthContext:
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

    return ResolvedAuthContext(subdomain=subdomain, qr_token=qr_token)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload_segment = token.split(".")[1]
        padded_payload = payload_segment + "=" * (-len(payload_segment) % 4)
        decoded_payload = base64.urlsafe_b64decode(padded_payload)
        parsed_payload = json.loads(decoded_payload)
    except (IndexError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}

    return parsed_payload if isinstance(parsed_payload, dict) else {}


def token_is_expired(token: str, *, leeway_seconds: int = 30) -> bool:
    exp = decode_jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp <= time.time() + leeway_seconds