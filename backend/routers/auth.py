import os
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    apply_brute_force_rules,
    get_client_ip,
    get_cookie_domain,
    is_ip_blocked,
    make_token,
    parse_token,
    record_attempt,
    verify_password,
    verify_user_password,
)
from models import AccessLog, Device, User, UserDeviceAccess

router = APIRouter(tags=["auth"])


def _set_session_cookie(response, subject: str, role: str):
    response.set_cookie(
        COOKIE_NAME,
        make_token(subject, role),
        domain=get_cookie_domain(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _slug_from_host(host: str) -> str:
    """Extrait le slug d'un hôte ``slug.DOMAIN`` (le port éventuel est retiré)."""
    host = (host or "").split(":")[0].strip().lower()
    domain = os.getenv("DOMAIN", "mondomaine.com").lower()
    suffix = f".{domain}"
    if host.endswith(suffix):
        return host[: -len(suffix)]
    return host

LOGIN_PAGE = os.path.join(os.path.dirname(__file__), "..", "frontend", "login.html")


@router.get("/auth/login", include_in_schema=False)
async def login_page():
    return FileResponse(LOGIN_PAGE)


@router.post("/auth/login", include_in_schema=False)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    if is_ip_blocked(db, ip):
        return HTMLResponse("Trop de tentatives. Réessayez plus tard.", status_code=429)

    # 1) Compte administrateur (identifiants issus de l'environnement)
    if verify_password(password) and username == os.getenv("ADMIN_USER", "admin"):
        record_attempt(db, ip, username, ua, success=True)
        response = RedirectResponse(next or "/", status_code=302)
        _set_session_cookie(response, username, "admin")
        return response

    # 2) Utilisateur secondaire (créé par l'administrateur, accès limité)
    user = db.query(User).filter(User.username == username).first()
    if user and verify_user_password(password, user.password_hash):
        now = datetime.utcnow()
        if not user.enabled:
            record_attempt(db, ip, username, ua, success=False, failure_reason="Compte désactivé")
            return RedirectResponse(f"/auth/login?error=disabled&next={next}", status_code=302)
        if user.valid_until and user.valid_until <= now:
            record_attempt(db, ip, username, ua, success=False, failure_reason="Compte expiré")
            return RedirectResponse(f"/auth/login?error=expired&next={next}", status_code=302)

        user.last_login = now
        db.commit()
        record_attempt(db, ip, username, ua, success=True)
        response = RedirectResponse(next or "/", status_code=302)
        _set_session_cookie(response, user.username, "user")
        return response

    record_attempt(db, ip, username, ua, success=False, failure_reason="Identifiants invalides")
    apply_brute_force_rules(db, ip, ua)
    return RedirectResponse(f"/auth/login?error=1&next={next}", status_code=302)


@router.get("/auth/logout", include_in_schema=False)
async def logout(request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    ua = request.headers.get("user-agent", "")
    log = AccessLog(event_type="logout", source_ip=ip, user_agent=ua, message="Déconnexion")
    db.add(log)
    db.commit()
    response = RedirectResponse("/auth/login", status_code=302)
    response.delete_cookie(COOKIE_NAME, domain=get_cookie_domain(), path="/")
    return response


@router.get("/device-suspended", include_in_schema=False)
async def device_suspended():
    html = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Service suspendu</title>
  <style>
    :root { --bg:#0f1117; --surface:#1a1d27; --border:#2e3250; --text:#e2e8f0; --muted:#8892a4; --unknown:#6b7280; }
    * { box-sizing:border-box; margin:0; padding:0; }
    body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }
    .card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:48px 40px; max-width:420px; width:100%; text-align:center; }
    .icon { font-size:48px; margin-bottom:20px; }
    h1 { font-size:22px; font-weight:700; margin-bottom:10px; }
    p { color:var(--muted); font-size:14px; line-height:1.6; }
    .badge { display:inline-block; background:rgba(107,114,128,0.2); color:var(--unknown); font-size:12px; font-weight:600; padding:4px 12px; border-radius:20px; margin-bottom:24px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⏸</div>
    <span class="badge">Service suspendu</span>
    <h1>Ce service est temporairement indisponible</h1>
    <p>L'accès à cet équipement a été suspendu par l'administrateur. Veuillez réessayer ultérieurement ou contacter l'administrateur.</p>
  </div>
</body>
</html>"""
    return Response(content=html, media_type="text/html", status_code=503)


_NO_ACCESS_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Accès refusé</title>
<style>
  :root { --bg:#0f1117; --surface:#1a1d27; --border:#2e3250; --text:#e2e8f0; --muted:#8892a4; --danger:#ef4444; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:48px 40px; max-width:420px; width:100%; text-align:center; }
  .icon { font-size:48px; margin-bottom:20px; }
  h1 { font-size:22px; font-weight:700; margin-bottom:10px; }
  p { color:var(--muted); font-size:14px; line-height:1.6; }
  .badge { display:inline-block; background:rgba(239,68,68,0.15); color:var(--danger); font-size:12px; font-weight:600; padding:4px 12px; border-radius:20px; margin-bottom:24px; }
  a { color:#4f8ef7; font-size:13px; display:inline-block; margin-top:20px; }
</style></head>
<body><div class="card">
  <div class="icon">⛔</div>
  <span class="badge">Accès refusé</span>
  <h1>Vous n'avez pas accès à ce service</h1>
  <p>Votre compte n'est pas autorisé pour ce service, ou son autorisation a expiré. Contactez l'administrateur si vous pensez qu'il s'agit d'une erreur.</p>
  <a href="__LOGOUT__">Changer de compte</a>
</div></body></html>"""


def _user_can_access(db: Session, subject: str, slug: str) -> bool:
    now = datetime.utcnow()
    user = db.query(User).filter(User.username == subject).first()
    if not user or not user.enabled:
        return False
    if user.valid_until and user.valid_until <= now:
        return False
    device = db.query(Device).filter(Device.slug == slug).first()
    if not device:
        return False
    access = db.query(UserDeviceAccess).filter(
        UserDeviceAccess.user_id == user.id,
        UserDeviceAccess.device_id == device.id,
    ).first()
    return access is not None


@router.get("/auth/check", include_in_schema=False)
async def auth_check(request: Request, db: Session = Depends(get_db)):
    """Point de contrôle appelé par le ``forward_auth`` de Caddy pour les
    services en mode « protégé ».

    - Administrateur authentifié → 200 (accès à tous les services).
    - Utilisateur authentifié → 200 uniquement pour les services qui lui sont
      assignés et non expirés ; sinon page 403 « accès refusé ».
    - Non authentifié → redirection vers la page de connexion.
    """
    token = request.cookies.get(COOKIE_NAME, "")
    principal = parse_token(token)

    domain = os.getenv("DOMAIN", "mondomaine.com")
    admin_domain = f"iot.{domain}"

    if principal and principal["role"] == "admin":
        return Response(status_code=200)

    if principal and principal["role"] == "user":
        original_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
        slug = _slug_from_host(original_host)
        if _user_can_access(db, principal["subject"], slug):
            return Response(status_code=200)
        logout_url = f"https://{admin_domain}/auth/logout"
        return HTMLResponse(
            _NO_ACCESS_HTML.replace("__LOGOUT__", logout_url),
            status_code=403,
        )

    # Non authentifié → redirection vers la connexion avec l'URL d'origine
    original_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    original_uri = request.headers.get("x-forwarded-uri", "/")
    scheme = "https"
    next_url = f"{scheme}://{original_host}{original_uri}" if original_host else "/"
    return RedirectResponse(
        f"{scheme}://{admin_domain}/auth/login?next={next_url}",
        status_code=302,
    )
