import os
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    _safe_next,
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

    # Neutralise une redirection ouverte : ?next=https://evil.com après login.
    next = _safe_next(next)

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


# Ces pages sont servies sur les sous-domaines des équipements, où la feuille
# de style du portail n'est pas accessible : elles doivent rester autonomes.
# Elles reprennent donc le même système visuel, en ligne et en version réduite.
_STATE_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg:#f2f2f7; --card:#fff; --fill:rgba(120,120,128,0.10);
      --label:#1c1c1e; --label-2:#55555c; --blue:#007aff; --accent:__ACCENT__;
      --shadow:0 8px 20px rgba(0,0,0,0.10), 0 32px 64px rgba(0,0,0,0.14);
      color-scheme:light;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg:#000; --card:#1c1c1e; --fill:rgba(120,120,128,0.22);
        --label:#fff; --label-2:#aeaeb2; --blue:#0a84ff; --accent:__ACCENT_DARK__;
        --shadow:0 12px 32px rgba(0,0,0,0.55), 0 40px 80px rgba(0,0,0,0.6);
        color-scheme:dark;
      }
    }
    *,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
    body {
      background:var(--bg); color:var(--label); min-height:100vh;
      display:flex; align-items:center; justify-content:center; padding:1rem;
      font:100%/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,system-ui,sans-serif;
      -webkit-font-smoothing:antialiased;
    }
    .card {
      background:var(--card); border-radius:26px; box-shadow:var(--shadow);
      padding:2.5rem 1.75rem; max-width:26rem; width:100%; text-align:center;
      animation:in 520ms cubic-bezier(0.22,0.9,0.28,1) both;
    }
    @keyframes in { from { opacity:0; transform:translate3d(0,14px,0) scale(0.97); } to { opacity:1; transform:none; } }
    .glyph {
      width:3.25rem; height:3.25rem; margin:0 auto 1.25rem; border-radius:14px;
      display:grid; place-items:center; font-size:1.5rem;
      background:color-mix(in srgb, var(--accent) 16%, transparent); color:var(--accent);
    }
    .chip {
      display:inline-block; margin-bottom:1rem; padding:0.1875rem 0.625rem; border-radius:100px;
      background:color-mix(in srgb, var(--accent) 14%, transparent); color:var(--accent);
      font-size:0.6875rem; font-weight:620; letter-spacing:0.006em;
    }
    h1 { font-size:1.3125rem; font-weight:700; line-height:1.19; letter-spacing:-0.018em; margin-bottom:0.5rem; }
    p { color:var(--label-2); font-size:0.9375rem; letter-spacing:-0.006em; line-height:1.5; }
    a {
      display:inline-block; margin-top:1.5rem; padding:0.5rem 0.9375rem; border-radius:10px;
      background:var(--fill); color:var(--blue); font-size:0.875rem; font-weight:590; text-decoration:none;
      transition:transform 110ms cubic-bezier(0.3,0.8,0.4,1);
    }
    a:active { transform:scale(0.96); }
    @media (prefers-reduced-motion: reduce) {
      .card { animation:none; }
      a:active { transform:none; }
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="glyph" aria-hidden="true">__GLYPH__</div>
    <span class="chip">__CHIP__</span>
    <h1>__HEADING__</h1>
    <p>__BODY__</p>
    __LINK__
  </div>
</body>
</html>"""


def _state_page(*, title, chip, glyph, heading, body, accent, accent_dark, link=""):
    html = _STATE_PAGE
    for token, value in (
        ("__TITLE__", title),
        ("__CHIP__", chip),
        ("__GLYPH__", glyph),
        ("__HEADING__", heading),
        ("__BODY__", body),
        ("__ACCENT__", accent),
        ("__ACCENT_DARK__", accent_dark),
        ("__LINK__", link),
    ):
        html = html.replace(token, value)
    return html


@router.get("/device-suspended", include_in_schema=False)
async def device_suspended():
    html = _state_page(
        title="Service suspendu",
        chip="Service suspendu",
        glyph="⏸",
        heading="Ce service est temporairement indisponible",
        body="L'accès à cet équipement a été suspendu par l'administrateur. "
        "Réessayez plus tard ou contactez-le.",
        accent="#8e8e93",
        accent_dark="#98989d",
    )
    return Response(content=html, media_type="text/html", status_code=503)


_NO_ACCESS_HTML = _state_page(
    title="Accès refusé",
    chip="Accès refusé",
    glyph="⛔",
    heading="Vous n'avez pas accès à ce service",
    body="Votre compte n'est pas autorisé pour ce service, ou son autorisation a expiré. "
    "Contactez l'administrateur si vous pensez qu'il s'agit d'une erreur.",
    accent="#ff3b30",
    accent_dark="#ff453a",
    link='<a href="__LOGOUT__">Changer de compte</a>',
)


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
