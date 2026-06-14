import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import Response

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
_SECRET = os.getenv("APP_SECRET_KEY", "changeme").encode()
COOKIE_NAME = "esp32_session"
SESSION_MAX_AGE = 86400  # 24 h

_UNPROTECTED = ("/css/", "/js/", "/favicon")


def _make_token() -> str:
    ts = str(int(time.time()))
    raw = f"{ADMIN_USER}:{ts}"
    sig = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}:{sig}"


def _verify_token(token: str) -> bool:
    try:
        last_colon = token.rfind(":")
        raw, sig = token[:last_colon], token[last_colon + 1:]
        ts = raw.rsplit(":", 1)[1]
        if int(time.time()) - int(ts) > SESSION_MAX_AGE:
            return False
        expected = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if any(path.startswith(p) for p in _UNPROTECTED):
        return await call_next(request)

    # Valid session cookie → let through
    if _verify_token(request.cookies.get(COOKIE_NAME, "")):
        return await call_next(request)

    # Basic Auth header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
            ok = secrets.compare_digest(user.encode(), ADMIN_USER.encode()) and \
                 secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
            if ok:
                response = await call_next(request)
                response.set_cookie(
                    COOKIE_NAME, _make_token(),
                    max_age=SESSION_MAX_AGE,
                    httponly=True,
                    samesite="lax",
                )
                return response
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="ESP32 Manager"'},
    )
