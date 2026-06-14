import os

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
    record_attempt,
    verify_password,
    verify_token,
)
from models import AccessLog

router = APIRouter(tags=["auth"])

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

    if verify_password(password) and username == os.getenv("ADMIN_USER", "admin"):
        record_attempt(db, ip, username, ua, success=True)
        response = RedirectResponse(next or "/", status_code=302)
        response.set_cookie(
            COOKIE_NAME,
            make_token(),
            domain=get_cookie_domain(),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
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


@router.get("/auth/check", include_in_schema=False)
async def auth_check(request: Request):
    """Used by Caddy forward_auth. Returns 200 if valid, 302 to login if not."""
    token = request.cookies.get(COOKIE_NAME, "")
    if verify_token(token):
        return Response(status_code=200)

    # Build redirect to login with next URL from original host/path
    original_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    original_uri = request.headers.get("x-forwarded-uri", "/")
    scheme = "https"
    domain = os.getenv("DOMAIN", "mondomaine.com")
    admin_domain = f"iot.{domain}"
    next_url = f"{scheme}://{original_host}{original_uri}" if original_host else "/"
    return RedirectResponse(
        f"{scheme}://{admin_domain}/auth/login?next={next_url}",
        status_code=302,
    )
