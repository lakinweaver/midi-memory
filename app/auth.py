"""Single shared password with a signed cookie. Enough for a device on your own LAN."""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "midi_memory_session"
_SALT = "midi-memory-login"

# Paths reachable without a session cookie.
PUBLIC_PATHS = frozenset({"/login", "/logout", "/healthz"})
PUBLIC_PREFIXES = ("/static/",)


class Auth:
    def __init__(self, password: str, secret_key: str, max_age_days: int = 90) -> None:
        self.password = password
        self.enabled = bool(password)
        self.max_age = max_age_days * 86_400
        self._serializer = URLSafeTimedSerializer(secret_key, salt=_SALT)
        self._expected = _digest(password) if password else ""

    def verify_password(self, candidate: str) -> bool:
        if not self.enabled:
            return True
        return secrets.compare_digest(_digest(candidate or ""), self._expected)

    def issue(self, response: Response) -> None:
        token = self._serializer.dumps({"v": 1})
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=self.max_age,
            httponly=True,
            samesite="lax",
            # No secure=True: this is served over plain HTTP on a home network.
        )

    def revoke(self, response: Response) -> None:
        response.delete_cookie(COOKIE_NAME)

    def is_authenticated(self, request: Request) -> bool:
        if not self.enabled:
            return True
        token: Optional[str] = request.cookies.get(COOKIE_NAME)
        if not token:
            return False
        try:
            self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return False
        return True


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
