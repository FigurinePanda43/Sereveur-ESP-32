import bcrypt
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timedelta

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
_SECRET = os.getenv("APP_SECRET_KEY", "").encode()
COOKIE_NAME = "esp32_session"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_SECONDS", "2592000"))


def get_domain():
    return os.getenv("DOMAIN", "mondomaine.com")


def get_cookie_domain():
    return f".{get_domain()}"


def verify_password(password: str) -> bool:
    if not ADMIN_PASSWORD_HASH:
        return False
    try:
        return bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode())
    except Exception:
        return False


def make_token() -> str:
    ts = str(int(time.time()))
    raw = f"{ADMIN_USER}:{ts}"
    sig = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}:{sig}"


def verify_token(token: str) -> bool:
    if not token or not _SECRET:
        return False
    try:
        last = token.rfind(":")
        raw, sig = token[:last], token[last + 1:]
        ts = raw.rsplit(":", 1)[1]
        if int(time.time()) - int(ts) > SESSION_MAX_AGE:
            return False
        expected = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def token_age(token: str) -> int:
    """Returns age in seconds, -1 if invalid."""
    try:
        parts = token.rsplit(":", 1)[0]
        ts = int(parts.rsplit(":", 1)[1])
        return int(time.time()) - ts
    except Exception:
        return -1


def get_client_ip(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        val = request.headers.get(header, "")
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_ip_blocked(db: Session, ip: str) -> bool:
    from models import BlockedIP
    now = datetime.utcnow()
    block = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
    return block is not None and block.blocked_until > now


def record_attempt(db: Session, ip: str, username: str, user_agent: str, success: bool, failure_reason: str = None):
    from models import AuthAttempt, AccessLog
    attempt = AuthAttempt(
        ip_address=ip,
        username=username,
        user_agent=user_agent,
        success=success,
        failure_reason=failure_reason,
    )
    db.add(attempt)
    log = AccessLog(
        event_type="auth_success" if success else "auth_failed",
        source_ip=ip,
        user_agent=user_agent,
        message=failure_reason or ("Login réussi" if success else "Échec login"),
    )
    db.add(log)
    db.commit()


def apply_brute_force_rules(db: Session, ip: str, user_agent: str):
    """Check failure counts and block IP if needed. Returns (is_blocked, blocked_until_or_None)."""
    from models import AuthAttempt, BlockedIP, AccessLog
    now = datetime.utcnow()

    rules = [
        # (window_minutes, max_failures, block_minutes)
        (10, 50, 15),
        (60, 100, 60),
        (1440, 200, 1440),
    ]

    for window_min, max_fails, block_min in rules:
        since = now - timedelta(minutes=window_min)
        count = db.query(AuthAttempt).filter(
            AuthAttempt.ip_address == ip,
            AuthAttempt.success == False,
            AuthAttempt.created_at >= since,
        ).count()

        if count >= max_fails:
            blocked_until = now + timedelta(minutes=block_min)
            existing = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
            if existing:
                existing.blocked_until = blocked_until
                existing.reason = f"{count} échecs en {window_min} min"
                existing.updated_at = now
            else:
                block = BlockedIP(
                    ip_address=ip,
                    blocked_until=blocked_until,
                    reason=f"{count} échecs en {window_min} min",
                )
                db.add(block)
            log = AccessLog(
                event_type="ip_blocked",
                source_ip=ip,
                user_agent=user_agent,
                message=f"Blocage {block_min}min : {count} échecs en {window_min}min",
            )
            db.add(log)
            db.commit()
            return True, blocked_until

    return False, None


# Middleware
_PUBLIC_PATHS = {"/auth/login", "/auth/logout", "/auth/check"}
_PUBLIC_PREFIXES = ("/css/", "/js/", "/favicon")


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    token = request.cookies.get(COOKIE_NAME, "")
    if not verify_token(token):
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Non authentifié"})
        next_url = str(request.url)
        return RedirectResponse(f"/auth/login?next={next_url}", status_code=302)

    response = await call_next(request)

    # Renew if > 50% of session time elapsed
    age = token_age(token)
    if 0 < age > SESSION_MAX_AGE // 2:
        new_token = make_token()
        response.set_cookie(
            COOKIE_NAME,
            new_token,
            domain=get_cookie_domain(),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )

    return response
