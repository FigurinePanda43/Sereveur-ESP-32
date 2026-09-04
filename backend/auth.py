import bcrypt
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
_SECRET = os.getenv("APP_SECRET_KEY", "").encode()
COOKIE_NAME = "esp32_session"

# Durée de vie du jeton de session. Plafond dur à 31 jours : quelle que soit la
# valeur de SESSION_MAX_AGE_SECONDS dans l'environnement, un jeton ne peut jamais
# rester valable plus d'un mois (exigence de sécurité). Plancher à 5 minutes pour
# éviter une configuration qui déconnecterait en boucle.
_SESSION_MAX_AGE_CAP = 31 * 24 * 3600   # 2 678 400 s ≈ 1 mois
_SESSION_MAX_AGE_FLOOR = 300
_requested_max_age = int(os.getenv("SESSION_MAX_AGE_SECONDS", "2592000"))
SESSION_MAX_AGE = max(_SESSION_MAX_AGE_FLOOR, min(_requested_max_age, _SESSION_MAX_AGE_CAP))
if _requested_max_age > _SESSION_MAX_AGE_CAP:
    logger.warning(
        "SESSION_MAX_AGE_SECONDS=%d dépasse le plafond de %d s (~1 mois) — ramené à ce plafond",
        _requested_max_age, _SESSION_MAX_AGE_CAP,
    )

# Levier de révocation globale : les jetons émis avant cet instant (epoch Unix)
# sont refusés. Laisser vide pour ne rien révoquer. Changer APP_SECRET_KEY
# invalide également tous les jetons existants.
try:
    _MIN_ISSUED_AT = int(os.getenv("AUTH_MIN_ISSUED_AT", "0"))
except ValueError:
    _MIN_ISSUED_AT = 0


def get_domain():
    return os.getenv("DOMAIN", "mondomaine.com")


def get_cookie_domain():
    return f".{get_domain()}"


def _safe_next(next_url: str, default: str = "/") -> str:
    """Neutralise les redirections ouvertes après connexion.

    N'autorise qu'un chemin relatif (``/...``) ou une URL absolue dont l'hôte
    appartient au domaine du portail. Tout le reste (``https://evil.com``,
    ``//evil.com``, ``https:evil.com``…) est ramené à ``default``.
    """
    if not next_url:
        return default
    # Chemin relatif : doit commencer par un seul '/', sans '\' ni '//'.
    if next_url.startswith("/") and not next_url.startswith(("//", "/\\", "/%2f", "/%2F")):
        return next_url
    try:
        from urllib.parse import urlparse
        parsed = urlparse(next_url)
    except Exception:
        return default
    if parsed.scheme in ("http", "https") and parsed.hostname:
        domain = get_domain().lower()
        host = parsed.hostname.lower()
        if host == domain or host.endswith(f".{domain}"):
            return next_url
    return default


def verify_password(password: str) -> bool:
    if not ADMIN_PASSWORD_HASH:
        return False
    try:
        return bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode())
    except Exception:
        return False


def hash_password(password: str) -> str:
    """Hash bcrypt d'un mot de passe utilisateur (à stocker en base)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_user_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def make_token(subject: str = None, role: str = "admin") -> str:
    """Jeton signé HMAC : ``role:subject:timestamp:signature``.

    ``role`` vaut "admin" (session administrateur) ou "user" (utilisateur
    secondaire à accès limité). ``subject`` est l'identifiant du compte.
    """
    subject = subject or ADMIN_USER
    ts = str(int(time.time()))
    raw = f"{role}:{subject}:{ts}"
    sig = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}:{sig}"


def parse_token(token: str) -> Optional[dict]:
    """Retourne ``{"role", "subject", "ts"}`` si la signature est valide et le
    jeton non expiré, sinon ``None``. Accepte aussi l'ancien format
    ``subject:ts:sig`` (traité comme un jeton administrateur) pour compatibilité."""
    if not token or not _SECRET:
        return None
    try:
        last = token.rfind(":")
        raw, sig = token[:last], token[last + 1:]
        expected = hmac.new(_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        parts = raw.split(":")
        if len(parts) == 3:
            role, subject, ts = parts
        elif len(parts) == 2:  # ancien format admin
            role, (subject, ts) = "admin", parts
        else:
            return None
        ts_int = int(ts)
        if int(time.time()) - ts_int > SESSION_MAX_AGE:
            return None
        if _MIN_ISSUED_AT and ts_int < _MIN_ISSUED_AT:
            return None  # jeton révoqué (émis avant le seuil de révocation)
        return {"role": role, "subject": subject, "ts": ts_int}
    except Exception:
        return None


def verify_token(token: str) -> bool:
    """Vrai si le jeton est une session administrateur valide.

    Les jetons "user" (accès limité à des services) ne donnent PAS accès au
    portail d'administration ni aux API ; ils ne sont acceptés que par le
    contrôle ``forward_auth`` (voir ``routers/auth.auth_check``)."""
    principal = parse_token(token)
    return principal is not None and principal["role"] == "admin"


def token_age(token: str) -> int:
    """Returns age in seconds, -1 if invalid."""
    principal = parse_token(token)
    return int(time.time()) - principal["ts"] if principal else -1


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

    # (fenêtre_minutes, échecs_max, blocage_minutes). Seuils resserrés : l'ancien
    # réglage tolérait 50 essais en 10 min avant tout blocage, ce qui laissait
    # largement la place à une attaque par dictionnaire. Le premier palier est
    # réglable via l'environnement pour les déploiements exigeants.
    try:
        primary_max = max(1, int(os.getenv("BRUTEFORCE_MAX_ATTEMPTS", "10")))
    except ValueError:
        primary_max = 10
    try:
        primary_window = max(1, int(os.getenv("BRUTEFORCE_WINDOW_MINUTES", "15")))
    except ValueError:
        primary_window = 15
    try:
        primary_block = max(1, int(os.getenv("BRUTEFORCE_BLOCK_MINUTES", "15")))
    except ValueError:
        primary_block = 15

    rules = [
        (primary_window, primary_max, primary_block),
        (60, 20, 60),      # 20 échecs / 1 h → blocage 1 h
        (1440, 50, 1440),  # 50 échecs / 24 h → blocage 24 h
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
_PUBLIC_PATHS = {"/auth/login", "/auth/logout", "/auth/check", "/device-suspended"}
_PUBLIC_PREFIXES = ("/css/", "/js/", "/favicon")

# En-têtes de sécurité appliqués à toutes les réponses du portail. /auth/check
# (contrôle forward_auth des équipements) est exclu pour ne pas polluer la
# réponse renvoyée à Caddy. Les équipements eux-mêmes sont servis directement
# par Caddy et ne passent pas par ce middleware : ils ne sont pas affectés.
_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",                       # anti-clickjacking (terminal, mise à jour…)
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def _apply_security_headers(response, path: str):
    if path == "/auth/check":
        return response
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return _apply_security_headers(await call_next(request), path)

    token = request.cookies.get(COOKIE_NAME, "")
    if not verify_token(token):
        if path.startswith("/api/"):
            resp = JSONResponse(status_code=401, content={"detail": "Non authentifié"})
        else:
            resp = RedirectResponse(f"/auth/login?next={_safe_next(str(request.url))}", status_code=302)
        return _apply_security_headers(resp, path)

    response = await call_next(request)

    # Renouvellement glissant : au-delà de 50 % de la durée de vie écoulée.
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

    return _apply_security_headers(response, path)
