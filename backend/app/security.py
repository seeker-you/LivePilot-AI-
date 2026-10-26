"""JWT (HS256) helpers and password hashing with the standard library only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from .config import settings


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_token(claims: dict[str, Any], expires_in_seconds: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now_ts = int(time.time())
    payload = {**claims, "iat": now_ts, "exp": now_ts + expires_in_seconds}
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(
        settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url_encode(signature)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = header_b64 + "." + payload_b64
        expected = hmac.new(
            settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        supplied = _b64url_decode(signature_b64)
        if not hmac.compare_digest(expected, supplied):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 120_000
    ).hex()
    return digest, salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, expected_hash)

