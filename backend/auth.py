import bcrypt
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
    from services import ipban
    return ipban.client_ip(request)


def get_client_country(request: Request) -> Optional[str]:
    from services import ipban
    return ipban.client_country(request)


def is_ip_blocked(db: Session, ip: str) -> bool:
    """Vrai si l'IP est actuellement bannie et qu'IPBan est actif."""
    from services import ipban
    return ipban.is_banned(db, ip)


def record_attempt(db: Session, ip: str, username: str, user_agent: str, success: bool,
                   failure_reason: str = None, country: str = None):
    from datetime import datetime
    from models import AuthAttempt, AccessLog
    from services import ipban
    ip = ipban.normalize_ip(ip)
    attempt = AuthAttempt(
        # Horodatage à la microseconde, comme le début d'un bannissement : les
        # échecs survenus dans la même seconde qu'un déblocage sont bien comptés.
        created_at=datetime.utcnow(),
        ip_address=ip,
        username=username,
        user_agent=user_agent,
        success=success,
        failure_reason=failure_reason,
        country=country,
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


def apply_brute_force_rules(db: Session, ip: str, user_agent: str, country: str = None):
    """Applique les règles IPBan après un échec. Retourne (bloquée, fin_ou_None).

    Les paliers (fenêtre, échecs max, durée), le bannissement progressif des
    récidivistes et l'activation se règlent dans l'onglet Sécurité ; les
    variables ``BRUTEFORCE_*`` ne fixent plus que le premier palier par défaut.
    """
    from services import ipban
    return ipban.register_failure(db, ip, user_agent, country)


# Middleware
_PUBLIC_PATHS = {"/auth/login", "/auth/logout", "/auth/check", "/ipban/check", "/device-suspended"}
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
    if path in ("/auth/check", "/ipban/check"):
        return response
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _blocked_response(request: Request, decision):
    """Réponse 403 d'IPBan : JSON pour les API, page autonome sinon (elle peut
    être servie sur le sous-domaine d'un équipement via forward_auth)."""
    import html
    from services import ipban
    from routers.auth import _state_page

    ip = html.escape(ipban.normalize_ip(get_client_ip(request)))
    if decision.code == "banned":
        heading = "Accès temporairement bloqué"
        if decision.until:
            body = (f"Trop de tentatives ont été détectées depuis votre adresse IP ({ip}). "
                    f"Réessayez après le {decision.until.strftime('%d/%m/%Y à %H:%M')} UTC.")
        else:
            heading = "Accès bloqué"
            body = f"Votre adresse IP ({ip}) a été bannie de ce serveur."
    else:
        heading = "Accès refusé"
        country = get_client_country(request)
        where = f", {html.escape(country)}" if country and country != ipban.UNKNOWN_COUNTRY else ""
        body = f"Votre adresse IP ({ip}{where}) n'est pas autorisée à accéder à ce serveur."

    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=403, content={"detail": heading, "reason": decision.code})
    page = _state_page(
        title=heading, chip="Protection IPBan", glyph="⛨", heading=heading,
        body=body + " Contactez l'administrateur si vous pensez qu'il s'agit d'une erreur.",
        accent="#ff3b30", accent_dark="#ff453a",
    )
    return HTMLResponse(page, status_code=403)


async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # IPBan passe avant tout le reste, pages publiques comprises : une IP
    # bannie ne doit même pas voir le formulaire de connexion. Les contrôles
    # forward_auth (/auth/check, /ipban/check) y passent aussi, avec l'IP et
    # l'hôte d'origine transmis par Caddy.
    from services import ipban
    decision = ipban.check(request)
    if decision is not None:
        return _apply_security_headers(_blocked_response(request, decision), path)

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
