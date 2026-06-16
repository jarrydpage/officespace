from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Any


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